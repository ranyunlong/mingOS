//! App-scoped immutable text layouts and their retained-resource budget.

use std::{collections::HashMap, sync::Arc};

use super::engine::{ShapedLayout, TextEngine};

const FIRST_HANDLE: i32 = 0x3000_0001;
const LAST_HANDLE: i32 = 0x3fff_ffff;
const LIVE_LAYOUT_LIMIT: usize = 64;
const INPUT_BYTES_LIMIT: usize = 512;
const RETAINED_INPUT_LIMIT: usize = 32_768;
const RETAINED_BYTES_LIMIT: usize = 2 * 1024 * 1024;
// Charge every reserved map slot, including vacant slots, enough for the
// entry, hash-table bookkeeping, and its layout's Arc allocation overhead.
const ENTRY_OVERHEAD: usize = 256;

struct Entry {
    active: bool,
    layout: Arc<ShapedLayout>,
}

/// One app session's text capabilities. Numeric handles can coincide between
/// services; resource lookup is scoped to this service, and the engine checks
/// layout ownership when rasterizing.
pub struct TextService {
    engine: TextEngine,
    entries: HashMap<i32, Entry>,
    next_handle: i32,
}

impl TextService {
    pub fn new() -> anyhow::Result<Self> {
        // Initialization failures remain host errors rather than guest codes.
        let mut engine = TextEngine::new()?;
        let limit = RETAINED_BYTES_LIMIT
            .checked_sub(std::mem::size_of::<Self>())
            .ok_or_else(|| anyhow::anyhow!("TEXT_METADATA_LIMIT"))?;
        engine.trim_metadata(limit)?;
        Ok(Self {
            engine,
            entries: HashMap::new(),
            next_handle: FIRST_HANDLE,
        })
    }

    pub fn create(&mut self, bytes: &[u8], font: i32, em_q6: i32, flags: i32) -> i32 {
        self.sweep_closed();
        let Some((layout_bytes, _)) = self.entry_totals() else {
            return -4;
        };
        if let Err(code) = self.fit_metadata(self.entries.capacity(), layout_bytes) {
            return code;
        }
        match self.try_create(bytes, font, em_q6, flags) {
            Ok(handle) => handle,
            Err(code) => {
                // A rejected shape can still have populated the mask cache.
                // Reclaim unpinned metadata against the previously owned set.
                if let Some((layout_bytes, _)) = self.entry_totals() {
                    let _ = self.fit_metadata(self.entries.capacity(), layout_bytes);
                }
                code
            }
        }
    }

    pub fn metrics(&self, handle: i32) -> Option<[i32; 12]> {
        self.active_entry(handle).map(|entry| entry.layout.metrics)
    }

    pub fn close(&mut self, handle: i32) -> bool {
        self.sweep_closed();
        if !is_text_handle(handle) {
            return false;
        }
        let Some(entry) = self.entries.get_mut(&handle).filter(|entry| entry.active) else {
            return false;
        };
        entry.active = false;
        self.sweep_closed();
        true
    }

    pub fn get(&self, handle: i32) -> Option<Arc<ShapedLayout>> {
        self.active_entry(handle)
            .map(|entry| Arc::clone(&entry.layout))
    }

    pub(crate) fn raster_layout(
        &mut self,
        layout: &ShapedLayout,
        x_q6: i32,
        baseline_q6: i32,
    ) -> anyhow::Result<Vec<super::engine::PositionedMask>> {
        self.sweep_closed();
        let (layout_bytes, _) = self
            .entry_totals()
            .ok_or_else(|| anyhow::anyhow!("TEXT_METADATA_LIMIT"))?;
        self.fit_metadata(self.entries.capacity(), layout_bytes)
            .map_err(|_| anyhow::anyhow!("TEXT_METADATA_LIMIT"))?;
        self.engine.raster_layout(layout, x_q6, baseline_q6)
    }

    #[cfg(test)]
    fn engine_mut(&mut self) -> &mut TextEngine {
        &mut self.engine
    }

    pub fn live_count(&self) -> usize {
        self.entries.values().filter(|entry| entry.active).count()
    }

    /// Includes closed layouts until a mutating operation observes that their
    /// last external Arc has been released. Vacant map capacity stays charged.
    pub fn retained_bytes(&self) -> usize {
        self.entry_totals()
            .and_then(|(layouts, _)| Self::owned_metadata_bytes(self.entries.capacity(), layouts))
            .and_then(|owned| owned.checked_add(self.engine.retained_metadata_bytes()))
            .unwrap_or(usize::MAX)
    }

    fn try_create(&mut self, bytes: &[u8], font: i32, em_q6: i32, flags: i32) -> Result<i32, i32> {
        if bytes.len() > INPUT_BYTES_LIMIT {
            return Err(-4);
        }
        let text = std::str::from_utf8(bytes).map_err(|_| -2)?;
        if self.live_count() >= LIVE_LAYOUT_LIMIT || !is_text_handle(self.next_handle) {
            return Err(-4);
        }
        let (layout_bytes, input_bytes) = self.entry_totals().ok_or(-4)?;
        if input_bytes.checked_add(bytes.len()).ok_or(-4)? > RETAINED_INPUT_LIMIT {
            return Err(-4);
        }
        let layout = self
            .engine
            .layout(text, font, em_q6, flags)
            .map_err(|error| engine_error_code(&error))?;
        let layout_bytes = layout_bytes
            .checked_add(layout.retained_bytes())
            .ok_or(-4)?;

        // Grow into a temporary map: failed reservations or budget checks must
        // not leave an enlarged allocation charged to the old resource set.
        let mut grown_entries = None;
        let capacity = if self.entries.len() == self.entries.capacity() {
            let mut entries = HashMap::<i32, Entry>::new();
            entries
                .try_reserve(self.entries.len().checked_add(1).ok_or(-4)?)
                .map_err(|_| -4)?;
            let capacity = entries.capacity();
            grown_entries = Some(entries);
            capacity
        } else {
            self.entries.capacity()
        };
        self.fit_metadata(capacity, layout_bytes)?;
        let handle = self.next_handle;
        let next_handle = handle.checked_add(1).ok_or(-4)?;
        let entry = Entry {
            active: true,
            layout: Arc::new(layout),
        };

        // All recoverable failure points precede publication and ID advance.
        if let Some(mut entries) = grown_entries {
            entries.extend(self.entries.drain());
            self.entries = entries;
        }
        self.entries.insert(handle, entry);
        self.next_handle = next_handle;
        Ok(handle)
    }

    fn active_entry(&self, handle: i32) -> Option<&Entry> {
        if !is_text_handle(handle) {
            return None;
        }
        self.entries.get(&handle).filter(|entry| entry.active)
    }

    fn sweep_closed(&mut self) {
        self.entries
            .retain(|_, entry| entry.active || Arc::strong_count(&entry.layout) > 1);
        if self.entries.is_empty() {
            // Dropping an empty table releases capacity without allocating.
            self.entries = HashMap::new();
        }
    }

    fn entry_totals(&self) -> Option<(usize, usize)> {
        self.entries
            .values()
            .try_fold((0usize, 0usize), |(layout_bytes, input_bytes), entry| {
                Some((
                    layout_bytes.checked_add(entry.layout.retained_bytes())?,
                    input_bytes.checked_add(entry.layout.input_bytes())?,
                ))
            })
    }

    fn owned_metadata_bytes(capacity: usize, layout_bytes: usize) -> Option<usize> {
        capacity
            .checked_mul(ENTRY_OVERHEAD)?
            .checked_add(std::mem::size_of::<Self>())?
            .checked_add(layout_bytes)
    }

    fn fit_metadata(&mut self, capacity: usize, layout_bytes: usize) -> Result<(), i32> {
        let owned = Self::owned_metadata_bytes(capacity, layout_bytes).ok_or(-4)?;
        let limit = RETAINED_BYTES_LIMIT.checked_sub(owned).ok_or(-4)?;
        self.engine
            .trim_metadata(limit)
            .map_err(|error| engine_error_code(&error))
    }
}

fn is_text_handle(handle: i32) -> bool {
    (FIRST_HANDLE..=LAST_HANDLE).contains(&handle)
}

fn engine_error_code(error: &anyhow::Error) -> i32 {
    for cause in error.chain() {
        let message = cause.to_string();
        if message.starts_with("TEXT_MISSING_GLYPH") {
            return -5;
        }
        if message.starts_with("TEXT_UNSUPPORTED") {
            return -6;
        }
        if message.starts_with("TEXT_")
            && (message.contains("LIMIT")
                || message.contains("CACHE")
                || message.contains("METADATA"))
        {
            return -4;
        }
    }
    -2
}

#[cfg(test)]
mod tests {
    use super::*;

    const FONT: i32 = 1;
    const EM: i32 = 16 * 64;

    #[test]
    fn sixty_four_live_layouts_then_close_releases_a_slot_without_reusing_ids() {
        let mut service = TextService::new().unwrap();
        let handles: Vec<_> = (0..64).map(|_| service.create(b"A", FONT, EM, 0)).collect();
        for (index, handle) in handles.iter().enumerate() {
            assert_eq!(*handle, FIRST_HANDLE + index as i32);
        }
        let metrics = service.metrics(handles[0]).unwrap();
        assert_eq!(service.live_count(), 64);
        assert_eq!(service.create(b"overflow", FONT, EM, 0), -4);
        assert_eq!(service.metrics(handles[0]), Some(metrics));
        assert!(service.close(handles[10]));
        assert!(!service.close(handles[10]));
        assert!(service.get(handles[10]).is_none());
        assert!(service.metrics(handles[10]).is_none());
        assert_eq!(
            service.create(b"replacement", FONT, EM, 0),
            FIRST_HANDLE + 64
        );
        assert_eq!(service.live_count(), 64);
        assert!(service.retained_bytes() <= RETAINED_BYTES_LIMIT);
    }

    #[test]
    fn invalid_and_other_capability_handles_never_resolve() {
        let mut service = TextService::new().unwrap();
        let handle = service.create(b"owned", FONT, EM, 0);
        assert_eq!(handle, FIRST_HANDLE);
        for invalid in [
            i32::MIN,
            -1,
            0,
            1,
            0x1000_0001,
            0x2000_0001,
            0x3000_0000,
            FIRST_HANDLE + 1,
            0x4000_0000,
            i32::MAX,
        ] {
            assert!(service.get(invalid).is_none(), "{invalid:#x}");
            assert!(service.metrics(invalid).is_none(), "{invalid:#x}");
            assert!(!service.close(invalid), "{invalid:#x}");
        }
        assert_eq!(service.live_count(), 1);
        assert!(service.get(handle).is_some());
    }

    #[test]
    fn utf8_is_strict_and_input_limit_counts_bytes() {
        let mut service = TextService::new().unwrap();
        for bytes in [
            &[0xff][..],
            &[0xc0, 0xaf],
            &[0xed, 0xa0, 0x80],
            &[0xf4, 0x90, 0x80, 0x80],
            &[0xe4, 0xb8],
        ] {
            assert_eq!(service.create(bytes, FONT, EM, 0), -2);
        }
        assert_eq!(service.create(&[b'A'; 513], FONT, EM, 0), -4);
        assert_eq!(service.create("中".repeat(171).as_bytes(), FONT, EM, 0), -4);
        assert_eq!(service.live_count(), 0);
        assert_eq!(service.create("中文".as_bytes(), FONT, EM, 0), FIRST_HANDLE);
        let empty = service.create(b"", FONT, EM, 0);
        assert_eq!(empty, FIRST_HANDLE + 1);
        assert_eq!(service.metrics(empty).unwrap()[9], 0);
    }

    #[test]
    fn invalid_font_flags_and_size_do_not_consume_a_handle() {
        let mut service = TextService::new().unwrap();
        for font in [i32::MIN, -1, 0, 2, i32::MAX] {
            assert_eq!(service.create(b"A", font, EM, 0), -2);
        }
        for flags in [i32::MIN, -1, 2, 3, i32::MAX] {
            assert_eq!(service.create(b"A", FONT, EM, flags), -2);
        }
        for em in [i32::MIN, -1, 0, 8 * 64 - 16, EM + 1, 96 * 64 + 16, i32::MAX] {
            assert_eq!(service.create(b"A", FONT, em, 0), -2);
        }
        assert_eq!(service.create(b"A", FONT, 8 * 64, 0), FIRST_HANDLE);
        assert_eq!(service.create(b"A", FONT, 96 * 64, 1), FIRST_HANDLE + 1);
        assert_eq!(service.create(b"A", FONT, EM + 16, 0), FIRST_HANDLE + 2);
    }

    #[test]
    fn unsupported_text_and_missing_glyph_errors_keep_their_distinct_codes() {
        let mut service = TextService::new().unwrap();
        for text in [
            "\n", "\t", "\u{0000}", "\u{0085}", "\u{202e}", "\u{2066}", "אב",
        ] {
            assert_eq!(service.create(text.as_bytes(), FONT, EM, 0), -6, "{text:?}");
        }
        let missing = "\u{10ffff}".as_bytes();
        assert_eq!(service.create(missing, FONT, EM, 0), -5);
        assert_eq!(service.live_count(), 0);
        let handle = service.create(missing, FONT, EM, 1);
        assert_eq!(handle, FIRST_HANDLE);
        assert_eq!(service.metrics(handle).unwrap()[10] & 1, 1);
    }

    #[test]
    fn metrics_are_the_layouts_twelve_i32_values_and_serialize_to_48_bytes() {
        let mut service = TextService::new().unwrap();
        let handle = service.create("MingOS 中文 AV fi é A\u{301}".as_bytes(), FONT, EM, 0);
        assert!(handle > 0);
        let layout = service.get(handle).unwrap();
        let metrics = service.metrics(handle).unwrap();
        assert_eq!(metrics, layout.metrics);
        assert_eq!(metrics[4], metrics[1] + metrics[2] + metrics[3]);
        assert_eq!(metrics[11], 1);
        let bytes: Vec<_> = metrics
            .iter()
            .flat_map(|value| value.to_le_bytes())
            .collect();
        assert_eq!(bytes.len(), 48);
        for (value, bytes) in metrics.iter().zip(bytes.as_chunks::<4>().0.iter()) {
            assert_eq!(*value, i32::from_le_bytes(*bytes));
        }
        assert!(Arc::ptr_eq(&layout, &service.get(handle).unwrap()));
        let masks = service.engine_mut().raster_layout(&layout, 0, 0).unwrap();
        assert!(!masks.is_empty());
        assert_eq!(service.metrics(handle), Some(metrics));
    }

    #[test]
    fn closed_external_arcs_stay_charged_and_rasterizable_until_released() {
        let mut service = TextService::new().unwrap();
        let handle = service.create(b"retained", FONT, EM, 0);
        let retained = service.get(handle).unwrap();
        let before_close = service.retained_bytes();
        assert!(service.close(handle));
        assert_eq!(service.live_count(), 0);
        assert!(service.get(handle).is_none());
        assert!(service.metrics(handle).is_none());
        assert_eq!(service.retained_bytes(), before_close);
        assert_eq!(service.entries.len(), 1);
        assert!(
            !service
                .engine_mut()
                .raster_layout(&retained, 0, 0)
                .unwrap()
                .is_empty()
        );
        let charged = retained.retained_bytes();
        drop(retained);
        assert!(!service.close(handle)); // Mutating operations sweep released retired entries.
        assert!(service.entries.is_empty());
        assert!(service.retained_bytes() <= before_close - charged);
        assert_eq!(service.create(b"next", FONT, EM, 0), handle + 1);
    }

    #[test]
    fn retained_original_utf8_includes_closed_layouts_and_recovers_after_release() {
        let mut service = TextService::new().unwrap();
        // Each A + combining acute shapes as one glyph, keeping metadata below
        // 2 MiB while exercising all 32 KiB of original, unnormalized UTF-8.
        let text = format!("{}  ", "A\u{301}".repeat(170));
        assert_eq!(text.len(), 512);
        let mut retained = Vec::new();
        for index in 0..64 {
            let handle = service.create(text.as_bytes(), FONT, EM, 0);
            assert_eq!(handle, FIRST_HANDLE + index);
            retained.push(service.get(handle).unwrap());
            assert!(service.close(handle));
        }
        assert_eq!(service.live_count(), 0);
        assert_eq!(service.create(b"A", FONT, EM, 0), -4);
        assert_eq!(service.create(b"", FONT, EM, 0), FIRST_HANDLE + 64);
        assert!(service.retained_bytes() <= RETAINED_BYTES_LIMIT);
        retained.pop();
        assert_eq!(service.create(b"A", FONT, EM, 0), FIRST_HANDLE + 65);
        assert_eq!(service.entries.len(), 65);
    }

    #[test]
    fn retained_metadata_budget_rejects_growth_and_reclaims_released_layouts() {
        let mut service = TextService::new().unwrap();
        let anchor = service.create(b"A", FONT, EM, 0);
        let anchor_metrics = service.metrics(anchor).unwrap();
        let mut retained = Vec::new();
        let rejected = loop {
            let handle = service.create(b"", FONT, EM, 0);
            if handle == -4 {
                break retained.len();
            }
            assert_eq!(handle, anchor + 1 + retained.len() as i32);
            retained.push(service.get(handle).unwrap());
            assert!(service.close(handle));
            assert_eq!(service.live_count(), 1);
            assert!(service.retained_bytes() <= RETAINED_BYTES_LIMIT);
            assert!(retained.len() < RETAINED_BYTES_LIMIT / ENTRY_OVERHEAD);
        };
        assert!(rejected > 64, "retired entries must not count as live");
        assert_eq!(service.metrics(anchor), Some(anchor_metrics));
        let before_release = service.retained_bytes();
        drop(retained);
        let next = service.create(b"", FONT, EM, 0);
        assert_eq!(next, anchor + 1 + rejected as i32);
        assert!(service.retained_bytes() < before_release);
        assert_eq!(service.metrics(anchor), Some(anchor_metrics));
        assert_eq!(service.live_count(), 2);
    }

    #[test]
    fn handle_window_exhaustion_does_not_wrap_or_reuse_ids() {
        let mut service = TextService::new().unwrap();
        service.next_handle = LAST_HANDLE;
        assert_eq!(service.create(b"", FONT, EM, 0), LAST_HANDLE);
        assert_eq!(service.create(b"", FONT, EM, 0), -4);
        assert!(service.close(LAST_HANDLE));
        assert_eq!(service.create(b"", FONT, EM, 0), -4);
        assert_eq!(service.live_count(), 0);
    }

    #[test]
    fn metadata_accounting_includes_table_capacity_layouts_and_engine_cache() {
        let mut service = TextService::new().unwrap();
        let baseline = service.retained_bytes();
        let handle = service.create(b"A", FONT, EM, 0);
        let layout = service.get(handle).unwrap();
        let expected = std::mem::size_of::<TextService>()
            + service.entries.capacity() * ENTRY_OVERHEAD
            + layout.retained_bytes()
            + service.engine.retained_metadata_bytes();
        assert_eq!(service.retained_bytes(), expected);
        assert!(expected > baseline + layout.retained_bytes());
        assert!(TextService::owned_metadata_bytes(usize::MAX, 0).is_none());
        assert!(TextService::owned_metadata_bytes(0, usize::MAX).is_none());
    }

    fn pad_layout_to_leave(service: &mut TextService, handle: i32, free_bytes: usize) {
        // Controlled capacity pressure without changing the original input or
        // immutable shaping result; only this test has mutable access to it.
        let extra = RETAINED_BYTES_LIMIT - service.retained_bytes() - free_bytes;
        let layout = Arc::get_mut(&mut service.entries.get_mut(&handle).unwrap().layout).unwrap();
        let target_capacity = layout.text.capacity() + extra;
        layout
            .text
            .reserve_exact(target_capacity - layout.text.len());
        assert!(service.retained_bytes() <= RETAINED_BYTES_LIMIT);
    }

    #[test]
    fn frame_rasterization_uses_remaining_layout_metadata_budget() {
        let mut service = TextService::new().unwrap();
        let handle = service.create("MingOS 中文，标点。AV fi é".as_bytes(), FONT, EM, 0);
        assert!(handle > 0);
        let layout_bytes = service.entry_totals().unwrap().0;
        let owned =
            TextService::owned_metadata_bytes(service.entries.capacity(), layout_bytes).unwrap();
        let base = TextEngine::new().unwrap().retained_metadata_bytes();
        service.engine.trim_metadata(base).unwrap();
        let free = RETAINED_BYTES_LIMIT - owned - base;
        pad_layout_to_leave(&mut service, handle, 1024.min(free));
        let layout = service.get(handle).unwrap();
        assert!(service.raster_layout(&layout, 0, 0).is_err());
        assert!(service.retained_bytes() <= RETAINED_BYTES_LIMIT);
        drop(layout);
        assert!(service.close(handle));
        let next = service.create(b"A", FONT, EM, 0);
        assert!(next > handle);
        let layout = service.get(next).unwrap();
        assert!(!service.raster_layout(&layout, 0, 0).unwrap().is_empty());
    }

    #[test]
    fn creation_trims_unpinned_metadata_before_publishing_a_layout() {
        let mut service = TextService::new().unwrap();
        let handle = service.create(b"A", FONT, EM, 0);
        let metrics = service.metrics(handle).unwrap();
        let empty_charge = service
            .engine_mut()
            .layout("", FONT, EM, 0)
            .unwrap()
            .retained_bytes();
        let before_metadata = service.engine.retained_metadata_bytes();
        pad_layout_to_leave(&mut service, handle, empty_charge / 2);
        assert_eq!(service.create(b"", FONT, EM, 0), handle + 1);
        assert!(service.engine.retained_metadata_bytes() < before_metadata);
        assert!(service.retained_bytes() <= RETAINED_BYTES_LIMIT);
        assert_eq!(service.metrics(handle), Some(metrics));
    }

    #[test]
    fn pinned_metadata_failure_preserves_existing_layout_and_the_next_id() {
        let mut service = TextService::new().unwrap();
        let base_metadata = service.engine.retained_metadata_bytes();
        let handle = service.create(b"A", FONT, EM, 0);
        let layout = service.get(handle).unwrap();
        let metrics = layout.metrics;
        let masks = service.engine_mut().raster_layout(&layout, 0, 0).unwrap();
        assert!(!masks.is_empty());
        drop(layout);
        assert!(service.engine_mut().trim_metadata(base_metadata).is_err());
        let empty_charge = service
            .engine_mut()
            .layout("", FONT, EM, 0)
            .unwrap()
            .retained_bytes();
        pad_layout_to_leave(&mut service, handle, empty_charge / 2);
        let before = service.retained_bytes();
        assert_eq!(service.create(b"", FONT, EM, 0), -4);
        assert_eq!(service.retained_bytes(), before);
        assert_eq!(service.live_count(), 1);
        assert_eq!(service.metrics(handle), Some(metrics));
        drop(masks);
        assert_eq!(service.create(b"", FONT, EM, 0), handle + 1);
        assert!(service.retained_bytes() <= RETAINED_BYTES_LIMIT);
        let layout = service.get(handle).unwrap();
        // The padded layout still occupies the budget: admitting an empty
        // layout does not promise room for newly rasterized mask records.
        assert!(service.raster_layout(&layout, 0, 0).is_err());
        assert!(service.retained_bytes() <= RETAINED_BYTES_LIMIT);
        drop(layout);
        assert!(service.close(handle));
        let fresh = service.create(b"A", FONT, EM, 0);
        assert!(fresh > handle);
        let layout = service.get(fresh).unwrap();
        assert!(!service.raster_layout(&layout, 0, 0).unwrap().is_empty());
    }

    #[test]
    fn engine_error_mapping_covers_wrapped_errors_and_all_budget_families() {
        for (message, expected) in [
            ("TEXT_MISSING_GLYPH", -5),
            ("TEXT_UNSUPPORTED_CONTROL", -6),
            ("TEXT_UNSUPPORTED_DIRECTION", -6),
            ("TEXT_UNSUPPORTED_MASK", -6),
            ("TEXT_LIMIT", -4),
            ("TEXT_GLYPH_LIMIT", -4),
            ("TEXT_MASK_LIMIT", -4),
            ("TEXT_CACHE_LIMIT", -4),
            ("TEXT_METADATA_LIMIT", -4),
            ("TEXT_INVALID_STYLE", -2),
            ("TEXT_EXTERNAL_FACE", -2),
            ("FONT_PARSE_FAILED", -2),
        ] {
            let error = anyhow::anyhow!(message).context("creating text layout");
            assert_eq!(engine_error_code(&error), expected, "{message}");
        }
    }
}
