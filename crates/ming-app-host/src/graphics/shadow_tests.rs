#![cfg(feature = "text")]
use super::*;
fn frame(w: i32, h: i32, commands: &[[i32; 8]]) -> Vec<u8> {
    [0x4d474632, 1, w, h, commands.len() as i32, 0]
        .into_iter()
        .chain(commands.iter().flatten().copied())
        .flat_map(i32::to_le_bytes)
        .collect()
}
#[test]
fn shadow_decoder_is_v4_only_and_rejects_geometry_and_reserved_words() {
    let command = [7, 0x40000001, 2, 3, 20, 30, -1, 0];
    let bytes = frame(64, 64, &[command]);
    assert!(Frame::decode_for_sdk(&bytes, 4).is_ok());
    for sdk in [1, 2, 3, 5] {
        assert!(Frame::decode_for_sdk(&bytes, sdk).is_err());
    }
    let mut g = Graphics::new();
    assert!(g.submit(&bytes).is_err());
    assert!(g.submit_with_text(&bytes, None).is_err());
    for (index, value) in [
        (1, 1),
        (1, 0x30000001),
        (2, 1000001),
        (3, -1000001),
        (4, 0),
        (5, 1000001),
        (7, 1),
    ] {
        let mut bad = command;
        bad[index] = value;
        assert!(Frame::decode_for_sdk(&frame(64, 64, &[bad]), 4).is_err());
    }
}
#[test]
fn shadow_missing_closed_and_hidden_handles_preserve_last_frame() {
    let mut g = Graphics::new();
    let h = g.create_shadow_style(5, 3);
    let good = frame(64, 64, &[[7, h, 10, 10, 23, 17, -1, 0]]);
    g.submit_with_sdk(&good, None, 4).unwrap();
    let old = g.last_frame().unwrap();
    assert!(old.read_pixels().unwrap().iter().any(|&p| p != 0xff000000));
    assert!(g.close_shadow_style(h));
    for commands in [
        vec![[7, h, 10, 10, 23, 17, -1, 0]],
        vec![[4, 0, 0, 0, 0, 0, 0, 0], [7, h, 10, 10, 23, 17, -1, 0]],
    ] {
        assert_eq!(
            g.submit_with_sdk(&frame(64, 64, &commands), None, 4)
                .unwrap_err()
                .to_string(),
            "SHADOW_HANDLE_INVALID"
        );
        assert_eq!(g.last_frame().unwrap().sequence, old.sequence);
        assert_eq!(
            g.last_frame().unwrap().read_pixels().unwrap(),
            old.read_pixels().unwrap()
        );
    }
    let h = g.create_shadow_style(5, 3);
    let hidden = frame(64, 64, &[[7, h, -1000, -1000, 1, 1, 0, 0]; 9]);
    assert_eq!(
        g.submit_with_sdk(&hidden, None, 4).unwrap_err().to_string(),
        "SHADOW_FRAME_LIMIT"
    );
    g.submit_with_sdk(&frame(64, 64, &[[7, h, 10, 10, 23, 17, -1, 0]]), None, 4)
        .unwrap();
}
#[test]
fn cpu_shadow_full_convolution_painter_order_clip_and_opacity() {
    let mut g = Graphics::new();
    let handle = g.create_shadow_style(5, 3);
    let bg = 0xff203040u32;
    let color = 0x806080a0u32;
    let bytes = frame(
        48,
        40,
        &[
            [1, 0, 0, 48, 40, bg as i32, 0, 0],
            [4, 7, 8, 31, 24, 0, 0, 0],
            [7, handle, 10, 11, 23, 17, color as i32, 0],
            [1, 18, 17, 5, 5, -1, 0, 0],
        ],
    );
    g.submit_with_sdk(&bytes, None, 4).unwrap();
    let actual = g.last_frame().unwrap().read_pixels().unwrap();
    // Independent full-size convolution, never uses the canonical mask or mapping.
    let (pw, ph) = (29usize, 23usize);
    let mut mask = vec![0u32; pw * ph];
    for y in 0..ph {
        for x in 0..pw {
            mask[y * pw + x] = aa::coverage(x as i32 - 3, y as i32 - 3, 23, 17, 5);
        }
    }
    let pass = |src: &[u32], horizontal: bool| {
        let mut out = vec![0u32; pw * ph];
        for y in 0..ph {
            for x in 0..pw {
                let mut sum = 0;
                for tap in -3i32..=3 {
                    let xx = x as i32 + if horizontal { tap } else { 0 };
                    let yy = y as i32 + if horizontal { 0 } else { tap };
                    if xx >= 0 && yy >= 0 && xx < pw as i32 && yy < ph as i32 {
                        sum += src[yy as usize * pw + xx as usize] * (4 - tap.unsigned_abs());
                    }
                }
                out[y * pw + x] = (sum + 8) / 16;
            }
        }
        out
    };
    let mask = pass(&pass(&mask, true), false);
    for y in 0..40 {
        for x in 0..48 {
            let mut expected = bg;
            if (7..38).contains(&x) && (8..32).contains(&y) {
                let sx = x - 7;
                let sy = y - 8;
                if sx < pw && sy < ph {
                    let a = (mask[sy * pw + sx] * (color >> 24) + 127) / 255;
                    for shift in [0, 8, 16] {
                        let c = (((color >> shift) & 255) * a
                            + ((bg >> shift) & 255) * (255 - a)
                            + 127)
                            / 255;
                        expected = (expected & !(255 << shift)) | (c << shift);
                    }
                }
                if (18..23).contains(&x) && (17..22).contains(&y) {
                    expected = 0xffffffff;
                }
            }
            assert_eq!(actual[y * 48 + x], expected, "{x},{y}");
        }
    }
}
#[test]
fn cold_budget_rejects_atomically_then_warm_large_resize_reuses_masks() {
    let mut g = Graphics::new();
    let mut commands = Vec::new();
    for r in 57..=64 {
        let h = g.create_shadow_style(r, 16);
        commands.push([7, h, 0, 0, 3000, 3000, 0, 0]);
    }
    let large = frame(2000, 1800, &commands);
    assert_eq!(
        g.submit_with_sdk(&large, None, 4).unwrap_err().to_string(),
        "SHADOW_WORK_LIMIT"
    );
    assert_eq!(g.frames_submitted(), 0);
    assert!(g.last_frame().is_none());
    let mut small = commands.clone();
    for c in &mut small {
        c[4] = 1000;
        c[5] = 1000;
    }
    g.submit_with_sdk(&frame(64, 64, &small), None, 4).unwrap();
    g.submit_with_sdk(&large, None, 4).unwrap();
    assert_eq!(g.frames_submitted(), 2);
    let seq = g.last_frame().unwrap().sequence;
    let mut excessive = commands.clone();
    excessive.push(commands[0]);
    assert_eq!(
        g.submit_with_sdk(&frame(64, 64, &excessive), None, 4)
            .unwrap_err()
            .to_string(),
        "SHADOW_FRAME_LIMIT"
    );
    assert_eq!(g.last_frame().unwrap().sequence, seq);
    g.submit_with_sdk(&large, None, 4).unwrap();
}
