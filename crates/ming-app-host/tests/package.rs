use ming_app_host::package::load_package;
use serde_json::json;
use std::fs;
#[test]
fn accepts_only_supported_manifest_and_local_wasm_entry() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    let base = json!({"schemaVersion":1,"id":"dev.ming.app","version":"0.1.0","sdkVersion":1,"entry":"main.wasm"});
    let file = dir.path().join("app.json");
    fs::write(&file, base.to_string()).unwrap();
    let package = load_package(&file).unwrap();
    assert_eq!(package.manifest.id, "dev.ming.app");
    for sdk in 1..=6 {
        let mut supported = base.clone();
        supported["sdkVersion"] = json!(sdk);
        fs::write(&file, supported.to_string()).unwrap();
        assert_eq!(load_package(&file).unwrap().manifest.sdk_version, sdk);
    }
    for (key, value) in [
        ("sdkVersion", json!(0)),
        ("sdkVersion", json!(7)),
        ("entry", json!("../escape.wasm")),
        ("entry", json!("C:\\file.wasm")),
        ("permissions", json!(["all"])),
        ("id", json!("../bad")),
    ] {
        let mut value_copy = base.clone();
        value_copy[key] = value;
        fs::write(&file, value_copy.to_string()).unwrap();
        assert!(
            load_package(&file)
                .unwrap_err()
                .to_string()
                .contains("MANIFEST_")
        );
    }
}

#[test]
fn private_storage_is_explicit_and_requires_sdk_six() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    let file = dir.path().join("app.json");
    let mut manifest = json!({"schemaVersion":1,"id":"dev.ming.app","version":"0.1.0","sdkVersion":6,"entry":"main.wasm"});
    fs::write(&file, manifest.to_string()).unwrap();
    assert!(!load_package(&file).unwrap().manifest.private_storage);
    manifest["privateStorage"] = json!(true);
    fs::write(&file, manifest.to_string()).unwrap();
    assert!(load_package(&file).unwrap().manifest.private_storage);
    manifest["sdkVersion"] = json!(5);
    fs::write(&file, manifest.to_string()).unwrap();
    assert!(load_package(&file).is_err());
    manifest["privateStorage"] = json!("yes");
    fs::write(&file, manifest.to_string()).unwrap();
    assert!(load_package(&file).is_err());
}

#[test]
fn public_storage_request_is_explicit_and_requires_sdk_six() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    let file = dir.path().join("app.json");
    let mut manifest = json!({"schemaVersion":1,"id":"dev.ming.app","version":"0.1.0","sdkVersion":6,"entry":"main.wasm"});
    fs::write(&file, manifest.to_string()).unwrap();
    assert!(!load_package(&file).unwrap().manifest.public_storage);
    manifest["publicStorage"] = json!(true);
    fs::write(&file, manifest.to_string()).unwrap();
    let requested = load_package(&file).unwrap();
    assert!(requested.manifest.public_storage);
    manifest["sdkVersion"] = json!(5);
    fs::write(&file, manifest.to_string()).unwrap();
    assert!(load_package(&file).is_err());
}

#[test]
fn data_migration_plan_is_loaded_and_is_part_of_package_identity() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    let file = dir.path().join("app.json");
    let base = json!({
        "schemaVersion":1,
        "id":"dev.ming.app",
        "version":"1.1.0",
        "sdkVersion":6,
        "entry":"main.wasm"
    });
    fs::write(&file, base.to_string()).unwrap();
    let without = load_package(&file).unwrap();
    let without_identity = serde_json::to_vec(&without.manifest.data_migration).unwrap();

    let mut migrated = base;
    migrated["dataMigration"] = json!({
        "fromVersion":"1.0.0",
        "steps":[
            {"op":"ensureDir","path":"state"},
            {"op":"renameFile","from":"counter","to":"state/counter"}
        ]
    });
    fs::write(&file, migrated.to_string()).unwrap();
    let with = load_package(&file).unwrap();
    assert!(with.manifest.data_migration.is_some());
    let with_identity = serde_json::to_vec(&with.manifest.data_migration).unwrap();
    assert_ne!(without_identity, with_identity);
}

#[test]
fn data_migration_plan_rejects_unknown_operations_and_unsafe_paths() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    let file = dir.path().join("app.json");
    for plan in [
        json!({"fromVersion":"1.0.0","steps":[{"op":"runCode","path":"x"}]}),
        json!({"fromVersion":"1.0.0","steps":[{"op":"deleteFile","path":"../escape"}]}),
    ] {
        let manifest = json!({
            "schemaVersion":1,
            "id":"dev.ming.app",
            "version":"1.1.0",
            "sdkVersion":6,
            "entry":"main.wasm",
            "dataMigration":plan
        });
        fs::write(&file, manifest.to_string()).unwrap();
        assert!(load_package(&file).is_err());
    }
}

#[test]
fn migration_versions_use_numeric_components() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    let file = dir.path().join("app.json");
    for (from, to, accepted) in [
        ("1.9.0", "1.10.0", true),
        ("1.10.0", "1.9.0", false),
        ("1.0.9", "1.0.10", true),
        ("01.00.0", "1.0.0", false),
        ("1.0.0", "2.0.0", true),
    ] {
        let manifest = json!({"schemaVersion":1,"id":"dev.ming.app","version":to,
            "sdkVersion":6,"entry":"main.wasm","dataMigration":{"fromVersion":from,
            "steps":[{"op":"ensureDir","path":"state"}]}});
        fs::write(&file, manifest.to_string()).unwrap();
        assert_eq!(load_package(&file).is_ok(), accepted, "{from} -> {to}");
    }
}

#[test]
fn migration_paths_follow_private_file_component_bounds() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("main.wasm"), b"\0asm\x01\0\0\0").unwrap();
    let file = dir.path().join("app.json");
    for (path, accepted) in [("x".repeat(256), false), ("x".repeat(255), true),
        ("状态/计数".to_owned(), true), ("/escape".to_owned(), false)] {
        let manifest = json!({"schemaVersion":1,"id":"dev.ming.app","version":"1.1.0",
            "sdkVersion":6,"entry":"main.wasm","dataMigration":{"fromVersion":"1.0.0",
            "steps":[{"op":"ensureDir","path":path}]}});
        fs::write(&file, manifest.to_string()).unwrap();
        assert_eq!(load_package(&file).is_ok(), accepted, "{path}");
    }
}
