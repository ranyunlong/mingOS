//! Also runnable as a tiny standalone Cargo test, with no engine dependency.
#[path = "../src/app_data.rs"]
mod app_data;
use app_data::*;
use std::{
    fs,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let p = std::env::temp_dir().join(format!(
            "ming-app-data-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&p).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&p, fs::Permissions::from_mode(0o700)).unwrap();
        }
        Self(p)
    }
    fn grant(&self, key: &str, slots: &[u8]) -> AppDataGrant {
        AppDataGrant::open(&self.0, key, slots).unwrap()
    }
    fn directory(&self, key: &str) -> PathBuf {
        self.0.join(format!(
            "key-{}",
            key.bytes()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>()
        ))
    }
    fn file(&self, key: &str, slot: u8) -> PathBuf {
        self.directory(key).join(format!("slot-{slot}"))
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
#[test]
fn cross_grant_same_slot_isolated_and_reopen_persists() {
    let f = Fixture::new();
    let a = f.grant("catalog-A", &[1, 8]);
    let b = f.grant("catalog-B", &[1]);
    assert_eq!(a.size(1), Err(AppDataError::NotFound));
    let receipt = a.write(1, b"a\0\xff").unwrap();
    assert_eq!(receipt.length, 3);
    #[cfg(target_os = "linux")]
    assert!(receipt.directory_synced);
    b.write(1, b"other").unwrap();
    assert_eq!(a.read(1, 0, 65536).unwrap(), b"a\0\xff");
    assert_eq!(b.read(1, 1, 3).unwrap(), b"the");
    assert_eq!(a.read(1, 3, 10).unwrap(), b"");
    assert_eq!(a.read(1, 4, 10).unwrap(), b"");
    drop(a);
    assert_eq!(
        f.grant("catalog-A", &[1]).read(1, 0, 8).unwrap(),
        b"a\0\xff"
    );
}
#[test]
fn revoke_invalidates_old_clone_and_all_io() {
    let f = Fixture::new();
    let g = f.grant("revoke", &[1]);
    let old = g.clone();
    g.write(1, b"saved").unwrap();
    g.revoke();
    assert_eq!(old.read(1, 0, 1), Err(AppDataError::Denied));
    assert_eq!(old.size(1), Err(AppDataError::Denied));
    assert!(matches!(old.write(1, b"new"), Err(AppDataError::Denied)));
    assert_eq!(old.delete(1), Err(AppDataError::Denied));
    assert_eq!(f.grant("revoke", &[1]).read(1, 0, 5).unwrap(), b"saved");
}
#[test]
fn key_and_explicit_slots_are_validated() {
    let f = Fixture::new();
    for key in [
        "",
        ".",
        "..",
        "../outside",
        "a/b",
        "a\\b",
        "a:b",
        "a.b",
        "a b",
        "中",
        "a\0b",
        "-start",
    ] {
        assert!(
            matches!(
                AppDataGrant::open(&f.0, key, &[1]),
                Err(AppDataError::InvalidArgument)
            ),
            "{key:?}"
        );
    }
    assert!(AppDataGrant::open(&f.0, &"a".repeat(65), &[1]).is_err());
    for slots in [&[][..], &[0], &[9], &[1, 1], &[1, 2, 3, 4, 5, 6, 7, 8, 8]] {
        assert!(AppDataGrant::open(&f.0, "valid", slots).is_err());
    }
    assert!(AppDataGrant::open("relative", "valid", &[1]).is_err());
    let g = f.grant("valid", &[1]);
    for slot in [-1, 0, 9, i32::MAX] {
        assert_eq!(g.size(slot), Err(AppDataError::InvalidArgument));
        assert!(g.write(slot, b"x").is_err());
        assert!(g.delete(slot).is_err());
        assert!(g.read(slot, 0, 1).is_err());
    }
    assert_eq!(g.size(2), Err(AppDataError::Denied));
    assert_eq!(g.read(1, -1, 1), Err(AppDataError::InvalidArgument));
    assert_eq!(g.read(1, 0, 65537), Err(AppDataError::InvalidArgument));
    assert_eq!(g.read(1, 0, -1), Err(AppDataError::InvalidArgument));
    assert_eq!(g.read(1, 65537, 0), Err(AppDataError::InvalidArgument));
}
#[test]
fn exactly_eight_slots_and_512_kib() {
    assert_eq!(MAX_TOTAL_BYTES, 512 * 1024);
    let f = Fixture::new();
    let g = f.grant("quota", &[1, 2, 3, 4, 5, 6, 7, 8]);
    let bytes = vec![0xab; MAX_SLOT_BYTES];
    for s in 1..=8 {
        assert_eq!(g.write(s, &bytes).unwrap().length, MAX_SLOT_BYTES);
        assert_eq!(g.size(s), Ok(MAX_SLOT_BYTES));
    }
    assert!(matches!(
        g.write(1, &vec![0; 65537]),
        Err(AppDataError::InvalidArgument)
    ));
    assert_eq!(g.read(1, 65535, 8).unwrap(), [0xab]);
    assert_eq!(g.write(2, b"").unwrap().length, 0);
    assert_eq!(g.size(2), Ok(0));
    assert_eq!(g.read(2, 0, 1).unwrap(), b"");
}
#[test]
fn failed_atomic_replace_preserves_old_and_cleans_own_temp() {
    let f = Fixture::new();
    let g = f.grant("atomic", &[1]);
    g.write(1, b"old").unwrap();
    let dir = f.directory("atomic");
    fs::write(dir.join(".unrelated-temp"), b"not ours").unwrap();
    g.fail_next_commit();
    assert!(matches!(g.write(1, b"new"), Err(AppDataError::Io)));
    assert_eq!(g.read(1, 0, 8).unwrap(), b"old");
    let mut names: Vec<_> = fs::read_dir(&dir)
        .unwrap()
        .map(|e| e.unwrap().file_name())
        .collect();
    names.sort();
    assert_eq!(
        names,
        vec![
            std::ffi::OsString::from(".unrelated-temp"),
            std::ffi::OsString::from("slot-1")
        ]
    );
    g.write(1, b"new").unwrap();
    assert_eq!(g.read(1, 0, 8).unwrap(), b"new");
}
#[test]
fn post_commit_sync_failure_is_receipt_not_failed_write() {
    let f = Fixture::new();
    let g = f.grant("sync", &[1]);
    g.write(1, b"old").unwrap();
    g.fail_next_directory_sync();
    let receipt = g.write(1, b"new").unwrap();
    assert_eq!(receipt.length, 3);
    assert!(!receipt.directory_synced);
    assert_eq!(g.read(1, 0, 8).unwrap(), b"new");
}
#[test]
fn delete_and_missing_are_explicit() {
    let f = Fixture::new();
    let g = f.grant("delete", &[1]);
    assert_eq!(g.delete(1), Err(AppDataError::NotFound));
    g.write(1, b"value").unwrap();
    g.delete(1).unwrap();
    assert_eq!(g.size(1), Err(AppDataError::NotFound));
}
#[test]
fn directory_and_oversized_file_are_refused() {
    let f = Fixture::new();
    let g = f.grant("unsafe", &[1, 2]);
    fs::create_dir(f.file("unsafe", 1)).unwrap();
    for s in [1, 2] {
        if s == 2 {
            let p = f.file("unsafe", 2);
            fs::write(&p, vec![0; 65537]).unwrap();
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&p, fs::Permissions::from_mode(0o600)).unwrap();
            }
        }
        assert!(g.read(s.into(), 0, 8).is_err());
        assert!(g.size(s.into()).is_err());
        assert!(g.write(s.into(), b"x").is_err());
        assert!(g.delete(s.into()).is_err());
    }
}
#[test]
fn abi_ranges_reject_before_any_io() {
    assert_eq!(validate_read(1, 2, 3, 4, 20), Ok(3..7));
    assert_eq!(validate_write(1, 3, 4, 20), Ok(3..7));
    for (s, o, p, c, m) in [
        (0, 0, 0, 1, 10),
        (9, 0, 0, 1, 10),
        (1, -1, 0, 1, 10),
        (1, 0, -1, 1, 10),
        (1, 0, 8, 3, 10),
        (1, 0, 0, -1, 10),
        (1, 0, 0, 65537, 99999),
        (1, 65537, 0, 0, 10),
    ] {
        assert_eq!(
            validate_read(s, o, p, c, m),
            Err(AppDataError::InvalidArgument)
        );
    }
    for (s, p, l, m) in [
        (0, 0, 1, 10),
        (9, 0, 1, 10),
        (1, -1, 1, 10),
        (1, 8, 3, 10),
        (1, 0, -1, 10),
        (1, 0, 65537, 99999),
    ] {
        assert!(validate_write(s, p, l, m).is_err());
    }
    assert_eq!(validate_write(8, 10, 0, 10), Ok(10..10));
    assert!(validate_write(8, 11, 0, 10).is_err());
    assert_eq!(AppDataError::Denied.code(), -1);
    assert_eq!(AppDataError::InvalidArgument.code(), -2);
    assert_eq!(AppDataError::NotFound.code(), -3);
    assert_eq!(AppDataError::Io.code(), -4);
}
#[cfg(unix)]
mod unix {
    use super::*;
    use std::os::unix::fs::{PermissionsExt, symlink};
    #[test]
    fn symlink_root_ancestor_key_and_slot_refused() {
        let f = Fixture::new();
        let outside = Fixture::new();
        symlink(&outside.0, f.0.join("link")).unwrap();
        assert!(AppDataGrant::open(f.0.join("link"), "key", &[1]).is_err());
        fs::create_dir(outside.0.join("child")).unwrap();
        assert!(AppDataGrant::open(f.0.join("link/child"), "key", &[1]).is_err());
        assert!(AppDataGrant::open(f.0.join("link/../child"), "key", &[1]).is_err());
        symlink(&outside.0, f.directory("key")).unwrap();
        assert!(AppDataGrant::open(&f.0, "key", &[1]).is_err());
        let g = f.grant("slots", &[1]);
        fs::write(outside.0.join("sentinel"), b"secret").unwrap();
        symlink(outside.0.join("sentinel"), f.file("slots", 1)).unwrap();
        assert!(g.read(1, 0, 8).is_err());
        assert!(g.size(1).is_err());
        assert!(g.write(1, b"evil").is_err());
        assert!(g.delete(1).is_err());
        assert_eq!(fs::read(outside.0.join("sentinel")).unwrap(), b"secret");
    }
    #[test]
    fn fifo_never_blocks() {
        use std::os::unix::ffi::OsStrExt;
        let f = Fixture::new();
        let g = f.grant("fifo", &[1]);
        let p = std::ffi::CString::new(f.file("fifo", 1).as_os_str().as_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(p.as_ptr(), 0o600) }, 0);
        assert!(g.read(1, 0, 1).is_err());
        assert!(g.size(1).is_err());
        assert!(g.write(1, b"x").is_err());
        assert!(g.delete(1).is_err());
    }
    #[test]
    fn hardlink_and_foreign_permissions_refused() {
        let f = Fixture::new();
        let g = f.grant("hard", &[1]);
        g.write(1, b"old").unwrap();
        fs::hard_link(f.file("hard", 1), f.0.join("second")).unwrap();
        assert!(g.read(1, 0, 8).is_err());
        assert!(g.write(1, b"new").is_err());
        let unsafe_root = Fixture::new();
        fs::set_permissions(&unsafe_root.0, fs::Permissions::from_mode(0o777)).unwrap();
        assert!(AppDataGrant::open(&unsafe_root.0, "key", &[1]).is_err());
    }
    #[test]
    fn reader_rejects_growth_and_inplace_change() {
        // Deterministic hook mutates a real regular file between the two fstat snapshots.
        let f = Fixture::new();
        let g = f.grant("change", &[1]);
        g.write(1, b"old").unwrap();
        g.change_during_next_read_for_test(true);
        assert!(g.read(1, 0, 3).is_err());
        g.write(1, b"old").unwrap();
        g.change_during_next_read_for_test(false);
        assert!(g.read(1, 0, 3).is_err());
    }
    #[test]
    fn directory_handle_stays_pinned_after_path_replacement() {
        let f = Fixture::new();
        let g = f.grant("pinned", &[1]);
        g.write(1, b"old").unwrap();
        fs::rename(f.directory("pinned"), f.0.join("moved")).unwrap();
        let outside = Fixture::new();
        symlink(&outside.0, f.directory("pinned")).unwrap();
        g.write(1, b"new").unwrap();
        assert_eq!(g.read(1, 0, 8).unwrap(), b"new");
        assert!(fs::read_dir(&outside.0).unwrap().next().is_none());
    }
}

#[cfg(target_os = "linux")]
#[test]
fn actual_filesystem_create_failure_keeps_previous_value() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fixture::new();
    let g = f.grant("readonly", &[1]);
    g.write(1, b"old").unwrap();
    let dir = f.directory("readonly");
    fs::set_permissions(&dir, fs::Permissions::from_mode(0o500)).unwrap();
    let outcome = g.write(1, b"new");
    fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
    assert!(
        outcome.is_err(),
        "Run without CAP_DAC_OVERRIDE, as in the isolated probe"
    );
    assert_eq!(g.read(1, 0, 3).unwrap(), b"old");
    assert_eq!(fs::read_dir(dir).unwrap().count(), 1);
}
#[cfg(windows)]
#[test]
fn windows_reparse_junction_root_ancestor_and_key_refused() {
    fn junction(from: &std::path::Path, to: &std::path::Path) {
        let result = std::process::Command::new("cmd.exe")
            .args(["/C", "mklink", "/J"])
            .arg(from)
            .arg(to)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
    }
    let f = Fixture::new();
    let outside = Fixture::new();
    junction(&f.0.join("link"), &outside.0);
    fs::create_dir(outside.0.join("child")).unwrap();
    assert!(AppDataGrant::open(f.0.join("link"), "key", &[1]).is_err());
    assert!(AppDataGrant::open(f.0.join("link/child"), "key", &[1]).is_err());
    junction(&f.directory("key"), &outside.0);
    assert!(AppDataGrant::open(&f.0, "key", &[1]).is_err());
    let g = f.grant("slots", &[1]);
    junction(&f.file("slots", 1), &outside.0);
    assert!(g.read(1, 0, 1).is_err());
    assert!(g.write(1, b"x").is_err());
    assert!(g.size(1).is_err());
    assert!(g.delete(1).is_err());
}
#[cfg(windows)]
#[test]
fn windows_sharing_failure_preserves_old_value() {
    use std::os::windows::fs::OpenOptionsExt;
    let f = Fixture::new();
    let g = f.grant("sharing", &[1]);
    g.write(1, b"old").unwrap();
    let held = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(f.file("sharing", 1))
        .unwrap();
    assert!(g.write(1, b"new").is_err());
    drop(held);
    assert_eq!(g.read(1, 0, 3).unwrap(), b"old");
    assert_eq!(fs::read_dir(f.directory("sharing")).unwrap().count(), 1);
}

#[test]
fn case_distinct_catalog_keys_do_not_alias() {
    let f = Fixture::new();
    let upper = f.grant("Key", &[1]);
    let lower = f.grant("key", &[1]);
    upper.write(1, b"upper").unwrap();
    lower.write(1, b"lower").unwrap();
    assert_eq!(upper.read(1, 0, 8).unwrap(), b"upper");
    assert_eq!(lower.read(1, 0, 8).unwrap(), b"lower");
    // Keys valid at the capability boundary must not become Windows device names.
    let reserved = f.grant("NUL", &[1]);
    reserved.write(1, b"ordinary data").unwrap();
    assert_eq!(reserved.size(1), Ok(13));
}
