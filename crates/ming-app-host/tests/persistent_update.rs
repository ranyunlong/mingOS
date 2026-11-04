#![cfg(all(target_os = "linux", feature = "desktop"))]

use ming_app_host::persistent_update::parse_update_record;
use serde_json::{Value, json};

fn record() -> Value {
    let hash = "a".repeat(64);
    let identity = |version| json!({"packageId":"dev.ming.example", "version":version,
        "fingerprint":hash, "manifestSha256":hash, "wasmSha256":hash, "migrationSha256":hash});
    json!({"schemaVersion":1,"operation":"update","transactionId":"task3-1","app":2,
        "old":identity("1.0.0"),"new":identity("2.0.0"),"catalogSha256":hash,
        "bootstrapSha256":hash,"trustSha256":hash,"siblingRecordsSha256":hash,
        "preservedContentSha256":hash,"candidateContentExistedBefore":false,
        "dataRootBindingSha256":hash,"publicPolicySlot":null,
        "privateBefore":{"schemaVersion":1,"packageId":"dev.ming.example",
            "namespaceExists":false,"files":null,"nonFilesSha256":hash},
        "backupExpected":false,"decision":"prepared","confirmation":null,"privateConfirmed":null})
}
fn parse(value: &Value) -> bool { parse_update_record(&serde_json::to_vec(value).unwrap()).is_ok() }

#[test]
fn update_record_rejects_truncated_duplicate_and_oversized_json() {
    for raw in [b"{".as_slice(), b"{\"schemaVersion\":1,\"schemaVersion\":1}".as_slice()] {
        assert!(parse_update_record(raw).is_err());
    }
    assert!(parse_update_record(&vec![b' ';4097]).is_err());
    let raw = serde_json::to_string(&record()).unwrap();
    assert!(parse_update_record(raw.replacen('{', "{\"schemaVersion\":1,", 1).as_bytes()).is_err());
}
#[test]
fn update_record_requires_every_field_and_rejects_unknown_fields() {
    let valid=record(); assert!(parse(&valid));
    for key in valid.as_object().unwrap().keys() {
        let mut missing=valid.clone(); missing.as_object_mut().unwrap().remove(key);
        assert!(!parse(&missing), "missing {key}");
    }
    let mut unknown=valid; unknown["unknown"]=json!(0); assert!(!parse(&unknown));
}
#[test]
fn update_record_binds_identity_private_baseline_and_backup() {
    for (path,value) in [("app",json!(1)),("schemaVersion",json!(2)),("operation",json!("install")),
        ("transactionId",json!("../escape")),("catalogSha256",json!("A".repeat(64))),
        ("backupExpected",json!(true))] {
        let mut invalid=record(); invalid[path]=value; assert!(!parse(&invalid),"{path}");
    }
    for (path,value) in [("packageId",json!("other.app")),("version",json!("1.0.0")),("version",json!("01.0.0"))] {
        let mut invalid=record(); invalid["new"][path]=value; assert!(!parse(&invalid),"new {path}");
    }
    let mut invalid=record(); invalid["privateBefore"]["packageId"]=json!("other.app"); assert!(!parse(&invalid));
    invalid=record(); invalid["privateBefore"]["files"]=json!({"entries":1,"bytes":0,"sha256":"a".repeat(64)}); assert!(!parse(&invalid));
}
#[test]
fn update_record_retains_existing_numeric_version_compatibility() {
    let mut value=record();value["old"]["version"]=json!("01.0.0");value["new"]["version"]=json!("02.0.0");assert!(parse(&value));
    value["new"]["version"]=json!(format!("{}.0.0","9".repeat(91)));assert!(parse(&value));
    value["new"]["version"]=json!(format!("{}.0.0","9".repeat(92)));assert!(!parse(&value));
}
fn committed() -> Value {
    let mut value=record(); value["decision"]=json!("commit-decided");
    value["confirmation"]=json!({"app":2,"package":value["new"],"generation":1,"window":1,
        "workerPid":1,"inputReady":true,"presentedSourceSequence":1,"completed":true,
        "resultAccepted":true,"nativeExitOk":true,"reaped":true});
    value["privateConfirmed"]=value["privateBefore"].clone(); value
}
#[test]
fn update_record_requires_complete_bound_confirmation() {
    let valid=committed(); assert!(parse(&valid));
    for key in ["generation","window","workerPid","presentedSourceSequence"] {
        let mut invalid=valid.clone(); invalid["confirmation"][key]=json!(0); assert!(!parse(&invalid),"{key}");
    }
    for key in ["inputReady","completed","resultAccepted","nativeExitOk","reaped"] {
        let mut invalid=valid.clone(); invalid["confirmation"][key]=json!(false); assert!(!parse(&invalid),"{key}");
    }
    for key in ["confirmation","privateConfirmed"] {
        let mut invalid=valid.clone(); invalid[key]=Value::Null; assert!(!parse(&invalid),"{key}");
        invalid=record(); invalid[key]=valid[key].clone(); assert!(!parse(&invalid),"prepared {key}");
    }
    let mut invalid=valid.clone(); invalid["confirmation"]["package"]["manifestSha256"]=json!("b".repeat(64)); assert!(!parse(&invalid));
    invalid=valid; invalid["privateConfirmed"]["nonFilesSha256"]=json!("b".repeat(64)); assert!(!parse(&invalid));
}
#[test]
fn update_record_public_slot_distinguishes_absent_and_present_and_is_required() {
    let mut value=record(); let absent=parse_update_record(&serde_json::to_vec(&value).unwrap()).unwrap();
    value["publicPolicySlot"]=json!({"bytes":30,"sha256":"a".repeat(64)});
    let present=parse_update_record(&serde_json::to_vec(&value).unwrap()).unwrap(); assert_ne!(absent,present);
    value["publicPolicySlot"]["bytes"]=json!(0); assert!(!parse(&value));
    value["publicPolicySlot"]=json!({"bytes":30,"sha256":"bad"}); assert!(!parse(&value));
}
#[test]
fn update_record_maximum_legal_commit_fits_bounded_outer() {
    let mut value=committed(); value["transactionId"]=json!("x".repeat(96));
    let id=format!("a.{}","a".repeat(126));
    for key in ["old","new"] { value[key]["packageId"]=json!(id); }
    value["confirmation"]["package"]=value["new"].clone();
    for key in ["privateBefore","privateConfirmed"] { value[key]["packageId"]=json!(id); }
    value["publicPolicySlot"]=json!({"bytes":65536,"sha256":"a".repeat(64)});
    value["old"]["version"]=json!(format!("{}.9.9","8".repeat(91)));
    value["new"]["version"]=json!(format!("{}.9.9","9".repeat(91)));
    value["confirmation"]["package"]=value["new"].clone();
    for key in ["generation","window","presentedSourceSequence"] { value["confirmation"][key]=json!(u64::MAX); }
    value["confirmation"]["workerPid"]=json!(u32::MAX);
    for key in ["privateBefore","privateConfirmed"] {
        value[key]["namespaceExists"]=json!(true);
        value[key]["files"]=json!({"entries":1024,"bytes":8388608,"sha256":"a".repeat(64)});
    }
    value["backupExpected"]=json!(true);
    let raw=serde_json::to_vec(&value).unwrap(); assert!(raw.len()<=4096,"{}",raw.len());
    assert!(parse_update_record(&raw).is_ok());
}

#[test]
fn public_trial_and_explicit_recovery_preserve_catalog_and_original_content() {
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use ed25519_dalek::{Signer, SigningKey};
    use ming_app_host::{app_data::{AppDataRoot,PackageStorageAuthority,observe_private_update_read_only},
        package::{Manifest,Package}, persistent_install,
        persistent_update::{begin_existing_update,recover_existing_update,UpdateRecovery}};
    use std::{fs,os::unix::fs::PermissionsExt};
    let temp = tempfile::tempdir().unwrap();
    fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
    let key = SigningKey::from_bytes(&[7;32]);
    let sign = |package: &mut Package| {
        package.manifest.publisher_signature = None;
        package.manifest.publisher_signature = Some(STANDARD.encode(key.sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
    };
    let mut package = Package {manifest: Manifest {schema_version:1,id:"dev.ming.text-entry-demo".into(),version:"0.1.0".into(),
        sdk_version:6,entry:"main.wasm".into(),private_storage:false,public_storage:false,data_migration:None,
        publisher_key_id:Some("mingos-dev-2026".into()),publisher_signature:None},bytes:b"\0asm\x01\0\0\0".to_vec()};
    sign(&mut package);
    let root = temp.path().join("install-root");
    persistent_install::initialize_first(&root,&package,"mingos-dev-2026",key.verifying_key().as_bytes(),
        br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#).unwrap();
    let bootstrap = persistent_install::open_existing(&root).unwrap();
    package.manifest.id = "dev.ming.public-update-test".into(); package.manifest.version = "1.0.0".into(); sign(&mut package);
    let source = temp.path().join("source"); fs::create_dir(&source).unwrap();
    fs::write(source.join("app.json"),serde_json::to_vec(&package.manifest).unwrap()).unwrap();
    fs::write(source.join("main.wasm"),&package.bytes).unwrap();
    bootstrap.registry.install_from_manifest(&source.join("app.json")).unwrap();
    let catalog = br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"},{"id":2,"title":"Update Test","packageId":"dev.ming.public-update-test"}],"autostart":[1]}"#;
    fs::write(root.join("catalog.json"),catalog).unwrap();
    drop(bootstrap);
    let opened = persistent_install::open_existing(&root).unwrap(); let old = opened.content.clone();
    let data = temp.path().join("data"); fs::create_dir(&data).unwrap(); fs::set_permissions(&data,fs::Permissions::from_mode(0o700)).unwrap();
    let authority = PackageStorageAuthority::open_persistent(AppDataRoot::open(&data).unwrap()).unwrap();
    let baseline = observe_private_update_read_only(&data,&package.manifest.id).unwrap();
    package.manifest.version = "2.0.0".into(); sign(&mut package);
    let trial = begin_existing_update(&opened.registry,&authority,&data,&package,2).unwrap();
    assert_eq!(trial.record.private_before,baseline);
    assert!(persistent_install::snapshot(&opened.registry).is_err());
    drop(opened); drop(authority);
    let UpdateRecovery::RolledBack(content) = recover_existing_update(&root,&data).unwrap() else {panic!("prepared must roll back")};
    assert_eq!(content.catalog_sha256,old.catalog_sha256);
    assert_eq!(content.apps,old.apps);
    assert!(old.files.iter().all(|file|content.files.contains(file)));
    assert_eq!(observe_private_update_read_only(&data,&package.manifest.id).unwrap(),baseline);
    assert_eq!(persistent_install::open_existing(&root).unwrap().content,content);
}

#[test]
fn public_recovery_rejects_missing_roots_without_creating_them() {
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("missing-install"); let data = temp.path().join("missing-data");
    assert!(ming_app_host::persistent_update::recover_existing_update(&root,&data).is_err());
    assert!(!root.exists()); assert!(!data.exists());
}
