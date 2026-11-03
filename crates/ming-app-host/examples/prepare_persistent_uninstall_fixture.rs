#[cfg(not(all(target_os = "linux", feature = "desktop")))]
fn main() { eprintln!("linux desktop only"); std::process::exit(1); }
#[cfg(all(target_os = "linux", feature = "desktop"))]
fn main() -> anyhow::Result<()> { run(std::env::args_os().skip(1).collect(), &mut std::io::stdout()) }

#[cfg(all(target_os = "linux", feature = "desktop"))]
fn run(args: Vec<std::ffi::OsString>, output: &mut impl std::io::Write) -> anyhow::Result<()> {
    use anyhow::{Context, ensure, bail};
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use ed25519_dalek::{Signer, SigningKey};
    use ming_app_host::{app_data::{PrivateContentFile, PrivateContentSnapshot, snapshot_private_read_only},
        package::{Manifest, Package, MAX_MODULE_BYTES}, persistent_install::{self, ContentSnapshot, RemovedPackageIdentity}};
    use std::{ffi::CString, fs::{self, File, OpenOptions}, io::{Read, Seek, SeekFrom, Write},
        os::{fd::{AsRawFd, FromRawFd}, unix::{ffi::OsStrExt, fs::{DirBuilderExt, MetadataExt, OpenOptionsExt}}},
        path::{Component, Path, PathBuf}};
    const ID: &str = "dev.ming.install-demo";
    #[derive(serde::Serialize, serde::Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct Actions { initialized_roots: u8, packages_installed: u8, trust_stores_provisioned: u8, catalogs_created: u8, sources_created: u8 }
    impl Actions { fn new(value:u8) -> Self { Self { initialized_roots:value, packages_installed:value, trust_stores_provisioned:value, catalogs_created:value, sources_created:value } } }
    #[derive(serde::Serialize, serde::Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct Receipt { schema_version:u32, status:String, actions:Actions, content:Option<ContentSnapshot>, removed:Option<RemovedPackageIdentity>, private_content:Option<PrivateContentSnapshot> }
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
    fn catalog_template(path:&Path,expected:&str) -> anyhow::Result<Vec<u8>> {
        ensure!(expected.len()==64&&expected.bytes().all(|b|b.is_ascii_digit()||(b'a'..=b'f').contains(&b)),"invalid reviewed catalog hash");
        let bytes=read(path,16*1024)?;ensure!(persistent_install::sha256(&bytes)==expected,"reviewed catalog hash drift");
        #[derive(serde::Deserialize)]
        #[serde(rename_all="camelCase",deny_unknown_fields)]
        struct Catalog { schema_version:u32,apps:Vec<App>,autostart:Vec<u16> }
        #[derive(serde::Deserialize)]
        #[serde(rename_all="camelCase",deny_unknown_fields)]
        struct App { id:u16,title:String,package_id:String }
        let c:Catalog=serde_json::from_slice(&bytes)?;
        ensure!(c.schema_version==3&&c.autostart==[1]&&c.apps.len()==1&&c.apps[0].id==1&&c.apps[0].title=="Text Lab"&&c.apps[0].package_id=="dev.ming.text-entry-demo","reviewed initial catalog contract");Ok(bytes)
    }
    fn private(data:&Path) -> anyhow::Result<PrivateContentSnapshot> {
        let snapshot=snapshot_private_read_only(data,ID)?;
        ensure!(snapshot.files==[PrivateContentFile { path:"note.txt".into(),size:5,sha256:"8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8".into() }],"private fixture content mismatch");Ok(snapshot)
    }
    let mode=args.first().and_then(|s|s.to_str()).context("explicit initialize-first/open-uninstalled/snapshot-private role required")?;
    let expected=match mode {"initialize-first"=>8,"open-uninstalled"=>5,"snapshot-private"=>3,_=>bail!("invalid role")};
    ensure!(args.len()==expected,"invalid role arguments");
    let root=PathBuf::from(&args[1]);absolute(&root)?;
    let receipt=PathBuf::from(&args[expected-1]);absolute(&receipt)?;
    ensure!(!receipt.starts_with(&root),"receipt must be outside observed root");
    ensure!(fs::symlink_metadata(&receipt).is_err_and(|e|e.kind()==std::io::ErrorKind::NotFound),"receipt already exists or unsafe");
    let result=match mode {
        "initialize-first"=>{
            let catalog=catalog_template(Path::new(&args[5]),args[6].to_str().context("catalog hash UTF-8")?)?;
            let source_manifest=PathBuf::from(&args[4]);
            ensure!(source_manifest.file_name()==Some(std::ffi::OsStr::new("app.json"))&&!source_manifest.starts_with(&root),"invalid source manifest");
            let source_parent=source_manifest.parent().context("source parent")?;
            let source_grandparent=source_parent.parent().context("source grandparent")?;absolute(source_grandparent)?;
            ensure!(fs::symlink_metadata(source_grandparent)?.is_dir(),"unsafe source grandparent");
            ensure!(fs::symlink_metadata(source_parent).is_err_and(|e|e.kind()==std::io::ErrorKind::NotFound),"source parent must be new");
            // Fixed development-only test key. Inputs are frozen before any initialization write.
            let key=SigningKey::from_bytes(&[7;32]);
            let initial=signed("dev.ming.text-entry-demo","0.1.0",read(Path::new(&args[2]),MAX_MODULE_BYTES)?,&key)?;
            let source=signed(ID,"1.0.0",read(Path::new(&args[3]),MAX_MODULE_BYTES)?,&key)?;
            let content=persistent_install::initialize_first(&root,&initial,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog)?;
            fs::DirBuilder::new().mode(0o700).create(source_parent)?;
            create(&source_parent.join("main.wasm"),&source.bytes)?;create(&source_manifest,&serde_json::to_vec(&source.manifest)?)?;File::open(source_grandparent)?.sync_all()?;
            Receipt {schema_version:1,status:"initialized-first".into(),actions:Actions::new(1),content:Some(content),removed:None,private_content:None}
        },
        "open-uninstalled"=>{
            let data=PathBuf::from(&args[3]);absolute(&data)?;ensure!(!receipt.starts_with(&data),"receipt must be outside data root");
            let expected=signed(ID,"1.0.0",read(Path::new(&args[2]),MAX_MODULE_BYTES)?,&SigningKey::from_bytes(&[7;32]))?;
            let opened=persistent_install::open_existing(&root)?;
            ensure!(opened.content.apps.len()==1&&opened.content.apps[0].app==1&&opened.content.apps[0].package_id=="dev.ming.text-entry-demo"&&opened.content.apps[0].version=="0.1.0","uninstalled reopen requires only original app1");
            // This reloads the record-derived on-disk history and verifies its stored
            // publisher signature. The frozen in-memory package is only an extra comparison.
            let removed=persistent_install::snapshot_removed_package(&opened.registry,&expected)?;
            let private_content=private(&data)?;
            ensure!(persistent_install::snapshot(&opened.registry)?==opened.content,"install content changed");
            Receipt {schema_version:1,status:"opened-uninstalled".into(),actions:Actions::new(0),content:Some(opened.content),removed:Some(removed),private_content:Some(private_content)}
        },
        "snapshot-private"=>Receipt {schema_version:1,status:"snapshotted-private".into(),actions:Actions::new(0),content:None,removed:None,private_content:Some(private(&root)?)},
        _=>unreachable!(),
    };
    let mut bytes=serde_json::to_vec(&result)?;bytes.push(b'\n');ensure!(bytes.len()<=256*1024,"receipt limit");create(&receipt,&bytes)?;output.write_all(&bytes)?;Ok(())
}

#[cfg(all(test, target_os = "linux", feature = "desktop"))]
mod tests {
    use super::run;
    use ming_app_host::{app_data::{AppDataRoot,PackageStorageAuthority}, package, package_install::PackageInstallRegistry, persistent_install};
    use std::{ffi::OsString,fs,path::{Path,PathBuf},os::unix::fs::{MetadataExt,PermissionsExt}};
    fn call(args: &[&Path]) -> anyhow::Result<Vec<u8>> { let mut output=Vec::new(); run(args.iter().map(|p|p.as_os_str().to_owned()).collect(),&mut output)?; Ok(output) }
    fn tree(root: &Path) -> Vec<(PathBuf,u32,u64,Vec<u8>)> {
        fn walk(path:&Path,out:&mut Vec<(PathBuf,u32,u64,Vec<u8>)>) { let m=fs::symlink_metadata(path).unwrap();let mut evidence=format!("{}:{}:{}:{}:{}:{}:{}:",m.uid(),m.nlink(),m.len(),m.mtime(),m.mtime_nsec(),m.ctime(),m.ctime_nsec()).into_bytes();if m.is_file(){evidence.extend(fs::read(path).unwrap());}out.push((path.to_owned(),m.mode(),m.ino(),evidence)); if m.is_dir(){let mut children:Vec<_>=fs::read_dir(path).unwrap().map(|e|e.unwrap().path()).collect();children.sort();for p in children{walk(&p,out);}} }
        let mut out=Vec::new();walk(root,&mut out);out
    }
    fn initialized() -> (tempfile::TempDir,PathBuf,PathBuf,PathBuf) {
        let temp=tempfile::tempdir().unwrap();fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let root=temp.path().join("install");let wasm=temp.path().join("new.wasm");fs::write(&wasm,b"\0asm\x01\0\0\0").unwrap();
        let source=temp.path().join("source/app.json"); let template=temp.path().join("catalog-template.json");
        let bytes=br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#;
        fs::write(&template,bytes).unwrap();let hash=persistent_install::sha256(bytes);let receipt=temp.path().join("initialize.json");
        let stdout=call(&[Path::new("initialize-first"),&root,&wasm,&wasm,&source,&template,Path::new(&hash),&receipt]).unwrap(); assert_eq!(stdout,fs::read(&receipt).unwrap());
        let value:serde_json::Value=serde_json::from_slice(&stdout).unwrap();assert_eq!(value["removed"],serde_json::Value::Null);assert_eq!(value["privateContent"],serde_json::Value::Null);
        assert!(value["actions"].as_object().unwrap().values().all(|v|v==1)); assert_eq!(fs::read(root.join("catalog.json")).unwrap(),bytes);
        (temp,root,wasm,source)
    }
    fn removed() -> (tempfile::TempDir,PathBuf,PathBuf,PathBuf) {
        let (temp,root,wasm,source)=initialized();
        let registry=PackageInstallRegistry::open_verified(&root).unwrap();registry.install_from_manifest(&source).unwrap();
        let p=package::load_package(&source).unwrap();let data=temp.path().join("data");fs::create_dir(&data).unwrap();fs::set_permissions(&data,fs::Permissions::from_mode(0o700)).unwrap();
        let authority=std::sync::Arc::new(PackageStorageAuthority::new(AppDataRoot::open(&data).unwrap()));
        authority.authorize(&p.manifest.id).unwrap();
        let session=authority.file_session(&p.manifest.id).unwrap();
        assert!(session.write_file("note.txt",b"alpha").unwrap().directory_synced);
        drop(session);drop(authority);
        assert!(registry.uninstall(&p.manifest.id).unwrap());drop(registry);
        (temp,root,wasm,data)
    }
    #[test]
    fn initialization_and_read_only_roles_are_explicit_and_receipts_match_stdout() {
        let (temp,root,wasm,data)=removed();let receipt=temp.path().join("opened.json");let before=(tree(&root),tree(&data));let inputs=(tree(&wasm),tree(&temp.path().join("source")));
        let output=call(&[Path::new("open-uninstalled"),&root,&wasm,&data,&receipt]).unwrap();assert_eq!(output,fs::read(&receipt).unwrap());
        let value:serde_json::Value=serde_json::from_slice(&output).unwrap();assert_eq!(value.as_object().unwrap().keys().map(String::as_str).collect::<Vec<_>>(),["actions","content","privateContent","removed","schemaVersion","status"]);assert_eq!(value["status"],"opened-uninstalled");assert_eq!(value["removed"]["packageId"],"dev.ming.install-demo");assert_eq!(value["removed"]["version"],"1.0.0");assert_eq!(value["content"]["apps"].as_array().unwrap().len(),1);assert_eq!(value["privateContent"]["files"].as_array().unwrap().len(),1);assert!(value["actions"].as_object().unwrap().values().all(|v|v==0)); assert_eq!((tree(&root),tree(&data)),before);
        let snapshot=temp.path().join("snapshot.json");let output=call(&[Path::new("snapshot-private"),&data,&snapshot]).unwrap();assert_eq!(output,fs::read(&snapshot).unwrap());assert_eq!((tree(&root),tree(&data)),before);
        assert_eq!((tree(&wasm),tree(&temp.path().join("source"))),inputs);
        assert!(call(&[Path::new("snapshot-private"),&data,&snapshot]).is_err());
        assert!(run(vec![OsString::from("implicit")],&mut Vec::new()).is_err());assert!(run(Vec::new(),&mut Vec::new()).is_err());
        assert!(call(&[Path::new("open-uninstalled"),&root,&wasm,&data,&root.join("receipt.json")]).is_err());
    }
    #[test]
    fn stored_signature_tampering_and_missing_locks_fail_without_repair() {
        for case in 0..3 {
            let (temp,root,wasm,data)=removed();
            match case {
                0=>{
                    let packages=root.join("packages");let mut manifests=Vec::new();fn find(p:&Path,out:&mut Vec<PathBuf>){for e in fs::read_dir(p).unwrap(){let e=e.unwrap();if e.file_type().unwrap().is_dir(){find(&e.path(),out)}else if e.file_name()=="app.json"{out.push(e.path())}}}find(&packages,&mut manifests);
                    let path=manifests.into_iter().find(|p|fs::read_to_string(p).unwrap().contains("dev.ming.install-demo")).unwrap();let mut m:serde_json::Value=serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();m["publisherSignature"]=serde_json::json!("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==");fs::write(path,serde_json::to_vec(&m).unwrap()).unwrap();},
                1=>fs::remove_file(root.join(".ming-installed-packages.lock")).unwrap(),
                _=>fs::remove_file(root.join(".ming-publisher-keyring.lock")).unwrap(),
            }
            let before=(tree(&root),tree(&data));let receipt=temp.path().join("rejected.json");assert!(call(&[Path::new("open-uninstalled"),&root,&wasm,&data,&receipt]).is_err(),"case {case}");assert!(!receipt.exists());assert_eq!((tree(&root),tree(&data)),before);
        }
    }
    #[test]
    fn private_fixture_rejects_additional_files_and_missing_root_without_writes() {
        let (temp,_root,_wasm,data)=removed();let missing=temp.path().join("missing");let receipt=temp.path().join("absent.json");assert!(call(&[Path::new("snapshot-private"),&missing,&receipt]).is_err());assert!(!missing.exists());
        let package=fs::read_dir(&data).unwrap().map(|e|e.unwrap().path()).find(|p|p.file_name().unwrap().as_encoded_bytes().starts_with(b"app-")).unwrap();let extra=package.join("files/extra.txt");fs::write(&extra,b"x").unwrap();fs::set_permissions(extra,fs::Permissions::from_mode(0o600)).unwrap();let before=tree(&data);assert!(call(&[Path::new("snapshot-private"),&data,&receipt]).is_err());assert_eq!(tree(&data),before);assert!(!receipt.exists());
    }
    #[test]
    fn invalid_roles_and_unsafe_inputs_fail_before_initialization() {
        for mode in ["initialize-first","open-uninstalled","snapshot-private","unknown"] {
            assert!(run(vec![OsString::from(mode)],&mut Vec::new()).is_err());
        }
        for case in 0..6 {
            let temp=tempfile::tempdir().unwrap();fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
            let root=temp.path().join("install");let initial=temp.path().join("initial.wasm");let new=temp.path().join("new.wasm");let source=temp.path().join("source/app.json");let template=temp.path().join("template.json");
            fs::write(&initial,b"\0asm\x01\0\0\0").unwrap();fs::write(&new,b"\0asm\x01\0\0\0").unwrap();
            let mut catalog=br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#.to_vec();
            let mut receipt=temp.path().join("receipt.json");
            match case {
                1=>{fs::remove_file(&new).unwrap();std::os::unix::fs::symlink(&initial,&new).unwrap();},
                2=>{fs::remove_file(&new).unwrap();fs::hard_link(&initial,&new).unwrap();},
                3=>{let mut value:serde_json::Value=serde_json::from_slice(&catalog).unwrap();value["unknown"]=serde_json::json!(1);catalog=serde_json::to_vec(&value).unwrap();},
                4=>receipt=root.join("receipt.json"),
                5=>{fs::create_dir(&root).unwrap();fs::set_permissions(&root,fs::Permissions::from_mode(0o700)).unwrap();},
                _=>(),
            }
            fs::write(&template,&catalog).unwrap();let hash=if case==0 {"0".repeat(64)} else {persistent_install::sha256(&catalog)};
            let before=tree(temp.path());
            assert!(call(&[Path::new("initialize-first"),&root,&initial,&new,&source,&template,Path::new(&hash),&receipt]).is_err(),"unsafe input {case}");
            assert_eq!(tree(temp.path()),before);assert!(!receipt.exists());assert!(!source.exists());
        }
    }

}
