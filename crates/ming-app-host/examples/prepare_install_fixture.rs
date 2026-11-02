#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("linux only");
    std::process::exit(1);
}

#[cfg(target_os = "linux")]
fn main() -> anyhow::Result<()> {
    use ed25519_dalek::SigningKey;
    use ming_app_host::{
        package::{load_package, Package},
        package_install::PackageInstallRegistry,
        publisher::PublisherTrustStore,
    };
    use serde_json::json;
    use std::{env, fs, os::unix::fs::PermissionsExt, path::PathBuf};

    let mut args = env::args_os().skip(1);
    let install_root = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("install root required"))?);
    let initial_wasm = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("initial wasm required"))?);
    let source_wasm = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("source wasm required"))?);
    let source_manifest = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("source manifest required"))?);
    let receipt_path = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("receipt path required"))?);
    if args.next().is_some() { anyhow::bail!("unexpected arguments"); }

    fs::create_dir_all(&install_root)?;
    fs::set_permissions(&install_root, fs::Permissions::from_mode(0o700))?;
    let key_id = "mingos-dev-2026";
    let signing_key = SigningKey::from_bytes(&[7u8; 32]);
    if !install_root.join("trusted-publishers-v1").exists() {
        PublisherTrustStore::provision(&install_root, key_id, signing_key.verifying_key().as_bytes())?;
    }

    let registry = PackageInstallRegistry::open_verified(&install_root)?;
    let initial_id = "dev.ming.text-entry-demo";
    if registry.active_manifest_path(initial_id).is_err() {
        let initial = signed_package(initial_id, "0.1.0", &initial_wasm, &signing_key, key_id)?;
        let path = PathBuf::from("/tmp/mingos-install-fixture/initial/app.json");
        write_package(path.parent().unwrap(), &initial)?;
        registry.install_from_manifest(&path)?;
    }

    let source = signed_package("dev.ming.install-demo", "1.0.0", &source_wasm, &signing_key, key_id)?;
    write_package(source_manifest.parent().ok_or_else(|| anyhow::anyhow!("source parent missing"))?, &source)?;
    let loaded = load_package(&source_manifest)?;
    write_receipt(&receipt_path, json!({
        "status": "ready",
        "initialPackageId": initial_id,
        "initialVersion": "0.1.0",
        "sourcePackageId": loaded.manifest.id,
        "sourceVersion": loaded.manifest.version,
        "publisherKeyId": key_id,
        "sourceFingerprint": hex_fingerprint(loaded.storage_fingerprint()),
        "initialActive": registry.active_manifest_path(initial_id).is_ok(),
    }))?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn signed_package(
    id: &str,
    version: &str,
    wasm: &std::path::Path,
    signing_key: &ed25519_dalek::SigningKey,
    key_id: &str,
) -> anyhow::Result<ming_app_host::package::Package> {
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use ed25519_dalek::Signer;
    use ming_app_host::package::{Manifest, Package};
    let manifest = Manifest {
        schema_version: 1,
        id: id.into(),
        version: version.into(),
        sdk_version: 6,
        entry: "main.wasm".into(),
        private_storage: false,
        public_storage: false,
        data_migration: None,
        publisher_key_id: Some(key_id.into()),
        publisher_signature: None,
    };
    let bytes = std::fs::read(wasm)?;
    let unsigned = Package { manifest, bytes };
    let signature = signing_key.sign(&unsigned.signature_message(key_id)?);
    let mut manifest = unsigned.manifest;
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
fn hex_fingerprint(bytes: [u8; 32]) -> String {
    bytes.into_iter().map(|byte| format!("{byte:02x}")).collect()
}
