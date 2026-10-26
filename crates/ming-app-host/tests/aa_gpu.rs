#![cfg(all(feature = "gpu", feature = "text"))]
use ming_app_host::{
    graphics::{GpuSession, Graphics},
    text::TextService,
};
fn frame(w: i32, h: i32, commands: &[[i32; 8]], pool: &[u8]) -> Vec<u8> {
    [
        0x4d474632,
        1,
        w,
        h,
        commands.len() as i32,
        pool.len() as i32,
    ]
    .into_iter()
    .chain(commands.iter().flatten().copied())
    .flat_map(i32::to_le_bytes)
    .chain(pool.iter().copied())
    .collect()
}
fn image() -> Vec<u8> {
    let mut bytes = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut bytes, 2, 1);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        encoder
            .write_header()
            .unwrap()
            .write_image_data(&[255, 80, 0, 128, 0, 128, 240, 255])
            .unwrap();
    }
    bytes
}
#[test]
#[ignore = "requires hardware GPU; explicitly run to validate SDK4 coverage and resource reuse"]
fn sdk4_all_radii_match_cpu_and_preserve_direct_frames_and_caches() {
    let session = GpuSession::new().unwrap();
    let mut gpu = Graphics::with_gpu_session(session.clone());
    let mut cpu = Graphics::new();
    let info = gpu.gpu_info().unwrap();
    assert!(matches!(
        info.device_type.as_str(),
        "DiscreteGpu" | "IntegratedGpu"
    ));
    println!("{}", serde_json::to_string(info).unwrap());
    for radius in 0..=64 {
        let commands = [
            [1, 0, 0, 150, 150, 0xff17293bu32 as i32, 0, 0],
            [
                6,
                3,
                4,
                2 * radius + 3,
                2 * radius + 5,
                0xc0e79b4fu32 as i32,
                radius,
                0,
            ],
            [4, 12, 11, 80, 77, 0, 0, 0],
            [
                6,
                -1_000_000 + 60,
                -1_000_000 + 65,
                1_000_000,
                1_000_000,
                0x70aaccff,
                radius,
                0,
            ],
        ];
        let bytes = frame(150, 150, &commands, &[]);
        cpu.submit_with_sdk(&bytes, None, 4).unwrap();
        gpu.submit_with_sdk(&bytes, None, 4).unwrap();
        let actual = gpu.last_frame().unwrap();
        assert!(actual.cpu_pixels().is_none());
        let before = session.stats().unwrap().readback_bytes;
        assert_eq!(
            actual.read_pixels().unwrap(),
            cpu.last_frame().unwrap().read_pixels().unwrap(),
            "radius={radius}"
        );
        assert_eq!(
            session.stats().unwrap().readback_bytes - before,
            150 * 150 * 4
        );
    }
    for target in [&mut cpu, &mut gpu] {
        assert_eq!(target.open_png(&image()).unwrap(), 1);
    }
    let mut service = TextService::new().unwrap();
    let h = service.create("MingOS 中文".as_bytes(), 1, 16 * 64, 0);
    assert!(h > 0);
    let mut commands = [
        [1, 0, 0, 256, 128, 0xff17293bu32 as i32, 0, 0],
        [6, -3, 7, 240, 105, 0xfff0e9ddu32 as i32, 23, 0],
        [2, 1, 11, 14, 63, 47, 0, 0],
        [5, h, 24 * 64, 82 * 64, 0xff123456u32 as i32, 0, 0, 0],
        [6, 45, 63, 63, 33, 0x80e79b4fu32 as i32, 15, 0],
        [3, 4, 3, 2, 0xfffedcbau32 as i32, 0, 1, 0],
        [4, 0, 0, 20, 20, 0, 0, 0],
        [
            6,
            -999_990,
            -999_990,
            1_000_000,
            1_000_000,
            0x809050a0u32 as i32,
            64,
            0,
        ],
    ];
    let mut uploaded = None;
    for change in 0..2 {
        if change == 1 {
            commands[1][5] = 0x80aaccffu32 as i32;
            commands[3][2] += 8 * 64;
        }
        let bytes = frame(256, 128, &commands, b"A");
        cpu.submit_with_sdk(&bytes, Some(&mut service), 4).unwrap();
        let readback_before = session.stats().unwrap().readback_bytes;
        gpu.submit_with_sdk(&bytes, Some(&mut service), 4).unwrap();
        assert_eq!(
            session.stats().unwrap().readback_bytes,
            readback_before,
            "compute/direct output must not read pixels"
        );
        assert_eq!(
            gpu.last_frame().unwrap().read_pixels().unwrap(),
            cpu.last_frame().unwrap().read_pixels().unwrap()
        );
        let stats = session.stats().unwrap();
        assert_eq!(stats.image_uploads, 1);
        assert_eq!(stats.image_upload_bytes, 8);
        assert!(stats.glyph_uploads > 0);
        if let Some(bytes) = uploaded {
            assert_eq!(
                stats.glyph_upload_bytes, bytes,
                "AA colors/integer text movement must reuse masks"
            );
        }
        uploaded = Some(stats.glyph_upload_bytes);
    }
    let seq = gpu.frames_submitted();
    let stats = session.stats().unwrap();
    let quota = frame(1, 1, &[[6, 0, 0, 1, 1, -1, 0, 0]; 17], &[]);
    assert_eq!(
        gpu.submit_with_sdk(&quota, None, 4)
            .unwrap_err()
            .to_string(),
        "AA_FRAME_LIMIT"
    );
    assert_eq!(gpu.frames_submitted(), seq);
    assert_eq!(
        session.stats().unwrap().render_submissions,
        stats.render_submissions
    );
    gpu.submit_with_sdk(&frame(1, 1, &[[6, 0, 0, 1, 1, -1, 64, 0]], &[]), None, 4)
        .unwrap();
}
