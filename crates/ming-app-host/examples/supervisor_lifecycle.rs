//! Reproduce supervisor cancellation races with real Wasmtime child processes.
//! This is a validation tool, not a guest capability or a strict-isolation test.
#[cfg(feature = "engine")]
mod probe {
    use anyhow::{Result, bail};
    use ming_app_host::{engine, supervisor, ui::ViewBridge};
    use serde_json::json;
    use std::{
        fs,
        sync::{Arc, atomic::{AtomicBool, Ordering}},
        thread,
        time::Instant,
    };

    pub fn main() -> Result<()> {
        let mut args = std::env::args_os().skip(1);
        let output = args.next();
        if output.as_ref().is_some_and(|arg| arg == "__worker") {
            if args.next().is_some() {
                bail!("INVALID_WORKER_ARGUMENTS");
            }
            if let Err(error) = engine::worker() {
                engine::report_worker_error(&error);
                std::process::exit(1);
            }
            return Ok(());
        }
        if output.is_some() {
            bail!("usage: supervisor_lifecycle");
        }

        let directory = tempfile::tempdir()?;
        let manifest = directory.path().join("app.json");
        fs::write(
            directory.path().join("main.wasm"),
            wat::parse_str(r#"(module (func (export "_start")))"#)?,
        )?;
        fs::write(
            &manifest,
            serde_json::to_vec(&json!({
                "schemaVersion": 1, "id": "dev.ming.supervisor-lifecycle",
                "version": "0.1.0", "sdkVersion": 1, "entry": "main.wasm",
            }))?,
        )?;

        let framed_manifest = directory.path().join("framed.json");
        let frame: String = [0x4d474632u32, 1, 1, 1, 1, 0, 1, 0, 0, 1, 1, 0xffa03020, 0, 0]
            .into_iter().flat_map(u32::to_le_bytes).map(|byte| format!("\\{byte:02x}")).collect();
        let framed = format!(r#"(module
            (import "ming_v2" "frame_submit" (func $draw(param i32 i32)(result i32)))
            (import "ming_frame_v1" "wait" (func $wait(param i32 i32 i32 i32)(result i32)))
            (memory(export "memory")1)(data(i32.const 0)"{frame}")
            (data(i32.const 64)"\01\00\00\00\02\00\00\00\ff\ff\ff\ff\ff\ff\ff\ff")
            (func(export "_start")
                i32.const 0 i32.const 56 call $draw drop
                loop $poll
                    i32.const 64 i32.const 16 i32.const 128 i32.const 48 call $wait drop
                    i32.const 132 i32.load i32.const 5 i32.ne br_if $poll
                end))"#);
        fs::write(directory.path().join("framed.wasm"), wat::parse_str(framed)?)?;
        fs::write(&framed_manifest, serde_json::to_vec(&json!({
            "schemaVersion": 1, "id": "dev.ming.supervisor-cancellation-frame",
            "version": "0.1.0", "sdkVersion": 5, "entry": "framed.wasm",
        }))?)?;

        let mut cases = Vec::new();
        // Repeated trials expose the original race; the fixed contract must hold
        // regardless of whether the polling watcher gets a turn before exit.
        for (name, close, cancel, after_frame, trials) in [
            ("normal_completion", false, false, false, 1),
            ("normal_close", true, false, false, 1),
            ("cancel_before_run", false, true, false, 32),
            ("close_then_explicit_cancel", true, true, false, 32),
            ("cancel_after_frame", false, true, true, 32),
            ("close_then_cancel_after_frame", true, true, true, 32),
        ] {
            for trial in 0..trials {
                let (bridge, _sender) = ViewBridge::new();
                if close && !after_frame {
                    bridge.request_close();
                }
                if cancel && !after_frame {
                    bridge.cancel.store(true, Ordering::SeqCst);
                }
                let finished = Arc::new(AtomicBool::new(false));
                let triggered = Arc::new(AtomicBool::new(false));
                let canceller = after_frame.then(|| {
                    let bridge = bridge.clone();
                    let finished = finished.clone();
                    let triggered = triggered.clone();
                    thread::spawn(move || {
                        while !finished.load(Ordering::SeqCst) {
                            if bridge.latest.lock().expect("frame receipt").is_some() {
                                if close { bridge.request_close(); }
                                bridge.cancel.store(true, Ordering::SeqCst);
                                triggered.store(true, Ordering::SeqCst);
                                return;
                            }
                            thread::yield_now();
                        }
                    })
                });
                let mut options = supervisor::Options::new(if after_frame { framed_manifest.clone() } else { manifest.clone() });
                options.live = Some(bridge.clone());
                let started = Instant::now();
                let outcome = supervisor::run(options);
                let elapsed_us = started.elapsed().as_micros();
                finished.store(true, Ordering::SeqCst);
                if let Some(canceller) = canceller { canceller.join().expect("cancellation driver"); }
                let triggered = triggered.load(Ordering::SeqCst);
                let error = outcome.as_ref().err().map(|error| format!("{error:#}"));
                let passed = (!after_frame || triggered) && if cancel {
                    error.as_deref() == Some("APP_CANCELLED")
                } else {
                    outcome.is_ok()
                };
                cases.push(json!({
                    "name": name, "trial": trial, "passed": passed,
                    "closeRequested": close, "cancelRequested": cancel,
                    "cancelFlagAfterRun": bridge.cancel.load(Ordering::SeqCst),
                    "elapsedMicros": elapsed_us, "error": error,
                    "cancelAfterFrame": after_frame, "frameTriggerObserved": triggered,
                    "frameClock": outcome.as_ref().ok().and_then(|value| value.frame_clock.as_ref()),
                    "fuelConsumed": outcome.as_ref().ok().map(|value| value.fuel_consumed),
                }));
                if after_frame && !triggered { break; }
            }
        }
        let failed = cases.iter().filter(|case| case["passed"] != true).count();
        let report = json!({
            "schemaVersion": 1,
            "scope": "Real Wasmtime child processes; bounded supervisor with parent-owned ViewBridge; no strict guest or persistent desktop qualification",
            "status": if failed == 0 { "passed" } else { "failed" },
            "total": cases.len(), "failed": failed, "cases": cases,
        });
        println!("{}", serde_json::to_string_pretty(&report)?);
        if failed != 0 {
            bail!("SUPERVISOR_CANCELLATION_CONTRACT: {failed} trials failed");
        }
        Ok(())
    }
}

#[cfg(feature = "engine")]
fn main() {
    if let Err(error) = probe::main() {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}

#[cfg(not(feature = "engine"))]
fn main() {
    eprintln!("ENGINE_FEATURE_REQUIRED");
    std::process::exit(1);
}
