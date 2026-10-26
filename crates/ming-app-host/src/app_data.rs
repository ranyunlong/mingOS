//! Parent-owned, bounded slot storage. Catalog authority creates grants; guest IDs do not.
//!
//! Linux uses pinned directory descriptors and no-follow relative operations. Windows is a
//! trusted-private-directory prototype: reparse points are checked, but ancestor replacement
//! races and ACL verification require a separate production implementation. No worker should
//! receive this grant or the root path. Revoke is linearized with I/O across all clones.
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    ops::Range,
    path::Path,
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicU64, Ordering},
    },
};

pub const MAX_SLOT_BYTES: usize = 65_536;
pub const MAX_SLOTS: usize = 8;
pub const MAX_TOTAL_BYTES: usize = MAX_SLOTS * MAX_SLOT_BYTES;
static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AppDataError {
    Denied,
    InvalidArgument,
    NotFound,
    Io,
}
impl AppDataError {
    pub const fn code(self) -> i32 {
        match self {
            Self::Denied => -1,
            Self::InvalidArgument => -2,
            Self::NotFound => -3,
            Self::Io => -4,
        }
    }
}
impl std::fmt::Display for AppDataError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Denied => "app data denied",
            Self::InvalidArgument => "invalid app data argument",
            Self::NotFound => "app data not found",
            Self::Io => "unsafe or unavailable app data",
        })
    }
}
impl std::error::Error for AppDataError {}
fn io_error(error: std::io::Error) -> AppDataError {
    if error.kind() == std::io::ErrorKind::NotFound {
        AppDataError::NotFound
    } else {
        AppDataError::Io
    }
}
type Result<T> = std::result::Result<T, AppDataError>;

/// A committed write. False means directory durability was not confirmed; the value is committed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AppDataWrite {
    pub length: usize,
    pub directory_synced: bool,
}

/// Parent-held root capability. Linux reopens generations relative to the retained directory fd.
#[derive(Clone)]
pub struct AppDataRoot { root: Arc<platform::Root> }
impl std::fmt::Debug for AppDataRoot {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.write_str("AppDataRoot(private)") }
}
impl AppDataRoot {
    pub fn open(root: impl AsRef<Path>) -> Result<Self> { Ok(Self { root: Arc::new(platform::Root::open(root.as_ref())?) }) }
    pub fn grant(&self, data_key: &str, slots: &[u8]) -> Result<AppDataGrant> {
        let mask = grant_slots(data_key, slots)?;
        let directory = platform::Directory::open(&self.root, &key_directory_name(data_key))?;
        Ok(AppDataGrant {
            shared: Arc::new(Mutex::new(State {
                directory,
                slots: mask,
                revoked: false,
                #[cfg(test)]
                fail_commit: false,
                #[cfg(test)]
                fail_directory_sync: false,
                #[cfg(test)]
                change_read: None,
            })),
        })
    }
}
/// Structural trusted-catalog validation; performs no I/O and grants no authority.
pub fn validate_grant(data_key: &str, slots: &[u8]) -> Result<()> { grant_slots(data_key, slots).map(|_| ()) }
fn grant_slots(data_key: &str, slots: &[u8]) -> Result<u8> {
        let key = data_key.as_bytes();
        if key.is_empty()
            || key.len() > 64
            || !key[0].is_ascii_alphanumeric()
            || !key
                .iter()
                .all(|b| b.is_ascii_alphanumeric() || *b == b'_' || *b == b'-')
            || slots.is_empty()
            || slots.len() > MAX_SLOTS
        {
            return Err(AppDataError::InvalidArgument);
        }
        let mut mask = 0u8;
        for &slot in slots {
            if !(1..=8).contains(&slot) || mask & (1 << (slot - 1)) != 0 {
                return Err(AppDataError::InvalidArgument);
            }
            mask |= 1 << (slot - 1);
        }
        Ok(mask)
}
#[derive(Clone)]
pub struct AppDataGrant {
    shared: Arc<Mutex<State>>,
}
struct State {
    directory: platform::Directory,
    slots: u8,
    revoked: bool,
    #[cfg(test)]
    fail_commit: bool,
    #[cfg(test)]
    fail_directory_sync: bool,
    #[cfg(test)]
    change_read: Option<bool>,
}
impl AppDataGrant {
    /// root must already exist, be absolute and be provisioned by the trusted parent.
    /// data_key: 1..=64 ASCII bytes, [A-Za-z0-9][A-Za-z0-9_-]*; slots are explicit and unique.
    /// Separate open calls have independent revocation, even when reopening the same data key.
    pub fn open(root: impl AsRef<Path>, data_key: &str, slots: &[u8]) -> Result<Self> {
        validate_grant(data_key, slots)?;
        AppDataRoot::open(root)?.grant(data_key, slots)
    }
    fn lock(&self, slot: i32) -> Result<MutexGuard<'_, State>> {
        let state = self.shared.lock().map_err(|_| AppDataError::Denied)?;
        if state.revoked {
            return Err(AppDataError::Denied);
        }
        valid_slot(slot)?;
        if state.slots & (1 << (slot - 1)) == 0 {
            return Err(AppDataError::Denied);
        }
        Ok(state)
    }
    /// After return, no clone can start further I/O. Already-running I/O completes before return.
    pub fn revoke(&self) {
        let mut state = self
            .shared
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        state.revoked = true;
    }
    pub fn size(&self, slot: i32) -> Result<usize> {
        let state = self.lock(slot)?;
        let file = state.directory.read_file(&slot_name(slot))?;
        let before = platform::stamp(&file)?;
        state
            .directory
            .unchanged(&slot_name(slot), &file, &before)?;
        Ok(before.length() as usize)
    }
    /// Copies a stable snapshot. offset beyond EOF yields an empty buffer, up to 65536.
    pub fn read(&self, slot: i32, offset: i32, capacity: i32) -> Result<Vec<u8>> {
        #[allow(unused_mut)]
        let mut state = self.lock(slot)?;
        valid_read(offset, capacity)?;
        let name = slot_name(slot);
        let mut file = state.directory.read_file(&name)?;
        let before = platform::stamp(&file)?;
        // Read the entire bounded value twice: coarse filesystem timestamps alone cannot
        // detect a same-length in-place overwrite, including outside the requested slice.
        let mut snapshot = vec![0u8; before.length() as usize];
        file.read_exact(&mut snapshot).map_err(io_error)?;
        #[cfg(test)]
        if let Some(grow) = state.change_read.take() {
            let mut writer = state.directory.test_open_writer(&name)?;
            writer
                .seek(if grow {
                    SeekFrom::End(0)
                } else {
                    SeekFrom::Start(0)
                })
                .map_err(io_error)?;
            writer
                .write_all(if grow { b"changed" } else { b"new" })
                .map_err(io_error)?;
            writer.sync_all().map_err(io_error)?;
        }
        file.seek(SeekFrom::Start(0)).map_err(io_error)?;
        let mut verification = vec![0u8; snapshot.len()];
        file.read_exact(&mut verification).map_err(io_error)?;
        if snapshot != verification {
            return Err(AppDataError::Io);
        }
        state.directory.unchanged(&name, &file, &before)?;
        let start = (offset as usize).min(snapshot.len());
        let end = (start + capacity as usize).min(snapshot.len());
        Ok(snapshot[start..end].to_vec())
    }
    /// All Err outcomes precede atomic replacement and preserve the old slot value.
    /// Directory sync happens after the commit; its result is reported without pretending rollback.
    pub fn write(&self, slot: i32, bytes: &[u8]) -> Result<AppDataWrite> {
        #[allow(unused_mut)]
        let mut state = self.lock(slot)?;
        if bytes.len() > MAX_SLOT_BYTES {
            return Err(AppDataError::InvalidArgument);
        }
        let name = slot_name(slot);
        state.directory.check_target(&name)?;
        // create_new/O_EXCL means only this invocation owns the temporary name it removes.
        let (temporary, mut file) = state.directory.new_temp()?;
        let result = (|| {
            file.write_all(bytes).map_err(io_error)?;
            file.sync_all().map_err(io_error)?;
            state.directory.check_target(&name)?;
            #[cfg(test)]
            if std::mem::take(&mut state.fail_commit) {
                return Err(AppDataError::Io);
            }
            state.directory.replace(&temporary, &name)
        })();
        drop(file);
        if let Err(error) = result {
            state.directory.remove_temp(&temporary);
            return Err(error);
        }
        #[cfg(test)]
        if std::mem::take(&mut state.fail_directory_sync) {
            return Ok(AppDataWrite {
                length: bytes.len(),
                directory_synced: false,
            });
        }
        Ok(AppDataWrite {
            length: bytes.len(),
            directory_synced: state.directory.sync(),
        })
    }
    /// Ok(bool) means committed deletion; bool reports directory sync. Missing is NOT_FOUND.
    pub fn delete(&self, slot: i32) -> Result<bool> {
        let state = self.lock(slot)?;
        let name = slot_name(slot);
        let file = state.directory.read_file(&name)?;
        let before = platform::stamp(&file)?;
        state.directory.unchanged(&name, &file, &before)?;
        state.directory.delete(&name)?;
        Ok(state.directory.sync())
    }
    #[cfg(test)]
    pub fn fail_next_commit(&self) {
        self.shared.lock().unwrap().fail_commit = true;
    }
    #[cfg(test)]
    pub fn fail_next_directory_sync(&self) {
        self.shared.lock().unwrap().fail_directory_sync = true;
    }
    #[cfg(all(test, target_os = "linux"))]
    pub fn change_during_next_read_for_test(&self, grow: bool) {
        self.shared.lock().unwrap().change_read = Some(grow);
    }
}
fn valid_slot(slot: i32) -> Result<()> {
    if (1..=8).contains(&slot) {
        Ok(())
    } else {
        Err(AppDataError::InvalidArgument)
    }
}
fn valid_read(offset: i32, capacity: i32) -> Result<()> {
    if (0..=MAX_SLOT_BYTES as i32).contains(&offset)
        && (0..=MAX_SLOT_BYTES as i32).contains(&capacity)
    {
        Ok(())
    } else {
        Err(AppDataError::InvalidArgument)
    }
}
fn slot_name(slot: i32) -> String {
    format!("slot-{slot}")
}
fn memory_range(pointer: i32, length: i32, memory_length: usize) -> Result<Range<usize>> {
    if !(0..=MAX_SLOT_BYTES as i32).contains(&length) {
        return Err(AppDataError::InvalidArgument);
    }
    let start = pointer as u32 as usize;
    let end = start
        .checked_add(length as usize)
        .ok_or(AppDataError::InvalidArgument)?;
    if end > memory_length {
        return Err(AppDataError::InvalidArgument);
    }
    Ok(start..end)
}
/// Call in the parent/engine before any host/RPC invocation or guest memory access.
pub fn validate_read(
    slot: i32,
    offset: i32,
    outptr: i32,
    capacity: i32,
    memory_length: usize,
) -> Result<Range<usize>> {
    valid_slot(slot)?;
    valid_read(offset, capacity)?;
    memory_range(outptr, capacity, memory_length)
}
/// Pointer is a wasm32 unsigned address encoded as i32. Zero length still requires a valid range.
pub fn validate_write(
    slot: i32,
    pointer: i32,
    length: i32,
    memory_length: usize,
) -> Result<Range<usize>> {
    valid_slot(slot)?;
    memory_range(pointer, length, memory_length)
}
// Encoding is injective even on case-insensitive filesystems and avoids DOS device names.
// The prefix and lowercase hex keep every filesystem component portable and below 255 bytes.
fn key_directory_name(key: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut name = String::with_capacity(4 + key.len() * 2);
    name.push_str("key-");
    for byte in key.bytes() {
        name.push(HEX[(byte >> 4) as usize] as char);
        name.push(HEX[(byte & 15) as usize] as char);
    }
    name
}
fn temp_name() -> String {
    format!(
        ".ming-data-{}-{}",
        std::process::id(),
        TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed)
    )
}

#[cfg(target_os = "linux")]
mod platform {
    use super::*;
    use std::{
        ffi::CString,
        fs::Metadata,
        os::{
            fd::{AsRawFd, FromRawFd},
            unix::{ffi::OsStrExt, fs::MetadataExt},
        },
        path::Component,
    };
    pub struct Root(File);
    pub struct Directory(File);
    #[derive(PartialEq, Eq)]
    pub struct Stamp {
        dev: u64,
        ino: u64,
        size: u64,
        mtime: (i64, i64),
        ctime: (i64, i64),
        mode: u32,
        uid: u32,
        nlink: u64,
    }
    impl Stamp {
        pub fn length(&self) -> u64 {
            self.size
        }
    }
    fn cstring(value: &std::ffi::OsStr) -> Result<CString> {
        CString::new(value.as_bytes()).map_err(|_| AppDataError::InvalidArgument)
    }
    fn open_at(dir: &File, name: &str, flags: i32, mode: u32) -> Result<File> {
        let name = CString::new(name).map_err(|_| AppDataError::InvalidArgument)?;
        // SAFETY: parent fd and terminated name are live; a successful fd is uniquely owned.
        let fd = unsafe {
            libc::openat(
                dir.as_raw_fd(),
                name.as_ptr(),
                flags | libc::O_CLOEXEC | libc::O_NOFOLLOW | libc::O_NONBLOCK,
                mode,
            )
        };
        if fd < 0 {
            Err(io_error(std::io::Error::last_os_error()))
        } else {
            Ok(unsafe { File::from_raw_fd(fd) })
        }
    }
    fn private_directory(file: &File) -> Result<()> {
        let m = file.metadata().map_err(io_error)?;
        if !m.is_dir() || m.uid() != unsafe { libc::geteuid() } || m.mode() & 0o077 != 0 {
            return Err(AppDataError::Io);
        }
        Ok(())
    }
    fn file_stamp(m: Metadata) -> Result<Stamp> {
        if !m.is_file()
            || m.len() > MAX_SLOT_BYTES as u64
            || m.nlink() != 1
            || m.uid() != unsafe { libc::geteuid() }
            || m.mode() & 0o077 != 0
        {
            return Err(AppDataError::Io);
        }
        Ok(Stamp {
            dev: m.dev(),
            ino: m.ino(),
            size: m.len(),
            mtime: (m.mtime(), m.mtime_nsec()),
            ctime: (m.ctime(), m.ctime_nsec()),
            mode: m.mode(),
            uid: m.uid(),
            nlink: m.nlink(),
        })
    }
    pub fn stamp(file: &File) -> Result<Stamp> {
        file_stamp(file.metadata().map_err(io_error)?)
    }
    impl Root {
        pub fn open(root: &Path) -> Result<Self> {
            if !root.is_absolute() {
                return Err(AppDataError::InvalidArgument);
            }
            let start = CString::new("/").unwrap();
            let fd = unsafe {
                libc::open(
                    start.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                )
            };
            if fd < 0 {
                return Err(AppDataError::Io);
            }
            let mut dir = unsafe { File::from_raw_fd(fd) };
            for part in root.components() {
                match part {
                    Component::RootDir => (),
                    Component::Normal(name) => {
                        let name = cstring(name)?;
                        let fd = unsafe {
                            libc::openat(
                                dir.as_raw_fd(),
                                name.as_ptr(),
                                libc::O_RDONLY
                                    | libc::O_DIRECTORY
                                    | libc::O_CLOEXEC
                                    | libc::O_NOFOLLOW
                                    | libc::O_NONBLOCK,
                            )
                        };
                        if fd < 0 {
                            return Err(io_error(std::io::Error::last_os_error()));
                        }
                        dir = unsafe { File::from_raw_fd(fd) };
                    }
                    _ => return Err(AppDataError::InvalidArgument),
                }
            }
            private_directory(&dir)?;
            Ok(Self(dir))
        }
    }
    impl Directory {
        pub fn open(root: &Root, key: &str) -> Result<Self> {
            let dir = &root.0;
            private_directory(dir)?;
            let key_c = CString::new(key).unwrap();
            let status = unsafe { libc::mkdirat(dir.as_raw_fd(), key_c.as_ptr(), 0o700) };
            if status != 0 && std::io::Error::last_os_error().raw_os_error() != Some(libc::EEXIST) {
                return Err(AppDataError::Io);
            }
            let data = open_at(&dir, key, libc::O_RDONLY | libc::O_DIRECTORY, 0)?;
            private_directory(&data)?;
            // Persist a newly created namespace before allowing any slot write.
            if status == 0 {
                dir.sync_all().map_err(io_error)?;
            }
            Ok(Self(data))
        }
        pub fn read_file(&self, name: &str) -> Result<File> {
            private_directory(&self.0)?;
            let file = open_at(&self.0, name, libc::O_RDONLY, 0)?;
            stamp(&file)?;
            Ok(file)
        }
        pub fn unchanged(&self, name: &str, file: &File, before: &Stamp) -> Result<()> {
            if stamp(file)? != *before {
                return Err(AppDataError::Io);
            }
            let current = self.read_file(name)?;
            if stamp(&current)? != *before {
                return Err(AppDataError::Io);
            }
            Ok(())
        }
        pub fn check_target(&self, name: &str) -> Result<()> {
            match self.read_file(name) {
                Ok(file) => {
                    let before = stamp(&file)?;
                    self.unchanged(name, &file, &before)
                }
                Err(AppDataError::NotFound) => Ok(()),
                Err(e) => Err(e),
            }
        }
        pub fn new_temp(&self) -> Result<(String, File)> {
            private_directory(&self.0)?;
            for _ in 0..32 {
                let name = temp_name();
                match open_at(
                    &self.0,
                    &name,
                    libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL,
                    0o600,
                ) {
                    Ok(file) => return Ok((name, file)),
                    Err(_) => continue,
                }
            }
            Err(AppDataError::Io)
        }
        pub fn replace(&self, from: &str, to: &str) -> Result<()> {
            private_directory(&self.0)?;
            let from = CString::new(from).unwrap();
            let to = CString::new(to).unwrap();
            if unsafe {
                libc::renameat(
                    self.0.as_raw_fd(),
                    from.as_ptr(),
                    self.0.as_raw_fd(),
                    to.as_ptr(),
                )
            } == 0
            {
                Ok(())
            } else {
                Err(AppDataError::Io)
            }
        }
        pub fn delete(&self, name: &str) -> Result<()> {
            private_directory(&self.0)?;
            let name = CString::new(name).unwrap();
            if unsafe { libc::unlinkat(self.0.as_raw_fd(), name.as_ptr(), 0) } == 0 {
                Ok(())
            } else {
                Err(io_error(std::io::Error::last_os_error()))
            }
        }
        pub fn remove_temp(&self, name: &str) {
            let _ = self.delete(name);
        }
        pub fn sync(&self) -> bool {
            self.0.sync_all().is_ok()
        }
        #[cfg(test)]
        pub fn test_open_writer(&self, name: &str) -> Result<File> {
            open_at(&self.0, name, libc::O_WRONLY, 0)
        }
    }
}

#[cfg(windows)]
mod platform {
    use super::*;
    use std::{
        fs::{self, OpenOptions},
        os::windows::{
            ffi::OsStrExt,
            fs::{MetadataExt, OpenOptionsExt},
            io::AsRawHandle,
        },
        path::{Component, PathBuf, Prefix},
    };
    const REPARSE: u32 = 0x400;
    const OPEN_REPARSE: u32 = 0x00200000;
    #[repr(C)]
    #[derive(Default)]
    struct Info {
        attributes: u32,
        creation: [u32; 2],
        access: [u32; 2],
        write: [u32; 2],
        volume: u32,
        size_high: u32,
        size_low: u32,
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn MoveFileExW(from: *const u16, to: *const u16, flags: u32) -> i32;
        fn GetFileInformationByHandle(file: *mut std::ffi::c_void, info: *mut Info) -> i32;
        fn GetFileType(file: *mut std::ffi::c_void) -> u32;
    }
    pub struct Root { path: PathBuf, _handle: File }
    impl Root {
        pub fn open(path: &Path) -> Result<Self> {
            check_chain(path)?;
            let handle = OpenOptions::new().read(true).share_mode(7).custom_flags(0x02000000 | OPEN_REPARSE).open(path).map_err(io_error)?;
            let m = handle.metadata().map_err(io_error)?;
            if !m.is_dir() || m.file_attributes() & REPARSE != 0 { return Err(AppDataError::Io); }
            Ok(Self { path: path.to_owned(), _handle: handle })
        }
    }
    pub struct Directory(PathBuf);
    #[derive(PartialEq, Eq)]
    pub struct Stamp {
        volume: u32,
        index: (u32, u32),
        size: u64,
        creation: [u32; 2],
        write: [u32; 2],
        attributes: u32,
    }
    impl Stamp {
        pub fn length(&self) -> u64 {
            self.size
        }
    }
    fn check_chain(path: &Path) -> Result<()> {
        if !path.is_absolute() {
            return Err(AppDataError::InvalidArgument);
        }
        let mut prefix = PathBuf::new();
        for part in path.components() {
            match part {
                Component::Prefix(p)
                    if matches!(p.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)) =>
                {
                    prefix.push(part.as_os_str());
                    continue;
                }
                Component::RootDir => {
                    prefix.push(part.as_os_str());
                }
                Component::Normal(_) => {
                    prefix.push(part.as_os_str());
                }
                _ => return Err(AppDataError::InvalidArgument),
            }
            let m = fs::symlink_metadata(&prefix).map_err(io_error)?;
            if !m.is_dir() || m.file_attributes() & REPARSE != 0 {
                return Err(AppDataError::Io);
            }
        }
        Ok(())
    }
    pub fn stamp(file: &File) -> Result<Stamp> {
        let m = file.metadata().map_err(io_error)?;
        let mut info = Info::default();
        if !m.is_file()
            || m.len() > MAX_SLOT_BYTES as u64
            || m.file_attributes() & REPARSE != 0
            || unsafe { GetFileType(file.as_raw_handle()) } != 1
            || unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.links != 1
        {
            return Err(AppDataError::Io);
        }
        Ok(Stamp {
            volume: info.volume,
            index: (info.index_high, info.index_low),
            size: m.len(),
            creation: info.creation,
            write: info.write,
            attributes: info.attributes,
        })
    }
    impl Directory {
        pub fn open(root: &Root, key: &str) -> Result<Self> {
            check_chain(&root.path)?;
            let data = root.path.join(key);
            match fs::create_dir(&data) {
                Ok(()) => (),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
                Err(e) => return Err(io_error(e)),
            }
            check_chain(&data)?;
            Ok(Self(data))
        }
        pub fn read_file(&self, name: &str) -> Result<File> {
            check_chain(&self.0)?;
            let p = self.0.join(name);
            let m = fs::symlink_metadata(&p).map_err(io_error)?;
            if !m.is_file() || m.file_attributes() & REPARSE != 0 || m.len() > MAX_SLOT_BYTES as u64
            {
                return Err(AppDataError::Io);
            }
            let file = OpenOptions::new()
                .read(true)
                .share_mode(7)
                .custom_flags(OPEN_REPARSE)
                .open(p)
                .map_err(io_error)?;
            stamp(&file)?;
            Ok(file)
        }
        pub fn unchanged(&self, name: &str, file: &File, before: &Stamp) -> Result<()> {
            if stamp(file)? != *before || stamp(&self.read_file(name)?)? != *before {
                return Err(AppDataError::Io);
            }
            Ok(())
        }
        pub fn check_target(&self, name: &str) -> Result<()> {
            match self.read_file(name) {
                Ok(file) => {
                    let before = stamp(&file)?;
                    self.unchanged(name, &file, &before)
                }
                Err(AppDataError::NotFound) => Ok(()),
                Err(e) => Err(e),
            }
        }
        pub fn new_temp(&self) -> Result<(String, File)> {
            check_chain(&self.0)?;
            for _ in 0..32 {
                let name = temp_name();
                match OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .share_mode(7)
                    .custom_flags(OPEN_REPARSE)
                    .open(self.0.join(&name))
                {
                    Ok(file) => return Ok((name, file)),
                    Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                    Err(e) => return Err(io_error(e)),
                }
            }
            Err(AppDataError::Io)
        }
        pub fn replace(&self, from: &str, to: &str) -> Result<()> {
            check_chain(&self.0)?;
            let wide = |p: PathBuf| {
                p.as_os_str()
                    .encode_wide()
                    .chain(Some(0))
                    .collect::<Vec<u16>>()
            };
            let from = wide(self.0.join(from));
            let to = wide(self.0.join(to));
            // Same-directory local-volume rename only; COPY_ALLOWED is deliberately absent.
            if unsafe { MoveFileExW(from.as_ptr(), to.as_ptr(), 1) } != 0 {
                Ok(())
            } else {
                Err(AppDataError::Io)
            }
        }
        pub fn delete(&self, name: &str) -> Result<()> {
            check_chain(&self.0)?;
            fs::remove_file(self.0.join(name)).map_err(io_error)
        }
        pub fn remove_temp(&self, name: &str) {
            let _ = self.delete(name);
        }
        pub fn sync(&self) -> bool {
            false
        }
        #[cfg(test)]
        pub fn test_open_writer(&self, name: &str) -> Result<File> {
            OpenOptions::new()
                .write(true)
                .open(self.0.join(name))
                .map_err(io_error)
        }
    }
}

#[cfg(not(any(target_os = "linux", windows)))]
compile_error!("App data storage supports Linux and the documented Windows prototype only");
