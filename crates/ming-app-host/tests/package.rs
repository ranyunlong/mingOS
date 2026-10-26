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
