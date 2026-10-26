use super::{FramePixels, GpuFrame, GpuSession, RenderedFrame, backend::BackendStatus, gpu::Gpu};
use anyhow::{Context, Result, bail};
use std::sync::Arc;
use winit::window::Window;

struct NativeSurfaceFrame {
    texture: Option<wgpu::SurfaceTexture>,
    status: BackendStatus,
}
impl NativeSurfaceFrame {
    fn texture(&self) -> &wgpu::Texture {
        &self.texture.as_ref().expect("live surface frame").texture
    }
    fn present(mut self) -> Result<()> {
        self.status.check()?;
        let texture = self.texture.take().context("GPU_SURFACE_FRAME")?;
        super::native_call::cleanup(&self.status, || texture.present())
    }
}
impl Drop for NativeSurfaceFrame {
    fn drop(&mut self) {
        if let Some(texture) = self.texture.take() {
            let _ = super::native_call::cleanup(&self.status, || drop(texture));
        }
    }
}
pub(crate) struct GpuPresenter {
    // Surface owns its window handle and is released before the session.
    surface: wgpu::Surface<'static>,
    pub(crate) session: GpuSession,
    pipeline: wgpu::RenderPipeline,
    config: wgpu::SurfaceConfiguration,
    size: Option<(u32, u32)>,
}
impl GpuPresenter {
    pub(crate) fn new(window: Arc<Window>) -> Result<Self> {
        let instance = Gpu::instance();
        let surface = instance
            .create_surface(window)
            .context("GPU_SURFACE_CREATE")?;
        let gpu = Gpu::from_instance(&instance, Some(&surface), BackendStatus::default())?;
        let caps = surface.get_capabilities(&gpu.adapter);
        let format = choose_format(&caps.formats)?;
        let mut config = surface
            .get_default_config(&gpu.adapter, 1, 1)
            .context("GPU_SURFACE_CONFIG")?;
        config.format = format;
        config.present_mode = wgpu::PresentMode::Fifo;
        config.desired_maximum_frame_latency = 2;
        let session = GpuSession::from_gpu(gpu)?;
        let pipeline = session.with_gpu(|gpu| {
            gpu.stats.surface_format = Some(format!("{format:?}"));
            Ok(create_pipeline(gpu, format))
        })?;
        Ok(Self {
            surface,
            session,
            pipeline,
            config,
            size: None,
        })
    }
    pub(crate) fn present(
        &mut self,
        frame: &RenderedFrame,
        width: u32,
        height: u32,
    ) -> Result<bool> {
        if width == 0 || height == 0 {
            return Ok(false);
        }
        if u64::from(width) * u64::from(height) > 16_777_216 || width > 8192 || height > 8192 {
            bail!("WINDOW_SURFACE_LIMIT");
        }
        let FramePixels::Gpu(frame) = &frame.pixels else {
            bail!("GPU_SURFACE_CPU_FRAME");
        };
        if !self.session.same_device(&frame.session) {
            bail!("GPU_SESSION_MISMATCH");
        }
        let session = self.session.clone();
        session.with_gpu(|gpu| {
            if self.size != Some((width, height)) {
                self.configure(gpu, width, height)?;
            }
            for attempt in 0..2 {
                let texture = match super::native_call::call(&gpu.status, || {
                    self.surface
                        .get_current_texture()
                        .map(|texture| NativeSurfaceFrame {
                            texture: Some(texture),
                            status: gpu.status.clone(),
                        })
                })? {
                    Ok(texture) => texture,
                    Err(error) => {
                        if recoverable(error, attempt)? {
                            self.configure(gpu, width, height)?;
                            continue;
                        }
                        return Ok(false);
                    }
                };
                let view = texture.texture().create_view(&Default::default());
                draw(
                    gpu,
                    &self.pipeline,
                    frame,
                    &view,
                    width,
                    height,
                    self.config.format.is_srgb(),
                )?;
                texture.present()?;
                gpu.stats.surface_presentations += 1;
                return Ok(true);
            }
            Ok(false)
        })
    }
    fn configure(&mut self, gpu: &mut Gpu, width: u32, height: u32) -> Result<()> {
        self.config.width = width;
        self.config.height = height;
        super::gpu::push_error_scopes(&gpu.device);
        let configured = super::native_call::call(&gpu.status, || {
            self.surface.configure(&gpu.device, &self.config)
        });
        super::gpu::collect_error_scopes(&gpu.device, &gpu.status);
        configured?;
        gpu.poll_device()?;
        gpu.status.check()?;
        self.size = Some((width, height));
        gpu.stats.surface_reconfigurations += 1;
        Ok(())
    }
}
fn choose_format(formats: &[wgpu::TextureFormat]) -> Result<wgpu::TextureFormat> {
    [
        wgpu::TextureFormat::Bgra8Unorm,
        wgpu::TextureFormat::Rgba8Unorm,
        wgpu::TextureFormat::Bgra8UnormSrgb,
        wgpu::TextureFormat::Rgba8UnormSrgb,
    ]
    .into_iter()
    .find(|f| formats.contains(f))
    .context("GPU_SURFACE_FORMAT: requires supported 8-bit UNORM or sRGB target")
}
fn recoverable(error: wgpu::SurfaceError, attempt: usize) -> Result<bool> {
    match error {
        wgpu::SurfaceError::Lost | wgpu::SurfaceError::Outdated => Ok(attempt == 0),
        wgpu::SurfaceError::Timeout => Ok(false),
        other => bail!("GPU_SURFACE: {other}"),
    }
}
fn create_pipeline(gpu: &Gpu, format: wgpu::TextureFormat) -> wgpu::RenderPipeline {
    let shader = gpu
        .device
        .create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("GPU frame presentation"),
            source: wgpu::ShaderSource::Wgsl(include_str!("present.wgsl").into()),
        });
    gpu.device
        .create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("Direct GPU surface"),
            layout: None,
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs"),
                compilation_options: Default::default(),
                buffers: &[],
            },
            primitive: Default::default(),
            depth_stencil: None,
            multisample: Default::default(),
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs"),
                compilation_options: Default::default(),
                targets: &[Some(wgpu::ColorTargetState {
                    format,
                    blend: None,
                    write_mask: wgpu::ColorWrites::ALL,
                })],
            }),
            multiview: None,
            cache: None,
        })
}
fn draw(
    gpu: &mut Gpu,
    pipeline: &wgpu::RenderPipeline,
    frame: &GpuFrame,
    view: &wgpu::TextureView,
    width: u32,
    height: u32,
    srgb: bool,
) -> Result<()> {
    let params = [
        frame.width as u32,
        frame.height as u32,
        width,
        height,
        u32::from(srgb),
        0,
        0,
        0,
    ]
    .map(u32::to_le);
    let uniform = gpu.allocate_buffer(&wgpu::BufferDescriptor {
        label: Some("presentation dimensions"),
        size: 32,
        usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    })?;
    gpu.queue
        .write_buffer(&uniform, 0, bytemuck::cast_slice(&params));
    gpu.stats.presentation_upload_bytes += 32;
    let binding = gpu.device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: None,
        layout: &pipeline.get_bind_group_layout(0),
        entries: &[
            wgpu::BindGroupEntry {
                binding: 0,
                resource: frame.buffer.as_entire_binding(),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: uniform.as_entire_binding(),
            },
        ],
    });
    let mut encoder = gpu.device.create_command_encoder(&Default::default());
    {
        let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
            label: Some("GPU frame to surface"),
            color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                view,
                resolve_target: None,
                depth_slice: None,
                ops: wgpu::Operations {
                    load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                    store: wgpu::StoreOp::Store,
                },
            })],
            depth_stencil_attachment: None,
            timestamp_writes: None,
            occlusion_query_set: None,
        });
        pass.set_pipeline(pipeline);
        pass.set_bind_group(0, &binding, &[]);
        pass.draw(0..3, 0..1);
    }
    gpu.queue.submit([encoder.finish()]);
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_verified_color_formats_are_selected() {
        use wgpu::TextureFormat::*;
        assert_eq!(
            choose_format(&[Bgra8UnormSrgb, Rgba8Unorm]).unwrap(),
            Rgba8Unorm
        );
        assert_eq!(choose_format(&[Bgra8UnormSrgb]).unwrap(), Bgra8UnormSrgb);
        assert!(choose_format(&[Rgba16Float]).is_err());
        assert!(choose_format(&[]).is_err());
    }
    #[test]
    fn surface_recovery_is_bounded_and_timeout_does_not_present() {
        assert!(recoverable(wgpu::SurfaceError::Lost, 0).unwrap());
        assert!(!recoverable(wgpu::SurfaceError::Lost, 1).unwrap());
        assert!(recoverable(wgpu::SurfaceError::Outdated, 0).unwrap());
        assert!(!recoverable(wgpu::SurfaceError::Timeout, 0).unwrap());
        assert!(recoverable(wgpu::SurfaceError::OutOfMemory, 0).is_err());
        assert!(recoverable(wgpu::SurfaceError::Other, 0).is_err());
    }
}

#[cfg(test)]
#[path = "presenter_tests.rs"]
mod hardware_tests;
