#![cfg(feature = "gpu")]
use ming_app_host::graphics::{GpuSession, GpuStats, Graphics, RenderedFrame};

fn png(color: u32) -> Vec<u8> {
    let mut bytes = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut bytes, 1, 1);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        encoder
            .write_header()
            .unwrap()
            .write_image_data(&[
                (color >> 16) as u8,
                (color >> 8) as u8,
                color as u8,
                (color >> 24) as u8,
            ])
            .unwrap();
    }
    bytes
}
fn frame(handle: i32) -> Vec<u8> {
    [0x4d474632i32, 1, 1, 1, 1, 0, 2, handle, 0, 0, 1, 1, 0, 0]
        .into_iter()
        .flat_map(i32::to_le_bytes)
        .collect()
}
fn stats(session: &GpuSession) -> serde_json::Value {
    serde_json::to_value(session.stats().unwrap()).unwrap()
}
fn draw(
    gpu: &mut Graphics,
    cpu: &mut Graphics,
    handle: i32,
    session: &GpuSession,
) -> RenderedFrame {
    let before = session.stats().unwrap();
    gpu.submit(&frame(handle)).unwrap();
    let rendered = gpu.last_frame().unwrap();
    assert!(rendered.cpu_pixels().is_none());
    assert_eq!(
        session.stats().unwrap().readback_bytes,
        before.readback_bytes
    );
    cpu.submit(&frame(handle)).unwrap();
    let expected = cpu.last_frame().unwrap().read_pixels().unwrap();
    let actual = rendered.read_pixels().unwrap();
    assert_eq!(actual, expected);
    assert_eq!(
        session.stats().unwrap().readback_bytes,
        before.readback_bytes + 4
    );
    println!(
        "handle={handle} sequence={} expected={:08x} actual={:08x} stats={}",
        rendered.sequence,
        expected[0],
        actual[0],
        stats(session)
    );
    rendered
}

#[test]
#[ignore = "requires a hardware GPU; checks independent app contexts on one device"]
fn forked_images_warm_close_and_saved_frames_are_isolated() {
    let device = GpuSession::new().unwrap();
    let a_session = device.fork_render_context().unwrap();
    let b_session = device.fork_render_context().unwrap();
    let alias = a_session.clone();
    assert_eq!(
        stats(&a_session),
        serde_json::to_value(GpuStats::default()).unwrap()
    );
    assert_eq!(stats(&b_session), stats(&a_session));
    let mut a = Graphics::with_gpu_session(a_session.clone());
    let mut b = Graphics::with_gpu_session(b_session.clone());
    let mut cpu_a = Graphics::new();
    let mut cpu_b = Graphics::new();
    for (gpu, cpu, color) in [
        (&mut a, &mut cpu_a, 0xffff0000),
        (&mut b, &mut cpu_b, 0xff0000ff),
    ] {
        let bytes = png(color);
        assert_eq!(gpu.open_png(&bytes).unwrap(), 1);
        assert_eq!(cpu.open_png(&bytes).unwrap(), 1);
    }
    let saved = draw(&mut a, &mut cpu_a, 1, &a_session);
    draw(&mut b, &mut cpu_b, 1, &b_session);
    for _ in 0..3 {
        draw(&mut a, &mut cpu_a, 1, &a_session);
        draw(&mut b, &mut cpu_b, 1, &b_session);
    }
    assert_eq!(a_session.stats().unwrap().image_uploads, 1);
    assert_eq!(b_session.stats().unwrap().image_uploads, 1);
    assert_eq!(stats(&alias), stats(&a_session));
    assert_eq!(device.stats().unwrap().render_submissions, 0);
    let before_b = stats(&b_session);
    assert!(a.close_image(1));
    assert_eq!(b.dimensions(1), Some((1, 1)));
    assert_eq!(stats(&b_session), before_b);
    drop(a);
    drop(a_session);
    drop(alias);
    draw(&mut b, &mut cpu_b, 1, &b_session);
    assert_eq!(b_session.stats().unwrap().image_uploads, 1);
    let before_b = stats(&b_session);
    assert_eq!(saved.read_pixels().unwrap().as_slice(), &[0xffff0000]);
    assert_eq!(stats(&b_session), before_b);
}

#[test]
#[ignore = "requires a hardware GPU; checks independent eight-image cache and resource quotas"]
fn forked_image_capacity_and_failed_admission_do_not_affect_siblings() {
    let device = GpuSession::new().unwrap();
    let a_session = device.fork_render_context().unwrap();
    let b_session = device.fork_render_context().unwrap();
    let mut a = Graphics::with_gpu_session(a_session.clone());
    let mut b = Graphics::with_gpu_session(b_session.clone());
    let mut cpu_a = Graphics::new();
    let mut cpu_b = Graphics::new();
    for index in 1..=8 {
        for (gpu, cpu, color) in [
            (&mut a, &mut cpu_a, 0xffff0000),
            (&mut b, &mut cpu_b, 0xff0000ff),
        ] {
            let bytes = png(color);
            assert_eq!(gpu.open_png(&bytes).unwrap(), index);
            assert_eq!(cpu.open_png(&bytes).unwrap(), index);
        }
    }
    draw(&mut a, &mut cpu_a, 1, &a_session);
    draw(&mut b, &mut cpu_b, 1, &b_session);
    assert_eq!(a_session.stats().unwrap().image_uploads, 8);
    assert_eq!(b_session.stats().unwrap().image_uploads, 8);
    let before_a = stats(&a_session);
    let before_b = stats(&b_session);
    assert_eq!(
        a.open_png(&png(0xffff0000)).unwrap_err().to_string(),
        "IMAGE_COUNT_LIMIT"
    );
    assert_eq!(stats(&a_session), before_a);
    assert_eq!(stats(&b_session), before_b);
    assert!(a.close_image(8));
    assert_eq!(a.open_png(&png(0xffff0000)).unwrap(), 9);
    assert_eq!(stats(&b_session), before_b);
    draw(&mut b, &mut cpu_b, 1, &b_session);
    assert_eq!(b_session.stats().unwrap().image_uploads, 8);
}

#[test]
#[ignore = "requires a hardware GPU; clone remains a session alias"]
fn clone_preserves_alias_statistics_and_resource_state() {
    let session = GpuSession::new().unwrap();
    let alias = session.clone();
    let mut gpu = Graphics::with_gpu_session(session.clone());
    let mut cpu = Graphics::new();
    assert_eq!(gpu.open_png(&png(0xffff0000)).unwrap(), 1);
    assert_eq!(cpu.open_png(&png(0xffff0000)).unwrap(), 1);
    draw(&mut gpu, &mut cpu, 1, &session);
    assert_eq!(stats(&session), stats(&alias));
    assert_eq!(alias.stats().unwrap().cached_images, 1);
    assert!(gpu.close_image(1));
    assert_eq!(alias.stats().unwrap().cached_images, 0);
}
