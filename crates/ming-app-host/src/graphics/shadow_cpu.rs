//! CPU shadow cache. Admission and output rendering precede the metadata commit.
use super::{
    Frame, Image, RenderedFrame, raster,
    shadow_cache::{CacheLayout, SLOT_WORDS},
    shadow_frame::PreparedShadows,
    shadow_mask::{ShadowKey, generate_mask},
};
use anyhow::{Context, Result};
use std::{collections::BTreeMap, sync::Arc};
pub(crate) type Masks = Vec<(ShadowKey, Arc<Vec<u8>>)>;
#[derive(Default)]
pub(crate) struct CpuCache {
    layout: CacheLayout,
    masks: [Option<Arc<Vec<u8>>>; 8],
}
impl CpuCache {
    pub fn render(
        &mut self,
        frame: &Frame,
        images: &BTreeMap<i32, Image>,
        sequence: u64,
        #[cfg(feature = "text")] text: Option<&crate::text::frame::PreparedTextFrame>,
        shadows: &PreparedShadows,
        cold_budget: usize,
    ) -> Result<RenderedFrame> {
        let plan = self.layout.plan(&shadows.keys, cold_budget)?;
        debug_assert_eq!(plan.hits + plan.misses.len(), shadows.keys.len());
        debug_assert!(plan.evictions <= plan.misses.len());
        debug_assert!(plan.cold_work <= cold_budget);
        debug_assert!(plan.next.resident_bytes() <= 2 * 1024 * 1024 && plan.next.len() <= 8);
        let mut next_masks = self.masks.clone();
        for &(key, offset) in &plan.misses {
            next_masks[(offset / SLOT_WORDS) as usize] = Some(Arc::new(generate_mask(key)));
        }
        let masks: Masks = plan
            .slots
            .iter()
            .map(|&(key, offset)| {
                Ok((
                    key,
                    next_masks[(offset / SLOT_WORDS) as usize]
                        .as_ref()
                        .context("SHADOW_CACHE_STATE")?
                        .clone(),
                ))
            })
            .collect::<Result<_>>()?;
        let rendered = raster::render_prepared(
            frame,
            images,
            sequence,
            #[cfg(feature = "text")]
            text,
            shadows,
            &masks,
        )?;
        self.layout = plan.next;
        self.masks = next_masks;
        Ok(rendered)
    }
}
