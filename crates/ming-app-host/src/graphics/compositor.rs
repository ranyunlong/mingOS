//! Host-owned composition of bounded, immutable application frames, bottom to top.
use super::{FramePixels, MAX_PIXELS, RenderedFrame};
use anyhow::{Result, bail};
use std::{sync::Arc, time::Instant};

const MAX_LAYERS: usize = 5;
#[derive(Clone, Copy, Debug)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}
#[derive(Clone, Copy, Debug)]
pub struct Layer<'a> {
    pub frame: &'a RenderedFrame,
    pub destination: Rect,
    /// Screen coordinates, with exclusive right and bottom edges.
    pub clip: Rect,
    pub opacity: u8,
}

pub struct Compositor {
    submitted: u64,
    #[cfg(feature = "gpu")]
    gpu: Option<super::compositor_gpu::GpuCompositor>,
}
impl Compositor {
    pub fn cpu() -> Self {
        Self {
            submitted: 0,
            #[cfg(feature = "gpu")]
            gpu: None,
        }
    }
    /// Fork a dedicated compositor context, sharing the application's device only.
    #[cfg(feature = "gpu")]
    pub fn gpu(session: &super::GpuSession) -> Result<Self> {
        Ok(Self {
            submitted: 0,
            gpu: Some(super::compositor_gpu::GpuCompositor::new(session)?),
        })
    }
    #[cfg(feature = "gpu")]
    pub fn gpu_stats(&self) -> Result<Option<super::GpuStats>> {
        self.gpu.as_ref().map(|gpu| gpu.stats()).transpose()
    }
    /// Reject incompatible host frames before a scene replaces its healthy latest.
    pub(crate) fn check_source(&self, source: &RenderedFrame) -> Result<()> {
        validate_source(source)?;
        #[cfg(feature = "gpu")]
        if let Some(gpu) = &self.gpu {
            return gpu.check_source(source);
        }
        if source.cpu_pixels().is_none() {
            bail!("COMPOSITOR_CPU_FRAME_REQUIRED");
        }
        Ok(())
    }
    pub fn compose(
        &mut self,
        width: usize,
        height: usize,
        background: u32,
        layers: &[Layer<'_>],
    ) -> Result<RenderedFrame> {
        validate(width, height, background, layers)?;
        let sequence = self
            .submitted
            .checked_add(1)
            .ok_or_else(|| anyhow::anyhow!("COMPOSITOR_SEQUENCE_LIMIT"))?;
        #[cfg(feature = "gpu")]
        if let Some(gpu) = &self.gpu {
            let output = gpu.compose(width, height, background, layers, sequence)?;
            self.submitted = sequence;
            return Ok(output);
        }
        for layer in layers {
            if layer.frame.cpu_pixels().is_none() {
                bail!("COMPOSITOR_CPU_FRAME_REQUIRED");
            }
        }

        let start = Instant::now();
        let pixels = cpu_pixels(width, height, background, layers);
        self.submitted = sequence;
        Ok(RenderedFrame {
            width,
            height,
            pixels: FramePixels::Cpu(Arc::new(pixels)),
            sequence,
            render_us: start.elapsed().as_micros(),
            text_prepare_us: None,
            gpu_timing: None,
            commands: layers.len(),
        })
    }
}
fn valid_size(width: usize, height: usize) -> bool {
    (1..=4096).contains(&width) && (1..=4096).contains(&height) && width * height <= MAX_PIXELS
}
fn valid_rect(r: Rect, allow_empty: bool) -> bool {
    let minimum = i32::from(!allow_empty);
    (-1_000_000..=1_000_000).contains(&r.x)
        && (-1_000_000..=1_000_000).contains(&r.y)
        && (minimum..=4096).contains(&r.width)
        && (minimum..=4096).contains(&r.height)
}
fn validate(width: usize, height: usize, background: u32, layers: &[Layer<'_>]) -> Result<()> {
    if !valid_size(width, height) {
        bail!("COMPOSITOR_VIEWPORT_LIMIT");
    }
    if background >> 24 != 255 {
        bail!("COMPOSITOR_BACKGROUND_INVALID");
    }
    if layers.len() > MAX_LAYERS {
        bail!("COMPOSITOR_LAYER_LIMIT");
    }
    for layer in layers {
        validate_source(layer.frame)?;
        if !valid_rect(layer.destination, false) || !valid_rect(layer.clip, true) {
            bail!("COMPOSITOR_RECT_INVALID");
        }
    }
    Ok(())
}
/// Shared host-side validation; never maps or uploads pixels.
pub(crate) fn validate_source(f: &RenderedFrame) -> Result<()> {
    if !valid_size(f.width, f.height) {
        bail!("COMPOSITOR_SOURCE_LIMIT");
    }
    match &f.pixels {
        FramePixels::Cpu(p) => {
            if p.len() != f.width * f.height {
                bail!("COMPOSITOR_SOURCE_STORAGE");
            }
        }
        #[cfg(feature = "gpu")]
        FramePixels::Gpu(g) => {
            if g.width != f.width
                || g.height != f.height
                || g.buffer.size() != (f.width * f.height * 4) as u64
            {
                bail!("COMPOSITOR_SOURCE_STORAGE");
            }
        }
    }
    Ok(())
}
// Independent painter-order CPU oracle; no shader packing or GPU snapshot calls.
fn cpu_pixels(width: usize, height: usize, background: u32, layers: &[Layer<'_>]) -> Vec<u32> {
    let mut output = vec![background; width * height];
    for layer in layers {
        let d = layer.destination;
        let c = layer.clip;
        let left = d.x.max(c.x).max(0);
        let top = d.y.max(c.y).max(0);
        let right = (d.x + d.width).min(c.x + c.width).min(width as i32);
        let bottom = (d.y + d.height).min(c.y + c.height).min(height as i32);
        let source = layer.frame.cpu_pixels().expect("validated CPU source");
        for y in top..bottom {
            let sy = (y - d.y) as usize * layer.frame.height / d.height as usize;
            for x in left..right {
                let sx = (x - d.x) as usize * layer.frame.width / d.width as usize;
                let color = source[sy * layer.frame.width + sx];
                let alpha = ((color >> 24) * u32::from(layer.opacity) + 127) / 255;
                let pixel = &mut output[y as usize * width + x as usize];
                let mut composed = 0xff000000;
                for shift in [0, 8, 16] {
                    composed |= ((((color >> shift) & 255) * alpha
                        + ((*pixel >> shift) & 255) * (255 - alpha)
                        + 127)
                        / 255)
                        << shift;
                }
                *pixel = composed;
            }
        }
    }
    output
}
