#![cfg(feature = "text")]
use ming_app_host::{graphics::Graphics, text::TextService};
fn frame(commands: &[[i32; 8]]) -> Vec<u8> {
    [0x4d474632, 1, 256, 128, commands.len() as i32, 0]
        .into_iter()
        .chain(commands.iter().flatten().copied())
        .flat_map(i32::to_le_bytes)
        .collect()
}
#[test]
fn rich_text_is_versioned_and_cpu_painter_order_and_stale_handles_are_enforced() {
    let mut text = TextService::new().unwrap();
    let h = text.create("MingOS 中文".as_bytes(), 1, 24 * 64, 0);
    assert!(h > 0);
    let white = [1, 0, 0, 256, 128, -1, 0, 0];
    let label = [5, h, 16 * 64, 80 * 64, 0xff20354fu32 as i32, 0, 0, 0];
    let bytes = frame(&[white, label]);
    let mut graphics = Graphics::new();
    assert!(graphics.submit(&bytes).is_err());
    graphics.submit_with_text(&bytes, Some(&mut text)).unwrap();
    let old = graphics.last_frame().unwrap();
    assert!(old.read_pixels().unwrap().iter().any(|&p| p != 0xffffffff));
    graphics
        .submit_with_text(&frame(&[label, white]), Some(&mut text))
        .unwrap();
    assert!(
        graphics
            .last_frame()
            .unwrap()
            .read_pixels()
            .unwrap()
            .iter()
            .all(|&p| p == 0xffffffff)
    );
    let sequence = graphics.last_frame().unwrap().sequence;
    assert!(text.close(h));
    assert!(graphics.submit_with_text(&bytes, Some(&mut text)).is_err());
    assert_eq!(graphics.last_frame().unwrap().sequence, sequence);
    assert!(old.read_pixels().unwrap().iter().any(|&p| p != 0xffffffff));
}
