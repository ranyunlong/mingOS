//! Strict parent-owned persistent-update records and read-only transaction checks.
use crate::app_data::{PrivateUpdateBaseline, valid_package_id};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use crate::{app_data::PackageStorageAuthority, package::{Package, load_package},
    package_install::PackageInstallRegistry, persistent_install::{self, ContentSnapshot, sha256}};
use anyhow::Context;
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs, os::unix::fs::MetadataExt, path::{Path, PathBuf}};
use std::{fs::{File, OpenOptions}, io::{Read, Write}, os::{fd::AsRawFd, unix::{ffi::OsStrExt, fs::OpenOptionsExt}}, ffi::CString};
use crate::{desktop_catalog::{Catalog, load_catalog_with_install_registry}, package_install::CoordinatedRegistryContext,
    app_data::CoordinatedPrivateUpdate};

pub const UPDATE_PENDING_NAME: &str = crate::package_install::COORDINATED_UPDATE_PENDING_NAME;
pub(crate) const INNER_JOURNAL_NAME: &str = "update-journal-v1";
pub const MAX_UPDATE_RECORD_BYTES: usize = 4096;

/// Only the two fresh-disk layouts used by the persistent-update fixture.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UpdateFixtureLayout { NoteAlpha, Absent }

/// Explicit first initialization, never a fallback for an existing or damaged root.
pub fn initialize_update_fixture_first(root: &Path, data_root: &Path, bootstrap: &Package,
    old_target: &Package, key_id: &str, public_key: &[u8; 32], initial_catalog: &[u8],
    layout: UpdateFixtureLayout) -> Result<ContentSnapshot> {
    initialize_update_fixture(root,data_root,bootstrap,old_target,key_id,public_key,initial_catalog,layout, |_| Ok(()))
}

fn fixture_path(path:&Path) -> Result<()> {
    use std::path::Component;
    ensure!(path.is_absolute()&&path.file_name().is_some(),"UPDATE_FIXTURE_ABSOLUTE");
    ensure!(!path.as_os_str().as_bytes().split(|b|*b==b'/').any(|c|c==b"."||c==b".."),"UPDATE_FIXTURE_PATH_ALIAS");
    let mut cursor=PathBuf::new();
    for component in path.components() {
        ensure!(matches!(component,Component::RootDir|Component::Normal(_)),"UPDATE_FIXTURE_PATH_ALIAS");
        cursor.push(component);
        if cursor!=path { let info=fs::symlink_metadata(&cursor)?;ensure!(info.is_dir()&&!info.file_type().is_symlink(),"UPDATE_FIXTURE_ANCESTOR"); }
    }
    Ok(())
}
fn fixture_private_directory(path:&Path) -> Result<()> {
    let info=fs::symlink_metadata(path)?;
    ensure!(info.is_dir()&&!info.file_type().is_symlink()&&info.uid()==unsafe{libc::geteuid()}&&info.mode()&0o7777==0o700,"UPDATE_FIXTURE_DIRECTORY");Ok(())
}
fn fixture_absent(path:&Path) -> Result<()> {
    ensure!(fs::symlink_metadata(path).is_err_and(|e|e.kind()==std::io::ErrorKind::NotFound),"UPDATE_FIXTURE_ALREADY_EXISTS");Ok(())
}
fn fixture_sync_tree(path:&Path) -> Result<()> {
    let info=fs::symlink_metadata(path)?;
    ensure!(!info.file_type().is_symlink()&&(info.is_file()||info.is_dir()),"UPDATE_FIXTURE_FILE_TYPE");
    if info.is_dir() { for entry in fs::read_dir(path)? {fixture_sync_tree(&entry?.path())?;} }
    File::open(path)?.sync_all()?;Ok(())
}
// This only validates the infrastructure shape supplied by an explicitly
// authorized first-initialization caller. A marker cannot authenticate a fresh
// disk; the guest must first verify its disk identity and first-boot role.
struct FixtureInfrastructure { root:TemporaryNode, gid:u32, nodes:Vec<TemporaryNode> }
impl FixtureInfrastructure {
    fn names(path:&Path)->Result<Vec<std::ffi::OsString>> {
        let mut names=fs::read_dir(path)?.take(3).map(|entry|entry.map(|e|e.file_name())).collect::<std::io::Result<Vec<_>>>()?;
        names.sort();Ok(names)
    }
    fn capture(data:&Path)->Result<Self> {
        let root=TemporaryNode::capture(data,true,0)?;let gid=root.handle.metadata()?.gid();
        let names=Self::names(data)?;let mut nodes=Vec::new();
        if !names.is_empty() {
            ensure!(names==[std::ffi::OsString::from(".mingos-boot"),std::ffi::OsString::from("lost+found")],"UPDATE_FIXTURE_DATA_NOT_EMPTY");
            let boot=TemporaryNode::capture(&data.join(".mingos-boot"),true,0)?;
            let lost=TemporaryNode::capture(&data.join("lost+found"),true,0)?;
            ensure!(Self::names(&boot.path)?==[std::ffi::OsString::from("current.json")]&&Self::names(&lost.path)?.is_empty(),"UPDATE_FIXTURE_INFRA_CONTENT");
            let marker=TemporaryNode::capture(&boot.path.join("current.json"),false,256)?;
            #[derive(Deserialize)]
            #[serde(rename_all="camelCase",deny_unknown_fields)]
            struct BootMarker { boot_id:String,counter:u64 }
            let value:BootMarker=serde_json::from_slice(&marker.bytes)?;
            let boot_id=fs::read_to_string("/proc/sys/kernel/random/boot_id")?;
            ensure!(value.counter==1&&value.boot_id==boot_id.trim(),"UPDATE_FIXTURE_INFRA_GENERATION");
            nodes.extend([boot,lost,marker]);
        }
        let captured=Self{root,gid,nodes};captured.verify_initial()?;Ok(captured)
    }
    fn verify_initial(&self)->Result<()> {
        self.verify()?;
        let expected=if self.nodes.is_empty(){Vec::new()}else{vec![std::ffi::OsString::from(".mingos-boot"),std::ffi::OsString::from("lost+found")]};
        ensure!(Self::names(&self.root.path)?==expected,"UPDATE_FIXTURE_DATA_NOT_EMPTY");Ok(())
    }
    fn verify(&self)->Result<()> {
        self.root.verify_directory_identity()?;
        ensure!(self.root.handle.metadata()?.gid()==self.gid&&fs::symlink_metadata(&self.root.path)?.gid()==self.gid,"UPDATE_FIXTURE_INFRA_GROUP");
        for node in &self.nodes {
            node.verify()?;let info=node.handle.metadata()?;
            ensure!(info.dev()==self.root.stamp.0&&info.gid()==self.gid&&fs::symlink_metadata(&node.path)?.gid()==self.gid,"UPDATE_FIXTURE_INFRA_IDENTITY");
        }
        if !self.nodes.is_empty() {
            ensure!(Self::names(&self.nodes[0].path)?==[std::ffi::OsString::from("current.json")]&&Self::names(&self.nodes[1].path)?.is_empty(),"UPDATE_FIXTURE_INFRA_CONTENT");
        }
        Ok(())
    }
}
fn initialize_update_fixture(root:&Path,data_root:&Path,bootstrap:&Package,old_target:&Package,key_id:&str,
    public_key:&[u8;32],initial_catalog:&[u8],layout:UpdateFixtureLayout,mut checkpoint:impl FnMut(&str)->Result<()>) -> Result<ContentSnapshot> {
    use base64::{Engine as _,engine::general_purpose::STANDARD};
    use ed25519_dalek::{Signature,Verifier,VerifyingKey};
    use std::{os::unix::fs::DirBuilderExt,sync::Arc};
    fixture_path(root)?;fixture_path(data_root)?;
    ensure!(!data_root.starts_with(root)&&(!root.starts_with(data_root)||root.parent()==Some(data_root)),"UPDATE_FIXTURE_ROOT_OVERLAP");
    let parent=root.parent().context("UPDATE_FIXTURE_PARENT")?;fixture_private_directory(parent)?;
    let name=root.file_name().and_then(|n|n.to_str()).context("UPDATE_FIXTURE_NAME")?;
    let guard=parent.join(format!(".{name}.update-fixture-initializing"));
    ensure!(!data_root.starts_with(&guard),"UPDATE_FIXTURE_GUARD_OVERLAP");
    fixture_absent(root)?;fixture_absent(&guard)?;fixture_private_directory(data_root)?;
    let infrastructure=FixtureInfrastructure::capture(data_root)?;
    ensure!(initial_catalog.len()<=16*1024,"UPDATE_FIXTURE_CATALOG_LIMIT");
    #[derive(Deserialize)]
    #[serde(rename_all="camelCase",deny_unknown_fields)]
    struct InitialCatalog {schema_version:u32,apps:Vec<InitialApp>,autostart:Vec<u16>}
    #[derive(Deserialize)]
    #[serde(rename_all="camelCase",deny_unknown_fields)]
    struct InitialApp {id:u16,title:String,package_id:String}
    let catalog:InitialCatalog=serde_json::from_slice(initial_catalog)?;
    ensure!(catalog.schema_version==3&&catalog.autostart==[1]&&catalog.apps.len()==1&&catalog.apps[0].id==1
        &&catalog.apps[0].title=="Text Lab"&&catalog.apps[0].package_id=="dev.ming.text-entry-demo","UPDATE_FIXTURE_CATALOG");
    let key=VerifyingKey::from_bytes(public_key)?;
    for (package,id) in [(bootstrap,"dev.ming.text-entry-demo"),(old_target,"dev.ming.persistent-update-demo")] {
        let manifest=&package.manifest;
        ensure!(manifest.schema_version==1&&manifest.id==id&&manifest.version=="0.1.0"&&manifest.sdk_version==6
            &&manifest.entry=="main.wasm"&&!manifest.public_storage&&manifest.data_migration.is_none()
            &&manifest.publisher_key_id.as_deref()==Some(key_id)&&package.bytes.len()<=crate::package::MAX_MODULE_BYTES,"UPDATE_FIXTURE_PACKAGE");
        let signature=Signature::from_slice(&STANDARD.decode(manifest.publisher_signature.as_deref().context("UPDATE_FIXTURE_SIGNATURE")?)?)?;
        key.verify(&package.signature_message(key_id)?,&signature)?;
    }
    checkpoint("before-claim")?;infrastructure.verify_initial()?;
    // Atomic claim precedes every data mutation. On any error this private
    // container is deliberately retained; initialization never repairs/replays it.
    fs::DirBuilder::new().mode(0o700).create(&guard)?;File::open(parent)?.sync_all()?;
    checkpoint("claimed")?;
    let staging=guard.join("install");
    persistent_install::initialize_first(&staging,bootstrap,key_id,public_key,initial_catalog)?;
    let opened=persistent_install::open_existing(&staging)?;
    persistent_install::install_new(&opened.registry,old_target).map_err(|e|anyhow::anyhow!("UPDATE_FIXTURE_INSTALL {}: {}",e.stage,e.error))?;
    let content=persistent_install::snapshot(&opened.registry)?;
    ensure!(content.apps.len()==2&&content.apps.iter().any(|app|app.app==1&&app.package_id==bootstrap.manifest.id)
        &&content.apps.iter().any(|app|app.app==2&&app.package_id==old_target.manifest.id
            &&app.fingerprint==old_target.storage_fingerprint().iter().map(|byte|format!("{byte:02x}")).collect::<String>()),"UPDATE_FIXTURE_DOUBLE_PACKAGE");
    let (catalog,grants)=load_catalog_with_install_registry(&staging.join(persistent_install::CATALOG_NAME),&opened.registry)?;
    ensure!(catalog.autostart==[1]&&grants.is_empty(),"UPDATE_FIXTURE_AUTOSTART");
    checkpoint("before-seed")?;
    let authority=Arc::new(PackageStorageAuthority::open_default_private(crate::app_data::AppDataRoot::open(data_root)?)?);
    if layout==UpdateFixtureLayout::NoteAlpha {
        let private=authority.default_private_session_for_package(old_target)?;
        ensure!(private.write_file("note.txt",b"alpha")?.directory_synced,"UPDATE_FIXTURE_SEED_SYNC");
    }
    drop(authority);fixture_sync_tree(data_root)?;
    checkpoint("after-seed")?;
    ensure!(persistent_install::snapshot(&opened.registry)?==content,"UPDATE_FIXTURE_CONTENT_DRIFT");
    fixture_sync_tree(&staging)?;drop(opened);
    checkpoint("before-publish")?;infrastructure.verify()?;
    let source=CString::new(staging.as_os_str().as_bytes())?;let destination=CString::new(root.as_os_str().as_bytes())?;
    ensure!(unsafe{libc::renameat2(libc::AT_FDCWD,source.as_ptr(),libc::AT_FDCWD,destination.as_ptr(),libc::RENAME_NOREPLACE)}==0,
        "UPDATE_FIXTURE_PUBLISH {}",std::io::Error::last_os_error());
    checkpoint("published")?;File::open(parent)?.sync_all()?;checkpoint("parent-synced")?;
    ensure!(persistent_install::open_existing(root)?.content==content,"UPDATE_FIXTURE_FINAL_READBACK");infrastructure.verify()?;
    fs::remove_dir(&guard)?;File::open(parent)?.sync_all()?;
    Ok(content)
}

#[cfg(test)]
mod fixture_initialization_tests {
    use super::*;
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use ed25519_dalek::{Signer, SigningKey};
    use std::os::unix::fs::PermissionsExt;
    fn input() -> (tempfile::TempDir, PathBuf, PathBuf, Package, Package, SigningKey, Vec<u8>) {
        let temp=tempfile::tempdir().unwrap();
        fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let root=temp.path().join("install");let data=temp.path().join("data");
        fs::create_dir(&data).unwrap();fs::set_permissions(&data,fs::Permissions::from_mode(0o700)).unwrap();
        let key=SigningKey::from_bytes(&[7;32]);
        let signed=|id:&str| {
            let mut package=Package {manifest:crate::package::Manifest {schema_version:1,id:id.into(),version:"0.1.0".into(),sdk_version:6,
                entry:"main.wasm".into(),private_storage:false,public_storage:false,data_migration:None,
                publisher_key_id:Some("mingos-dev-2026".into()),publisher_signature:None},bytes:b"\0asm\x01\0\0\0".to_vec()};
            package.manifest.publisher_signature=Some(STANDARD.encode(key.sign(&package.signature_message("mingos-dev-2026").unwrap()).to_bytes()));package
        };
        let bootstrap=signed("dev.ming.text-entry-demo");let old=signed("dev.ming.persistent-update-demo");
        let catalog=br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#.to_vec();
        (temp,root,data,bootstrap,old,key,catalog)
    }
    #[test]
    fn fixture_initialization_publishes_two_packages_and_seed_together() {
        let (_temp,root,data,bootstrap,old,key,catalog)=input();
        let content=initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::NoteAlpha).unwrap();
        assert_eq!(content.apps.len(),2,"formal root must contain both packages on first publication");
        assert_eq!(content.apps.iter().map(|app|(app.app,(app.package_id.as_str(),app.version.as_str()))).collect::<BTreeMap<_,_>>(),
            BTreeMap::from([(1,("dev.ming.text-entry-demo","0.1.0")),(2,("dev.ming.persistent-update-demo","0.1.0"))]));
        assert_eq!(persistent_install::open_existing(&root).unwrap().content,content);
        let private=crate::app_data::snapshot_private_read_only(&data,&old.manifest.id).unwrap();
        assert_eq!(private.files.len(),1);assert_eq!(private.files[0].path,"note.txt");assert_eq!(private.files[0].size,5);
        assert_eq!(private.files[0].sha256,sha256(b"alpha"));
    }
    fn guard(root:&Path)->PathBuf {root.parent().unwrap().join(".install.update-fixture-initializing")}
    fn tree(root:&Path)->BTreeMap<PathBuf,(u64,u32,Vec<u8>)> {
        fn walk(root:&Path,path:&Path,out:&mut BTreeMap<PathBuf,(u64,u32,Vec<u8>)>) {
            let info=fs::symlink_metadata(path).unwrap();
            out.insert(path.strip_prefix(root).unwrap().into(),(info.ino(),info.mode(),if info.is_file(){fs::read(path).unwrap()}else{Vec::new()}));
            if info.is_dir(){for entry in fs::read_dir(path).unwrap(){walk(root,&entry.unwrap().path(),out);}}
        }
        let mut out=BTreeMap::new();walk(root,root,&mut out);out
    }
    #[test]
    fn fixture_absent_layout_creates_recovery_locks_but_no_target_namespace() {
        let (_temp,root,data,bootstrap,old,key,catalog)=input();
        initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::Absent).unwrap();
        let baseline=crate::app_data::observe_private_update_read_only(&data,&old.manifest.id).unwrap();
        assert!(!baseline.namespace_exists);assert!(baseline.files.is_none());assert!(!guard(&root).exists());
        drop(PackageStorageAuthority::open_update_recovery_existing(&data).unwrap());
    }
    #[test]
    fn fixture_supports_install_root_directly_inside_data_root() {
        let (_temp,_root,data,bootstrap,old,key,catalog)=input();let root=data.join("install-root");
        let content=initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::NoteAlpha).unwrap();
        assert_eq!(content.apps.len(),2);assert_eq!(persistent_install::open_existing(&root).unwrap().content,content);
        assert_eq!(crate::app_data::snapshot_private_read_only(&data,&old.manifest.id).unwrap().files[0].sha256,sha256(b"alpha"));
        assert!(!data.join(".install-root.update-fixture-initializing").exists());
    }
    fn seed_boot_infrastructure(data:&Path) {
        for name in ["lost+found",".mingos-boot"] {let path=data.join(name);fs::create_dir(&path).unwrap();fs::set_permissions(path,fs::Permissions::from_mode(0o700)).unwrap();}
        let bytes=serde_json::to_vec(&serde_json::json!({"bootId":fs::read_to_string("/proc/sys/kernel/random/boot_id").unwrap().trim(),"counter":1})).unwrap();
        let path=data.join(".mingos-boot/current.json");fs::write(&path,bytes).unwrap();fs::set_permissions(path,fs::Permissions::from_mode(0o600)).unwrap();
    }
    #[test]
    fn fixture_supports_trusted_boot_infrastructure_without_modifying_it() {
        for layout in [UpdateFixtureLayout::NoteAlpha,UpdateFixtureLayout::Absent] {
            let (_temp,_root,data,bootstrap,old,key,catalog)=input();let root=data.join("install-root");seed_boot_infrastructure(&data);
            let boot_before=tree(&data.join(".mingos-boot"));let lost_before=tree(&data.join("lost+found"));
            let content=initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,layout).unwrap();
            assert_eq!(content.apps.len(),2);assert_eq!(persistent_install::open_existing(&root).unwrap().content,content);
            assert_eq!(tree(&data.join(".mingos-boot")),boot_before);assert_eq!(tree(&data.join("lost+found")),lost_before);
            assert_eq!(crate::app_data::observe_private_update_read_only(&data,&old.manifest.id).unwrap().namespace_exists,layout==UpdateFixtureLayout::NoteAlpha);
        }
    }
    #[test]
    fn fixture_boot_infrastructure_rejects_extra_data_and_tampering_before_writes() {
        for case in 0..22 {
            let (temp,_root,data,bootstrap,old,key,catalog)=input();let root=data.join("install-root");seed_boot_infrastructure(&data);
            let boot=data.join(".mingos-boot");let marker=boot.join("current.json");let lost=data.join("lost+found");
            match case {
                0=>{fs::write(data.join("old-user-data"),b"keep").unwrap();},
                1=>{fs::write(lost.join("recovered"),b"keep").unwrap();},
                2=>{fs::write(boot.join("extra"),b"keep").unwrap();},
                3=>{fs::write(&marker,br#"{"bootId":"unused","counter":2}"#).unwrap();},
                4=>{fs::write(&marker,br#"{"bootId":"00000000-0000-0000-0000-000000000000","counter":1}"#).unwrap();},
                5=>{fs::write(&marker,br#"{"bootId":"unused","counter":1,"extra":0}"#).unwrap();},
                6=>{fs::write(&marker,br#"{"bootId":"unused","counter":1,"counter":1}"#).unwrap();},
                7=>{fs::remove_file(&marker).unwrap();fs::remove_dir(&boot).unwrap();},
                8=>{fs::remove_dir(&lost).unwrap();},
                9=>{fs::remove_dir(&lost).unwrap();std::os::unix::fs::symlink(temp.path(),&lost).unwrap();},
                10=>{fs::remove_file(&marker).unwrap();fs::remove_dir(&boot).unwrap();std::os::unix::fs::symlink(temp.path(),&boot).unwrap();},
                11=>{fs::remove_file(&marker).unwrap();std::os::unix::fs::symlink(temp.path().join("missing"),&marker).unwrap();},
                12=>{fs::hard_link(&marker,temp.path().join("linked-marker")).unwrap();},
                13=>{fs::set_permissions(&marker,fs::Permissions::from_mode(0o640)).unwrap();},
                14=>{fs::set_permissions(&boot,fs::Permissions::from_mode(0o755)).unwrap();},
                15=>{fs::set_permissions(&lost,fs::Permissions::from_mode(0o755)).unwrap();},
                16=>{fs::remove_file(&marker).unwrap();fs::create_dir(&marker).unwrap();},
                17=>{fs::write(&marker,vec![b' ';257]).unwrap();},
                18=>{fs::write(&marker,br#"{"bootId":"unused","counter":"1"}"#).unwrap();},
                19=>{fs::remove_file(&marker).unwrap();},
                20=>{fs::set_permissions(&lost,fs::Permissions::from_mode(0o4700)).unwrap();},
                _=>{fs::write(&marker,br#"{"bootId":"unused","counter":1.0}"#).unwrap();},
            }
            let before=tree(temp.path());
            assert!(initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::NoteAlpha).is_err(),"case {case}");
            assert_eq!(tree(temp.path()),before,"case {case}");assert!(!data.join(".install-root.update-fixture-initializing").exists());
        }
    }
    #[test]
    fn fixture_initial_data_is_rechecked_immediately_before_claim() {
        let (_temp,_root,data,bootstrap,old,key,catalog)=input();let root=data.join("install-root");seed_boot_infrastructure(&data);
        assert!(initialize_update_fixture(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::Absent,|stage| {
            if stage=="before-claim" {fs::write(data.join("unexpected"),b"keep")?;}Ok(())
        }).is_err());
        assert!(!root.exists());assert!(!data.join(".install-root.update-fixture-initializing").exists());assert_eq!(fs::read(data.join("unexpected")).unwrap(),b"keep");
    }
    #[test]
    fn fixture_boot_infrastructure_is_rechecked_before_publication() {
        for case in 0..4 {
            let (_temp,_root,data,bootstrap,old,key,catalog)=input();let root=data.join("install-root");seed_boot_infrastructure(&data);
            let result=initialize_update_fixture(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::Absent,|stage| {
                if stage=="before-publish" {let marker=data.join(".mingos-boot/current.json");match case {
                    0=>fs::write(marker,b"changed")?,
                    1=>{let bytes=fs::read(&marker)?;fs::remove_file(&marker)?;fs::write(marker,bytes)?;},
                    2=>fs::write(data.join("lost+found/unexpected"),b"keep")?,
                    _=>fs::set_permissions(data.join(".mingos-boot"),fs::Permissions::from_mode(0o755))?,
                }}Ok(())
            });
            assert!(result.is_err(),"case {case}");assert!(!root.exists());assert!(data.join(".install-root.update-fixture-initializing").exists());
        }
    }
    #[test]
    fn fixture_preflight_rejects_existing_or_invalid_inputs_before_claim_or_data_writes() {
        for case in 0..7 {
            let (_temp,root,data,mut bootstrap,old,key,mut catalog)=input();
            match case {
                0=>{fs::create_dir(&root).unwrap();},
                1=>{fs::write(data.join("keep"),b"sentinel").unwrap();},
                2=>bootstrap.manifest.publisher_signature=Some(STANDARD.encode([0;64])),
                3=>catalog.extend_from_slice(b"bad"),
                4=>bootstrap.manifest.id="dev.ming.wrong".into(),
                5=>{fs::remove_dir(&data).unwrap();std::os::unix::fs::symlink(root.parent().unwrap(),&data).unwrap();},
                _=>{fs::set_permissions(&data,fs::Permissions::from_mode(0o755)).unwrap();},
            }
            let before=tree(root.parent().unwrap());
            assert!(initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::NoteAlpha).is_err(),"case {case}");
            assert!(!guard(&root).exists());assert_eq!(tree(root.parent().unwrap()),before,"case {case}");
        }
    }
    #[test]
    fn fixture_failure_retains_claim_and_never_replays_partial_data() {
        for point in ["claimed","before-seed","after-seed","published","parent-synced"] {
            let (_temp,root,data,bootstrap,old,key,catalog)=input();
            let error=initialize_update_fixture(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::NoteAlpha,
                |stage| {if stage==point {anyhow::bail!("injected {stage}");}Ok(())}).unwrap_err();
            assert!(error.to_string().contains("injected"));assert!(guard(&root).is_dir());
            assert_eq!(root.exists(),matches!(point,"published"|"parent-synced"));
            if root.exists(){assert_eq!(persistent_install::open_existing(&root).unwrap().content.apps.len(),2);}
            let before=tree(root.parent().unwrap());
            assert!(initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::NoteAlpha).is_err());
            assert_eq!(tree(root.parent().unwrap()),before);
        }
    }
    #[test]
    fn fixture_publication_is_complete_and_never_clobbers_racing_root() {
        let (_temp,root,data,bootstrap,old,key,catalog)=input();
        assert!(initialize_update_fixture(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::NoteAlpha,|stage| {
            if stage=="before-publish" {
                assert!(!root.exists());assert_eq!(persistent_install::open_existing(&guard(&root).join("install")).unwrap().content.apps.len(),2);
                assert_eq!(crate::app_data::snapshot_private_read_only(&data,&old.manifest.id).unwrap().files[0].sha256,sha256(b"alpha"));
                fs::create_dir(&root)?;fs::write(root.join("sentinel"),b"racing target")?;
            }Ok(())
        }).is_err());
        assert_eq!(fs::read(root.join("sentinel")).unwrap(),b"racing target");assert!(!root.join("catalog.json").exists());
        assert!(guard(&root).exists());
        let before=tree(root.parent().unwrap());
        assert!(initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,UpdateFixtureLayout::NoteAlpha).is_err());
        assert_eq!(tree(root.parent().unwrap()),before);
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum UpdateDecision {
    #[serde(rename = "prepared")] Prepared,
    #[serde(rename = "commit-decided")] CommitDecided,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct UpdatePackageIdentity {
    pub package_id: String, pub version: String, pub fingerprint: String,
    pub manifest_sha256: String, pub wasm_sha256: String, pub migration_sha256: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct UpdateLifecycleProof {
    pub app: u16, pub package: UpdatePackageIdentity, pub generation: u64,
    pub window: u64, pub worker_pid: u32, pub input_ready: bool,
    pub presented_source_sequence: u64, pub completed: bool,
    pub result_accepted: bool, pub native_exit_ok: bool, pub reaped: bool,
}
/// The raw slot's existence is represented by the required Option in UpdateRecord.
/// A header-only policy remains distinct from a missing policy.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PublicPolicySlotDigest { pub bytes: u64, pub sha256: String }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct UpdateRecord {
    pub schema_version: u32, pub operation: String, pub transaction_id: String,
    pub app: u16, pub old: UpdatePackageIdentity, pub new: UpdatePackageIdentity,
    pub catalog_sha256: String, pub bootstrap_sha256: String, pub trust_sha256: String,
    pub sibling_records_sha256: String, pub preserved_content_sha256: String,
    pub candidate_content_existed_before: bool, pub data_root_binding_sha256: String,
    #[serde(deserialize_with = "required_option")]
    pub public_policy_slot: Option<PublicPolicySlotDigest>,
    pub private_before: PrivateUpdateBaseline, pub backup_expected: bool,
    pub decision: UpdateDecision,
    #[serde(deserialize_with = "required_option")]
    pub confirmation: Option<UpdateLifecycleProof>,
    #[serde(deserialize_with = "required_option")]
    pub private_confirmed: Option<PrivateUpdateBaseline>,
}
fn required_option<'de, D: serde::Deserializer<'de>, T: Deserialize<'de>>(decoder: D) -> std::result::Result<Option<T>, D::Error> {
    Deserialize::deserialize(decoder)
}
pub(crate) fn valid_hash(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn version(value: &str) -> Result<()> {
    let parts: Vec<_> = value.split('.').collect();
    // The controlled directory is <version>-<64-byte hex fingerprint>, at most
    // 160 bytes. Keep the existing overflow-free, leading-zero version contract.
    ensure!(value.len()<=95 && parts.len()==3 && parts.iter().all(|part|!part.is_empty() && part.bytes().all(|c|c.is_ascii_digit())),"UPDATE_VERSION");
    Ok(())
}
fn validate_identity(identity: &UpdatePackageIdentity) -> Result<()> {
    ensure!(valid_package_id(&identity.package_id), "UPDATE_PACKAGE_ID");
    version(&identity.version)?;
    for value in [&identity.fingerprint, &identity.manifest_sha256, &identity.wasm_sha256, &identity.migration_sha256] {
        ensure!(valid_hash(value), "UPDATE_PACKAGE_HASH");
    }
    Ok(())
}
fn validate_private(value: &PrivateUpdateBaseline, package_id: &str) -> Result<()> {
    ensure!(value.schema_version==1 && value.package_id==package_id && valid_hash(&value.non_files_sha256), "UPDATE_PRIVATE_BASELINE");
    if let Some(tree)=&value.files {
        ensure!(value.namespace_exists && tree.entries<=1024 && tree.bytes<=8*1024*1024 && valid_hash(&tree.sha256), "UPDATE_PRIVATE_TREE");
    }
    Ok(())
}
impl UpdateRecord {
    pub(crate) fn validate(&self) -> Result<()> {
        ensure!(self.schema_version==1 && self.operation=="update" && (2..=4).contains(&self.app), "UPDATE_SCHEMA");
        ensure!(!self.transaction_id.is_empty() && self.transaction_id.len()<=96
            && self.transaction_id.bytes().all(|c| c.is_ascii_alphanumeric() || b"-_".contains(&c)), "UPDATE_TRANSACTION_ID");
        validate_identity(&self.old)?; validate_identity(&self.new)?;
        ensure!(self.old.package_id==self.new.package_id && crate::package::version_is_newer(&self.new.version,&self.old.version), "UPDATE_IDENTITY_TRANSITION");
        for value in [&self.catalog_sha256, &self.bootstrap_sha256, &self.trust_sha256,
            &self.sibling_records_sha256, &self.preserved_content_sha256, &self.data_root_binding_sha256] {
            ensure!(valid_hash(value), "UPDATE_HASH");
        }
        if let Some(slot)=&self.public_policy_slot {
            ensure!(slot.bytes>0 && slot.bytes<=crate::app_data::MAX_SLOT_BYTES as u64 && valid_hash(&slot.sha256), "UPDATE_PUBLIC_POLICY_SLOT");
        }
        validate_private(&self.private_before,&self.old.package_id)?;
        ensure!(self.backup_expected==self.private_before.files.is_some(), "UPDATE_BACKUP_EXPECTED");
        match (&self.decision,&self.confirmation,&self.private_confirmed) {
            (UpdateDecision::Prepared,None,None) => {},
            (UpdateDecision::CommitDecided,Some(proof),Some(private)) => {
                ensure!(proof.app==self.app && proof.package==self.new && proof.generation>0 && proof.window>0
                    && proof.worker_pid>0 && proof.presented_source_sequence>0 && proof.input_ready && proof.completed
                    && proof.result_accepted && proof.native_exit_ok && proof.reaped, "UPDATE_CONFIRMATION");
                validate_private(private,&self.old.package_id)?;
                ensure!(private.non_files_sha256==self.private_before.non_files_sha256
                    && (self.private_before.files.is_none() || private.files.is_some())
                    && (!self.private_before.namespace_exists || private.namespace_exists), "UPDATE_PRIVATE_CONFIRMATION");
            },
            _ => anyhow::bail!("UPDATE_DECISION_CONFIRMATION"),
        }
        Ok(())
    }
}
pub fn parse_update_record(raw: &[u8]) -> Result<UpdateRecord> {
    ensure!(raw.len()<=MAX_UPDATE_RECORD_BYTES, "UPDATE_RECORD_LIMIT");
    let record: UpdateRecord=serde_json::from_slice(raw)?;
    record.validate()?;
    Ok(record)
}

pub(crate) fn identity_from_package(package: &Package, manifest: &[u8]) -> Result<UpdatePackageIdentity> {
    ensure!(package.manifest.sdk_version==6 && package.manifest.entry=="main.wasm", "UPDATE_PACKAGE_SCHEMA");
    let identity=UpdatePackageIdentity { package_id:package.manifest.id.clone(), version:package.manifest.version.clone(),
        fingerprint:package.storage_fingerprint().iter().map(|byte|format!("{byte:02x}")).collect(),
        manifest_sha256:sha256(manifest),wasm_sha256:sha256(&package.bytes),
        migration_sha256:sha256(&serde_json::to_vec(&package.manifest.data_migration)?) };
    validate_identity(&identity)?; Ok(identity)
}
pub(crate) fn candidate_directory(root: &Path, identity: &UpdatePackageIdentity) -> PathBuf {
    root.join("packages").join(identity.package_id.as_bytes().iter().map(|b|format!("{b:02x}")).collect::<String>())
        .join(format!("{}-{}",identity.version,identity.fingerprint))
}
// The list is transient evidence. Recovery relies only on this versioned digest.
#[derive(Serialize)]
struct PreservedEntry { path:String, #[serde(rename="type")] kind:&'static str, length:u64, sha256:Option<String> }
fn preserved_content(root: &Path, files: &BTreeMap<String,Vec<u8>>, exclude: Option<&Path>, temporary: &[PathBuf]) -> Result<String> {
    fn directories(root:&Path, path:&Path, exclude:Option<&Path>, temporary:&[PathBuf], entries:&mut Vec<PreservedEntry>) -> Result<()> {
        ensure!(entries.len()<=512 && path.strip_prefix(root)?.components().count()<=3,"UPDATE_HISTORY_LIMIT");
        if Some(path)==exclude || temporary.iter().any(|temporary|temporary==path) { return Ok(()); }
        let metadata=fs::symlink_metadata(path)?;
        ensure!(metadata.is_dir() && !metadata.file_type().is_symlink(), "UPDATE_HISTORY_DIRECTORY");
        entries.push(PreservedEntry { path:path.strip_prefix(root)?.to_str().context("UPDATE_HISTORY_PATH")?.into(),kind:"directory",length:0,sha256:None });
        for child in fs::read_dir(path)?.take(257) { let child=child?.path(); if fs::symlink_metadata(&child)?.is_dir() { directories(root,&child,exclude,temporary,entries)?; } }
        Ok(())
    }
    let mut entries=Vec::new(); directories(root,&root.join("packages"),exclude,temporary,&mut entries)?;
    for (path,bytes) in files {
        if !path.starts_with("packages/") || exclude.is_some_and(|excluded|root.join(path).starts_with(excluded)) { continue; }
        entries.push(PreservedEntry {path:path.clone(),kind:"file",length:bytes.len() as u64,sha256:Some(sha256(bytes))});
    }
    entries.sort_by(|a,b|a.path.as_bytes().cmp(b.path.as_bytes()));
    let info=fs::symlink_metadata(root)?;
    let mut hash=Sha256::new();hash.update(b"ming-update-preserved-content-v1\0");
    // Bind the actual installation root across observations. Mutable directory
    // timestamps and link counts are excluded because staging adds children.
    hash.update(info.dev().to_be_bytes());hash.update(info.ino().to_be_bytes());
    hash.update(info.uid().to_be_bytes());hash.update(info.mode().to_be_bytes());
    hash.update(serde_json::to_vec(&entries)?);Ok(format!("{:x}",hash.finalize()))
}
fn sibling_records(bytes:&[u8], target:&str) -> Result<String> {
    let text=std::str::from_utf8(bytes)?;
    let (header,lines)=text.split_once('\n').context("UPDATE_RECORDS_HEADER")?;
    ensure!(header=="ming-installed-packages-v1" && lines.ends_with('\n'),"UPDATE_RECORDS_SCHEMA");
    let mut preserved=String::from("ming-update-sibling-records-v1\0");
    let mut target_count=0;
    for line in lines.lines() {
        if line.split('\t').next()==Some(target) { target_count+=1; }
        else { preserved.push_str(line);preserved.push('\n'); }
    }
    ensure!(target_count==1,"UPDATE_TARGET_RECORD");
    Ok(sha256(preserved.as_bytes()))
}
fn verify_history(registry:&PackageInstallRegistry,files:&BTreeMap<String,Vec<u8>>) -> Result<BTreeMap<PathBuf,UpdatePackageIdentity>> {
    let root=registry.root_path();let mut result=BTreeMap::new();
    for (path,manifest) in files.iter().filter(|(path,_)|path.starts_with("packages/") && path.ends_with("/app.json")) {
        let package=load_package(&root.join(path))?;
        registry.verify_package_source(&package)?;
        let identity=identity_from_package(&package,manifest)?;
        let directory=candidate_directory(root,&identity);
        ensure!(directory.join("app.json")==root.join(path)
            && files.get(&format!("{}/main.wasm",path.strip_suffix("/app.json").unwrap()))==Some(&package.bytes)
            && crate::package::read_bounded(&root.join(path),16*1024,"UPDATE_HISTORY_MANIFEST")?==*manifest,"UPDATE_HISTORY_IDENTITY");
        result.insert(directory,identity);
    }
    Ok(result)
}

/// Read-only preparation under the registry lock and held storage authority.
/// No marker, candidate directory, namespace, policy, or lock is created here.
pub(crate) fn prepare_record(registry:&PackageInstallRegistry,authority:&PackageStorageAuthority,data_root:&Path,
    app:u16,candidate:&Package,transaction_id:&str) -> Result<UpdateRecord> {
    ensure!((2..=4).contains(&app),"UPDATE_TARGET_APP");
    registry.verify_package_source(candidate)?;
    let (content,files)=persistent_install::snapshot_before_update(registry)?;
    let current=content.apps.iter().find(|entry|entry.app==app).context("UPDATE_TARGET_ACTIVE")?;
    let manifest=files.get(&current.manifest_path).context("UPDATE_TARGET_MANIFEST")?;
    let old_package=load_package(&registry.root_path().join(&current.manifest_path))?;
    let old=identity_from_package(&old_package,manifest)?;
    let new=identity_from_package(candidate,&serde_json::to_vec(&candidate.manifest)?)?;
    ensure!(old.package_id==new.package_id && crate::package::version_is_newer(&new.version,&old.version),"UPDATE_IDENTITY_TRANSITION");
    if let Some(migration)=&candidate.manifest.data_migration { ensure!(migration.from_version==old.version,"UPDATE_MIGRATION_FROM"); }
    let history=verify_history(registry,&files)?;
    let candidate_path=candidate_directory(registry.root_path(),&new);
    let candidate_existed=history.contains_key(&candidate_path);
    if let Some(existing)=history.get(&candidate_path) { ensure!(*existing==new,"UPDATE_CANDIDATE_IDENTITY"); }
    let private_before=authority.observe_update(&old.package_id)?;
    let (data_root_binding_sha256,slot)=authority.observe_update_authority(data_root)?;
    let record=UpdateRecord {schema_version:1,operation:"update".into(),transaction_id:transaction_id.into(),app,
        sibling_records_sha256:sibling_records(files.get("installed-packages-v1").context("UPDATE_REGISTRY")?,&old.package_id)?,
        preserved_content_sha256:preserved_content(registry.root_path(),&files,None,&[])?,old,new,
        catalog_sha256:content.catalog_sha256,bootstrap_sha256:content.bootstrap_sha256,trust_sha256:content.trust_sha256,
        candidate_content_existed_before:candidate_existed,data_root_binding_sha256,
        public_policy_slot:slot.map(|(bytes,sha256)|PublicPolicySlotDigest {bytes,sha256}),
        backup_expected:private_before.files.is_some(),private_before,decision:UpdateDecision::Prepared,
        confirmation:None,private_confirmed:None};
    // The bounded outer is the actual contract, including the largest identities.
    let parsed=parse_update_record(&serde_json::to_vec(&record)?)?;
    ensure!(parsed==record,"UPDATE_RECORD_READBACK");
    let (_,again)=persistent_install::snapshot_before_update(registry)?;
    ensure!(files==again,"UPDATE_CONTENT_CHANGED");
    Ok(record)
}

/// Fields are private: only this module can authorize these two exact markers.
pub(crate) struct UpdateSnapshotAccess<'a> { outer:&'a [u8],inner:Option<&'a [u8]>,temporary:&'a [PathBuf] }
impl UpdateSnapshotAccess<'_> {
    pub(crate) fn temporary(&self)->&[PathBuf] { self.temporary }
    pub(crate) fn markers(&self)->Vec<(&'static str,&[u8])> {
        let mut markers=vec![(UPDATE_PENDING_NAME,self.outer)];
        if let Some(inner)=self.inner {markers.push((INNER_JOURNAL_NAME,inner));} markers
    }
}
#[derive(Serialize,Deserialize)]
#[serde(rename_all="camelCase",deny_unknown_fields)]
struct BoundInnerJournal {
    package_id:String,old_version:String,old_fingerprint:String,new_version:String,new_fingerprint:String,
    backup_name:String,migration_declared:bool,phase:String,
}
fn validate_inner(record:&UpdateRecord,raw:Option<&[u8]>) -> Result<Option<BoundInnerJournal>> {
    let Some(raw)=raw else {return Ok(None);};
    ensure!(raw.len()<=16*1024,"UPDATE_INNER_LIMIT");
    let inner:BoundInnerJournal=serde_json::from_slice(raw)?;
    ensure!(inner.package_id==record.old.package_id && inner.old_version==record.old.version
        && inner.old_fingerprint==record.old.fingerprint && inner.new_version==record.new.version
        && inner.new_fingerprint==record.new.fingerprint && inner.backup_name==format!(".ming-update-backup-{}",record.transaction_id)
        && ["planned","dataSwapped","recordPublished","startupAttempted","startupConfirmed","rollingBack"].contains(&inner.phase.as_str()),"UPDATE_INNER_IDENTITY");
    let no_migration=sha256(b"null");
    ensure!(inner.migration_declared==(record.new.migration_sha256!=no_migration),"UPDATE_INNER_MIGRATION");
    match record.decision {
        UpdateDecision::Prepared=>ensure!(inner.phase!="startupConfirmed","UPDATE_PREPARED_INNER_PHASE"),
        UpdateDecision::CommitDecided=>ensure!(["startupAttempted","startupConfirmed"].contains(&inner.phase.as_str()),"UPDATE_COMMIT_INNER_PHASE"),
    }
    Ok(Some(inner))
}
pub(crate) fn validate_inner_binding(record:&UpdateRecord, raw:&[u8]) -> Result<()> {
    validate_inner(record,Some(raw)).map(|_|())
}
/// Checks content and authority bindings only. The coordinator must separately
/// validate private layout, backup state and its launch/mutation gate under the
/// same operation mutex before using this observation to authorize any mutation.
pub(crate) fn validate_transaction_snapshot(registry:&PackageInstallRegistry,authority:&PackageStorageAuthority,data_root:&Path,
    record:&UpdateRecord,outer:&[u8],inner:Option<&[u8]>) -> Result<ContentSnapshot> {
    validate_transaction_snapshot_with_temporary(registry,authority,data_root,record,outer,inner,&[])
}
fn validate_transaction_snapshot_with_temporary(registry:&PackageInstallRegistry,authority:&PackageStorageAuthority,data_root:&Path,
    record:&UpdateRecord,outer:&[u8],inner:Option<&[u8]>,temporary:&[PathBuf]) -> Result<ContentSnapshot> {
    ensure!(parse_update_record(outer)?==*record,"UPDATE_OUTER_IDENTITY");
    let bound_inner=validate_inner(record,inner)?;
    let access=UpdateSnapshotAccess {outer,inner,temporary};
    let (content,files)=persistent_install::snapshot_for_update(registry,&access)?;
    ensure!(content.catalog_sha256==record.catalog_sha256 && content.bootstrap_sha256==record.bootstrap_sha256
        && content.trust_sha256==record.trust_sha256,"UPDATE_STATIC_CONTENT");
    let active=content.apps.iter().find(|entry|entry.app==record.app).context("UPDATE_TARGET_ACTIVE")?;
    let history=verify_history(registry,&files)?;
    let active_identity=history.get(&registry.root_path().join(&active.manifest_path).parent().unwrap().to_path_buf()).context("UPDATE_TARGET_HISTORY")?;
    ensure!(*active_identity==record.old || *active_identity==record.new,"UPDATE_TARGET_IDENTITY");
    let active_is_new=*active_identity==record.new;
    let allowed=match record.decision {
        UpdateDecision::CommitDecided=>active_is_new,
        UpdateDecision::Prepared=>match bound_inner.as_ref().map(|journal|journal.phase.as_str()) {
            None|Some("planned")=>!active_is_new,
            Some("dataSwapped"|"rollingBack")=>true,
            Some("recordPublished"|"startupAttempted")=>active_is_new,
            _=>false,
        },
    };
    ensure!(allowed,"UPDATE_DECISION_PHASE_ACTIVE");
    ensure!(sibling_records(files.get("installed-packages-v1").context("UPDATE_REGISTRY")?,&record.old.package_id)?==record.sibling_records_sha256,"UPDATE_SIBLING_RECORDS");
    let candidate=candidate_directory(registry.root_path(),&record.new);
    if let Some(identity)=history.get(&candidate) {ensure!(*identity==record.new,"UPDATE_CANDIDATE_IDENTITY");}
    else {ensure!(!record.candidate_content_existed_before && bound_inner.is_none(),"UPDATE_CANDIDATE_MISSING");}
    let excluded=if record.candidate_content_existed_before {None}else{Some(candidate.as_path())};
    ensure!(preserved_content(registry.root_path(),&files,excluded,temporary)?==record.preserved_content_sha256,"UPDATE_PRESERVED_CONTENT");
    let (binding,slot)=authority.observe_update_authority(data_root)?;
    ensure!(binding==record.data_root_binding_sha256
        && slot.map(|(bytes,sha256)|PublicPolicySlotDigest {bytes,sha256})==record.public_policy_slot,"UPDATE_AUTHORITY_BINDING");
    let (_,again)=persistent_install::snapshot_for_update(registry,&access)?;
    ensure!(files==again,"UPDATE_CONTENT_CHANGED");
    Ok(content)
}

/// A bounded observation made before the trial worker can launch. Journal bytes
/// are included so a verifier hashes and parses the exact durable representation.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TrialObservation {
    pub schema_version: u32,
    pub transaction_id: String,
    pub outer_record_sha256: String,
    pub inner_journal_sha256: String,
    pub outer_record_base64: String,
    pub inner_journal_base64: String,
    pub inner_phase: String,
    pub content: ContentSnapshot,
    pub private_content: PrivateUpdateBaseline,
}
#[derive(Debug)]
pub struct TrialUpdate { pub record: UpdateRecord, pub catalog: Catalog, pub observation: TrialObservation }
#[derive(Debug)]
pub struct UpdateCommit { pub record: UpdateRecord, pub content: ContentSnapshot }
#[derive(Debug)]
pub enum UpdateRecovery { RolledBack(ContentSnapshot), Committed(ContentSnapshot) }
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateFailure {
    pub status: String, pub stage: String, pub error: String,
    pub persistent_block_confirmed: bool, pub compensation_attempted: bool,
}
impl std::fmt::Display for UpdateFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { write!(f, "{} at {}: {}", self.status, self.stage, self.error) }
}
impl std::error::Error for UpdateFailure {}
fn failure(status: &str, stage: &str, error: impl std::fmt::Display) -> UpdateFailure {
    UpdateFailure { status: status.into(), stage: stage.into(), error: error.to_string(), persistent_block_confirmed: false, compensation_attempted: false }
}

#[cfg(test)]
pub(crate) fn test_checkpoint(stage: &str) -> Result<()> {
    crate::app_data::update_test_checkpoint(stage)
}
fn checkpoint(stage: &str) -> Result<()> {
    #[cfg(test)]
    test_checkpoint(stage)?;
    let _ = stage;
    Ok(())
}
fn step<T>(stage: &str, action: impl FnOnce() -> Result<T>) -> Result<T> {
    checkpoint(stage).with_context(|| stage.to_owned())?;
    action().with_context(|| stage.to_owned())
}
#[derive(Clone, Copy, PartialEq, Eq)]
struct MarkerIdentity { device: u64, inode: u64 }
fn marker_identity(path: &Path) -> Result<MarkerIdentity> {
    let info = fs::symlink_metadata(path)?;
    ensure!(info.is_file() && !info.file_type().is_symlink() && info.nlink() == 1
        && info.uid() == unsafe { libc::geteuid() } && info.mode() & 0o7777 == 0o600, "UPDATE_MARKER_IDENTITY");
    Ok(MarkerIdentity { device: info.dev(), inode: info.ino() })
}
fn exact_marker(registry: &PackageInstallRegistry, bytes: &[u8], identity: MarkerIdentity) -> Result<()> {
    ensure!(marker_identity(&registry.root_path().join(UPDATE_PENDING_NAME))? == identity, "UPDATE_MARKER_REPLACED");
    ensure!(registry.read_coordinated_marker(UPDATE_PENDING_NAME, MAX_UPDATE_RECORD_BYTES as u64)?.as_deref() == Some(bytes), "UPDATE_MARKER_CHANGED");
    ensure!(marker_identity(&registry.root_path().join(UPDATE_PENDING_NAME))? == identity, "UPDATE_MARKER_REPLACED");
    Ok(())
}
fn context(record: &UpdateRecord, data_root: &Path) -> Result<CoordinatedRegistryContext> {
    let decode = |hash: &str| -> Result<[u8;32]> {
        ensure!(valid_hash(hash), "UPDATE_HASH");
        let mut bytes = [0;32]; for (index, byte) in bytes.iter_mut().enumerate() { *byte = u8::from_str_radix(&hash[index*2..index*2+2],16)?; } Ok(bytes)
    };
    Ok(CoordinatedRegistryContext { package_id: record.old.package_id.clone(), old_version: record.old.version.clone(),
        old_fingerprint: decode(&record.old.fingerprint)?, new_version: record.new.version.clone(), new_fingerprint: decode(&record.new.fingerprint)?,
        data: CoordinatedPrivateUpdate { transaction_id: record.transaction_id.clone(), baseline: record.private_before.clone(),
            backup_name: format!(".ming-update-backup-{}", record.transaction_id) }, outer: record.clone(), data_root: data_root.into() })
}
fn load_bound(registry: &PackageInstallRegistry, authority: &PackageStorageAuthority, data_root: &Path)
    -> Result<(UpdateRecord, Vec<u8>, MarkerIdentity)> {
    let raw = registry.read_coordinated_marker(UPDATE_PENDING_NAME, MAX_UPDATE_RECORD_BYTES as u64)?.context("UPDATE_OUTER_MISSING")?;
    let identity = marker_identity(&registry.root_path().join(UPDATE_PENDING_NAME))?;
    let record = parse_update_record(&raw)?;
    let inner = registry.read_coordinated_marker(INNER_JOURNAL_NAME, 16*1024)?;
    validate_transaction_snapshot(registry, authority, data_root, &record, &raw, inner.as_deref())?;
    exact_marker(registry, &raw, identity)?;
    Ok((record, raw, identity))
}
// The caller owns the registry operation mutex and the target launch gate; the
// authority rejects sessions throughout each coordinated private observation.
fn observe_trial_locked(registry: &PackageInstallRegistry, authority: &PackageStorageAuthority, data_root: &Path,
    expected: &UpdateRecord) -> Result<TrialObservation> {
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    let (record, raw, identity) = load_bound(registry, authority, data_root)?;
    ensure!(record == *expected && record.decision == UpdateDecision::Prepared, "UPDATE_TRIAL_TRANSACTION");
    let outer = TemporaryNode::capture(&registry.root_path().join(UPDATE_PENDING_NAME), false, MAX_UPDATE_RECORD_BYTES)?;
    ensure!(outer.bytes == raw, "UPDATE_TRIAL_OUTER_CHANGED");
    let inner = TemporaryNode::capture(&registry.root_path().join(INNER_JOURNAL_NAME), false, 16*1024)?;
    let bound = validate_inner(&record, Some(&inner.bytes))?.context("UPDATE_TRIAL_INNER_MISSING")?;
    ensure!(bound.phase == "recordPublished", "UPDATE_TRIAL_INNER_PHASE");
    let content = validate_transaction_snapshot(registry, authority, data_root, &record, &raw, Some(&inner.bytes))?;
    let data = context(&record, data_root)?.data;
    authority.validate_coordinated_rollback(&data, crate::app_data::PrivateUpdatePhase::SwappedUnconfirmed)?;
    let private_content = authority.observe_coordinated_update(&data)?;
    checkpoint("trial-observed")?;
    ensure!(content == validate_transaction_snapshot(registry, authority, data_root, &record, &raw, Some(&inner.bytes))?, "UPDATE_TRIAL_CONTENT_CHANGED");
    authority.validate_coordinated_rollback(&data, crate::app_data::PrivateUpdatePhase::SwappedUnconfirmed)?;
    ensure!(private_content == authority.observe_coordinated_update(&data)?, "UPDATE_TRIAL_PRIVATE_CHANGED");
    exact_marker(registry, &raw, identity)?;
    outer.verify()?; inner.verify()?;
    Ok(TrialObservation { schema_version: 1, transaction_id: record.transaction_id,
        outer_record_sha256: sha256(&raw), inner_journal_sha256: sha256(&inner.bytes),
        outer_record_base64: STANDARD.encode(&raw), inner_journal_base64: STANDARD.encode(&inner.bytes),
        inner_phase: bound.phase, content, private_content })
}
// Unpublished objects never supply a decision. Only the exact transaction names
// are admitted into a recovery-only view, after bounded no-follow captures.
type TemporaryStamp = (u64,u64,u32,u32,u64,u64,i64,i64,i64,i64);
fn temporary_stamp(info:&fs::Metadata) -> TemporaryStamp {
    (info.dev(),info.ino(),info.mode(),info.uid(),info.nlink(),info.len(),info.mtime(),info.mtime_nsec(),info.ctime(),info.ctime_nsec())
}
struct TemporaryNode { path:PathBuf, handle:File, stamp:TemporaryStamp, bytes:Vec<u8> }
impl TemporaryNode {
    fn capture(path:&Path,directory:bool,limit:usize)->Result<Self> {
        let before=fs::symlink_metadata(path)?;
        ensure!(!before.file_type().is_symlink() && if directory {before.is_dir()} else {before.is_file()},"UPDATE_TEMP_TYPE");
        ensure!(before.uid()==unsafe{libc::geteuid()} && before.mode()&0o7777==if directory {0o700}else{0o600}
            && (directory || before.nlink()==1) && (directory || before.len()<=limit as u64),"UPDATE_TEMP_PERMISSIONS_LIMIT");
        let mut handle=OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW|libc::O_NONBLOCK|if directory {libc::O_DIRECTORY}else{0}).open(path)?;
        let stamp=temporary_stamp(&before);
        ensure!(temporary_stamp(&handle.metadata()?)==stamp,"UPDATE_TEMP_CHANGED");
        let mut bytes=Vec::new();
        if !directory {
            Read::by_ref(&mut handle).take(limit as u64+1).read_to_end(&mut bytes)?;
            ensure!(bytes.len()<=limit && bytes.len() as u64==before.len(),"UPDATE_TEMP_LIMIT");
        }
        let node=Self{path:path.into(),handle,stamp,bytes}; node.verify()?;Ok(node)
    }
    fn verify(&self)->Result<()> {
        ensure!(temporary_stamp(&self.handle.metadata()?)==self.stamp
            && temporary_stamp(&fs::symlink_metadata(&self.path)?)==self.stamp,"UPDATE_TEMP_CHANGED");Ok(())
    }
    fn verify_directory_identity(&self)->Result<()> {
        let check=|actual:fs::Metadata| actual.is_dir() && !actual.file_type().is_symlink()
            && (actual.dev(),actual.ino(),actual.mode(),actual.uid())==(self.stamp.0,self.stamp.1,self.stamp.2,self.stamp.3);
        ensure!(check(self.handle.metadata()?) && check(fs::symlink_metadata(&self.path)?),"UPDATE_TEMP_PARENT_CHANGED");Ok(())
    }
}
struct RecoveryTemporary { top:TemporaryNode, children:Vec<TemporaryNode>, parent:TemporaryNode }
impl RecoveryTemporary {
    fn capture(path:&Path,directory:bool,limit:usize)->Result<Self> {
        let parent=TemporaryNode::capture(path.parent().context("UPDATE_TEMP_PARENT")?,true,0)?;
        let top=TemporaryNode::capture(path,directory,limit)?;
        let mut children=Vec::new();
        if directory {
            let names=fs::read_dir(path)?.take(3).collect::<std::io::Result<Vec<_>>>()?;
            ensure!(names.len()<=2,"UPDATE_TEMP_CHILDREN");
            for child in names {
                let name=child.file_name();
                ensure!(name=="app.json" || name=="main.wasm","UPDATE_TEMP_CHILD");
                children.push(TemporaryNode::capture(&child.path(),false,if name=="app.json" {64*1024}else{crate::package::MAX_MODULE_BYTES})?);
            }
        }
        let result=Self{top,children,parent};result.verify()?;Ok(result)
    }
    fn verify(&self)->Result<()> {
        self.parent.verify_directory_identity()?;self.top.verify()?;
        for child in &self.children {child.verify()?;}Ok(())
    }
    fn unlink(parent:&TemporaryNode,node:&TemporaryNode,directory:bool)->Result<()> {
        parent.verify_directory_identity()?;
        if directory {node.verify_directory_identity()?;}else{node.verify()?;}
        let name=CString::new(node.path.file_name().context("UPDATE_TEMP_NAME")?.as_bytes())?;
        ensure!(unsafe{libc::unlinkat(parent.handle.as_raw_fd(),name.as_ptr(),if directory{libc::AT_REMOVEDIR}else{0})}==0,
            "UPDATE_TEMP_UNLINK {}",std::io::Error::last_os_error());
        checkpoint(if directory{"temporary-rmdir"}else{"temporary-unlink"})?;
        parent.handle.sync_all()?;checkpoint("temporary-sync")
    }
    fn remove(self)->Result<()> {
        self.verify()?;
        for child in &self.children {Self::unlink(&self.top,child,false)?;}
        Self::unlink(&self.parent,&self.top,self.top.handle.metadata()?.is_dir())
    }
}
fn recovery_temporary(registry:&PackageInstallRegistry,record:&UpdateRecord,inner:Option<&BoundInnerJournal>)->Result<Vec<RecoveryTemporary>> {
    let root=registry.root_path();let phase=inner.map(|inner|inner.phase.as_str());
    let prepared=record.decision==UpdateDecision::Prepared;
    let candidate=candidate_directory(root,&record.new);
    let paths=[(candidate.parent().unwrap().join(format!(".ming-update-candidate-{}.tmp",record.transaction_id)),true,0,"candidate"),
        (root.join(format!(".ming-update-journal-{}.tmp",record.transaction_id)),false,16*1024,"journal"),
        (root.join(format!(".ming-update-record-{}.tmp",record.transaction_id)),false,64*1024,"record"),
        (root.join(format!(".ming-update-decision-{}.tmp",record.transaction_id)),false,MAX_UPDATE_RECORD_BYTES,"decision")];
    let mut result=Vec::new();
    for (path,directory,limit,kind) in paths {
        match fs::symlink_metadata(&path) {
            Err(error) if error.kind()==std::io::ErrorKind::NotFound=>continue,
            Err(error)=>return Err(error.into()),Ok(_)=>{},
        }
        let allowed=match kind {
            "candidate"=>prepared && phase.is_none() && !record.candidate_content_existed_before
                && matches!(fs::symlink_metadata(&candidate),Err(error) if error.kind()==std::io::ErrorKind::NotFound),
            "journal"=>if prepared {phase!=Some("startupConfirmed")}else{matches!(phase,Some("startupAttempted"|"startupConfirmed"))},
            "record"=>prepared && matches!(phase,Some("dataSwapped"|"rollingBack")),
            "decision"=>prepared && phase==Some("startupAttempted"),_=>false,
        };
        ensure!(allowed,"UPDATE_TEMP_PHASE {kind}");
        let captured=RecoveryTemporary::capture(&path,directory,limit)?;
        if kind=="journal" {
            ensure!(fs::symlink_metadata(&candidate)?.is_dir(),"UPDATE_TEMP_JOURNAL_CANDIDATE");
            let next: &[&str]=match (prepared,phase) {
                (true,None)=>&["planned"],(true,Some("planned"))=>&["dataSwapped","rollingBack"],
                (true,Some("dataSwapped"))=>&["recordPublished","rollingBack"],
                (true,Some("recordPublished"))=>&["startupAttempted","rollingBack"],
                (true,Some("startupAttempted"|"rollingBack"))=>&["rollingBack"],
                (false,Some("startupAttempted"|"startupConfirmed"))=>&["startupConfirmed"],_=>&[],
            };
            let mut possible=false;
            for next in next {
                let expected=BoundInnerJournal{package_id:record.old.package_id.clone(),old_version:record.old.version.clone(),old_fingerprint:record.old.fingerprint.clone(),
                    new_version:record.new.version.clone(),new_fingerprint:record.new.fingerprint.clone(),backup_name:format!(".ming-update-backup-{}",record.transaction_id),
                    migration_declared:record.new.migration_sha256!=sha256(b"null"),phase:(*next).into()};
                possible|=serde_json::to_vec(&expected)?.starts_with(&captured.top.bytes);
            }
            ensure!(possible,"UPDATE_TEMP_JOURNAL_BYTES");
        } else if kind=="record" {
            let bytes=registry.read_coordinated_marker("installed-packages-v1",64*1024)?.context("UPDATE_REGISTRY")?;
            let text=std::str::from_utf8(&bytes)?;let target=if phase==Some("rollingBack"){&record.old}else{&record.new};
            let mut expected=String::new();
            for line in text.lines() {
                if line.split('\t').next()==Some(record.old.package_id.as_str()) {
                    expected.push_str(&format!("{}\tactive\t{}\t{}\n",target.package_id,target.version,target.fingerprint));
                }else{expected.push_str(line);expected.push('\n');}
            }
            ensure!(expected.as_bytes().starts_with(&captured.top.bytes),"UPDATE_TEMP_RECORD_BYTES");
        } else if kind=="candidate" {
            if let Some(manifest)=captured.children.iter().find(|child|child.path.file_name().is_some_and(|name|name=="app.json")) {
                // Complete manifests are bound exactly. A killed write may leave
                // a bounded incomplete JSON prefix, which is never published.
                if serde_json::from_slice::<crate::package::Manifest>(&manifest.bytes).is_ok() {
                    ensure!(sha256(&manifest.bytes)==record.new.manifest_sha256,"UPDATE_TEMP_MANIFEST_IDENTITY");
                }
            }
        } else if kind=="decision" {
            if let Ok(value)=parse_update_record(&captured.top.bytes) {
                let mut expected=record.clone();expected.decision=value.decision.clone();expected.confirmation=value.confirmation.clone();expected.private_confirmed=value.private_confirmed.clone();
                ensure!(value.decision==UpdateDecision::CommitDecided && value==expected,"UPDATE_TEMP_DECISION_IDENTITY");
            }
        }
        result.push(captured);
    }
    ensure!(result.len()<=1,"UPDATE_MULTIPLE_TEMPORARIES");Ok(result)
}
fn validate_recovery_private(authority:&PackageStorageAuthority,record:&UpdateRecord,data_root:&Path,inner:Option<&BoundInnerJournal>)->Result<()> {
    use crate::app_data::{CoordinatedCleanupPhase,PrivateUpdatePhase};
    let context=context(record,data_root)?;let phase=inner.map(|inner|inner.phase.as_str());
    match record.decision {
        UpdateDecision::Prepared=>match phase {
            None=>authority.validate_coordinated_cleanup(&context.data,&record.private_before,CoordinatedCleanupPhase::JournalCleared,false)?,
            Some(phase)=>{
                let private=match phase {"planned"=>PrivateUpdatePhase::BeforeSwap,"rollingBack"=>PrivateUpdatePhase::RestoringOld,
                    "dataSwapped"|"recordPublished"|"startupAttempted"=>PrivateUpdatePhase::SwappedUnconfirmed,_=>anyhow::bail!("UPDATE_RECOVERY_PRIVATE_PHASE")};
                authority.validate_coordinated_rollback(&context.data,private)?;
                ensure!(!(inner.unwrap().migration_declared && private==PrivateUpdatePhase::SwappedUnconfirmed
                    && authority.observe_coordinated_update(&context.data)?.files.is_none()),"UPDATE_RECOVERY_MIGRATED_FILES_MISSING");
            }
        },
        UpdateDecision::CommitDecided=>{
            let phase=match phase {Some("startupAttempted")=>CoordinatedCleanupPhase::BeforeConfirmation,Some("startupConfirmed")=>CoordinatedCleanupPhase::Cleaning,
                None=>CoordinatedCleanupPhase::JournalCleared,_=>anyhow::bail!("UPDATE_RECOVERY_PRIVATE_PHASE")};
            authority.validate_coordinated_cleanup(&context.data,record.private_confirmed.as_ref().context("UPDATE_RECOVERY_CONFIRMATION")?,phase,
                record.backup_expected || record.new.migration_sha256!=sha256(b"null"))?;
        }
    }Ok(())
}
fn load_recovery_bound(registry:&PackageInstallRegistry,authority:&PackageStorageAuthority,data_root:&Path,expected:&str)->Result<(UpdateRecord,Vec<u8>,MarkerIdentity)> {
    let raw=registry.read_coordinated_marker(UPDATE_PENDING_NAME,MAX_UPDATE_RECORD_BYTES as u64)?.context("UPDATE_OUTER_MISSING")?;
    let identity=marker_identity(&registry.root_path().join(UPDATE_PENDING_NAME))?;
    let record=parse_update_record(&raw)?;ensure!(record.transaction_id==expected,"UPDATE_RECOVERY_TRANSACTION");
    let inner=registry.read_coordinated_marker(INNER_JOURNAL_NAME,16*1024)?;
    let bound=validate_inner(&record,inner.as_deref())?;
    let temporary=recovery_temporary(registry,&record,bound.as_ref())?;
    let paths:Vec<_>=temporary.iter().map(|temporary|temporary.top.path.clone()).collect();
    validate_transaction_snapshot_with_temporary(registry,authority,data_root,&record,&raw,inner.as_deref(),&paths)?;
    validate_recovery_private(authority,&record,data_root,bound.as_ref())?;
    exact_marker(registry,&raw,identity)?;
    ensure!(registry.read_coordinated_marker(INNER_JOURNAL_NAME,16*1024)?==inner,"UPDATE_INNER_CHANGED");
    for object in &temporary {object.verify()?;}
    checkpoint("temporary-validated")?;
    for object in temporary {object.remove()?;}
    let value=load_bound(registry,authority,data_root)?;
    ensure!(value.0==record && value.1==raw && value.2==identity,"UPDATE_RECOVERY_CHANGED");Ok(value)
}

fn create_outer(registry: &PackageInstallRegistry, raw: &[u8], prefix: &str) -> Result<MarkerIdentity> {
    let path = registry.root_path().join(UPDATE_PENDING_NAME);
    let mut file = step(&format!("{prefix}-create"), || Ok(OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(&path)?))?;
    let identity = marker_identity(&path)?;
    checkpoint(&format!("{prefix}-created"))?;
    step(&format!("{prefix}-write"), || { file.write_all(raw)?; Ok(()) })?;
    checkpoint(&format!("{prefix}-written"))?;
    step(&format!("{prefix}-file-sync"), || { file.sync_all()?; Ok(()) })?;
    step(&format!("{prefix}-directory-sync"), || { File::open(registry.root_path())?.sync_all()?; Ok(()) })?;
    step(&format!("{prefix}-readback"), || exact_marker(registry, raw, identity))?;
    checkpoint(&format!("{prefix}-durable"))?;
    Ok(identity)
}
/// At most one attempt, under operation; never overwrite a changed decision or
/// another transaction. A surviving name alone is not evidence of durability.
fn block_failure(registry: &PackageInstallRegistry, raw: &[u8], stage: &str, error: impl std::fmt::Display) -> UpdateFailure {
    let error = error.to_string();
    registry.with_update_operation(|| Ok(block_failure_locked(registry, raw, stage, &error, false)))
        .unwrap_or_else(|lock| failure("commit-uncertain", stage, format!("{error}; block lock: {lock:#}")))
}
fn block_failure_locked(registry: &PackageInstallRegistry, raw: &[u8], stage: &str, error: impl std::fmt::Display,
    recreate_after_own_unlink: bool) -> UpdateFailure {
    let mut result = failure("commit-uncertain", stage, error);
    let attempt = (|| -> Result<()> {
        match registry.read_coordinated_marker(UPDATE_PENDING_NAME, MAX_UPDATE_RECORD_BYTES as u64)? {
            Some(current) => {
                ensure!(current == raw, "UPDATE_BLOCK_CHANGED");
                let identity = marker_identity(&registry.root_path().join(UPDATE_PENDING_NAME))?;
                let file = OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW).open(registry.root_path().join(UPDATE_PENDING_NAME))?;
                step("block-file-sync", || { file.sync_all()?; Ok(()) })?;
                step("block-directory-sync", || { File::open(registry.root_path())?.sync_all()?; Ok(()) })?;
                step("block-readback", || exact_marker(registry, raw, identity))
            }
            None => {
                // Another coordinator may already have finalized this transaction.
                // Only the operation that unlinked this exact marker may recreate
                // it, while retaining the operation mutex through compensation.
                ensure!(recreate_after_own_unlink, "UPDATE_BLOCK_MARKER_DISAPPEARED");
                result.compensation_attempted = true;
                match fs::symlink_metadata(registry.root_path().join(persistent_install::PENDING_NAME)) {
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
                    Err(error) => return Err(error.into()),
                    Ok(_) => anyhow::bail!("UPDATE_BLOCK_OLD_PENDING"),
                }
                create_outer(registry, raw, "compensation").map(|_| ())
            }
        }
    })();
    match attempt {
        Ok(()) => {
            result.persistent_block_confirmed = true;
            result.status = if parse_update_record(raw).is_ok_and(|record| record.decision == UpdateDecision::CommitDecided) { "commit-pending" } else { "fatal" }.into();
        }
        Err(error) => result.error.push_str(&format!("; block: {error:#}")),
    }
    result
}
fn finalize(registry: &PackageInstallRegistry, authority: &PackageStorageAuthority, data_root: &Path,
    record: &UpdateRecord, raw: &[u8], identity: MarkerIdentity) -> std::result::Result<ContentSnapshot, UpdateFailure> {
    let result = registry.with_update_operation(|| {
        let mut unlinked = false;
        let attempt = (|| -> Result<ContentSnapshot> {
        exact_marker(registry, raw, identity)?;
        let (current, bytes, current_identity) = load_bound(registry, authority, data_root)?;
        ensure!(current == *record && bytes == raw && current_identity == identity, "UPDATE_FINAL_TRANSACTION");
        ensure!(registry.read_coordinated_marker(INNER_JOURNAL_NAME, 16*1024)?.is_none(), "UPDATE_FINAL_INNER");
        let data = context(record, data_root)?.data;
        let expected = if record.decision == UpdateDecision::Prepared { &record.private_before } else { record.private_confirmed.as_ref().context("UPDATE_FINAL_CONFIRMATION")? };
        authority.validate_coordinated_cleanup(&data, expected, crate::app_data::CoordinatedCleanupPhase::JournalCleared,
            record.decision == UpdateDecision::CommitDecided && (record.backup_expected || record.new.migration_sha256 != sha256(b"null")))?;
        step("outer-unlink", || { exact_marker(registry, raw, identity)?; fs::remove_file(registry.root_path().join(UPDATE_PENDING_NAME))?; unlinked = true; Ok(()) })?;
        checkpoint("outer-unlinked")?;
        step("outer-remove-directory-sync", || { File::open(registry.root_path())?.sync_all()?; Ok(()) })?;
        step("final-strict-readback", || persistent_install::snapshot(registry))
        })();
        // Preserve operation ownership across unlink, a failed sync/readback,
        // and its one compensation attempt; no unmarked mutation gap exists.
        Ok(attempt.map_err(|error| block_failure_locked(registry, raw, "finalize", format!("{error:#}"), unlinked)))
    });
    result.map_err(|error| failure("commit-uncertain", "finalize-lock", format!("{error:#}")))?
}

/// Caller holds the target launch gate through trial, reap/session release and
/// confirmation or explicit rollback. data_root is a trusted absolute input.
pub fn begin_existing_update(registry: &PackageInstallRegistry, authority: &PackageStorageAuthority, data_root: &Path,
    package: &Package, app: u16) -> std::result::Result<TrialUpdate, UpdateFailure> {
    let mut prepared = None;
    let initial = registry.with_update_operation(|| {
        let mut random = [0u8;16]; getrandom::fill(&mut random).map_err(|error| anyhow::anyhow!("UPDATE_RANDOM {error}"))?;
        let id: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
        let record = prepare_record(registry, authority, data_root, app, package, &id)?;
        authority.validate_coordinated_cleanup(&context(&record, data_root)?.data, &record.private_before,
            crate::app_data::CoordinatedCleanupPhase::JournalCleared, false)?;
        let raw = serde_json::to_vec(&record)?;
        prepared = Some((record, raw.clone()));
        let identity = create_outer(registry, &raw, "outer")?;
        Ok(identity)
    });
    if let Err(error) = initial {
        return Err(if let Some((_, raw)) = prepared {
            if registry.read_coordinated_marker(UPDATE_PENDING_NAME, MAX_UPDATE_RECORD_BYTES as u64).is_ok_and(|raw| raw.is_none()) {
                failure("rejected", "prepare", format!("{error:#}"))
            } else { block_failure(registry, &raw, "outer-prepare", format!("{error:#}")) }
        } else { failure("rejected", "preflight", format!("{error:#}")) });
    }
    let (record, raw) = prepared.expect("prepared before outer creation");
    let publish = context(&record, data_root).and_then(|context| registry.publish_coordinated_update(package, authority, &context))
        .and_then(|_| checkpoint("trial-published"))
        .and_then(|_| registry.with_update_operation(|| {
            let observation = observe_trial_locked(registry, authority, data_root, &record)?;
            let (catalog, grants) = load_catalog_with_install_registry(&registry.root_path().join(persistent_install::CATALOG_NAME), registry)?;
            ensure!(grants.is_empty(), "UPDATE_CATALOG_GRANTS"); Ok((catalog, observation))
        }));
    match publish {
        Ok((catalog, observation)) => Ok(TrialUpdate { record, catalog, observation }),
        Err(error) => match recover_loaded_update(registry, authority, data_root, &record.transaction_id) {
            Ok(UpdateRecovery::RolledBack(_)) => Err(failure("rolled-back", "publish", format!("{error:#}"))),
            Ok(UpdateRecovery::Committed(_)) => Err(block_failure(registry, &raw, "publish-decision-changed", format!("{error:#}"))),
            Err(recovery) => Err(UpdateFailure { error: format!("{error:#}; recovery: {recovery}"), ..recovery }),
        },
    }
}

fn replace_decision(registry: &PackageInstallRegistry, old: &[u8], identity: MarkerIdentity, record: &UpdateRecord) -> Result<(Vec<u8>, MarkerIdentity)> {
    exact_marker(registry, old, identity)?;
    let raw = serde_json::to_vec(record)?;
    let temporary = registry.root_path().join(format!(".ming-update-decision-{}.tmp", record.transaction_id));
    let mut temporary_identity = None;
    let result = (|| {
        let mut file = step("decision-create", || Ok(OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(&temporary)?))?;
        temporary_identity = Some(marker_identity(&temporary)?);
        checkpoint("decision-created")?;
        step("decision-write", || { file.write_all(&raw)?; Ok(()) })?;
        step("decision-file-sync", || { file.sync_all()?; Ok(()) })?;
        step("decision-rename", || { exact_marker(registry, old, identity)?; fs::rename(&temporary, registry.root_path().join(UPDATE_PENDING_NAME))?; Ok(()) })?;
        temporary_identity = None;
        checkpoint("decision-renamed")?;
        step("decision-directory-sync", || { File::open(registry.root_path())?.sync_all()?; Ok(()) })?;
        let new_identity = marker_identity(&registry.root_path().join(UPDATE_PENDING_NAME))?;
        step("decision-readback", || exact_marker(registry, &raw, new_identity))?;
        checkpoint("decision-durable")?;
        Ok((raw, new_identity))
    })();
    if result.is_err() {
        if let Some(expected) = temporary_identity {
            ensure!(marker_identity(&temporary)? == expected, "UPDATE_TEMP_REPLACED");
            fs::remove_file(&temporary)?;
            File::open(registry.root_path())?.sync_all()?;
        }
    }
    result
}

pub fn confirm_existing_update(registry: &PackageInstallRegistry, authority: &PackageStorageAuthority, data_root: &Path,
    proof: &UpdateLifecycleProof) -> std::result::Result<UpdateCommit, UpdateFailure> {
    let mut expected = None;
    let decision = registry.with_update_operation(|| {
        let (mut record, raw, identity) = load_bound(registry, authority, data_root)?;
        ensure!(record.decision == UpdateDecision::Prepared, "UPDATE_CONFIRM_ALREADY_DECIDED");
        let inner = registry.read_coordinated_marker(INNER_JOURNAL_NAME, 16*1024)?.context("UPDATE_CONFIRM_INNER_MISSING")?;
        ensure!(validate_inner(&record, Some(&inner))?.is_some_and(|journal| journal.phase == "startupAttempted"), "UPDATE_CONFIRM_PHASE");
        record.confirmation = Some(proof.clone()); record.decision = UpdateDecision::CommitDecided;
        record.private_confirmed = Some(authority.observe_coordinated_update(&context(&record, data_root)?.data)?);
        record.validate()?;
        let data = context(&record, data_root)?.data;
        authority.validate_coordinated_cleanup(&data, record.private_confirmed.as_ref().unwrap(), crate::app_data::CoordinatedCleanupPhase::BeforeConfirmation,
            record.backup_expected || record.new.migration_sha256 != sha256(b"null"))?;
        expected = Some((raw.clone(), serde_json::to_vec(&record)?));
        let (new_raw, new_identity) = replace_decision(registry, &raw, identity, &record)?;
        load_bound(registry, authority, data_root)?;
        Ok((record, new_raw, new_identity))
    });
    let (record, raw, identity) = match decision {
        Ok(value) => value,
        Err(error) => {
            let Some((old, new)) = expected else { return Err(failure("rejected", "confirmation", format!("{error:#}"))); };
            let actual = registry.read_coordinated_marker(UPDATE_PENDING_NAME, MAX_UPDATE_RECORD_BYTES as u64);
            let bound = if actual.as_ref().is_ok_and(|actual| actual.as_deref() == Some(new.as_slice())) { &new } else { &old };
            return Err(block_failure(registry, bound, "decision", format!("{error:#}")));
        }
    };
    if let Err(error) = registry.finish_coordinated_update(authority, &context(&record, data_root).map_err(|error| failure("fatal", "context", error))?, record.private_confirmed.as_ref().unwrap()) {
        return Err(block_failure(registry, &raw, "finish", format!("{error:#}")));
    }
    let content = finalize(registry, authority, data_root, &record, &raw, identity)?;
    Ok(UpdateCommit { record, content })
}

pub(crate) fn recover_loaded_update(registry: &PackageInstallRegistry, authority: &PackageStorageAuthority, data_root: &Path,
    expected_transaction_id: &str) -> std::result::Result<UpdateRecovery, UpdateFailure> {
    let (record, raw, identity) = registry.with_update_operation(|| {
        load_recovery_bound(registry, authority, data_root, expected_transaction_id)
    }).map_err(|error| failure("fatal", "recovery-preflight", format!("{error:#}")))?;
    checkpoint("recovery-bound").map_err(|error| failure("fatal", "recovery-bound", error))?;
    let context = context(&record, data_root).map_err(|error| failure("fatal", "recovery-context", error))?;
    let result = match record.decision {
        UpdateDecision::Prepared => registry.rollback_coordinated_update(authority, &context),
        UpdateDecision::CommitDecided => registry.finish_coordinated_update(authority, &context, record.private_confirmed.as_ref().unwrap()),
    };
    if let Err(error) = result { return Err(block_failure(registry, &raw, "recovery-mutation", format!("{error:#}"))); }
    let content = finalize(registry, authority, data_root, &record, &raw, identity)?;
    Ok(match record.decision { UpdateDecision::Prepared => UpdateRecovery::RolledBack(content), UpdateDecision::CommitDecided => UpdateRecovery::Committed(content) })
}

pub fn recover_existing_update(root: &Path, data_root: &Path) -> std::result::Result<UpdateRecovery, UpdateFailure> {
    // The fixed order is registry/trust, then the already-existing data authority.
    let registry = PackageInstallRegistry::open_verified_existing(root).map_err(|error| failure("fatal", "recovery-registry", error))?;
    let authority = PackageStorageAuthority::open_update_recovery_existing(data_root).map_err(|error| failure("fatal", "recovery-authority", error))?;
    let raw = registry.read_coordinated_marker(UPDATE_PENDING_NAME, MAX_UPDATE_RECORD_BYTES as u64).map_err(|error| failure("fatal", "recovery-outer", error))?;
    let Some(raw) = raw else {
        return persistent_install::snapshot(&registry).map(UpdateRecovery::Committed).map_err(|error| failure("fatal", "recovery-strict-open", error));
    };
    let record = parse_update_record(&raw).map_err(|error| failure("fatal", "recovery-record", error))?;
    recover_loaded_update(&registry, &authority, data_root, &record.transaction_id)
}

#[cfg(test)]
mod recovery_tests {
    use super::*;
    use crate::app_data::{AppDataRoot, PackageStorageAuthority};
    use base64::{Engine as _, engine::general_purpose::STANDARD};
    use ed25519_dalek::{Signer, SigningKey};
    use std::{os::unix::fs::PermissionsExt, sync::Arc};

    fn write(path: &Path, bytes: &[u8]) { fs::write(path, bytes).unwrap(); fs::set_permissions(path, fs::Permissions::from_mode(0o600)).unwrap(); }
    fn directory(path: &Path) { fs::create_dir_all(path).unwrap(); fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap(); }
    fn fixture(layout: u8, migration: bool, existing: bool) -> (tempfile::TempDir, PackageInstallRegistry, Arc<PackageStorageAuthority>, PathBuf, Package) {
        let (temp, opened, old) = persistent_install::transaction_tests::fixture();
        persistent_install::install_new(&opened.registry, &old).unwrap();
        let data_root = temp.path().join("data"); directory(&data_root);
        let authority = Arc::new(PackageStorageAuthority::open_default_private(AppDataRoot::open(&data_root).unwrap()).unwrap());
        let namespace = data_root.join(crate::app_data::package_directory_name(&old.manifest.id));
        if layout >= 1 { directory(&namespace); }
        if layout >= 2 { directory(&namespace.join("files")); }
        if layout >= 3 { let session = authority.default_private_session_for_package(&old).unwrap(); session.write_file("note", b"alpha").unwrap(); session.write_file("second", b"beta").unwrap(); }
        let mut new = old; new.manifest.version = "2.0.0".into();
        if migration { new.manifest.data_migration = Some(crate::package::DataMigration { from_version: "1.0.0".into(), steps: vec![crate::package::MigrationStep::EnsureDir { path: "state".into() }] }); }
        new.manifest.publisher_signature = None;
        new.manifest.publisher_signature = Some(STANDARD.encode(SigningKey::from_bytes(&[7; 32]).sign(&new.signature_message("mingos-dev-2026").unwrap()).to_bytes()));
        if existing {
            let identity = identity_from_package(&new, &serde_json::to_vec(&new.manifest).unwrap()).unwrap();
            let path = candidate_directory(opened.registry.root_path(), &identity); directory(&path);
            write(&path.join("app.json"), &serde_json::to_vec(&new.manifest).unwrap()); write(&path.join("main.wasm"), &new.bytes);
        }
        (temp, opened.registry, authority, data_root, new)
    }
    fn proof(record: &UpdateRecord) -> UpdateLifecycleProof {
        UpdateLifecycleProof { app: record.app, package: record.new.clone(), generation: 1, window: 1, worker_pid: 1,
            input_ready: true, presented_source_sequence: 1, completed: true, result_accepted: true, native_exit_ok: true, reaped: true }
    }
    fn hook(action: impl FnMut(&str) -> Result<()> + 'static) { crate::app_data::UPDATE_TEST_HOOK.with(|slot| *slot.borrow_mut() = Some(Box::new(action))); }
    fn clear_hook() { crate::app_data::UPDATE_TEST_HOOK.with(|slot| *slot.borrow_mut() = None); }
    fn tree(root: &Path) -> BTreeMap<PathBuf, (u32, u64, Vec<u8>)> {
        fn visit(root: &Path, path: &Path, result: &mut BTreeMap<PathBuf, (u32,u64,Vec<u8>)>) {
            let info = fs::symlink_metadata(path).unwrap();
            let bytes = if info.is_file() { fs::read(path).unwrap() } else if info.file_type().is_symlink() { fs::read_link(path).unwrap().as_os_str().as_encoded_bytes().to_vec() } else { Vec::new() };
            result.insert(path.strip_prefix(root).unwrap().into(), (info.mode(), info.nlink(), bytes));
            if info.is_dir() { for child in fs::read_dir(path).unwrap() { visit(root, &child.unwrap().path(), result); } }
        }
        let mut result = BTreeMap::new(); visit(root, root, &mut result); result
    }
    #[test]
    fn persistent_update_preserves_catalog_and_all_old_content() {
        for layout in 0..4 { for migration in [false, true] { for existing in [false, true] {
            let (_temp, registry, authority, data_root, new) = fixture(layout, migration, existing);
            let old = persistent_install::snapshot(&registry).unwrap();
            let before = authority.observe_update(&new.manifest.id).unwrap();
            let trial = begin_existing_update(&registry, &authority, &data_root, &new, 2).unwrap();
            assert_eq!(trial.record.private_before, before);
            let outer=registry.read_coordinated_marker(UPDATE_PENDING_NAME,MAX_UPDATE_RECORD_BYTES as u64).unwrap().unwrap();
            let inner=registry.read_coordinated_marker(INNER_JOURNAL_NAME,16*1024).unwrap().unwrap();
            assert_eq!(trial.observation.schema_version,1);
            assert_eq!(trial.observation.transaction_id,trial.record.transaction_id);
            assert_eq!(trial.observation.inner_phase,"recordPublished");
            assert_eq!(trial.observation.outer_record_sha256,sha256(&outer));
            assert_eq!(trial.observation.inner_journal_sha256,sha256(&inner));
            assert_eq!(STANDARD.decode(&trial.observation.outer_record_base64).unwrap(),outer);
            assert_eq!(STANDARD.decode(&trial.observation.inner_journal_base64).unwrap(),inner);
            assert_eq!(trial.observation.content,validate_transaction_snapshot(&registry,&authority,&data_root,&trial.record,&outer,Some(&inner)).unwrap());
            assert_eq!(trial.observation.private_content,authority.observe_coordinated_update(&context(&trial.record,&data_root).unwrap().data).unwrap());
            assert_eq!(trial.observation.private_content.non_files_sha256,before.non_files_sha256);
            if migration { assert_ne!(trial.observation.private_content.files,before.files); }
            assert_eq!(trial.catalog.apps.iter().find(|entry| entry.id == 2).unwrap().package_id, new.manifest.id);
            assert!(persistent_install::snapshot(&registry).is_err());
            registry.begin_update_startup(&new.manifest.id, new.storage_fingerprint()).unwrap();
            let committed = confirm_existing_update(&registry, &authority, &data_root, &proof(&trial.record)).unwrap();
            assert_eq!(committed.content.catalog_sha256, old.catalog_sha256);
            for file in old.files.iter().filter(|file| file.path.starts_with("packages/")) {
                assert!(committed.content.files.iter().any(|current| current == file));
            }
            assert_eq!(committed.content.apps.iter().find(|entry| entry.app == 2).unwrap().version, "2.0.0");
            assert!(!registry.root_path().join(UPDATE_PENDING_NAME).exists());
        } } }
    }
    #[test]
    fn trial_observation_rejects_missing_wrong_or_advanced_inner_without_writes() {
        for mutation in ["missing","identity","phase"] {
            let (_temp,registry,authority,data,new)=fixture(3,true,false);
            let trial=begin_existing_update(&registry,&authority,&data,&new,2).unwrap();
            let journal=registry.root_path().join(INNER_JOURNAL_NAME);
            let mut inner:serde_json::Value=serde_json::from_slice(&fs::read(&journal).unwrap()).unwrap();
            match mutation {
                "missing"=>fs::remove_file(&journal).unwrap(),
                "identity"=>{inner["newFingerprint"]=serde_json::json!("0".repeat(64));write(&journal,&serde_json::to_vec(&inner).unwrap());},
                "phase"=>{inner["phase"]=serde_json::json!("startupAttempted");write(&journal,&serde_json::to_vec(&inner).unwrap());},
                _=>unreachable!(),
            }
            let before=tree(_temp.path());
            assert!(registry.with_update_operation(||observe_trial_locked(&registry,&authority,&data,&trial.record)).is_err(),"{mutation}");
            assert_eq!(tree(_temp.path()),before,"observer wrote on {mutation}");
        }
    }
    #[test]
    fn trial_observation_rejects_markers_content_and_private_tree_changed_during_read() {
        for mutation in ["outer-replaced","inner-replaced","catalog","private"] {
            let (_temp,registry,authority,data,new)=fixture(3,true,false);
            let trial=begin_existing_update(&registry,&authority,&data,&new,2).unwrap();
            let root=registry.root_path().to_owned();let data_copy=data.clone();let package_id=new.manifest.id.clone();
            hook(move |stage| {
                if stage=="trial-observed" {
                    match mutation {
                        "outer-replaced"|"inner-replaced"=>{
                            let path=root.join(if mutation=="outer-replaced"{UPDATE_PENDING_NAME}else{INNER_JOURNAL_NAME});
                            let replacement=root.join("marker-replacement");write(&replacement,&fs::read(&path).unwrap());fs::rename(replacement,path)?;
                        },
                        "catalog"=>write(&root.join("catalog.json"),b"{}"),
                        "private"=>write(&data_copy.join(crate::app_data::package_directory_name(&package_id)).join("files/note"),b"changed"),
                        _=>unreachable!(),
                    }
                }
                Ok(())
            });
            let result=registry.with_update_operation(||observe_trial_locked(&registry,&authority,&data,&trial.record));clear_hook();
            assert!(result.is_err(),"{mutation}");
        }
    }
    #[test]
    fn persistent_update_commit_is_after_complete_proof() {
        let (_temp, registry, authority, data_root, new) = fixture(3, true, false);
        let trial = begin_existing_update(&registry, &authority, &data_root, &new, 2).unwrap();
        registry.begin_update_startup(&new.manifest.id, new.storage_fingerprint()).unwrap();
        let raw = fs::read(registry.root_path().join(UPDATE_PENDING_NAME)).unwrap();
        for field in ["app", "package", "generation", "window", "workerPid", "inputReady", "presentedSourceSequence",
            "completed", "resultAccepted", "nativeExitOk", "reaped"] {
            let mut value = serde_json::to_value(proof(&trial.record)).unwrap();
            value[field] = match field {
                "package" => { let mut package = value[field].clone(); package["fingerprint"] = serde_json::json!("1".repeat(64)); package },
                "inputReady"|"completed"|"resultAccepted"|"nativeExitOk"|"reaped" => serde_json::json!(false),
                _ => serde_json::json!(0),
            };
            let incomplete = serde_json::from_value(value).unwrap();
            assert!(confirm_existing_update(&registry, &authority, &data_root, &incomplete).is_err(), "{field}");
            assert_eq!(fs::read(registry.root_path().join(UPDATE_PENDING_NAME)).unwrap(), raw);
        }
        recover_loaded_update(&registry, &authority, &data_root, &trial.record.transaction_id).unwrap();
        assert_eq!(persistent_install::snapshot(&registry).unwrap().apps.iter().find(|app| app.app == 2).unwrap().version, "1.0.0");
    }
    #[test]
    fn persistent_update_rollback_preserves_missing_layout() {
        for layout in 0..4 { for migration in [false, true] {
            let (_temp, registry, authority, data_root, new) = fixture(layout, migration, false);
            let before = authority.observe_update(&new.manifest.id).unwrap();
            let trial = begin_existing_update(&registry, &authority, &data_root, &new, 2).unwrap();
            registry.begin_update_startup(&new.manifest.id, new.storage_fingerprint()).unwrap();
            { let session = authority.default_private_session_for_package(&new).unwrap(); session.write_file("trial", b"nonempty trial data").unwrap(); }
            let recovered = recover_loaded_update(&registry, &authority, &data_root, &trial.record.transaction_id).unwrap();
            assert!(matches!(recovered, UpdateRecovery::RolledBack(_)));
            assert_eq!(authority.observe_update(&new.manifest.id).unwrap(), before);
            assert_eq!(persistent_install::snapshot(&registry).unwrap().apps.iter().find(|app| app.app == 2).unwrap().version, "1.0.0");
        } }
    }

    #[test]
    fn outer_gate_blocks_all_ordinary_mutations_in_both_marker_gaps() {
        for cleared_inner in [false, true] {
            let (_temp, registry, authority, data_root, new) = fixture(3, false, false);
            let record = prepare_record(&registry, &authority, &data_root, 2, &new, "gate-test").unwrap();
            registry.with_update_operation(|| { create_outer(&registry, &serde_json::to_vec(&record)?, "outer")?; Ok(()) }).unwrap();
            if cleared_inner { registry.publish_coordinated_update(&new, &authority, &context(&record, &data_root).unwrap()).unwrap(); registry.rollback_coordinated_update(&authority, &context(&record, &data_root).unwrap()).unwrap(); }
            let before = tree(registry.root_path()); let data = tree(&data_root);
            let old = load_package(&registry.active_manifest_path(&new.manifest.id).unwrap()).unwrap();
            let rollback = registry.prepare_install_rollback(&old.manifest.id).unwrap();
            assert!(registry.install_package(&new).is_err()); assert!(registry.install_loaded_package(&new).is_err());
            assert!(registry.rollback_install(rollback, &old.manifest.version, old.storage_fingerprint()).is_err());
            assert!(registry.restore_records_snapshot(b"ming-installed-packages-v1\n").is_err());
            assert!(registry.update_loaded_package(&new, &authority).is_err());
            assert!(registry.recover_pending_update(&authority).is_err());
            assert!(registry.rollback_pending_update(&authority).is_err());
            assert!(registry.confirm_pending_update(&authority, &new.manifest.id, &new.manifest.version, new.storage_fingerprint()).is_err());
            assert!(registry.uninstall(&new.manifest.id).is_err());
            assert!(persistent_install::uninstall_existing(&registry, &old).is_err());
            assert_eq!(tree(registry.root_path()), before); assert_eq!(tree(&data_root), data);
            recover_loaded_update(&registry, &authority, &data_root, &record.transaction_id).unwrap();
        }
    }

    #[test]
    fn concurrent_stale_recovery_never_recreates_a_completed_outer() {
        let (_temp, registry, authority, data_root, new) = fixture(3, false, false);
        let trial = begin_existing_update(&registry, &authority, &data_root, &new, 2).unwrap();
        let bound = Arc::new(std::sync::Barrier::new(2));
        let resume = Arc::new(std::sync::Barrier::new(2));
        std::thread::scope(|scope| {
            let (bound_child, resume_child) = (bound.clone(), resume.clone());
            let registry_ref = &registry; let authority_ref = &authority;
            let data_ref = &data_root; let transaction = &trial.record.transaction_id;
            let stale = scope.spawn(move || {
                hook(move |stage| { if stage == "recovery-bound" { bound_child.wait(); resume_child.wait(); } Ok(()) });
                let result = recover_loaded_update(registry_ref, authority_ref, data_ref, transaction);
                clear_hook(); result
            });
            bound.wait();
            assert!(matches!(recover_loaded_update(&registry, &authority, &data_root, transaction).unwrap(), UpdateRecovery::RolledBack(_)));
            let old = load_package(&registry.active_manifest_path(&new.manifest.id).unwrap()).unwrap();
            persistent_install::uninstall_existing(&registry, &old).unwrap();
            let stable = persistent_install::snapshot(&registry).unwrap();
            let registry_before = tree(registry.root_path()); let data_before = tree(&data_root);
            resume.wait();
            let rejected = stale.join().unwrap().unwrap_err();
            assert!(!rejected.compensation_attempted);
            assert!(!registry.root_path().join(UPDATE_PENDING_NAME).exists());
            assert_eq!(tree(registry.root_path()), registry_before); assert_eq!(tree(&data_root), data_before);
            assert_eq!(persistent_install::snapshot(&registry).unwrap(), stable);
        });
    }

    #[test]
    fn active_private_session_rejects_begin_before_creating_outer() {
        let (_temp, registry, authority, data_root, new) = fixture(3, false, false);
        let old = load_package(&registry.active_manifest_path(&new.manifest.id).unwrap()).unwrap();
        let session = authority.default_private_session_for_package(&old).unwrap();
        let before = tree(registry.root_path()); let data_before = tree(&data_root);
        let rejected = begin_existing_update(&registry, &authority, &data_root, &new, 2).unwrap_err();
        assert_eq!(rejected.status, "rejected");
        assert_eq!(tree(registry.root_path()), before); assert_eq!(tree(&data_root), data_before);
        drop(session);
        assert!(begin_existing_update(&registry, &authority, &data_root, &new, 2).is_ok());
    }

    #[test]
    fn commit_and_rollback_preserve_live_sibling_public_bytes_and_old_fingerprint_grant() {
        for committed in [false,true] {
            let (_temp, registry, authority, data_root, new) = fixture(3, true, false);
            let old = load_package(&registry.active_manifest_path(&new.manifest.id).unwrap()).unwrap();
            let sibling = authority.default_private_session("dev.ming.sibling",[9;32]).unwrap();
            sibling.write_file("sentinel",b"private sibling sentinel").unwrap();
            authority.authorize_public_bound(&old.manifest.id,old.storage_fingerprint()).unwrap();
            { let public = authority.public_session_bound(&old.manifest.id,old.storage_fingerprint()).unwrap();
                public.write_file("sentinel",b"public sentinel").unwrap(); }
            let sibling_root = data_root.join(crate::app_data::package_directory_name("dev.ming.sibling"));
            let public_root = data_root.join("public-files-v1");
            let policy_root = data_root.join("system-public-storage-policy-v1");
            let (sibling_before,public_before,policy_before) = (tree(&sibling_root),tree(&public_root),tree(&policy_root));
            let trial = begin_existing_update(&registry,&authority,&data_root,&new,2).unwrap();
            registry.begin_update_startup(&new.manifest.id,new.storage_fingerprint()).unwrap();
            { let session = authority.default_private_session_for_package(&new).unwrap(); session.write_file("trial",b"updated target").unwrap(); }
            if committed { confirm_existing_update(&registry,&authority,&data_root,&proof(&trial.record)).unwrap(); }
            else { recover_loaded_update(&registry,&authority,&data_root,&trial.record.transaction_id).unwrap(); }
            assert_eq!(tree(&sibling_root),sibling_before); assert_eq!(tree(&public_root),public_before); assert_eq!(tree(&policy_root),policy_before);
            assert!(authority.public_session_bound(&new.manifest.id,new.storage_fingerprint()).is_err());
            assert!(authority.public_session_bound(&old.manifest.id,old.storage_fingerprint()).is_ok());
            assert_eq!(sibling.read_file("sentinel").unwrap(),b"private sibling sentinel");
        }
    }

    #[test]
    fn complete_outer_inner_recovery_state_table() {
        for decision in ["absent", "prepared", "commit-decided"] {
            for phase in [None, Some("planned"), Some("dataSwapped"), Some("recordPublished"), Some("startupAttempted"), Some("startupConfirmed"), Some("rollingBack")] {
                let (_temp, registry, authority, data_root, new) = fixture(0, false, false);
                let record = prepare_record(&registry, &authority, &data_root, 2, &new, "matrix").unwrap();
                create_outer(&registry, &serde_json::to_vec(&record).unwrap(), "outer").unwrap();
                registry.publish_coordinated_update(&new, &authority, &context(&record, &data_root).unwrap()).unwrap();
                // Missing-layout/no-migration has no business-tree copies, so
                // each phase can be examined without synthesizing private data.
                if decision == "prepared" && matches!(phase, None | Some("planned")) {
                    registry.rollback_coordinated_update(&authority, &context(&record, &data_root).unwrap()).unwrap();
                }
                let mut inner = serde_json::json!({"packageId":record.old.package_id,"oldVersion":record.old.version,"oldFingerprint":record.old.fingerprint,
                    "newVersion":record.new.version,"newFingerprint":record.new.fingerprint,"backupName":format!(".ming-update-backup-{}",record.transaction_id),"migrationDeclared":false,"phase":"planned"});
                if let Some(phase) = phase { inner["phase"] = serde_json::json!(phase); write(&registry.root_path().join(INNER_JOURNAL_NAME), &serde_json::to_vec(&inner).unwrap()); }
                else { let _ = fs::remove_file(registry.root_path().join(INNER_JOURNAL_NAME)); }
                let mut outer = record.clone();
                if decision == "commit-decided" {
                    outer.decision = UpdateDecision::CommitDecided; outer.confirmation = Some(proof(&record)); outer.private_confirmed = Some(record.private_before.clone());
                    write(&registry.root_path().join(UPDATE_PENDING_NAME), &serde_json::to_vec(&outer).unwrap());
                } else if decision == "absent" { fs::remove_file(registry.root_path().join(UPDATE_PENDING_NAME)).unwrap(); }
                let before = tree(registry.root_path()); let data = tree(&data_root);
                let expected = match decision { "absent" => phase.is_none(), "prepared" => phase != Some("startupConfirmed"), _ => matches!(phase, None | Some("startupAttempted"|"startupConfirmed")) };
                let root = registry.root_path().to_path_buf(); drop(registry); drop(authority);
                let result = recover_existing_update(&root, &data_root);
                assert_eq!(result.is_ok(), expected, "{decision}/{phase:?}: {result:?}");
                if !expected { assert_eq!(tree(&root), before); assert_eq!(tree(&data_root), data); }
                else { assert!(!root.join(UPDATE_PENDING_NAME).exists()); assert!(!root.join(INNER_JOURNAL_NAME).exists()); }
                println!("UPDATE_CASE {}", serde_json::json!({"kind":"state-table","decision":decision,"phase":phase,"accepted":result.is_ok()}));
            }
        }
    }

    #[test]
    fn deterministic_fault_matrix_preserves_decisions_and_reports_compensation() {
        let stages = ["outer-create","outer-created","outer-write","outer-written","outer-file-sync","outer-directory-sync","outer-readback","outer-durable",
            "candidate-created","candidate-manifest-written","candidate-wasm-written","candidate-directory-synced","candidate-renamed","candidate-parent-synced",
            "candidate-staged","inner-planned","data-rename","inner-data-swapped","registry-record-directory-sync","record-published","inner-record-published","trial-published",
            "decision-create","decision-created","decision-write","decision-file-sync","decision-rename","decision-renamed","decision-directory-sync","decision-readback","decision-durable",
            "inner-confirmed","data-unlink","outer-unlink","outer-unlinked","outer-remove-directory-sync","final-strict-readback"];
        for stage in stages {
            let (_temp, registry, authority, data_root, new) = fixture(3, true, false);
            let original = authority.observe_update(&new.manifest.id).unwrap();
            let armed = std::rc::Rc::new(std::cell::Cell::new(false)); let fired = armed.clone();
            hook(move |seen| { if seen == stage && !fired.replace(true) { anyhow::bail!("injected {stage}"); } Ok(()) });
            let operation = begin_existing_update(&registry, &authority, &data_root, &new, 2).and_then(|trial| {
                registry.begin_update_startup(&new.manifest.id, new.storage_fingerprint()).unwrap();
                confirm_existing_update(&registry, &authority, &data_root, &proof(&trial.record)).map(|_| ())
            });
            clear_hook();
            assert!(armed.get(), "unreached {stage}"); assert!(operation.is_err(), "{stage}");
            let failure = operation.unwrap_err();
            let root = registry.root_path().to_path_buf();
            let raw = registry.read_coordinated_marker(UPDATE_PENDING_NAME, MAX_UPDATE_RECORD_BYTES as u64).unwrap();
            let committed = raw.as_deref().and_then(|raw| parse_update_record(raw).ok()).is_some_and(|record| record.decision == UpdateDecision::CommitDecided);
            drop(registry); drop(authority);
            let recovered = recover_existing_update(&root, &data_root);
            let incomplete = matches!(stage, "outer-created" | "outer-write");
            assert_eq!(recovered.is_err(), incomplete, "unexpected recovery classification at {stage}: {recovered:?}");
            let publish_rolled_back = stage.starts_with("candidate-") || matches!(stage, "inner-planned"|"data-rename"|"inner-data-swapped"|"registry-record-directory-sync"|"record-published"|"inner-record-published"|"trial-published");
            let removed = matches!(stage, "outer-unlinked"|"outer-remove-directory-sync"|"final-strict-readback");
            assert_eq!(failure.status, if incomplete { "commit-uncertain" } else if stage == "outer-create" { "rejected" }
                else if publish_rolled_back { "rolled-back" } else if committed { "commit-pending" } else { "fatal" }, "{stage}");
            assert_eq!(failure.persistent_block_confirmed, !incomplete && stage != "outer-create" && !publish_rolled_back, "{stage}");
            assert_eq!(failure.compensation_attempted, removed, "{stage}");
            if !incomplete {
                let opened = persistent_install::open_existing(&root).unwrap();
                let current = opened.content.apps.iter().find(|app| app.app == 2).unwrap();
                assert_eq!(current.version, if committed { "2.0.0" } else { "1.0.0" }, "{stage}");
                if !committed { assert_eq!(crate::app_data::observe_private_update_read_only(&data_root, &new.manifest.id).unwrap(), original); }
            } else { assert!(persistent_install::open_existing(&root).is_err()); }
            println!("UPDATE_CASE {}", serde_json::json!({"kind":"fault","stage":stage,"failure":failure,"committedDecision":committed,"recovered":recovered.is_ok()}));
        }
    }

    #[test]
    fn failed_outer_cleanup_compensation_reports_uncertainty_without_claiming_a_block() {
        let (_temp, registry, authority, data_root, new) = fixture(3, true, false);
        let trial = begin_existing_update(&registry, &authority, &data_root, &new, 2).unwrap();
        registry.begin_update_startup(&new.manifest.id, new.storage_fingerprint()).unwrap();
        hook(|stage| { if matches!(stage, "outer-remove-directory-sync"|"compensation-create") { anyhow::bail!("injected {stage}"); } Ok(()) });
        let failure = confirm_existing_update(&registry, &authority, &data_root, &proof(&trial.record)).unwrap_err(); clear_hook();
        assert_eq!(failure.status, "commit-uncertain"); assert!(!failure.persistent_block_confirmed); assert!(failure.compensation_attempted);
        assert!(!registry.root_path().join(UPDATE_PENDING_NAME).exists());
        assert_eq!(persistent_install::snapshot(&registry).unwrap().apps.iter().find(|app| app.app==2).unwrap().version, "2.0.0");
    }

    #[test]
    fn every_compensation_failure_preserves_the_commit_choice_without_claiming_durability() {
        for target in ["compensation-create","compensation-created","compensation-write","compensation-written",
            "compensation-file-sync","compensation-directory-sync","compensation-readback","compensation-durable"] {
            let (_temp,registry,authority,data_root,new) = fixture(3,true,false);
            let trial = begin_existing_update(&registry,&authority,&data_root,&new,2).unwrap();
            registry.begin_update_startup(&new.manifest.id,new.storage_fingerprint()).unwrap();
            let fired = std::rc::Rc::new(std::cell::Cell::new(false)); let seen = fired.clone();
            hook(move |stage| {
                if stage == target { seen.set(true); anyhow::bail!("injected {stage}"); }
                if stage == "outer-remove-directory-sync" { anyhow::bail!("injected cleanup sync"); }
                Ok(())
            });
            let error = confirm_existing_update(&registry,&authority,&data_root,&proof(&trial.record)).unwrap_err(); clear_hook();
            assert!(fired.get(),"{target}"); assert_eq!(error.status,"commit-uncertain");
            assert!(error.compensation_attempted); assert!(!error.persistent_block_confirmed);
            let root = registry.root_path().to_path_buf();
            let data_before = crate::app_data::observe_private_update_read_only(&data_root,&new.manifest.id).unwrap();
            let incomplete = matches!(target,"compensation-created"|"compensation-write");
            drop(registry); drop(authority);
            let recovered = recover_existing_update(&root,&data_root);
            assert_eq!(recovered.is_err(),incomplete,"{target}: {recovered:?}");
            if !incomplete { assert_eq!(persistent_install::open_existing(&root).unwrap().content.apps.iter().find(|app|app.app==2).unwrap().version,"2.0.0"); }
            else { assert!(persistent_install::open_existing(&root).is_err()); }
            assert_eq!(crate::app_data::observe_private_update_read_only(&data_root,&new.manifest.id).unwrap(),data_before);
            println!("UPDATE_CASE {}",serde_json::json!({"kind":"compensation-fault","stage":target,"failure":error,"safeRejection":incomplete}));
        }
    }

    fn child_command(mode: &str, root: &Path, data_root: &Path, source: &Path) -> std::process::Command {
        let mut command = std::process::Command::new(std::env::current_exe().unwrap());
        command.args(["--exact", "persistent_update::recovery_tests::child_process", "--ignored", "--nocapture"])
            .env("MING_UPDATE_CHILD", mode).env("MING_UPDATE_ROOT", root).env("MING_UPDATE_DATA", data_root).env("MING_UPDATE_SOURCE", source);
        command
    }
    fn completed(command: &mut std::process::Command) -> std::process::Output {
        use std::time::{Duration, Instant};
        let mut child = command.stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped()).spawn().unwrap();
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            if child.try_wait().unwrap().is_some() {
                let output = child.wait_with_output().unwrap();
                if output.status.success() { assert!(String::from_utf8_lossy(&output.stdout).contains("1 passed; 0 failed"),
                    "child command did not run exactly one successful test: {}",String::from_utf8_lossy(&output.stdout)); }
                return output;
            }
            if Instant::now() >= deadline { let _ = child.kill(); let _ = child.wait(); panic!("update child timeout"); }
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    fn killed_at(command: &mut std::process::Command, stage: &str, occurrence: usize) {
        use std::{io::Read, os::{fd::AsRawFd, unix::process::ExitStatusExt}, time::{Duration, Instant}};
        let mut child = command.env("MING_UPDATE_STOP", stage).env("MING_UPDATE_STOP_OCCURRENCE", occurrence.to_string())
            .stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped()).spawn().unwrap();
        let deadline = Instant::now() + Duration::from_secs(20);
        let mut captured = Vec::new(); let mut ready = false;
        while Instant::now() < deadline {
            let fd = child.stdout.as_ref().unwrap().as_raw_fd();
            let mut poll = libc::pollfd { fd, events: libc::POLLIN, revents: 0 };
            let count = unsafe { libc::poll(&mut poll, 1, 100) };
            if count > 0 {
                let mut bytes = [0;4096]; let read = child.stdout.as_mut().unwrap().read(&mut bytes).unwrap();
                if read == 0 { break; } captured.extend_from_slice(&bytes[..read]);
                if String::from_utf8_lossy(&captured).contains("UPDATE_READY ") { ready = true; break; }
            }
            if child.try_wait().unwrap().is_some() { break; }
        }
        if !ready {
            let _ = child.kill(); let output = child.wait_with_output().unwrap();
            panic!("checkpoint {stage}/{occurrence} not reached: {} {}", String::from_utf8_lossy(&captured), String::from_utf8_lossy(&output.stderr));
        }
        child.kill().unwrap(); let status = child.wait().unwrap(); assert_eq!(status.signal(), Some(libc::SIGKILL));
        println!("UPDATE_KILLED {}", serde_json::json!({"stage":stage,"occurrence":occurrence,"signal":status.signal(),"reaped":true}));
    }
    #[test]
    #[ignore = "Only invoked by the bounded parent SIGKILL matrix"]
    fn child_process() {
        let mode = std::env::var("MING_UPDATE_CHILD").expect("parent-only child entry");
        let root = PathBuf::from(std::env::var_os("MING_UPDATE_ROOT").unwrap());
        let data_root = PathBuf::from(std::env::var_os("MING_UPDATE_DATA").unwrap());
        if let Ok(target) = std::env::var("MING_UPDATE_STOP") {
            let expected: usize = std::env::var("MING_UPDATE_STOP_OCCURRENCE").unwrap().parse().unwrap(); let mut count = 0;
            hook(move |stage| {
                if stage == target { count += 1; if count == expected {
                    println!("UPDATE_READY {}", serde_json::json!({"stage":stage,"occurrence":count,"pid":std::process::id()})); std::io::stdout().flush().unwrap();
                    loop { unsafe { libc::pause(); } }
                } } Ok(())
            });
        }
        let blocked = std::env::var_os("MING_UPDATE_EXPECT_BLOCKED").is_some();
        match mode.as_str() {
            "update" => {
                let registry = PackageInstallRegistry::open_verified_existing(&root).unwrap();
                let authority = Arc::new(PackageStorageAuthority::open_update_recovery_existing(&data_root).unwrap());
                let package = load_package(&PathBuf::from(std::env::var_os("MING_UPDATE_SOURCE").unwrap()).join("app.json")).unwrap();
                let trial = begin_existing_update(&registry, &authority, &data_root, &package, 2).unwrap();
                registry.begin_update_startup(&package.manifest.id, package.storage_fingerprint()).unwrap();
                { let session = authority.default_private_session_for_package(&package).unwrap(); session.write_file("trial-a", b"nonempty trial a").unwrap(); session.write_file("trial-b", b"nonempty trial b").unwrap(); }
                checkpoint("trial-started").unwrap();
                confirm_existing_update(&registry, &authority, &data_root, &proof(&trial.record)).unwrap();
            }
            "recover" => {
                let recovered = recover_existing_update(&root, &data_root);
                assert_eq!(recovered.is_err(), blocked, "{recovered:?}");
                println!("UPDATE_RECOVERED {}", serde_json::json!({"success":recovered.is_ok(),"error":recovered.err().map(|error| error.to_string())}));
            }
            "open" => {
                let reopened = persistent_install::open_existing(&root);
                assert_eq!(reopened.is_err(), blocked, "{reopened:?}");
                if let Ok(opened) = reopened {
                    let app = opened.content.apps.iter().find(|app|app.app==2).unwrap();
                    assert_eq!(app.version, std::env::var("MING_UPDATE_EXPECT_VERSION").unwrap());
                    println!("UPDATE_REOPENED {}", serde_json::to_string(&opened.content).unwrap());
                }
            }
            _ => panic!("unknown child mode"),
        }
    }

    #[test]
    fn partial_candidate_subsets_recover_without_publishing_partial_bytes() {
        for mask in 0..4 {for partial in [false,true] {
            let (_temp,registry,authority,data,new)=fixture(3,false,false);
            let record=prepare_record(&registry,&authority,&data,2,&new,"partial-candidate").unwrap();
            create_outer(&registry,&serde_json::to_vec(&record).unwrap(),"outer").unwrap();
            let path=candidate_directory(registry.root_path(),&record.new).parent().unwrap().join(format!(".ming-update-candidate-{}.tmp",record.transaction_id));
            directory(&path);
            let manifest=serde_json::to_vec(&new.manifest).unwrap();
            if mask&1!=0 {write(&path.join("app.json"),if partial{&manifest[..manifest.len()/2]}else{&manifest});}
            if mask&2!=0 {write(&path.join("main.wasm"),if partial{&new.bytes[..new.bytes.len()/2]}else{&new.bytes});}
            assert!(persistent_install::snapshot(&registry).is_err());
            assert!(matches!(recover_loaded_update(&registry,&authority,&data,&record.transaction_id).unwrap(),UpdateRecovery::RolledBack(_)));
            assert!(!path.exists());assert!(!candidate_directory(registry.root_path(),&record.new).exists());
            assert_eq!(authority.observe_update(&new.manifest.id).unwrap(),record.private_before);
            println!("UPDATE_CASE {}",serde_json::json!({"kind":"temporary-subset","mask":mask,"partial":partial}));
        }}
    }

    #[test]
    fn temporary_recovery_rejects_unbound_unsafe_or_corrupt_state_without_writing() {
        for case in 0..18 {
            let (_temp,registry,authority,data,new)=fixture(3,false,false);
            let record=if [10,12,14].contains(&case) {
                begin_existing_update(&registry,&authority,&data,&new,2).unwrap().record
            }else{
                let record=prepare_record(&registry,&authority,&data,2,&new,"temporary-rejection").unwrap();
                create_outer(&registry,&serde_json::to_vec(&record).unwrap(),"outer").unwrap();record
            };
            let root=registry.root_path();
            let path=candidate_directory(root,&record.new).parent().unwrap().join(format!(".ming-update-candidate-{}.tmp",record.transaction_id));
            let journal=root.join(format!(".ming-update-journal-{}.tmp",record.transaction_id));
            let records=root.join(format!(".ming-update-record-{}.tmp",record.transaction_id));
            if [11,13].contains(&case) {
                let candidate=candidate_directory(root,&record.new);directory(&candidate);
                write(&candidate.join("app.json"),&serde_json::to_vec(&new.manifest).unwrap());write(&candidate.join("main.wasm"),&new.bytes);
                write(&journal,b"");
            }else if [12,14].contains(&case) {
                let mut inner:serde_json::Value=serde_json::from_slice(&fs::read(root.join(INNER_JOURNAL_NAME)).unwrap()).unwrap();
                inner["phase"]=serde_json::json!("dataSwapped");write(&root.join(INNER_JOURNAL_NAME),&serde_json::to_vec(&inner).unwrap());
                write(&records,&fs::read(root.join("installed-packages-v1")).unwrap());
            }else if case==8 {write(&root.join(format!(".ming-update-decision-{}.tmp",record.transaction_id)),b"");}
            else if case==9 {write(&records,b"");}
            else if case==2 {std::os::unix::fs::symlink(".",&path).unwrap();}
            else {directory(&path);}
            match case {
                0=>fs::rename(&path,path.with_file_name(".ming-update-candidate-other.tmp")).unwrap(),
                1=>write(&path.join("unknown"),b"x"),
                2=>{},
                3=>{write(&path.join("app.json"),b"{");fs::hard_link(path.join("app.json"),root.join("alias")).unwrap();},
                4=>{let fifo=CString::new(path.join("app.json").as_os_str().as_bytes()).unwrap();assert_eq!(unsafe{libc::mkfifo(fifo.as_ptr(),0o600)},0);},
                5=>fs::set_permissions(&path,fs::Permissions::from_mode(0o755)).unwrap(),
                6=>write(&path.join("app.json"),&vec![b'x';64*1024+1]),
                7=>write(&root.join("unexpected"),b"sentinel"),
                8|9|10=>{},
                11=>write(&candidate_directory(root,&record.old).join("main.wasm"),b"invalid old history"),
                12=>write(&data.join(crate::app_data::package_directory_name(&record.old.package_id)).join(format!(".ming-update-backup-{}",record.transaction_id)).join("note"),b"corrupt backup"),
                13=>write(&journal,b"invalid journal temporary"),
                14=>write(&records,b"invalid records temporary"),
                15=>write(&root.join(".ming-update-journal-other.tmp"),b"unbound"),
                16=>write(&root.join(persistent_install::PENDING_NAME),b"{}"),
                17=>{let mut manifest:crate::package::Manifest=serde_json::from_slice(&serde_json::to_vec(&new.manifest).unwrap()).unwrap();manifest.version="3.0.0".into();write(&path.join("app.json"),&serde_json::to_vec(&manifest).unwrap());},
                _=>unreachable!(),
            }
            let before=tree(root);let data_before=tree(&data);
            assert!(recover_loaded_update(&registry,&authority,&data,&record.transaction_id).is_err(),"case {case}");
            assert_eq!(tree(root),before,"case {case}");assert_eq!(tree(&data),data_before,"case {case}");
            println!("UPDATE_CASE {}",serde_json::json!({"kind":"temporary-rejection","case":case}));
        }
    }

    #[test]
    fn real_sigkill_during_temporary_cleanup_and_rolling_back_rewrites() {
        for layout in 0..4 {for migration in [false,true] {
            for initial in ["candidate-wasm-written","registry-journal-file-sync","registry-record-file-sync","decision-created"] {
                let mut stops=vec![("temporary-unlink",1),("temporary-sync",1)];
                if initial=="candidate-wasm-written" {stops.extend([("temporary-unlink",2),("temporary-rmdir",1),("temporary-sync",2),("temporary-sync",3)]);}
                for (stage,occurrence) in stops {
                    let (temp,registry,authority,data,new)=fixture(layout,migration,false);let baseline=authority.observe_update(&new.manifest.id).unwrap();
                    let root=registry.root_path().to_path_buf();let source=temp.path().join("source");directory(&source);
                    write(&source.join("app.json"),&serde_json::to_vec(&new.manifest).unwrap());write(&source.join("main.wasm"),&new.bytes);drop(registry);drop(authority);
                    killed_at(&mut child_command("update",&root,&data,&source),initial,1);
                    killed_at(&mut child_command("recover",&root,&data,&source),stage,occurrence);
                    let recovered=completed(&mut child_command("recover",&root,&data,&source));assert!(recovered.status.success(),"{}",String::from_utf8_lossy(&recovered.stderr));
                    let mut reopen=child_command("open",&root,&data,&source);reopen.env("MING_UPDATE_EXPECT_VERSION","1.0.0");assert!(completed(&mut reopen).status.success());
                    assert_eq!(crate::app_data::observe_private_update_read_only(&data,&new.manifest.id).unwrap(),baseline);
                    println!("UPDATE_CASE {}",serde_json::json!({"kind":"temporary-cleanup-sigkill","layout":layout,"migration":migration,"initial":initial,"stage":stage,"occurrence":occurrence}));
                }
            }
            for stage in ["registry-journal-write","registry-journal-file-sync","registry-record-write","registry-record-file-sync"] {
                let (temp,registry,authority,data,new)=fixture(layout,migration,false);let baseline=authority.observe_update(&new.manifest.id).unwrap();
                let root=registry.root_path().to_path_buf();let source=temp.path().join("source");directory(&source);
                write(&source.join("app.json"),&serde_json::to_vec(&new.manifest).unwrap());write(&source.join("main.wasm"),&new.bytes);drop(registry);drop(authority);
                killed_at(&mut child_command("update",&root,&data,&source),"trial-started",1);
                killed_at(&mut child_command("recover",&root,&data,&source),"inner-rolling-back",1);
                killed_at(&mut child_command("recover",&root,&data,&source),stage,1);
                let recovered=completed(&mut child_command("recover",&root,&data,&source));assert!(recovered.status.success(),"{}",String::from_utf8_lossy(&recovered.stderr));
                let mut reopen=child_command("open",&root,&data,&source);reopen.env("MING_UPDATE_EXPECT_VERSION","1.0.0");assert!(completed(&mut reopen).status.success());
                assert_eq!(crate::app_data::observe_private_update_read_only(&data,&new.manifest.id).unwrap(),baseline);
                println!("UPDATE_CASE {}",serde_json::json!({"kind":"rolling-back-temporary-sigkill","layout":layout,"migration":migration,"stage":stage}));
            }
        }}
    }

    #[test]
    fn real_sigkill_recovery_and_independent_strict_reopen_matrix() {
        let cases = [("outer-created",true,false),("outer-written",false,false),("outer-file-sync",false,false),("outer-durable",false,false),
            ("candidate-created",false,false),("candidate-manifest-written",false,false),("candidate-wasm-written",false,false),("candidate-directory-synced",false,false),
            ("candidate-renamed",false,false),("candidate-parent-synced",false,false),
            ("candidate-staged",false,false),("registry-journal-write",false,false),("registry-journal-file-sync",false,false),
            ("inner-planned",false,false),("data-rename",false,false),("inner-data-swapped",false,false),
            ("registry-record-write",false,false),("registry-record-file-sync",false,false),("registry-record-directory-sync",false,false),("inner-record-published",false,false),
            ("inner-startup-attempted",false,false),("trial-started",false,false),("decision-created",false,false),("decision-file-sync",false,false),("decision-renamed",false,true),("decision-durable",false,true),
            ("inner-confirmed",false,true),("data-unlink",false,true),("registry-journal-remove",false,true),
            ("registry-journal-remove-directory-sync",false,true),("outer-unlinked",false,true)];
        for layout in 0..4 { for migration in [false,true] { for (stage, blocked, committed) in cases {
            if layout < 2 && !migration && stage == "data-rename" { continue; }
            if layout < 3 && stage == "data-unlink" { continue; }
            for occurrence in 1..=if ["registry-journal-write","registry-journal-file-sync"].contains(&stage) {5} else if stage == "data-rename" && layout >= 2 {2} else {1} {
            let committed=committed || (["registry-journal-write","registry-journal-file-sync"].contains(&stage) && occurrence==5);
            let (temp, registry, authority, data_root, new) = fixture(layout, migration, false);
            let baseline = authority.observe_update(&new.manifest.id).unwrap();
            let root = registry.root_path().to_path_buf(); let source = temp.path().join("source"); directory(&source);
            write(&source.join("app.json"), &serde_json::to_vec(&new.manifest).unwrap()); write(&source.join("main.wasm"), &new.bytes);
            drop(registry); drop(authority);
            killed_at(&mut child_command("update", &root, &data_root, &source), stage, occurrence);
            let before = tree(&root); let data_before = tree(&data_root);
            let committed_data = committed.then(|| {
                // A live transaction observer includes its backup in non-files;
                // the outer already binds the business-only confirmed digest.
                match fs::read(root.join(UPDATE_PENDING_NAME)) {
                    Ok(raw) => parse_update_record(&raw).unwrap().private_confirmed.unwrap(),
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound =>
                        crate::app_data::observe_private_update_read_only(&data_root,&new.manifest.id).unwrap(),
                    Err(error) => panic!("committed outer read: {error}"),
                }
            });
            if committed {
                let files = data_root.join(crate::app_data::package_directory_name(&new.manifest.id)).join("files");
                assert_eq!(fs::read(files.join("trial-a")).unwrap(),b"nonempty trial a");
                assert_eq!(fs::read(files.join("trial-b")).unwrap(),b"nonempty trial b");
            }
            let mut recover = child_command("recover", &root, &data_root, &source);
            if blocked { recover.env("MING_UPDATE_EXPECT_BLOCKED","1"); }
            let result = completed(&mut recover); assert!(result.status.success(), "{stage}: {} {}", String::from_utf8_lossy(&result.stdout),String::from_utf8_lossy(&result.stderr));
            let mut reopen = child_command("open", &root, &data_root, &source);
            reopen.env("MING_UPDATE_EXPECT_VERSION", if committed {"2.0.0"} else {"1.0.0"});
            if blocked { reopen.env("MING_UPDATE_EXPECT_BLOCKED","1"); }
            let opened = completed(&mut reopen); assert!(opened.status.success(), "{stage}: {} {}", String::from_utf8_lossy(&opened.stdout),String::from_utf8_lossy(&opened.stderr));
            if blocked { assert_eq!(tree(&root), before); assert_eq!(tree(&data_root), data_before); }
            else {
                let current = crate::app_data::observe_private_update_read_only(&data_root, &new.manifest.id).unwrap();
                if !committed { assert_eq!(current, baseline); }
                else { assert_eq!(current, committed_data.unwrap()); }
                assert!(!root.join(UPDATE_PENDING_NAME).exists()); assert!(!root.join(INNER_JOURNAL_NAME).exists());
            }
            println!("UPDATE_CASE {}",serde_json::json!({"kind":"sigkill","layout":layout,"migration":migration,"stage":stage,"occurrence":occurrence,"blocked":blocked,"committed":committed,
                "recoverExit":result.status.code(),"strictReopenExit":opened.status.code(),"private":crate::app_data::observe_private_update_read_only(&data_root,&new.manifest.id).ok()}));
        } } } }
    }

    #[test]
    fn recovery_process_itself_is_killed_during_missing_tree_and_partial_backup_cleanup() {
        for layout in 0..4 { for committed in [false,true] {
            let mut stages = vec!["data-sync", "registry-journal-remove", "registry-journal-remove-directory-sync", "outer-unlinked", "outer-remove-directory-sync", "registry-journal-write", "registry-journal-file-sync"];
            if !committed {stages.extend(["registry-record-write","registry-record-file-sync"]);}
            if !committed || layout >= 2 { stages.push("data-rmdir"); }
            if !committed || layout == 3 { stages.push("data-unlink"); }
            if !committed { stages.push("inner-rolling-back"); }
            for stage in stages {
            let occurrences = match stage {
                "data-unlink" => if committed {2} else if layout == 3 {4} else {2},
                "data-rmdir" if !committed && layout == 0 => 2,
                _ => 1,
            };
            for occurrence in 1..=occurrences {
            let (temp, registry, authority, data_root, new) = fixture(layout, false, false);
            let baseline = authority.observe_update(&new.manifest.id).unwrap();
            let root = registry.root_path().to_path_buf(); let source = temp.path().join("source"); directory(&source);
            write(&source.join("app.json"),&serde_json::to_vec(&new.manifest).unwrap());write(&source.join("main.wasm"),&new.bytes);
            drop(registry);drop(authority);
            killed_at(&mut child_command("update", &root, &data_root, &source),if committed {"inner-confirmed"} else {"trial-started"},1);
            let confirmed = parse_update_record(&fs::read(root.join(UPDATE_PENDING_NAME)).unwrap()).unwrap().private_confirmed;
            killed_at(&mut child_command("recover",&root,&data_root,&source),stage,occurrence);
            let recovered=completed(&mut child_command("recover",&root,&data_root,&source));assert!(recovered.status.success(),"{}",String::from_utf8_lossy(&recovered.stderr));
            let mut reopen=child_command("open",&root,&data_root,&source);reopen.env("MING_UPDATE_EXPECT_VERSION",if committed {"2.0.0"} else {"1.0.0"});
            assert!(completed(&mut reopen).status.success());
            assert_eq!(crate::app_data::observe_private_update_read_only(&data_root,&new.manifest.id).unwrap(),confirmed.unwrap_or(baseline));
            println!("UPDATE_CASE {}",serde_json::json!({"kind":"recovery-sigkill","layout":layout,"committed":committed,"stage":stage,"occurrence":occurrence,"recoverExit":recovered.status.code()}));
        } } } }
    }
}

#[cfg(test)]
mod update_transaction_tests {
    use crate::{app_data::{AppDataRoot,PackageStorageAuthority},package::Package,persistent_install};
    use super::*;
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
            let registry=crate::package_install::PackageInstallRegistry::open_verified_existing(&root).unwrap();
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
        let registry=crate::package_install::PackageInstallRegistry::open_verified_existing(&root).unwrap();
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
        let registry=crate::package_install::PackageInstallRegistry::open_verified_existing(&root).unwrap();
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
