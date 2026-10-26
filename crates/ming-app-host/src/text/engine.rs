use anyhow::{Context, Result, bail};
use cosmic_text::{
    Attrs, Buffer, CacheKey, Family, FontSystem, LayoutGlyph, Metrics, Shaping, SwashCache,
    SwashContent, Weight, Wrap, fontdb,
};
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};

const FONT: &[u8] = include_bytes!("../../../../assets/fonts/NotoSansSC-Regular.otf");
const MASK_BYTES: usize = 4 * 1024 * 1024;
const MASK_ENTRIES: usize = 4096;
static OWNERS: AtomicU64 = AtomicU64::new(1);
#[derive(Clone, Debug)]
pub struct ShapedLayout {
    pub metrics: [i32; 12],
    pub(super) glyphs: Vec<LayoutGlyph>,
    pub(super) text: String,
    owner: u64,
}
impl ShapedLayout {
    pub fn retained_bytes(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.text.capacity()
            + self.glyphs.capacity() * std::mem::size_of::<LayoutGlyph>()
    }
    pub fn input_bytes(&self) -> usize {
        self.text.len()
    }
}
#[derive(Clone, Debug)]
pub struct PositionedMask {
    pub key: CacheKey,
    pub left: i32,
    pub top: i32,
    pub width: usize,
    pub height: usize,
    pub data: Arc<Vec<u8>>,
}
#[derive(Clone, Debug, Default)]
pub struct TextCacheStats {
    pub mask_bytes: usize,
    pub entries: usize,
    pub hits: u64,
    pub misses: u64,
}
#[derive(Clone)]
struct Mask {
    left: i32,
    top: i32,
    width: usize,
    height: usize,
    data: Arc<Vec<u8>>,
}
struct CachedMask {
    mask: Arc<Mask>,
    used: u64,
}
pub struct TextEngine {
    system: FontSystem,
    swash: SwashCache,
    face: fontdb::ID,
    family: String,
    owner: u64,
    masks: HashMap<CacheKey, CachedMask>,
    stats: TextCacheStats,
    tick: u64,
    metadata_limit: usize,
}
impl TextEngine {
    pub fn new() -> Result<Self> {
        if FONT.len() != 8_331_336 || &FONT[..4] != b"OTTO" {
            bail!("FONT_PACK_INVALID");
        }
        let mut db = fontdb::Database::new();
        db.load_font_data(FONT.to_vec());
        if db.faces().count() != 1 {
            bail!("FONT_FACE_COUNT");
        }
        let face = db.faces().next().context("FONT_FACE_MISSING")?;
        let id = face.id;
        let family = face
            .families
            .first()
            .context("FONT_FAMILY_MISSING")?
            .0
            .clone();
        if family != "Noto Sans SC" {
            bail!("FONT_FAMILY_UNEXPECTED: {family}");
        }
        let mut system = FontSystem::new_with_locale_and_db("zh-CN".into(), db);
        system
            .get_font(id, Weight::NORMAL)
            .context("FONT_PARSE_FAILED")?;
        Ok(Self {
            system,
            swash: SwashCache::new(),
            face: id,
            family,
            owner: OWNERS.fetch_add(1, Ordering::Relaxed),
            masks: HashMap::new(),
            stats: TextCacheStats::default(),
            tick: 0,
            metadata_limit: 2 * 1024 * 1024,
        })
    }
    pub fn face_count(&self) -> usize {
        self.system.db().faces().count()
    }
    pub fn retained_metadata_bytes(&self) -> usize {
        self.metadata_bytes_for(self.masks.capacity(), self.masks.len())
    }
    fn metadata_bytes_for(&self, capacity: usize, entries: usize) -> usize {
        // HashMap capacity counts usable slots. Charge twice that capacity to
        // cover spare buckets, control bytes and trailing SIMD/alignment bytes.
        let table = if capacity == 0 {
            0
        } else {
            capacity
                .saturating_mul(2)
                .saturating_mul(std::mem::size_of::<(CacheKey, CachedMask)>() + 1)
                .saturating_add(64)
        };
        std::mem::size_of::<Self>()
            .saturating_add(self.family.capacity())
            .saturating_add(table)
            .saturating_add(entries.saturating_mul(
                std::mem::size_of::<Mask>()
                    + std::mem::size_of::<Vec<u8>>()
                    + 4 * std::mem::size_of::<usize>(),
            ))
    }
    fn evict_unpinned(&mut self) -> Result<()> {
        let victim = self
            .masks
            .iter()
            .filter(|(_, e)| {
                Arc::strong_count(&e.mask) == 1 && Arc::strong_count(&e.mask.data) == 1
            })
            .min_by_key(|(_, e)| e.used)
            .map(|(k, _)| *k)
            .context("TEXT_CACHE_LIMIT")?;
        let removed = self.masks.remove(&victim).expect("cache victim");
        self.stats.mask_bytes -= removed.mask.data.capacity();
        if self.masks.len() < self.masks.capacity() / 2 || self.masks.is_empty() {
            self.masks.shrink_to_fit();
        }
        Ok(())
    }
    pub fn trim_metadata(&mut self, limit: usize) -> Result<()> {
        while self.retained_metadata_bytes() > limit {
            self.evict_unpinned().context("TEXT_METADATA_LIMIT")?;
        }
        // Also constrain future inserts, including frame-time rasterization.
        self.metadata_limit = limit;
        Ok(())
    }
    pub fn cache_stats(&self) -> TextCacheStats {
        let mut s = self.stats.clone();
        s.entries = self.masks.len();
        s
    }
    pub fn layout(
        &mut self,
        text: &str,
        font: i32,
        em_q6: i32,
        flags: i32,
    ) -> Result<ShapedLayout> {
        if font != 1 || !(8 * 64..=96 * 64).contains(&em_q6) || em_q6 % 16 != 0 || flags & !1 != 0 {
            bail!("TEXT_INVALID_STYLE");
        }
        if text.len() > 512 {
            bail!("TEXT_LIMIT");
        }
        if text.chars().any(|c|c.is_control()||matches!(c,'\u{061c}'|'\u{200e}'|'\u{200f}'|'\u{2028}'..='\u{202e}'|'\u{2066}'..='\u{2069}')){bail!("TEXT_UNSUPPORTED_CONTROL");}
        let em = em_q6 as f32 / 64.;
        let font = self
            .system
            .get_font(self.face, Weight::NORMAL)
            .context("FONT_FACE_MISSING")?;
        let face_metrics = font.as_swash().metrics(&[]).scale(em);
        let asc = (face_metrics.ascent.max(0.) * 64.).round() as i32;
        let desc = (face_metrics.descent.abs() * 64.).round() as i32;
        let gap = (face_metrics.leading.max(0.) * 64.).round() as i32;
        let height = asc + desc + gap;
        let mut buffer = Buffer::new(
            &mut self.system,
            Metrics::new(em, (height as f32 / 64.).max(em)),
        );
        buffer.set_wrap(Wrap::None);
        buffer.set_size(None, None);
        let attrs = Attrs::new()
            .family(Family::Name(&self.family))
            .weight(Weight::NORMAL);
        buffer.set_text(text, &attrs, Shaping::Advanced, None);
        buffer.shape_until_scroll(&mut self.system, true);
        let mut glyphs = Vec::new();
        let mut advance = 0f32;
        for run in buffer.layout_runs() {
            if run.line_i != 0 || run.rtl || run.glyphs.iter().any(|g| g.level.is_rtl()) {
                bail!("TEXT_UNSUPPORTED_DIRECTION");
            }
            advance = advance.max(run.line_w);
            for glyph in run.glyphs {
                if glyph.font_id != self.face {
                    bail!("TEXT_EXTERNAL_FACE");
                }
                advance = advance.max(glyph.x + glyph.w);
                glyphs.push(glyph.clone());
            }
        }
        if glyphs.len() > 512 {
            bail!("TEXT_GLYPH_LIMIT");
        }
        let missing = glyphs.iter().any(|g| g.glyph_id == 0);
        if missing && flags & 1 == 0 {
            bail!("TEXT_MISSING_GLYPH");
        }
        let mut bounds: Option<[i32; 4]> = None;
        // Y is hinted to integers by the pinned engine, so coverage depends on
        // X phase only. Separate phase envelopes cover all 64x64 Q6 positions
        // with 128 samples/glyph instead of rasterizing a Cartesian product.
        for glyph in &glyphs {
            let base = glyph.physical((0., 0.), 1.);
            let (mut ymin, mut ymax) = (i32::MAX, i32::MIN);
            for y in 0..64 {
                let p = glyph.physical((0., y as f32 / 64.), 1.);
                if p.cache_key != base.cache_key {
                    bail!("TEXT_Y_QUANTIZATION_CHANGED");
                }
                let value = p.y * 64 - y;
                ymin = ymin.min(value);
                ymax = ymax.max(value);
            }
            for x in 0..64 {
                let p = glyph.physical((x as f32 / 64., 0.), 1.);
                let mask = self.mask(p.cache_key)?;
                if mask.width == 0 || mask.height == 0 {
                    continue;
                }
                let l = (p.x + mask.left) * 64 - x;
                let b = [
                    l,
                    ymin - mask.top * 64,
                    l + mask.width as i32 * 64,
                    ymax + (mask.height as i32 - mask.top) * 64,
                ];
                bounds = Some(bounds.map_or(b, |old| {
                    [
                        old[0].min(b[0]),
                        old[1].min(b[1]),
                        old[2].max(b[2]),
                        old[3].max(b[3]),
                    ]
                }));
            }
        }
        let b = bounds.unwrap_or([0; 4]);
        let metrics = [
            (advance * 64.).round() as i32,
            asc,
            desc,
            gap,
            height,
            b[0],
            b[1],
            b[2],
            b[3],
            glyphs.len() as i32,
            i32::from(missing),
            1,
        ];
        Ok(ShapedLayout {
            metrics,
            glyphs,
            text: text.into(),
            owner: self.owner,
        })
    }
    pub fn raster_layout(
        &mut self,
        layout: &ShapedLayout,
        x_q6: i32,
        baseline_q6: i32,
    ) -> Result<Vec<PositionedMask>> {
        if layout.owner != self.owner {
            bail!("TEXT_FOREIGN_LAYOUT");
        }
        if !(-64_000_000..=64_000_000).contains(&x_q6)
            || !(-64_000_000..=64_000_000).contains(&baseline_q6)
        {
            bail!("TEXT_INVALID_COORDINATE");
        }
        let mut masks = Vec::new();
        for glyph in &layout.glyphs {
            let p = glyph.physical(
                (
                    x_q6.rem_euclid(64) as f32 / 64.,
                    baseline_q6.rem_euclid(64) as f32 / 64.,
                ),
                1.,
            );
            let mask = self.mask(p.cache_key)?;
            if mask.width == 0 || mask.height == 0 {
                continue;
            }
            masks.push(PositionedMask {
                key: p.cache_key,
                left: x_q6.div_euclid(64) + p.x + mask.left,
                top: baseline_q6.div_euclid(64) + p.y - mask.top,
                width: mask.width,
                height: mask.height,
                data: mask.data.clone(),
            });
        }
        Ok(masks)
    }
    fn mask(&mut self, key: CacheKey) -> Result<Arc<Mask>> {
        self.tick += 1;
        if let Some(cached) = self.masks.get_mut(&key) {
            cached.used = self.tick;
            self.stats.hits += 1;
            return Ok(cached.mask.clone());
        }
        self.stats.misses += 1;
        let image = self.swash.get_image_uncached(&mut self.system, key);
        let mask = if let Some(image) = image {
            if !matches!(image.content, SwashContent::Mask) {
                bail!("TEXT_UNSUPPORTED_MASK");
            }
            let width = image.placement.width as usize;
            let height = image.placement.height as usize;
            if width > 256 || height > 256 || width.checked_mul(height) != Some(image.data.len()) {
                bail!("TEXT_MASK_LIMIT");
            }
            Mask {
                left: image.placement.left,
                top: image.placement.top,
                width,
                height,
                data: Arc::new(image.data),
            }
        } else {
            Mask {
                left: 0,
                top: 0,
                width: 0,
                height: 0,
                data: Arc::new(Vec::new()),
            }
        };
        loop {
            let mut grown = None;
            let capacity = if self.masks.len() == self.masks.capacity() {
                let mut table = HashMap::<CacheKey, CachedMask>::new();
                table
                    .try_reserve(self.masks.len() + 1)
                    .context("TEXT_CACHE_LIMIT")?;
                let capacity = table.capacity();
                grown = Some(table);
                capacity
            } else {
                self.masks.capacity()
            };
            if self.masks.len() < MASK_ENTRIES
                && self.stats.mask_bytes.saturating_add(mask.data.capacity()) <= MASK_BYTES
                && self.metadata_bytes_for(capacity, self.masks.len() + 1) <= self.metadata_limit
            {
                if let Some(mut table) = grown {
                    table.extend(self.masks.drain());
                    self.masks = table;
                }
                break;
            }
            // No externally held coverage can be evicted to admit another mask.
            self.evict_unpinned()?;
        }
        self.stats.mask_bytes += mask.data.capacity();
        let mask = Arc::new(mask);
        self.masks.insert(
            key,
            CachedMask {
                mask: mask.clone(),
                used: self.tick,
            },
        );
        Ok(mask)
    }
}

#[cfg(test)]
mod metadata_tests {
    use super::*;
    #[test]
    fn table_charge_covers_bucket_rounding_and_control_bytes() {
        let engine = TextEngine::new().unwrap();
        let base = engine.retained_metadata_bytes();
        for requested in [1usize, 2, 3, 4, 8, 16, 100, 1000, 4096] {
            let map = HashMap::<CacheKey, CachedMask>::with_capacity(requested);
            let capacity = map.capacity();
            let buckets = if capacity <= 3 {
                4
            } else if capacity <= 7 {
                8
            } else {
                (capacity * 8 / 7).next_power_of_two()
            };
            let actual_lower_bound =
                buckets * (std::mem::size_of::<(CacheKey, CachedMask)>() + 1) + 16;
            assert!(engine.metadata_bytes_for(capacity, 0) - base >= actual_lower_bound);
        }
    }
}
