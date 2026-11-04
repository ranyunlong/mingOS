#[cfg(not(all(target_os = "linux", feature = "desktop")))]
fn main() { eprintln!("linux desktop only"); std::process::exit(1); }
#[cfg(all(target_os = "linux", feature = "desktop"))]
fn main() -> anyhow::Result<()> { helper::run(std::env::args_os().skip(1).collect(), &mut std::io::stdout()) }

#[cfg(all(target_os = "linux", feature = "desktop"))]
mod helper {
    use anyhow::{Context, ensure, bail};
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use ed25519_dalek::{Signer, SigningKey};
    use ming_app_host::{app_data::{PrivateUpdateBaseline, observe_private_update_read_only},
        package::{Manifest, Package, DataMigration, MigrationStep, MAX_MODULE_BYTES}, persistent_install::{self, ContentSnapshot},
        persistent_update::{self, UpdateFixtureLayout, UpdateRecovery}};
    use std::{ffi::CString, fs::{self, File, OpenOptions}, io::{Read, Seek, SeekFrom, Write},
        os::{fd::{AsRawFd, FromRawFd}, unix::{ffi::OsStrExt, fs::{DirBuilderExt, MetadataExt, OpenOptionsExt}}},
        path::{Component, Path, PathBuf}};
    const ID: &str = "dev.ming.persistent-update-demo";
    #[derive(Debug, Default, serde::Serialize, serde::Deserialize)]
    #[serde(rename_all="camelCase",deny_unknown_fields)]
    struct Actions {
        initialized_roots:u8, packages_installed:u8, publisher_provisioned:u8, bootstrap_published:u8,
        catalog_writes:u8, root_published:u8, source_bundles_created:u8, private_seed_writes:u8,
        recovery_invocations:u8, ui_updates_committed:u8,
    }
    #[derive(serde::Serialize, serde::Deserialize)]
    #[serde(rename_all="camelCase",deny_unknown_fields)]
    struct Receipt {
        schema_version:u32, status:String, actions:Actions, content:ContentSnapshot,
        private_content:PrivateUpdateBaseline,
    }
    fn absolute(path:&Path) -> anyhow::Result<()> {
        ensure!(path.is_absolute() && path.file_name().is_some(), "absolute path required");
        ensure!(!path.as_os_str().as_bytes().split(|b|*b==b'/').any(|c|c==b"."||c==b".."), "path alias rejected");
        let mut cursor=PathBuf::new();
        for part in path.components() {
            ensure!(matches!(part, Component::RootDir|Component::Normal(_)), "path alias rejected");
            cursor.push(part);
            if cursor!=path { let m=fs::symlink_metadata(&cursor)?; ensure!(m.is_dir()&&!m.file_type().is_symlink(),"unsafe path ancestor"); }
        }
        Ok(())
    }
    fn open_pinned(path:&Path) -> anyhow::Result<File> {
        absolute(path)?;
        let mut file=File::open("/")?;
        let components:Vec<_>=path.components().filter_map(|part|match part {Component::Normal(name)=>Some(name),_=>None}).collect();
        for (index,name) in components.iter().enumerate() {
            let name=CString::new(name.as_bytes())?;
            let flags=libc::O_RDONLY|libc::O_CLOEXEC|libc::O_NOFOLLOW|libc::O_NONBLOCK
                |if index+1<components.len(){libc::O_DIRECTORY}else{0};
            let fd=unsafe{libc::openat(file.as_raw_fd(),name.as_ptr(),flags)};
            if fd<0 { return Err(std::io::Error::last_os_error()).context("fixture input open"); }
            file=unsafe{File::from_raw_fd(fd)};
        }
        Ok(file)
    }
    fn read(path:&Path,limit:usize) -> anyhow::Result<Vec<u8>> {
        fn stamp(m:&fs::Metadata)->(u64,u64,u64,u64,u32,u32,i64,i64,i64,i64) {
            (m.dev(),m.ino(),m.len(),m.nlink(),m.mode(),m.uid(),m.mtime(),m.mtime_nsec(),m.ctime(),m.ctime_nsec())
        }
        let mut file=open_pinned(path)?;let before=file.metadata()?;
        ensure!(before.is_file()&&before.nlink()==1&&before.len()<=limit as u64,"unsafe fixture input");
        let mut bytes=Vec::new();Read::by_ref(&mut file).take(limit as u64+1).read_to_end(&mut bytes)?;
        ensure!(bytes.len()<=limit&&bytes.len() as u64==before.len(),"fixture input changed");
        file.seek(SeekFrom::Start(0))?;
        let mut verify=Vec::new();Read::by_ref(&mut file).take(limit as u64+1).read_to_end(&mut verify)?;
        ensure!(bytes==verify&&stamp(&file.metadata()?)==stamp(&before)&&stamp(&open_pinned(path)?.metadata()?)==stamp(&before),"fixture input changed");
        Ok(bytes)
    }
    fn create(path:&Path,bytes:&[u8]) -> anyhow::Result<()> {
        absolute(path)?;
        let mut file=OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW|libc::O_NONBLOCK).open(path)?;
        file.write_all(bytes)?;file.sync_all()?;File::open(path.parent().context("output parent")?)?.sync_all()?;Ok(())
    }
    fn signed(id:&str,version:&str,bytes:Vec<u8>,key:&SigningKey) -> anyhow::Result<Package> {
        let mut package=Package { manifest:Manifest { schema_version:1,id:id.into(),version:version.into(),sdk_version:6,entry:"main.wasm".into(),
            private_storage:false,public_storage:false,data_migration:None,publisher_key_id:Some("mingos-dev-2026".into()),publisher_signature:None },bytes };
        package.manifest.publisher_signature=Some(STANDARD.encode(key.sign(&package.signature_message("mingos-dev-2026")?).to_bytes()));Ok(package)
    }
    fn disjoint(a:&Path,b:&Path) -> anyhow::Result<()> {
        ensure!(!a.starts_with(b)&&!b.starts_with(a),"fixture paths overlap");Ok(())
    }
    fn absent(path:&Path) -> anyhow::Result<()> {
        ensure!(fs::symlink_metadata(path).is_err_and(|e|e.kind()==std::io::ErrorKind::NotFound),"fixture output exists or unsafe");Ok(())
    }
    fn catalog_template(path:&Path,expected:&str) -> anyhow::Result<Vec<u8>> {
        ensure!(expected.len()==64&&expected.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b)),"reviewed catalog hash required");
        let bytes=read(path,16*1024)?;ensure!(persistent_install::sha256(&bytes)==expected,"reviewed catalog hash drift");Ok(bytes)
    }
    fn inspect(root:&Path,data:&Path) -> anyhow::Result<(ContentSnapshot,PrivateUpdateBaseline)> {
        let opened=persistent_install::open_existing(root)?;
        ensure!(opened.content.apps.len()==2&&opened.content.apps.iter().any(|app|app.app==1&&app.package_id=="dev.ming.text-entry-demo"
            &&app.version=="0.1.0")&&opened.content.apps.iter().any(|app|app.app==2&&app.package_id==ID
            &&["0.1.0","0.2.0"].contains(&app.version.as_str())),"update fixture identity mismatch");
        let private=observe_private_update_read_only(data,ID)?;
        ensure!(persistent_install::snapshot(&opened.registry)?==opened.content,"fixture changed during observation");
        Ok((opened.content,private))
    }
    pub(super) fn run(args:Vec<std::ffi::OsString>,output:&mut impl Write) -> anyhow::Result<()> {
        let role=args.first().and_then(|s|s.to_str()).context("explicit initialize-first/open-existing/recover-update role required")?;
        let expected=match role {"initialize-first"=>11,"open-existing"|"recover-update"=>4,_=>bail!("unknown fixture role")};
        ensure!(args.len()==expected,"invalid fixture role arguments");
        let root=PathBuf::from(&args[1]);let data=PathBuf::from(&args[2]);let receipt=PathBuf::from(&args[expected-1]);
        absolute(&root)?;absolute(&data)?;absolute(&receipt)?;
        ensure!(!data.starts_with(&root)&&(!root.starts_with(&data)||root.parent()==Some(data.as_path())),"fixture root relationship");
        disjoint(&receipt,&root)?;disjoint(&receipt,&data)?;absent(&receipt)?;
        let guard=root.parent().context("install parent")?.join(format!(".{}.update-fixture-initializing",root.file_name().context("install name")?.to_str().context("install name UTF-8")?));
        disjoint(&receipt,&guard)?;
        let mut actions=Actions::default();
        let status=match role {
            "initialize-first"=>{
                let source=PathBuf::from(&args[6]);
                ensure!(source.file_name()==Some(std::ffi::OsStr::new("app.json")),"source app.json required");
                let source_parent=source.parent().context("source parent")?;absolute(source_parent)?;absent(source_parent)?;
                disjoint(source_parent,&root)?;disjoint(source_parent,&data)?;disjoint(source_parent,&guard)?;disjoint(source_parent,&receipt)?;
                let layout=match args[9].to_str(){Some("seed")=>UpdateFixtureLayout::NoteAlpha,Some("absent")=>UpdateFixtureLayout::Absent,_=>bail!("seed/absent fixture layout required")};
                let catalog=catalog_template(Path::new(&args[7]),args[8].to_str().context("catalog hash UTF-8")?)?;
                let key=SigningKey::from_bytes(&[7;32]);
                let bootstrap=signed("dev.ming.text-entry-demo","0.1.0",read(Path::new(&args[3]),MAX_MODULE_BYTES)?,&key)?;
                let old=signed(ID,"0.1.0",read(Path::new(&args[4]),MAX_MODULE_BYTES)?,&key)?;
                let mut new=signed(ID,"0.2.0",read(Path::new(&args[5]),MAX_MODULE_BYTES)?,&key)?;
                ensure!(old.bytes!=new.bytes,"old and new fixture modules must differ");
                if layout==UpdateFixtureLayout::NoteAlpha {
                    new.manifest.data_migration=Some(DataMigration{from_version:"0.1.0".into(),steps:vec![
                        MigrationStep::EnsureDir{path:"state".into()},MigrationStep::RenameFile{from:"note.txt".into(),to:"state/note.txt".into()}]});
                    new.manifest.publisher_signature=None;
                    new.manifest.publisher_signature=Some(STANDARD.encode(key.sign(&new.signature_message("mingos-dev-2026")?).to_bytes()));
                }
                persistent_update::initialize_update_fixture_first(&root,&data,&bootstrap,&old,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog,layout)?;
                fs::DirBuilder::new().mode(0o700).create(source_parent)?;
                create(&source_parent.join("main.wasm"),&new.bytes)?;create(&source,&serde_json::to_vec(&new.manifest)?)?;
                File::open(source_parent.parent().context("source grandparent")?)?.sync_all()?;
                actions=Actions{initialized_roots:1,packages_installed:2,publisher_provisioned:1,bootstrap_published:1,catalog_writes:2,
                    root_published:1,source_bundles_created:1,private_seed_writes:u8::from(layout==UpdateFixtureLayout::NoteAlpha),..Default::default()};
                "initialized-first"
            },
            "open-existing"=>"opened-existing",
            "recover-update"=>{
                let marker=read(&root.join(persistent_update::UPDATE_PENDING_NAME),persistent_update::MAX_UPDATE_RECORD_BYTES)?;
                let record=persistent_update::parse_update_record(&marker)?;
                ensure!(record.app==2&&record.old.package_id==ID,"recovery fixture transaction identity");
                let result=persistent_update::recover_existing_update(&root,&data).map_err(anyhow::Error::new)?;
                actions.recovery_invocations=1;
                match result {UpdateRecovery::RolledBack(_)=>"recovered-rolled-back",UpdateRecovery::Committed(_)=>"recovered-committed"}
            },
            _=>unreachable!(),
        };
        let (content,private_content)=inspect(&root,&data)?;
        let value=Receipt{schema_version:1,status:status.into(),actions,content,private_content};
        let mut bytes=serde_json::to_vec(&value)?;bytes.push(b'\n');ensure!(bytes.len()<=256*1024,"fixture receipt limit");
        create(&receipt,&bytes)?;output.write_all(&bytes)?;Ok(())
    }
}

#[cfg(all(test,target_os="linux",feature="desktop"))]
mod tests {
    use super::helper::run;
    use ming_app_host::{app_data::{AppDataRoot,PackageStorageAuthority,observe_private_update_read_only},package,persistent_install,persistent_update};
    use std::{ffi::OsString,fs,path::{Path,PathBuf},os::unix::fs::{MetadataExt,PermissionsExt}};
    fn tree(root:&Path)->Vec<(PathBuf,u32,u64,Vec<u8>)> {
        fn visit(path:&Path,out:&mut Vec<(PathBuf,u32,u64,Vec<u8>)>) {
            let m=fs::symlink_metadata(path).unwrap();let mut bytes=format!("{}:{}:{}:{}:{}:{}:{}",m.uid(),m.nlink(),m.len(),m.mtime(),m.mtime_nsec(),m.ctime(),m.ctime_nsec()).into_bytes();
            if m.is_file(){bytes.extend(fs::read(path).unwrap());}out.push((path.into(),m.mode(),m.ino(),bytes));
            if m.is_dir(){let mut children:Vec<_>=fs::read_dir(path).unwrap().map(|e|e.unwrap().path()).collect();children.sort();for p in children{visit(&p,out);}}
        }
        let mut out=Vec::new();visit(root,&mut out);out
    }
    fn call(args:&[&Path])->anyhow::Result<Vec<u8>> {let mut out=Vec::new();run(args.iter().map(|p|p.as_os_str().to_owned()).collect(),&mut out)?;Ok(out)}
    fn input(layout:&str)->(tempfile::TempDir,Vec<OsString>) {
        let temp=tempfile::tempdir().unwrap();fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let data=temp.path().join("data");fs::create_dir(&data).unwrap();fs::set_permissions(&data,fs::Permissions::from_mode(0o700)).unwrap();
        let root=data.join("install-root");let old=temp.path().join("old.wasm");let new=temp.path().join("new.wasm");
        fs::write(&old,wat::parse_str("(module)").unwrap()).unwrap();fs::write(&new,wat::parse_str("(module (func))").unwrap()).unwrap();
        let catalog=temp.path().join("initial.json");let bytes=br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#;
        fs::write(&catalog,bytes).unwrap();
        let args=vec!["initialize-first".into(),root.into_os_string(),data.into_os_string(),old.clone().into_os_string(),old.into_os_string(),new.into_os_string(),
            temp.path().join("source/app.json").into_os_string(),catalog.into_os_string(),persistent_install::sha256(bytes).into(),layout.into(),temp.path().join("first.json").into_os_string()];
        (temp,args)
    }
    #[test]
    fn explicit_roles_receipts_and_physical_initialization_counts() {
        for layout in ["seed","absent"] {
            let (temp,args)=input(layout);let mut stdout=Vec::new();run(args.clone(),&mut stdout).unwrap();assert_eq!(stdout,fs::read(&args[10]).unwrap());
            let value:serde_json::Value=serde_json::from_slice(&stdout).unwrap();let actions=&value["actions"];
            assert_eq!(actions["packagesInstalled"],2);assert_eq!(actions["catalogWrites"],2);assert_eq!(actions["rootPublished"],1);
            assert_eq!(actions["publisherProvisioned"],1);assert_eq!(actions["bootstrapPublished"],1);assert_eq!(actions["uiUpdatesCommitted"],0);
            assert_eq!(actions["privateSeedWrites"],u8::from(layout=="seed"));
            let root=Path::new(&args[1]);let data=Path::new(&args[2]);let before=tree(data);let receipt=temp.path().join("opened.json");
            let stdout=call(&[Path::new("open-existing"),root,data,&receipt]).unwrap();assert_eq!(stdout,fs::read(&receipt).unwrap());
            let value:serde_json::Value=serde_json::from_slice(&stdout).unwrap();assert!(value["actions"].as_object().unwrap().values().all(|v|v==0));
            assert_eq!(value["content"]["apps"].as_array().unwrap().len(),2);assert_eq!(tree(data),before);
            assert!(run(args.clone(),&mut Vec::new()).is_err());assert_eq!(tree(data),before);
        }
    }
    #[test]
    fn first_role_preserves_exact_guest_boot_infrastructure_in_nested_data_root() {
        let (temp,args)=input("seed");let data=Path::new(&args[2]);
        for name in ["lost+found",".mingos-boot"] {let path=data.join(name);fs::create_dir(&path).unwrap();fs::set_permissions(path,fs::Permissions::from_mode(0o700)).unwrap();}
        let marker=data.join(".mingos-boot/current.json");
        fs::write(&marker,serde_json::to_vec(&serde_json::json!({"bootId":fs::read_to_string("/proc/sys/kernel/random/boot_id").unwrap().trim(),"counter":1})).unwrap()).unwrap();
        fs::set_permissions(&marker,fs::Permissions::from_mode(0o600)).unwrap();
        let boot=tree(&data.join(".mingos-boot"));let lost=tree(&data.join("lost+found"));run(args.clone(),&mut Vec::new()).unwrap();
        assert_eq!(tree(&data.join(".mingos-boot")),boot);assert_eq!(tree(&data.join("lost+found")),lost);
        let before=tree(data);call(&[Path::new("open-existing"),Path::new(&args[1]),data,&temp.path().join("boot-open.json")]).unwrap();assert_eq!(tree(data),before);
    }
    #[test]
    fn open_and_recover_never_create_or_repair_missing_roots_or_locks() {
        for role in ["open-existing","recover-update"] {
            let (temp,args)=input("seed");let root=Path::new(&args[1]);let data=Path::new(&args[2]);let receipt=temp.path().join("missing.json");
            let before=tree(data);assert!(call(&[Path::new(role),root,data,&receipt]).is_err());assert_eq!(tree(data),before);assert!(!receipt.exists());assert!(!root.exists());
            run(args.clone(),&mut Vec::new()).unwrap();
            for name in [".ming-installed-packages.lock",".ming-publisher-keyring.lock"] {
                let path=root.join(name);let saved=fs::read(&path).unwrap();fs::remove_file(&path).unwrap();let before=tree(data);
                assert!(call(&[Path::new(role),root,data,&receipt]).is_err());assert_eq!(tree(data),before);assert!(!receipt.exists());
                fs::write(&path,saved).unwrap();fs::set_permissions(path,fs::Permissions::from_mode(0o600)).unwrap();
            }
        }
    }
    #[test]
    fn prepared_recovery_restores_nested_migration_and_read_only_open() {
        let (temp,args)=input("seed");run(args.clone(),&mut Vec::new()).unwrap();let root=Path::new(&args[1]);let data=Path::new(&args[2]);
        let opened=persistent_install::open_existing(root).unwrap();let authority=PackageStorageAuthority::open_persistent(AppDataRoot::open(data).unwrap()).unwrap();
        let new=package::load_package(Path::new(&args[6])).unwrap();
        let before=observe_private_update_read_only(data,&new.manifest.id).unwrap();let trial=persistent_update::begin_existing_update(&opened.registry,&authority,data,&new,2).unwrap();
        assert_eq!(trial.record.private_before,before);let pending=observe_private_update_read_only(data,&new.manifest.id).unwrap();assert!(pending.files.as_ref().unwrap().entries>=2);
        let receipt=temp.path().join("pending-open.json");let unchanged=tree(data);assert!(call(&[Path::new("open-existing"),root,data,&receipt]).is_err());assert_eq!(tree(data),unchanged);
        drop(opened);drop(authority);
        let recovery=temp.path().join("recovery.json");let out=call(&[Path::new("recover-update"),root,data,&recovery]).unwrap();let value:serde_json::Value=serde_json::from_slice(&out).unwrap();
        assert_eq!(value["status"],"recovered-rolled-back");assert_eq!(value["actions"]["recoveryInvocations"],1);assert_eq!(value["actions"]["packagesInstalled"],0);
        assert_eq!(observe_private_update_read_only(data,&new.manifest.id).unwrap(),before);
        let opened_receipt=temp.path().join("after.json");let unchanged=tree(data);call(&[Path::new("open-existing"),root,data,&opened_receipt]).unwrap();assert_eq!(tree(data),unchanged);
    }
    #[test]
    fn prepared_recovery_rejects_missing_policy_lock_without_writes() {
        for policy in ["system-private-storage-policy-v1","system-public-storage-policy-v1"] {
            let (temp,args)=input("seed");run(args.clone(),&mut Vec::new()).unwrap();
            let root=Path::new(&args[1]);let data=Path::new(&args[2]);
            let opened=persistent_install::open_existing(root).unwrap();
            let authority=PackageStorageAuthority::open_persistent(AppDataRoot::open(data).unwrap()).unwrap();
            let new=package::load_package(Path::new(&args[6])).unwrap();
            persistent_update::begin_existing_update(&opened.registry,&authority,data,&new,2).unwrap();
            drop(opened);drop(authority);
            fs::remove_file(data.join(policy).join(".policy-lock")).unwrap();
            let before=tree(data);let receipt=temp.path().join("rejected-recovery.json");
            assert!(call(&[Path::new("recover-update"),root,data,&receipt]).is_err());
            assert_eq!(tree(data),before);assert!(!receipt.exists());
        }
    }
    #[test]
    fn unsafe_inputs_and_receipt_paths_are_rejected_before_first_writes() {
        for case in 0..7 {
            let (temp,mut args)=input("seed");
            match case {
                0=>args[10]=Path::new(&args[2]).join("receipt.json").into_os_string(),
                1=>args[6]=Path::new(&args[2]).join("source/app.json").into_os_string(),
                2=>args[8]="0".repeat(64).into(),
                3=>args[9]="implicit".into(),
                4=>{let new=PathBuf::from(&args[5]);fs::remove_file(&new).unwrap();std::os::unix::fs::symlink(&args[4],new).unwrap();},
                5=>{let new=PathBuf::from(&args[5]);fs::remove_file(&new).unwrap();fs::hard_link(&args[4],new).unwrap();},
                _=>args[0]="initialize-or-open".into(),
            }
            let before=tree(Path::new(&args[2]));assert!(run(args.clone(),&mut Vec::new()).is_err(),"case {case}");assert_eq!(tree(Path::new(&args[2])),before,"case {case}");
            assert!(!temp.path().join("source").exists());assert!(!Path::new(&args[1]).exists());
        }
    }
}
