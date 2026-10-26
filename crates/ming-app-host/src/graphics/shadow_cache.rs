//! Fixed-slot metadata planning; callers commit only after a successful draw.
use super::shadow_mask::ShadowKey;

pub(crate) const SLOT_WORDS: u32 = 65_536;

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
struct Entry {
    key: ShadowKey,
    used: u64,
}

#[derive(Default, Clone, Debug, PartialEq, Eq)]
pub(crate) struct CacheLayout {
    entries: [Option<Entry>; 8],
    tick: u64,
}

#[derive(Clone, Debug)]
pub(crate) struct Plan {
    pub next: CacheLayout,
    pub slots: Vec<(ShadowKey, u32)>,
    pub misses: Vec<(ShadowKey, u32)>,
    pub hits: usize,
    pub evictions: usize,
    pub cold_work: usize,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub(crate) enum CacheError {
    Capacity,
    Work,
    Tick,
}

impl std::fmt::Display for CacheError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Capacity => "SHADOW_CACHE_LIMIT",
            Self::Work => "SHADOW_WORK_LIMIT",
            Self::Tick => "SHADOW_TICK_LIMIT",
        })
    }
}
impl std::error::Error for CacheError {}

impl CacheLayout {
    pub(crate) fn plan(&self, keys: &[ShadowKey], cold_budget: usize) -> Result<Plan, CacheError> {
        // Discover the entire pin set before choosing any eviction victim.
        let mut needed = Vec::with_capacity(8);
        for &key in keys {
            if needed.contains(&key) {
                continue;
            }
            if needed.len() == 8 {
                return Err(CacheError::Capacity);
            }
            needed.push(key);
        }

        let mut cold_work = 0_usize;
        for key in &needed {
            if !self.entries.iter().flatten().any(|entry| entry.key == *key) {
                cold_work = cold_work
                    .checked_add(key.generation_work())
                    .ok_or(CacheError::Work)?;
            }
        }
        if cold_work > cold_budget {
            return Err(CacheError::Work);
        }
        let tick = self.tick.checked_add(1).ok_or(CacheError::Tick)?;
        let mut next = self.clone();
        next.tick = tick;
        let mut slots = Vec::with_capacity(needed.len());
        let mut misses = Vec::with_capacity(needed.len());
        let mut hits = 0;
        let mut evictions = 0;
        for &key in &needed {
            let slot = if let Some(slot) = next
                .entries
                .iter()
                .position(|entry| entry.as_ref().is_some_and(|entry| entry.key == key))
            {
                hits += 1;
                slot
            } else {
                let slot = next
                    .entries
                    .iter()
                    .position(Option::is_none)
                    .or_else(|| {
                        next.entries
                            .iter()
                            .enumerate()
                            .filter_map(|(index, entry)| {
                                let entry = entry.as_ref()?;
                                if needed.contains(&entry.key) {
                                    None
                                } else {
                                    Some((entry.used, index))
                                }
                            })
                            .min()
                            .map(|(_, index)| index)
                    })
                    .expect("at most eight unique keys are pinned");
                evictions += usize::from(next.entries[slot].is_some());
                misses.push((key, slot as u32 * SLOT_WORDS));
                slot
            };
            next.entries[slot] = Some(Entry { key, used: tick });
            slots.push((key, slot as u32 * SLOT_WORDS));
        }
        Ok(Plan {
            next,
            slots,
            misses,
            hits,
            evictions,
            cold_work,
        })
    }

    pub fn resident_bytes(&self) -> usize {
        self.entries
            .iter()
            .flatten()
            .map(|entry| entry.key.patch_width() * entry.key.patch_height() * 4)
            .sum()
    }

    pub fn len(&self) -> usize {
        self.entries.iter().flatten().count()
    }
}

#[cfg(test)]
mod tests {
    use super::{CacheError, CacheLayout, Entry, SLOT_WORDS, ShadowKey};

    fn key(radius: i32) -> ShadowKey {
        ShadowKey::new(1_000_000, 1_000_000, radius, 0).unwrap()
    }
    fn eight_keys() -> [ShadowKey; 8] {
        std::array::from_fn(|i| key(i as i32))
    }
    fn full_layout() -> CacheLayout {
        CacheLayout::default()
            .plan(&eight_keys(), usize::MAX)
            .unwrap()
            .next
    }

    #[test]
    fn default_and_empty_frame_keep_zero_residency() {
        let layout = CacheLayout::default();
        assert_eq!(layout.len(), 0);
        assert_eq!(layout.resident_bytes(), 0);
        let plan = layout.plan(&[], 0).unwrap();
        assert!(plan.slots.is_empty() && plan.misses.is_empty());
        assert_eq!((plan.hits, plan.evictions, plan.cold_work), (0, 0, 0));
        assert_eq!(plan.next.len(), 0);
        assert_eq!(plan.next.tick, 1);
        assert_eq!(layout.tick, 0);
    }

    #[test]
    fn misses_deduplicate_in_first_seen_order() {
        let (a, b, c) = (key(1), key(2), key(3));
        let layout = CacheLayout::default();
        let plan = layout.plan(&[b, a, b, c, a, c], usize::MAX).unwrap();
        assert_eq!(
            plan.slots,
            vec![(b, 0), (a, SLOT_WORDS), (c, 2 * SLOT_WORDS)]
        );
        assert_eq!(plan.misses, plan.slots);
        assert_eq!((plan.hits, plan.evictions, plan.next.len()), (0, 0, 3));
        assert_eq!(
            plan.cold_work,
            a.generation_work() + b.generation_work() + c.generation_work()
        );
        assert_eq!(layout, CacheLayout::default());
    }

    #[test]
    fn duplicate_hits_count_unique_keys_and_retain_offsets() {
        let layout = full_layout();
        let original = layout.clone();
        let input: Vec<_> = (0..100).map(|_| key(3)).collect();
        let plan = layout.plan(&input, 0).unwrap();
        assert_eq!(plan.slots, vec![(key(3), 3 * SLOT_WORDS)]);
        assert!(plan.misses.is_empty());
        assert_eq!((plan.hits, plan.evictions, plan.cold_work), (1, 0, 0));
        assert_eq!(plan.next.len(), 8);
        assert_eq!(layout, original);
    }

    #[test]
    fn eight_slots_accept_but_ninth_unique_need_rejects_atomically() {
        let layout = full_layout();
        for (i, entry) in layout.entries.iter().enumerate() {
            assert_eq!(entry.unwrap().key, key(i as i32));
        }
        let original = layout.clone();
        let mut nine = eight_keys().to_vec();
        nine.push(key(8));
        assert_eq!(
            layout.plan(&nine, usize::MAX).unwrap_err(),
            CacheError::Capacity
        );
        assert_eq!(layout, original);
        let recovery = layout.plan(&eight_keys(), 0).unwrap();
        assert_eq!(recovery.hits, 8);
        assert_eq!(recovery.slots.last(), Some(&(key(7), 7 * SLOT_WORDS)));
        assert_eq!(SLOT_WORDS, 65_536);
    }

    #[test]
    fn all_current_needed_keys_are_pinned_before_the_first_miss() {
        let layout = full_layout();
        // A streaming pin implementation would evict key(0) for this first miss.
        let needed = [
            key(8),
            key(0),
            key(2),
            key(3),
            key(4),
            key(5),
            key(6),
            key(7),
        ];
        let plan = layout.plan(&needed, usize::MAX).unwrap();
        assert_eq!(plan.misses, vec![(key(8), SLOT_WORDS)]);
        assert_eq!(plan.slots[1], (key(0), 0));
        assert_eq!((plan.hits, plan.evictions), (7, 1));
        assert_eq!(plan.cold_work, key(8).generation_work());
        assert_eq!(plan.next.entries[0].unwrap().key, key(0));
        assert_eq!(layout.entries[1].unwrap().key, key(1));
    }

    #[test]
    fn lru_orders_by_oldest_tick_then_lowest_slot() {
        let mut layout = full_layout();
        layout.tick = 10;
        for entry in layout.entries.iter_mut().flatten() {
            entry.used = 7;
        }
        layout.entries[5].as_mut().unwrap().used = 2;
        layout.entries[2].as_mut().unwrap().used = 2;
        let plan = layout.plan(&[key(8), key(9), key(10)], usize::MAX).unwrap();
        assert_eq!(
            plan.misses,
            vec![
                (key(8), 2 * SLOT_WORDS),
                (key(9), 5 * SLOT_WORDS),
                (key(10), 0)
            ]
        );
        assert_eq!(plan.evictions, 3);
        assert_eq!(plan.next.tick, 11);
        assert_eq!(plan.next.entries[1], layout.entries[1]);
    }

    #[test]
    fn holes_are_used_in_slot_order_before_any_eviction() {
        let mut layout = CacheLayout {
            tick: 2,
            ..Default::default()
        };
        layout.entries[1] = Some(Entry {
            key: key(0),
            used: 2,
        });
        layout.entries[4] = Some(Entry {
            key: key(1),
            used: 1,
        });
        let plan = layout.plan(&[key(2), key(1), key(3)], usize::MAX).unwrap();
        assert_eq!(
            plan.slots,
            vec![
                (key(2), 0),
                (key(1), 4 * SLOT_WORDS),
                (key(3), 2 * SLOT_WORDS)
            ]
        );
        assert_eq!(plan.misses, vec![(key(2), 0), (key(3), 2 * SLOT_WORDS)]);
        assert_eq!((plan.hits, plan.evictions, plan.next.len()), (1, 0, 4));
        assert_eq!(plan.next.entries[1], layout.entries[1]);
        assert_eq!(plan.next.entries[4].unwrap().used, 3);
    }

    #[test]
    fn cold_work_obeys_exact_boundary_dedup_and_zero_budget() {
        let a = key(1);
        let b = ShadowKey::new(769, 513, 64, 16).unwrap();
        let layout = CacheLayout::default();
        let work = a.generation_work() + b.generation_work();
        assert_eq!(
            layout.plan(&[a, b, a], work - 1).unwrap_err(),
            CacheError::Work
        );
        assert_eq!(layout.plan(&[a], 0).unwrap_err(), CacheError::Work);
        let exact = layout.plan(&[a, b, a], work).unwrap();
        assert_eq!(exact.cold_work, work);
        assert_eq!(exact.misses.len(), 2);
        let warm = exact.next.plan(&[b, a, b], 0).unwrap();
        assert_eq!((warm.hits, warm.cold_work), (2, 0));
        assert!(warm.misses.is_empty());
        let partly_warm = layout.plan(&[a], a.generation_work()).unwrap().next;
        let mixed = partly_warm.plan(&[a, b], b.generation_work()).unwrap();
        assert_eq!(mixed.cold_work, b.generation_work());
        assert_eq!(mixed.hits, 1);
    }

    #[test]
    fn rejected_quota_leaves_metadata_and_next_recovery_unchanged() {
        let layout = full_layout();
        let original = layout.clone();
        let before_bytes = layout.resident_bytes();
        assert_eq!(
            layout.plan(&[key(8), key(0)], 0).unwrap_err(),
            CacheError::Work
        );
        assert_eq!(layout, original);
        assert_eq!(layout.resident_bytes(), before_bytes);
        let after = layout
            .plan(&[key(8), key(0)], key(8).generation_work())
            .unwrap();
        assert_eq!(after.evictions, 1);
        assert_eq!(after.misses, vec![(key(8), SLOT_WORDS)]);
        assert_eq!(layout, original);
    }

    #[test]
    fn checked_tick_limit_is_atomic_for_empty_hit_and_miss_frames() {
        let mut layout = full_layout();
        layout.tick = u64::MAX - 1;
        let last = layout.plan(&[key(0)], 0).unwrap();
        assert_eq!(last.next.tick, u64::MAX);
        assert_eq!(last.next.entries[0].unwrap().used, u64::MAX);
        let exhausted = last.next;
        let original = exhausted.clone();
        for keys in [&[][..], &[key(0)][..], &[key(8)][..]] {
            assert_eq!(
                exhausted.plan(keys, usize::MAX).unwrap_err(),
                CacheError::Tick
            );
            assert_eq!(exhausted, original);
        }
        assert_eq!(layout.tick, u64::MAX - 1);
    }

    #[test]
    fn successful_plan_does_not_commit_and_branches_are_independent() {
        let layout = CacheLayout::default();
        let a = layout.plan(&[key(1)], usize::MAX).unwrap();
        let b = layout.plan(&[key(2)], usize::MAX).unwrap();
        assert_eq!(layout.len(), 0);
        assert_eq!(layout.tick, 0);
        assert_eq!(a.slots, vec![(key(1), 0)]);
        assert_eq!(b.slots, vec![(key(2), 0)]);
        assert_eq!(a.next.entries[0].unwrap().key, key(1));
        assert_eq!(b.next.entries[0].unwrap().key, key(2));
        assert_eq!(a.next.plan(&[key(1)], 0).unwrap().hits, 1);
        assert_eq!(b.next.plan(&[key(1)], 0).unwrap_err(), CacheError::Work);
    }

    #[test]
    fn unused_entries_survive_and_only_needed_keys_are_touched() {
        let layout = full_layout();
        let empty = layout.plan(&[], 0).unwrap();
        assert_eq!(empty.next.entries, layout.entries);
        assert_eq!(empty.next.tick, layout.tick + 1);
        assert_eq!(empty.next.resident_bytes(), layout.resident_bytes());
        let plan = empty.next.plan(&[key(5), key(2)], 0).unwrap();
        assert_eq!(
            plan.slots,
            vec![(key(5), 5 * SLOT_WORDS), (key(2), 2 * SLOT_WORDS)]
        );
        for i in 0..8 {
            let want = if i == 5 || i == 2 {
                plan.next.tick
            } else {
                layout.tick
            };
            assert_eq!(plan.next.entries[i].unwrap().used, want);
        }
    }

    #[test]
    fn canonical_resize_and_effective_radius_reuse_existing_slots() {
        let a = ShadowKey::new(769, 513, 64, 16).unwrap();
        let resized = ShadowKey::new(1_000_000, 999_999, 64, 16).unwrap();
        assert_eq!(a, resized);
        let layout = CacheLayout::default().plan(&[a], usize::MAX).unwrap().next;
        let plan = layout.plan(&[resized, a], 0).unwrap();
        assert_eq!(plan.slots, vec![(a, 0)]);
        assert_eq!((plan.hits, plan.evictions, plan.cold_work), (1, 0, 0));
        assert!(plan.misses.is_empty());
        assert_eq!(plan.next.len(), 1);
        let clamped = ShadowKey::new(5, 7, 64, 2).unwrap();
        let effective = ShadowKey::new(5, 7, 2, 2).unwrap();
        let layout = layout.plan(&[clamped], usize::MAX).unwrap().next;
        assert_eq!(layout.plan(&[effective], 0).unwrap().hits, 1);
    }

    #[test]
    fn resident_bytes_counts_u32_payloads_including_unused_entries() {
        let large = ShadowKey::new(769, 513, 64, 16).unwrap();
        let small = key(0);
        let layout = CacheLayout::default()
            .plan(&[large, small], usize::MAX)
            .unwrap()
            .next;
        assert_eq!(layout.len(), 2);
        assert_eq!(layout.resident_bytes(), 193 * 193 * 4 + 4);
        let plan = layout.plan(&[small], 0).unwrap();
        assert_eq!(plan.next.resident_bytes(), layout.resident_bytes());
        assert_eq!(plan.next.len(), 2);
        assert!(std::mem::size_of::<CacheLayout>() <= 512);
    }

    #[test]
    fn errors_implement_std_error_with_exact_display_codes() {
        let errors = [
            (CacheError::Capacity, "SHADOW_CACHE_LIMIT"),
            (CacheError::Work, "SHADOW_WORK_LIMIT"),
            (CacheError::Tick, "SHADOW_TICK_LIMIT"),
        ];
        for (error, expected) in errors {
            let standard: &dyn std::error::Error = &error;
            assert_eq!(standard.to_string(), expected);
            assert!(standard.source().is_none());
        }
    }
}
