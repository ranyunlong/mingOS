#![cfg(feature = "engine")]
use ming_app_host::cli::parse;
use std::ffi::OsString;
fn args(values: &[&str]) -> Vec<OsString> {
    values.iter().map(OsString::from).collect()
}
#[test]
fn extended_deadline_is_explicit_and_only_for_preview() {
    let normal = parse(args(&["view", "app.json"])).unwrap();
    assert_eq!(normal.timeout_ms, 60000);
    assert_eq!(normal.fuel, 1_000_000_000);
    let qa = parse(args(&[
        "view",
        "app.json",
        "--timeout-ms",
        "180000",
        "--auto-close-ms",
        "150000",
    ]))
    .unwrap();
    assert_eq!(qa.timeout_ms, 180000);
    assert!(parse(args(&["run", "app.json", "--timeout-ms", "180000"])).is_err());
    assert!(parse(args(&["view", "app.json", "--timeout-ms", "300001"])).is_err());
}

#[test]
fn renderer_selection_is_explicit_and_defaults_to_cpu() {
    use ming_app_host::graphics::RendererKind;
    assert_eq!(
        parse(args(&["run", "app.json"])).unwrap().renderer,
        RendererKind::Cpu
    );
    assert_eq!(
        parse(args(&["run", "app.json", "--renderer", "gpu"]))
            .unwrap()
            .renderer,
        RendererKind::Gpu
    );
    assert!(parse(args(&["run", "app.json", "--renderer", "auto"])).is_err());
    assert!(
        parse(args(&[
            "run",
            "app.json",
            "--renderer",
            "cpu",
            "--renderer",
            "gpu"
        ]))
        .is_err()
    );
}

#[test]
#[cfg(not(feature = "gpu"))]
fn gpu_selection_without_compiled_feature_fails_explicitly() {
    use ming_app_host::graphics::{Graphics, RendererKind};
    let error = Graphics::with_renderer(RendererKind::Gpu).err().unwrap();
    assert!(error.to_string().contains("GPU_FEATURE_REQUIRED"));
}
