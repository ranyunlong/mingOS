use super::{PositionedMask, TextService};
use crate::graphics::Frame;
use anyhow::{Context, Result, bail};
use std::collections::{BTreeMap, BTreeSet};
#[derive(Default)]
pub(crate) struct PreparedTextFrame {
    pub draws: BTreeMap<usize, PreparedTextDraw>,
    pub work: usize,
}
pub(crate) struct PreparedTextDraw {
    pub masks: Vec<PositionedMask>,
    pub bins: super::bins::GlyphBins,
}
impl PreparedTextFrame {
    pub fn prepare(frame: &Frame, service: &mut TextService) -> Result<Self> {
        let mut retained = BTreeMap::new();
        let mut unique = BTreeSet::new();
        let (mut bytes, mut glyphs) = (0usize, 0usize);
        for (index, command) in frame.commands.iter().enumerate() {
            if command.op == 5 {
                let id = command.a[0];
                let layout = service.get(id).context("TEXT_HANDLE_INVALID")?;
                if unique.insert(id) {
                    bytes = bytes
                        .checked_add(layout.input_bytes())
                        .context("TEXT_LIMIT")?;
                }
                glyphs = glyphs
                    .checked_add(layout.metrics[9] as usize)
                    .context("TEXT_LIMIT")?;
                if bytes > 4096 || glyphs > 2048 {
                    bail!("TEXT_FRAME_LIMIT");
                }
                retained.insert(index, layout);
            }
        }
        let mut result = Self::default();
        let (mut references, mut staging) = (0usize, 0usize);
        let mut clip = [0, 0, frame.width as i32, frame.height as i32];
        for (index, command) in frame.commands.iter().enumerate() {
            let a = command.a;
            if command.op == 4 {
                clip = [a[0], a[1], a[0] + a[2], a[1] + a[3]];
            }
            if command.op != 5 {
                continue;
            }
            if clip[2].min(frame.width as i32) <= clip[0].max(0)
                || clip[3].min(frame.height as i32) <= clip[1].max(0)
            {
                result.draws.insert(
                    index,
                    PreparedTextDraw {
                        masks: Vec::new(),
                        bins: Default::default(),
                    },
                );
                continue;
            }
            let layout = &retained[&index];
            let mut masks = service.raster_layout(layout, a[1], a[2])?;
            for mask in &masks {
                let x0 = mask.left.max(clip[0]).max(0).min(frame.width as i32);
                let y0 = mask.top.max(clip[1]).max(0).min(frame.height as i32);
                let x1 = (mask.left + mask.width as i32)
                    .min(clip[2])
                    .min(frame.width as i32)
                    .max(x0);
                let y1 = (mask.top + mask.height as i32)
                    .min(clip[3])
                    .min(frame.height as i32)
                    .max(y0);
                result.work = result
                    .work
                    .checked_add((x1 - x0) as usize * (y1 - y0) as usize)
                    .context("TEXT_WORK_LIMIT")?;
                if result.work > 32_000_000 {
                    bail!("TEXT_WORK_LIMIT");
                }
            }
            masks.retain(|mask| {
                mask.left < clip[2].min(frame.width as i32)
                    && mask.top < clip[3].min(frame.height as i32)
                    && mask.left + mask.width as i32 > clip[0].max(0)
                    && mask.top + mask.height as i32 > clip[1].max(0)
            });
            let rects: Vec<_> = masks
                .iter()
                .enumerate()
                .map(|(i, m)| super::bins::GlyphRect {
                    glyph_index: i as u32,
                    x: m.left,
                    y: m.top,
                    width: m.width,
                    height: m.height,
                })
                .collect();
            let bins = super::bins::build_bins(&rects, clip, frame.width, frame.height)?;
            references = references
                .checked_add(bins.indices.len())
                .context("TEXT_TILE_REFERENCE_LIMIT")?;
            staging = staging
                .checked_add(bins.storage_bytes())
                .and_then(|n| {
                    n.checked_add(masks.capacity() * std::mem::size_of::<PositionedMask>())
                })
                .context("TEXT_STAGING_LIMIT")?;
            if references > 65536 || staging > 2 * 1024 * 1024 {
                bail!("TEXT_FRAME_STAGING_LIMIT");
            }
            result.draws.insert(index, PreparedTextDraw { masks, bins });
        }
        Ok(result)
    }
}
