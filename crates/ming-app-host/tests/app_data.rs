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
fn private_file_paths_accept_normal_relative_components() {
    assert_eq!(validate_private_path("documents/报告.txt"), Ok(vec!["documents", "报告.txt"]));
    assert_eq!(validate_private_path("file name.txt"), Ok(vec!["file name.txt"]));
}

#[test]
fn private_file_paths_reject_escape_ambiguity_and_reserved_names() {
    for path in ["", "/a", "a/", "a//b", ".", "..", "a/../b", "a/./b", "a\0b", "a\\b", ".ming-data-1", "a/.ming-data-2"] {
        assert_eq!(validate_private_path(path), Err(AppDataError::InvalidArgument), "{path:?}");
    }
}

#[test]
fn private_file_paths_bound_depth_and_encoded_length() {
    assert!(validate_private_path(&vec!["a"; 16].join("/")).is_ok());
    assert_eq!(validate_private_path(&vec!["a"; 17].join("/")), Err(AppDataError::InvalidArgument));
    assert!(validate_private_path(&"a".repeat(255)).is_ok());
    assert_eq!(validate_private_path(&"a".repeat(256)), Err(AppDataError::InvalidArgument));
    assert_eq!(validate_private_path(&vec!["a".repeat(255); 5].join("/")), Err(AppDataError::InvalidArgument));
}

#[cfg(target_os = "linux")]
#[test]
fn authorized_private_files_are_isolated_persistent_and_revocable() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let authority = PackageStorageAuthority::new(root.clone());
    let a = "dev.example.alpha";
    let b = "dev.example.beta";
    assert_eq!(authority.write_file(a, "notes/a.txt", b"secret"), Err(AppDataError::Denied));
    authority.authorize(a).unwrap();
    authority.authorize(b).unwrap();
    authority.create_file_directory(a, "notes").unwrap();
    authority.write_file(a, "notes/a.txt", b"secret").unwrap();
    assert_eq!(authority.read_file(a, "notes/a.txt").unwrap(), b"secret");
    assert_eq!(authority.read_file(b, "notes/a.txt"), Err(AppDataError::NotFound));
    authority.revoke(a).unwrap();
    assert_eq!(authority.read_file(a, "notes/a.txt"), Err(AppDataError::Denied));
    authority.authorize(a).unwrap();
    assert_eq!(authority.read_file(a, "notes/a.txt").unwrap(), b"secret");
    authority.delete_file(a, "notes/a.txt").unwrap();
    assert_eq!(authority.read_file(a, "notes/a.txt"), Err(AppDataError::NotFound));
}

#[cfg(target_os = "linux")]
#[test]
fn private_file_sessions_bind_identity_and_old_sessions_stay_revoked() {
    use std::sync::Arc;
    let f = Fixture::new();
    let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap()));
    let a = "dev.example.alpha";
    let b = "dev.example.beta";
    assert!(matches!(authority.file_session(a), Err(AppDataError::Denied)));
    authority.authorize(a).unwrap();
    authority.authorize(b).unwrap();
    let a_session = authority.file_session(a).unwrap();
    let a_clone = a_session.clone();
    let b_session = authority.file_session(b).unwrap();
    a_session.write_file("shared-name", b"alpha").unwrap();
    b_session.write_file("shared-name", b"beta").unwrap();
    assert_eq!(a_clone.read_file("shared-name").unwrap(), b"alpha");
    assert_eq!(b_session.read_file("shared-name").unwrap(), b"beta");
    authority.revoke(a).unwrap();
    authority.authorize(a).unwrap();
    assert_eq!(a_clone.read_file("shared-name"), Err(AppDataError::Denied));
    let new_session = authority.file_session(a).unwrap();
    assert_eq!(new_session.read_file("shared-name").unwrap(), b"alpha");
    new_session.create_directory("docs").unwrap();
    new_session.rename_file("shared-name", "docs/note").unwrap();
    assert_eq!(new_session.list_directory("docs").unwrap().len(), 1);
    new_session.delete_file("docs/note").unwrap();
    new_session.delete_directory("docs").unwrap();
    let sibling = authority.file_session(a).unwrap();
    new_session.close();
    assert_eq!(new_session.list_directory(""), Err(AppDataError::Denied));
    assert!(sibling.list_directory("").is_ok());
    authority.revoke(a).unwrap();
    assert_eq!(sibling.list_directory(""), Err(AppDataError::Denied));
    assert_eq!(b_session.read_file("shared-name").unwrap(), b"beta");
}

#[cfg(target_os = "linux")]
#[test]
fn private_file_sessions_are_bounded_and_dropped_sessions_are_reclaimed() {
    use std::sync::Arc;
    let f = Fixture::new();
    let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap()));
    let app = "dev.example.alpha";
    authority.authorize(app).unwrap();
    let mut sessions: Vec<_> = (0..64).map(|_| authority.file_session(app).unwrap()).collect();
    assert!(matches!(authority.file_session(app), Err(AppDataError::Io)));
    sessions.pop();
    assert!(authority.file_session(app).is_ok());
}

#[cfg(target_os = "linux")]
#[test]
fn private_files_reject_symlinks_and_invalid_paths() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new();
    let authority = PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap());
    let app = "dev.example.alpha";
    authority.authorize(app).unwrap();
    authority.create_file_directory(app, "safe").unwrap();
    let package = fs::read_dir(&f.0).unwrap().map(|e| e.unwrap().path()).find(|p| p.file_name().unwrap().to_string_lossy().starts_with("app-")).unwrap();
    symlink(&f.0, package.join("files/link")).unwrap();
    assert!(authority.write_file(app, "link/escape", b"x").is_err());
    assert!(authority.read_file(app, "link/escape").is_err());
    assert_eq!(authority.write_file(app, "../escape", b"x"), Err(AppDataError::InvalidArgument));
    assert!(!f.0.join("escape").exists());
}

#[cfg(target_os = "linux")]
#[test]
fn private_file_quota_applies_to_all_paths_and_replacements() {
    let f = Fixture::new();
    let authority = PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap());
    let app = "dev.example.alpha";
    authority.authorize(app).unwrap();
    let data = vec![7; MAX_PRIVATE_FILE_BYTES];
    for i in 0..(MAX_PRIVATE_TOTAL_BYTES / MAX_PRIVATE_FILE_BYTES) {
        authority.write_file(app, &format!("file-{i}"), &data).unwrap();
    }
    assert_eq!(authority.read_file(app, "file-0").unwrap(), data);
    assert_eq!(authority.write_file(app, "overflow", b"x"), Err(AppDataError::Io));
    authority.write_file(app, "file-0", b"small").unwrap();
    authority.write_file(app, "overflow", b"x").unwrap();
    assert_eq!(authority.read_file(app, "file-0").unwrap(), b"small");
}

#[cfg(target_os = "linux")]
#[test]
fn private_file_can_exceed_legacy_slot_limit_without_changing_slot_limit() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let authority = PackageStorageAuthority::new(root.clone());
    let app = "dev.example.alpha";
    authority.authorize(app).unwrap();
    let data = vec![42; MAX_SLOT_BYTES + 1];
    authority.write_file(app, "large", &data).unwrap();
    assert_eq!(authority.read_file(app, "large").unwrap(), data);
    assert_eq!(authority.list_file_directory(app, "").unwrap().len(), 1);
    authority.rename_file(app, "large", "renamed").unwrap();
    assert_eq!(authority.read_file(app, "renamed").unwrap(), data);
    authority.delete_file(app, "renamed").unwrap();
    authority.write_file(app, "large", &data).unwrap();
    authority.write_file(app, "large", b"replacement").unwrap();
    assert_eq!(authority.read_file(app, "large").unwrap(), b"replacement");
    let legacy = root.grant("legacy", &[1]).unwrap();
    assert_eq!(legacy.write(1, &data), Err(AppDataError::InvalidArgument));
}

#[cfg(target_os = "linux")]
#[test]
fn private_file_concurrent_writes_share_the_package_quota() {
    use std::sync::{Arc, Barrier};
    let f = Fixture::new();
    let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap()));
    let app = "dev.example.alpha";
    authority.authorize(app).unwrap();
    let data = vec![7; MAX_PRIVATE_FILE_BYTES];
    for i in 0..(MAX_PRIVATE_TOTAL_BYTES / MAX_PRIVATE_FILE_BYTES - 1) {
        authority.write_file(app, &format!("file-{i}"), &data).unwrap();
    }
    let barrier = Arc::new(Barrier::new(3));
    let threads: Vec<_> = (0..2).map(|i| {
        let authority = Arc::clone(&authority);
        let barrier = Arc::clone(&barrier);
        std::thread::spawn(move || {
            barrier.wait();
            authority.write_file(app, &format!("concurrent-{i}"), &vec![1; MAX_PRIVATE_FILE_BYTES])
        })
    }).collect();
    barrier.wait();
    let results: Vec<_> = threads.into_iter().map(|thread| thread.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(results.iter().filter(|result| **result == Err(AppDataError::Io)).count(), 1);
}

#[cfg(target_os = "linux")]
#[test]
fn private_files_reject_final_symlinks_and_hard_links() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new();
    let authority = PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap());
    let app = "dev.example.alpha";
    authority.authorize(app).unwrap();
    authority.write_file(app, "original", b"original").unwrap();
    let package = fs::read_dir(&f.0).unwrap().map(|e| e.unwrap().path()).find(|p| p.file_name().unwrap().to_string_lossy().starts_with("app-")).unwrap();
    let files = package.join("files");
    symlink(files.join("original"), files.join("link")).unwrap();
    assert!(authority.read_file(app, "link").is_err());
    assert!(authority.write_file(app, "link", b"replace").is_err());
    fs::remove_file(files.join("link")).unwrap();
    fs::hard_link(files.join("original"), files.join("hard")).unwrap();
    assert!(authority.read_file(app, "original").is_err());
    assert!(authority.write_file(app, "other", b"x").is_err());
    assert_eq!(fs::read(files.join("original")).unwrap(), b"original");
}

#[cfg(target_os = "linux")]
#[test]
fn private_directory_listing_is_sorted_scoped_and_requires_authorization() {
    let f = Fixture::new();
    let authority = PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap());
    let a = "dev.example.alpha";
    let b = "dev.example.beta";
    assert_eq!(authority.list_file_directory(a, ""), Err(AppDataError::Denied));
    authority.authorize(a).unwrap();
    authority.authorize(b).unwrap();
    authority.create_file_directory(a, "docs").unwrap();
    authority.write_file(a, "z.txt", b"z").unwrap();
    authority.write_file(a, "docs/a.txt", b"a").unwrap();
    assert_eq!(authority.list_file_directory(a, "").unwrap(), vec![
        PrivateFileEntry { name: "docs".into(), is_directory: true },
        PrivateFileEntry { name: "z.txt".into(), is_directory: false },
    ]);
    assert_eq!(authority.list_file_directory(a, "docs").unwrap(), vec![
        PrivateFileEntry { name: "a.txt".into(), is_directory: false },
    ]);
    assert!(authority.list_file_directory(b, "").unwrap().is_empty());
    authority.revoke(a).unwrap();
    assert_eq!(authority.list_file_directory(a, ""), Err(AppDataError::Denied));
}

#[cfg(target_os = "linux")]
#[test]
fn private_directory_removal_only_removes_empty_directories() {
    let f = Fixture::new();
    let authority = PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap());
    let app = "dev.example.alpha";
    authority.authorize(app).unwrap();
    authority.create_file_directory(app, "docs").unwrap();
    authority.write_file(app, "docs/a.txt", b"keep").unwrap();
    assert!(authority.delete_file_directory(app, "docs").is_err());
    assert_eq!(authority.read_file(app, "docs/a.txt").unwrap(), b"keep");
    assert_eq!(authority.delete_file_directory(app, ""), Err(AppDataError::InvalidArgument));
    authority.delete_file(app, "docs/a.txt").unwrap();
    authority.delete_file_directory(app, "docs").unwrap();
    assert!(authority.list_file_directory(app, "").unwrap().is_empty());
}

#[cfg(target_os = "linux")]
#[test]
fn private_directory_listing_hides_temporary_files_and_rejects_links() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let f = Fixture::new();
    let authority = PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap());
    let app = "dev.example.alpha";
    authority.authorize(app).unwrap();
    authority.create_file_directory(app, "docs").unwrap();
    let package = fs::read_dir(&f.0).unwrap().map(|e| e.unwrap().path()).find(|p| p.file_name().unwrap().to_string_lossy().starts_with("app-")).unwrap();
    let files = package.join("files");
    let temp = files.join(".ming-data-orphan");
    fs::write(&temp, b"incomplete").unwrap();
    fs::set_permissions(&temp, fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(authority.list_file_directory(app, "").unwrap(), vec![PrivateFileEntry { name: "docs".into(), is_directory: true }]);
    symlink(&f.0, files.join("link")).unwrap();
    assert!(authority.list_file_directory(app, "").is_err());
    assert!(authority.delete_file_directory(app, "link").is_err());
    assert!(f.0.exists());
}

#[cfg(target_os = "linux")]
#[test]
fn private_file_rename_moves_between_directories_without_overwrite() {
    let f = Fixture::new();
    let authority = PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap());
    let app = "dev.example.alpha";
    authority.authorize(app).unwrap();
    authority.create_file_directory(app, "a").unwrap();
    authority.create_file_directory(app, "b").unwrap();
    authority.write_file(app, "a/source", b"source").unwrap();
    authority.write_file(app, "b/existing", b"existing").unwrap();
    assert!(authority.rename_file(app, "a/source", "b/existing").is_err());
    assert_eq!(authority.read_file(app, "a/source").unwrap(), b"source");
    assert_eq!(authority.read_file(app, "b/existing").unwrap(), b"existing");
    assert_eq!(authority.rename_file(app, "a/source", "../escape"), Err(AppDataError::InvalidArgument));
    authority.rename_file(app, "a/source", "b/moved").unwrap();
    assert_eq!(authority.read_file(app, "a/source"), Err(AppDataError::NotFound));
    assert_eq!(authority.read_file(app, "b/moved").unwrap(), b"source");
    authority.revoke(app).unwrap();
    assert_eq!(authority.rename_file(app, "b/moved", "b/other"), Err(AppDataError::Denied));
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
fn package_storage_is_identity_bound_persistent_and_separate_from_legacy_keys() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let a = root.grant_package("dev.example.alpha", &[1]).unwrap();
    let b = root.grant_package("dev.example.beta", &[1]).unwrap();
    a.write(1, b"alpha").unwrap();
    b.write(1, b"beta").unwrap();
    assert_eq!(a.read(1, 0, 16).unwrap(), b"alpha");
    assert_eq!(b.read(1, 0, 16).unwrap(), b"beta");
    a.revoke();
    assert_eq!(a.read(1, 0, 16), Err(AppDataError::Denied));
    assert_eq!(root.grant_package("dev.example.alpha", &[1]).unwrap().read(1, 0, 16).unwrap(), b"alpha");
    assert_eq!(root.grant("dev-example-alpha", &[1]).unwrap().size(1), Err(AppDataError::NotFound));
}

#[test]
fn package_storage_rejects_invalid_identity_before_creating_a_directory() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    for identity in ["", "plain", "../escape", "dev..app", "Dev.app", "dev.app/other", "dev.app\0x", &"a".repeat(129)] {
        assert!(matches!(root.grant_package(identity, &[1]), Err(AppDataError::InvalidArgument)), "{identity:?}");
    }
    assert_eq!(std::fs::read_dir(&f.0).unwrap().count(), 0);
}

#[test]
fn package_storage_names_are_distinct_and_support_maximum_identity_length() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let max_identity = format!("{}.b", "a".repeat(126));
    for (index, identity) in ["a.b", "a-b", "a.b0", "a.b00", max_identity.as_str()].iter().enumerate() {
        root.grant_package(identity, &[1]).unwrap().write(1, &[index as u8]).unwrap();
    }
    for (index, identity) in ["a.b", "a-b", "a.b0", "a.b00", max_identity.as_str()].iter().enumerate() {
        assert_eq!(root.grant_package(identity, &[1]).unwrap().read(1, 0, 1).unwrap(), [index as u8]);
    }
    assert_eq!(std::fs::read_dir(&f.0).unwrap().count(), 5);
}

#[test]
fn package_authority_denies_without_consent_and_revokes_all_live_generations() {
    let f = Fixture::new();
    let authority = PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap());
    assert!(matches!(authority.grant("dev.example.alpha", &[1]), Err(AppDataError::Denied)));
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 0);
    authority.authorize("dev.example.alpha").unwrap();
    authority.authorize("dev.example.beta").unwrap();
    let a1 = authority.grant("dev.example.alpha", &[1]).unwrap();
    let a2 = authority.grant("dev.example.alpha", &[1]).unwrap();
    let b = authority.grant("dev.example.beta", &[1]).unwrap();
    a1.write(1, b"private").unwrap();
    b.write(1, b"other").unwrap();
    authority.revoke("dev.example.alpha").unwrap();
    assert_eq!(a1.read(1, 0, 10), Err(AppDataError::Denied));
    assert_eq!(a2.size(1), Err(AppDataError::Denied));
    assert!(matches!(authority.grant("dev.example.alpha", &[1]), Err(AppDataError::Denied)));
    assert_eq!(b.read(1, 0, 10).unwrap(), b"other");
    authority.authorize("dev.example.alpha").unwrap();
    assert_eq!(authority.grant("dev.example.alpha", &[1]).unwrap().read(1, 0, 10).unwrap(), b"private");
    assert_eq!(a1.size(1), Err(AppDataError::Denied));
    assert_eq!(a2.size(1), Err(AppDataError::Denied));
}

#[test]
fn package_authority_rejects_invalid_identity_and_does_not_restore_consent() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let authority = PackageStorageAuthority::new(root.clone());
    assert_eq!(authority.authorize("../escape"), Err(AppDataError::InvalidArgument));
    assert_eq!(authority.revoke("../escape"), Err(AppDataError::InvalidArgument));
    authority.authorize("dev.example.app").unwrap();
    authority.grant("dev.example.app", &[1]).unwrap().write(1, b"saved").unwrap();
    let new_authority = PackageStorageAuthority::new(root);
    assert!(matches!(new_authority.grant("dev.example.app", &[1]), Err(AppDataError::Denied)));
}

#[test]
fn package_authority_revoke_orders_against_concurrent_grants() {
    use std::sync::{Arc, Barrier};
    let f = Fixture::new();
    let authority = Arc::new(PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap()));
    authority.authorize("dev.example.app").unwrap();
    let barrier = Arc::new(Barrier::new(17));
    let threads: Vec<_> = (0..16).map(|_| {
        let authority = Arc::clone(&authority);
        let barrier = Arc::clone(&barrier);
        std::thread::spawn(move || {
            barrier.wait();
            authority.grant("dev.example.app", &[1])
        })
    }).collect();
    barrier.wait();
    authority.revoke("dev.example.app").unwrap();
    for thread in threads {
        match thread.join().unwrap() {
            Ok(grant) => assert_eq!(grant.size(1), Err(AppDataError::Denied)),
            Err(error) => assert_eq!(error, AppDataError::Denied),
        }
    }
    assert!(matches!(authority.grant("dev.example.app", &[1]), Err(AppDataError::Denied)));
}

#[test]
fn package_authority_limits_live_grants_and_reclaims_dropped_entries() {
    let f = Fixture::new();
    let authority = PackageStorageAuthority::new(AppDataRoot::open(&f.0).unwrap());
    authority.authorize("dev.example.app").unwrap();
    let mut grants: Vec<_> = (0..64).map(|_| authority.grant("dev.example.app", &[1]).unwrap()).collect();
    assert!(matches!(authority.grant("dev.example.app", &[1]), Err(AppDataError::Io)));
    grants.pop();
    assert!(authority.grant("dev.example.app", &[1]).is_ok());
}

#[cfg(target_os = "linux")]
#[test]
fn persistent_authority_restores_decisions_and_revocation_across_restart() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let authority = PackageStorageAuthority::open_persistent(root.clone()).unwrap();
    assert!(matches!(authority.grant("dev.example.app", &[1]), Err(AppDataError::Denied)));
    assert!(authority.authorize("dev.example.app").unwrap().directory_synced);
    let old = authority.grant("dev.example.app", &[1]).unwrap();
    old.write(1, b"persistent").unwrap();
    assert!(matches!(PackageStorageAuthority::open_persistent(root.clone()), Err(AppDataError::Io)));
    drop(authority);
    assert_eq!(old.size(1), Err(AppDataError::Denied));
    let reopened = PackageStorageAuthority::open_persistent(root.clone()).unwrap();
    assert_eq!(reopened.grant("dev.example.app", &[1]).unwrap().read(1, 0, 32).unwrap(), b"persistent");
    assert!(reopened.revoke("dev.example.app").unwrap().directory_synced);
    drop(reopened);
    let final_session = PackageStorageAuthority::open_persistent(root).unwrap();
    assert!(matches!(final_session.grant("dev.example.app", &[1]), Err(AppDataError::Denied)));
}


#[cfg(target_os = "linux")]
#[test]
fn persistent_authority_rejects_corrupt_records_and_reports_commit_outcomes() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let authority = PackageStorageAuthority::open_persistent(root.clone()).unwrap();
    authority.fail_next_policy_commit();
    assert_eq!(authority.authorize("dev.example.app"), Err(AppDataError::Io));
    assert!(matches!(authority.grant("dev.example.app", &[1]), Err(AppDataError::Denied)));
    authority.fail_next_policy_directory_sync();
    assert!(!authority.authorize("dev.example.app").unwrap().directory_synced);
    let grant = authority.grant("dev.example.app", &[1]).unwrap();
    authority.fail_next_policy_commit();
    assert_eq!(authority.revoke("dev.example.app"), Err(AppDataError::Io));
    assert_eq!(grant.size(1), Err(AppDataError::Denied));
    assert!(matches!(authority.grant("dev.example.app", &[1]), Err(AppDataError::Denied)));
    assert!(authority.revoke("dev.example.app").unwrap().directory_synced);
    drop(authority);
    let reopened = PackageStorageAuthority::open_persistent(root.clone()).unwrap();
    assert!(matches!(reopened.grant("dev.example.app", &[1]), Err(AppDataError::Denied)));
    drop(reopened);
    fs::write(f.0.join("system-private-storage-policy-v1/slot-1"), b"invalid\n").unwrap();
    assert!(matches!(PackageStorageAuthority::open_persistent(root), Err(AppDataError::Io)));
}

#[cfg(target_os = "linux")]
#[test]
fn failed_revocation_blocks_reauthorization_until_durable_retry() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let authority = PackageStorageAuthority::open_persistent(root.clone()).unwrap();
    let id = "dev.example.app";
    authority.authorize(id).unwrap();
    let grant = authority.grant(id, &[1]).unwrap();
    authority.fail_next_policy_commit();
    assert_eq!(authority.revoke(id), Err(AppDataError::Io));
    assert_eq!(grant.size(1), Err(AppDataError::Denied));
    assert_eq!(authority.authorize(id), Err(AppDataError::Denied));
    assert!(matches!(authority.grant(id, &[1]), Err(AppDataError::Denied)));
    authority.fail_next_policy_directory_sync();
    assert!(!authority.revoke(id).unwrap().directory_synced);
    assert_eq!(authority.authorize(id), Err(AppDataError::Denied));
    assert!(authority.revoke(id).unwrap().directory_synced);
    drop(authority);
    let reopened = PackageStorageAuthority::open_persistent(root).unwrap();
    assert!(matches!(reopened.grant(id, &[1]), Err(AppDataError::Denied)));
    reopened.authorize(id).unwrap();
}

#[cfg(target_os = "linux")]
#[test]
fn bound_revocation_retry_keeps_snapshot_and_rejects_new_generation() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let authority = std::sync::Arc::new(PackageStorageAuthority::open_persistent(root).unwrap());
    let id = "dev.example.bound";
    let fingerprint = [42; 32];
    authority.authorize_bound(id, fingerprint).unwrap();
    let snapshot = authority.authorization_snapshot(id).unwrap().unwrap();
    let session = authority.file_session_bound(id, fingerprint).unwrap();
    authority.fail_next_policy_commit();
    assert_eq!(authority.revoke_snapshot(id, &snapshot), Err(AppDataError::Io));
    assert_eq!(session.read_file("note"), Err(AppDataError::Denied));
    assert_eq!(authority.authorize_bound(id, fingerprint), Err(AppDataError::Denied));
    assert!(matches!(authority.file_session_bound(id, fingerprint), Err(AppDataError::Denied)));
    assert_eq!(authority.binding_status(id, fingerprint).unwrap(), PackageBinding::Conflict);
    assert!(authority.revoke_snapshot(id, &snapshot).unwrap().directory_synced);
    authority.authorize_bound(id, fingerprint).unwrap();
    assert_eq!(authority.revoke_snapshot(id, &snapshot), Err(AppDataError::Denied));
    assert!(authority.file_session_bound(id, fingerprint).is_ok());
}

#[cfg(target_os = "linux")]
#[test]
fn persistent_authority_lock_child_process() {
    let Some(path) = std::env::var_os("MING_TEST_STORAGE_LOCK_ROOT") else { return; };
    let root = AppDataRoot::open(path).unwrap();
    assert!(matches!(PackageStorageAuthority::open_persistent(root), Err(AppDataError::Io)));
}

#[cfg(target_os = "linux")]
#[test]
fn persistent_authority_lock_excludes_another_os_process() {
    let f = Fixture::new();
    let authority = PackageStorageAuthority::open_persistent(AppDataRoot::open(&f.0).unwrap()).unwrap();
    let output = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "persistent_authority_lock_child_process", "--nocapture"])
        .env("MING_TEST_STORAGE_LOCK_ROOT", &f.0)
        .output().unwrap();
    assert!(output.status.success(), "child failed: {}", String::from_utf8_lossy(&output.stderr));
    drop(authority);
}

#[cfg(target_os = "linux")]
#[test]
fn persistent_authority_drop_releases_lock_despite_inherited_descriptor() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let authority = PackageStorageAuthority::open_persistent(root.clone()).unwrap();
    let mut ready = [0; 2];
    let mut release = [0; 2];
    assert_eq!(unsafe { libc::pipe(ready.as_mut_ptr()) }, 0);
    assert_eq!(unsafe { libc::pipe(release.as_mut_ptr()) }, 0);
    // The child uses only async-signal-safe libc calls before _exit.
    let pid = unsafe { libc::fork() };
    assert!(pid >= 0);
    if pid == 0 {
        unsafe {
            libc::close(ready[0]);
            libc::close(release[1]);
            let byte = [1u8];
            libc::write(ready[1], byte.as_ptr().cast(), 1);
            let mut byte = [0u8];
            libc::read(release[0], byte.as_mut_ptr().cast(), 1);
            libc::_exit(0);
        }
    }
    unsafe { libc::close(ready[1]); libc::close(release[0]); }
    let mut byte = [0u8];
    assert_eq!(unsafe { libc::read(ready[0], byte.as_mut_ptr().cast(), 1) }, 1);
    drop(authority);
    let reopened = PackageStorageAuthority::open_persistent(root);
    unsafe { libc::close(release[1]); libc::close(ready[0]); }
    let mut status = 0;
    assert_eq!(unsafe { libc::waitpid(pid, &mut status, 0) }, pid);
    assert!(reopened.is_ok(), "the inherited descriptor retained the authority lock");
}

#[cfg(target_os = "linux")]
#[test]
fn persistent_authority_child_process() {
    let Some(path) = std::env::var_os("MING_TEST_STORAGE_ROOT") else { return; };
    let authority = PackageStorageAuthority::open_persistent(AppDataRoot::open(path).unwrap()).unwrap();
    assert_eq!(authority.grant("dev.example.app", &[1]).unwrap().read(1, 0, 32).unwrap(), b"from-parent");
    assert!(authority.revoke("dev.example.app").unwrap().directory_synced);
}

#[cfg(target_os = "linux")]
#[test]
fn persistent_authority_round_trips_through_a_new_os_process() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    let authority = PackageStorageAuthority::open_persistent(root.clone()).unwrap();
    assert!(authority.authorize("dev.example.app").unwrap().directory_synced);
    authority.grant("dev.example.app", &[1]).unwrap().write(1, b"from-parent").unwrap();
    drop(authority);
    let output = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "persistent_authority_child_process", "--nocapture"])
        .env("MING_TEST_STORAGE_ROOT", &f.0)
        .output().unwrap();
    assert!(output.status.success(), "child failed: {}", String::from_utf8_lossy(&output.stderr));
    let authority = PackageStorageAuthority::open_persistent(root).unwrap();
    assert!(matches!(authority.grant("dev.example.app", &[1]), Err(AppDataError::Denied)));
}

#[cfg(target_os = "linux")]
#[test]
fn persistent_authority_rejects_noncanonical_or_malformed_records() {
    let f = Fixture::new();
    let root = AppDataRoot::open(&f.0).unwrap();
    drop(PackageStorageAuthority::open_persistent(root.clone()).unwrap());
    let record = f.0.join("system-private-storage-policy-v1/slot-1");
    for contents in [
        "",
        "ming-private-storage-policy-v1\ndev.example.app",
        "ming-private-storage-policy-v1\ndev.example.app\ndev.example.app\n",
        "ming-private-storage-policy-v1\ndev.example.z\ndev.example.a\n",
        "ming-private-storage-policy-v1\n../escape\n",
    ] {
        fs::write(&record, contents).unwrap();
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&record, fs::Permissions::from_mode(0o600)).unwrap();
        assert!(matches!(PackageStorageAuthority::open_persistent(root.clone()), Err(AppDataError::Io)));
    }
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
    let has_dac_override = std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|status| {
            status
                .lines()
                .find_map(|line| line.strip_prefix("CapEff:\t"))
                .and_then(|value| u64::from_str_radix(value.trim(), 16).ok())
        })
        .is_some_and(|effective| effective & (1 << 1) != 0);
    if has_dac_override {
        eprintln!("skipping permission-denial probe: CAP_DAC_OVERRIDE is effective");
        return;
    }
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
