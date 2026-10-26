#![cfg(feature = "text")]
use ming_app_host::text::TextEngine;

#[test]
fn bundled_face_shapes_and_rasterizes_chinese_and_latin_with_metrics() {
    let mut engine = TextEngine::new().unwrap();
    assert_eq!(engine.face_count(), 1);
    for size in [12, 16, 24, 48] {
        let layout = engine
            .layout("MingOS 中文，标点。AV fi é A\u{0301}", 1, size * 64, 0)
            .unwrap();
        assert!(layout.metrics[0] > 0);
        assert!(layout.metrics[1] > 0);
        assert!(layout.metrics[2] >= 0);
        assert_eq!(
            layout.metrics[4],
            layout.metrics[1] + layout.metrics[2] + layout.metrics[3]
        );
        assert!(layout.metrics[9] > 0);
        assert_eq!(layout.metrics[10], 0);
        assert_eq!(layout.metrics[11], 1);
        let masks = engine.raster_layout(&layout, 10 * 64, 60 * 64).unwrap();
        assert!(!masks.is_empty());
        assert!(
            masks
                .iter()
                .all(|m| m.width <= 256 && m.height <= 256 && m.data.len() == m.width * m.height)
        );
        assert!(
            masks
                .iter()
                .flat_map(|m| m.data.iter())
                .any(|&alpha| alpha > 0 && alpha < 255)
        );
    }
}
#[test]
fn layout_rejects_untrusted_ranges_and_unsupported_text_before_publication() {
    let mut engine = TextEngine::new().unwrap();
    for (text, font, size, flags) in [
        ("ok", 2, 1280, 0),
        ("ok", 1, 7 * 64, 0),
        ("ok", 1, 97 * 64, 0),
        ("ok", 1, 1281, 0),
        ("ok", 1, 1280, 2),
        ("a\nb", 1, 1280, 0),
        ("a\tb", 1, 1280, 0),
        ("\u{202e}", 1, 1280, 0),
    ] {
        assert!(
            engine.layout(text, font, size, flags).is_err(),
            "{text:?}/{font}/{size}/{flags}"
        );
    }
    assert!(engine.layout(&"a".repeat(513), 1, 1280, 0).is_err());
    assert!(engine.layout("שלום", 1, 1280, 0).is_err());
    assert!(engine.layout("\u{10ffff}", 1, 1280, 0).is_err());
    let missing = engine.layout("\u{10ffff}", 1, 1280, 1).unwrap();
    assert_eq!(missing.metrics[10] & 1, 1);
}
#[test]
fn advance_includes_spaces_and_empty_labels_have_no_ink() {
    let mut engine = TextEngine::new().unwrap();
    let a = engine.layout("A", 1, 1280, 0).unwrap().metrics[0];
    let spaced = engine.layout("A ", 1, 1280, 0).unwrap().metrics[0];
    assert!(spaced > a, "trailing space must advance");
    let empty = engine.layout("", 1, 1280, 0).unwrap();
    assert_eq!(empty.metrics[0], 0);
    assert_eq!(&empty.metrics[5..10], &[0; 5]);
    assert!(engine.raster_layout(&empty, 0, 0).unwrap().is_empty());
}

#[test]
fn integer_movement_reuses_masks_and_ink_metrics_cover_all_phases() {
    let mut engine = TextEngine::new().unwrap();
    let layout = engine.layout("A\u{0301} gj 中文", 1, 24 * 64, 0).unwrap();
    assert!(layout.retained_bytes() < 2 * 1024 * 1024);
    assert_eq!(layout.input_bytes(), "A\u{0301} gj 中文".len());
    let first = engine.raster_layout(&layout, 0, 0).unwrap();
    let misses = engine.cache_stats().misses;
    let moved = engine.raster_layout(&layout, 64, 0).unwrap();
    assert_eq!(misses, engine.cache_stats().misses);
    for (a, b) in first.iter().zip(&moved) {
        assert_eq!(a.key, b.key);
        assert!(std::sync::Arc::ptr_eq(&a.data, &b.data));
        assert_eq!(a.left + 1, b.left);
    }
    for delta in [-1_000_000, -1, 1, 1_000_000] {
        let shifted = engine
            .raster_layout(&layout, delta * 64, delta * 64)
            .unwrap();
        for (a, b) in first.iter().zip(&shifted) {
            assert_eq!(a.key, b.key);
            assert_eq!(a.left + delta, b.left);
            assert_eq!(a.top + delta, b.top);
        }
    }
    for x in 0..64 {
        for y in 0..64 {
            for mask in engine.raster_layout(&layout, x, y).unwrap() {
                assert!(mask.left * 64 - x >= layout.metrics[5]);
                assert!(mask.top * 64 - y >= layout.metrics[6]);
                assert!((mask.left + mask.width as i32) * 64 - x <= layout.metrics[7]);
                assert!((mask.top + mask.height as i32) * 64 - y <= layout.metrics[8]);
            }
        }
    }
    let mut other = TextEngine::new().unwrap();
    assert!(other.raster_layout(&layout, 0, 0).is_err());
    assert!(engine.cache_stats().mask_bytes <= 4 * 1024 * 1024);
}

#[test]
fn mask_growth_respects_the_metadata_limit_after_layout_admission() {
    let mut engine = TextEngine::new().unwrap();
    let base = engine.retained_metadata_bytes();
    let layout = engine
        .layout("MingOS 中文，标点。AV fi é", 1, 24 * 64, 0)
        .unwrap();
    let limit = base + 1024;
    engine.trim_metadata(limit).unwrap();
    let result = engine.raster_layout(&layout, 0, 0);
    assert!(
        result.is_err(),
        "too many pinned mask records must not bypass metadata allowance"
    );
    assert!(engine.retained_metadata_bytes() <= limit);
}
