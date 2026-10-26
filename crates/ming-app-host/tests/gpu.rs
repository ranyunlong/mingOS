#![cfg(feature = "gpu")]
use ming_app_host::graphics::{Graphics, RendererKind};

fn frame(w: i32, h: i32, commands: &[[i32; 8]], text: &[u8]) -> Vec<u8> {
    [
        0x4d474632,
        1,
        w,
        h,
        commands.len() as i32,
        text.len() as i32,
    ]
    .into_iter()
    .chain(commands.iter().flatten().copied())
    .flat_map(i32::to_le_bytes)
    .chain(text.iter().copied())
    .collect()
}
fn image() -> Vec<u8> {
    let mut data = Vec::new();
    {
        let mut e = png::Encoder::new(&mut data, 3, 2);
        e.set_color(png::ColorType::Rgba);
        e.set_depth(png::BitDepth::Eight);
        e.write_header()
            .unwrap()
            .write_image_data(&[
                255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 0, 20, 30, 40, 200, 255, 255, 255, 255,
                0, 0, 0, 255,
            ])
            .unwrap();
    }
    data
}
#[test]
#[ignore = "requires a hardware GPU; run explicitly, unavailable hardware is a failure"]
fn gpu_matches_reference_for_every_sdk_command_and_resource_lifecycle() {
    let mut cpu = Graphics::new();
    let mut gpu = Graphics::with_renderer(RendererKind::Gpu).unwrap();
    let meta = gpu.gpu_info().unwrap();
    assert_ne!(meta.device_type, "Cpu");
    println!("{}", serde_json::to_string(meta).unwrap());
    for g in [&mut cpu, &mut gpu] {
        assert_eq!(g.open_png(&image()).unwrap(), 1);
    }
    let text = "aZ. +%?中".as_bytes();
    let commands = [
        [1, 0, 0, 127, 91, 0xff123456u32 as i32, 0, 0],
        [1, -7, 2, 45, 39, 0x80604523u32 as i32, 19, 0],
        [1, 25, 1, 51, 55, 0x00112233, 64, 0],
        [1, 100, 50, 7, 5, -1, 9, 0],
        [2, 1, -5, 15, 71, 38, 0, 0],
        [4, 11, 9, 90, 63, 0, 0, 0],
        [3, 3, 3, 2, 0xc0fedcbau32 as i32, 0, text.len() as i32, 0],
        [2, 1, -999_900, -999_900, 1_000_000, 1_000_000, 0, 0],
        [1, 80, 50, 10, 10, 0x01102030, 2, 0],
        [4, 0, 0, 0, 0, 0, 0, 0],
        [1, 0, 0, 127, 91, -1, 0, 0],
    ];
    for n in 0..=commands.len() {
        let bytes = frame(127, 91, &commands[..n], text);
        cpu.submit(&bytes).unwrap();
        gpu.submit(&bytes).unwrap();
        assert_eq!(
            cpu.last_frame().unwrap().read_pixels().unwrap(),
            gpu.last_frame().unwrap().read_pixels().unwrap(),
            "command prefix {n}"
        );
    }
    // Exercise unsigned products above i32::MAX in image coordinate mapping.
    let mut wide_png = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut wide_png, 4096, 1);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let pixels: Vec<u8> = (0..4096u32)
            .flat_map(|i| [i as u8, (i >> 8) as u8, 73, 255])
            .collect();
        encoder
            .write_header()
            .unwrap()
            .write_image_data(&pixels)
            .unwrap();
    }
    for g in [&mut cpu, &mut gpu] {
        assert_eq!(g.open_png(&wide_png).unwrap(), 2);
    }
    let large_dest = frame(127, 91, &[[2, 2, -999_900, 0, 1_000_000, 91, 0, 0]], &[]);
    cpu.submit(&large_dest).unwrap();
    gpu.submit(&large_dest).unwrap();
    assert_eq!(
        cpu.last_frame().unwrap().read_pixels().unwrap(),
        gpu.last_frame().unwrap().read_pixels().unwrap()
    );
    // Seeded scenes vary alpha, clipping, corner radii and font scales reproducibly.
    let mut seed = 1947u32;
    for _ in 0..64 {
        let mut commands = Vec::new();
        for i in 0..12 {
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            let x = (seed % 160) as i32 - 30;
            let y = ((seed >> 8) % 120) as i32 - 20;
            commands.push(match i % 4 {
                0 => [4, x, y, 64, 51, 0, 0, 0],
                1 => [
                    1,
                    x,
                    y,
                    1 + (seed % 95) as i32,
                    1 + ((seed >> 8) % 73) as i32,
                    seed as i32,
                    (seed % 65) as i32,
                    0,
                ],
                2 => [2, 1, x, y, 45, 37, 0, 0],
                _ => [
                    3,
                    x,
                    y,
                    1 + (seed % 4) as i32,
                    seed as i32,
                    0,
                    text.len() as i32,
                    0,
                ],
            });
        }
        let bytes = frame(127, 91, &commands, text);
        cpu.submit(&bytes).unwrap();
        gpu.submit(&bytes).unwrap();
        assert_eq!(
            cpu.last_frame().unwrap().read_pixels().unwrap(),
            gpu.last_frame().unwrap().read_pixels().unwrap(),
            "seed {seed}"
        );
    }
    let timing_frame = gpu.last_frame().unwrap();
    let timing = timing_frame.gpu_timing.as_ref().unwrap();
    assert!(timing.pack_micros + timing.execute_readback_micros <= timing_frame.render_us);
    let seq = timing_frame.sequence;
    assert!(
        gpu.submit(&frame(4, 4, &[[2, 999, 0, 0, 4, 4, 0, 0]], &[]))
            .is_err()
    );
    assert_eq!(gpu.last_frame().unwrap().sequence, seq);
    assert!(gpu.close_image(1));
    assert!(
        gpu.submit(&frame(4, 4, &[[2, 1, 0, 0, 4, 4, 0, 0]], &[]))
            .is_err()
    );
    assert_eq!(gpu.last_frame().unwrap().sequence, seq);
    // Subsequent valid frame recovers after validation failure.
    gpu.submit(&frame(1, 1, &[], &[])).unwrap();
    assert_eq!(
        &*gpu.last_frame().unwrap().read_pixels().unwrap(),
        &[0xff000000]
    );
    assert!(
        gpu.submit(&frame(
            2048,
            2048,
            &vec![[1, 0, 0, 2048, 2048, -1, 0, 0]; 9],
            &[]
        ))
        .is_err()
    );
    assert!(
        gpu.submit(&frame(2048, 2048, &vec![[4, 0, 0, 0, 0, 0, 0, 0]; 16], &[]))
            .is_err()
    );
}

#[test]
#[ignore = "requires hardware GPU; explicitly verifies direct frames and image reuse"]
fn direct_frames_keep_pixels_on_gpu_and_reuse_image_uploads() {
    use ming_app_host::graphics::GpuSession;
    let session = GpuSession::new().unwrap();
    let mut graphics = Graphics::with_gpu_session(session.clone());
    let id = graphics.open_png(&image()).unwrap();
    let bytes = frame(127, 91, &[[2, id, 0, 0, 127, 91, 0, 0]], &[]);
    for _ in 0..3 {
        graphics.submit(&bytes).unwrap();
    }
    let rendered = graphics.last_frame().unwrap();
    assert!(rendered.cpu_pixels().is_none());
    let stats = session.stats().unwrap();
    assert_eq!(stats.readback_bytes, 0);
    assert_eq!(stats.image_uploads, 1);
    assert_eq!(stats.image_upload_bytes, 24);
    assert_eq!(stats.render_submissions, 3);
    let mut reference = Graphics::new();
    reference.open_png(&image()).unwrap();
    reference.submit(&bytes).unwrap();
    assert_eq!(
        rendered.read_pixels().unwrap(),
        reference.last_frame().unwrap().read_pixels().unwrap()
    );
    assert_eq!(session.stats().unwrap().readback_bytes, 127 * 91 * 4);
    assert!(graphics.close_image(id));
    assert_eq!(session.stats().unwrap().cached_images, 0);
    assert!(graphics.submit(&bytes).is_err());
    assert_eq!(session.stats().unwrap().image_uploads, 1);
    let new_id = graphics.open_png(&image()).unwrap();
    graphics
        .submit(&frame(2, 2, &[[2, new_id, 0, 0, 2, 2, 0, 0]], &[]))
        .unwrap();
    assert_eq!(session.stats().unwrap().image_uploads, 2);
}

#[test]
#[cfg(feature = "text")]
#[ignore = "requires hardware GPU; SDK3 coverage and persistent glyph reuse"]
fn sdk3_gpu_text_matches_cpu_and_reuses_coverage_across_movement_and_color() {
    use ming_app_host::{graphics::GpuSession, text::TextService};
    let mut service = TextService::new().unwrap();
    let h = service.create("MingOS 中文 fi A\u{0301}".as_bytes(), 1, 24 * 64, 0);
    assert!(h > 0);
    let session = GpuSession::new().unwrap();
    let mut gpu = Graphics::with_gpu_session(session.clone());
    let mut cpu = Graphics::new();
    let commands = |x, color| {
        vec![
            [1, 0, 0, 256, 128, -1, 0, 0],
            [4, 2, 2, 250, 110, 0, 0, 0],
            [5, h, x, 80 * 64, color, 0, 0, 0],
            [3, 8, 8, 2, 0xffdd1122u32 as i32, 0, 1, 0],
        ]
    };
    let bytes = frame(256, 128, &commands(8 * 64, 0x8020354fu32 as i32), b"a");
    cpu.submit_with_text(&bytes, Some(&mut service)).unwrap();
    gpu.submit_with_text(&bytes, Some(&mut service)).unwrap();
    let initial = session.stats().unwrap();
    assert!(initial.glyph_uploads > 0);
    assert!(initial.glyph_upload_bytes > 0);
    assert_eq!(initial.readback_bytes, 0);
    assert_eq!(
        gpu.last_frame().unwrap().read_pixels().unwrap(),
        cpu.last_frame().unwrap().read_pixels().unwrap()
    );
    for (x, color) in [
        (9 * 64, 0xff20354fu32 as i32),
        (8 * 64, 0xff12aa34u32 as i32),
    ] {
        let bytes = frame(256, 128, &commands(x, color), b"a");
        cpu.submit_with_text(&bytes, Some(&mut service)).unwrap();
        gpu.submit_with_text(&bytes, Some(&mut service)).unwrap();
        assert_eq!(
            gpu.last_frame().unwrap().read_pixels().unwrap(),
            cpu.last_frame().unwrap().read_pixels().unwrap()
        );
        assert_eq!(
            session.stats().unwrap().glyph_upload_bytes,
            initial.glyph_upload_bytes
        );
    }
}
