//! Transactional allocation plans for the GPU's packed-u8 coverage atlas.

use std::{collections::HashMap, sync::Arc};

use anyhow::{Context, Result, ensure};
use cosmic_text::CacheKey;

use crate::text::PositionedMask;

const CAPACITY_BYTES: usize = 4 * 1024 * 1024;
const MAX_ENTRIES: usize = 4096;
const MAX_UPLOAD_BYTES: usize = 1024 * 1024;
const MAX_METADATA_BYTES: usize = 1024 * 1024;
const MAX_MASK_EDGE: usize = 256;
// Charge every bucket's control byte plus a full, conservatively sized control
// group and allocation alignment, separately from the key/value bucket storage.
const MAP_CONTROL_BYTES: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Entry {
    offset: u32,
    allocated: u32,
    width: u16,
    height: u16,
    raw_len: u32,
    used: u64,
}

impl Entry {
    fn matches(&self, mask: &PositionedMask) -> bool {
        usize::from(self.width) == mask.width
            && usize::from(self.height) == mask.height
            && self.raw_len as usize == mask.data.len()
    }

    fn new(mask: &PositionedMask, offset: u32, used: u64) -> Result<Self> {
        // Only validated masks reach this constructor: edges <=256, logical
        // and padded lengths <=65536, and atlas offsets <4 MiB.
        Ok(Self {
            offset,
            allocated: padded_bytes(mask.data.len())? as u32,
            width: mask.width as u16,
            height: mask.height as u16,
            raw_len: mask.data.len() as u32,
            used,
        })
    }
}

#[derive(Clone, Copy, Debug)]
struct Limits {
    capacity: usize,
    entries: usize,
    uploads: usize,
    metadata: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            capacity: CAPACITY_BYTES,
            entries: MAX_ENTRIES,
            uploads: MAX_UPLOAD_BYTES,
            metadata: MAX_METADATA_BYTES,
        }
    }
}

/// GPU residency metadata only; CPU alpha Arcs belong to the upload plan.
#[derive(Clone, Debug, Default)]
pub(super) struct GlyphCache {
    entries: HashMap<CacheKey, Entry>,
    // Usable capacity before deletions; tombstones retain their allocated buckets.
    reserved_capacity: usize,
    tick: u64,
    limits: Limits,
}

#[derive(Debug)]
pub(super) struct GlyphUpload {
    pub offset: u32,
    /// Logical alpha bytes. The GPU caller pads each upload to four bytes.
    pub data: Arc<Vec<u8>>,
}

#[derive(Debug)]
pub(super) struct GlyphPlan {
    pub next: GlyphCache,
    /// Byte offsets for this frame's unique, nonempty requested keys only.
    pub offsets: HashMap<CacheKey, u32>,
    pub uploads: Vec<GlyphUpload>,
    /// Previously resident keys removed by this plan; moves are not evictions.
    pub evictions: u64,
    pub compacted: bool,
}

impl GlyphCache {
    /// Does not change this cache. Commit next only after the draw succeeds.
    pub fn plan<'a>(
        &self,
        masks: impl IntoIterator<Item = &'a PositionedMask>,
    ) -> Result<GlyphPlan> {
        let tick = self.tick.checked_add(1).context("TEXT_GPU_TICK_LIMIT")?;
        ensure!(
            self.metadata_bytes() <= self.limits.metadata,
            "TEXT_GPU_METADATA_LIMIT"
        );

        let mut needed = HashMap::<CacheKey, usize>::new();
        let mut ordered = Vec::<&PositionedMask>::new();
        let mut needed_bytes = 0usize;
        let mut missing_bytes = 0usize;
        let mut missing_count = 0usize;
        for mask in masks {
            validate_mask(mask)?;
            if mask.data.is_empty() {
                continue;
            }
            if let Some(entry) = self.entries.get(&mask.key) {
                ensure!(entry.matches(mask), "TEXT_GPU_KEY_COLLISION");
            }
            if let Some(&index) = needed.get(&mask.key) {
                let original = ordered[index];
                ensure!(
                    original.width == mask.width
                        && original.height == mask.height
                        && original.data.len() == mask.data.len(),
                    "TEXT_GPU_KEY_COLLISION"
                );
                continue;
            }
            ensure!(needed.len() < self.limits.entries, "TEXT_GPU_ENTRY_LIMIT");
            let allocated = padded_bytes(mask.data.len())?;
            needed_bytes = needed_bytes
                .checked_add(allocated)
                .context("TEXT_GPU_RESIDENCY_LIMIT")?;
            ensure!(
                needed_bytes <= self.limits.capacity,
                "TEXT_GPU_RESIDENCY_LIMIT"
            );
            if !self.entries.contains_key(&mask.key) {
                missing_bytes = missing_bytes
                    .checked_add(allocated)
                    .context("TEXT_GPU_UPLOAD_LIMIT")?;
                missing_count = missing_count
                    .checked_add(1)
                    .context("TEXT_GPU_ENTRY_LIMIT")?;
            }
            needed.try_reserve(1).context("TEXT_GPU_METADATA_LIMIT")?;
            ordered.try_reserve(1).context("TEXT_GPU_METADATA_LIMIT")?;
            needed.insert(mask.key, ordered.len());
            ordered.push(mask);
        }
        ensure!(
            missing_bytes <= self.limits.uploads,
            "TEXT_GPU_UPLOAD_LIMIT"
        );

        // Pin the complete needed set before choosing victims. Sort once by
        // prior frame use, with allocation offset as a deterministic tie break.
        let mut victims = Vec::new();
        victims
            .try_reserve_exact(self.entries.len())
            .context("TEXT_GPU_METADATA_LIMIT")?;
        victims.extend(
            self.entries
                .iter()
                .filter(|(key, _)| !needed.contains_key(key))
                .map(|(key, entry)| (*key, *entry)),
        );
        victims.sort_unstable_by_key(|(_, entry)| (entry.used, entry.offset));
        let mut count = self
            .entries
            .len()
            .checked_add(missing_count)
            .context("TEXT_GPU_ENTRY_LIMIT")?;
        let mut bytes = self
            .resident_bytes()
            .checked_add(missing_bytes)
            .context("TEXT_GPU_RESIDENCY_LIMIT")?;
        let mut removed = 0usize;
        while count > self.limits.entries || bytes > self.limits.capacity {
            let (_, victim) = victims.get(removed).context("TEXT_GPU_PINNED_LIMIT")?;
            bytes = bytes
                .checked_sub(victim.allocated as usize)
                .context("TEXT_GPU_RESIDENCY_LIMIT")?;
            count = count.checked_sub(1).context("TEXT_GPU_ENTRY_LIMIT")?;
            removed += 1;
        }

        // Reserve fallibly for both the old table and the largest planned
        // table, so subsequent inserts cannot trigger unaccounted growth.
        let mut next = Self {
            entries: HashMap::new(),
            reserved_capacity: 0,
            tick,
            limits: self.limits,
        };
        next.entries
            .try_reserve(self.entries.len().max(count))
            .context("TEXT_GPU_METADATA_LIMIT")?;
        next.reserved_capacity = next.entries.capacity();
        ensure!(
            next.metadata_bytes() <= self.limits.metadata,
            "TEXT_GPU_METADATA_LIMIT"
        );
        next.entries
            .extend(self.entries.iter().map(|(key, entry)| (*key, *entry)));
        for (key, _) in &victims[..removed] {
            next.entries.remove(key);
        }
        // Derive and coalesce all free spans once, after the LRU removals.
        let mut free = next.free_spans()?;
        let mut offsets = HashMap::new();
        offsets
            .try_reserve(ordered.len())
            .context("TEXT_GPU_METADATA_LIMIT")?;
        let mut uploads = Vec::new();
        uploads
            .try_reserve_exact(ordered.len())
            .context("TEXT_GPU_METADATA_LIMIT")?;
        let mut compacted = false;
        for mask in &ordered {
            if let Some(entry) = next.entries.get_mut(&mask.key) {
                entry.used = tick;
                offsets.insert(mask.key, entry.offset);
                continue;
            }
            let allocated = padded_bytes(mask.data.len())?;
            let Some(offset) = first_fit(&mut free, allocated) else {
                // Aggregate needed residency was validated above. Fragmented
                // space requires repacking all needed masks, including hits.
                ensure!(needed_bytes <= self.limits.uploads, "TEXT_GPU_UPLOAD_LIMIT");
                next.entries.clear();
                offsets.clear();
                uploads.clear();
                let mut cursor = 0usize;
                for mask in &ordered {
                    let entry = Entry::new(mask, cursor as u32, tick)?;
                    offsets.insert(mask.key, entry.offset);
                    uploads.push(GlyphUpload {
                        offset: entry.offset,
                        data: Arc::clone(&mask.data),
                    });
                    cursor = cursor
                        .checked_add(entry.allocated as usize)
                        .context("TEXT_GPU_RESIDENCY_LIMIT")?;
                    next.entries.insert(mask.key, entry);
                }
                compacted = true;
                break;
            };
            next.entries
                .insert(mask.key, Entry::new(mask, offset, tick)?);
            offsets.insert(mask.key, offset);
            uploads.push(GlyphUpload {
                offset,
                data: Arc::clone(&mask.data),
            });
        }
        ensure!(
            next.metadata_bytes() <= self.limits.metadata,
            "TEXT_GPU_METADATA_LIMIT"
        );
        let evictions = self
            .entries
            .keys()
            .filter(|key| !next.entries.contains_key(key))
            .count() as u64;
        Ok(GlyphPlan {
            next,
            offsets,
            uploads,
            evictions,
            compacted,
        })
    }

    pub fn resident_bytes(&self) -> usize {
        self.entries
            .values()
            .try_fold(0usize, |bytes, entry| {
                bytes.checked_add(entry.allocated as usize)
            })
            .unwrap_or(usize::MAX)
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Persistent residency metadata, separate from alpha atlas allocation
    /// bytes and from the caller's transient frame/upload staging budget.
    pub fn metadata_bytes(&self) -> usize {
        // HashMap capacity counts usable entries, not allocated buckets. Erasing
        // an entry can leave a tombstone and reduce capacity without freeing any
        // storage, so keep the reservation's capacity as a high-water mark.
        let capacity = self.reserved_capacity.max(self.entries.capacity());
        if capacity == 0 {
            return std::mem::size_of::<Self>();
        }
        let alignment = std::mem::align_of::<(CacheKey, Entry)>().max(MAP_CONTROL_BYTES);
        capacity
            .checked_mul(2)
            .and_then(|buckets| buckets.checked_mul(std::mem::size_of::<(CacheKey, Entry)>() + 1))
            .and_then(|bytes| bytes.checked_add(MAP_CONTROL_BYTES))
            .and_then(|bytes| bytes.checked_add(alignment - 1))
            .and_then(|bytes| bytes.checked_add(std::mem::size_of::<Self>()))
            .unwrap_or(usize::MAX)
    }

    fn free_spans(&self) -> Result<Vec<Span>> {
        let mut occupied = Vec::new();
        occupied
            .try_reserve_exact(self.entries.len())
            .context("TEXT_GPU_METADATA_LIMIT")?;
        occupied.extend(self.entries.values().copied());
        occupied.sort_unstable_by_key(|entry| entry.offset);
        let mut free = Vec::new();
        free.try_reserve_exact(
            occupied
                .len()
                .checked_add(1)
                .context("TEXT_GPU_METADATA_LIMIT")?,
        )
        .context("TEXT_GPU_METADATA_LIMIT")?;
        let mut cursor = 0usize;
        for entry in occupied {
            let offset = entry.offset as usize;
            let end = offset
                .checked_add(entry.allocated as usize)
                .context("TEXT_GPU_RESIDENCY_LIMIT")?;
            ensure!(
                offset >= cursor && end <= self.limits.capacity,
                "TEXT_GPU_INVALID_ALLOCATION"
            );
            if cursor < offset {
                free.push(Span {
                    offset: cursor,
                    len: offset - cursor,
                });
            }
            cursor = end;
        }
        if cursor < self.limits.capacity {
            free.push(Span {
                offset: cursor,
                len: self.limits.capacity - cursor,
            });
        }
        Ok(free)
    }

    #[cfg(test)]
    fn with_limits(capacity: usize, entries: usize, uploads: usize) -> Self {
        assert!(
            capacity <= CAPACITY_BYTES && entries <= MAX_ENTRIES && uploads <= MAX_UPLOAD_BYTES
        );
        Self {
            limits: Limits {
                capacity,
                entries,
                uploads,
                ..Limits::default()
            },
            ..Self::default()
        }
    }
}

#[derive(Clone, Copy)]
struct Span {
    offset: usize,
    len: usize,
}

fn first_fit(free: &mut Vec<Span>, allocated: usize) -> Option<u32> {
    let index = free.iter().position(|span| span.len >= allocated)?;
    let span = &mut free[index];
    let offset = span.offset;
    // Every span is within the validated atlas capacity and allocations are
    // multiples of four, so these updates preserve alignment and cannot wrap.
    span.offset += allocated;
    span.len -= allocated;
    if span.len == 0 {
        free.remove(index);
    }
    Some(offset as u32)
}

fn validate_mask(mask: &PositionedMask) -> Result<()> {
    ensure!(
        mask.width <= MAX_MASK_EDGE && mask.height <= MAX_MASK_EDGE,
        "TEXT_GPU_MASK_LIMIT"
    );
    let raw_len = mask
        .width
        .checked_mul(mask.height)
        .context("TEXT_GPU_MASK_LIMIT")?;
    ensure!(mask.data.len() == raw_len, "TEXT_GPU_INVALID_MASK_DATA");
    Ok(())
}

fn padded_bytes(raw_len: usize) -> Result<usize> {
    raw_len
        .checked_add(3)
        .map(|bytes| bytes & !3)
        .context("TEXT_GPU_RESIDENCY_LIMIT")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::text::TextEngine;
    use cosmic_text::{CacheKeyFlags, fontdb};

    fn mask(id: u16, width: usize, height: usize) -> PositionedMask {
        let key = CacheKey::new(
            fontdb::ID::dummy(),
            id,
            16.0,
            (0.0, 0.0),
            fontdb::Weight::NORMAL,
            CacheKeyFlags::empty(),
        )
        .0;
        PositionedMask {
            key,
            left: 0,
            top: 0,
            width,
            height,
            data: Arc::new(vec![id as u8; width * height]),
        }
    }

    fn state(cache: &GlyphCache) -> (u64, usize, usize, Vec<(CacheKey, Entry)>) {
        let mut entries: Vec<_> = cache
            .entries
            .iter()
            .map(|(key, entry)| (*key, *entry))
            .collect();
        entries.sort_unstable_by_key(|(key, _)| *key);
        (
            cache.tick,
            cache.resident_bytes(),
            cache.metadata_bytes(),
            entries,
        )
    }

    fn upload_bytes(plan: &GlyphPlan) -> usize {
        plan.uploads
            .iter()
            .map(|upload| (upload.data.len() + 3) & !3)
            .sum()
    }

    #[test]
    fn planning_is_immutable_and_committed_hits_do_not_upload() {
        let cache = GlyphCache::default();
        let mask = mask(1, 3, 3);
        let plan = cache.plan([&mask]).unwrap();
        assert_eq!(cache.len(), 0);
        assert_eq!(cache.tick, 0);
        assert_eq!(plan.next.len(), 1);
        assert_eq!(plan.next.resident_bytes(), 12);
        assert_eq!(plan.offsets.get(&mask.key), Some(&0));
        assert_eq!(plan.uploads.len(), 1);
        assert_eq!(plan.uploads[0].offset, 0);
        assert_eq!(plan.uploads[0].data.len(), 9);
        assert!(Arc::ptr_eq(&plan.uploads[0].data, &mask.data));
        assert_eq!(plan.evictions, 0);
        assert!(!plan.compacted);
        let cache = plan.next;
        let before = state(&cache);
        let hit = cache.plan([&mask]).unwrap();
        assert_eq!(state(&cache), before);
        assert!(hit.uploads.is_empty());
        assert_eq!(hit.offsets, plan.offsets);
        assert_eq!(hit.next.entries[&mask.key].used, 2);
        assert_eq!(cache.entries[&mask.key].used, 1);
    }

    #[test]
    fn raw_upload_data_and_four_byte_allocations_have_distinct_lengths() {
        let masks: Vec<_> = (1..=5).map(|id| mask(id, id as usize, 1)).collect();
        let plan = GlyphCache::default().plan(&masks).unwrap();
        for (index, expected) in [0, 4, 8, 12, 16].into_iter().enumerate() {
            assert_eq!(plan.offsets[&masks[index].key], expected);
            assert_eq!(plan.uploads[index].offset, expected);
            assert_eq!(plan.uploads[index].data.len(), index + 1);
        }
        assert_eq!(plan.next.resident_bytes(), 24);
        assert_eq!(upload_bytes(&plan), 24);
    }

    #[test]
    fn duplicate_keys_reuse_one_allocation_and_upload_even_with_many_instances() {
        let cache = GlyphCache::with_limits(16, 1, 16);
        let original = mask(1, 2, 2);
        let mut moved = original.clone();
        moved.left = i32::MAX;
        moved.top = i32::MIN;
        let plan = cache
            .plan([&original, &moved].into_iter().cycle().take(5000))
            .unwrap();
        assert_eq!(plan.offsets.len(), 1);
        assert_eq!(plan.next.len(), 1);
        assert_eq!(plan.uploads.len(), 1);
        assert_eq!(plan.next.resident_bytes(), 4);
    }

    #[test]
    fn integer_translation_and_color_independent_mask_inputs_reuse_real_engine_keys() {
        let mut engine = TextEngine::new().unwrap();
        let layout = engine.layout("MingOS 中文", 1, 16 * 64, 0).unwrap();
        let masks = engine.raster_layout(&layout, 0, 0).unwrap();
        assert!(!masks.is_empty());
        let initial = GlyphCache::default().plan(&masks).unwrap();
        let moved = engine.raster_layout(&layout, 23 * 64, -7 * 64).unwrap();
        for (old, new) in masks.iter().zip(&moved) {
            assert_eq!(old.key, new.key);
            assert_eq!(new.left, old.left + 23);
            assert_eq!(new.top, old.top - 7);
        }
        let hit = initial.next.plan(&moved).unwrap();
        assert_eq!(hit.offsets, initial.offsets);
        assert!(hit.uploads.is_empty());
        // Color is supplied by the parent draw command; changing that command
        // leaves the same PositionedMask inputs and therefore remains a hit.
        let recolored = hit.next.plan(&moved).unwrap();
        assert!(recolored.uploads.is_empty());
        assert_eq!(recolored.evictions, 0);
    }

    #[test]
    fn unused_residents_are_retained_when_neither_count_nor_space_requires_eviction() {
        let masks = [mask(1, 2, 2), mask(2, 2, 2)];
        let cache = GlyphCache::with_limits(32, 4, 32)
            .plan(&masks)
            .unwrap()
            .next;
        let old_used = cache.entries[&masks[1].key].used;
        let hit = cache.plan([&masks[0]]).unwrap();
        assert_eq!(hit.offsets.len(), 1);
        assert_eq!(hit.next.len(), 2);
        assert_eq!(hit.next.entries[&masks[1].key].used, old_used);
        assert_eq!(hit.evictions, 0);
        let empty = hit.next.plan(std::iter::empty()).unwrap();
        assert_eq!(empty.next.len(), 2);
        assert!(empty.offsets.is_empty());
        assert!(empty.uploads.is_empty());
        assert_eq!(empty.evictions, 0);
        assert!(!empty.compacted);
    }

    #[test]
    fn lru_eviction_uses_frame_ticks_and_keeps_needed_entries_pinned() {
        let masks: Vec<_> = (1..=6).map(|id| mask(id, 2, 2)).collect();
        let cache = GlyphCache::with_limits(16, 4, 16)
            .plan(&masks[..4])
            .unwrap()
            .next;
        let first = cache.plan([&masks[3], &masks[4]]).unwrap();
        assert_eq!(first.evictions, 1);
        assert!(!first.next.entries.contains_key(&masks[0].key));
        assert_eq!(first.offsets[&masks[3].key], 12);
        assert_eq!(first.offsets[&masks[4].key], 0);
        let cache = first.next.plan([&masks[1]]).unwrap().next;
        let next = cache.plan([&masks[5]]).unwrap();
        assert_eq!(next.evictions, 1);
        assert!(!next.next.entries.contains_key(&masks[2].key));
        assert!(next.next.entries.contains_key(&masks[1].key));
        assert_eq!(next.offsets[&masks[5].key], 8);

        let cache = GlyphCache::with_limits(8, 2, 8)
            .plan(&masks[..2])
            .unwrap()
            .next;
        let cache = cache.plan([&masks[1]]).unwrap().next;
        let pinned = cache.plan([&masks[0], &masks[2]]).unwrap();
        assert!(pinned.next.entries.contains_key(&masks[0].key));
        assert!(!pinned.next.entries.contains_key(&masks[1].key));
        assert_eq!(pinned.offsets[&masks[0].key], 0);
        assert_eq!(pinned.offsets[&masks[2].key], 4);
    }

    #[test]
    fn entry_count_can_force_lru_eviction_with_plenty_of_byte_capacity() {
        let masks = [mask(1, 2, 2), mask(2, 2, 2), mask(3, 2, 2)];
        let cache = GlyphCache::with_limits(64, 2, 64)
            .plan(&masks[..2])
            .unwrap()
            .next;
        let plan = cache.plan([&masks[2]]).unwrap();
        assert_eq!(plan.evictions, 1);
        assert_eq!(plan.next.len(), 2);
        assert_eq!(plan.next.resident_bytes(), 8);
        assert!(!plan.next.entries.contains_key(&masks[0].key));
        assert!(plan.next.entries.contains_key(&masks[1].key));
    }

    #[test]
    fn first_fit_uses_a_small_early_hole_before_the_larger_tail() {
        let masks = [mask(1, 8, 1), mask(2, 4, 1), mask(3, 8, 1), mask(4, 4, 1)];
        let cache = GlyphCache::with_limits(32, 3, 32)
            .plan(&masks[..3])
            .unwrap()
            .next;
        let plan = cache.plan([&masks[2], &masks[0], &masks[3]]).unwrap();
        assert_eq!(plan.evictions, 1);
        assert!(!plan.compacted);
        assert_eq!(plan.offsets[&masks[3].key], 8);
        assert_eq!(plan.uploads.len(), 1);
        assert_eq!(plan.next.resident_bytes(), 20);
    }

    #[test]
    fn adjacent_lru_evictions_coalesce_before_allocation_without_compaction() {
        let masks = [
            mask(1, 8, 1),
            mask(2, 8, 1),
            mask(3, 8, 1),
            mask(4, 8, 1),
            mask(5, 16, 1),
        ];
        let cache = GlyphCache::with_limits(32, 4, 32)
            .plan(&masks[..4])
            .unwrap()
            .next;
        let plan = cache.plan([&masks[2], &masks[3], &masks[4]]).unwrap();
        assert_eq!(plan.evictions, 2);
        assert!(!plan.compacted);
        assert_eq!(plan.offsets[&masks[4].key], 0);
        assert_eq!(plan.uploads.len(), 1);
        assert_eq!(upload_bytes(&plan), 16);
    }

    #[test]
    fn fragmentation_compacts_needed_only_in_first_seen_order_and_reuploads_all() {
        let masks = [
            mask(1, 8, 1),
            mask(2, 8, 1),
            mask(3, 8, 1),
            mask(4, 8, 1),
            mask(5, 12, 1),
        ];
        let cache = GlyphCache::with_limits(32, 4, 32)
            .plan(&masks[..4])
            .unwrap()
            .next;
        let before = state(&cache);
        let plan = cache
            .plan([&masks[2], &masks[0], &masks[4], &masks[2]])
            .unwrap();
        assert_eq!(state(&cache), before);
        assert!(plan.compacted);
        assert_eq!(plan.evictions, 2);
        assert_eq!(plan.next.len(), 3);
        assert_eq!(plan.next.resident_bytes(), 28);
        assert_eq!(plan.offsets[&masks[2].key], 0);
        assert_eq!(plan.offsets[&masks[0].key], 8);
        assert_eq!(plan.offsets[&masks[4].key], 16);
        assert_eq!(
            plan.uploads
                .iter()
                .map(|upload| upload.offset)
                .collect::<Vec<_>>(),
            [0, 8, 16]
        );
        for (upload, mask) in plan.uploads.iter().zip([&masks[2], &masks[0], &masks[4]]) {
            assert!(Arc::ptr_eq(&upload.data, &mask.data));
        }
        assert_eq!(upload_bytes(&plan), 28);
    }

    #[test]
    fn compaction_upload_limit_counts_existing_hits_and_failure_is_atomic() {
        let masks = [
            mask(1, 8, 1),
            mask(2, 8, 1),
            mask(3, 8, 1),
            mask(4, 8, 1),
            mask(5, 12, 1),
        ];
        let cache = GlyphCache::with_limits(32, 4, 24)
            .plan(&masks[..3])
            .unwrap()
            .next;
        let cache = cache.plan([&masks[3]]).unwrap().next;
        let before = state(&cache);
        let error = cache.plan([&masks[2], &masks[0], &masks[4]]).unwrap_err();
        assert!(error.to_string().contains("UPLOAD_LIMIT"));
        assert_eq!(state(&cache), before);
        assert!(cache.plan([&masks[3]]).unwrap().uploads.is_empty());
    }

    #[test]
    fn exactly_one_mib_of_new_uploads_is_allowed_but_one_padded_byte_more_is_not() {
        let mut masks: Vec<_> = (0..16).map(|id| mask(id, 256, 256)).collect();
        let cache = GlyphCache::default();
        let plan = cache.plan(&masks).unwrap();
        assert_eq!(upload_bytes(&plan), 1024 * 1024);
        assert_eq!(plan.next.resident_bytes(), 1024 * 1024);
        masks.push(mask(16, 1, 1));
        let before = state(&cache);
        assert!(
            cache
                .plan(&masks)
                .unwrap_err()
                .to_string()
                .contains("UPLOAD_LIMIT")
        );
        assert_eq!(state(&cache), before);
    }

    #[test]
    fn padding_is_charged_to_upload_limits_instead_of_just_logical_bytes() {
        let masks = [mask(1, 3, 1), mask(2, 5, 1)];
        let cache = GlyphCache::with_limits(16, 4, 8);
        assert!(cache.plan(&masks).is_err()); // Logical 8 bytes require 12 uploaded bytes.
        assert_eq!(cache.len(), 0);
        let cache = cache.plan([&masks[0]]).unwrap().next;
        let plan = cache.plan([&masks[1]]).unwrap();
        assert_eq!(upload_bytes(&plan), 8);
        assert_eq!(plan.next.resident_bytes(), 12);
    }

    #[test]
    fn exactly_four_mib_of_needed_residency_is_allowed_but_excess_cannot_evict_pins() {
        let mut masks: Vec<_> = (0..64).map(|id| mask(id, 256, 256)).collect();
        let mut cache = GlyphCache::default();
        for chunk in masks.chunks(16) {
            let plan = cache.plan(chunk).unwrap();
            assert_eq!(plan.evictions, 0);
            cache = plan.next;
        }
        assert_eq!(cache.resident_bytes(), 4 * 1024 * 1024);
        assert_eq!(cache.len(), 64);
        let hits = cache.plan(&masks).unwrap();
        assert!(hits.uploads.is_empty());
        assert_eq!(hits.offsets.len(), 64);
        masks.push(mask(64, 1, 1));
        let before = state(&cache);
        assert!(
            cache
                .plan(&masks)
                .unwrap_err()
                .to_string()
                .contains("RESIDENCY_LIMIT")
        );
        assert_eq!(state(&cache), before);
        let miss = cache.plan([&masks[64]]).unwrap();
        assert_eq!(miss.evictions, 1);
        assert_eq!(miss.offsets[&masks[64].key], 0);
        assert_eq!(miss.next.resident_bytes(), 4 * 1024 * 1024 - 65_536 + 4);
    }

    #[test]
    fn exactly_4096_unique_entries_fit_metadata_and_one_more_needed_key_is_rejected() {
        let mut masks: Vec<_> = (0..4096).map(|id| mask(id, 1, 1)).collect();
        let cache = GlyphCache::default().plan(&masks).unwrap().next;
        assert_eq!(cache.len(), 4096);
        assert_eq!(cache.resident_bytes(), 4096 * 4);
        assert!(cache.metadata_bytes() <= 1024 * 1024);
        masks.push(mask(4096, 1, 1));
        let before = state(&cache);
        assert!(
            cache
                .plan(&masks)
                .unwrap_err()
                .to_string()
                .contains("ENTRY_LIMIT")
        );
        assert_eq!(state(&cache), before);
        let replacement = cache.plan([&masks[4096]]).unwrap();
        assert_eq!(replacement.next.len(), 4096);
        assert_eq!(replacement.evictions, 1);
        assert_eq!(replacement.offsets[&masks[4096].key], 0);
        assert!(replacement.next.metadata_bytes() <= 1024 * 1024);
    }

    #[test]
    fn invalid_mask_dimensions_and_data_lengths_fail_before_any_cache_changes() {
        let valid = mask(1, 2, 2);
        let cache = GlyphCache::default().plan([&valid]).unwrap().next;
        let before = state(&cache);
        for (width, height, len) in [
            (257, 1, 257),
            (1, 257, 257),
            (usize::MAX, 1, 0),
            (1, usize::MAX, 0),
            (0, 257, 0),
            (2, 2, 3),
            (2, 2, 5),
            (0, 1, 1),
        ] {
            let mut invalid = mask(2, 1, 1);
            invalid.width = width;
            invalid.height = height;
            invalid.data = Arc::new(vec![0; len]);
            assert!(cache.plan([&valid, &invalid]).is_err());
            assert_eq!(state(&cache), before);
        }
    }

    #[test]
    fn immutable_key_dimensions_are_checked_for_residents_and_within_one_plan() {
        let original = mask(1, 4, 4);
        let mut changed = mask(1, 2, 8); // Same raw length is still a shape collision.
        assert_eq!(changed.key, original.key);
        let cache = GlyphCache::default().plan([&original]).unwrap().next;
        let before = state(&cache);
        assert!(cache.plan([&mask(2, 1, 1), &changed]).is_err());
        assert_eq!(state(&cache), before);
        assert!(GlyphCache::default().plan([&original, &changed]).is_err());
        changed.width = 4;
        changed.height = 5;
        changed.data = Arc::new(vec![0; 20]);
        assert!(cache.plan([&changed]).is_err());
        assert_eq!(state(&cache), before);
    }

    #[test]
    fn zero_area_masks_are_validated_then_skipped_without_upload_or_eviction() {
        let nonempty = mask(1, 1, 1);
        let cache = GlyphCache::default().plan([&nonempty]).unwrap().next;
        let masks = [mask(2, 0, 256), mask(3, 256, 0), mask(4, 0, 0)];
        let plan = cache.plan(&masks).unwrap();
        assert!(plan.offsets.is_empty());
        assert!(plan.uploads.is_empty());
        assert_eq!(plan.next.len(), 1);
        assert_eq!(plan.evictions, 0);
    }

    #[test]
    fn metadata_covers_bucket_storage_after_tombstones_reduce_usable_capacity() {
        use std::hash::BuildHasher;

        let masks: Vec<_> = (1..=24).map(|id| mask(id, 1, 1)).collect();
        let mut cache = GlyphCache::default().plan(&masks).unwrap().next;
        let reserved = cache.entries.capacity();
        // Independently recover the original SwissTable bucket count from its
        // pristine usable capacity. Deleting entries does not free that table.
        let buckets = (reserved + 1).next_power_of_two();
        cache.entries.clear();
        let mut colliding = Vec::new();
        for id in 0..=u16::MAX {
            let mask = mask(id, 1, 1);
            if cache.entries.hasher().hash_one(mask.key) as usize & (buckets - 1) != 0 {
                continue;
            }
            let offset = (colliding.len() * 4) as u32;
            cache
                .entries
                .insert(mask.key, Entry::new(&mask, offset, cache.tick).unwrap());
            colliding.push(mask.key);
            if colliding.len() == 24 {
                break;
            }
        }
        assert_eq!(colliding.len(), 24);
        assert_eq!(cache.entries.capacity(), reserved);
        for key in &colliding[..16] {
            cache.entries.remove(key);
        }
        assert!(cache.entries.capacity() < reserved);
        // Count every original key/value bucket and its control byte, plus at
        // least one mirrored control byte. This lower bound does not reuse the
        // production estimate, its spare-slot multiplier, or overhead constant.
        let bucket_lower_bound = std::mem::size_of::<GlyphCache>()
            + buckets * (std::mem::size_of::<(CacheKey, Entry)>() + 1)
            + 1;
        assert!(
            cache.metadata_bytes() >= bucket_lower_bound,
            "metadata {} < bucket lower bound {}; reserved={reserved}, usable={}, buckets={buckets}",
            cache.metadata_bytes(),
            bucket_lower_bound,
            cache.entries.capacity(),
        );
    }
    #[test]
    fn metadata_accounting_uses_reserved_map_capacity_and_enforces_its_own_limit() {
        let masks = [mask(1, 1, 1), mask(2, 1, 1)];
        let cache = GlyphCache::default().plan(&masks).unwrap().next;
        let buckets = (cache.entries.capacity() + 1).next_power_of_two();
        assert!(
            cache.metadata_bytes()
                > std::mem::size_of::<GlyphCache>()
                    + buckets * (std::mem::size_of::<(CacheKey, Entry)>() + 1)
        );
        assert_eq!(cache.limits.metadata, 1024 * 1024);
        assert!(cache.entries.capacity() > cache.entries.len());
        let mut limited = GlyphCache::default();
        limited.limits.metadata = std::mem::size_of::<GlyphCache>();
        let before = state(&limited);
        assert!(
            limited
                .plan(&masks)
                .unwrap_err()
                .to_string()
                .contains("METADATA_LIMIT")
        );
        assert_eq!(state(&limited), before);
        assert!(limited.plan(std::iter::empty()).is_ok());
    }

    #[test]
    fn cache_does_not_keep_cpu_alpha_arcs_after_the_upload_plan_is_released() {
        let mask = mask(1, 2, 2);
        let weak = Arc::downgrade(&mask.data);
        let plan = GlyphCache::default().plan([&mask]).unwrap();
        let GlyphPlan { next, uploads, .. } = plan;
        drop(mask);
        assert!(weak.upgrade().is_some());
        drop(uploads);
        assert!(weak.upgrade().is_none());
        assert_eq!(next.len(), 1);
        assert_eq!(next.resident_bytes(), 4);
    }

    #[test]
    fn exhausted_frame_tick_fails_atomically_instead_of_wrapping_lru_order() {
        let mask = mask(1, 1, 1);
        let mut cache = GlyphCache::default().plan([&mask]).unwrap().next;
        cache.tick = u64::MAX;
        let before = state(&cache);
        assert!(cache.plan([&mask]).is_err());
        assert_eq!(state(&cache), before);
    }

    #[test]
    fn zero_test_budgets_accept_only_an_empty_plan() {
        let cache = GlyphCache::with_limits(0, 0, 0);
        assert!(cache.plan(std::iter::empty()).is_ok());
        assert!(cache.plan([&mask(1, 1, 1)]).is_err());
        assert_eq!(cache.len(), 0);
    }
}
