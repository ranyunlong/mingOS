#![cfg(feature = "desktop")]
use serde_json::{Value, json};
use std::{
    fs,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
fn fixture(dir: &std::path::Path, name: &str, color: u32) {
    let app = dir.join(name);
    fs::create_dir(&app).unwrap();
    let mut words = vec![0x4d474632u32, 1, 1, 1, 1, 0, 1, 0, 0, 1, 1, color, 0, 0];
    let data: String = words
        .drain(..)
        .flat_map(u32::to_le_bytes)
        .map(|b| format!("\\{b:02x}"))
        .collect();
    let source = format!(
        r#"(module
 (import "ming_v2" "frame_submit" (func $draw(param i32 i32)(result i32)))
 (import "ming_v2" "event_next" (func $next(param i32 i32)(result i32)))
 (import "ming_v1" "log" (func $log(param i32 i32)(result i32)))
 (memory(export "memory")1)(data(i32.const 0)"{data}")
 (func(export "_start")(local $kind i32)
 i32.const 0 i32.const 56 call $draw drop
 block $end loop $again
 i32.const 128 i32.const 16 call $next drop
 i32.const 128 i32.load local.tee $kind i32.eqz br_if $end
 local.get $kind i32.const 1 i32.eq if
 i32.const 8 i32.const 132 i32.load i32.store
 i32.const 12 i32.const 136 i32.load i32.store
 i32.const 36 i32.const 132 i32.load i32.store
 i32.const 40 i32.const 136 i32.load i32.store
 i32.const 0 i32.const 56 call $draw drop end
 local.get $kind i32.const 4 i32.eq if i32.const 701 i32.const 1 call $log drop end
 br $again end end))"#
    );
    fs::write(app.join("main.wasm"), wat::parse_str(source).unwrap()).unwrap();
    fs::write(app.join("app.json"),json!({"schemaVersion":1,"id":format!("dev.desktop.{name}"),"version":"0.1.0","sdkVersion":2,"entry":"main.wasm"}).to_string()).unwrap();
}
fn run(renderer: &str) -> Value {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path(), "red", 0xffa03020);
    fixture(dir.path(), "blue", 0xff2050b0);
    fs::write(dir.path().join("catalog.json"),json!({"schemaVersion":1,"apps":[{"id":1,"title":"Red","manifest":"red/app.json"},{"id":2,"title":"Blue","manifest":"blue/app.json"}],"autostart":[1,2]}).to_string()).unwrap();
    fs::write(
        dir.path().join("events.json"),
        json!([
        {"afterMs":0,"action":{"op":"click","app":2,"x":20,"y":20}},
        {"afterMs":50,"action":{"op":"minimize","app":1}},
        {"afterMs":100,"action":{"op":"restore","app":1}},
        {"afterMs":150,"action":{"op":"geometry","app":2,"x":90,"y":90,"width":480,"height":360}},
        {"afterMs":250,"action":{"op":"terminate","app":1}},
        {"afterMs":350,"action":{"op":"launch","app":1}},
        {"afterMs":400,"action":{"op":"click","app":1,"x":20,"y":20}},
        {"afterMs":500,"action":{"op":"close","app":2}},
        {"afterMs":650,"action":{"op":"launch","app":2}}
        ])
        .to_string(),
    )
    .unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("desktop")
        .arg(dir.path().join("catalog.json"))
        .args([
            "--backend",
            "offscreen",
            "--renderer",
            renderer,
            "--width",
            "800",
            "--height",
            "600",
            "--duration-ms",
            "900",
            "--events",
        ])
        .arg(dir.path().join("events.json"))
        .arg("--state-file")
        .arg(dir.path().join("state.json"))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut saw_ready = false;
    let mut state_session = None;
    let deadline = Instant::now() + Duration::from_secs(30);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > deadline {
            child.kill().unwrap();
            panic!("desktop runtime exceeded test deadline");
        }
        if let Ok(bytes) = fs::read(dir.path().join("state.json")) {
            let current: Value =
                serde_json::from_slice(&bytes).expect("atomic state must always be complete JSON");
            assert_eq!(current["schemaVersion"], 1);
            if current["phase"] == "ready" {
                saw_ready = true;
                state_session = Some(current["sessionId"].clone());
            }
        }
        thread::sleep(Duration::from_millis(10));
    }
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        saw_ready,
        "must observe actual app/presentation readiness before exit"
    );
    let final_state: Value =
        serde_json::from_slice(&fs::read(dir.path().join("state.json")).unwrap()).unwrap();
    assert_eq!(final_state["phase"], "stopped");
    assert_eq!(Some(final_state["sessionId"].clone()), state_session);
    assert!(final_state["revision"].as_u64().unwrap() > 1);
    serde_json::from_slice(&output.stdout).unwrap()
}
fn verify(v: Value) {
    assert_eq!(v["runtime"], "ming-desktop");
    assert_eq!(v["windowSystem"], "offscreen");
    assert_eq!(v["activeWorkersAfterShutdown"], 0);
    assert!(v["maxConcurrentWorkers"].as_u64().unwrap() >= 2);
    assert_eq!(v["actionsExecuted"], 9);
    assert!(v["offscreenFrames"].as_u64().unwrap() > 0);
    assert_eq!(v["framesPresented"], 0);
    assert_eq!(v["droppedInputs"], 0);
    assert!(v["acceptedRoot"]["sequence"].as_u64().unwrap() > 0);
    assert!(v["presentedFrame"].is_null());
    let apps = v["instances"].as_array().unwrap();
    assert_eq!(apps.len(), 4, "{v}");
    let pids: std::collections::BTreeSet<_> = apps
        .iter()
        .map(|a| a["workerPid"].as_u64().unwrap())
        .collect();
    assert_eq!(pids.len(), 4);
    assert!(!pids.contains(&0));
    assert!(apps.iter().all(|a| a["completed"] == true));
    assert!(
        apps.iter()
            .any(|a| a["app"] == 1 && a["generation"] == 1 && a["status"] == "failed")
    );
    assert!(apps.iter().any(|a| a["app"] == 1
        && a["generation"] == 2
        && a["framesSubmitted"].as_u64().unwrap() > 0));
    assert!(
        apps.iter()
            .any(|a| a["app"] == 2 && a["generation"] == 1 && a["status"] == "closed")
    );
    assert!(apps.iter().any(|a| {
        a["app"] == 2
            && a["events"]
                .as_array()
                .unwrap()
                .iter()
                .any(|e| e["code"] == 701)
    }));
}
#[test]
fn cpu_desktop_uses_independent_workers_and_recovers_without_losing_sibling() {
    verify(run("cpu"));
}
#[cfg(feature = "gpu")]
#[test]
#[ignore = "requires actual hardware GPU; native worker lifecycle with shared device"]
fn gpu_desktop_uses_independent_workers_and_zero_readback() {
    let v = run("gpu");
    assert_eq!(v["sceneGpuStats"]["readbackBytes"], 0);
    assert_eq!(v["shellGpuStats"]["readbackBytes"], 0);
    verify(v);
}
