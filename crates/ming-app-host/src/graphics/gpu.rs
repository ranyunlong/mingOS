use super::{
    Frame, FramePixels, GpuFrame, GpuInfo, GpuSession, GpuTiming, Image, RenderedFrame,
    backend::BackendStatus, font,
};
use anyhow::{Context, Result, bail};
use std::{
    collections::BTreeMap,
    sync::{Arc, mpsc},
    time::{Duration, Instant},
};
const MAX_CANDIDATE_CHECKS: usize = 64_000_000;
const COMMAND_WORDS: usize = 20;
const MAX_SCENE_BYTES: usize = 36 * 1024 * 1024;
pub(super) struct Gpu {
    pub(super) device: wgpu::Device,
    pub(super) queue: wgpu::Queue,
    #[cfg(feature = "desktop")]
    pub(super) adapter: wgpu::Adapter,
    pipeline: wgpu::ComputePipeline,
    pub info: GpuInfo,
    pub(super) status: BackendStatus,
    atlas: Option<wgpu::Buffer>,
    glyph_atlas: Option<wgpu::Buffer>,
    shadow_gpu: Option<super::shadow_gpu::ShadowGpu>,
    shadow_dummy: Option<wgpu::Buffer>,
    shadow_cache: super::shadow_cache::CacheLayout,
    #[cfg(feature = "text")]
    glyph_cache: super::glyph_cache::GlyphCache,
    cache: super::image_cache::CacheLayout,
    pub(super) stats: super::GpuStats,
}
impl Gpu {
    pub fn new(status: BackendStatus) -> Result<Self> {
        Self::from_instance(&Self::instance(), None, status)
    }
    pub(super) fn instance() -> wgpu::Instance {
        #[cfg(target_os = "windows")]
        let backends = wgpu::Backends::DX12;
        #[cfg(not(target_os = "windows"))]
        let backends = wgpu::Backends::VULKAN;
        wgpu::Instance::new(&wgpu::InstanceDescriptor {
            backends,
            ..Default::default()
        })
    }
    pub(super) fn from_instance(
        instance: &wgpu::Instance,
        surface: Option<&wgpu::Surface<'_>>,
        status: BackendStatus,
    ) -> Result<Self> {
        let start = Instant::now();
        let mut adapters = instance.enumerate_adapters(wgpu::Backends::all());
        adapters.sort_by_key(|a| match a.get_info().device_type {
            wgpu::DeviceType::DiscreteGpu => 0,
            wgpu::DeviceType::IntegratedGpu => 1,
            _ => 2,
        });
        let adapter = adapters
            .into_iter()
            .find(|a| {
                matches!(
                    a.get_info().device_type,
                    wgpu::DeviceType::DiscreteGpu | wgpu::DeviceType::IntegratedGpu
                ) && surface.is_none_or(|s| a.is_surface_supported(s))
            })
            .context(
                "GPU_HARDWARE_UNAVAILABLE: no discrete/integrated adapter; CPU fallback disabled",
            )?;
        let meta = adapter.get_info();
        let (device, queue) = pollster::block_on(adapter.request_device(&wgpu::DeviceDescriptor {
            label: Some("Ming SDK scene renderer"),
            ..Default::default()
        }))
        .context("GPU_DEVICE_REQUEST")?;
        let callback_status = status.clone();
        device.on_uncaptured_error(Arc::new(move |error| {
            capture_error(&callback_status, error)
        }));
        let lost_status = status.clone();
        device.set_device_lost_callback(move |reason, message| {
            lost_status.record(format!("GPU_DEVICE_LOST: {reason:?}: {message}"));
        });
        push_error_scopes(&device);
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("Ming integer scene rasterizer"),
            source: wgpu::ShaderSource::Wgsl(include_str!("scene.wgsl").into()),
        });
        let pipeline = device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
            label: Some("Ming scene compute"),
            layout: None,
            module: &shader,
            entry_point: Some("main"),
            compilation_options: Default::default(),
            cache: None,
        });
        collect_error_scopes(&device, &status);
        super::native_call::call(&status, || device.poll(wgpu::PollType::Poll))?
            .context("GPU_DEVICE_POLL")?;
        status.check().context("GPU_PIPELINE")?;
        Ok(Self {
            device,
            queue,
            #[cfg(feature = "desktop")]
            adapter,
            atlas: None,
            glyph_atlas: None,
            shadow_gpu: None,
            shadow_dummy: None,
            shadow_cache: Default::default(),
            #[cfg(feature = "text")]
            glyph_cache: Default::default(),
            cache: super::image_cache::CacheLayout::new(8_388_608),
            stats: super::GpuStats::default(),
            pipeline,
            status,
            info: GpuInfo {
                name: meta.name,
                backend: format!("{:?}", meta.backend),
                device_type: format!("{:?}", meta.device_type),
                driver: meta.driver,
                driver_info: meta.driver_info,
                initialization_micros: start.elapsed().as_micros(),
            },
        })
    }

    /// Reuse device handles and immutable pipelines, but no application residency or counters.
    pub(super) fn fork_render_context(&self) -> Self {
        Self {
            device: self.device.clone(),
            queue: self.queue.clone(),
            #[cfg(feature = "desktop")]
            adapter: self.adapter.clone(),
            pipeline: self.pipeline.clone(),
            info: self.info.clone(),
            status: self.status.clone(),
            atlas: None,
            glyph_atlas: None,
            shadow_gpu: None,
            shadow_dummy: None,
            shadow_cache: Default::default(),
            #[cfg(feature = "text")]
            glyph_cache: Default::default(),
            cache: super::image_cache::CacheLayout::new(8_388_608),
            stats: super::GpuStats::default(),
        }
    }

    #[allow(
        clippy::too_many_arguments,
        reason = "Explicit frame resources are checked before GPU cache mutation"
    )]
    pub(super) fn render(
        &mut self,
        frame: &Frame,
        images: &BTreeMap<i32, Image>,
        sequence: u64,
        session: &GpuSession,
        direct: bool,
        #[cfg(feature = "text")] text: Option<&crate::text::frame::PreparedTextFrame>,
        shadows: &super::shadow_frame::PreparedShadows,
        cold_budget: usize,
    ) -> Result<RenderedFrame> {
        self.status.check()?;
        if frame.width * frame.height * frame.commands.len().max(1) > MAX_CANDIDATE_CHECKS {
            bail!("GPU_CANDIDATE_WORK_LIMIT");
        }
        // Shared driver and context mutexes are held. Admission precedes GPU operations.
        let shadow_plan = self.shadow_cache.plan(&shadows.keys, cold_budget)?;
        let start = Instant::now();
        #[cfg(feature = "text")]
        let mut glyph_plan = if let Some(text) = text {
            Some(
                self.glyph_cache
                    .plan(text.draws.values().flat_map(|d| d.masks.iter()))?,
            )
        } else {
            None
        };
        #[cfg(feature = "text")]
        let empty_glyph_offsets = std::collections::HashMap::new();
        push_error_scopes(&self.device);
        let result: Result<RenderedFrame> = (|| {
            let sizes = images
                .iter()
                .map(|(&id, image)| (id, image.pixels.len()))
                .collect();
            let update = self.cache.sync(&sizes)?;
            if update.compacted {
                self.stats.image_cache_compactions += 1;
            }
            if self.atlas.is_none() {
                self.atlas = Some(self.allocate_buffer(&wgpu::BufferDescriptor {
                    label: Some("bounded image cache"),
                    size: 32 * 1024 * 1024,
                    usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
                    mapped_at_creation: false,
                })?);
            }
            for id in update.uploads {
                let image = &images[&id];
                #[cfg(target_endian = "little")]
                let data = bytemuck::cast_slice(&image.pixels);
                #[cfg(target_endian = "big")]
                let converted: Vec<u32> = image.pixels.iter().map(|p| p.to_le()).collect();
                #[cfg(target_endian = "big")]
                let data = bytemuck::cast_slice(&converted);
                self.queue.write_buffer(
                    self.atlas.as_ref().expect("atlas allocated"),
                    u64::from(update.offsets[&id]) * 4,
                    data,
                );
                self.stats.image_uploads += 1;
                self.stats.image_upload_bytes += data.len() as u64;
            }
            self.stats.cached_images = images.len();
            if self.glyph_atlas.is_none() {
                self.glyph_atlas = Some(self.allocate_buffer(&wgpu::BufferDescriptor {
                    label: Some("bounded glyph coverage cache"),
                    size: 4 * 1024 * 1024,
                    usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
                    mapped_at_creation: false,
                })?);
            }
            #[cfg(feature = "text")]
            if let Some(plan) = &glyph_plan {
                for upload in &plan.uploads {
                    let mut padded = vec![0u8; upload.data.len().div_ceil(4) * 4];
                    padded[..upload.data.len()].copy_from_slice(&upload.data);
                    self.queue.write_buffer(
                        self.glyph_atlas.as_ref().expect("glyph atlas"),
                        u64::from(upload.offset),
                        &padded,
                    );
                    self.stats.glyph_uploads += 1;
                    self.stats.glyph_upload_bytes += padded.len() as u64;
                }
            }
            if !shadows.keys.is_empty() && self.shadow_gpu.is_none() {
                push_error_scopes(&self.device);
                let resource = super::native_call::call(&self.status, || {
                    super::shadow_gpu::ShadowGpu::new(&self.device)
                });
                collect_error_scopes(&self.device, &self.status);
                self.status.check()?;
                self.shadow_gpu = Some(resource?);
            }
            if self.shadow_gpu.is_none() && self.shadow_dummy.is_none() {
                self.shadow_dummy = Some(self.allocate_buffer(&wgpu::BufferDescriptor {
                    label: Some("empty shadow binding"),
                    size: 4,
                    usage: wgpu::BufferUsages::STORAGE,
                    mapped_at_creation: false,
                })?);
            }
            let scene = pack(
                frame,
                images,
                &update.offsets,
                shadows,
                &shadow_plan.slots,
                #[cfg(feature = "text")]
                text.map(|t| {
                    (
                        t,
                        glyph_plan
                            .as_ref()
                            .map(|p| &p.offsets)
                            .unwrap_or(&empty_glyph_offsets),
                    )
                }),
            )?;
            let pack_micros = start.elapsed().as_micros();
            let execute_start = Instant::now();
            let status = self.status.clone();
            let output = super::native_call::call(&status, || {
                self.execute(frame, &scene, &shadow_plan.misses)
            })??;
            let pixels = if direct {
                FramePixels::Gpu(Arc::new(GpuFrame {
                    session: session.clone(),
                    buffer: output,
                    width: frame.width,
                    height: frame.height,
                }))
            } else {
                FramePixels::Cpu(Arc::new(self.readback(
                    &output,
                    frame.width,
                    frame.height,
                )?))
            };
            #[cfg(feature = "text")]
            if let Some(plan) = glyph_plan.take() {
                self.stats.glyph_cache_evictions += plan.evictions;
                self.stats.glyph_cache_compactions += u64::from(plan.compacted);
                self.glyph_cache = plan.next;
                self.stats.glyph_resident_bytes = self.glyph_cache.resident_bytes();
                self.stats.glyph_entries = self.glyph_cache.len();
            }
            Ok(RenderedFrame {
                width: frame.width,
                height: frame.height,
                pixels,
                sequence,
                commands: frame.commands.len(),
                gpu_timing: Some(GpuTiming {
                    pack_micros,
                    execute_readback_micros: execute_start.elapsed().as_micros(),
                }),
                render_us: start.elapsed().as_micros(),
                text_prepare_us: None,
            })
        })();
        collect_error_scopes(&self.device, &self.status);
        self.status.check()?;
        if result.is_ok() {
            self.stats.shadow_cache_hits += shadow_plan.hits as u64;
            self.stats.shadow_cache_misses += shadow_plan.misses.len() as u64;
            self.stats.shadow_cache_evictions += shadow_plan.evictions as u64;
            self.stats.shadow_generation_work += shadow_plan.cold_work as u64;
            self.shadow_cache = shadow_plan.next;
            self.stats.shadow_resident_bytes = self.shadow_cache.resident_bytes();
            self.stats.shadow_entries = self.shadow_cache.len();
        } else if let Err(error) = &result {
            // Ordinary quotas reject one frame; actual backend errors remain terminal.
            if error.to_string() != "GPU_SCENE_LIMIT" {
                self.status.record(format!("{error:#}"));
            }
        }
        result
    }
    pub(super) fn allocate_buffer(
        &self,
        descriptor: &wgpu::BufferDescriptor<'_>,
    ) -> Result<wgpu::Buffer> {
        self.status.check()?;
        push_error_scopes(&self.device);
        let buffer = self.device.create_buffer(descriptor);
        collect_error_scopes(&self.device, &self.status);
        self.poll_device()?;
        self.status.check()?;
        Ok(buffer)
    }
    pub(super) fn release_image(&mut self, id: i32) {
        if self.cache.release(id) {
            self.stats.cached_images = self.stats.cached_images.saturating_sub(1);
        }
    }
    fn execute(
        &mut self,
        frame: &Frame,
        scene: &[u32],
        misses: &[(super::shadow_mask::ShadowKey, u32)],
    ) -> Result<wgpu::Buffer> {
        let input = self.allocate_buffer(&wgpu::BufferDescriptor {
            label: Some("scene commands and glyphs"),
            size: std::mem::size_of_val(scene) as u64,
            usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        })?;
        self.queue
            .write_buffer(&input, 0, bytemuck::cast_slice(scene));
        self.stats.scene_upload_bytes += std::mem::size_of_val(scene) as u64;
        let output = self.allocate_buffer(&wgpu::BufferDescriptor {
            label: Some("GPU resident ARGB frame"),
            size: (frame.width * frame.height * 4) as u64,
            usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC,
            mapped_at_creation: false,
        })?;
        let bindings = self.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: None,
            layout: &self.pipeline.get_bind_group_layout(0),
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 4,
                    resource: self
                        .shadow_gpu
                        .as_ref()
                        .map(|s| &s.cache)
                        .or(self.shadow_dummy.as_ref())
                        .expect("shadow binding")
                        .as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: input.as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: output.as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 2,
                    resource: self.atlas.as_ref().expect("atlas").as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 3,
                    resource: self
                        .glyph_atlas
                        .as_ref()
                        .expect("glyph atlas")
                        .as_entire_binding(),
                },
            ],
        });
        let mut encoder = self.device.create_command_encoder(&Default::default());
        if let Some(shadows) = &self.shadow_gpu {
            self.stats.shadow_parameter_upload_bytes +=
                shadows.encode(&self.device, &self.queue, &mut encoder, misses);
        }
        {
            let mut pass = encoder.begin_compute_pass(&wgpu::ComputePassDescriptor {
                label: None,
                timestamp_writes: None,
            });
            pass.set_pipeline(&self.pipeline);
            pass.set_bind_group(0, &bindings, &[]);
            pass.dispatch_workgroups(
                (frame.width as u32).div_ceil(8),
                (frame.height as u32).div_ceil(8),
                1,
            );
        }
        let submission = self.queue.submit([encoder.finish()]);
        self.stats.render_submissions += 1;
        // One completed compute output at a time bounds queued resources without CPU pixel transfer.
        self.wait(submission)?;
        Ok(output)
    }
    pub(super) fn poll_device(&self) -> Result<()> {
        super::native_call::call(&self.status, || self.device.poll(wgpu::PollType::Poll))?
            .context("GPU_DEVICE_POLL")?;
        self.status.check()
    }
    pub(super) fn wait(&self, submission: wgpu::SubmissionIndex) -> Result<()> {
        super::native_call::call(&self.status, || {
            self.device.poll(wgpu::PollType::Wait {
                submission_index: Some(submission),
                timeout: Some(Duration::from_secs(2)),
            })
        })?
        .context("GPU_WAIT")?;
        Ok(())
    }
    pub(super) fn readback(
        &mut self,
        output: &wgpu::Buffer,
        width: usize,
        height: usize,
    ) -> Result<Vec<u32>> {
        let byte_len = (width * height * 4) as u64;
        let staging = self.allocate_buffer(&wgpu::BufferDescriptor {
            label: Some("explicit pixel readback"),
            size: byte_len,
            usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        })?;
        let mut encoder = self.device.create_command_encoder(&Default::default());
        encoder.copy_buffer_to_buffer(output, 0, &staging, 0, byte_len);
        let submission = self.queue.submit([encoder.finish()]);
        let (sender, receiver) = mpsc::sync_channel(1);
        staging.slice(..).map_async(wgpu::MapMode::Read, move |r| {
            let _ = sender.send(r);
        });
        self.wait(submission)?;
        receiver
            .recv_timeout(Duration::from_secs(2))
            .context("GPU_MAP_CALLBACK")?
            .context("GPU_MAP")?;
        let pixels = {
            let bytes = staging.slice(..).get_mapped_range();
            let words: &[u32] = bytemuck::try_cast_slice(&bytes)
                .map_err(|_| anyhow::anyhow!("GPU_MAP_ALIGNMENT"))?;
            #[cfg(target_endian = "little")]
            let pixels = words.to_vec();
            #[cfg(target_endian = "big")]
            let pixels = words.iter().map(|p| u32::from_le(*p)).collect();
            pixels
        };
        staging.unmap();
        self.stats.readback_bytes += byte_len;
        Ok(pixels)
    }
}
// Each record contains op + seven ABI arguments, a resolved clip, image metadata,
// and a character-indexed glyph stream. Offsets are words from the buffer start.
fn pack(
    frame: &Frame,
    images: &BTreeMap<i32, Image>,
    offsets: &BTreeMap<i32, u32>,
    shadows: &super::shadow_frame::PreparedShadows,
    shadow_slots: &[(super::shadow_mask::ShadowKey, u32)],
    #[cfg(feature = "text")] text: Option<(
        &crate::text::frame::PreparedTextFrame,
        &std::collections::HashMap<cosmic_text::CacheKey, u32>,
    )>,
) -> Result<Vec<u32>> {
    if frame.width * frame.height * frame.commands.len().max(1) > MAX_CANDIDATE_CHECKS {
        bail!("GPU_CANDIDATE_WORK_LIMIT");
    }
    let count = frame.commands.iter().filter(|c| c.op != 4).count();
    let mut words = vec![0u32; 4 + count * COMMAND_WORDS];
    words[..4].copy_from_slice(&[frame.width as u32, frame.height as u32, count as u32, 0]);
    let mut clip = [0, 0, frame.width as i32, frame.height as i32];
    let mut index = 0;
    for (command_index, c) in frame.commands.iter().enumerate() {
        let a = c.a;
        if c.op == 4 {
            clip = [a[0], a[1], a[0] + a[2], a[1] + a[3]];
            continue;
        }
        let mut record = [0u32; COMMAND_WORDS];
        record[0] = c.op as u32;
        for (out, value) in record[1..8].iter_mut().zip(a) {
            *out = value as u32;
        }
        for (out, value) in record[8..12].iter_mut().zip(clip) {
            *out = value as u32;
        }
        if c.op == 7 {
            let draw = shadows
                .draws
                .get(&command_index)
                .context("SHADOW_DRAW_MISSING")?;
            if draw.visible {
                let offset = shadow_slots
                    .iter()
                    .find(|(key, _)| *key == draw.key)
                    .context("SHADOW_SLOT_MISSING")?
                    .1;
                record[12..18].copy_from_slice(&[
                    offset,
                    draw.key.core_width() as u32,
                    draw.key.core_height() as u32,
                    draw.key.radius() as u32,
                    draw.key.blur() as u32,
                    draw.key.patch_width() as u32,
                ]);
            } else {
                record[0] = 0; // Invisible validated draw: no cache slot was admitted.
            }
        }
        if c.op == 2 {
            let image = &images[&a[0]];
            record[12..15].copy_from_slice(&[
                offsets[&a[0]],
                image.width as u32,
                image.height as u32,
            ]);
        }
        if c.op == 3 {
            record[15] = words.len() as u32;
            record[16] = c.text.chars().count() as u32;
            for ch in c.text.chars() {
                words.extend(font::glyph(ch).map(u32::from));
            }
        }
        #[cfg(feature = "text")]
        if c.op == 5 {
            let (prepared, offsets) = text.context("TEXT_PREPARATION_MISSING")?;
            let draw = prepared
                .draws
                .get(&command_index)
                .context("TEXT_DRAW_MISSING")?;
            record[1] = draw.bins.origin[0] as u32;
            record[2] = draw.bins.origin[1] as u32;
            record[3] = draw.bins.columns as u32;
            record[4] = draw.bins.rows as u32;
            record[5] = a[3] as u32;
            record[12] = words.len() as u32;
            for mask in &draw.masks {
                words.extend([
                    mask.left as u32,
                    mask.top as u32,
                    mask.width as u32,
                    mask.height as u32,
                    *offsets.get(&mask.key).context("TEXT_GLYPH_NOT_RESIDENT")?,
                    0,
                    0,
                    0,
                ]);
            }
            record[6] = words.len() as u32;
            for cell in &draw.bins.cells {
                words.extend(*cell);
            }
            record[7] = words.len() as u32;
            words.extend(&draw.bins.indices);
        }
        let base = 4 + index * COMMAND_WORDS;
        words[base..base + COMMAND_WORDS].copy_from_slice(&record);
        index += 1;
    }
    if words.len() * 4 > MAX_SCENE_BYTES {
        bail!("GPU_SCENE_LIMIT");
    }
    // Preserve the little-endian shader wire format; this loop is free on LE hosts.
    for word in &mut words {
        *word = word.to_le();
    }
    Ok(words)
}

pub(super) fn capture_error(status: &BackendStatus, error: wgpu::Error) {
    status.record(format!("GPU_BACKEND: {error:?}"));
}
pub(super) fn push_error_scopes(device: &wgpu::Device) {
    for filter in [
        wgpu::ErrorFilter::Validation,
        wgpu::ErrorFilter::OutOfMemory,
        wgpu::ErrorFilter::Internal,
    ] {
        device.push_error_scope(filter);
    }
}
pub(super) fn collect_error_scopes(device: &wgpu::Device, status: &BackendStatus) {
    for _ in 0..3 {
        if let Some(error) = pollster::block_on(device.pop_error_scope()) {
            capture_error(status, error);
        }
    }
}

#[cfg(test)]
mod error_tests {
    use super::*;
    #[test]
    #[ignore = "requires a hardware GPU; run explicitly"]
    fn failed_gpu_allocation_is_an_error_instead_of_a_mapping_panic() {
        let gpu = Gpu::new(BackendStatus::default()).unwrap();
        let descriptor = wgpu::BufferDescriptor {
            label: None,
            size: u64::MAX,
            usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        };
        let error = gpu
            .allocate_buffer(&descriptor)
            .expect_err("invalid allocation must fail");
        assert!(error.to_string().contains("RENDERER_FAILED"));
        assert!(gpu.status.check().is_err());
    }
    #[test]
    fn uncaptured_allocation_and_internal_errors_latch_without_panicking() {
        for error in [
            wgpu::Error::OutOfMemory {
                source: Box::new(std::io::Error::other("injected OOM")),
            },
            wgpu::Error::Internal {
                source: Box::new(std::io::Error::other("injected driver failure")),
                description: "driver failure".into(),
            },
        ] {
            let status = super::super::backend::BackendStatus::default();
            capture_error(&status, error);
            let first = status.check().unwrap_err().to_string();
            status.record("secondary".into());
            assert_eq!(status.check().unwrap_err().to_string(), first);
        }
    }
}
