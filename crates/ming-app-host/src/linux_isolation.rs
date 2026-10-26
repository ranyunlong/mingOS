//! Stage-8 Linux/x86_64 worker isolation prototype; NOT production-secure.
//! Call only in a dedicated, freshly exec'd, single-threaded worker, after trusted
//! Init but before any Wasm parsing/JIT. On ANY apply error the caller MUST exit.
//! Parent must keep the unreaped Child, join its owned cgroup before sending Init,
//! switch CPU only on trusted Ready, then kill/wait/cleanup. No silent fallback.
use std::{fmt, io, process::Child};
pub const CGROUP_ROOT: &str = "/sys/fs/cgroup/mingos";
pub const PRIVATE_ROOT: &str = "/run/mingos-workers";
pub const STARTUP_CPU_MAX: &str = "100000 100000";
pub const STEADY_CPU_MAX: &str = "25000 100000";
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WorkerKey {
    app: u16,
    generation: u64,
}
impl WorkerKey {
    pub fn new(app: u16, generation: u64) -> Result<Self> {
        if app == 0 || generation == 0 {
            return Err(invalid("worker key"));
        }
        Ok(Self { app, generation })
    }
    pub fn app(self) -> u16 {
        self.app
    }
    pub fn generation(self) -> u64 {
        self.generation
    }
    pub fn name(self) -> String {
        format!("app-{}-g-{}", self.app, self.generation)
    }
}
/// No unmeasured production default: caller explicitly selects a candidate.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Limits {
    memory_bytes: u64,
    pids: u32,
}
impl Limits {
    pub fn new(memory_bytes: u64, pids: u32) -> Result<Self> {
        if ![512 << 20, 1024 << 20].contains(&memory_bytes) || ![32, 64].contains(&pids) {
            return Err(invalid("resource candidates"));
        }
        Ok(Self { memory_bytes, pids })
    }
    pub fn memory_bytes(self) -> u64 {
        self.memory_bytes
    }
    pub fn pids(self) -> u32 {
        self.pids
    }
}
#[derive(Clone, Debug)]
pub struct ChildPolicy {
    key: WorkerKey,
    uid: u32,
    gid: u32,
    parent: u32,
    pid: u32,
    limits: Limits,
}
impl ChildPolicy {
    pub fn new(
        key: WorkerKey,
        uid: u32,
        gid: u32,
        parent: u32,
        pid: u32,
        limits: Limits,
    ) -> Result<Self> {
        if !(20000..60000).contains(&uid)
            || !(20000..60000).contains(&gid)
            || parent == 0
            || pid <= 1
            || pid == parent
            || pid > i32::MAX as u32
            || parent > i32::MAX as u32
        {
            return Err(invalid("child identity"));
        }
        Ok(Self {
            key,
            uid,
            gid,
            parent,
            pid,
            limits,
        })
    }
    pub fn key(&self) -> WorkerKey {
        self.key
    }
    pub fn uid(&self) -> u32 {
        self.uid
    }
    pub fn gid(&self) -> u32 {
        self.gid
    }
    pub fn expected_parent(&self) -> u32 {
        self.parent
    }
    pub fn worker_pid(&self) -> u32 {
        self.pid
    }
    pub fn limits(&self) -> Limits {
        self.limits
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ErrorKind {
    InvalidPolicy,
    Unsupported,
    Os,
}
#[derive(Debug)]
pub struct IsolationError {
    pub kind: ErrorKind,
    pub stage: &'static str,
    pub source: io::Error,
}
pub type Result<T> = std::result::Result<T, IsolationError>;
impl fmt::Display for IsolationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "LINUX_ISOLATION_{:?}: {}: {}",
            self.kind, self.stage, self.source
        )
    }
}
impl std::error::Error for IsolationError {}
fn invalid(stage: &'static str) -> IsolationError {
    IsolationError {
        kind: ErrorKind::InvalidPolicy,
        stage,
        source: io::Error::new(io::ErrorKind::InvalidInput, stage),
    }
}
fn unsupported(stage: &'static str) -> IsolationError {
    IsolationError {
        kind: ErrorKind::Unsupported,
        stage,
        source: io::Error::new(io::ErrorKind::Unsupported, stage),
    }
}
#[derive(Debug)]
pub struct Applied {
    pub uid: u32,
    pub gid: u32,
    pub pid: u32,
    pub parent: u32,
    pub pid_namespace: bool,
    pub namespace_ids: [u64; 4],
}
#[derive(Debug)]
pub struct BaselineApplied {
    pub strict: bool,
    pub pid: u32,
}
#[derive(Debug)]
pub struct Metrics {
    pub memory_current: u64,
    pub memory_peak: u64,
    pub pids_current: u64,
    pub vm_hwm_bytes: Option<u64>,
    pub cpu_stat: String,
    pub memory_events: String,
    pub pids_events: String,
}
#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
pub use linux::WorkerIsolation;
#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
pub struct WorkerIsolation;
#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
impl WorkerIsolation {
    pub fn prepare(_: WorkerKey, _: Limits) -> Result<Self> {
        Err(unsupported("strict requires Linux x86_64"))
    }
    pub fn attach(&mut self, _: &mut Child) -> Result<()> {
        Err(unsupported("cgroup"))
    }
    pub fn child_policy(&self, _: u32, _: u32, _: u32) -> Result<ChildPolicy> {
        Err(unsupported("cgroup"))
    }
    pub fn mark_ready(&mut self) -> Result<()> {
        Err(unsupported("cgroup"))
    }
    pub fn metrics(&self) -> Result<Metrics> {
        Err(unsupported("cgroup"))
    }
    pub fn kill(&mut self) -> Result<()> {
        Err(unsupported("cgroup"))
    }
    pub fn cleanup_after_reap(&mut self, _: &mut Child) -> Result<()> {
        Err(unsupported("cgroup"))
    }
    pub fn discard_unattached(&mut self) -> Result<()> {
        Err(unsupported("cgroup"))
    }
}
/// # Safety
/// Dedicated single-threaded worker only. The caller must exit on error because
/// UID/root/namespace changes cannot be rolled back safely.
pub unsafe fn apply_child(policy: &ChildPolicy) -> Result<Applied> {
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    {
        linux::apply_child(policy)
    }
    #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
    {
        let _ = policy;
        Err(unsupported("strict requires Linux x86_64"))
    }
}
/// Diagnostic ONLY: installs nnp/seccomp/PDEATHSIG without UID/namespaces/cgroup.
/// This is explicitly not strict confinement and is never selected by apply_child.
/// # Safety
/// Same dedicated-worker/exit-on-error rule as apply_child.
pub unsafe fn apply_syscall_baseline(expected_parent: u32) -> Result<BaselineApplied> {
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    {
        linux::baseline(expected_parent)
    }
    #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
    {
        let _ = expected_parent;
        Err(unsupported("baseline requires Linux x86_64"))
    }
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
mod linux {
    use super::*;
    use std::{
        ffi::CString,
        fs::{self, File},
        io::{Read, Write},
        os::fd::{AsRawFd, FromRawFd, RawFd},
    };
    fn os(stage: &'static str, e: io::Error) -> IsolationError {
        IsolationError {
            kind: if matches!(
                e.raw_os_error(),
                Some(
                    libc::ENOSYS
                        | libc::EPERM
                        | libc::EACCES
                        | libc::EROFS
                        | libc::ENOENT
                        | libc::ENODEV
                        | libc::EOPNOTSUPP
                )
            ) {
                ErrorKind::Unsupported
            } else {
                ErrorKind::Os
            },
            stage,
            source: e,
        }
    }
    fn check(n: libc::c_long, stage: &'static str) -> Result<()> {
        if n == 0 {
            Ok(())
        } else {
            Err(os(stage, io::Error::last_os_error()))
        }
    }
    fn c(s: &str) -> CString {
        CString::new(s).expect("trusted generated name")
    }
    fn prctl(op: i32, arg: usize, stage: &'static str) -> Result<()> {
        check(
            unsafe { libc::prctl(op, arg, 0usize, 0usize, 0usize) } as _,
            stage,
        )
    }
    fn root(path: &str, cgroup: bool) -> Result<File> {
        for ancestor in std::path::Path::new(path).ancestors() {
            let m = fs::symlink_metadata(ancestor).map_err(|e| os("prepared root", e))?;
            if m.file_type().is_symlink() {
                return Err(invalid("symlink in prepared root"));
            }
        }
        let fd = unsafe {
            libc::open(
                c(path).as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(os("open prepared root", io::Error::last_os_error()));
        }
        let f = unsafe { File::from_raw_fd(fd) };
        let m = f.metadata().map_err(|e| os("root metadata", e))?;
        use std::os::unix::fs::MetadataExt;
        if m.uid() != 0 || m.mode() & 0o777 != 0o700 {
            return Err(invalid("prepared root must be root:0700"));
        }
        if cgroup {
            let mut s = std::mem::MaybeUninit::<libc::statfs>::uninit();
            check(
                unsafe { libc::fstatfs(fd, s.as_mut_ptr()) } as _,
                "cgroup fstatfs",
            )?;
            if unsafe { s.assume_init() }.f_type != 0x63677270 {
                return Err(unsupported("not cgroup v2"));
            }
        }
        Ok(f)
    }
    fn control(dir: RawFd, name: &str, write: bool) -> Result<File> {
        let fd = unsafe {
            libc::openat(
                dir,
                c(name).as_ptr(),
                libc::O_CLOEXEC
                    | libc::O_NOFOLLOW
                    | if write {
                        libc::O_WRONLY
                    } else {
                        libc::O_RDONLY
                    },
            )
        };
        if fd < 0 {
            return Err(os("cgroup control open", io::Error::last_os_error()));
        }
        Ok(unsafe { File::from_raw_fd(fd) })
    }
    fn read(dir: RawFd, name: &str) -> Result<String> {
        let mut s = String::new();
        control(dir, name, false)?
            .take(65536)
            .read_to_string(&mut s)
            .map_err(|e| os("control read", e))?;
        Ok(s)
    }
    fn write(dir: RawFd, name: &str, value: &str) -> Result<()> {
        control(dir, name, true)?
            .write_all(value.as_bytes())
            .map_err(|e| os("control write", e))
    }
    fn number(dir: RawFd, name: &str) -> Result<u64> {
        read(dir, name)?
            .trim()
            .parse()
            .map_err(|_| invalid("numeric control"))
    }
    struct OwnedDir {
        parent: File,
        leaf: File,
        name: String,
        dev: u64,
        ino: u64,
        removed: bool,
    }
    impl OwnedDir {
        fn create(parent: File, key: WorkerKey) -> Result<Self> {
            let name = key.name();
            check(
                unsafe { libc::mkdirat(parent.as_raw_fd(), c(&name).as_ptr(), 0o700) } as _,
                "mkdir owned leaf",
            )?;
            let fd = unsafe {
                libc::openat(
                    parent.as_raw_fd(),
                    c(&name).as_ptr(),
                    libc::O_DIRECTORY | libc::O_RDONLY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                )
            };
            if fd < 0 {
                let e = os("open owned leaf", io::Error::last_os_error());
                unsafe {
                    libc::unlinkat(parent.as_raw_fd(), c(&name).as_ptr(), libc::AT_REMOVEDIR)
                };
                return Err(e);
            }
            let leaf = unsafe { File::from_raw_fd(fd) };
            use std::os::unix::fs::MetadataExt;
            let m = leaf.metadata().map_err(|e| os("leaf metadata", e))?;
            Ok(Self {
                parent,
                leaf,
                name,
                dev: m.dev(),
                ino: m.ino(),
                removed: false,
            })
        }
        fn verify(&self) -> Result<()> {
            if self.removed {
                return Err(invalid("removed owned leaf"));
            }
            let mut s = std::mem::MaybeUninit::<libc::stat>::uninit();
            check(
                unsafe {
                    libc::fstatat(
                        self.parent.as_raw_fd(),
                        c(&self.name).as_ptr(),
                        s.as_mut_ptr(),
                        libc::AT_SYMLINK_NOFOLLOW,
                    )
                } as _,
                "owned leaf identity",
            )?;
            let s = unsafe { s.assume_init() };
            if s.st_dev != self.dev
                || s.st_ino != self.ino
                || s.st_mode & libc::S_IFMT != libc::S_IFDIR
            {
                return Err(invalid("replaced owned leaf"));
            }
            Ok(())
        }
        fn remove(&mut self) -> Result<()> {
            if self.removed {
                return Ok(());
            }
            self.verify()?;
            check(
                unsafe {
                    libc::unlinkat(
                        self.parent.as_raw_fd(),
                        c(&self.name).as_ptr(),
                        libc::AT_REMOVEDIR,
                    )
                } as _,
                "remove owned empty leaf",
            )?;
            self.removed = true;
            Ok(())
        }
        fn fd(&self) -> RawFd {
            self.leaf.as_raw_fd()
        }
    }
    pub struct WorkerIsolation {
        cg: OwnedDir,
        empty: OwnedDir,
        key: WorkerKey,
        limits: Limits,
        pid: Option<u32>,
        steady: bool,
        cleaned: bool,
    }
    fn parent_of(pid: u32) -> Result<u32> {
        let s = fs::read_to_string(format!("/proc/{pid}/status"))
            .map_err(|e| os("worker status", e))?;
        s.lines()
            .find_map(|l| l.strip_prefix("PPid:"))
            .and_then(|s| s.trim().parse().ok())
            .ok_or_else(|| invalid("worker PPid"))
    }
    impl WorkerIsolation {
        pub fn prepare(key: WorkerKey, limits: Limits) -> Result<Self> {
            if unsafe { libc::geteuid() } != 0 {
                return Err(unsupported("parent requires owned VM root"));
            }
            let cgroot = root(CGROUP_ROOT, true)?;
            let subtree = read(cgroot.as_raw_fd(), "cgroup.subtree_control")?;
            for name in ["cpu", "memory", "pids"] {
                if !subtree.split_whitespace().any(|s| s == name) {
                    return Err(unsupported("PID1 must prepare controllers"));
                }
            }
            let emptyroot = root(PRIVATE_ROOT, false)?;
            let mut cg = OwnedDir::create(cgroot, key)?;
            let configured = (|| {
                for (name, value) in [
                    ("cpu.max", STARTUP_CPU_MAX.to_owned()),
                    ("memory.max", limits.memory_bytes.to_string()),
                    ("memory.swap.max", "0".into()),
                    ("memory.oom.group", "1".into()),
                    ("pids.max", limits.pids.to_string()),
                ] {
                    write(cg.fd(), name, &value)?;
                }
                Ok(())
            })();
            if let Err(e) = configured {
                cg.remove()?;
                return Err(e);
            }
            let empty = match OwnedDir::create(emptyroot, key) {
                Ok(v) => v,
                Err(e) => {
                    cg.remove()?;
                    return Err(e);
                }
            };
            Ok(Self {
                cg,
                empty,
                key,
                limits,
                pid: None,
                steady: false,
                cleaned: false,
            })
        }
        /// Child must be blocked awaiting Init; no concurrent external SIGCHLD reaper.
        pub fn attach(&mut self, child: &mut Child) -> Result<()> {
            if self.pid.is_some()
                || child
                    .try_wait()
                    .map_err(|e| os("child liveness", e))?
                    .is_some()
            {
                return Err(invalid("attach live unreaped child once"));
            }
            let pid = child.id();
            if pid <= 1 || parent_of(pid)? != unsafe { libc::getpid() } as u32 {
                return Err(invalid("not direct owned child"));
            }
            self.cg.verify()?;
            write(self.cg.fd(), "cgroup.procs", &pid.to_string())?;
            if !read(self.cg.fd(), "cgroup.procs")?
                .lines()
                .any(|s| s.trim() == pid.to_string())
            {
                return Err(invalid("cgroup attach verification"));
            }
            self.pid = Some(pid);
            Ok(())
        }
        pub fn child_policy(
            &self,
            uid: u32,
            gid: u32,
            expected_parent: u32,
        ) -> Result<ChildPolicy> {
            if expected_parent != unsafe { libc::getpid() } as u32 {
                return Err(invalid("policy parent"));
            }
            ChildPolicy::new(
                self.key,
                uid,
                gid,
                expected_parent,
                self.pid.ok_or_else(|| invalid("attach before policy"))?,
                self.limits,
            )
        }
        pub fn mark_ready(&mut self) -> Result<()> {
            if self.pid.is_none() || self.steady || self.cleaned {
                return Err(invalid("Ready transition"));
            }
            self.cg.verify()?;
            write(self.cg.fd(), "cpu.max", STEADY_CPU_MAX)?;
            self.steady = true;
            Ok(())
        }
        pub fn metrics(&self) -> Result<Metrics> {
            self.cg.verify()?;
            let vm_hwm_bytes = self.pid.and_then(|p| {
                if parent_of(p).ok() != Some(unsafe { libc::getpid() } as u32) {
                    return None;
                }
                fs::read_to_string(format!("/proc/{p}/status"))
                    .ok()?
                    .lines()
                    .find_map(|l| {
                        l.strip_prefix("VmHWM:").and_then(|v| {
                            v.split_whitespace()
                                .next()?
                                .parse::<u64>()
                                .ok()?
                                .checked_mul(1024)
                        })
                    })
            });
            Ok(Metrics {
                memory_current: number(self.cg.fd(), "memory.current")?,
                memory_peak: number(self.cg.fd(), "memory.peak")?,
                pids_current: number(self.cg.fd(), "pids.current")?,
                vm_hwm_bytes,
                cpu_stat: read(self.cg.fd(), "cpu.stat")?,
                memory_events: read(self.cg.fd(), "memory.events")?,
                pids_events: read(self.cg.fd(), "pids.events")?,
            })
        }
        pub fn kill(&mut self) -> Result<()> {
            self.cg.verify()?;
            write(self.cg.fd(), "cgroup.kill", "1")
        }
        fn cleanup_empty(&mut self) -> Result<()> {
            if self.cleaned {
                return Ok(());
            }
            if !read(self.cg.fd(), "cgroup.procs")?.trim().is_empty() {
                return Err(invalid("cgroup still populated"));
            }
            self.empty.remove()?;
            self.cg.remove()?;
            self.cleaned = true;
            Ok(())
        }
        pub fn cleanup_after_reap(&mut self, child: &mut Child) -> Result<()> {
            if self.pid != Some(child.id())
                || child
                    .try_wait()
                    .map_err(|e| os("child reap check", e))?
                    .is_none()
            {
                return Err(invalid("kill and reap before cleanup"));
            }
            self.cleanup_empty()
        }
        pub fn discard_unattached(&mut self) -> Result<()> {
            if self.pid.is_some() {
                return Err(invalid("attached cgroup needs reap"));
            }
            self.cleanup_empty()
        }
    }
    fn single_thread() -> Result<()> {
        if fs::read_dir("/proc/self/task")
            .map_err(|e| os("single thread check", e))?
            .count()
            != 1
        {
            return Err(invalid("apply before creating threads"));
        }
        Ok(())
    }
    fn parent(expected: u32) -> Result<()> {
        if expected == 0 || unsafe { libc::getppid() } as u32 != expected {
            return Err(invalid("expected PPid mismatch"));
        }
        Ok(())
    }
    fn death_and_nnp(expected: u32) -> Result<()> {
        // setresuid/setresgid clear PDEATHSIG: set it AFTER both drops, then close
        // the parent-died-before-prctl race by rechecking PPid.
        prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL as usize, "PDEATHSIG")?;
        parent(expected)?;
        let mut signal = 0;
        check(
            unsafe { libc::prctl(libc::PR_GET_PDEATHSIG, &mut signal, 0, 0, 0) } as _,
            "PDEATHSIG read",
        )?;
        if signal != libc::SIGKILL {
            return Err(invalid("PDEATHSIG verification"));
        }
        prctl(libc::PR_SET_DUMPABLE, 0, "nondumpable")?;
        prctl(libc::PR_SET_NO_NEW_PRIVS, 1, "no_new_privs")?;
        if unsafe { libc::prctl(libc::PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) } != 1
            || unsafe { libc::prctl(libc::PR_GET_DUMPABLE, 0, 0, 0, 0) } != 0
        {
            return Err(invalid("nnp/dumpable verification"));
        }
        Ok(())
    }
    #[repr(C)]
    struct CapHeader {
        version: u32,
        pid: i32,
    }
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    struct CapData {
        effective: u32,
        permitted: u32,
        inheritable: u32,
    }
    fn capabilities_zero() -> Result<()> {
        let h = CapHeader {
            version: 0x20080522,
            pid: 0,
        };
        let zero = [CapData::default(); 2];
        check(
            unsafe { libc::syscall(libc::SYS_capset, &h, zero.as_ptr()) },
            "clear capabilities",
        )?;
        let mut got = [CapData::default(); 2];
        check(
            unsafe { libc::syscall(libc::SYS_capget, &h, got.as_mut_ptr()) },
            "get capabilities",
        )?;
        if got
            .iter()
            .any(|c| c.effective | c.permitted | c.inheritable != 0)
        {
            return Err(invalid("capability verification"));
        }
        Ok(())
    }
    pub(super) fn apply_child(p: &ChildPolicy) -> Result<Applied> {
        single_thread()?;
        parent(p.parent)?;
        if unsafe { libc::geteuid() } != 0 || unsafe { libc::getpid() } as u32 != p.pid {
            return Err(invalid("strict worker identity"));
        }
        let cg = root(CGROUP_ROOT, true)?;
        drop(cg);
        let path = format!("{CGROUP_ROOT}/{}", p.key.name());
        let member = fs::read_to_string("/proc/self/cgroup").map_err(|e| os("membership", e))?;
        if !member
            .lines()
            .any(|l| l == format!("0::/mingos/{}", p.key.name()))
        {
            return Err(invalid("owned cgroup membership"));
        }
        let fd = unsafe {
            libc::open(
                c(&path).as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(os("worker cgroup", io::Error::last_os_error()));
        }
        let cg = unsafe { File::from_raw_fd(fd) };
        for (name, value) in [
            ("cpu.max", STARTUP_CPU_MAX.to_owned()),
            ("memory.max", p.limits.memory_bytes.to_string()),
            ("memory.swap.max", "0".into()),
            ("pids.max", p.limits.pids.to_string()),
        ] {
            if read(cg.as_raw_fd(), name)?.trim() != value {
                return Err(invalid("worker cgroup limits"));
            }
        }
        drop(cg);
        let rootfd = root(PRIVATE_ROOT, false)?;
        let name = p.key.name();
        let rootpath = format!("{PRIVATE_ROOT}/{name}");
        let m = fs::symlink_metadata(&rootpath).map_err(|e| os("private root leaf", e))?;
        use std::os::unix::fs::MetadataExt;
        if !m.is_dir()
            || m.file_type().is_symlink()
            || m.uid() != 0
            || m.mode() & 0o777 != 0o700
            || fs::read_dir(&rootpath)
                .map_err(|e| os("empty root", e))?
                .next()
                .is_some()
        {
            return Err(invalid("owned empty root"));
        }
        drop(rootfd);
        let last_cap: u32 = fs::read_to_string("/proc/sys/kernel/cap_last_cap")
            .map_err(|e| os("last cap", e))?
            .trim()
            .parse()
            .map_err(|_| invalid("cap range"))?;
        if last_cap > 63 {
            return Err(unsupported("capability range"));
        }
        let namespace_ids = || -> Result<[u64; 4]> {
            use std::os::unix::fs::MetadataExt;
            let mut ids = [0; 4];
            for (i, name) in ["mnt", "net", "ipc", "uts"].iter().enumerate() {
                ids[i] = fs::metadata(format!("/proc/self/ns/{name}"))
                    .map_err(|e| os("namespace identity", e))?
                    .ino();
            }
            Ok(ids)
        };
        let previous_namespaces = namespace_ids()?;
        // No PID, user, or cgroup namespace is claimed by this prototype.
        check(
            unsafe {
                libc::unshare(
                    libc::CLONE_NEWNS
                        | libc::CLONE_NEWNET
                        | libc::CLONE_NEWIPC
                        | libc::CLONE_NEWUTS,
                )
            } as _,
            "strict namespaces",
        )?;
        let private_namespaces = namespace_ids()?;
        if previous_namespaces
            .iter()
            .zip(private_namespaces)
            .any(|(a, b)| *a == b)
        {
            return Err(invalid("namespace separation verification"));
        }
        check(
            unsafe {
                libc::mount(
                    std::ptr::null(),
                    c("/").as_ptr(),
                    std::ptr::null(),
                    libc::MS_REC | libc::MS_PRIVATE,
                    std::ptr::null(),
                )
            } as _,
            "private mounts",
        )?;
        check(
            unsafe { libc::sethostname(c("ming-worker").as_ptr(), 11) } as _,
            "private hostname",
        )?;
        check(
            unsafe {
                libc::mount(
                    c("tmpfs").as_ptr(),
                    c(&rootpath).as_ptr(),
                    c("tmpfs").as_ptr(),
                    libc::MS_NODEV | libc::MS_NOSUID | libc::MS_NOEXEC,
                    c("size=65536,mode=000").as_ptr().cast(),
                )
            } as _,
            "empty private root mount",
        )?;
        // Check IPC stdio and seal all inherited non-stdio FDs, including root/cgroup.
        for fd in [0, 1] {
            let mut s = std::mem::MaybeUninit::<libc::stat>::uninit();
            check(unsafe { libc::fstat(fd, s.as_mut_ptr()) } as _, "stdio")?;
            if unsafe { s.assume_init() }.st_mode & libc::S_IFMT != libc::S_IFIFO {
                return Err(invalid("SDK stdio must be pipes"));
            }
        }
        let mut stderr_stat = std::mem::MaybeUninit::<libc::stat>::uninit();
        check(
            unsafe { libc::fstat(2, stderr_stat.as_mut_ptr()) } as _,
            "stderr",
        )?;
        let stderr_stat = unsafe { stderr_stat.assume_init() };
        if stderr_stat.st_mode & libc::S_IFMT != libc::S_IFIFO
            && !(stderr_stat.st_mode & libc::S_IFMT == libc::S_IFCHR
                && libc::major(stderr_stat.st_rdev) == 1
                && libc::minor(stderr_stat.st_rdev) == 3)
        {
            return Err(invalid("stderr must be a pipe or devnull"));
        }
        check(
            unsafe { libc::syscall(libc::SYS_close_range, 3u32, u32::MAX, 0u32) },
            "close inherited descriptors",
        )?;
        check(
            unsafe { libc::chdir(c(&rootpath).as_ptr()) } as _,
            "chdir private root",
        )?;
        check(
            unsafe { libc::chroot(c(".").as_ptr()) } as _,
            "chroot private root",
        )?;
        check(
            unsafe { libc::chdir(c("/").as_ptr()) } as _,
            "chdir sealed root",
        )?;
        prctl(libc::PR_SET_KEEPCAPS, 0, "clear keepcaps")?;
        prctl(
            libc::PR_CAP_AMBIENT,
            libc::PR_CAP_AMBIENT_CLEAR_ALL as usize,
            "clear ambient",
        )?;
        for cap in 0..=last_cap {
            prctl(libc::PR_CAPBSET_DROP, cap as usize, "drop bounding cap")?;
            if unsafe { libc::prctl(libc::PR_CAPBSET_READ, cap, 0, 0, 0) } != 0 {
                return Err(invalid("bounding cap verification"));
            }
        }
        check(
            unsafe { libc::setgroups(0, std::ptr::null()) } as _,
            "clear groups",
        )?;
        check(
            unsafe { libc::setresgid(p.gid, p.gid, p.gid) } as _,
            "drop GID",
        )?;
        check(
            unsafe { libc::setresuid(p.uid, p.uid, p.uid) } as _,
            "drop UID",
        )?;
        capabilities_zero()?;
        for cap in 0..=last_cap {
            if unsafe { libc::prctl(libc::PR_CAP_AMBIENT, libc::PR_CAP_AMBIENT_IS_SET, cap, 0, 0) }
                != 0
            {
                return Err(invalid("ambient cap verification"));
            }
        }
        let (mut ruid, mut euid, mut suid, mut rgid, mut egid, mut sgid) = (0, 0, 0, 0, 0, 0);
        check(
            unsafe { libc::getresuid(&mut ruid, &mut euid, &mut suid) } as _,
            "verify UIDs",
        )?;
        check(
            unsafe { libc::getresgid(&mut rgid, &mut egid, &mut sgid) } as _,
            "verify GIDs",
        )?;
        if [ruid, euid, suid] != [p.uid; 3]
            || [rgid, egid, sgid] != [p.gid; 3]
            || unsafe { libc::getgroups(0, std::ptr::null_mut()) } != 0
        {
            return Err(invalid("identity drop verification"));
        }
        death_and_nnp(p.parent)?;
        install_filter()?;
        Ok(Applied {
            uid: p.uid,
            gid: p.gid,
            pid: p.pid,
            parent: p.parent,
            pid_namespace: false,
            namespace_ids: private_namespaces,
        })
    }
    pub(super) fn baseline(expected: u32) -> Result<BaselineApplied> {
        single_thread()?;
        parent(expected)?;
        death_and_nnp(expected)?;
        install_filter()?;
        Ok(BaselineApplied {
            strict: false,
            pid: unsafe { libc::getpid() } as u32,
        })
    }

    // Classic BPF seccomp_data offsets: nr=0, arch=4, args[n]=16+8*n.
    // x86_64 only: reject x32's alternate syscall-number bit as well as bad arch.
    const LD: u16 = 0x20;
    const JEQ: u16 = 0x15;
    const JGE: u16 = 0x35;
    const AND: u16 = 0x54;
    const RET: u16 = 0x06;
    const ALLOW: u32 = 0x7fff0000;
    const KILL: u32 = 0x80000000;
    const ERR: u32 = 0x00050000 | libc::EPERM as u32;
    fn ins(code: u16, jt: u8, jf: u8, k: u32) -> libc::sock_filter {
        libc::sock_filter { code, jt, jf, k }
    }
    fn yes() -> libc::sock_filter {
        ins(RET, 0, 0, ALLOW)
    }
    fn no() -> libc::sock_filter {
        ins(RET, 0, 0, ERR)
    }
    fn values(arg: u32, allowed: &[u32]) -> Vec<libc::sock_filter> {
        let mut v = vec![
            ins(LD, 0, 0, 20 + 8 * arg),
            ins(JEQ, 1, 0, 0),
            no(),
            ins(LD, 0, 0, 16 + 8 * arg),
        ];
        for value in allowed {
            v.extend([ins(JEQ, 0, 1, *value), yes()]);
        }
        v.push(no());
        v
    }
    fn special(out: &mut Vec<libc::sock_filter>, nr: i64, body: Vec<libc::sock_filter>) {
        assert!(body.len() < 256);
        out.push(ins(JEQ, 0, body.len() as u8, nr as u32));
        out.extend(body);
    }
    fn filter(pid: u32) -> Vec<libc::sock_filter> {
        let mut v = vec![
            ins(LD, 0, 0, 4),
            ins(JEQ, 1, 0, 0xc000003e),
            ins(RET, 0, 0, KILL),
            ins(LD, 0, 0, 0),
            ins(JGE, 0, 1, 0x40000000),
            ins(RET, 0, 0, KILL),
        ];
        special(
            &mut v,
            libc::SYS_clone3,
            vec![ins(RET, 0, 0, 0x00050000 | libc::ENOSYS as u32)],
        );
        let required = (libc::CLONE_VM | libc::CLONE_SIGHAND | libc::CLONE_THREAD) as u32;
        let allowed = required
            | (libc::CLONE_FS
                | libc::CLONE_FILES
                | libc::CLONE_SYSVSEM
                | libc::CLONE_SETTLS
                | libc::CLONE_PARENT_SETTID
                | libc::CLONE_CHILD_CLEARTID
                | libc::CLONE_CHILD_SETTID) as u32;
        special(
            &mut v,
            libc::SYS_clone,
            vec![
                ins(LD, 0, 0, 20),
                ins(JEQ, 1, 0, 0),
                no(),
                ins(LD, 0, 0, 16),
                ins(AND, 0, 0, !allowed),
                ins(JEQ, 1, 0, 0),
                no(),
                ins(LD, 0, 0, 16),
                ins(AND, 0, 0, required),
                ins(JEQ, 1, 0, required),
                no(),
                yes(),
            ],
        );
        special(&mut v, libc::SYS_kill, values(0, &[pid]));
        special(&mut v, libc::SYS_tgkill, values(0, &[pid]));
        special(
            &mut v,
            libc::SYS_prctl,
            values(
                0,
                &[
                    libc::PR_SET_NAME as u32,
                    libc::PR_GET_NAME as u32,
                    libc::PR_GET_NO_NEW_PRIVS as u32,
                    libc::PR_GET_DUMPABLE as u32,
                    libc::PR_GET_PDEATHSIG as u32,
                    libc::PR_GET_SECCOMP as u32,
                ],
            ),
        );
        special(
            &mut v,
            libc::SYS_fcntl,
            values(
                1,
                &[
                    libc::F_GETFD as u32,
                    libc::F_SETFD as u32,
                    libc::F_GETFL as u32,
                    libc::F_SETFL as u32,
                    libc::F_DUPFD as u32,
                    libc::F_DUPFD_CLOEXEC as u32,
                    libc::F_ADD_SEALS as u32,
                    libc::F_GET_SEALS as u32,
                ],
            ),
        );
        let mut limit = vec![
            ins(LD, 0, 0, 32),
            ins(JEQ, 1, 0, 0),
            no(),
            ins(LD, 0, 0, 36),
            ins(JEQ, 1, 0, 0),
            no(),
        ];
        limit.extend(values(0, &[0, pid]));
        special(&mut v, libc::SYS_prlimit64, limit);
        // Denylist prototype: default ALLOW is a documented limit, not a claim
        // that future/unknown Linux syscalls have been exhaustively constrained.
        // memfd_create + mmap remain available for anonymous JIT backing only:
        // no host files can be opened and all inherited FD>=3 were sealed.
        let denied = [
            libc::SYS_open,
            libc::SYS_openat,
            libc::SYS_openat2,
            libc::SYS_creat,
            libc::SYS_access,
            libc::SYS_faccessat,
            libc::SYS_faccessat2,
            libc::SYS_statfs,
            libc::SYS_fstatfs,
            libc::SYS_stat,
            libc::SYS_lstat,
            libc::SYS_newfstatat,
            libc::SYS_statx,
            libc::SYS_getdents,
            libc::SYS_getdents64,
            libc::SYS_readlink,
            libc::SYS_readlinkat,
            libc::SYS_chdir,
            libc::SYS_fchdir,
            libc::SYS_chroot,
            libc::SYS_getcwd,
            libc::SYS_mkdir,
            libc::SYS_mkdirat,
            libc::SYS_rmdir,
            libc::SYS_link,
            libc::SYS_linkat,
            libc::SYS_unlink,
            libc::SYS_unlinkat,
            libc::SYS_symlink,
            libc::SYS_symlinkat,
            libc::SYS_rename,
            libc::SYS_renameat,
            libc::SYS_renameat2,
            libc::SYS_mknod,
            libc::SYS_mknodat,
            libc::SYS_chmod,
            libc::SYS_fchmod,
            libc::SYS_fchmodat,
            libc::SYS_chown,
            libc::SYS_fchown,
            libc::SYS_lchown,
            libc::SYS_fchownat,
            libc::SYS_truncate,
            libc::SYS_ftruncate,
            libc::SYS_setxattr,
            libc::SYS_lsetxattr,
            libc::SYS_fsetxattr,
            libc::SYS_getxattr,
            libc::SYS_lgetxattr,
            libc::SYS_fgetxattr,
            libc::SYS_listxattr,
            libc::SYS_llistxattr,
            libc::SYS_flistxattr,
            libc::SYS_removexattr,
            libc::SYS_lremovexattr,
            libc::SYS_fremovexattr,
            libc::SYS_mount,
            libc::SYS_umount2,
            libc::SYS_pivot_root,
            libc::SYS_open_tree,
            libc::SYS_move_mount,
            libc::SYS_fsopen,
            libc::SYS_fsconfig,
            libc::SYS_fsmount,
            libc::SYS_fspick,
            libc::SYS_mount_setattr,
            libc::SYS_name_to_handle_at,
            libc::SYS_open_by_handle_at,
            libc::SYS_unshare,
            libc::SYS_setns,
            libc::SYS_socket,
            libc::SYS_socketpair,
            libc::SYS_connect,
            libc::SYS_bind,
            libc::SYS_listen,
            libc::SYS_accept,
            libc::SYS_accept4,
            libc::SYS_sendto,
            libc::SYS_sendmsg,
            libc::SYS_sendmmsg,
            libc::SYS_recvfrom,
            libc::SYS_recvmsg,
            libc::SYS_recvmmsg,
            libc::SYS_getsockname,
            libc::SYS_getpeername,
            libc::SYS_getsockopt,
            libc::SYS_setsockopt,
            libc::SYS_shutdown,
            libc::SYS_execve,
            libc::SYS_execveat,
            libc::SYS_fork,
            libc::SYS_vfork,
            libc::SYS_ptrace,
            libc::SYS_process_vm_readv,
            libc::SYS_process_vm_writev,
            libc::SYS_kcmp,
            libc::SYS_pidfd_open,
            libc::SYS_pidfd_getfd,
            libc::SYS_pidfd_send_signal,
            libc::SYS_process_madvise,
            libc::SYS_process_mrelease,
            libc::SYS_tkill,
            libc::SYS_rt_sigqueueinfo,
            libc::SYS_rt_tgsigqueueinfo,
            libc::SYS_setuid,
            libc::SYS_setgid,
            libc::SYS_setresuid,
            libc::SYS_setresgid,
            libc::SYS_setreuid,
            libc::SYS_setregid,
            libc::SYS_setfsuid,
            libc::SYS_setfsgid,
            libc::SYS_setgroups,
            libc::SYS_capset,
            libc::SYS_setrlimit,
            libc::SYS_bpf,
            libc::SYS_perf_event_open,
            libc::SYS_userfaultfd,
            libc::SYS_io_uring_setup,
            libc::SYS_io_uring_enter,
            libc::SYS_io_uring_register,
            libc::SYS_io_setup,
            libc::SYS_io_submit,
            libc::SYS_io_getevents,
            libc::SYS_io_cancel,
            libc::SYS_io_destroy,
            libc::SYS_keyctl,
            libc::SYS_add_key,
            libc::SYS_request_key,
            libc::SYS_reboot,
            libc::SYS_init_module,
            libc::SYS_finit_module,
            libc::SYS_delete_module,
            libc::SYS_kexec_load,
            libc::SYS_kexec_file_load,
            libc::SYS_iopl,
            libc::SYS_ioperm,
            libc::SYS_acct,
            libc::SYS_syslog,
            libc::SYS_swapon,
            libc::SYS_swapoff,
            libc::SYS_quotactl,
            libc::SYS_modify_ldt,
            libc::SYS_personality,
            libc::SYS_fanotify_init,
            libc::SYS_inotify_init,
            libc::SYS_inotify_init1,
        ];
        for nr in denied {
            v.extend([ins(JEQ, 0, 1, nr as u32), no()]);
        }
        v.push(yes());
        v
    }
    fn install_filter() -> Result<()> {
        let f = filter(unsafe { libc::getpid() } as u32);
        let prog = libc::sock_fprog {
            len: f.len() as u16,
            filter: f.as_ptr().cast_mut(),
        };
        let n = unsafe { libc::syscall(libc::SYS_seccomp, 1u32, 1u32, &prog) }; // SET_MODE_FILTER, TSYNC
        if n != 0 {
            return Err(if n > 0 {
                invalid("seccomp TSYNC failed thread")
            } else {
                os("seccomp TSYNC", io::Error::last_os_error())
            });
        }
        Ok(())
    }
    #[cfg(test)]
    mod tests {
        use super::*;
        fn decision(nr: u32, arch: u32, args: [u64; 6]) -> u32 {
            let v = filter(100);
            let mut a = 0u32;
            let mut pc = 0;
            loop {
                let x = v[pc];
                pc += 1;
                match x.code {
                    LD => {
                        a = match x.k {
                            0 => nr,
                            4 => arch,
                            k if k >= 16 => {
                                let i = ((k - 16) / 8) as usize;
                                if (k - 16) % 8 == 0 {
                                    args[i] as u32
                                } else {
                                    (args[i] >> 32) as u32
                                }
                            }
                            _ => panic!("offset"),
                        }
                    }
                    JEQ => pc += if a == x.k { x.jt } else { x.jf } as usize,
                    JGE => pc += if a >= x.k { x.jt } else { x.jf } as usize,
                    AND => a &= x.k,
                    RET => return x.k,
                    _ => panic!("instruction"),
                }
            }
        }
        #[test]
        fn arch_x32_thread_flags_and_external_signal_are_filtered() {
            let d = |nr: i64, args| decision(nr as u32, 0xc000003e, args);
            assert_eq!(decision(0, 0, [0; 6]), KILL);
            assert_eq!(decision(0x40000000, 0xc000003e, [0; 6]), KILL);
            assert_eq!(d(libc::SYS_clone3, [0; 6]), 0x50000 | libc::ENOSYS as u32);
            assert_eq!(
                d(libc::SYS_clone, [libc::SIGCHLD as u64, 0, 0, 0, 0, 0]),
                ERR
            );
            let thread = (libc::CLONE_THREAD | libc::CLONE_VM | libc::CLONE_SIGHAND) as u64;
            assert_eq!(d(libc::SYS_clone, [thread, 0, 0, 0, 0, 0]), ALLOW);
            assert_eq!(
                d(
                    libc::SYS_clone,
                    [thread | libc::CLONE_NEWUSER as u64, 0, 0, 0, 0, 0]
                ),
                ERR
            );
            assert_eq!(d(libc::SYS_clone, [thread | (1 << 32), 0, 0, 0, 0, 0]), ERR);
            assert_eq!(d(libc::SYS_kill, [101, 0, 0, 0, 0, 0]), ERR);
            assert_eq!(d(libc::SYS_kill, [100, 0, 0, 0, 0, 0]), ALLOW);
            assert_eq!(d(libc::SYS_tgkill, [101, 1, 0, 0, 0, 0]), ERR);
            for nr in [
                libc::SYS_openat,
                libc::SYS_socket,
                libc::SYS_execve,
                libc::SYS_ptrace,
                libc::SYS_setuid,
                libc::SYS_unshare,
            ] {
                assert_eq!(d(nr, [0; 6]), ERR);
            }
            assert_eq!(
                d(libc::SYS_fcntl, [3, libc::F_ADD_SEALS as u64, 0, 0, 0, 0]),
                ALLOW
            );
            assert_eq!(
                d(libc::SYS_fcntl, [3, libc::F_SETOWN as u64, 0, 0, 0, 0]),
                ERR
            );
            assert_eq!(d(libc::SYS_prlimit64, [0, 0, 0, 0, 0, 0]), ALLOW);
            assert_eq!(d(libc::SYS_prlimit64, [100, 0, 1, 0, 0, 0]), ERR);
            assert_eq!(d(libc::SYS_prlimit64, [101, 0, 0, 0, 0, 0]), ERR);
            for nr in [
                libc::SYS_read,
                libc::SYS_write,
                libc::SYS_mmap,
                libc::SYS_mprotect,
                libc::SYS_futex,
                libc::SYS_clock_gettime,
                libc::SYS_getrandom,
            ] {
                assert_eq!(d(nr, [0; 6]), ALLOW);
            }
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identity_and_resource_contract_is_fail_closed() {
        assert!(WorkerKey::new(0, 1).is_err());
        assert!(WorkerKey::new(1, 0).is_err());
        assert_eq!(WorkerKey::new(3, 9).unwrap().name(), "app-3-g-9");
        assert!(Limits::new(64 << 20, 64).is_err());
        assert!(Limits::new(1 << 30, 0).is_err());
        assert!(Limits::new(1 << 30, 64).is_ok());
        let name = WorkerKey::new(u16::MAX, u64::MAX).unwrap().name();
        assert!(name.len() < 64);
        assert!(!name.contains(['/', '.', '\\']));
        assert_eq!(STARTUP_CPU_MAX, "100000 100000");
        assert_eq!(STEADY_CPU_MAX, "25000 100000");
    }
    #[test]
    fn identity_cannot_request_root_or_wrong_parent() {
        let k = WorkerKey::new(1, 1).unwrap();
        let l = Limits::new(1 << 30, 64).unwrap();
        assert!(ChildPolicy::new(k, 0, 0, 1, 2, l).is_err());
        assert!(ChildPolicy::new(k, 20001, 20001, 0, 2, l).is_err());
        assert!(ChildPolicy::new(k, 20001, 20001, 2, 2, l).is_err());
        assert!(ChildPolicy::new(k, 20001, 20001, 1, 2, l).is_ok());
    }
    #[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
    #[test]
    fn strict_is_explicitly_unsupported() {
        assert_eq!(
            WorkerIsolation::prepare(
                WorkerKey::new(1, 1).unwrap(),
                Limits::new(1 << 30, 64).unwrap()
            )
            .err()
            .unwrap()
            .kind,
            ErrorKind::Unsupported
        );
    }
}
