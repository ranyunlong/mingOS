#![cfg(feature = "text")]
use ming_app_host::{
    graphics::{Frame, Graphics},
    text::TextService,
};
fn frame(width: i32, height: i32, commands: &[[i32; 8]]) -> Vec<u8> {
    [0x4d474632, 1, width, height, commands.len() as i32, 0]
        .into_iter()
        .chain(commands.iter().flatten().copied())
        .flat_map(i32::to_le_bytes)
        .collect()
}
fn draw(commands: &[[i32; 8]]) -> Vec<u32> {
    let mut graphics = Graphics::new();
    graphics
        .submit_with_sdk(&frame(24, 24, commands), None, 4)
        .unwrap();
    graphics
        .last_frame()
        .unwrap()
        .read_pixels()
        .unwrap()
        .as_ref()
        .clone()
}
#[test]
fn aa_frame_is_explicit_and_keeps_legacy_rounding_unchanged() {
    let aa = [6, 2, 2, 20, 20, -1, 4, 0];
    let bytes = frame(24, 24, &[aa]);
    assert!(Frame::decode_for_sdk(&bytes, 4).is_ok());
    for sdk in [1, 2, 3] {
        assert!(Frame::decode_for_sdk(&bytes, sdk).is_err());
    }
    let mut legacy = Graphics::new();
    assert!(legacy.submit_with_text(&bytes, None).is_err());
    let pixels = draw(&[aa]);
    assert_eq!(pixels[2 * 24 + 2], 0xff000000);
    assert_eq!(pixels[2 * 24 + 3], 0xff303030);
    assert_eq!(pixels[2 * 24 + 4], 0xffafafaf);
    assert_eq!(pixels[4 * 24 + 4], 0xffffffff);
    let old = draw(&[[1, 2, 2, 20, 20, -1, 4, 0]]);
    assert!(old.iter().all(|&p| p == 0xff000000 || p == 0xffffffff));
    assert_ne!(pixels, old);
    assert_eq!(
        draw(&[[6, 2, 2, 20, 20, -1, 0, 0]]),
        draw(&[[1, 2, 2, 20, 20, -1, 0, 0]])
    );
}
#[test]
fn aa_respects_clip_opacity_and_painter_order() {
    let background = [1, 0, 0, 24, 24, 0xff1e2a36u32 as i32, 0, 0];
    let shape = [6, 2, 2, 20, 20, 0x80f0d0b0u32 as i32, 4, 0];
    let clipped = draw(&[background, [4, 5, 5, 10, 10, 0, 0, 0], shape]);
    assert_eq!(clipped[8 * 24 + 8], 0xff877d73);
    assert_eq!(clipped[4 * 24 + 8], 0xff1e2a36);
    assert_eq!(clipped[15 * 24 + 8], 0xff1e2a36);
    assert_eq!(
        draw(&[background, [6, 2, 2, 20, 20, 0x00ffffff, 4, 0]]),
        draw(&[background])
    );
    assert_eq!(draw(&[shape, background]), draw(&[background]));
}
#[test]
fn aa_quotas_preflight_even_hidden_draws_and_preserve_last_success() {
    let mut graphics = Graphics::new();
    let valid = frame(24, 24, &[[6, 2, 2, 20, 20, -1, 4, 0]]);
    graphics.submit_with_sdk(&valid, None, 4).unwrap();
    let old = graphics.last_frame().unwrap();
    for commands in [
        vec![[6, 0, 0, 1, 1, -1, 64, 0]; 17],
        vec![[6, -1_000_000, -1_000_000, 1, 1, 0, 0, 0]; 17],
    ] {
        let err = graphics
            .submit_with_sdk(&frame(24, 24, &commands), None, 4)
            .unwrap_err();
        assert_eq!(err.to_string(), "AA_FRAME_LIMIT");
        assert_eq!(graphics.last_frame().unwrap().sequence, old.sequence);
        assert_eq!(
            graphics.last_frame().unwrap().read_pixels().unwrap(),
            old.read_pixels().unwrap()
        );
    }
    graphics
        .submit_with_sdk(&frame(24, 24, &[[6, 0, 0, 1, 1, -1, 64, 0]; 16]), None, 4)
        .unwrap();
    let seq = graphics.frames_submitted();
    let work = frame(1920, 2048, &[[6, 0, 0, 1920, 2048, -1, 64, 0]; 8]);
    assert_eq!(
        graphics
            .submit_with_sdk(&work, None, 4)
            .unwrap_err()
            .to_string(),
        "FRAME_WORK_LIMIT"
    );
    assert_eq!(graphics.frames_submitted(), seq);
    graphics.submit_with_sdk(&valid, None, 4).unwrap();
}
#[test]
fn aa_syntax_rejects_bad_geometry_and_mixed_text_retains_typed_lifetime() {
    for command in [
        [6, 0, 0, 0, 1, -1, 0, 0],
        [6, 0, 0, 1, 1, -1, 65, 0],
        [6, 0, 0, 1, 1, -1, -1, 0],
        [6, 1_000_001, 0, 1, 1, -1, 0, 0],
        [6, 0, 0, 1, 1, -1, 0, 1],
    ] {
        assert!(Frame::decode_for_sdk(&frame(24, 24, &[command]), 4).is_err());
    }
    let mut service = TextService::new().unwrap();
    let h = service.create("中文".as_bytes(), 1, 12 * 64, 0);
    assert!(h > 0);
    let commands = [
        [6, 0, 0, 24, 24, -1, 6, 0],
        [5, h, 0, 18 * 64, 0xff20354fu32 as i32, 0, 0, 0],
    ];
    let mut graphics = Graphics::new();
    graphics
        .submit_with_sdk(&frame(24, 24, &commands), Some(&mut service), 4)
        .unwrap();
    assert!(graphics.last_frame().unwrap().text_prepare_us.is_some());
    let seq = graphics.frames_submitted();
    assert!(service.close(h));
    assert!(
        graphics
            .submit_with_sdk(&frame(24, 24, &commands), Some(&mut service), 4)
            .is_err()
    );
    assert_eq!(graphics.frames_submitted(), seq);
}
