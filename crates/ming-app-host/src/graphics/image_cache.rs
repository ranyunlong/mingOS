//! Pure pixel-span allocation for the per-session GPU image cache.

use std::collections::BTreeMap;

use anyhow::{Result, ensure};

const MAX_CAPACITY_PIXELS: usize = 8_388_608;
const MAX_IMAGES: usize = 8;

#[derive(Clone, Copy)]
struct Allocation {
    offset: usize,
    len: usize,
}

pub(super) struct CacheLayout {
    capacity_pixels: usize,
    allocations: BTreeMap<i32, Allocation>,
}

#[derive(Debug)]
pub(super) struct CacheUpdate {
    pub(super) offsets: BTreeMap<i32, u32>,
    pub(super) uploads: Vec<i32>,
    pub(super) compacted: bool,
}

impl CacheLayout {
    /// Capacity is trusted configuration; zero permits only an empty cache.
    pub(super) fn new(capacity_pixels: usize) -> Self {
        assert!(capacity_pixels <= MAX_CAPACITY_PIXELS);
        Self {
            capacity_pixels,
            allocations: BTreeMap::new(),
        }
    }

    /// Synchronize the complete set of live images. Live IDs have immutable lengths.
    /// Validate everything before planning or committing any layout changes.
    pub(super) fn sync(&mut self, sizes: &BTreeMap<i32, usize>) -> Result<CacheUpdate> {
        ensure!(
            sizes.len() <= MAX_IMAGES,
            "image cache supports at most {MAX_IMAGES} images"
        );
        let mut remaining = self.capacity_pixels;
        for (&id, &len) in sizes {
            ensure!(id > 0, "image cache ID must be positive: {id}");
            ensure!(len > 0, "image cache length must be positive for ID {id}");
            // Subtracting from the budget also handles usize::MAX without overflow.
            ensure!(len <= remaining, "image cache pixel capacity exceeded");
            remaining -= len;
            if let Some(allocation) = self.allocations.get(&id) {
                ensure!(
                    allocation.len == len,
                    "image cache length changed for live ID {id}"
                );
            }
        }

        let mut allocations = self.allocations.clone();
        allocations.retain(|id, _| sizes.contains_key(id));
        let mut uploads = Vec::new();
        let mut compacted = false;

        for (&id, &len) in sizes {
            if allocations.contains_key(&id) {
                continue;
            }
            if let Some(offset) = self.first_fit(&allocations, len) {
                allocations.insert(id, Allocation { offset, len });
                uploads.push(id);
            } else {
                // Total size was validated, so packing all live images must fit.
                // Replace any partial plan and upload each live ID exactly once.
                allocations.clear();
                let mut offset = 0;
                for (&live_id, &live_len) in sizes {
                    allocations.insert(
                        live_id,
                        Allocation {
                            offset,
                            len: live_len,
                        },
                    );
                    offset += live_len;
                }
                uploads = sizes.keys().copied().collect();
                compacted = true;
                break;
            }
        }

        // The constructor bounds every offset well below u32::MAX.
        let offsets = allocations
            .iter()
            .map(|(&id, allocation)| (id, allocation.offset as u32))
            .collect();
        self.allocations = allocations;
        Ok(CacheUpdate {
            offsets,
            uploads,
            compacted,
        })
    }

    pub(super) fn release(&mut self, id: i32) -> bool {
        self.allocations.remove(&id).is_some()
    }

    fn first_fit(&self, allocations: &BTreeMap<i32, Allocation>, len: usize) -> Option<usize> {
        let mut occupied: Vec<_> = allocations.values().collect();
        occupied.sort_unstable_by_key(|allocation| allocation.offset);
        let mut start = 0;
        for allocation in occupied {
            if len <= allocation.offset - start {
                return Some(start);
            }
            start = allocation.offset + allocation.len;
        }
        (len <= self.capacity_pixels - start).then_some(start)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unchanged_images_keep_offsets_without_uploading() {
        let mut layout = CacheLayout::new(12);
        let sizes = BTreeMap::from([(2, 3), (5, 4)]);
        let first = layout.sync(&sizes).unwrap();
        assert_eq!(first.offsets, BTreeMap::from([(2, 0), (5, 3)]));
        assert_eq!(first.uploads, vec![2, 5]);
        assert!(!first.compacted);

        let second = layout.sync(&sizes).unwrap();
        assert_eq!(second.offsets, first.offsets);
        assert!(second.uploads.is_empty());
        assert!(!second.compacted);
    }

    #[test]
    fn released_span_is_reused_before_the_larger_tail() {
        let mut layout = CacheLayout::new(20);
        layout
            .sync(&BTreeMap::from([(1, 4), (2, 4), (3, 4)]))
            .unwrap();
        assert!(!layout.release(0));
        assert!(!layout.release(-1));
        assert!(!layout.release(99));
        assert!(layout.release(2));
        assert!(!layout.release(2));

        let sizes = BTreeMap::from([(1, 4), (3, 4), (4, 3)]);
        let update = layout.sync(&sizes).unwrap();
        assert_eq!(update.offsets, BTreeMap::from([(1, 0), (3, 8), (4, 4)]));
        assert_eq!(update.uploads, vec![4]);
        assert!(!update.compacted);
    }

    #[test]
    fn adjacent_releases_coalesce_in_either_order() {
        for released in [[2, 3], [3, 2]] {
            let mut layout = CacheLayout::new(16);
            layout
                .sync(&BTreeMap::from([(1, 4), (2, 4), (3, 4), (4, 4)]))
                .unwrap();
            for id in released {
                assert!(layout.release(id));
            }
            let update = layout
                .sync(&BTreeMap::from([(1, 4), (4, 4), (5, 8)]))
                .unwrap();
            assert_eq!(update.offsets, BTreeMap::from([(1, 0), (4, 12), (5, 4)]));
            assert_eq!(update.uploads, vec![5]);
            assert!(!update.compacted);
        }
    }

    #[test]
    fn sync_evicts_absent_images_before_allocating() {
        let mut layout = CacheLayout::new(12);
        layout
            .sync(&BTreeMap::from([(1, 4), (2, 4), (3, 4)]))
            .unwrap();
        let update = layout.sync(&BTreeMap::from([(1, 4), (4, 8)])).unwrap();
        assert_eq!(update.offsets, BTreeMap::from([(1, 0), (4, 4)]));
        assert_eq!(update.uploads, vec![4]);
        assert!(!update.compacted);
        assert!(!layout.release(2));
        assert!(!layout.release(3));
    }

    #[test]
    fn fragmentation_compacts_eight_images_and_uploads_each_once() {
        let mut layout = CacheLayout::new(16);
        let initial = (1..=8).map(|id| (id, 2)).collect();
        layout.sync(&initial).unwrap();
        // The first new image fits a hole; the next one forces compaction.
        let sizes = BTreeMap::from([
            (1, 2),
            (3, 2),
            (5, 2),
            (7, 2),
            (9, 1),
            (10, 3),
            (11, 3),
            (12, 1),
        ]);
        let update = layout.sync(&sizes).unwrap();
        assert!(update.compacted);
        assert_eq!(update.uploads, vec![1, 3, 5, 7, 9, 10, 11, 12]);
        assert_eq!(
            update.offsets,
            BTreeMap::from([
                (1, 0),
                (3, 2),
                (5, 4),
                (7, 6),
                (9, 8),
                (10, 9),
                (11, 12),
                (12, 15),
            ])
        );
        let repeated = layout.sync(&sizes).unwrap();
        assert_eq!(repeated.offsets, update.offsets);
        assert!(repeated.uploads.is_empty());
        assert!(!repeated.compacted);
    }

    #[test]
    fn failed_sync_preserves_live_images_and_free_spans() {
        let invalid_inputs = [
            BTreeMap::from([(0, 1)]),
            BTreeMap::from([(-1, 1)]),
            BTreeMap::from([(i32::MIN, 1)]),
            BTreeMap::from([(9, 0)]),
            BTreeMap::from([(9, 21)]),
            BTreeMap::from([(9, usize::MAX)]),
            BTreeMap::from([(4, 4), (9, 17)]),
            BTreeMap::from([(4, 5), (6, 2)]),
            BTreeMap::from([(4, 3), (6, 2)]),
            (1..=9).map(|id| (id, 1)).collect(),
        ];
        for invalid in invalid_inputs {
            let mut layout = CacheLayout::new(20);
            layout
                .sync(&BTreeMap::from([(2, 6), (4, 4), (6, 2)]))
                .unwrap();
            assert!(layout.release(2));
            assert!(layout.sync(&invalid).is_err(), "accepted {invalid:?}");

            let sizes = BTreeMap::from([(4, 4), (6, 2)]);
            let unchanged = layout.sync(&sizes).unwrap();
            assert_eq!(unchanged.offsets, BTreeMap::from([(4, 6), (6, 10)]));
            assert!(unchanged.uploads.is_empty());
            assert!(!unchanged.compacted);

            let reused = layout
                .sync(&BTreeMap::from([(4, 4), (6, 2), (8, 6)]))
                .unwrap();
            assert_eq!(reused.offsets, BTreeMap::from([(4, 6), (6, 10), (8, 0)]));
            assert_eq!(reused.uploads, vec![8]);
            assert!(!reused.compacted);
        }
    }

    #[test]
    fn empty_sync_evicts_everything_and_restores_full_capacity() {
        let mut layout = CacheLayout::new(10);
        layout.sync(&BTreeMap::from([(1, 3), (2, 7)])).unwrap();
        let empty = layout.sync(&BTreeMap::new()).unwrap();
        assert!(empty.offsets.is_empty());
        assert!(empty.uploads.is_empty());
        assert!(!empty.compacted);
        assert!(!layout.release(1));
        let full = layout.sync(&BTreeMap::from([(3, 10)])).unwrap();
        assert_eq!(full.offsets, BTreeMap::from([(3, 0)]));
        assert_eq!(full.uploads, vec![3]);
        assert!(!full.compacted);
    }

    #[test]
    fn released_ids_can_be_reintroduced_with_different_lengths() {
        let mut layout = CacheLayout::new(8);
        layout.sync(&BTreeMap::from([(1, 3), (2, 2)])).unwrap();
        assert!(layout.release(1));
        let update = layout.sync(&BTreeMap::from([(1, 2), (2, 2)])).unwrap();
        assert_eq!(update.offsets, BTreeMap::from([(1, 0), (2, 3)]));
        assert_eq!(update.uploads, vec![1]);
        assert!(!update.compacted);
    }

    #[test]
    fn zero_capacity_accepts_only_empty_input() {
        let mut layout = CacheLayout::new(0);
        assert!(layout.sync(&BTreeMap::from([(1, 1)])).is_err());
        assert!(!layout.release(1));
        let empty = layout.sync(&BTreeMap::new()).unwrap();
        assert!(empty.offsets.is_empty());
        assert!(empty.uploads.is_empty());
        assert!(!empty.compacted);
    }

    #[test]
    fn maximum_capacity_and_positive_handle_are_supported() {
        let mut layout = CacheLayout::new(8_388_608);
        let update = layout
            .sync(&BTreeMap::from([(i32::MAX, 8_388_608)]))
            .unwrap();
        assert_eq!(update.offsets, BTreeMap::from([(i32::MAX, 0)]));
        assert_eq!(update.uploads, vec![i32::MAX]);
        assert!(!update.compacted);
        assert!(layout.release(i32::MAX));
        let update = layout
            .sync(&BTreeMap::from([(1, 8_388_607), (2, 1)]))
            .unwrap();
        assert_eq!(update.offsets, BTreeMap::from([(1, 0), (2, 8_388_607)]));
    }

    #[test]
    #[should_panic]
    fn constructor_rejects_capacity_above_the_limit() {
        CacheLayout::new(8_388_609);
    }
}
