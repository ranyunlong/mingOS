#![cfg(feature = "desktop")]
use ming_app_host::desktop_catalog::{DesktopAction, DesktopOptions, load_catalog};
use std::{ffi::OsString, fs};
fn args(v: &[&str]) -> Vec<OsString> {
    v.iter().map(OsString::from).collect()
}
#[test]
fn desktop_flags_are_bounded_and_duplicate_unknown_arguments_rejected() {
    let o = DesktopOptions::parse(args(&[
        "catalog.json",
        "--renderer",
        "cpu",
        "--backend",
        "offscreen",
        "--width",
        "800",
        "--height",
        "600",
        "--duration-ms",
        "1000",
    ]))
    .unwrap();
    assert_eq!((o.width, o.height, o.duration_ms), (800, 600, 1000));
    assert!(o.offscreen);
    let install_root = tempfile::tempdir().unwrap();
    let configured = DesktopOptions::parse(args(&[
        "catalog.json",
        "--install-root",
        install_root.path().to_str().unwrap(),
    ])).unwrap();
    assert_eq!(configured.install_root.as_deref(), Some(install_root.path()));
    for bad in [
        args(&[]),
        args(&["x", "--renderer", "typo"]),
        args(&["x", "--width", "319"]),
        args(&["x", "--width", "4096", "--height", "4096"]),
        args(&["x", "--duration-ms", "60001"]),
        args(&["x", "--backend", "fake"]),
        args(&["x", "--width", "800", "--width", "900"]),
        args(&["x", "--grant", "1=secret"]),
    ] {
        assert!(DesktopOptions::parse(bad).is_err());
    }
}

#[test]
fn trusted_install_source_is_absolute_and_singleton() {
    let dir = tempfile::tempdir().unwrap();
    let source = dir.path().join("app.json");
    let options = DesktopOptions::parse(args(&[
        "catalog.json",
        "--install-source",
        source.to_str().unwrap(),
    ])).unwrap();
    assert_eq!(options.install_source.as_deref(), Some(source.as_path()));
    assert!(DesktopOptions::parse(args(&["catalog.json", "--install-source", "relative/app.json"])).is_err());
    assert!(DesktopOptions::parse(args(&[
        "catalog.json",
        "--install-source",
        source.to_str().unwrap(),
        "--install-source",
        source.to_str().unwrap(),
    ])).is_err());
    let overlong = format!("/{}", "a".repeat(4096));
    assert!(DesktopOptions::parse(args(&["catalog.json", "--install-source", &overlong])).is_err());
}

#[test]
fn reviewed_event_script_extracts_timed_actions_from_wrapped_steps() {
    let dir = tempfile::tempdir().unwrap();
    let events = dir.path().join("events.json");
    fs::write(&events, r#"{
        "schemaVersion": 1,
        "requireApps": [1],
        "steps": [
            {"name":"install","afterMs":1000,"action":{"op":"install","source":"/tmp/app.json"},"events":[],"expect":{}},
            {"name":"wait","events":[],"expect":{}}
        ]
    }"#).unwrap();
    let options = DesktopOptions::parse(vec![
        OsString::from("catalog.json"),
        OsString::from("--duration-ms"),
        OsString::from("0"),
        OsString::from("--events"),
        events.into_os_string(),
    ]).unwrap();
    assert_eq!(options.actions.len(), 1);
    assert_eq!(options.actions[0].after_ms, 1000);
    assert!(matches!(options.actions[0].action, DesktopAction::Install { .. }));
}
#[test]
fn malformed_catalogs_fail_before_any_application_launch() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("catalog.json");
    for content in [
        r#"{}"#,
        r#"{"schemaVersion":2,"apps":[],"autostart":[]}"#,
        r#"{"schemaVersion":1,"apps":[{"id":0,"title":"X","manifest":"x"}],"autostart":[]}"#,
        r#"{"schemaVersion":1,"apps":[{"id":1,"title":"X","manifest":"x"},{"id":1,"title":"Y","manifest":"y"}],"autostart":[1]}"#,
        r#"{"schemaVersion":1,"apps":[{"id":1,"title":"中文","manifest":"x"}],"autostart":[1]}"#,
        r#"{"schemaVersion":1,"apps":[{"id":1,"title":"X","manifest":"x"}],"autostart":[2]}"#,
    ] {
        fs::write(&file, content).unwrap();
        assert!(load_catalog(&file).is_err());
    }
}

#[test]
fn valid_catalog_resolves_packages_relative_to_its_own_directory() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir(dir.path().join("app")).unwrap();
    fs::write(dir.path().join("app/main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    fs::write(dir.path().join("app/app.json"),r#"{"schemaVersion":1,"id":"dev.test.desktop","version":"0.1.0","sdkVersion":2,"entry":"main.wasm"}"#).unwrap();
    let file = dir.path().join("catalog.json");
    fs::write(&file,r#"{"schemaVersion":1,"apps":[{"id":7,"title":"Counter","manifest":"app/app.json"}],"autostart":[7]}"#).unwrap();
    let c = load_catalog(&file).unwrap();
    assert_eq!(c.autostart, vec![7]);
    assert_eq!(c.apps[0].manifest_path, dir.path().join("app/app.json"));
}

#[test]
fn catalog_rejects_distinct_entries_with_the_same_package_identity() {
    let dir = tempfile::tempdir().unwrap();
    for name in ["a", "b"] {
        fs::create_dir(dir.path().join(name)).unwrap();
        fs::write(dir.path().join(name).join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
        fs::write(dir.path().join(name).join("app.json"), r#"{"schemaVersion":1,"id":"dev.example.same","version":"1.0.0","sdkVersion":6,"entry":"main.wasm"}"#).unwrap();
    }
    let catalog = dir.path().join("catalog.json");
    fs::write(&catalog, r#"{"schemaVersion":1,"apps":[{"id":1,"title":"A","manifest":"a/app.json"},{"id":2,"title":"B","manifest":"b/app.json"}],"autostart":[]}"#).unwrap();
    assert!(load_catalog(&catalog).is_err());
}
