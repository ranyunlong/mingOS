#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("linux only");
    std::process::exit(1);
}

#[cfg(target_os = "linux")]
fn main() -> anyhow::Result<()> {
    use ed25519_dalek::SigningKey;
    use ming_app_host::{
        app_data::{AppDataRoot, PackageStorageAuthority},
        package::{load_package, DataMigration, MigrationStep},
        package_install::PackageInstallRegistry,
        publisher::PublisherTrustStore,
    };
    use serde_json::json;
    use std::{
        env, fs, os::unix::fs::PermissionsExt, path::PathBuf, sync::Arc,
    };

    let mut args = env::args_os().skip(1);
    let data_root = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("data root required"))?);
    let install_root = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("install root required"))?);
    let update_manifest = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("update manifest required"))?);
    let receipt_path = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("receipt path required"))?);
    if args.next().is_some() { anyhow::bail!("unexpected arguments"); }

    let key_id = "mingos-dev-2026";
    let signing_key = SigningKey::from_bytes(&[7u8; 32]);
    fs::create_dir_all(&data_root)?;
    fs::set_permissions(&data_root, fs::Permissions::from_mode(0o700))?;
    for name in [
        "system-private-storage-policy-v1",
        "system-public-storage-policy-v1",
        "public-files-v1",
        &package_directory_name("dev.ming.app-data-desktop"),
        &package_directory_name("dev.ming.text-entry-demo"),
    ] {
        let path = data_root.join(name);
        fs::create_dir_all(&path)?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
    }
    fs::create_dir_all(&install_root)?;
    fs::set_permissions(&install_root, fs::Permissions::from_mode(0o700))?;
    if !install_root.join("trusted-publishers-v1").exists() {
        PublisherTrustStore::provision(&install_root, key_id, signing_key.verifying_key().as_bytes())?;
    }

    let registry = PackageInstallRegistry::open_verified(&install_root)?;
    let app1 = "dev.ming.app-data-desktop";
    let app2 = "dev.ming.text-entry-demo";
    let current = registry.active_manifest_path(app1).ok()
        .and_then(|path| load_package(&path).ok())
        .map(|package| package.manifest.version);
    if current.is_none() {
        let old1 = signed_package(app1, "0.1.0", &PathBuf::from("/opt/mingos/packages/1/main.wasm"), None, &signing_key, key_id)?;
        let old2 = signed_package(app2, "0.1.0", &PathBuf::from("/opt/mingos/packages/2/main.wasm"), None, &signing_key, key_id)?;
        let temp = PathBuf::from("/tmp/mingos-update-fixture");
        write_package(&temp.join("old-1"), &old1)?;
        write_package(&temp.join("old-2"), &old2)?;
        registry.install_from_manifest(&temp.join("old-1/app.json"))?;
        registry.install_from_manifest(&temp.join("old-2/app.json"))?;
    }

    let authority = Arc::new(PackageStorageAuthority::open_persistent(AppDataRoot::open(&data_root)?)?);
    if current.is_none() {
        authority.authorize(app1)?;
        let session = authority.file_session(app1)?;
        session.write_file("counter", b"42")?;
        drop(session);
        authority.revoke(app1)?;
        authority.authorize(app2)?;
        let sibling = authority.file_session(app2)?;
        sibling.write_file("sibling", b"keep")?;
        drop(sibling);
        authority.revoke(app2)?;

        let migration = DataMigration {
            from_version: "0.1.0".into(),
            steps: vec![
                MigrationStep::EnsureDir { path: "state".into() },
                MigrationStep::RenameFile { from: "counter".into(), to: "state/counter".into() },
            ],
        };
        let update = signed_package(app1, "0.2.0", &PathBuf::from("/opt/mingos/packages/1/main.wasm"), Some(migration), &signing_key, key_id)?;
        write_package(&update_manifest.parent().unwrap().to_path_buf(), &update)?;
        let state = registry.update_from_manifest(&update_manifest, &authority)?;
        if format!("{state:?}") != "Updated" { anyhow::bail!("unexpected update state: {state:?}"); }
        let files = data_root.join(package_directory_name(app1)).join("files");
        let before = files.join("counter").exists();
        let after = fs::read_to_string(files.join("state/counter"))?;
        if before || after != "42" { anyhow::bail!("migration readback mismatch"); }
        write_receipt(&receipt_path, json!({
            "status": "prepared-record-published",
            "oldVersion": "0.1.0",
            "newVersion": "0.2.0",
            "dataBefore": "counter=42",
            "dataAfter": "state/counter=42",
            "siblingPreserved": fs::read_to_string(data_root.join(package_directory_name(app2)).join("files/sibling"))? == "keep",
            "journalPresent": registry.has_pending_update()?,
            "startupConfirmationRequired": true,
        }))?;
    } else {
        let version = registry.active_manifest_path(app1).ok()
            .and_then(|path| load_package(&path).ok())
            .map(|package| package.manifest.version);
        write_receipt(&receipt_path, json!({
            "status": if version.as_deref() == Some("0.2.0") { "already-updated" } else { "unexpected-state" },
            "activeVersion": version,
            "journalPresent": registry.has_pending_update()?,
            "dataAfter": fs::read_to_string(data_root.join(package_directory_name(app1)).join("files/state/counter")).ok(),
            "siblingPreserved": fs::read_to_string(data_root.join(package_directory_name(app2)).join("files/sibling")).ok().as_deref() == Some("keep"),
        }))?;
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn signed_package(
    id: &str,
    version: &str,
    wasm: &std::path::Path,
    migration: Option<ming_app_host::package::DataMigration>,
    signing_key: &ed25519_dalek::SigningKey,
    key_id: &str,
) -> anyhow::Result<ming_app_host::package::Package> {
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use ed25519_dalek::Signer;
    use ming_app_host::package::{Manifest, Package};
    let mut manifest = Manifest {
        schema_version: 1,
        id: id.into(),
        version: version.into(),
        sdk_version: 6,
        entry: "main.wasm".into(),
        private_storage: true,
        public_storage: false,
        data_migration: migration,
        publisher_key_id: Some(key_id.into()),
        publisher_signature: None,
    };
    let bytes = std::fs::read(wasm)?;
    let unsigned = Package { manifest, bytes };
    let signature = signing_key.sign(&unsigned.signature_message(key_id)?);
    manifest = unsigned.manifest;
    manifest.publisher_signature = Some(STANDARD.encode(signature.to_bytes()));
    Ok(Package { manifest, bytes: unsigned.bytes })
}

#[cfg(target_os = "linux")]
fn write_package(root: &std::path::Path, package: &ming_app_host::package::Package) -> anyhow::Result<()> {
    use std::{fs, os::unix::fs::PermissionsExt};
    fs::create_dir_all(root)?;
    fs::set_permissions(root, fs::Permissions::from_mode(0o700))?;
    fs::write(root.join(&package.manifest.entry), &package.bytes)?;
    fs::write(root.join("app.json"), serde_json::to_vec_pretty(&package.manifest)?)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn write_receipt(path: &std::path::Path, value: serde_json::Value) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() { std::fs::create_dir_all(parent)?; }
    std::fs::write(path, serde_json::to_vec_pretty(&value)?)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn package_directory_name(id: &str) -> String {
    const ALPHABET: &[u8; 32] = b"abcdefghijklmnopqrstuvwxyz234567";
    let mut name = String::from("app-");
    let mut bits = 0u32;
    let mut count = 0u32;
    for byte in id.bytes() {
        bits = (bits << 8) | u32::from(byte);
        count += 8;
        while count >= 5 {
            count -= 5;
            name.push(ALPHABET[((bits >> count) & 31) as usize] as char);
        }
        bits &= (1u32 << count).saturating_sub(1);
    }
    if count != 0 { name.push(ALPHABET[(bits << (5 - count)) as usize] as char); }
    name
}
