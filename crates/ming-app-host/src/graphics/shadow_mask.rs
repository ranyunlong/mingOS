//! Bounded canonical A8 shadow masks and integer nine-slice sampling.

#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash)]
pub(crate) struct ShadowKey {
    core_width: usize,
    core_height: usize,
    radius: i32,
    blur: i32,
}

impl ShadowKey {
    pub(crate) fn new(w: i32, h: i32, radius: i32, blur: i32) -> Option<Self> {
        if !(1..=1_000_000).contains(&w)
            || !(1..=1_000_000).contains(&h)
            || !(0..=64).contains(&radius)
            || !(0..=16).contains(&blur)
        {
            return None;
        }
        let radius = radius.min(w / 2).min(h / 2);
        let core_limit = 2 * (radius + blur) + 1;
        Some(Self {
            core_width: w.min(core_limit) as usize,
            core_height: h.min(core_limit) as usize,
            radius,
            blur,
        })
    }

    pub(crate) fn core_width(&self) -> usize {
        self.core_width
    }
    pub(crate) fn core_height(&self) -> usize {
        self.core_height
    }
    pub(crate) fn patch_width(&self) -> usize {
        self.core_width + 2 * self.blur as usize
    }
    pub(crate) fn patch_height(&self) -> usize {
        self.core_height + 2 * self.blur as usize
    }
    pub(crate) fn generation_work(&self) -> usize {
        self.patch_width() * self.patch_height() * (1 + 2 * (2 * self.blur as usize + 1))
            + 64 * self.radius as usize * self.radius as usize
    }
    pub(crate) fn radius(&self) -> i32 {
        self.radius
    }
    pub(crate) fn blur(&self) -> i32 {
        self.blur
    }
}

/// Generate only the canonical patch; both A8 buffers are at most 193 squared.
pub(crate) fn generate_mask(key: ShadowKey) -> Vec<u8> {
    let width = key.patch_width();
    let height = key.patch_height();
    let mut mask = vec![0; width * height];
    for y in 0..height {
        for x in 0..width {
            mask[y * width + x] = super::aa::coverage(
                x as i32 - key.blur,
                y as i32 - key.blur,
                key.core_width() as i32,
                key.core_height() as i32,
                key.radius(),
            ) as u8;
        }
    }
    if key.blur == 0 {
        return mask;
    }
    let mut horizontal = vec![0; mask.len()];
    blur_pass(&mask, &mut horizontal, width, height, key.blur, false);
    blur_pass(&horizontal, &mut mask, width, height, key.blur, true);
    mask
}

fn blur_pass(input: &[u8], output: &mut [u8], width: usize, height: usize, b: i32, vertical: bool) {
    let norm = ((b + 1) * (b + 1)) as u32;
    for y in 0..height as i32 {
        for x in 0..width as i32 {
            let mut sum = 0_u32;
            for tap in -b..=b {
                let (sx, sy) = if vertical { (x, y + tap) } else { (x + tap, y) };
                if sx >= 0 && sy >= 0 && sx < width as i32 && sy < height as i32 {
                    sum += u32::from(input[sy as usize * width + sx as usize])
                        * (b + 1 - tap.abs()) as u32;
                }
            }
            // At b <= 16, sum <= 255 * 289; rounding stays well within u32.
            output[y as usize * width + x as usize] = ((sum + norm / 2) / norm) as u8;
        }
    }
}

fn map_axis(u: i32, size: i32, core: i32, margin: i32) -> i32 {
    if size == core || u < margin {
        u
    } else if u >= size - margin {
        u - (size - core)
    } else {
        margin
    }
}

/// Sample body-local coordinates, including the halo. Geometry and mask must match key.
pub(crate) fn sample(key: ShadowKey, mask: &[u8], w: i32, h: i32, x: i32, y: i32) -> u32 {
    let b = key.blur;
    // Matching dimensions are <= 1,000,000. Reject coordinates before any arithmetic on them.
    if x < -b || y < -b || x >= w + b || y >= h + b {
        return 0;
    }
    let margin = key.radius + b;
    let px = map_axis(x, w, key.core_width() as i32, margin) + b;
    let py = map_axis(y, h, key.core_height() as i32, margin) + b;
    u32::from(mask[py as usize * key.patch_width() + px as usize])
}
#[cfg(test)]
mod tests {
    use super::{ShadowKey, generate_mask, sample};

    // Independent full-coordinate rational geometry; never calls production AA.
    fn oracle_alpha(x: i32, y: i32, w: i32, h: i32, requested: i32) -> u8 {
        if x < 0 || y < 0 || x >= w || y >= h {
            return 0;
        }
        let r = i64::from(requested.min(w / 2).min(h / 2)) * 8;
        let mut hits = 0;
        for index in 0..16 {
            let px = i64::from(x) * 8 + 2 * (index % 4) + 1;
            let py = i64::from(y) * 8 + 2 * (index / 4) + 1;
            let dx = px - px.clamp(r, i64::from(w) * 8 - r);
            let dy = py - py.clamp(r, i64::from(h) * 8 - r);
            hits += u32::from(dx * dx + dy * dy <= r * r);
        }
        ((255 * hits + 8) / 16) as u8
    }

    // Scatter each source pixel into a full-sized pass accumulator, then round.
    // This is independent of production's per-destination gather and mapper.
    fn oracle_pass(input: &[u8], w: usize, h: usize, b: i32, vertical: bool) -> Vec<u8> {
        let mut sums = vec![0_u32; input.len()];
        for y in 0..h as i32 {
            for x in 0..w as i32 {
                let alpha = u32::from(input[y as usize * w + x as usize]);
                for tap in -b..=b {
                    let (tx, ty) = if vertical { (x, y + tap) } else { (x + tap, y) };
                    if tx >= 0 && ty >= 0 && tx < w as i32 && ty < h as i32 {
                        sums[ty as usize * w + tx as usize] += alpha * (b + 1 - tap.abs()) as u32;
                    }
                }
            }
        }
        let norm = ((b + 1) * (b + 1)) as u32;
        sums.into_iter()
            .map(|sum| ((sum + norm / 2) / norm) as u8)
            .collect()
    }

    fn full_oracle(w: i32, h: i32, r: i32, b: i32) -> Vec<u8> {
        let mut alpha = Vec::new();
        for y in -b..h + b {
            for x in -b..w + b {
                alpha.push(oracle_alpha(x, y, w, h, r));
            }
        }
        let pw = (w + 2 * b) as usize;
        let ph = (h + 2 * b) as usize;
        let horizontal = oracle_pass(&alpha, pw, ph, b, false);
        oracle_pass(&horizontal, pw, ph, b, true)
    }

    fn assert_full(w: i32, h: i32, r: i32, b: i32) {
        let key = ShadowKey::new(w, h, r, b).unwrap();
        let mask = generate_mask(key);
        let expected = full_oracle(w, h, r, b);
        for y in -b - 1..=h + b {
            for x in -b - 1..=w + b {
                let want = if x >= -b && x < w + b && y >= -b && y < h + b {
                    u32::from(expected[((y + b) * (w + 2 * b) + x + b) as usize])
                } else {
                    0
                };
                assert_eq!(
                    sample(key, &mask, w, h, x, y),
                    want,
                    "{w}x{h}, r={r}, b={b}, pixel ({x},{y})"
                );
            }
        }
    }

    #[test]
    fn new_rejects_invalid_geometry_and_styles() {
        for bad in [i32::MIN, -1, 0, 1_000_001, i32::MAX] {
            assert!(ShadowKey::new(bad, 10, 1, 1).is_none());
            assert!(ShadowKey::new(10, bad, 1, 1).is_none());
        }
        for bad in [i32::MIN, -1, 65, i32::MAX] {
            assert!(ShadowKey::new(10, 10, bad, 0).is_none());
        }
        for bad in [i32::MIN, -1, 17, i32::MAX] {
            assert!(ShadowKey::new(10, 10, 0, bad).is_none());
        }
    }

    #[test]
    fn key_getters_bounds_and_work_cover_every_style() {
        for radius in 0..=64 {
            for blur in 0..=16 {
                for (w, h) in [
                    (1, 1),
                    (1, 1_000_000),
                    (1_000_000, 1),
                    (127, 129),
                    (1_000_000, 1_000_000),
                ] {
                    let key = ShadowKey::new(w, h, radius, blur).unwrap();
                    let r = radius.min(w / 2).min(h / 2);
                    let cw = w.min(2 * (r + blur) + 1) as usize;
                    let ch = h.min(2 * (r + blur) + 1) as usize;
                    assert_eq!(key.radius(), r);
                    assert_eq!(key.blur(), blur);
                    assert_eq!(key.core_width(), cw);
                    assert_eq!(key.core_height(), ch);
                    assert_eq!(key.patch_width(), cw + 2 * blur as usize);
                    assert_eq!(key.patch_height(), ch + 2 * blur as usize);
                    assert!(key.patch_width() <= 193 && key.patch_height() <= 193);
                    assert_eq!(
                        key.generation_work(),
                        key.patch_width() * key.patch_height() * (1 + 2 * (2 * blur as usize + 1))
                            + 64 * r as usize * r as usize
                    );
                }
            }
        }
        let max = ShadowKey::new(1_000_000, 1_000_000, 64, 16).unwrap();
        assert_eq!(
            (max.patch_width(), max.patch_height(), max.generation_work()),
            (193, 193, 2_757_827)
        );
    }

    #[test]
    fn key_equality_tracks_effective_geometry_and_supports_copy_hash() {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        fn hash(key: ShadowKey) -> u64 {
            let mut hasher = DefaultHasher::new();
            key.hash(&mut hasher);
            hasher.finish()
        }
        let key = ShadowKey::new(769, 513, 64, 16).unwrap();
        let copy = key;
        assert_eq!(key, copy);
        assert_eq!(key, ShadowKey::new(1_000_000, 1_000_000, 64, 16).unwrap());
        assert_eq!(hash(key), hash(ShadowKey::new(512, 384, 64, 16).unwrap()));
        assert_ne!(key, ShadowKey::new(769, 513, 63, 16).unwrap());
        assert_ne!(key, ShadowKey::new(769, 513, 64, 15).unwrap());
        assert_ne!(key, ShadowKey::new(160, 513, 64, 16).unwrap());
        assert_eq!(ShadowKey::new(5, 7, 64, 2), ShadowKey::new(5, 7, 2, 2));
        assert!(!format!("{key:?}").is_empty());
    }

    #[test]
    fn python_full_convolution_fixture_goldens() {
        let fixtures: &[(i32, i32, i32, i32, &[u8])] = &[
            (
                769,
                513,
                64,
                16,
                include_bytes!("../../tests/fixtures/shadow-mask/max-r64-b16.a8"),
            ),
            (
                513,
                257,
                64,
                0,
                include_bytes!("../../tests/fixtures/shadow-mask/r64-b0.a8"),
            ),
            (
                12,
                9,
                64,
                2,
                include_bytes!("../../tests/fixtures/shadow-mask/clamp-odd.a8"),
            ),
            (
                1,
                9,
                64,
                16,
                include_bytes!("../../tests/fixtures/shadow-mask/thin-b16.a8"),
            ),
            (
                41,
                19,
                8,
                3,
                include_bytes!("../../tests/fixtures/shadow-mask/odd-single-axis.a8"),
            ),
            (
                1,
                2,
                0,
                2,
                include_bytes!("../../tests/fixtures/shadow-mask/round-each-pass.a8"),
            ),
        ];
        for &(w, h, r, b, expected) in fixtures {
            let key = ShadowKey::new(w, h, r, b).unwrap();
            let mask = generate_mask(key);
            assert_eq!(mask.len(), key.patch_width() * key.patch_height());
            assert!(
                mask == expected,
                "Python full-oracle fixture mismatch: {w}x{h}, r={r}, b={b}"
            );
        }
    }

    #[test]
    fn rounding_occurs_after_each_pass() {
        let key = ShadowKey::new(1, 2, 0, 2).unwrap();
        let mask = generate_mask(key);
        assert_eq!(sample(key, &mask, 1, 2, -1, 0), 32);
        // One combined 2D rounding would produce 31 at this pixel.
        assert_full(1, 2, 0, 2);
    }

    #[test]
    fn blur_zero_has_explicit_aa_and_half_open_goldens() {
        let key = ShadowKey::new(8, 9, 2, 0).unwrap();
        let mask = generate_mask(key);
        assert_eq!(sample(key, &mask, 8, 9, 0, 0), 96);
        assert_eq!(sample(key, &mask, 8, 9, 1, 0), 239);
        assert_eq!(sample(key, &mask, 8, 9, 2, 0), 255);
        assert_eq!(sample(key, &mask, 8, 9, 7, 8), 96);
        for (x, y) in [(-1, 0), (0, -1), (8, 0), (0, 9)] {
            assert_eq!(sample(key, &mask, 8, 9, x, y), 0);
        }
    }

    #[test]
    fn small_cartesian_matches_independent_full_oracle() {
        for w in 1..=7 {
            for h in 1..=7 {
                for r in [0, 1, 2, 3, 8, 64] {
                    for b in [0, 1, 2, 4, 16] {
                        assert_full(w, h, r, b);
                    }
                }
            }
        }
    }

    #[test]
    fn all_radii_and_all_blurs_match_full_oracle() {
        for r in 0..=64 {
            for b in [0, 1, 8, 16] {
                assert_full(2 * (r + b) + 3, 2 * (r + b) + 4, r, b);
            }
        }
        for b in 0..=16 {
            for r in [0, 1, 3, 8, 32, 64] {
                assert_full(2 * (r + b) + 5, (2 * r - 1).max(1), r, b);
                assert_full((2 * r - 1).max(1), 2 * (r + b) + 4, r, b);
            }
        }
    }

    #[test]
    fn stretch_transition_seams_match_full_oracle() {
        for (r, b) in [(0, 0), (0, 1), (1, 2), (3, 4), (8, 1), (64, 16)] {
            for dw in -1..=3 {
                for dh in -1..=3 {
                    let w = (2 * (r + b) + dw).max(1);
                    let h = (2 * (r + b) + dh).max(1);
                    assert_full(w, h, r, b);
                }
            }
        }
    }

    #[test]
    fn large_rectangles_match_complete_full_oracle() {
        assert_full(769, 513, 64, 16);
        assert_full(512, 384, 64, 16);
    }

    #[test]
    fn million_scale_sampling_and_extreme_coordinates_are_safe() {
        let w = 1_000_000;
        let h = 999_999;
        let key = ShadowKey::new(w, h, 64, 16).unwrap();
        let mask = generate_mask(key);
        assert_eq!(mask.len(), 193 * 193);
        for (x, y) in [(80, 80), (500_000, 400_000), (w - 81, h - 81)] {
            assert_eq!(sample(key, &mask, w, h, x, y), 255);
        }
        for (x, y) in [
            (i32::MIN, 0),
            (i32::MAX, 0),
            (0, i32::MIN),
            (0, i32::MAX),
            (-17, 0),
            (w + 16, 0),
            (0, -17),
            (0, h + 16),
        ] {
            assert_eq!(sample(key, &mask, w, h, x, y), 0);
        }
        // Translation-equivalent corner pixels compared against actual full blur,
        // not against another use of the production mapper.
        let small_w = 181;
        let small_h = 179;
        let full = full_oracle(small_w, small_h, 64, 16);
        for y in -16..=80 {
            for x in -16..=80 {
                let expected = u32::from(full[((y + 16) * (small_w + 32) + x + 16) as usize]);
                assert_eq!(sample(key, &mask, w, h, x, y), expected);
                assert_eq!(sample(key, &mask, w, h, w - 1 - x, h - 1 - y), expected);
            }
        }
    }
}
