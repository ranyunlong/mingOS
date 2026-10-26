/// Coverage for local pixels of an integer rounded rectangle (host radius 0..=64).
pub(crate) fn coverage(x: i32, y: i32, width: i32, height: i32, radius: i32) -> u32 {
    if x < 0 || y < 0 || x >= width || y >= height {
        return 0;
    }
    let r = radius.max(0).min(width / 2).min(height / 2);
    let x = x.min(width - 1 - x);
    let y = y.min(height - 1 - y);
    if x >= r || y >= r {
        return 255;
    }

    // Only corner-local distances are scaled: |d| <= 512 for host radii.
    let mut hits = 0;
    for sy in [1, 3, 5, 7] {
        let dy = 8 * (r - y) - sy;
        for sx in [1, 3, 5, 7] {
            let dx = 8 * (r - x) - sx;
            if dx * dx + dy * dy <= 64 * r * r {
                hits += 1;
            }
        }
    }
    (255 * hits + 8) / 16
}

#[cfg(test)]
mod tests {
    use super::coverage;

    // Intersect each horizontal sample row with the continuous rounded rectangle,
    // then count lattice columns analytically. No corner reflection or 16-point
    // distance loop from the production kernel is used here.
    fn section_oracle(x: i32, y: i32, width: i32, height: i32, radius: i32) -> u32 {
        if x < 0 || y < 0 || x >= width || y >= height {
            return 0;
        }
        let r = f64::from(radius.max(0).min(width / 2).min(height / 2));
        let mut count = 0;
        for row in 0..4 {
            let py = f64::from(y) + (f64::from(row) + 0.5) / 4.0;
            let dy = (r - py).max(py - (f64::from(height) - r)).max(0.0);
            let reach = (r * r - dy * dy).sqrt();
            let left = r - reach;
            let right = f64::from(width) - r + reach;
            let first = ((left - f64::from(x)) * 4.0 - 0.5).ceil().max(0.0);
            let last = ((right - f64::from(x)) * 4.0 - 0.5).floor().min(3.0);
            count += (last - first + 1.0).max(0.0) as u32;
        }
        (255 * count + 8) / 16
    }

    // Exact rational Minkowski-sum definition: distance to the inner box <= r.
    // Full coordinates and i128 deliberately avoid production's corner shortcut.
    fn rational_oracle(x: i32, y: i32, width: i32, height: i32, radius: i32) -> u32 {
        if x < 0 || y < 0 || x >= width || y >= height {
            return 0;
        }
        let r = i128::from(radius.max(0).min(width / 2).min(height / 2)) * 8;
        let right = i128::from(width) * 8 - r;
        let bottom = i128::from(height) * 8 - r;
        let count = (0..16)
            .filter(|index| {
                let px = i128::from(x) * 8 + 2 * (index % 4) + 1;
                let py = i128::from(y) * 8 + 2 * (index / 4) + 1;
                let dx = px - px.clamp(r, right);
                let dy = py - py.clamp(r, bottom);
                dx * dx + dy * dy <= r * r
            })
            .count() as u32;
        (255 * count + 8) / 16
    }

    fn check(x: i32, y: i32, width: i32, height: i32, radius: i32) {
        assert_eq!(
            coverage(x, y, width, height, radius),
            section_oracle(x, y, width, height, radius),
            "pixel ({x},{y}), rectangle {width}x{height}, radius {radius}"
        );
    }

    #[test]
    fn explicit_corner_goldens() {
        let cases: &[(i32, &[&[u32]])] = &[
            (1, &[&[207]]),
            (2, &[&[96, 239], &[239, 255]]),
            (3, &[&[0, 143, 239], &[143, 255, 255], &[239, 255, 255]]),
            (
                4,
                &[
                    &[0, 48, 175, 255],
                    &[48, 239, 255, 255],
                    &[175, 255, 255, 255],
                    &[255, 255, 255, 255],
                ],
            ),
            (
                8,
                &[
                    &[0, 0, 0, 0, 48, 159, 223, 255],
                    &[0, 0, 16, 143, 255, 255, 255, 255],
                    &[0, 16, 207, 255, 255, 255, 255, 255],
                    &[0, 143, 255, 255, 255, 255, 255, 255],
                    &[48, 255, 255, 255, 255, 255, 255, 255],
                    &[159, 255, 255, 255, 255, 255, 255, 255],
                    &[223, 255, 255, 255, 255, 255, 255, 255],
                    &[255, 255, 255, 255, 255, 255, 255, 255],
                ],
            ),
        ];
        for &(radius, rows) in cases {
            for (y, row) in rows.iter().enumerate() {
                for (x, &expected) in row.iter().enumerate() {
                    assert_eq!(
                        coverage(x as i32, y as i32, 2 * radius + 3, 2 * radius + 5, radius),
                        expected,
                        "r={radius}, ({x},{y})"
                    );
                }
            }
        }
    }

    #[test]
    fn zero_radius_matches_half_open_rectangle() {
        for (width, height) in [(1, 1), (2, 7), (9, 3)] {
            for y in -2..height + 2 {
                for x in -2..width + 2 {
                    let expected = if (0..width).contains(&x) && (0..height).contains(&y) {
                        255
                    } else {
                        0
                    };
                    assert_eq!(coverage(x, y, width, height, 0), expected);
                }
            }
        }
    }

    #[test]
    fn small_and_odd_dimensions_match_sections_for_every_radius() {
        for width in 1..=13 {
            for height in 1..=13 {
                for radius in 0..=64 {
                    for y in -1..=height {
                        for x in -1..=width {
                            check(x, y, width, height, radius);
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn every_radius_matches_sections_across_full_rectangles() {
        for radius in 0..=64 {
            let width = 2 * radius + 3;
            let height = 2 * radius + 5;
            for y in -1..=height {
                for x in -1..=width {
                    check(x, y, width, height, radius);
                }
            }
        }
    }

    #[test]
    fn reflections_and_transpose_preserve_coverage() {
        for radius in 0..=64 {
            let width = 2 * radius + 3;
            let height = 2 * radius + 7;
            for y in 0..=radius {
                for x in 0..=radius {
                    let value = coverage(x, y, width, height, radius);
                    assert_eq!(value, coverage(width - 1 - x, y, width, height, radius));
                    assert_eq!(value, coverage(x, height - 1 - y, width, height, radius));
                    assert_eq!(
                        value,
                        coverage(width - 1 - x, height - 1 - y, width, height, radius)
                    );
                    assert_eq!(value, coverage(y, x, height, width, radius));
                }
            }
        }
    }

    #[test]
    fn radius_clamps_to_integer_half_of_each_dimension() {
        for (width, height) in [(1, 9), (9, 1), (2, 3), (3, 2), (5, 7), (7, 5), (127, 129)] {
            let clamped = (width / 2).min(height / 2);
            for y in 0..height {
                for x in 0..width {
                    assert_eq!(
                        coverage(x, y, width, height, 64),
                        coverage(x, y, width, height, clamped)
                    );
                }
            }
        }
    }

    #[test]
    fn straight_body_is_fully_covered() {
        for radius in 0..=64 {
            let width = 2 * radius + 3;
            let height = 2 * radius + 5;
            for y in 0..height {
                for x in 0..width {
                    if (radius..width - radius).contains(&x)
                        || (radius..height - radius).contains(&y)
                    {
                        assert_eq!(coverage(x, y, width, height, radius), 255);
                    }
                }
            }
        }
    }

    #[test]
    fn large_coordinates_match_both_oracles_without_overflow() {
        for (width, height) in [
            (1_000_000, 999_999),
            (i32::MAX, i32::MAX - 1),
            (i32::MAX, 7),
            (7, i32::MAX),
        ] {
            let xs = [
                i32::MIN,
                -1_000_000,
                -1,
                0,
                1,
                31,
                63,
                64,
                width / 2,
                width - 65,
                width - 64,
                width - 2,
                width - 1,
                width,
                i32::MAX,
            ];
            let ys = [
                i32::MIN,
                -1_000_000,
                -1,
                0,
                1,
                31,
                63,
                64,
                height / 2,
                height - 65,
                height - 64,
                height - 2,
                height - 1,
                height,
                i32::MAX,
            ];
            for radius in 0..=64 {
                for x in xs {
                    for y in ys {
                        check(x, y, width, height, radius);
                        assert_eq!(
                            coverage(x, y, width, height, radius),
                            rational_oracle(x, y, width, height, radius)
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn independent_oracles_agree_for_all_radius_corner_samples() {
        for radius in 0..=64 {
            let width = 2 * radius + 1;
            let height = 2 * radius + 3;
            for y in 0..=radius {
                for x in 0..=radius {
                    assert_eq!(
                        section_oracle(x, y, width, height, radius),
                        rational_oracle(x, y, width, height, radius),
                        "oracles: r={radius}, ({x},{y})"
                    );
                }
            }
        }
    }

    #[test]
    fn invalid_dimensions_are_empty() {
        for width in [i32::MIN, -1, 0] {
            for height in [i32::MIN, -1, 0, 1, i32::MAX] {
                for x in [i32::MIN, -1, 0, i32::MAX] {
                    for y in [i32::MIN, -1, 0, i32::MAX] {
                        assert_eq!(coverage(x, y, width, height, 64), 0);
                        assert_eq!(coverage(y, x, height, width, 64), 0);
                    }
                }
            }
        }
    }
}
