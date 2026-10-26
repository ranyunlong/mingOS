use super::{Frame, Image, RenderedFrame, font};
use anyhow::Result;
use std::{collections::BTreeMap, sync::Arc, time::Instant};
fn blend(dst: u32, src: u32) -> u32 {
    let a = (src >> 24) & 255;
    if a == 255 {
        return src;
    }
    if a == 0 {
        return dst;
    }
    let mut out = 0xff000000;
    for shift in [0, 8, 16] {
        out |= ((((src >> shift) & 255) * a + ((dst >> shift) & 255) * (255 - a) + 127) / 255)
            << shift;
    }
    out
}
fn bounds(x: i32, y: i32, w: i32, h: i32, clip: [i32; 4], fw: usize, fh: usize) -> [usize; 4] {
    let x0 = x.max(clip[0]).max(0).min(fw as i32);
    let y0 = y.max(clip[1]).max(0).min(fh as i32);
    let x1 = (x + w).min(clip[2]).min(fw as i32).max(x0);
    let y1 = (y + h).min(clip[3]).min(fh as i32).max(y0);
    [x0 as usize, y0 as usize, x1 as usize, y1 as usize]
}
pub fn render(
    frame: &Frame,
    images: &BTreeMap<i32, Image>,
    sequence: u64,
) -> Result<RenderedFrame> {
    render_inner(
        frame,
        images,
        sequence,
        #[cfg(feature = "text")]
        None,
        None,
    )
}
#[cfg(feature = "text")]
pub fn render_with_text(
    frame: &Frame,
    images: &BTreeMap<i32, Image>,
    sequence: u64,
    text: &crate::text::frame::PreparedTextFrame,
) -> Result<RenderedFrame> {
    render_inner(frame, images, sequence, Some(text), None)
}
pub(crate) fn render_prepared(
    frame: &Frame,
    images: &BTreeMap<i32, Image>,
    sequence: u64,
    #[cfg(feature = "text")] text: Option<&crate::text::frame::PreparedTextFrame>,
    shadows: &super::shadow_frame::PreparedShadows,
    masks: &super::shadow_cpu::Masks,
) -> Result<RenderedFrame> {
    render_inner(
        frame,
        images,
        sequence,
        #[cfg(feature = "text")]
        text,
        Some((shadows, masks)),
    )
}
fn render_inner(
    frame: &Frame,
    images: &BTreeMap<i32, Image>,
    sequence: u64,
    #[cfg(feature = "text")] text: Option<&crate::text::frame::PreparedTextFrame>,
    shadows: Option<(
        &super::shadow_frame::PreparedShadows,
        &super::shadow_cpu::Masks,
    )>,
) -> Result<RenderedFrame> {
    let start = Instant::now();
    super::validation::validate(frame, images)?;
    let mut pixels = vec![0xff000000; frame.width * frame.height];
    let mut clip = [0, 0, frame.width as i32, frame.height as i32];
    for (command_index, c) in frame.commands.iter().enumerate() {
        let a = c.a;
        match c.op {
            4 => clip = [a[0], a[1], a[0] + a[2], a[1] + a[3]],
            1 => {
                let b = bounds(a[0], a[1], a[2], a[3], clip, frame.width, frame.height);
                let radius = a[5].min(a[2] / 2).min(a[3] / 2);
                for y in b[1]..b[3] {
                    for x in b[0]..b[2] {
                        let lx = x as i32 - a[0];
                        let ly = y as i32 - a[1];
                        let dx = (radius - lx - 1).max(lx - (a[2] - radius)).max(0);
                        let dy = (radius - ly - 1).max(ly - (a[3] - radius)).max(0);
                        if dx * dx + dy * dy <= radius * radius {
                            let i = y * frame.width + x;
                            pixels[i] = blend(pixels[i], a[4] as u32);
                        }
                    }
                }
            }
            6 => {
                let b = bounds(a[0], a[1], a[2], a[3], clip, frame.width, frame.height);
                for y in b[1]..b[3] {
                    for x in b[0]..b[2] {
                        let coverage =
                            super::aa::coverage(x as i32 - a[0], y as i32 - a[1], a[2], a[3], a[5]);
                        let color = a[4] as u32;
                        let alpha = (coverage * (color >> 24) + 127) / 255;
                        let i = y * frame.width + x;
                        pixels[i] = blend(pixels[i], (color & 0x00ffffff) | (alpha << 24));
                    }
                }
            }
            7 => {
                let (prepared, masks) =
                    shadows.ok_or_else(|| anyhow::anyhow!("SHADOW_CACHE_STATE"))?;
                let draw = prepared
                    .draws
                    .get(&command_index)
                    .ok_or_else(|| anyhow::anyhow!("SHADOW_CACHE_STATE"))?;
                if !draw.visible {
                    continue;
                }
                let (_, mask) = masks
                    .iter()
                    .find(|(key, _)| *key == draw.key)
                    .ok_or_else(|| anyhow::anyhow!("SHADOW_CACHE_STATE"))?;
                let halo = draw.key.blur();
                let b = bounds(
                    a[1] - halo,
                    a[2] - halo,
                    a[3] + 2 * halo,
                    a[4] + 2 * halo,
                    clip,
                    frame.width,
                    frame.height,
                );
                for y in b[1]..b[3] {
                    for x in b[0]..b[2] {
                        let coverage = super::shadow_mask::sample(
                            draw.key,
                            mask,
                            a[3],
                            a[4],
                            x as i32 - a[1],
                            y as i32 - a[2],
                        );
                        let color = a[5] as u32;
                        let alpha = (coverage * (color >> 24) + 127) / 255;
                        let i = y * frame.width + x;
                        pixels[i] = blend(pixels[i], (color & 0x00ffffff) | (alpha << 24));
                    }
                }
            }
            2 => {
                let image = &images[&a[0]];
                let b = bounds(a[1], a[2], a[3], a[4], clip, frame.width, frame.height);
                for y in b[1]..b[3] {
                    let sy =
                        ((y as i64 - a[2] as i64) * image.height as i64 / a[4] as i64) as usize;
                    for x in b[0]..b[2] {
                        let sx =
                            ((x as i64 - a[1] as i64) * image.width as i64 / a[3] as i64) as usize;
                        let i = y * frame.width + x;
                        pixels[i] = blend(pixels[i], image.pixels[sy * image.width + sx]);
                    }
                }
            }
            3 => {
                for (index, ch) in c.text.chars().enumerate() {
                    for (row, bits) in font::glyph(ch).iter().enumerate() {
                        for col in 0..5 {
                            if bits & (1 << (4 - col)) != 0 {
                                let x = a[0] + (index as i32 * 6 + col) * a[2];
                                let y = a[1] + row as i32 * a[2];
                                let b = bounds(x, y, a[2], a[2], clip, frame.width, frame.height);
                                for yy in b[1]..b[3] {
                                    for xx in b[0]..b[2] {
                                        let i = yy * frame.width + xx;
                                        pixels[i] = blend(pixels[i], a[3] as u32);
                                    }
                                }
                            }
                        }
                    }
                }
            }
            #[cfg(feature = "text")]
            5 => {
                if let Some(draw) = text.and_then(|t| t.draws.get(&command_index)) {
                    let clip = [
                        clip[0].max(draw.bins.origin[0]),
                        clip[1].max(draw.bins.origin[1]),
                        clip[2].min(draw.bins.origin[0] + draw.bins.columns as i32 * 16),
                        clip[3].min(draw.bins.origin[1] + draw.bins.rows as i32 * 16),
                    ];
                    for mask in &draw.masks {
                        let b = bounds(
                            mask.left,
                            mask.top,
                            mask.width as i32,
                            mask.height as i32,
                            clip,
                            frame.width,
                            frame.height,
                        );
                        for y in b[1]..b[3] {
                            for x in b[0]..b[2] {
                                let coverage = u32::from(
                                    mask.data[(y as i32 - mask.top) as usize * mask.width
                                        + (x as i32 - mask.left) as usize],
                                );
                                let color = a[3] as u32;
                                let alpha = (coverage * (color >> 24) + 127) / 255;
                                let i = y * frame.width + x;
                                pixels[i] = blend(pixels[i], (color & 0x00ffffff) | (alpha << 24));
                            }
                        }
                    }
                }
            }
            _ => {}
        }
    }
    Ok(RenderedFrame {
        width: frame.width,
        height: frame.height,
        pixels: super::FramePixels::Cpu(Arc::new(pixels)),
        sequence,
        render_us: start.elapsed().as_micros(),
        text_prepare_us: None,
        gpu_timing: None,
        commands: frame.commands.len(),
    })
}
