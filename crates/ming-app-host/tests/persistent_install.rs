#![cfg(all(target_os = "linux", feature = "desktop"))]

use base64::{Engine as _, engine::general_purpose::STANDARD};
use ed25519_dalek::{Signer, SigningKey};
use ming_app_host::{package::{Manifest, Package}, package_install::PackageInstallRegistry,
    persistent_install::{self, ContentSnapshot, BOOTSTRAP_NAME, CATALOG_NAME}, publisher::PublisherTrustStore};
use std::{collections::BTreeMap, fs, os::unix::fs::{symlink, MetadataExt, PermissionsExt}, path::{Path, PathBuf}, process::Command};

const KEY: &str = "mingos-dev-2026";
const INITIAL: &str = "dev.ming.text-entry-demo";
fn package(id: &str, version: &str) -> Package {
    let key = SigningKey::from_bytes(&[7;32]);
    let mut package = Package { manifest: Manifest { schema_version:1, id:id.into(), version:version.into(), sdk_version:6,
        entry:"main.wasm".into(), private_storage:false, public_storage:false, data_migration:None,
        publisher_key_id:Some(KEY.into()), publisher_signature:None }, bytes:b"\0asm\x01\0\0\0".to_vec() };
    package.manifest.publisher_signature = Some(STANDARD.encode(key.sign(&package.signature_message(KEY).unwrap()).to_bytes()));
    package
}
fn catalog(two: bool) -> Vec<u8> {
    let mut apps = vec![serde_json::json!({"id":1,"title":"TextLab","packageId":INITIAL})];
    if two { apps.push(serde_json::json!({"id":2,"title":"Installed","packageId":"dev.ming.install-demo"})); }
    serde_json::to_vec(&serde_json::json!({"schemaVersion":3,"apps":apps,"autostart":[1]})).unwrap()
}
fn initialize(parent: &Path) -> (PathBuf, ContentSnapshot) {
    fs::set_permissions(parent, fs::Permissions::from_mode(0o700)).unwrap();
    let root = parent.join("install-root");
    let key = SigningKey::from_bytes(&[7;32]);
    let content = persistent_install::initialize_first(&root, &package(INITIAL,"0.1.0"), KEY, key.verifying_key().as_bytes(), &catalog(false)).unwrap();
    (root, content)
}
// Captures unsafe objects without following links, so rejection cannot conceal repairs.
fn tree(root: &Path) -> BTreeMap<PathBuf, (u32,u64,Vec<u8>)> {
    fn visit(root:&Path,path:&Path,out:&mut BTreeMap<PathBuf,(u32,u64,Vec<u8>)>) {
        let meta=fs::symlink_metadata(path).unwrap();
        let bytes=if meta.file_type().is_symlink(){fs::read_link(path).unwrap().as_os_str().as_encoded_bytes().to_vec()}
            else if meta.is_file(){fs::read(path).unwrap()}else{Vec::new()};
        out.insert(path.strip_prefix(root).unwrap().into(),(meta.mode(),meta.nlink(),bytes));
        if meta.is_dir(){for e in fs::read_dir(path).unwrap(){visit(root,&e.unwrap().path(),out);}}
    }
    let mut result=BTreeMap::new(); visit(root,root,&mut result); result
}
fn rejects_unchanged(root: &Path) {
    let before=tree(root);
    assert!(persistent_install::open_existing(root).is_err());
    assert_eq!(tree(root),before);
}
#[test]
fn persistent_open_must_not_accept_a_registry_with_no_catalog_or_bootstrap() {
    let root=tempfile::tempdir().unwrap();
    fs::set_permissions(root.path(),fs::Permissions::from_mode(0o700)).unwrap();
    let key=SigningKey::from_bytes(&[7;32]);
    PublisherTrustStore::provision(root.path(),KEY,key.verifying_key().as_bytes()).unwrap();
    rejects_unchanged(root.path());
}
#[test]
fn initializes_only_app1_and_independent_process_reopens_exact_content() {
    let temp=tempfile::tempdir().unwrap();
    let (root,content)=initialize(temp.path());
    assert_eq!(content.apps.len(),1); assert_eq!(content.apps[0].app,1);
    assert_eq!(content.apps[0].package_id,INITIAL); assert_eq!(content.apps[0].version,"0.1.0");
    assert_eq!(content.apps[0].wasm_sha256,persistent_install::sha256(b"\0asm\x01\0\0\0"));
    assert_eq!(content.files.len(),6);
    let before=tree(&root);
    let child=Command::new(std::env::current_exe().unwrap()).args(["--exact","independent_process_reader","--nocapture"])
        .env("MING_PERSISTENT_CHILD_ROOT",&root).env("MING_PERSISTENT_CHILD_CONTENT",serde_json::to_string(&content).unwrap()).output().unwrap();
    assert!(child.status.success(),"{}",String::from_utf8_lossy(&child.stdout));
    assert_eq!(tree(&root),before);
    for (path,(mode,nlink,_)) in before { assert_eq!(mode&0o7777,if root.join(&path).is_dir(){0o700}else{0o600}); if root.join(path).is_file(){assert_eq!(nlink,1);} }
}
#[test]
fn independent_process_reader() {
    let Some(root)=std::env::var_os("MING_PERSISTENT_CHILD_ROOT")else{return};
    let expected:ContentSnapshot=serde_json::from_str(&std::env::var("MING_PERSISTENT_CHILD_CONTENT").unwrap()).unwrap();
    assert_eq!(persistent_install::open_existing(Path::new(&root)).unwrap().content,expected);
}
#[test]
fn strict_reopen_never_creates_missing_locks() {
    for name in [".ming-installed-packages.lock", ".ming-publisher-keyring.lock"] {
        let temp = tempfile::tempdir().unwrap();
        let (root, _) = initialize(temp.path());
        fs::remove_file(root.join(name)).unwrap();
        rejects_unchanged(&root);
        assert!(!root.join(name).exists());
    }
}

#[test]
fn strict_reopen_rejects_unsafe_existing_locks_without_writes() {
    for name in [".ming-installed-packages.lock", ".ming-publisher-keyring.lock"] {
        for case in ["symlink", "hardlink", "permissions", "directory"] {
            let temp = tempfile::tempdir().unwrap();
            let (root, _) = initialize(temp.path());
            let lock = root.join(name);
            match case {
                "symlink" => {
                    fs::remove_file(&lock).unwrap();
                    symlink(root.join(CATALOG_NAME), &lock).unwrap();
                }
                "hardlink" => fs::hard_link(&lock, temp.path().join("lock-alias")).unwrap(),
                "permissions" => fs::set_permissions(&lock, fs::Permissions::from_mode(0o640)).unwrap(),
                "directory" => {
                    fs::remove_file(&lock).unwrap();
                    fs::create_dir(&lock).unwrap();
                    fs::set_permissions(&lock, fs::Permissions::from_mode(0o700)).unwrap();
                }
                _ => unreachable!(),
            }
            rejects_unchanged(&root);
        }
    }
}
#[test]
fn every_required_file_is_required_without_repair() {
    for name in [CATALOG_NAME,BOOTSTRAP_NAME,"installed-packages-v1","trusted-publishers-v1"] {
        let temp=tempfile::tempdir().unwrap(); let(root,_)=initialize(temp.path());
        fs::remove_file(root.join(name)).unwrap(); rejects_unchanged(&root);
    }
}
#[test]
fn permissions_links_pending_and_unknown_content_are_rejected_without_repair() {
    for case in 0..8 {
        let temp=tempfile::tempdir().unwrap();let(root,content)=initialize(temp.path());let catalog=root.join(CATALOG_NAME);
        match case {
            0=>fs::set_permissions(&root,fs::Permissions::from_mode(0o750)).unwrap(),
            1=>fs::set_permissions(&catalog,fs::Permissions::from_mode(0o644)).unwrap(),
            2=>{fs::rename(&catalog,temp.path().join("external")).unwrap();symlink(temp.path().join("external"),&catalog).unwrap();},
            3=>{fs::hard_link(&catalog,temp.path().join("external")).unwrap();},
            4=>fs::write(root.join("desktop-install-pending-v1.json"),b"{}").unwrap(),
            5=>fs::write(root.join("update-journal-v1"),b"{}").unwrap(),
            6=>fs::write(root.join("unexpected"),b"x").unwrap(),
            7=>fs::write(root.join(&content.apps[0].wasm_path),b"tampered").unwrap(),
            _=>unreachable!(),
        }; rejects_unchanged(&root);
    }
}
#[test]
fn strict_json_and_autostart_contract_reject_drift() {
    for bytes in [b"{bad".to_vec(), b"{\"schemaVersion\":3,\"schemaVersion\":3,\"apps\":[],\"autostart\":[1]}".to_vec(),
        b"{\"schemaVersion\":3,\"apps\":[],\"autostart\":[1],\"unknown\":0}".to_vec(),
        String::from_utf8(catalog(false)).unwrap().replace("[1]","[1,2]").into_bytes()] {
        let temp=tempfile::tempdir().unwrap();let(root,_)=initialize(temp.path());fs::write(root.join(CATALOG_NAME),bytes).unwrap();rejects_unchanged(&root);
    }
    for bytes in [b"{\"schemaVersion\":1,\"schemaVersion\":1}".as_slice(),b"{\"unknown\":true}"] {
        let temp=tempfile::tempdir().unwrap();let(root,_)=initialize(temp.path());fs::write(root.join(BOOTSTRAP_NAME),bytes).unwrap();rejects_unchanged(&root);
    }
}
#[test]
fn bad_publisher_signature_is_rejected_even_with_unchanged_wasm() {
    let temp=tempfile::tempdir().unwrap();let(root,content)=initialize(temp.path());let path=root.join(&content.apps[0].manifest_path);
    let mut manifest:serde_json::Value=serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    manifest["publisherSignature"]=STANDARD.encode([0u8;64]).into();fs::write(path,serde_json::to_vec(&manifest).unwrap()).unwrap();rejects_unchanged(&root);
}
#[test]
fn active_catalog_sets_must_match_in_both_directions() {
    for extra_active in [true,false] {
        let temp=tempfile::tempdir().unwrap();let(root,_)=initialize(temp.path());
        if extra_active { let source=temp.path().join("source");fs::create_dir(&source).unwrap();let pkg=package("dev.ming.install-demo","1.0.0");
            fs::write(source.join("main.wasm"),&pkg.bytes).unwrap();fs::write(source.join("app.json"),serde_json::to_vec(&pkg.manifest).unwrap()).unwrap();
            let registry=PackageInstallRegistry::open_verified(&root).unwrap();registry.install_from_manifest(&source.join("app.json")).unwrap();drop(registry);
        } else {fs::write(root.join(CATALOG_NAME),catalog(true)).unwrap();}
        rejects_unchanged(&root);
    }
}
#[test]
fn existing_empty_root_and_abandoned_staging_never_initialize() {
    for staging in [true,false] {
        let temp=tempfile::tempdir().unwrap();fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();let root=temp.path().join("install-root");
        fs::create_dir(if staging{temp.path().join(".install-root.staging-abandoned")}else{root.clone()}).unwrap();
        let before=tree(temp.path());let key=SigningKey::from_bytes(&[7;32]);
        assert!(persistent_install::initialize_first(&root,&package(INITIAL,"0.1.0"),KEY,key.verifying_key().as_bytes(),&catalog(false)).is_err());
        assert_eq!(tree(temp.path()),before);
    }
}

#[test]
fn removed_records_and_complete_inactive_content_can_remain() {
    let temp = tempfile::tempdir().unwrap();
    let (root, initial) = initialize(temp.path());
    let source = temp.path().join("source");
    fs::create_dir(&source).unwrap();
    let candidate = package("dev.ming.install-demo", "1.0.0");
    fs::write(source.join("main.wasm"), &candidate.bytes).unwrap();
    fs::write(source.join("app.json"), serde_json::to_vec(&candidate.manifest).unwrap()).unwrap();
    let registry = PackageInstallRegistry::open_verified(&root).unwrap();
    registry.install_from_manifest(&source.join("app.json")).unwrap();
    assert!(registry.uninstall("dev.ming.install-demo").unwrap());
    drop(registry);
    let opened = persistent_install::open_existing(&root).unwrap();
    assert_eq!(opened.content.apps, initial.apps);
    assert_eq!(opened.content.files.len(), 8);
    drop(opened);
    let before = tree(&root);
    assert!(persistent_install::open_existing(&root).is_ok());
    assert_eq!(tree(&root), before);
}

#[test]
fn snapshot_rejects_malformed_disk_records_while_the_registry_lock_is_held() {
    let temp = tempfile::tempdir().unwrap();
    let (root, _) = initialize(temp.path());
    let opened = persistent_install::open_existing(&root).unwrap();
    fs::write(root.join("installed-packages-v1"), b"malformed while locked").unwrap();
    let before = tree(&root);
    assert!(persistent_install::snapshot(&opened.registry).is_err(), "snapshot must parse the disk registry, not only hash it");
    assert_eq!(tree(&root), before);
}

#[test]
fn snapshot_rejects_valid_disk_record_drift_including_removed_records() {
    let temp = tempfile::tempdir().unwrap();
    let (root, _) = initialize(temp.path());
    let path = root.join("installed-packages-v1");
    let original = fs::read_to_string(&path).unwrap();
    let opened = persistent_install::open_existing(&root).unwrap();
    // A well-formed removed record does not change the active set, but still
    // differs from the exact records protected by the registry lock.
    fs::write(&path, format!("{original}dev.ming.history\tremoved\t1.0.0\t{}\n", "0".repeat(64))).unwrap();
    let before = tree(&root);
    assert!(persistent_install::snapshot(&opened.registry).is_err(), "removed record drift must not be hidden by filtering the active set");
    assert_eq!(tree(&root), before);
}
