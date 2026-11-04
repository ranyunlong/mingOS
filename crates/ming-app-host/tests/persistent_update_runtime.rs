#![cfg(all(target_os = "linux", feature = "desktop"))]

use ming_app_host::{app_data::{AppDataRoot, PackageStorageAuthority}, package, persistent_install, persistent_update};
use std::{fs, os::unix::fs::{MetadataExt, PermissionsExt}, path::{Path, PathBuf}, process::{Child, Command, ExitStatus, Stdio}, thread, time::{Duration, Instant}};

struct ChildGuard(Child);
impl Drop for ChildGuard { fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); } }
fn executable(name: &str) -> PathBuf {
    let path = PathBuf::from(std::env::var_os(name).unwrap_or_else(|| panic!("explicit {name} required")));
    assert!(path.is_absolute() && path.is_file()); path
}
fn run(mut command: Command, logs: &Path, name: &str) -> (ExitStatus, Vec<u8>, Vec<u8>) {
    let stdout = logs.join(format!("{name}.stdout")); let stderr = logs.join(format!("{name}.stderr"));
    command.stdout(Stdio::from(fs::File::create(&stdout).unwrap())).stderr(Stdio::from(fs::File::create(&stderr).unwrap()));
    let mut child = ChildGuard(command.spawn().unwrap()); let deadline = Instant::now() + Duration::from_secs(120);
    let status = loop {
        if let Some(status) = child.0.try_wait().unwrap() { break status; }
        assert!(Instant::now() < deadline, "{name} timed out"); thread::sleep(Duration::from_millis(5));
    };
    assert!(fs::metadata(&stdout).unwrap().len() < 2 * 1024 * 1024 && fs::metadata(&stderr).unwrap().len() < 2 * 1024 * 1024);
    (status, fs::read(stdout).unwrap(), fs::read(stderr).unwrap())
}
fn tree(root: &Path) -> Vec<(PathBuf, u64, u32, Vec<u8>)> {
    fn visit(path: &Path, out: &mut Vec<(PathBuf, u64, u32, Vec<u8>)>) {
        let m = fs::symlink_metadata(path).unwrap(); let bytes = if m.is_file() { fs::read(path).unwrap() } else { vec![] };
        out.push((path.into(), m.ino(), m.mode(), bytes));
        if m.is_dir() { let mut paths: Vec<_> = fs::read_dir(path).unwrap().map(|p| p.unwrap().path()).collect(); paths.sort(); for path in paths { visit(&path, out); } }
    }
    let mut out = vec![]; visit(root, &mut out); out
}
fn helper(binary: &Path, role: &str, root: &Path, data: &Path, receipt: &Path) -> Command {
    let mut cmd = Command::new(binary); cmd.arg(role).arg(root).arg(data).arg(receipt); cmd
}
fn host(root: &Path, data: &Path, events: &Path) -> Command {
    let mut cmd = Command::new(executable("MING_TEST_PRIVATE_WORKER"));
    cmd.arg("desktop").arg(root.join("catalog.json")).args(["--backend", "offscreen", "--renderer", "cpu", "--duration-ms", "100", "--persistent-install-state"])
        .arg(root).arg("--data-root").arg(data).arg("--events").arg(events); cmd
}

#[test]
#[ignore = "requires explicit packaged Kotlin modules and release Host/helper binaries"]
fn separate_process_roles_and_real_stable_host_refuse_active_update_without_writes() {
    let temp = tempfile::tempdir().unwrap(); fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let data = temp.path().join("data"); fs::create_dir(&data).unwrap(); fs::set_permissions(&data, fs::Permissions::from_mode(0o700)).unwrap();
    let root = data.join("install-root"); let source = temp.path().join("source/app.json");
    let catalog = temp.path().join("initial.json"); let bytes = br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#;
    fs::write(&catalog, bytes).unwrap(); let binary = executable("MING_UPDATE_FIXTURE_HELPER");
    let mut initialize = Command::new(&binary);
    initialize.arg("initialize-first").arg(&root).arg(&data).arg(executable("MING_UPDATE_BOOTSTRAP_WASM")).arg(executable("MING_UPDATE_OLD_WASM"))
        .arg(executable("MING_UPDATE_NEW_WASM")).arg(&source).arg(catalog).arg(persistent_install::sha256(bytes)).arg("seed").arg(temp.path().join("initial-receipt.json"));
    let (status, stdout, stderr) = run(initialize, temp.path(), "initialize"); assert!(status.success(), "{}", String::from_utf8_lossy(&stderr));
    let initialized: serde_json::Value = serde_json::from_slice(&stdout).unwrap(); assert_eq!(initialized["actions"]["uiUpdatesCommitted"], 0);
    let events = temp.path().join("events.json");
    fs::write(&events, br#"[{"afterMs":0,"action":{"op":"close","app":1}},{"afterMs":0,"action":{"op":"launch","app":2}},{"afterMs":1,"action":{"op":"close","app":2}}]"#).unwrap();
    let (status, stdout, stderr) = run(host(&root, &data, &events), temp.path(), "stable-host"); assert!(status.success(), "{}", String::from_utf8_lossy(&stderr));
    let outcome: serde_json::Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(outcome["activeWorkersAfterShutdown"], 0);
    let app = outcome["instances"].as_array().unwrap().iter().find(|a| a["app"] == 2).unwrap();
    assert_eq!(app["completed"], true); assert!(app["error"].is_null()); assert!(app["workerPid"].as_u64().unwrap() > 0);
    assert!(app["firstReadyMs"].is_number() && app["framesSubmitted"].as_u64().unwrap() > 0);
    assert_eq!(unsafe { libc::kill(app["workerPid"].as_u64().unwrap() as i32, 0) }, -1);
    let (status, _, stderr) = run(helper(&binary, "open-existing", &root, &data, &temp.path().join("opened.json")), temp.path(), "opened"); assert!(status.success(), "{}", String::from_utf8_lossy(&stderr));
    let opened = persistent_install::open_existing(&root).unwrap();
    let authority = PackageStorageAuthority::open_persistent(AppDataRoot::open(&data).unwrap()).unwrap();
    let package = package::load_package(&source).unwrap();
    let prepared = persistent_update::begin_existing_update(&opened.registry, &authority, &data, &package, 2).unwrap();
    drop(opened); drop(authority); let before = tree(&data);
    let (status, _, _) = run(helper(&binary, "open-existing", &root, &data, &temp.path().join("blocked.json")), temp.path(), "blocked-open"); assert!(!status.success()); assert_eq!(tree(&data), before);
    let (status, _, _) = run(host(&root, &data, &events), temp.path(), "blocked-host"); assert!(!status.success()); assert_eq!(tree(&data), before);
    let (status, stdout, stderr) = run(helper(&binary, "recover-update", &root, &data, &temp.path().join("recovered.json")), temp.path(), "recovered"); assert!(status.success(), "{}", String::from_utf8_lossy(&stderr));
    let recovered: serde_json::Value = serde_json::from_slice(&stdout).unwrap(); assert_eq!(recovered["status"], "recovered-rolled-back");
    assert_eq!(recovered["privateContent"], serde_json::to_value(prepared.record.private_before).unwrap());
    let before = tree(&data);
    let (status, _, stderr) = run(helper(&binary, "open-existing", &root, &data, &temp.path().join("reopened.json")), temp.path(), "reopened"); assert!(status.success(), "{}", String::from_utf8_lossy(&stderr)); assert_eq!(tree(&data), before);
    eprintln!("REAL_STABLE_HOST_RECORD {}", app);
}
