// Source-bound strict isolation probe.
//
// This is diagnostic evidence for the current linux_isolation implementation.
// It is intentionally a separate generation from the historical probe.

use anyhow::{Context, Result, bail};
use ming_app_host::linux_isolation::{
    ChildPolicy, Limits, WorkerIsolation, WorkerKey, apply_child,
};
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::AtomicU32;
use std::time::{Duration, Instant};

const PROFILE: &str = "source-v2";
const UID: u32 = 20001;
const GID: u32 = 20001;

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("--child") => child(args.next().as_deref().unwrap_or("vm")),
        Some("vm") => smoke(),
        Some("vm-resources") => resources(),
        _ => bail!("usage: probe vm|vm-resources"),
    }
}

fn child(mode: &str) -> Result<()> {
    println!("CHILD_READY profile={PROFILE} mode={mode}");
    std::io::stdout().flush()?;
    for control in ["cpu.max", "memory.max", "memory.swap.max", "pids.max"] {
        let path = format!("/sys/fs/cgroup/mingos/app-1-g-1/{control}");
        eprintln!("CHILD_CGROUP_LIMIT {control}={}", fs::read_to_string(path)?.trim());
    }
    let stdin = std::io::stdin();
    let mut input = BufReader::new(stdin.lock());
    let mut line = String::new();
    input.read_line(&mut line)?;
    let wire: ming_app_host::protocol::IsolationPolicy =
        serde_json::from_str(&line).context("policy JSON")?;
    let policy: ChildPolicy = wire.to_native()?;
    let applied = unsafe { apply_child(&policy) }?;
    if applied.uid != UID || applied.gid != GID || applied.pid != std::process::id() {
        bail!("identity report mismatch");
    }
    println!("CHILD_APPLIED profile={PROFILE} mode={mode}");
    std::io::stdout().flush()?;
    line.clear();
    input.read_line(&mut line)?;
    if line.trim() != "steady" {
        bail!("steady transition missing");
    }
    match mode {
        "vm" => smoke_child()?,
        "memory" => memory_pressure()?,
        "pids" => pids_pressure()?,
        "cpu" => cpu_pressure()?,
        _ => bail!("unknown child mode"),
    }
    Ok(())
}

fn prepare_child(mode: &str, pids: u32) -> Result<(WorkerIsolation, Child)> {
    let key = WorkerKey::new(1, 1)?;
    let mut group = WorkerIsolation::prepare(key, Limits::new(512 << 20, pids)?)?;
    let exe = std::env::current_exe()?;
    let mut child = Command::new(exe)
        .arg("--child")
        .arg(mode)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("spawn worker")?;
    read_marker(&mut child, "CHILD_READY profile=source-v2")?;
    group.attach(&mut child)?;
    for control in ["cpu.max", "memory.max", "memory.swap.max", "pids.max"] {
        let path = format!("/sys/fs/cgroup/mingos/{}/{}", key.name(), control);
        println!("PARENT_CGROUP_LIMIT {control}={}", fs::read_to_string(path)?.trim());
    }
    let native = group.child_policy(UID, GID, std::process::id())?;
    let wire = ming_app_host::protocol::IsolationPolicy::from_native(&native);
    let mut stdin = child.stdin.take().context("worker stdin")?;
    writeln!(stdin, "{}", serde_json::to_string(&wire)?)?;
    read_marker(&mut child, "CHILD_APPLIED profile=source-v2")?;
    group.mark_ready()?;
    writeln!(stdin, "steady")?;
    stdin.flush()?;
    drop(stdin);
    Ok((group, child))
}

fn read_marker(child: &mut Child, prefix: &str) -> Result<String> {
    let stdout = child.stdout.as_mut().context("worker stdout")?;
    let mut line = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        if stdout.read(&mut byte)? == 0 {
            bail!("worker marker stream closed");
        }
        if byte[0] == b'\n' {
            break;
        }
        line.push(byte[0]);
        if line.len() > 4096 {
            bail!("worker marker too long");
        }
    }
    let line = String::from_utf8(line).context("worker marker UTF-8")?;
    if !line.starts_with(prefix) {
        bail!("worker marker mismatch: {line}");
    }
    Ok(line)
}

fn collect(mut group: WorkerIsolation, mut child: Child, mode: &str) -> Result<String> {
    let mut output = String::new();
    if let Some(mut stdout) = child.stdout.take() {
        stdout.read_to_string(&mut output)?;
    }
    let mut error = String::new();
    if let Some(mut stderr) = child.stderr.take() {
        stderr.read_to_string(&mut error)?;
    }
    let status = child.wait()?;
    if !status.success() && mode == "vm" {
        bail!("strict smoke worker failed: {status}: stdout={output} stderr={error}");
    }
    if !status.success() && mode != "memory" && mode != "pids" {
        bail!("resource worker failed: {status}: stdout={output} stderr={error}");
    }
    let metrics = group.metrics()?;
    if mode == "memory" {
        require_counter(&metrics.memory_events, "oom_kill")?;
    } else if mode == "pids" {
        require_counter(&metrics.pids_events, "max")?;
    } else if mode == "cpu" {
        require_counter(&metrics.cpu_stat, "nr_throttled")?;
    }
    group.cleanup_after_reap(&mut child)?;
    Ok(output)
}

fn smoke() -> Result<()> {
    let (group, child) = prepare_child("vm", 64)?;
    let output = collect(group, child, "vm")?;
    if !output.contains("NEGATIVE_CHECKS_OK") {
        bail!("negative isolation checks missing");
    }
    println!("STRICT_CLEANUP_OK profile={PROFILE}");
    Ok(())
}

fn resources() -> Result<()> {
    for (case, pids) in [("memory", 64), ("pids", 32), ("cpu", 64)] {
        let (group, child) = prepare_child(case, pids)?;
        let _ = collect(group, child, case)?;
        println!("RESOURCE_VERIFIED_AND_REAPED case={} profile={PROFILE}", match case {
            "memory" => 0,
            "pids" => 1,
            "cpu" => 2,
            _ => unreachable!(),
        });
    }
    Ok(())
}

fn smoke_child() -> Result<()> {
    let page = unsafe {
        libc::mmap(
            std::ptr::null_mut(),
            4096,
            libc::PROT_READ | libc::PROT_WRITE | libc::PROT_EXEC,
            libc::MAP_PRIVATE | libc::MAP_ANONYMOUS,
            -1,
            0,
        )
    };
    if page == libc::MAP_FAILED {
        bail!("anonymous executable mmap failed");
    }
    unsafe {
        std::ptr::write_volatile(page.cast::<u8>(), 7);
        libc::munmap(page, 4096);
    }
    let mut timespec = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut timespec) } != 0 {
        bail!("clock failed");
    }
    let mut random = [0u8; 32];
    if unsafe { libc::getrandom(random.as_mut_ptr().cast(), random.len(), 0) } != random.len() as isize {
        bail!("random failed");
    }
    let word = AtomicU32::new(0);
    let handle = std::thread::spawn(|| 1u32);
    let value = handle.join().map_err(|_| anyhow::anyhow!("thread failed"))?;
    if value != 1 {
        bail!("thread result mismatch");
    }
    let futex_ptr = &word as *const AtomicU32 as *mut u32;
    let futex_result = unsafe {
        libc::syscall(
            libc::SYS_futex,
            futex_ptr,
            libc::FUTEX_WAKE | libc::FUTEX_PRIVATE_FLAG,
            1,
            std::ptr::null::<libc::timespec>(),
        )
    };
    if futex_result < 0 {
        bail!("futex failed");
    }
    if unsafe { libc::open(cstr("/etc/passwd").as_ptr(), libc::O_RDONLY) } >= 0 {
        bail!("host filesystem unexpectedly reachable");
    }
    if unsafe { libc::socket(libc::AF_INET, libc::SOCK_STREAM, 0) } >= 0 {
        bail!("network unexpectedly reachable");
    }
    if unsafe { libc::ptrace(libc::PTRACE_TRACEME, 0, 0, 0) } == 0 {
        bail!("ptrace unexpectedly allowed");
    }
    if unsafe { libc::fork() } >= 0 {
        bail!("process clone unexpectedly allowed");
    }
    if unsafe { libc::kill(libc::getppid(), libc::SIGUSR1) } == 0 {
        bail!("external signal unexpectedly allowed");
    }
    let argv = cstr("/bin/true");
    let rc = unsafe {
        libc::execve(
            argv.as_ptr(),
            [argv.as_ptr(), std::ptr::null()].as_ptr(),
            [std::ptr::null()].as_ptr(),
        )
    };
    if rc == 0 {
        bail!("exec unexpectedly succeeded");
    }
    println!("NEGATIVE_CHECKS_OK");
    Ok(())
}

fn memory_pressure() -> Result<()> {
    let mut blocks = Vec::new();
    for _ in 0..40 {
        let mut block = vec![0u8; 16 * 1024 * 1024];
        for byte in block.iter_mut().step_by(4096) {
            *byte = 1;
        }
        blocks.push(block);
    }
    std::hint::black_box(blocks);
    std::thread::sleep(Duration::from_millis(100));
    Ok(())
}

fn pids_pressure() -> Result<()> {
    let mut handles = Vec::new();
    for _ in 0..128 {
        match std::thread::Builder::new().stack_size(64 * 1024).spawn(|| {
            std::thread::sleep(Duration::from_millis(500));
        }) {
            Ok(handle) => handles.push(handle),
            Err(_) => break,
        }
    }
    for handle in handles {
        let _ = handle.join();
    }
    Ok(())
}

fn cpu_pressure() -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut value = 0u64;
    while Instant::now() < deadline {
        value = value.wrapping_mul(1664525).wrapping_add(1013904223);
        std::hint::black_box(value);
    }
    Ok(())
}

fn require_counter(text: &str, key: &str) -> Result<()> {
    let value = text.lines().find_map(|line| {
        let mut fields = line.split_whitespace();
        (fields.next() == Some(key)).then(|| fields.next()?.parse::<u64>().ok()).flatten()
    });
    if value.unwrap_or(0) == 0 {
        bail!("resource event not observed: {key}: {text}");
    }
    Ok(())
}

fn cstr(value: &str) -> std::ffi::CString {
    std::ffi::CString::new(value).expect("static C string")
}
