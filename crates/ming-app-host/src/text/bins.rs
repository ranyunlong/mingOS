//! Bounded, painter-ordered glyph candidates for 16-by-16 pixel tiles.

use anyhow::{Context, Result, ensure};

const TILE_SIZE: i64 = 16;
const MAX_FRAME_EDGE: usize = 4096;
const MAX_FRAME_PIXELS: usize = 4_194_304;
const MAX_GLYPHS: usize = 2048;
const MAX_BITMAP_EDGE: usize = 256;
const MAX_REFERENCES: usize = 65_536;
const MAX_CANDIDATES: u32 = 64;
const MAX_STORAGE_BYTES: usize = 2 * 1024 * 1024;

#[derive(Clone, Copy, Debug)]
pub(super) struct GlyphRect {
    pub glyph_index: u32,
    pub x: i32,
    pub y: i32,
    pub width: usize,
    pub height: usize,
}

#[derive(Debug, Default)]
pub(crate) struct GlyphBins {
    /// Pixel origin of the tile-aligned grid, after clipping to the frame.
    pub origin: [i32; 2],
    pub columns: usize,
    pub rows: usize,
    /// Row-major [start, count] ranges into this bin set's indices vector.
    pub cells: Vec<[u32; 2]>,
    /// Original glyph indices in painter order within each cell.
    pub indices: Vec<u32>,
}

impl GlyphBins {
    pub fn storage_bytes(&self) -> usize {
        self.cells
            .capacity()
            .checked_mul(std::mem::size_of::<[u32; 2]>())
            .and_then(|cells| {
                self.indices
                    .capacity()
                    .checked_mul(std::mem::size_of::<u32>())
                    .and_then(|indices| cells.checked_add(indices))
            })
            .and_then(|bytes| bytes.checked_add(std::mem::size_of::<Self>()))
            .unwrap_or(usize::MAX)
    }
}

/// The clip contains half-open [left, top, right, bottom] pixel bounds.
/// Input order defines painter order; glyph indices are opaque and may repeat.
pub(super) fn build_bins(
    rects: &[GlyphRect],
    clip: [i32; 4],
    frame_width: usize,
    frame_height: usize,
) -> Result<GlyphBins> {
    ensure!(
        (1..=MAX_FRAME_EDGE).contains(&frame_width) && (1..=MAX_FRAME_EDGE).contains(&frame_height),
        "TEXT_INVALID_FRAME"
    );
    let pixels = frame_width
        .checked_mul(frame_height)
        .context("TEXT_FRAME_LIMIT")?;
    ensure!(pixels <= MAX_FRAME_PIXELS, "TEXT_FRAME_LIMIT");
    ensure!(rects.len() <= MAX_GLYPHS, "TEXT_GLYPH_LIMIT");
    let clip = [
        i64::from(clip[0]).max(0),
        i64::from(clip[1]).max(0),
        i64::from(clip[2]).min(frame_width as i64),
        i64::from(clip[3]).min(frame_height as i64),
    ];

    // Discover the minimal visible grid without allocating per-glyph lists.
    // Validate every bitmap even if the clip is empty or its size is zero.
    let mut grid: Option<[usize; 4]> = None;
    for rect in rects {
        ensure!(
            rect.width <= MAX_BITMAP_EDGE && rect.height <= MAX_BITMAP_EDGE,
            "TEXT_MASK_LIMIT"
        );
        if let Some(bounds) = tile_bounds(rect, clip) {
            grid = Some(grid.map_or(bounds, |old| {
                [
                    old[0].min(bounds[0]),
                    old[1].min(bounds[1]),
                    old[2].max(bounds[2]),
                    old[3].max(bounds[3]),
                ]
            }));
        }
    }
    let Some([left, top, right, bottom]) = grid else {
        return Ok(GlyphBins::default());
    };
    let columns = right - left;
    let rows = bottom - top;
    let cell_count = columns.checked_mul(rows).context("TEXT_STAGING_LIMIT")?;
    let cell_bytes = cell_count
        .checked_mul(std::mem::size_of::<[u32; 2]>())
        .and_then(|bytes| bytes.checked_add(std::mem::size_of::<GlyphBins>()))
        .context("TEXT_STAGING_LIMIT")?;
    ensure!(cell_bytes <= MAX_STORAGE_BYTES, "TEXT_STAGING_LIMIT");
    // Frame validation bounds tile coordinates to 0..256, so these pixel
    // origins are representable i32 values and every row offset fits usize.
    let mut bins = GlyphBins {
        origin: [
            (left as i64 * TILE_SIZE) as i32,
            (top as i64 * TILE_SIZE) as i32,
        ],
        columns,
        rows,
        ..GlyphBins::default()
    };
    bins.cells
        .try_reserve_exact(cell_count)
        .context("TEXT_STAGING_LIMIT")?;
    ensure!(
        bins.storage_bytes() <= MAX_STORAGE_BYTES,
        "TEXT_STAGING_LIMIT"
    );
    bins.cells.resize(cell_count, [0, 0]);

    // First pass counts references. Reject the entire operation on excess;
    // duplicate glyph indices consume the same quota as distinct indices.
    let mut reference_count = 0usize;
    for rect in rects {
        if let Some([x0, y0, x1, y1]) = tile_bounds(rect, clip) {
            for y in y0..y1 {
                let row = (y - top) * columns;
                for x in x0..x1 {
                    let cell = &mut bins.cells[row + x - left];
                    let count = cell[1]
                        .checked_add(1)
                        .context("TEXT_TILE_CANDIDATE_LIMIT")?;
                    ensure!(count <= MAX_CANDIDATES, "TEXT_TILE_CANDIDATE_LIMIT");
                    reference_count = reference_count
                        .checked_add(1)
                        .context("TEXT_TILE_REFERENCE_LIMIT")?;
                    ensure!(
                        reference_count <= MAX_REFERENCES,
                        "TEXT_TILE_REFERENCE_LIMIT"
                    );
                    cell[1] = count;
                }
            }
        }
    }

    // Prefix sums establish relative starts. Reset counts to use the same
    // cell table as fill cursors instead of allocating another staging vector.
    let mut start = 0u32;
    for cell in &mut bins.cells {
        let next = start
            .checked_add(cell[1])
            .context("TEXT_TILE_REFERENCE_LIMIT")?;
        *cell = [start, 0];
        start = next;
    }
    debug_assert_eq!(start as usize, reference_count);
    let storage_bytes = reference_count
        .checked_mul(std::mem::size_of::<u32>())
        .and_then(|bytes| bins.storage_bytes().checked_add(bytes))
        .context("TEXT_STAGING_LIMIT")?;
    ensure!(storage_bytes <= MAX_STORAGE_BYTES, "TEXT_STAGING_LIMIT");
    bins.indices
        .try_reserve_exact(reference_count)
        .context("TEXT_STAGING_LIMIT")?;
    ensure!(
        bins.storage_bytes() <= MAX_STORAGE_BYTES,
        "TEXT_STAGING_LIMIT"
    );
    bins.indices.resize(reference_count, 0);

    // Second pass fills each cell in original rectangle order. Bounds and
    // counts are identical to the first pass, so every write is in its range.
    for rect in rects {
        if let Some([x0, y0, x1, y1]) = tile_bounds(rect, clip) {
            for y in y0..y1 {
                let row = (y - top) * columns;
                for x in x0..x1 {
                    let cell = &mut bins.cells[row + x - left];
                    bins.indices[cell[0] as usize + cell[1] as usize] = rect.glyph_index;
                    cell[1] += 1;
                }
            }
        }
    }
    Ok(bins)
}

/// Bitmap dimensions have already been validated. Widen coordinates before
/// adding dimensions, and convert to tile indices only after frame clipping.
fn tile_bounds(rect: &GlyphRect, clip: [i64; 4]) -> Option<[usize; 4]> {
    if rect.width == 0 || rect.height == 0 {
        return None;
    }
    let x0 = i64::from(rect.x).max(clip[0]);
    let y0 = i64::from(rect.y).max(clip[1]);
    let x1 = (i64::from(rect.x) + rect.width as i64).min(clip[2]);
    let y1 = (i64::from(rect.y) + rect.height as i64).min(clip[3]);
    if x0 >= x1 || y0 >= y1 {
        return None;
    }
    Some([
        (x0 / TILE_SIZE) as usize,
        (y0 / TILE_SIZE) as usize,
        ((x1 + TILE_SIZE - 1) / TILE_SIZE) as usize,
        ((y1 + TILE_SIZE - 1) / TILE_SIZE) as usize,
    ])
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::{BTreeMap, BTreeSet};

    fn rect(glyph_index: u32, x: i32, y: i32, width: usize, height: usize) -> GlyphRect {
        GlyphRect {
            glyph_index,
            x,
            y,
            width,
            height,
        }
    }

    fn candidates(bins: &GlyphBins, x: usize, y: usize) -> &[u32] {
        let [start, count] = bins.cells[y * bins.columns + x];
        &bins.indices[start as usize..start as usize + count as usize]
    }

    fn assert_empty(bins: &GlyphBins) {
        assert_eq!(bins.origin, [0, 0]);
        assert_eq!((bins.columns, bins.rows), (0, 0));
        assert!(bins.cells.is_empty());
        assert!(bins.indices.is_empty());
        assert_eq!(bins.storage_bytes(), std::mem::size_of::<GlyphBins>());
    }

    #[test]
    fn exact_sixteen_pixel_edges_are_half_open() {
        let bins = build_bins(&[rect(7, 16, 32, 16, 16)], [0, 0, 64, 64], 64, 64).unwrap();
        assert_eq!(bins.origin, [16, 32]);
        assert_eq!((bins.columns, bins.rows), (1, 1));
        assert_eq!(bins.cells, [[0, 1]]);
        assert_eq!(bins.indices, [7]);
        let bins = build_bins(&[rect(9, 15, 15, 1, 1)], [0, 0, 64, 64], 64, 64).unwrap();
        assert_eq!(bins.origin, [0, 0]);
        assert_eq!((bins.columns, bins.rows), (1, 1));
        assert_eq!(bins.indices, [9]);
        let bins = build_bins(&[rect(11, 15, 15, 2, 2)], [0, 0, 64, 64], 64, 64).unwrap();
        assert_eq!((bins.columns, bins.rows), (2, 2));
        assert_eq!(bins.cells, [[0, 1], [1, 1], [2, 1], [3, 1]]);
        assert_eq!(bins.indices, [11; 4]);
    }

    #[test]
    fn negative_positions_and_partial_clips_intersect_before_tile_alignment() {
        let bins = build_bins(&[rect(3, -8, -17, 32, 40)], [5, 7, 17, 18], 64, 64).unwrap();
        assert_eq!(bins.origin, [0, 0]);
        assert_eq!((bins.columns, bins.rows), (2, 2));
        assert_eq!(bins.indices, [3; 4]);
        let bins = build_bins(&[rect(4, -1, -1, 17, 17)], [-100, -100, 100, 100], 64, 64).unwrap();
        assert_eq!((bins.columns, bins.rows), (1, 1));
        assert_eq!(bins.indices, [4]);
        let bins = build_bins(&[rect(5, -256, 0, 256, 16)], [0, 0, 64, 64], 64, 64).unwrap();
        assert_empty(&bins); // The right edge is exactly zero.
        let bins = build_bins(&[rect(6, -255, 0, 256, 16)], [0, 0, 64, 64], 64, 64).unwrap();
        assert_eq!(bins.indices, [6]);
    }

    #[test]
    fn empty_reversed_and_outside_clips_produce_no_grid() {
        for clip in [
            [8, 0, 8, 16],
            [0, 9, 16, 9],
            [20, 20, 10, 10],
            [-20, -20, -1, -1],
            [64, 64, 100, 100],
            [i32::MAX, i32::MAX, i32::MIN, i32::MIN],
        ] {
            assert_empty(&build_bins(&[rect(1, 0, 0, 64, 64)], clip, 64, 64).unwrap());
        }
        assert_empty(&build_bins(&[], [0, 0, 64, 64], 64, 64).unwrap());
    }

    #[test]
    fn zero_size_and_fully_clipped_glyphs_do_not_enlarge_the_grid() {
        let rects = [
            rect(1, -100, -100, 0, 256),
            rect(2, 50, 50, 256, 0),
            rect(3, -256, -256, 256, 256),
            rect(4, 64, 64, 1, 1),
            rect(5, 48, 48, 1, 1),
        ];
        let bins = build_bins(&rects, [0, 0, 64, 64], 64, 64).unwrap();
        assert_eq!(bins.origin, [48, 48]);
        assert_eq!((bins.columns, bins.rows), (1, 1));
        assert_eq!(bins.indices, [5]);
        assert_empty(&build_bins(&rects[..4], [0, 0, 64, 64], 64, 64).unwrap());
    }

    #[test]
    fn frame_edges_clip_partial_last_tiles() {
        let bins = build_bins(
            &[rect(17, 32, 16, 20, 20)],
            [i32::MIN, i32::MIN, i32::MAX, i32::MAX],
            33,
            17,
        )
        .unwrap();
        assert_eq!(bins.origin, [32, 16]);
        assert_eq!((bins.columns, bins.rows), (1, 1));
        assert_eq!(bins.indices, [17]);
        assert_empty(&build_bins(&[rect(1, 33, 17, 1, 1)], [0, 0, 100, 100], 33, 17).unwrap());
    }

    #[test]
    fn grid_extends_in_both_directions_and_keeps_empty_cells_in_row_major_order() {
        let rects = [
            rect(9, 48, 16, 16, 16),
            rect(3, 16, 48, 16, 16),
            rect(7, 32, 32, 16, 16),
        ];
        let bins = build_bins(&rects, [0, 0, 64, 64], 64, 64).unwrap();
        assert_eq!(bins.origin, [16, 16]);
        assert_eq!((bins.columns, bins.rows), (3, 3));
        assert_eq!(
            bins.cells,
            [
                [0, 0],
                [0, 0],
                [0, 1],
                [1, 0],
                [1, 1],
                [2, 0],
                [2, 1],
                [3, 0],
                [3, 0]
            ]
        );
        assert_eq!(bins.indices, [9, 7, 3]);
    }

    #[test]
    fn each_cell_preserves_input_painter_order_including_duplicate_indices() {
        let rects = [
            rect(90, 0, 0, 32, 16),
            rect(4, 16, 0, 16, 16),
            rect(90, 0, 0, 32, 16),
            rect(u32::MAX, 0, 0, 16, 16),
            rect(0, 0, 0, 32, 16),
        ];
        let bins = build_bins(&rects, [0, 0, 32, 16], 32, 16).unwrap();
        assert_eq!(bins.cells, [[0, 4], [4, 4]]);
        assert_eq!(candidates(&bins, 0, 0), [90, 90, u32::MAX, 0]);
        assert_eq!(candidates(&bins, 1, 0), [90, 4, 90, 0]);
    }

    #[test]
    fn sixty_four_overlapping_candidates_are_allowed_but_sixty_five_are_rejected() {
        let rects = vec![rect(17, 0, 0, 1, 1); 65];
        let bins = build_bins(&rects[..64], [0, 0, 16, 16], 16, 16).unwrap();
        assert_eq!(bins.cells, [[0, 64]]);
        assert_eq!(bins.indices, [17; 64]);
        let error = build_bins(&rects, [0, 0, 16, 16], 16, 16).unwrap_err();
        assert!(error.to_string().contains("CANDIDATE_LIMIT"));
    }

    #[test]
    fn total_reference_limit_is_inclusive_and_excess_is_rejected_without_truncation() {
        let mut rects = Vec::new();
        for y in 0..4 {
            for x in 0..4 {
                for _ in 0..16 {
                    rects.push(rect(rects.len() as u32, x * 256, y * 256, 256, 256));
                }
            }
        }
        let bins = build_bins(&rects, [0, 0, 1024, 1024], 1024, 1024).unwrap();
        assert_eq!((bins.columns, bins.rows), (64, 64));
        assert_eq!(bins.indices.len(), 65_536);
        assert!(bins.cells.iter().all(|cell| cell[1] == 16));
        assert!(bins.storage_bytes() <= 2 * 1024 * 1024);
        rects.push(rect(0, 0, 0, 1, 1));
        let error = build_bins(&rects, [0, 0, 1024, 1024], 1024, 1024).unwrap_err();
        assert!(error.to_string().contains("REFERENCE_LIMIT"));
    }

    #[test]
    fn glyph_count_limit_counts_all_input_rectangles_even_when_invisible() {
        let rects: Vec<_> = (0..2048)
            .map(|i| rect(i, (i % 64) as i32 * 16, (i / 64) as i32 * 16, 1, 1))
            .collect();
        let bins = build_bins(&rects, [0, 0, 1024, 512], 1024, 512).unwrap();
        assert_eq!((bins.columns, bins.rows), (64, 32));
        assert_eq!(bins.indices, (0..2048).collect::<Vec<_>>());
        assert!(bins.cells.iter().all(|cell| cell[1] == 1));
        let invisible = vec![rect(0, 0, 0, 0, 0); 2049];
        assert!(build_bins(&invisible, [0, 0, 0, 0], 1, 1).is_err());
        assert_empty(&build_bins(&invisible[..2048], [0, 0, 1, 1], 1, 1).unwrap());
    }

    #[test]
    fn bitmap_size_limit_is_checked_before_skipping_zero_size_or_clipped_rects() {
        let bins = build_bins(&[rect(1, 1, 1, 256, 256)], [0, 0, 512, 512], 512, 512).unwrap();
        assert_eq!((bins.columns, bins.rows), (17, 17));
        assert_eq!(bins.indices.len(), 17 * 17);
        for (width, height) in [
            (257, 1),
            (1, 257),
            (usize::MAX, 1),
            (1, usize::MAX),
            (0, 257),
            (257, 0),
        ] {
            for clip in [[0, 0, 16, 16], [0, 0, 0, 0]] {
                assert!(
                    build_bins(&[rect(1, i32::MAX, i32::MIN, width, height)], clip, 16, 16)
                        .is_err()
                );
            }
        }
    }

    #[test]
    fn invalid_frame_dimensions_and_pixel_products_are_rejected_without_wrap() {
        for (width, height) in [
            (0, 1),
            (1, 0),
            (4097, 1),
            (1, 4097),
            (4096, 1025),
            (2049, 2048),
            (4096, 4096),
            (usize::MAX, 2),
            (2, usize::MAX),
            (usize::MAX, usize::MAX),
        ] {
            assert!(
                build_bins(&[], [0, 0, 0, 0], width, height).is_err(),
                "{width}x{height}"
            );
        }
        for (width, height) in [(1, 1), (4096, 1024), (1024, 4096), (2048, 2048)] {
            let bins =
                build_bins(&[rect(0, 0, 0, 1, 1)], [0, 0, 4096, 4096], width, height).unwrap();
            assert_eq!(bins.indices, [0]);
        }
    }

    #[test]
    fn extreme_coordinates_and_clip_endpoints_cannot_wrap_into_visible_tiles() {
        let rects = [
            rect(0, i32::MIN, i32::MIN, 256, 256),
            rect(1, i32::MAX, i32::MAX, 256, 256),
            rect(2, i32::MAX, 0, 256, 1),
            rect(3, 0, i32::MAX, 1, 256),
            rect(4, 0, 0, 1, 1),
        ];
        let clip = [i32::MIN, i32::MIN, i32::MAX, i32::MAX];
        assert_empty(&build_bins(&rects[..4], clip, 16, 16).unwrap());
        let bins = build_bins(&rects, clip, 16, 16).unwrap();
        assert_eq!(bins.origin, [0, 0]);
        assert_eq!((bins.columns, bins.rows), (1, 1));
        assert_eq!(bins.indices, [4]);
    }

    #[test]
    fn sparse_grid_can_span_a_large_valid_frame_without_unbounded_staging() {
        let bins = build_bins(
            &[rect(7, 0, 0, 1, 1), rect(8, 1024, 4091, 1, 1)],
            [0, 0, 1025, 4092],
            1025,
            4092,
        )
        .unwrap();
        assert_eq!((bins.columns, bins.rows), (65, 256));
        assert_eq!(bins.cells.len(), 65 * 256);
        assert_eq!(bins.cells[0], [0, 1]);
        assert_eq!(bins.cells[1], [1, 0]);
        assert_eq!(bins.cells.last(), Some(&[1, 1]));
        assert_eq!(bins.indices, [7, 8]);
        assert!(bins.storage_bytes() <= 2 * 1024 * 1024);
    }

    #[test]
    fn storage_bytes_charges_struct_and_vector_capacities_even_for_empty_lengths() {
        let bins = GlyphBins {
            cells: Vec::with_capacity(128),
            indices: Vec::with_capacity(257),
            ..GlyphBins::default()
        };
        assert_eq!(
            bins.storage_bytes(),
            std::mem::size_of::<GlyphBins>()
                + bins.cells.capacity() * std::mem::size_of::<[u32; 2]>()
                + bins.indices.capacity() * std::mem::size_of::<u32>()
        );
        assert_empty(&GlyphBins::default());
    }

    #[test]
    fn clipped_tile_membership_matches_an_independent_pixel_oracle() {
        let rects: Vec<_> = (0..32)
            .map(|i| {
                rect(
                    (i % 7) as u32,
                    (i * 13 % 79) - 17,
                    (i * 19 % 61) - 13,
                    (i * 7 % 34) as usize,
                    (i * 11 % 36) as usize,
                )
            })
            .collect();
        for width in [1, 17, 31, 64] {
            for height in [1, 19, 33] {
                for clip in [
                    [0, 0, width as i32, height as i32],
                    [-7, -3, 18, 20],
                    [7, 5, 50, 31],
                    [20, 15, 9, 4],
                ] {
                    let mut expected = BTreeMap::<(usize, usize), Vec<u32>>::new();
                    for rect in &rects {
                        let mut touched = BTreeSet::new();
                        // Enumerate actual frame pixels instead of duplicating
                        // the builder's rectangle-to-tile rounding arithmetic.
                        for y in 0..height {
                            for x in 0..width {
                                let px = x as i64;
                                let py = y as i64;
                                if px >= i64::from(clip[0])
                                    && px < i64::from(clip[2])
                                    && py >= i64::from(clip[1])
                                    && py < i64::from(clip[3])
                                    && px >= i64::from(rect.x)
                                    && px < i64::from(rect.x) + rect.width as i64
                                    && py >= i64::from(rect.y)
                                    && py < i64::from(rect.y) + rect.height as i64
                                {
                                    touched.insert((x / 16, y / 16));
                                }
                            }
                        }
                        for tile in touched {
                            expected.entry(tile).or_default().push(rect.glyph_index);
                        }
                    }
                    let bins = build_bins(&rects, clip, width, height).unwrap();
                    if expected.is_empty() {
                        assert_empty(&bins);
                        continue;
                    }
                    let left = expected.keys().map(|&(x, _)| x).min().unwrap();
                    let right = expected.keys().map(|&(x, _)| x).max().unwrap();
                    let top = expected.keys().map(|&(_, y)| y).min().unwrap();
                    let bottom = expected.keys().map(|&(_, y)| y).max().unwrap();
                    assert_eq!(bins.origin, [(left * 16) as i32, (top * 16) as i32]);
                    assert_eq!(
                        (bins.columns, bins.rows),
                        (right - left + 1, bottom - top + 1)
                    );
                    let mut next_start = 0;
                    for y in 0..bins.rows {
                        for x in 0..bins.columns {
                            let actual = candidates(&bins, x, y);
                            let expected = expected
                                .get(&(x + left, y + top))
                                .map(Vec::as_slice)
                                .unwrap_or(&[]);
                            assert_eq!(
                                actual, expected,
                                "frame {width}x{height}, clip {clip:?}, tile {x},{y}"
                            );
                            assert_eq!(bins.cells[y * bins.columns + x][0] as usize, next_start);
                            next_start += actual.len();
                        }
                    }
                    assert_eq!(next_start, bins.indices.len());
                    assert!(bins.storage_bytes() <= 2 * 1024 * 1024);
                }
            }
        }
    }
}
