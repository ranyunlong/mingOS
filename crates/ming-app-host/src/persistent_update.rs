//! Strict parent-owned persistent-update records and read-only transaction checks.
use crate::app_data::{PrivateUpdateBaseline, valid_package_id};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use crate::{app_data::PackageStorageAuthority, package::{Package, load_package},
    package_install::PackageInstallRegistry, persistent_install::{self, ContentSnapshot, sha256}};
use anyhow::Context;
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs, os::unix::fs::MetadataExt, path::{Path, PathBuf}};

pub const UPDATE_PENDING_NAME: &str = "desktop-update-pending-v1.json";
pub(crate) const INNER_JOURNAL_NAME: &str = "update-journal-v1";
pub const MAX_UPDATE_RECORD_BYTES: usize = 4096;

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
fn preserved_content(root: &Path, files: &BTreeMap<String,Vec<u8>>, exclude: Option<&Path>) -> Result<String> {
    fn directories(root:&Path, path:&Path, exclude:Option<&Path>, entries:&mut Vec<PreservedEntry>) -> Result<()> {
        ensure!(entries.len()<=512 && path.strip_prefix(root)?.components().count()<=3,"UPDATE_HISTORY_LIMIT");
        if Some(path)==exclude { return Ok(()); }
        let metadata=fs::symlink_metadata(path)?;
        ensure!(metadata.is_dir() && !metadata.file_type().is_symlink(), "UPDATE_HISTORY_DIRECTORY");
        entries.push(PreservedEntry { path:path.strip_prefix(root)?.to_str().context("UPDATE_HISTORY_PATH")?.into(),kind:"directory",length:0,sha256:None });
        for child in fs::read_dir(path)?.take(257) { let child=child?.path(); if fs::symlink_metadata(&child)?.is_dir() { directories(root,&child,exclude,entries)?; } }
        Ok(())
    }
    let mut entries=Vec::new(); directories(root,&root.join("packages"),exclude,&mut entries)?;
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
        preserved_content_sha256:preserved_content(registry.root_path(),&files,None)?,old,new,
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
pub(crate) struct UpdateSnapshotAccess<'a> { outer:&'a [u8],inner:Option<&'a [u8]> }
impl UpdateSnapshotAccess<'_> {
    pub(crate) fn markers(&self)->Vec<(&'static str,&[u8])> {
        let mut markers=vec![(UPDATE_PENDING_NAME,self.outer)];
        if let Some(inner)=self.inner {markers.push((INNER_JOURNAL_NAME,inner));} markers
    }
}
#[derive(Deserialize)]
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
/// Checks content and authority bindings only. The coordinator must separately
/// validate private layout, backup state and its launch/mutation gate under the
/// same operation mutex before using this observation to authorize any mutation.
pub(crate) fn validate_transaction_snapshot(registry:&PackageInstallRegistry,authority:&PackageStorageAuthority,data_root:&Path,
    record:&UpdateRecord,outer:&[u8],inner:Option<&[u8]>) -> Result<ContentSnapshot> {
    ensure!(parse_update_record(outer)?==*record,"UPDATE_OUTER_IDENTITY");
    let bound_inner=validate_inner(record,inner)?;
    let access=UpdateSnapshotAccess {outer,inner};
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
    ensure!(preserved_content(registry.root_path(),&files,excluded)?==record.preserved_content_sha256,"UPDATE_PRESERVED_CONTENT");
    let (binding,slot)=authority.observe_update_authority(data_root)?;
    ensure!(binding==record.data_root_binding_sha256
        && slot.map(|(bytes,sha256)|PublicPolicySlotDigest {bytes,sha256})==record.public_policy_slot,"UPDATE_AUTHORITY_BINDING");
    let (_,again)=persistent_install::snapshot_for_update(registry,&access)?;
    ensure!(files==again,"UPDATE_CONTENT_CHANGED");
    Ok(content)
}
