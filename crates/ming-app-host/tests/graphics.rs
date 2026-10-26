use ming_app_host::{
    broker::Broker,
    graphics::{Frame, Graphics},
};
use std::{collections::BTreeMap, fs};
fn png_bytes(w: u32, h: u32) -> Vec<u8> {
    let mut out = Vec::new();
    {
        let mut e = png::Encoder::new(&mut out, w, h);
        e.set_color(png::ColorType::Rgba);
        e.set_depth(png::BitDepth::Eight);
        let mut writer = e.write_header().unwrap();
        writer
            .write_image_data(&vec![255; (w * h * 4) as usize])
            .unwrap();
    }
    out
}
fn frame(w: i32, h: i32, commands: &[[i32; 8]], pool: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    for v in [
        0x4d474632,
        1,
        w,
        h,
        commands.len() as i32,
        pool.len() as i32,
    ]
    .into_iter()
    .chain(commands.iter().flatten().copied())
    {
        out.extend(v.to_le_bytes());
    }
    out.extend(pool);
    out
}
#[test]
fn chunk_reads_are_bounded_and_eof_is_not_an_error() {
    let d = tempfile::tempdir().unwrap();
    let p = d.path().join("data");
    fs::write(&p, vec![77; 70000]).unwrap();
    let mut b = Broker::new_with_sdk(&BTreeMap::from([(1, p)]), 2).unwrap();
    let h = b.dispatch("file_open", &[1]);
    let read = b.read_chunk(h, 0, 65536);
    assert_eq!(read.value, 65536);
    assert_eq!(read.bytes.unwrap().len(), 65536);
    assert_eq!(b.read_chunk(h, 70000, 1).value, 0);
    for (o, n) in [(-1, 2), (0, 65537), (70001, 1)] {
        assert_eq!(b.read_chunk(h, o, n).value, -2);
    }
    assert_eq!(b.read_chunk(0, 0, 1).value, -2);
}
#[test]
fn frame_parser_rejects_corruption_and_budget_abuse() {
    assert!(Frame::decode(&[]).is_err());
    for bytes in [
        frame(0, 10, &[], &[]),
        frame(4096, 4096, &[], &[]),
        frame(10, 10, &[[99, 0, 0, 0, 0, 0, 0, 0]], &[]),
        frame(10, 10, &[[3, 0, 0, 2, -1, 0, 200, 0]], b"x"),
    ] {
        assert!(Frame::decode(&bytes).is_err());
    }
    let mut b = frame(10, 10, &[], &[]);
    b.push(1);
    assert!(Frame::decode(&b).is_err());
}
#[test]
fn rectangle_alpha_and_clip_have_deterministic_pixels() {
    let mut g = Graphics::new();
    let bytes = frame(
        8,
        8,
        &[
            [1, 0, 0, 8, 8, 0xff102030u32 as i32, 0, 0],
            [4, 2, 2, 2, 2, 0, 0, 0],
            [1, 0, 0, 8, 8, 0xffff0000u32 as i32, 0, 0],
        ],
        &[],
    );
    g.submit(&bytes).unwrap();
    let f = g.last_frame().unwrap();
    assert_eq!(f.read_pixels().unwrap()[0], 0xff102030);
    assert_eq!(f.read_pixels().unwrap()[2 + 2 * 8], 0xffff0000);
    assert_eq!(f.width, 8);
}
#[test]
fn png_handles_are_local_and_failed_submit_preserves_previous_frame() {
    let mut g = Graphics::new();
    let image = g.open_png(&png_bytes(2, 2)).unwrap();
    assert_eq!(g.dimensions(image), Some((2, 2)));
    g.submit(&frame(4, 4, &[[2, image, 0, 0, 4, 4, 0, 0]], &[]))
        .unwrap();
    assert_eq!(
        g.last_frame().unwrap().read_pixels().unwrap()[0],
        0xffffffff
    );
    let seq = g.last_frame().unwrap().sequence;
    assert!(
        g.submit(&frame(4, 4, &[[2, 999, 0, 0, 4, 4, 0, 0]], &[]))
            .is_err()
    );
    assert_eq!(g.last_frame().unwrap().sequence, seq);
    assert!(g.close_image(image));
    assert!(g.dimensions(image).is_none());
}
#[test]
fn bad_png_and_image_budget_fail_without_committing() {
    let mut g = Graphics::new();
    assert!(g.open_png(b"not png").is_err());
    let bytes = png_bytes(1, 1);
    for _ in 0..8 {
        g.open_png(&bytes).unwrap();
    }
    assert!(g.open_png(&bytes).is_err());
}

#[test]
fn default_graphics_also_issues_positive_image_handles() {
    let mut g = Graphics::default();
    assert!(g.open_png(&png_bytes(1, 1)).unwrap() > 0);
}
#[test]
fn repeated_full_screen_paint_is_rejected_before_raster_work() {
    let mut g = Graphics::new();
    let bytes = frame(2048, 2048, &vec![[1, 0, 0, 2048, 2048, -1, 0, 0]; 9], &[]);
    assert!(
        g.submit(&bytes)
            .unwrap_err()
            .to_string()
            .contains("WORK_LIMIT")
    );
    assert!(g.last_frame().is_none());
}
