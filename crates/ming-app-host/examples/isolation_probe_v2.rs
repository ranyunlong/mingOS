#[cfg(target_os = "linux")]
include!("../../../probes/linux-isolation-source-v2/probe.rs");

#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("isolation_probe_v2 requires Linux");
    std::process::exit(2);
}
