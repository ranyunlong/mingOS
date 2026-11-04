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

// Exercise the exact production modules with crate visibility without compiling
// the engine's full lib-test harness or exporting transaction-only capabilities.
#[path = "../src/app_data.rs"] mod app_data;
#[path = "../src/package.rs"] mod package;
#[path = "../src/package_install.rs"] mod package_install;
#[path = "../src/publisher.rs"] mod publisher;
mod graphics { pub use ming_app_host::graphics::RendererKind; }
#[path = "../src/desktop_catalog.rs"] mod desktop_catalog;
#[path = "../src/persistent_install.rs"] mod persistent_install;
#[path = "../src/persistent_update.rs"] mod persistent_update;

mod update_transaction_tests {
    use super::{app_data::{AppDataRoot,PackageStorageAuthority},package::Package,persistent_install,persistent_update::*};
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use ed25519_dalek::{Signer,SigningKey};
    use std::{collections::BTreeMap,fs,os::unix::fs::{MetadataExt,PermissionsExt},path::{Path,PathBuf}};
    fn write(path:&Path, bytes:&[u8]) { fs::write(path,bytes).unwrap(); fs::set_permissions(path,fs::Permissions::from_mode(0o600)).unwrap(); }
    fn fixture() -> (tempfile::TempDir,persistent_install::OpenedInstall,Package,PackageStorageAuthority,PathBuf) {
        let (temp,opened,old)=persistent_install::transaction_tests::fixture();
        persistent_install::install_new(&opened.registry,&old).unwrap();
        let mut new=old; new.manifest.version="2.0.0".into();
        new.manifest.publisher_signature=None;
        new.manifest.publisher_signature=Some(STANDARD.encode(SigningKey::from_bytes(&[7;32]).sign(&new.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        let data=temp.path().join("data"); fs::create_dir(&data).unwrap(); fs::set_permissions(&data,fs::Permissions::from_mode(0o700)).unwrap();
        let authority=PackageStorageAuthority::open_default_private(AppDataRoot::open(&data).unwrap()).unwrap();
        (temp,opened,new,authority,data)
    }
    fn candidate(root:&Path,new:&Package) -> PathBuf {
        let path=candidate_directory(root,&identity_from_package(new,&serde_json::to_vec(&new.manifest).unwrap()).unwrap());
        fs::create_dir(&path).unwrap(); fs::set_permissions(&path,fs::Permissions::from_mode(0o700)).unwrap();
        write(&path.join("app.json"),&serde_json::to_vec(&new.manifest).unwrap()); write(&path.join("main.wasm"),&new.bytes); path
    }
    fn validate(opened:&persistent_install::OpenedInstall, authority:&PackageStorageAuthority,data:&Path, record:&UpdateRecord) -> bool {
        let bytes=serde_json::to_vec(record).unwrap();
        write(&opened.registry.root_path().join(UPDATE_PENDING_NAME),&bytes);
        let before=tree(opened.registry.root_path());let data_before=tree(data);
        let result=validate_transaction_snapshot(&opened.registry,authority,data,record,&bytes,None).is_ok();
        assert_eq!(tree(opened.registry.root_path()),before);assert_eq!(tree(data),data_before);result
    }
    fn tree(root:&Path)->BTreeMap<PathBuf,(u32,u64,Vec<u8>)> {
        fn walk(root:&Path,path:&Path,out:&mut BTreeMap<PathBuf,(u32,u64,Vec<u8>)>) {
            let info=fs::symlink_metadata(path).unwrap();
            let bytes=if info.file_type().is_symlink(){fs::read_link(path).unwrap().as_os_str().as_encoded_bytes().to_vec()}
                else if info.is_file(){fs::read(path).unwrap()}else{Vec::new()};
            out.insert(path.strip_prefix(root).unwrap().into(),(info.mode(),info.nlink(),bytes));
            if info.is_dir(){for entry in fs::read_dir(path).unwrap(){walk(root,&entry.unwrap().path(),out);}}
        }
        let mut out=BTreeMap::new();walk(root,root,&mut out);out
    }
    #[test]
    fn update_transaction_allows_only_exact_signed_new_candidate_increment() {
        let (_temp,opened,new,authority,data)=fixture();
        let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
        let root=opened.registry.root_path();
        assert!(!record.candidate_content_existed_before);
        candidate(root,&new); assert!(validate(&opened,&authority,&data,&record));
        assert!(persistent_install::snapshot(&opened.registry).is_err());
        write(&candidate_directory(root,&record.new).join("main.wasm"),b"invalid");
        assert!(!validate(&opened,&authority,&data,&record));
    }
    #[test]
    fn update_transaction_existing_candidate_remains_in_preserved_digest() {
        let (_temp,opened,new,authority,data)=fixture(); candidate(opened.registry.root_path(),&new);
        let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
        assert!(record.candidate_content_existed_before); assert!(validate(&opened,&authority,&data,&record));
        let path=candidate_directory(opened.registry.root_path(),&record.new);
        fs::remove_file(path.join("app.json")).unwrap(); assert!(!validate(&opened,&authority,&data,&record));
    }
    #[test]
    fn update_transaction_rejects_extra_history_or_unknown_objects_without_writing() {
        for case in 0..6 {
            let (_temp,opened,new,authority,data)=fixture(); let root=opened.registry.root_path();
            let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
            let path=candidate(root,&new);
            match case {
                0=>write(&path.join("extra"),b"unexpected"),
                1=>{let extra=path.parent().unwrap().join(format!("3.0.0-{}","a".repeat(64)));fs::create_dir(&extra).unwrap();fs::set_permissions(&extra,fs::Permissions::from_mode(0o700)).unwrap();},
                2=>fs::remove_file(root.join(".ming-installed-packages.lock")).unwrap(),
                3=>{fs::remove_file(path.join("main.wasm")).unwrap();std::os::unix::fs::symlink("app.json",path.join("main.wasm")).unwrap();},
                4=>fs::hard_link(path.join("main.wasm"),path.join("alias")).unwrap(),
                5=>write(&root.join("desktop-install-pending-v1.json"),b"{}"),
                _=>unreachable!(),
            }
            assert!(!validate(&opened,&authority,&data,&record),"case {case}");
        }
    }
    #[test]
    fn update_transaction_rejects_bootstrap_target_removed_target_and_root_replacement() {
        let (temp,opened,new,authority,data)=fixture();
        assert!(prepare_record(&opened.registry,&authority,&data,1,&new,"task3-test").is_err());
        assert!(prepare_record(&opened.registry,&authority,&data,4,&new,"task3-test").is_err());
        let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
        let root=opened.registry.root_path().to_path_buf(); fs::rename(&root,temp.path().join("old-root")).unwrap();
        fs::create_dir(&root).unwrap(); fs::set_permissions(&root,fs::Permissions::from_mode(0o700)).unwrap();
        assert!(!validate(&opened,&authority,&data,&record));
    }
    #[test]
    fn update_transaction_public_slot_missing_is_legal_but_bound_existing_slot_cannot_disappear_or_change() {
        let (_temp,opened,new,authority,data)=fixture();
        let absent=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap(); assert!(absent.public_policy_slot.is_none());
        assert!(validate(&opened,&authority,&data,&absent));
        fs::remove_file(opened.registry.root_path().join(UPDATE_PENDING_NAME)).unwrap();
        let slot=data.join("system-public-storage-policy-v1/slot-1");
        write(&slot,b"ming-public-storage-policy-v1\n");
        let present=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap(); assert!(present.public_policy_slot.is_some());
        assert!(validate(&opened,&authority,&data,&present));
        fs::remove_file(&slot).unwrap(); assert!(!validate(&opened,&authority,&data,&present));
        write(&slot,b"ming-public-storage-policy-v1\ndev.ming.other\n"); assert!(!validate(&opened,&authority,&data,&present));
        write(&slot,b""); assert!(!validate(&opened,&authority,&data,&present));
    }
    #[test]
    fn update_transaction_public_policy_locks_and_root_are_bound_without_reopening_lock() {
        let (temp,opened,new,authority,data)=fixture();
        let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
        assert!(validate(&opened,&authority,&data,&record));
        fs::rename(&data,temp.path().join("original-data")).unwrap();
        fs::create_dir(&data).unwrap(); fs::set_permissions(&data,fs::Permissions::from_mode(0o700)).unwrap();
        assert!(!validate(&opened,&authority,&data,&record));
    }
    fn proof(record:&UpdateRecord)->UpdateLifecycleProof {
        UpdateLifecycleProof {app:record.app,package:record.new.clone(),generation:1,window:1,worker_pid:1,input_ready:true,
            presented_source_sequence:1,completed:true,result_accepted:true,native_exit_ok:true,reaped:true}
    }
    fn inner(record:&UpdateRecord,phase:&str)->Vec<u8> {
        serde_json::to_vec(&serde_json::json!({"packageId":record.old.package_id,"oldVersion":record.old.version,
            "oldFingerprint":record.old.fingerprint,"newVersion":record.new.version,"newFingerprint":record.new.fingerprint,
            "backupName":format!(".ming-update-backup-{}",record.transaction_id),"migrationDeclared":false,"phase":phase})).unwrap()
    }
    #[test]
    fn update_transaction_decision_and_inner_phase_reject_impossible_old_active_states() {
        let (_temp,opened,new,authority,data)=fixture();candidate(opened.registry.root_path(),&new);
        let mut record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
        let bytes=serde_json::to_vec(&record).unwrap();let journal=inner(&record,"startupConfirmed");
        write(&opened.registry.root_path().join(UPDATE_PENDING_NAME),&bytes);
        write(&opened.registry.root_path().join("update-journal-v1"),&journal);
        assert!(validate_transaction_snapshot(&opened.registry,&authority,&data,&record,&bytes,Some(&journal)).is_err(),"prepared startupConfirmed");
        record.confirmation=Some(proof(&record));record.private_confirmed=Some(record.private_before.clone());record.decision=UpdateDecision::CommitDecided;
        let bytes=serde_json::to_vec(&record).unwrap();write(&opened.registry.root_path().join(UPDATE_PENDING_NAME),&bytes);
        assert!(validate_transaction_snapshot(&opened.registry,&authority,&data,&record,&bytes,Some(&journal)).is_err(),"committed old active");
    }
    #[test]
    fn update_transaction_requires_authorized_marker_files_to_exist() {
        let (_temp,opened,new,authority,data)=fixture();
        let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
        let bytes=serde_json::to_vec(&record).unwrap();
        assert!(validate_transaction_snapshot(&opened.registry,&authority,&data,&record,&bytes,None).is_err(),"missing outer");
        candidate(opened.registry.root_path(),&new);
        write(&opened.registry.root_path().join(UPDATE_PENDING_NAME),&bytes);
        let journal=inner(&record,"planned");
        assert!(validate_transaction_snapshot(&opened.registry,&authority,&data,&record,&bytes,Some(&journal)).is_err(),"missing inner");
    }
    #[test]
    fn update_transaction_rejects_deleted_and_replaced_held_registry_and_publisher_locks() {
        for name in [".ming-installed-packages.lock",".ming-publisher-keyring.lock"] {
            for replace in [false,true] {
                let (_temp,opened,new,authority,data)=fixture();
                let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
                let lock=opened.registry.root_path().join(name);fs::remove_file(&lock).unwrap();
                if replace {write(&lock,b"");}
                assert!(!validate(&opened,&authority,&data,&record),"{name}, replaced={replace}");
            }
        }
    }
    #[test]
    fn update_transaction_decision_phase_active_matrix() {
        for committed in [false,true] {for new_active in [false,true] {for phase in [None,Some("planned"),Some("dataSwapped"),Some("recordPublished"),Some("startupAttempted"),Some("startupConfirmed"),Some("rollingBack")] {
            let (_temp,opened,new,authority,data)=fixture();candidate(opened.registry.root_path(),&new);
            let mut record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
            if committed {record.confirmation=Some(proof(&record));record.private_confirmed=Some(record.private_before.clone());record.decision=UpdateDecision::CommitDecided;}
            let root=opened.registry.root_path().to_path_buf();drop(opened);
            if new_active {
                let path=root.join("installed-packages-v1");let before=fs::read_to_string(&path).unwrap();
                let old=format!("{}\tactive\t{}\t{}",record.old.package_id,record.old.version,record.old.fingerprint);
                let new=format!("{}\tactive\t{}\t{}",record.new.package_id,record.new.version,record.new.fingerprint);
                assert!(before.contains(&old));write(&path,before.replace(&old,&new).as_bytes());
            }
            let registry=super::package_install::PackageInstallRegistry::open_verified_existing(&root).unwrap();
            let outer=serde_json::to_vec(&record).unwrap();write(&root.join(UPDATE_PENDING_NAME),&outer);
            let journal=phase.map(|phase|inner(&record,phase));if let Some(journal)=&journal {write(&root.join("update-journal-v1"),journal);}
            let expected=if committed {new_active && matches!(phase,None|Some("startupAttempted"|"startupConfirmed"))}
                else {match phase {None|Some("planned")=>!new_active,Some("recordPublished"|"startupAttempted")=>new_active,Some("dataSwapped"|"rollingBack")=>true,_=>false}};
            let actual=validate_transaction_snapshot(&registry,&authority,&data,&record,&outer,journal.as_deref());
            assert_eq!(actual.is_ok(),expected,"committed={committed},new_active={new_active},phase={phase:?}: {actual:?}");
        }}}
    }
    fn sign(package:&mut Package) {
        package.manifest.publisher_signature=None;
        package.manifest.publisher_signature=Some(STANDARD.encode(SigningKey::from_bytes(&[7;32]).sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
    }
    #[test]
    fn update_transaction_rejects_additional_complete_signed_history() {
        let (_temp,opened,new,authority,data)=fixture();
        let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
        let mut extra=new;extra.manifest.version="3.0.0".into();sign(&mut extra);
        candidate(opened.registry.root_path(),&extra);
        assert!(!validate(&opened,&authority,&data,&record));
    }
    #[test]
    fn update_transaction_preserves_removed_records_across_registry_reopen() {
        let (_temp,opened,new,authority,data)=fixture();
        let mut removed=Package {manifest:serde_json::from_slice(&serde_json::to_vec(&new.manifest).unwrap()).unwrap(),bytes:new.bytes.clone()};
        removed.manifest.id="dev.ming.history".into();removed.manifest.version="01.0.0".into();sign(&mut removed);
        opened.registry.install_loaded_package(&removed).unwrap();opened.registry.uninstall(&removed.manifest.id).unwrap();
        let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
        let root=opened.registry.root_path().to_path_buf();drop(opened);
        let path=root.join("installed-packages-v1");let before=fs::read_to_string(&path).unwrap();
        write(&path,before.replace("dev.ming.history\tremoved\t01.0.0", "dev.ming.history\tremoved\t02.0.0").as_bytes());
        let registry=super::package_install::PackageInstallRegistry::open_verified_existing(&root).unwrap();
        let outer=serde_json::to_vec(&record).unwrap();write(&root.join(UPDATE_PENDING_NAME),&outer);
        assert!(validate_transaction_snapshot(&registry,&authority,&data,&record,&outer,None).is_err());
    }
    #[test]
    fn update_transaction_binds_install_root_identity_even_with_identical_copied_content() {
        fn copy(from:&Path,to:&Path) {
            fs::create_dir(to).unwrap();fs::set_permissions(to,fs::Permissions::from_mode(0o700)).unwrap();
            for entry in fs::read_dir(from).unwrap() {let entry=entry.unwrap();let target=to.join(entry.file_name());
                if entry.file_type().unwrap().is_dir() {copy(&entry.path(),&target);}else {write(&target,&fs::read(entry.path()).unwrap());}
            }
        }
        let (temp,opened,new,authority,data)=fixture();
        let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
        let root=opened.registry.root_path().to_path_buf();drop(opened);
        let previous=temp.path().join("previous-root");fs::rename(&root,&previous).unwrap();copy(&previous,&root);
        let registry=super::package_install::PackageInstallRegistry::open_verified_existing(&root).unwrap();
        let outer=serde_json::to_vec(&record).unwrap();write(&root.join(UPDATE_PENDING_NAME),&outer);
        assert!(validate_transaction_snapshot(&registry,&authority,&data,&record,&outer,None).is_err());
    }
    #[test]
    fn update_transaction_requires_original_private_and_public_policy_locks() {
        for policy in ["system-private-storage-policy-v1","system-public-storage-policy-v1"] {for replace in [false,true] {
            let (_temp,opened,new,authority,data)=fixture();
            let record=prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").unwrap();
            let lock=data.join(policy).join(".policy-lock");fs::remove_file(&lock).unwrap();if replace {write(&lock,b"");}
            assert!(!validate(&opened,&authority,&data,&record),"{policy} replace={replace}");
        }}
    }
    #[test]
    fn update_transaction_rejects_removed_target_without_repair() {
        let (_temp,opened,new,authority,data)=fixture();
        opened.registry.uninstall(&new.manifest.id).unwrap();
        let before=tree(opened.registry.root_path());let data_before=tree(&data);
        assert!(prepare_record(&opened.registry,&authority,&data,2,&new,"task3-test").is_err());
        assert_eq!(tree(opened.registry.root_path()),before);assert_eq!(tree(&data),data_before);
    }
}
