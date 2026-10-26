#![cfg(feature = "desktop")]
use ming_app_host::{desktop_catalog::DesktopOptions,protocol::{Init,WorkerMessage}};
use serde_json::json;
#[test]
fn isolation_flag_is_explicit_strict_or_off() {
    assert!(DesktopOptions::parse(["catalog","--isolation","off"].map(Into::into)).is_ok());
    assert!(DesktopOptions::parse(["catalog","--isolation","strict"].map(Into::into)).is_ok());
    for value in ["auto","unconfined","","Strict"] {assert!(DesktopOptions::parse(["catalog","--isolation",value].map(Into::into)).is_err());}
    assert!(DesktopOptions::parse(["catalog","--isolation","off","--isolation","strict"].map(Into::into)).is_err());
}
#[test]
fn optional_private_policy_and_ready_report_are_wire_compatible() {
    let bounded=json!({"wasm":"AA==","fuel":1000,"memoryBytes":65536,"sdkVersion":2});
    let init:Init=serde_json::from_value(bounded.clone()).unwrap();assert_eq!(serde_json::to_value(init).unwrap(),bounded);
    let mut strict=bounded;strict["isolation"]=json!({"app":1,"generation":1,"uid":20001,"gid":20001,"expectedParent":12,"workerPid":13,"memoryBytes":536870912u64,"pids":64});
    assert!(serde_json::from_value::<Init>(strict.clone()).is_ok());
    strict["isolation"]["uid"]=(-1).into();assert!(serde_json::from_value::<Init>(strict).is_err());
    let ready:WorkerMessage=serde_json::from_value(json!({"type":"ready"})).unwrap();assert_eq!(serde_json::to_value(ready).unwrap(),json!({"type":"ready"}));
    assert!(serde_json::from_value::<WorkerMessage>(json!({"type":"ready","isolation":{"uid":20001,"gid":20001,"pid":13,"parent":12,"pidNamespace":false,"namespaceIds":[1,2,3,4]}})).is_ok());
}
