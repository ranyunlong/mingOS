#![cfg(feature = "desktop")]
use serde_json::{Value, json};
use std::{
    fs,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
#[test]
fn real_sdk6_workers_receive_only_their_own_ordered_text() {
    let d = tempfile::tempdir().unwrap();
    let wat = r#"(module
(import "ming_v2" "frame_submit"(func $draw(param i32 i32)(result i32)))
(import "ming_v2" "event_next"(func $next(param i32 i32)(result i32)))
(import "ming_input_v1" "poll"(func $poll(param i32 i32)(result i32)))
(import "ming_v1" "log"(func $log(param i32 i32)(result i32)))
(memory(export "memory")1)(data(i32.const 0)"\32\46\47\4d\01\00\00\00\01\00\00\00\01\00\00\00")
(func(export "_start")(local $n i32)
i32.const 0 i32.const 24 call $draw drop
block $end loop $again
i32.const 128 i32.const 16 call $next drop
i32.const 128 i32.load i32.eqz br_if $end
i32.const 128 i32.load i32.const 1 i32.eq if
i32.const 8 i32.const 132 i32.load i32.store
i32.const 12 i32.const 136 i32.load i32.store
i32.const 0 i32.const 24 call $draw drop end
block $drained loop $drain
i32.const 256 i32.const 568 call $poll local.tee $n i32.const 0 i32.le_s br_if $drained
i32.const 260 i32.load i32.const 1 i32.eq if i32.const 720 i32.const 312 i32.load8_u call $log drop end
br $drain end end
br $again end end))"#;
    for app in [1, 2] {
        let p = d.path().join(app.to_string());
        fs::create_dir(&p).unwrap();
        fs::write(p.join("main.wasm"), wat::parse_str(wat).unwrap()).unwrap();
        fs::write(p.join("app.json"),json!({"schemaVersion":1,"id":format!("dev.input.test{app}"),"version":"0.1.0","sdkVersion":6,"entry":"main.wasm"}).to_string()).unwrap();
    }
    fs::write(d.path().join("catalog.json"),json!({"schemaVersion":1,"apps":[{"id":1,"title":"One","manifest":"1/app.json"},{"id":2,"title":"Two","manifest":"2/app.json"}],"autostart":[1,2]}).to_string()).unwrap();
    fs::write(
        d.path().join("events.json"),
        json!([
        {"afterMs":0,"action":{"op":"click","app":1,"x":20,"y":20}},
        {"afterMs":0,"action":{"op":"text","app":1,"text":"甲A"}},
        {"afterMs":0,"action":{"op":"focus","app":2}},
        {"afterMs":0,"action":{"op":"text","app":2,"text":"乙B"}}
        ])
        .to_string(),
    )
    .unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("desktop")
        .arg(d.path().join("catalog.json"))
        .args([
            "--renderer",
            "cpu",
            "--backend",
            "offscreen",
            "--duration-ms",
            "300",
            "--events",
        ])
        .arg(d.path().join("events.json"))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(30);
    while child.try_wait().unwrap().is_none() {
        if Instant::now() > deadline {
            child.kill().unwrap();
            panic!("typed desktop timeout");
        }
        thread::sleep(Duration::from_millis(5));
    }
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let v: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(v["actionsExecuted"], 4);
    assert_eq!(v["droppedInputs"], 0);
    for (app, byte) in [(1, 231), (2, 228)] {
        let record = v["instances"]
            .as_array()
            .unwrap()
            .iter()
            .find(|r| r["app"] == app)
            .unwrap();
        assert_eq!(record["status"], "closed");
        let events = record["events"].as_array().unwrap();
        assert_eq!(
            events
                .iter()
                .filter(|e| e["code"] == 720)
                .collect::<Vec<_>>(),
            vec![&json!({"code":720,"value":byte})]
        );
    }
}
