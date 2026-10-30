//! Real process-crash checks for the production Parent AppData API.
//! This tool does not exercise Wasm, boot recovery, or power-loss durability.
#[cfg(target_os = "linux")]
mod probe {
    use anyhow::{Context, Result, bail, ensure};
    use ming_app_host::app_data::{AppDataRoot, MAX_SLOT_BYTES};
    use serde_json::{Value, json};
    use std::{
        collections::BTreeMap,
        fs::{self, OpenOptions},
        io::Write,
        os::unix::{fs::{MetadataExt, OpenOptionsExt}, process::ExitStatusExt},
        path::{Path, PathBuf},
        process::{Child, Command, ExitStatus, Stdio},
        thread,
        time::{Duration, Instant},
    };

    const MAX_WRITES: u64 = 128;
    const WAIT: Duration = Duration::from_secs(2);

    struct OwnedChild {
        process: Child,
        reaped: bool,
    }
    impl OwnedChild {
        fn spawn(args: &[&std::ffi::OsStr]) -> Result<Self> {
            Ok(Self {
                process: Command::new(std::env::current_exe()?)
                    .args(args)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::inherit())
                    .spawn()?,
                reaped: false,
            })
        }
        fn poll(&mut self) -> Result<Option<ExitStatus>> {
            let status = self.process.try_wait()?;
            self.reaped |= status.is_some();
            Ok(status)
        }
        fn wait(&mut self) -> Result<ExitStatus> {
            let limit = Instant::now() + WAIT;
            loop {
                if let Some(status) = self.poll()? { return Ok(status); }
                ensure!(Instant::now() < limit, "CHILD_REAP_UNCONFIRMED: pid={}", self.process.id());
                thread::sleep(Duration::from_millis(1));
            }
        }
    }
    impl Drop for OwnedChild {
        fn drop(&mut self) {
            if !self.reaped {
                let _ = self.process.kill();
                if let Err(error) = self.wait() {
                    // An outer timeout/container owns the remaining cleanup.
                    // An unconfirmed reap must never be treated as success.
                    eprintln!("CHILD_CLEANUP_UNCONFIRMED: {error:#}");
                }
            }
        }
    }

    fn payload(sequence: u64) -> Vec<u8> {
        let mut bytes: Vec<_> = (0..MAX_SLOT_BYTES)
            .map(|index| ((index as u64 * 31 + sequence * 17) % 251) as u8)
            .collect();
        bytes[..8].copy_from_slice(b"MINGCRSH");
        bytes[8..16].copy_from_slice(&sequence.to_le_bytes());
        bytes
    }
    fn sequence(bytes: &[u8]) -> Result<u64> {
        ensure!(bytes.len() == MAX_SLOT_BYTES && &bytes[..8] == b"MINGCRSH", "TORN_SLOT_SIZE_OR_HEADER");
        let sequence = u64::from_le_bytes(bytes[8..16].try_into()?);
        ensure!(sequence <= 100_008, "INVALID_SEQUENCE");
        ensure!(bytes == payload(sequence), "TORN_SLOT_PAYLOAD");
        Ok(sequence)
    }
    fn key_path(root: &Path, key: &str) -> PathBuf {
        root.join(format!("key-{}", key.bytes().map(|b| format!("{b:02x}")).collect::<String>()))
    }
    fn ack_path(root: &Path, pid: u32) -> PathBuf {
        root.join(format!(".commit-{pid}"))
    }
    fn file_receipt(path: &Path) -> Result<Value> {
        let metadata = fs::symlink_metadata(path)?;
        ensure!(metadata.is_file() && metadata.nlink() == 1, "UNSAFE_TEST_FILE");
        ensure!(metadata.mode() & 0o777 == 0o600, "NONPRIVATE_TEST_FILE");
        ensure!(metadata.uid() == unsafe { libc::geteuid() }, "FOREIGN_TEST_FILE");
        Ok(json!({"bytes": metadata.len(), "mode": metadata.mode() & 0o777,
            "uid": metadata.uid(), "gid": metadata.gid(), "links": metadata.nlink()}))
    }

    fn orphan_snapshots(directory: &Path) -> Result<BTreeMap<String, (Value, Vec<u8>)>> {
        let mut files = BTreeMap::new();
        let mut entries = fs::read_dir(directory)?;
        for entry in entries.by_ref().take(132) {
            let entry = entry?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.starts_with(".ming-data-") {
                let receipt = file_receipt(&entry.path())?;
                ensure!(receipt["bytes"].as_u64().context("orphan size")? <= MAX_SLOT_BYTES as u64, "OVERSIZED_ORPHAN");
                files.insert(name, (receipt, fs::read(entry.path())?));
            }
        }
        ensure!(entries.next().is_none(), "TOO_MANY_TEST_DIRECTORY_ENTRIES");
        Ok(files)
    }

    fn writer(root: &Path, key: &str, mode: &str) -> Result<()> {
        let grant = AppDataRoot::open(root)?.grant(key, &[1])?;
        match mode {
            "during-write" => {
                for sequence in 1..=MAX_WRITES {
                    let receipt = grant.write(1, &payload(sequence))?;
                    ensure!(receipt.directory_synced, "DIRECTORY_SYNC_UNCONFIRMED");
                }
                bail!("WRITER_FINISHED_BEFORE_CRASH");
            }
            "after-commit" => {
                let receipt = grant.write(1, &payload(1))?;
                ensure!(receipt.directory_synced, "DIRECTORY_SYNC_UNCONFIRMED");
                let mut ack = OpenOptions::new().write(true).create_new(true).mode(0o600)
                    .open(ack_path(root, std::process::id()))?;
                ack.write_all(&1u64.to_le_bytes())?;
                ack.sync_all()?;
                loop { thread::park_timeout(Duration::from_secs(1)); }
            }
            _ => bail!("INVALID_CRASH_MODE"),
        }
    }
    fn recover(root: &Path, key: &str, next: u64) -> Result<()> {
        let grant = AppDataRoot::open(root)?.grant(key, &[1, 2])?;
        ensure!(sequence(&grant.read(1, 0, MAX_SLOT_BYTES as i32)?)? <= MAX_WRITES, "INVALID_CRASH_VALUE");
        let receipt = grant.write(2, &payload(next))?;
        ensure!(receipt.directory_synced, "RECOVERY_SYNC_UNCONFIRMED");
        ensure!(grant.read(2, 0, MAX_SLOT_BYTES as i32)? == payload(next), "RECOVERY_READBACK");
        Ok(())
    }

    fn run_case(root: &Path, mode: &str, index: usize, origin: Instant) -> Result<Value> {
        let key = format!("crash-{mode}-{index}");
        let sibling_key = format!("sibling-{mode}-{index}");
        let authority = AppDataRoot::open(root)?;
        let grant = authority.grant(&key, &[1])?;
        let sibling = authority.grant(&sibling_key, &[1])?;
        ensure!(grant.write(1, &payload(0))?.directory_synced, "INITIAL_SYNC_UNCONFIRMED");
        let sentinel = format!("unchanged-{index}").into_bytes();
        ensure!(sibling.write(1, &sentinel)?.directory_synced, "SIBLING_SYNC_UNCONFIRMED");

        let mut child = OwnedChild::spawn(&["__writer".as_ref(), root.as_os_str(), key.as_ref(), mode.as_ref()])?;
        let pid = child.process.id();
        let temporary_prefix = format!(".ming-data-{pid}-");
        let directory = key_path(root, &key);
        let limit = Instant::now() + WAIT;
        let mut temporary = None;
        let mut temporary_bytes = None;
        loop {
            if let Some(status) = child.poll()? { bail!("CRASH_CHILD_EARLY_EXIT: {status}"); }
            ensure!(Instant::now() < limit, "CRASH_OBSERVATION_TIMEOUT");
            if mode == "during-write" {
                for entry in fs::read_dir(&directory)?.take(132) {
                    let entry = entry?;
                    if entry.file_name().to_string_lossy().starts_with(&temporary_prefix) {
                        temporary_bytes = entry.metadata().ok().map(|metadata| metadata.len());
                        temporary = Some(entry.file_name().to_string_lossy().into_owned());
                        break;
                    }
                }
                if temporary.is_some() { break; }
            } else if fs::read(ack_path(root, pid)).ok().as_deref() == Some(&1u64.to_le_bytes()) {
                break;
            }
            thread::sleep(Duration::from_micros(100));
        }
        let observed_us = origin.elapsed().as_micros();
        child.process.kill()?;
        let kill_requested_us = origin.elapsed().as_micros();
        let status = child.wait()?;
        let reaped_us = origin.elapsed().as_micros();
        ensure!(status.signal() == Some(libc::SIGKILL), "CRASH_WAS_NOT_SIGKILL: {status}");
        ensure!(!Path::new(&format!("/proc/{pid}")).exists(), "CRASH_CHILD_NOT_REAPED");

        let orphans_before = orphan_snapshots(&directory)?;
        let fresh = AppDataRoot::open(root)?.grant(&key, &[1, 2])?;
        let value_after_crash = fresh.read(1, 0, MAX_SLOT_BYTES as i32)?;
        let observed_sequence = sequence(&value_after_crash)?;
        ensure!(observed_sequence <= MAX_WRITES, "UNKNOWN_CRASH_VALUE");
        if mode == "after-commit" { ensure!(observed_sequence == 1, "ACKNOWLEDGED_COMMIT_LOST"); }
        ensure!(sibling.read(1, 0, MAX_SLOT_BYTES as i32)? == sentinel, "SIBLING_CHANGED_ON_CRASH");

        // A new OS process reopens the root, reads slot 1 and writes slot 2.
        // Keeping slot 1 preserves the actual post-crash value for offline audit.
        let next = 100_000 + index as u64;
        let next_arg = next.to_string();
        let mut restarted = OwnedChild::spawn(&["__recover".as_ref(), root.as_os_str(), key.as_ref(), next_arg.as_ref()])?;
        let restarted_pid = restarted.process.id();
        let limit = Instant::now() + WAIT;
        let restarted_status = loop {
            if let Some(status) = restarted.poll()? { break status; }
            ensure!(Instant::now() < limit, "RECOVERY_CHILD_TIMEOUT");
            thread::sleep(Duration::from_millis(1));
        };
        let recovery_reaped_us = origin.elapsed().as_micros();
        ensure!(restarted_status.success(), "RECOVERY_CHILD_FAILED: {restarted_status}");
        ensure!(!Path::new(&format!("/proc/{restarted_pid}")).exists(), "RECOVERY_CHILD_NOT_REAPED");
        ensure!(fresh.read(1, 0, MAX_SLOT_BYTES as i32)? == value_after_crash, "RECOVERY_CHANGED_CRASH_SLOT");
        ensure!(fresh.read(2, 0, MAX_SLOT_BYTES as i32)? == payload(next), "RECOVERY_SLOT_MISMATCH");
        ensure!(sibling.read(1, 0, MAX_SLOT_BYTES as i32)? == sentinel, "RECOVERY_CHANGED_SIBLING");

        let orphans_after = orphan_snapshots(&directory)?;
        ensure!(orphans_before == orphans_after, "RECOVERY_CHANGED_ORPHAN_BYTES_OR_METADATA");
        let mut orphans = Vec::new();
        for (name, (metadata, _)) in &orphans_after {
            orphans.push(json!({"name":name, "metadata":metadata}));
        }
        let orphan_before_receipts: Vec<_> = orphans_before.iter()
            .map(|(name, (metadata, bytes))| json!({"name":name, "bytes":bytes.len(), "metadata":metadata})).collect();
        Ok(json!({"index":index, "mode":mode, "key":key, "siblingKey":sibling_key,
            "crashPid":pid, "recoveryPid":restarted_pid, "signal":status.signal(), "reaped":true,
            "recoveryExitCode":restarted_status.code(), "recoveryReaped":true, "recoveryReapedAtMicros":recovery_reaped_us,
            "observedAtMicros":observed_us, "killRequestedAtMicros":kill_requested_us, "reapedAtMicros":reaped_us,
            "temporaryObserved":temporary, "temporaryBytesAtObservation":temporary_bytes,
            "commitAckObserved":mode=="after-commit", "observedSequence":observed_sequence,
            "restartSequence":next, "siblingUnchanged":true, "freshProcessRecovery":true,
            "crashSlot":file_receipt(&directory.join("slot-1"))?,
            "recoverySlot":file_receipt(&directory.join("slot-2"))?,
            "siblingSlot":file_receipt(&key_path(root, &sibling_key).join("slot-1"))?,
            "orphansBeforeRecovery":orphan_before_receipts, "orphanBytesUnchanged":true,
            "orphansPreserved":orphans}))
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn unreaped_child_deadline_is_reported_without_fabricating_an_exit() {
            let mut child = OwnedChild {
                process: Command::new("sleep").arg("5")
                    .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
                    .spawn().expect("bounded single-process fixture"),
                reaped: false,
            };
            let error = child.wait().expect_err("A live child must reach the reap deadline");
            assert!(error.to_string().contains("CHILD_REAP_UNCONFIRMED"), "{error:#}");
            assert!(!child.reaped, "A timeout cannot invent an exit status");
            child.process.kill().unwrap();
            let status = child.wait().unwrap();
            assert_eq!(status.signal(), Some(libc::SIGKILL));
            assert!(child.reaped);
        }
    }

    pub fn main() -> Result<()> {
        let args: Vec<_> = std::env::args_os().skip(1).collect();
        if args.len() == 4 && args[0] == "__writer" {
            return writer(Path::new(&args[1]), args[2].to_str().context("key")?, args[3].to_str().context("mode")?);
        }
        if args.len() == 4 && args[0] == "__recover" {
            return recover(Path::new(&args[1]), args[2].to_str().context("key")?, args[3].to_str().context("sequence")?.parse()?);
        }
        ensure!(args.len() == 1, "usage: app_data_crash ABSOLUTE_EMPTY_PRIVATE_ROOT");
        let root = Path::new(&args[0]);
        AppDataRoot::open(root)?;
        ensure!(fs::read_dir(root)?.next().is_none(), "TEST_ROOT_MUST_BE_EMPTY");
        let origin = Instant::now();
        let mut cases = Vec::new();
        for mode in ["during-write", "after-commit"] {
            for round in 0..4 {
                let index = cases.len();
                match run_case(root, mode, index, origin) {
                    Ok(case) => cases.push(case),
                    Err(error) => {
                        println!("{}", serde_json::to_string_pretty(&json!({"status":"failed", "cases":cases,
                            "failedMode":mode, "round":round, "error":format!("{error:#}")}))?);
                        return Err(error);
                    }
                }
            }
        }
        println!("{}", serde_json::to_string_pretty(&json!({"schemaVersion":1, "status":"passed", "total":cases.len(),
            "scope":"Production Parent AppData API, real Linux SIGKILL and fresh-process reopen; no Wasm, strict boot or power-cut qualification",
            "clock":"driver std::time::Instant; crash requested after external observation, exact precommit phase is not guaranteed",
            "root":root, "uid":unsafe { libc::geteuid() }, "cases":cases}))?);
        Ok(())
    }
}

#[cfg(target_os = "linux")]
fn main() {
    if let Err(error) = probe::main() {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("LINUX_CRASH_PROBE_REQUIRED");
    std::process::exit(1);
}
