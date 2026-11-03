#[cfg(not(all(target_os = "linux", feature = "desktop")))]
fn main() { eprintln!("linux desktop only"); std::process::exit(1); }

#[cfg(all(target_os = "linux", feature = "desktop"))]
fn main() -> anyhow::Result<()> { run(std::env::args_os().skip(1).collect()) }

#[cfg(all(target_os = "linux", feature = "desktop"))]
fn run(args: Vec<std::ffi::OsString>) -> anyhow::Result<()> {
    use anyhow::{Context, ensure, bail};
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use ed25519_dalek::{Signer, SigningKey};
    use ming_app_host::{package::{Manifest, Package, MAX_MODULE_BYTES}, persistent_install::{initialize_first, open_existing}};
    use serde_json::json;
    use std::{fs::{self, File, OpenOptions}, io::{Read, Write}, os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt}, path::{Component, Path, PathBuf}};

    fn absolute(path: &Path) -> anyhow::Result<()> {
        ensure!(path.is_absolute() && path.file_name().is_some(), "absolute file path required");
        ensure!(!path.as_os_str().as_encoded_bytes().split(|b|*b==b'/').any(|c|c==b"."||c==b".."),"path alias rejected");
        let mut cursor=PathBuf::new();
        for component in path.components() {
            ensure!(matches!(component,Component::RootDir|Component::Normal(_)),"path alias rejected");cursor.push(component);
            if cursor!=path {let metadata=fs::symlink_metadata(&cursor)?;ensure!(metadata.is_dir()&&!metadata.file_type().is_symlink(),"unsafe path ancestor");}
        }
        Ok(())
    }
    fn create(path:&Path, bytes:&[u8]) -> anyhow::Result<()> {
        absolute(path)?;
        let mut file=OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW).open(path)?;
        file.write_all(bytes)?;file.sync_all()?;File::open(path.parent().unwrap())?.sync_all()?;Ok(())
    }
    fn signed(id:&str,version:&str,path:&Path,key:&SigningKey) -> anyhow::Result<Package> {
        absolute(path)?;let meta=fs::symlink_metadata(path)?;
        ensure!(meta.is_file()&&!meta.file_type().is_symlink()&&meta.nlink()==1&&meta.len()<=MAX_MODULE_BYTES as u64,"unsafe Wasm input");
        let mut file=OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW).open(path)?;
        let mut bytes=Vec::new();Read::by_ref(&mut file).take(MAX_MODULE_BYTES as u64+1).read_to_end(&mut bytes)?;
        ensure!(bytes.len()<=MAX_MODULE_BYTES,"Wasm limit");
        let mut package=Package{manifest:Manifest{schema_version:1,id:id.into(),version:version.into(),sdk_version:6,entry:"main.wasm".into(),
            private_storage:false,public_storage:false,data_migration:None,publisher_key_id:Some("mingos-dev-2026".into()),publisher_signature:None},bytes};
        package.manifest.publisher_signature=Some(STANDARD.encode(key.sign(&package.signature_message("mingos-dev-2026")?).to_bytes()));Ok(package)
    }
    fn catalog_template(path: &Path, expected: &str) -> anyhow::Result<Vec<u8>> {
        absolute(path)?;
        ensure!(expected.len() == 64 && expected.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)), "invalid reviewed catalog hash");
        let before = fs::symlink_metadata(path)?;
        ensure!(before.is_file() && !before.file_type().is_symlink() && before.nlink() == 1 && before.len() <= 16 * 1024, "unsafe catalog template");
        let mut file = OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK).open(path)?;
        let opened = file.metadata()?;
        ensure!(opened.dev() == before.dev() && opened.ino() == before.ino() && opened.is_file(), "catalog template changed");
        let mut bytes = Vec::new();
        Read::by_ref(&mut file).take(16 * 1024 + 1).read_to_end(&mut bytes)?;
        ensure!(bytes.len() <= 16 * 1024 && bytes.len() as u64 == opened.len(), "catalog template changed");
        ensure!(ming_app_host::persistent_install::sha256(&bytes) == expected, "reviewed catalog hash drift");
        #[derive(serde::Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        struct Catalog { schema_version: u32, apps: Vec<App>, autostart: Vec<u16> }
        #[derive(serde::Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        struct App { id: u16, title: String, package_id: String }
        let catalog: Catalog = serde_json::from_slice(&bytes)?;
        ensure!(catalog.schema_version == 3 && catalog.autostart == [1] && catalog.apps.len() == 1
            && catalog.apps[0].id == 1 && catalog.apps[0].title == "Text Lab"
            && catalog.apps[0].package_id == "dev.ming.text-entry-demo", "reviewed initial catalog contract");
        // Return the exact bytes whose hash and schema were checked. Do not
        // reopen the template after validation or serialize a replacement.
        Ok(bytes)
    }
    let mode=args.first().and_then(|s|s.to_str()).context("explicit initialize-first/open-existing role required")?;
    let expected=match mode {"initialize-first"=>8,"open-existing"=>3,_=>bail!("invalid role")};
    ensure!(args.len()==expected,"invalid role arguments");
    let root=PathBuf::from(&args[1]);absolute(&root)?;
    let receipt=PathBuf::from(&args[expected-1]);absolute(&receipt)?;
    ensure!(!receipt.starts_with(&root),"receipt must be outside install content");
    ensure!(fs::symlink_metadata(&receipt).is_err_and(|e|e.kind()==std::io::ErrorKind::NotFound),"receipt already exists or unsafe");
    let (content,actions,status)=if mode=="initialize-first" {
        let template_path = PathBuf::from(&args[5]);
        let template_hash = args[6].to_str().context("reviewed catalog hash must be UTF-8")?;
        let catalog = catalog_template(&template_path, template_hash)?;
        let initial_path=PathBuf::from(&args[2]);let source_path=PathBuf::from(&args[3]);let source_manifest=PathBuf::from(&args[4]);
        ensure!(source_manifest.file_name()==Some(std::ffi::OsStr::new("app.json"))&&!source_manifest.starts_with(&root),"invalid source manifest");
        let source_parent=source_manifest.parent().context("source parent")?;
        let source_grandparent=source_parent.parent().context("source grandparent")?;absolute(source_grandparent)?;
        let metadata=fs::symlink_metadata(source_grandparent)?;ensure!(metadata.is_dir()&&!metadata.file_type().is_symlink(),"unsafe source grandparent");
        ensure!(fs::symlink_metadata(source_parent).is_err_and(|e|e.kind()==std::io::ErrorKind::NotFound),"source parent must be new");
        let key=SigningKey::from_bytes(&[7;32]);
        let initial=signed("dev.ming.text-entry-demo","0.1.0",&initial_path,&key)?;
        let source=signed("dev.ming.install-demo","1.0.0",&source_path,&key)?;
        let content=initialize_first(&root,&initial,"mingos-dev-2026",key.verifying_key().as_bytes(),&catalog)?;
        fs::DirBuilder::new().mode(0o700).create(source_parent)?;
        create(&source_parent.join("main.wasm"),&source.bytes)?;create(&source_manifest,&serde_json::to_vec(&source.manifest)?)?;
        File::open(source_grandparent)?.sync_all()?;
        (content,json!({"initializedRoots":1,"packagesInstalled":1,"trustStoresProvisioned":1,"catalogsCreated":1,"sourcesCreated":1}),"initialized-first")
    } else {
        let content=open_existing(&root)?.content;
        ensure!(content.apps.len()==2&&content.apps.iter().any(|a|a.app==1&&a.package_id=="dev.ming.text-entry-demo"&&a.version=="0.1.0")
            &&content.apps.iter().any(|a|a.app==2&&a.package_id=="dev.ming.install-demo"&&a.version=="1.0.0"),"qualified reopen requires committed app1/app2");
        (content,json!({"initializedRoots":0,"packagesInstalled":0,"trustStoresProvisioned":0,"catalogsCreated":0,"sourcesCreated":0}),"opened-existing")
    };
    let mut bytes=serde_json::to_vec(&json!({"schemaVersion":1,"status":status,"content":content,"actions":actions}))?;bytes.push(b'\n');
    ensure!(bytes.len()<=256*1024,"receipt limit");create(&receipt,&bytes)?;std::io::stdout().write_all(&bytes)?;Ok(())
}

#[cfg(all(test,target_os="linux",feature="desktop"))]
mod tests {
    use super::run;
    use ming_app_host::{package_install::PackageInstallRegistry,persistent_install};
    use std::{fs,os::unix::fs::{MetadataExt,PermissionsExt}};
    #[test]
    fn explicit_roles_seed_once_and_reopen_only_after_committed_app2() {
        let temp=tempfile::tempdir().unwrap();fs::set_permissions(temp.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let root=temp.path().join("install-root");let wasm=temp.path().join("seed.wasm");fs::write(&wasm,b"\0asm\x01\0\0\0").unwrap();
        let source=temp.path().join("source/app.json");let first=temp.path().join("first.json");let second=temp.path().join("second.json");
        let template=temp.path().join("initial-catalog.json");
        let template_bytes=br#"{ "schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#;
        fs::write(&template,template_bytes).unwrap();
        let expected_hash=persistent_install::sha256(template_bytes);
        run(vec!["initialize-first".into(),root.clone().into(),wasm.clone().into(),wasm.into(),source.clone().into(),template.clone().into(),expected_hash.clone().into(),first.clone().into()]).unwrap();
        let receipt:serde_json::Value=serde_json::from_slice(&fs::read(&first).unwrap()).unwrap();
        assert_eq!(receipt["status"],"initialized-first");assert!(receipt["actions"].as_object().unwrap().values().all(|v|v==1));
        assert_eq!(fs::metadata(&first).unwrap().mode()&0o7777,0o600);
        assert_eq!(fs::read(root.join("catalog.json")).unwrap(),template_bytes);
        let bootstrap:serde_json::Value=serde_json::from_slice(&fs::read(root.join("desktop-install-state-v1.json")).unwrap()).unwrap();
        assert_eq!(bootstrap["initialCatalogSha256"],expected_hash);
        assert!(run(vec!["open-existing".into(),root.clone().into(),second.clone().into()]).is_err());assert!(!second.exists());
        let registry=PackageInstallRegistry::open_verified(&root).unwrap();registry.install_from_manifest(&source).unwrap();drop(registry);
        fs::write(root.join("catalog.json"),br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"},{"id":2,"title":"Installed","packageId":"dev.ming.install-demo"}],"autostart":[1]}"#).unwrap();
        fs::remove_dir_all(source.parent().unwrap()).unwrap();
        fs::remove_file(template).unwrap();
        let before=persistent_install::open_existing(&root).unwrap().content;
        run(vec!["open-existing".into(),root.clone().into(),second.clone().into()]).unwrap();
        let receipt:serde_json::Value=serde_json::from_slice(&fs::read(&second).unwrap()).unwrap();
        assert_eq!(receipt["status"],"opened-existing");assert!(receipt["actions"].as_object().unwrap().values().all(|v|v==0));
        assert_eq!(persistent_install::open_existing(&root).unwrap().content,before);
        assert!(run(vec!["open-existing".into(),root.into(),second.into()]).is_err());
        assert!(run(vec!["implicit".into()]).is_err());
    }
    #[test]
    fn template_drift_and_invalid_schema_fail_before_any_initialization_write() {
        let canonical = br#"{"schemaVersion":3,"apps":[{"id":1,"title":"Text Lab","packageId":"dev.ming.text-entry-demo"}],"autostart":[1]}"#;
        for case in 0..6 {
            let temp = tempfile::tempdir().unwrap();
            fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();
            let root = temp.path().join("install-root");
            let wasm = temp.path().join("seed.wasm");
            fs::write(&wasm, b"\0asm\x01\0\0\0").unwrap();
            let source = temp.path().join("source/app.json");
            let receipt = temp.path().join("receipt.json");
            let template = temp.path().join("catalog.json");
            let bytes = match case {
                0 => canonical.to_vec(),
                1 => [canonical.as_slice(), b"\n"].concat(),
                2 => String::from_utf8(canonical.to_vec()).unwrap().replace("Text Lab", "TextLab").into_bytes(),
                3 => String::from_utf8(canonical.to_vec()).unwrap().replace("\"schemaVersion\":3", "\"schemaVersion\":3,\"schemaVersion\":3").into_bytes(),
                4 => String::from_utf8(canonical.to_vec()).unwrap().replace("\"autostart\":[1]", "\"autostart\":[1],\"unknown\":true").into_bytes(),
                5 => vec![b' '; 16 * 1024 + 1],
                _ => unreachable!(),
            };
            fs::write(&template, &bytes).unwrap();
            let hash = match case {
                0 => "0".repeat(64),
                1 => persistent_install::sha256(canonical),
                _ => persistent_install::sha256(&bytes),
            };
            assert!(run(vec!["initialize-first".into(), root.clone().into(), wasm.clone().into(), wasm.into(),
                source.clone().into(), template.into(), hash.into(), receipt.clone().into()]).is_err(), "case {case}");
            assert!(!root.exists() && !source.parent().unwrap().exists() && !receipt.exists(), "case {case}");
            assert!(!fs::read_dir(temp.path()).unwrap().any(|entry| entry.unwrap().file_name().to_string_lossy().starts_with(".install-root.staging-")), "case {case}");
        }
    }

}
