pub mod desktop_shell;
#[cfg(feature="gpu")]
mod desktop_shell_gpu;
pub mod compositor;
#[cfg(feature = "gpu")]
mod compositor_gpu;
mod aa;
mod shadow_cache;
mod shadow_cpu;
mod shadow_frame;
#[cfg(feature = "gpu")]
mod shadow_gpu;
mod shadow_mask;
mod shadow_service;
#[cfg(all(feature = "gpu", panic = "abort"))]
compile_error!("The GPU backend requires panic=unwind to contain native wgpu fatal errors");
mod backend;
mod font;
mod frame;
#[cfg(all(feature = "gpu", feature = "text"))]
mod glyph_cache;
#[cfg(feature = "gpu")]
mod gpu;
#[cfg(feature = "gpu")]
mod gpu_session;
#[cfg(feature = "gpu")]
mod image_cache;
#[cfg(feature = "gpu")]
mod native_call;
#[cfg(feature = "gpu")]
pub use gpu_session::{GpuFrame, GpuSession};
#[cfg(all(feature = "gpu", feature = "desktop"))]
mod presenter;
#[cfg(all(feature = "gpu", feature = "desktop"))]
pub(crate) use presenter::GpuPresenter;
mod raster;
mod validation;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum RendererKind {
    #[default]
    Cpu,
    Gpu,
}

#[derive(Clone, Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GpuInfo {
    pub name: String,
    pub backend: String,
    pub device_type: String,
    pub driver: String,
    pub driver_info: String,
    pub initialization_micros: u128,
}

use anyhow::{Context, Result, bail};
pub use frame::{Frame, MAX_FRAME_BYTES, MAX_PIXELS};
use std::{collections::BTreeMap, io::Cursor, path::Path, sync::Arc};
#[derive(Clone, Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GpuTiming {
    pub pack_micros: u128,
    pub execute_readback_micros: u128,
}
#[derive(Clone, Debug, Default, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GpuStats {
    pub shadow_cache_hits: u64,
    pub shadow_cache_misses: u64,
    pub shadow_cache_evictions: u64,
    pub shadow_generation_work: u64,
    pub shadow_parameter_upload_bytes: u64,
    pub shadow_resident_bytes: usize,
    pub shadow_entries: usize,
    pub image_uploads: u64,
    pub glyph_uploads: u64,
    pub glyph_upload_bytes: u64,
    pub glyph_cache_evictions: u64,
    pub glyph_cache_compactions: u64,
    pub glyph_resident_bytes: usize,
    pub glyph_entries: usize,
    pub image_cache_compactions: u64,
    pub image_upload_bytes: u64,
    pub scene_upload_bytes: u64,
    pub readback_bytes: u64,
    pub render_submissions: u64,
    pub surface_presentations: u64,
    pub surface_format: Option<String>,
    pub presentation_upload_bytes: u64,
    pub surface_reconfigurations: u64,
    pub cached_images: usize,
}
#[derive(Clone, Debug)]
pub enum FramePixels {
    Cpu(Arc<Vec<u32>>),
    #[cfg(feature = "gpu")]
    Gpu(Arc<GpuFrame>),
}
#[derive(Clone, Debug)]
pub struct RenderedFrame {
    pub width: usize,
    pub height: usize,
    pub pixels: FramePixels,
    pub sequence: u64,
    pub render_us: u128,
    pub text_prepare_us: Option<u128>,
    pub gpu_timing: Option<GpuTiming>,
    pub commands: usize,
}
impl RenderedFrame {
    pub fn cpu_pixels(&self) -> Option<&[u32]> {
        match &self.pixels {
            FramePixels::Cpu(p) => Some(p),
            #[cfg(feature = "gpu")]
            FramePixels::Gpu(_) => None,
        }
    }
    pub fn read_pixels(&self) -> Result<Arc<Vec<u32>>> {
        match &self.pixels {
            FramePixels::Cpu(p) => Ok(p.clone()),
            #[cfg(feature = "gpu")]
            FramePixels::Gpu(g) => g.read_pixels(),
        }
    }
}
pub struct Image {
    pub width: usize,
    pub height: usize,
    pub pixels: Vec<u32>,
}
pub struct Graphics {
    pub(crate) status: backend::BackendStatus,
    #[cfg(feature = "gpu")]
    gpu: Option<GpuSession>,
    direct: bool,
    images: BTreeMap<i32, Image>,
    shadows: shadow_service::Styles,
    shadow_cpu: shadow_cpu::CpuCache,
    next_id: i32,
    allocated: usize,
    last: Option<RenderedFrame>,
    submitted: u64,
}
impl Graphics {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn with_renderer(renderer: RendererKind) -> Result<Self> {
        match renderer {
            RendererKind::Cpu => Ok(Self::new()),
            RendererKind::Gpu => {
                #[cfg(feature = "gpu")]
                {
                    let mut graphics = Self::default();
                    let session = GpuSession::new()?;
                    graphics.status = session.status.clone();
                    graphics.gpu = Some(session);
                    Ok(graphics)
                }
                #[cfg(not(feature = "gpu"))]
                {
                    bail!("GPU_FEATURE_REQUIRED: build with --features gpu")
                }
            }
        }
    }
    pub fn gpu_info(&self) -> Option<&GpuInfo> {
        #[cfg(feature = "gpu")]
        {
            self.gpu.as_ref().map(|g| &g.info)
        }
        #[cfg(not(feature = "gpu"))]
        {
            None
        }
    }
    pub fn backend(&self) -> &'static str {
        if self.gpu_info().is_some() {
            if self.direct {
                "wgpu-compute-surface"
            } else {
                "wgpu-compute-readback"
            }
        } else {
            "cpu-reference"
        }
    }
    #[cfg(feature = "gpu")]
    pub fn with_gpu_session(session: GpuSession) -> Self {
        Self {
            status: session.status.clone(),
            gpu: Some(session),
            direct: true,
            ..Self::default()
        }
    }
    #[cfg(all(feature = "gpu", feature = "desktop"))]
    pub(crate) fn gpu_session(&self) -> Option<GpuSession> {
        self.gpu.clone()
    }
    pub(crate) fn create_shadow_style(&mut self, radius: i32, blur: i32) -> i32 {
        self.shadows.create(radius, blur)
    }
    pub(crate) fn close_shadow_style(&mut self, handle: i32) -> bool {
        self.shadows.close(handle)
    }
    pub fn open_png(&mut self, bytes: &[u8]) -> Result<i32> {
        if self.images.len() >= 8 {
            bail!("IMAGE_COUNT_LIMIT");
        }
        let mut decoder = png::Decoder::new(Cursor::new(bytes));
        decoder.set_limits(png::Limits {
            bytes: 32 * 1024 * 1024,
        });
        decoder.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
        let mut reader = decoder.read_info()?;
        let info = reader.info();
        let (w, h) = (info.width as usize, info.height as usize);
        if w == 0
            || h == 0
            || w > 4096
            || h > 4096
            || w * h > MAX_PIXELS
            || self.allocated + w * h * 4 > 32 * 1024 * 1024
        {
            bail!("IMAGE_SIZE_LIMIT");
        }
        let size = reader.output_buffer_size().context("IMAGE_SIZE_LIMIT")?;
        if size > MAX_PIXELS * 4 {
            bail!("IMAGE_SIZE_LIMIT");
        }
        let mut raw = vec![0; size];
        let out = reader.next_frame(&mut raw)?;
        if out.width as usize != w || out.height as usize != h {
            bail!("IMAGE_ANIMATION_UNSUPPORTED");
        }
        let channels = match out.color_type {
            png::ColorType::Rgb => 3,
            png::ColorType::Rgba => 4,
            png::ColorType::Grayscale => 1,
            png::ColorType::GrayscaleAlpha => 2,
            _ => bail!("IMAGE_COLOR_UNSUPPORTED"),
        };
        if out.buffer_size() != w * h * channels {
            bail!("IMAGE_INVALID_LENGTH");
        }
        let pixels = raw[..out.buffer_size()]
            .chunks_exact(channels)
            .map(|p| {
                let (r, g, b, a) = match channels {
                    1 => (p[0], p[0], p[0], 255),
                    2 => (p[0], p[0], p[0], p[1]),
                    3 => (p[0], p[1], p[2], 255),
                    _ => (p[0], p[1], p[2], p[3]),
                };
                (u32::from(a) << 24) | (u32::from(r) << 16) | (u32::from(g) << 8) | u32::from(b)
            })
            .collect();
        let id = self.next_id;
        self.next_id = self.next_id.checked_add(1).context("IMAGE_HANDLE_LIMIT")?;
        self.allocated += w * h * 4;
        self.images.insert(
            id,
            Image {
                width: w,
                height: h,
                pixels,
            },
        );
        Ok(id)
    }
    pub fn dimensions(&self, h: i32) -> Option<(i32, i32)> {
        self.images
            .get(&h)
            .map(|i| (i.width as i32, i.height as i32))
    }
    pub fn close_image(&mut self, h: i32) -> bool {
        if let Some(i) = self.images.remove(&h) {
            self.allocated -= i.width * i.height * 4;
            #[cfg(feature = "gpu")]
            if let Some(gpu) = &self.gpu {
                gpu.release_image(h);
            }
            true
        } else {
            false
        }
    }
    pub(crate) fn check_backend(&self) -> Result<()> {
        self.status.check()
    }
    pub fn submit(&mut self, bytes: &[u8]) -> Result<()> {
        self.status.check()?;
        let frame = Frame::decode(bytes)?;
        validation::validate(&frame, &self.images)?;
        self.submit_decoded(frame)
    }
    fn submit_decoded(&mut self, frame: Frame) -> Result<()> {
        self.submit_prepared(
            frame,
            #[cfg(feature = "text")]
            None,
        )
    }
    fn submit_prepared(
        &mut self,
        frame: Frame,
        #[cfg(feature = "text")] text: Option<&crate::text::frame::PreparedTextFrame>,
    ) -> Result<()> {
        let overall = std::time::Instant::now();
        let base_work = validation::work(&frame, &self.images)?;
        #[cfg(feature = "text")]
        let text_work = text.map_or(0, |t| t.work);
        #[cfg(not(feature = "text"))]
        let text_work = 0;
        let shadows = shadow_frame::PreparedShadows::prepare(&frame, &self.shadows)?;
        let fixed_work = base_work
            .checked_add(text_work)
            .context("FRAME_WORK_LIMIT")?;
        if fixed_work > 32_000_000 {
            bail!("FRAME_WORK_LIMIT");
        }
        let cold_budget = 32_000_000usize
            .checked_sub(fixed_work)
            .and_then(|v| v.checked_sub(shadows.area_work))
            .context("SHADOW_WORK_LIMIT")?;
        let preparation = overall.elapsed().as_micros();
        #[cfg(feature = "gpu")]
        let mut rendered = if let Some(gpu) = &self.gpu {
            if !shadows.draws.is_empty() {
                gpu.render_prepared(
                    &frame,
                    &self.images,
                    self.submitted + 1,
                    self.direct,
                    #[cfg(feature = "text")]
                    text,
                    &shadows,
                    cold_budget,
                )?
            } else {
                #[cfg(feature = "text")]
                if let Some(text) = text {
                    gpu.render_text(&frame, &self.images, self.submitted + 1, self.direct, text)?
                } else {
                    gpu.render(&frame, &self.images, self.submitted + 1, self.direct)?
                }
                #[cfg(not(feature = "text"))]
                gpu.render(&frame, &self.images, self.submitted + 1, self.direct)?
            }
        } else {
            self.render_cpu(
                &frame,
                #[cfg(feature = "text")]
                text,
                &shadows,
                cold_budget,
            )?
        };
        #[cfg(not(feature = "gpu"))]
        let mut rendered = self.render_cpu(
            &frame,
            #[cfg(feature = "text")]
            text,
            &shadows,
            cold_budget,
        )?;
        #[cfg(feature = "text")]
        if text.is_some() {
            rendered.text_prepare_us = Some(preparation);
        }
        #[cfg(not(feature = "text"))]
        let _ = preparation;
        rendered.render_us = overall.elapsed().as_micros();
        self.submitted += 1;
        self.last = Some(rendered);
        Ok(())
    }
    fn render_cpu(
        &mut self,
        frame: &Frame,
        #[cfg(feature = "text")] text: Option<&crate::text::frame::PreparedTextFrame>,
        shadows: &shadow_frame::PreparedShadows,
        cold_budget: usize,
    ) -> Result<RenderedFrame> {
        if !shadows.draws.is_empty() {
            return self.shadow_cpu.render(
                frame,
                &self.images,
                self.submitted + 1,
                #[cfg(feature = "text")]
                text,
                shadows,
                cold_budget,
            );
        }
        #[cfg(feature = "text")]
        if let Some(text) = text {
            return raster::render_with_text(frame, &self.images, self.submitted + 1, text);
        }
        raster::render(frame, &self.images, self.submitted + 1)
    }
    #[cfg(feature = "text")]
    pub fn submit_with_text(
        &mut self,
        bytes: &[u8],
        service: Option<&mut crate::text::TextService>,
    ) -> Result<()> {
        self.submit_with_sdk(bytes, service, 3)
    }
    #[cfg(feature = "text")]
    pub fn submit_with_sdk(
        &mut self,
        bytes: &[u8],
        service: Option<&mut crate::text::TextService>,
        sdk: u32,
    ) -> Result<()> {
        self.status.check()?;
        if !matches!(sdk, 3 | 4) {
            bail!("FRAME_INVALID_SDK");
        }
        let frame = Frame::decode_for_sdk(bytes, sdk)?;
        if !frame.commands.iter().any(|c| c.op == 5) {
            return self.submit_decoded(frame);
        }
        validation::validate(&frame, &self.images)?;
        let overall = std::time::Instant::now();
        let service = service.context("TEXT_HANDLE_INVALID")?;
        let prepared = crate::text::frame::PreparedTextFrame::prepare(&frame, service)?;
        let text_preparation = overall.elapsed().as_micros();
        self.submit_prepared(frame, Some(&prepared))?;
        if let Some(rendered) = &mut self.last {
            rendered.text_prepare_us =
                Some(text_preparation + rendered.text_prepare_us.unwrap_or(0));
            rendered.render_us += text_preparation;
        }
        Ok(())
    }
    pub fn last_frame(&self) -> Option<RenderedFrame> {
        self.last.clone()
    }
    pub fn frames_submitted(&self) -> u64 {
        self.submitted
    }
}
pub fn write_png(path: &Path, frame: &RenderedFrame) -> Result<()> {
    let pixels = frame.read_pixels()?;
    let mut encoder = png::Encoder::new(
        std::fs::File::create(path)?,
        frame.width as u32,
        frame.height as u32,
    );
    encoder.set_color(png::ColorType::Rgb);
    encoder.set_depth(png::BitDepth::Eight);
    let mut writer = encoder.write_header()?;
    let bytes: Vec<u8> = pixels
        .iter()
        .flat_map(|p| [(p >> 16) as u8, (p >> 8) as u8, *p as u8])
        .collect();
    writer.write_image_data(&bytes)?;
    Ok(())
}

impl Default for Graphics {
    fn default() -> Self {
        Self {
            #[cfg(feature = "gpu")]
            gpu: None,
            status: backend::BackendStatus::default(),
            direct: false,
            images: BTreeMap::new(),
            shadows: shadow_service::Styles::default(),
            shadow_cpu: shadow_cpu::CpuCache::default(),
            next_id: 1,
            allocated: 0,
            last: None,
            submitted: 0,
        }
    }
}

#[cfg(test)]
mod shadow_tests;
