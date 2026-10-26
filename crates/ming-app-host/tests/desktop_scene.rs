#![cfg(feature = "gpu")]
use ming_app_host::{
    desktop_model::WindowId,
    desktop_scene::DesktopScene,
    graphics::{FramePixels, GpuSession, Graphics, RenderedFrame, compositor::Compositor},
};
use std::sync::Arc;
fn dimensions(scene: &DesktopScene, id: WindowId) -> (usize, usize) {
    let rect = scene.model().view(id).unwrap().outer.content();
    (rect.width as usize, rect.height as usize)
}
fn cpu_frame(width: usize, height: usize, sequence: u64, color: u32) -> RenderedFrame {
    RenderedFrame {
        width,
        height,
        sequence,
        pixels: FramePixels::Cpu(Arc::new(vec![color; width * height])),
        render_us: 0,
        text_prepare_us: None,
        gpu_timing: None,
        commands: 0,
    }
}
fn application_frame(
    session: &GpuSession,
    width: usize,
    height: usize,
    sequence: usize,
    color: u32,
) -> RenderedFrame {
    let mut graphics = Graphics::with_gpu_session(session.clone());
    assert!(matches!(
        graphics.gpu_info().unwrap().device_type.as_str(),
        "DiscreteGpu" | "IntegratedGpu"
    ));
    let words = [
        0x4d474632i32,
        1,
        width as i32,
        height as i32,
        1,
        0,
        1,
        0,
        0,
        width as i32,
        height as i32,
        color as i32,
        0,
        0,
    ];
    let bytes: Vec<u8> = words.into_iter().flat_map(i32::to_le_bytes).collect();
    for _ in 0..sequence {
        graphics.submit(&bytes).unwrap();
    }
    graphics.last_frame().unwrap()
}
#[test]
#[ignore = "requires hardware GPU; links actual window model, application contexts, compositor and presentation acknowledgment"]
fn actual_gpu_window_scene_preserves_application_identity_through_minimize_and_restore() {
    let root = GpuSession::new().unwrap();
    let sessions = [
        root.fork_render_context().unwrap(),
        root.fork_render_context().unwrap(),
    ];
    let mut gpu = DesktopScene::new(1000, 700, Compositor::gpu(&root).unwrap()).unwrap();
    let mut cpu = DesktopScene::new(1000, 700, Compositor::cpu()).unwrap();
    let ga = gpu.model_mut().open(1).unwrap().0;
    let gb = gpu.model_mut().open(2).unwrap().0;
    let ca = cpu.model_mut().open(1).unwrap().0;
    let cb = cpu.model_mut().open(2).unwrap().0;
    let (aw, ah) = dimensions(&gpu, ga);
    let (bw, bh) = dimensions(&gpu, gb);
    let a = application_frame(&sessions[0], aw, ah, 7, 0xffb03020);
    let b = application_frame(&sessions[1], bw, bh, 11, 0xff2050d0);
    gpu.set_frame(ga, a).unwrap();
    gpu.set_frame(gb, b).unwrap();
    cpu.set_frame(ca, cpu_frame(aw, ah, 7, 0xffb03020)).unwrap();
    cpu.set_frame(cb, cpu_frame(bw, bh, 11, 0xff2050d0))
        .unwrap();
    for round in 0..3 {
        if round == 1 {
            gpu.model_mut().minimize(gb).unwrap();
            cpu.model_mut().minimize(cb).unwrap();
        }
        if round == 2 {
            gpu.model_mut().restore(gb).unwrap();
            cpu.model_mut().restore(cb).unwrap();
        }
        let actual = gpu.compose().unwrap();
        let expected = cpu.compose().unwrap();
        assert!(actual.cpu_pixels().is_none());
        for context in &sessions {
            assert_eq!(context.stats().unwrap().readback_bytes, 0);
        }
        assert_eq!(
            actual.read_pixels().unwrap(),
            expected.read_pixels().unwrap()
        );
        gpu.presented(actual.sequence).unwrap();
        cpu.presented(expected.sequence).unwrap();
        assert_eq!(gpu.model().view(ga).unwrap().presented_sequence, 7);
        if round != 1 {
            let v = gpu.model().view(gb).unwrap();
            assert_eq!(v.presented_sequence, 11);
            assert!(v.input_ready);
        }
    }
    assert_eq!(root.stats().unwrap().readback_bytes, 0);
}

#[test]
#[ignore = "requires hardware GPU; incompatible sources must not replace a healthy latest frame"]
fn scene_rejects_wrong_backend_and_foreign_device_before_replacing_latest() {
    let root = GpuSession::new().unwrap();
    let foreign_root = GpuSession::new().unwrap();
    let mut gpu = DesktopScene::new(800, 600, Compositor::gpu(&root).unwrap()).unwrap();
    let id = gpu.model_mut().open(1).unwrap().0;
    let (w, h) = dimensions(&gpu, id);
    gpu.set_frame(id, application_frame(&root, w, h, 1, 0xff00ff00))
        .unwrap();
    let first = gpu.compose().unwrap();
    let foreign = application_frame(&foreign_root, w, h, 2, 0xffff0000);
    assert!(
        gpu.set_frame(id, foreign).is_err(),
        "foreign GPU must be rejected before consuming sequence 2"
    );
    assert!(
        gpu.set_frame(id, cpu_frame(w, h, 2, 0xffff0000)).is_err(),
        "CPU input must not replace GPU content"
    );
    gpu.presented(first.sequence).unwrap();
    gpu.set_frame(id, application_frame(&root, w, h, 2, 0xff0000ff))
        .unwrap();
    let second = gpu.compose().unwrap();
    assert_eq!(second.sequence, 2);
    gpu.presented(second.sequence).unwrap();
    assert_eq!(gpu.model().view(id).unwrap().presented_sequence, 2);
    let mut cpu = DesktopScene::new(800, 600, Compositor::cpu()).unwrap();
    let cid = cpu.model_mut().open(1).unwrap().0;
    cpu.set_frame(cid, cpu_frame(w, h, 1, 0xff00ff00)).unwrap();
    assert!(
        cpu.set_frame(cid, application_frame(&root, w, h, 2, 0xffff0000))
            .is_err()
    );
    cpu.set_frame(cid, cpu_frame(w, h, 2, 0xff0000ff)).unwrap();
    assert!(cpu.compose().is_ok());
}
