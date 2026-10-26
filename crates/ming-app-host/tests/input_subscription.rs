#![cfg(feature = "desktop")]
use serde_json::{Value, json};
use std::{fs, process::{Command, Stdio}, thread, time::{Duration, Instant}};

#[test]
fn sdk6_without_text_input_calls_does_not_receive_repeating_rich_wakes() {
    let dir = tempfile::tempdir().unwrap();
    let source = r#"(module
      (import "ming_v2" "frame_submit" (func $draw (param i32 i32) (result i32)))
      (import "ming_v2" "event_next" (func $next (param i32 i32) (result i32)))
      (memory (export "memory") 1)
      (data (i32.const 0) "\32\46\47\4d\01\00\00\00\01\00\00\00\01\00\00\00")
      (func (export "_start")
        i32.const 0 i32.const 24 call $draw drop
        block $closed loop $events
          i32.const 128 i32.const 16 call $next drop
          i32.const 128 i32.load i32.eqz br_if $closed
          i32.const 128 i32.load i32.const 1 i32.eq if
            i32.const 8 i32.const 132 i32.load i32.store
            i32.const 12 i32.const 136 i32.load i32.store
            i32.const 0 i32.const 24 call $draw drop
          end
          br $events
        end end))"#;
    for id in [1, 2] {
        let app = dir.path().join(id.to_string());
        fs::create_dir(&app).unwrap();
        fs::write(app.join("main.wasm"), wat::parse_str(source).unwrap()).unwrap();
        fs::write(app.join("app.json"), json!({"schemaVersion":1,"id":format!("dev.input.unsubscribed{id}"),"version":"0.1.0","sdkVersion":6,"entry":"main.wasm"}).to_string()).unwrap();
    }
    fs::write(dir.path().join("catalog.json"), json!({"schemaVersion":1,"apps":[{"id":1,"title":"One","manifest":"1/app.json"},{"id":2,"title":"Two","manifest":"2/app.json"}],"autostart":[1,2]}).to_string()).unwrap();
    fs::write(dir.path().join("events.json"), json!([{"afterMs":50,"action":{"op":"close","app":1}}]).to_string()).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_ming-app-host"))
        .arg("desktop").arg(dir.path().join("catalog.json"))
        .args(["--renderer","cpu","--backend","offscreen","--duration-ms","400","--events"])
        .arg(dir.path().join("events.json")).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    let deadline = Instant::now()+Duration::from_secs(20);
    while child.try_wait().unwrap().is_none() {
        if Instant::now()>deadline { let _=child.kill();let _=child.wait();panic!("subscription regression exceeded deadline"); }
        thread::sleep(Duration::from_millis(5));
    }
    let output=child.wait_with_output().unwrap();
    assert!(output.status.success(),"{}",String::from_utf8_lossy(&output.stderr));
    let value:Value=serde_json::from_slice(&output.stdout).unwrap();
    if let Some(path)=std::env::var_os("MING_SUBSCRIPTION_EVIDENCE") { fs::write(path,&output.stdout).unwrap(); }
    assert_eq!(value["activeWorkersAfterShutdown"],0);
    let app=value["instances"].as_array().unwrap().iter().find(|a|a["app"]==2).unwrap();
    assert_eq!(app["status"],"closed","{value}");assert_eq!(app["error"],Value::Null,"{value}");
    assert_eq!(app["inputCounts"][1],1,"An SDK6 app which never requests text input should receive only its initial viewport, not rich FIFO wakes: {value}");
    // Private idle tokens are normal 20ms wakeups; only typed resize wakes must be absent.
}

