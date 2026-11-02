#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("linux only");
    std::process::exit(1);
}

#[cfg(target_os = "linux")]
fn main() -> anyhow::Result<()> {
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use ed25519_dalek::{Signer, SigningKey};
    use ming_app_host::{package::{load_package, Manifest}, package_install::PackageInstallRegistry, publisher::PublisherTrustStore};
    use std::{env, fs, os::unix::fs::PermissionsExt, path::PathBuf};

    let mut args = env::args_os().skip(1);
    let payload = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("payload required"))?);
    let install_root = PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("install root required"))?);
    if args.next().is_some() {
        anyhow::bail!("unexpected arguments");
    }
    fs::create_dir_all(&install_root)?;
    fs::set_permissions(&install_root, fs::Permissions::from_mode(0o700))?;

    let key_id = "mingos-dev-2026";
    let signing_key = SigningKey::from_bytes(&[7u8; 32]);
    PublisherTrustStore::provision(&install_root, key_id, signing_key.verifying_key().as_bytes())?;

    for number in ["1", "2"] {
        let package_root = payload.join("packages").join(number);
        let manifest_path = package_root.join("app.json");
        let mut manifest: Manifest = serde_json::from_slice(&fs::read(&manifest_path)?)?;
        manifest.publisher_key_id = Some(key_id.to_owned());
        manifest.publisher_signature = None;
        let bytes = fs::read(package_root.join(&manifest.entry))?;
        let unsigned = ming_app_host::package::Package { manifest, bytes };
        let signature = signing_key.sign(&unsigned.signature_message(key_id)?);
        let mut signed_manifest = unsigned.manifest;
        signed_manifest.publisher_signature = Some(STANDARD.encode(signature.to_bytes()));
        fs::write(&manifest_path, serde_json::to_vec_pretty(&signed_manifest)?)?;
    }

    let registry = PackageInstallRegistry::open_verified(&install_root)?;
    for number in ["1", "2"] {
        let manifest_path = payload.join("packages").join(number).join("app.json");
        registry.install_from_manifest(&manifest_path)?;
        let package = load_package(&manifest_path)?;
        registry.verify_package(&package)?;
    }
    println!("prepared signed payload and verified install registry");
    Ok(())
}
