//! GPU post-processing of a GPU-resident content-only root. Metadata upload only.
use super::desktop_shell::{self as shell, ShellApp, ShellAppState, ShellWindow, StoragePrompt};
use super::{FramePixels, GpuFrame, GpuSession, GpuStats, GpuTiming, RenderedFrame, gpu::Gpu};
use anyhow::{Result, bail};
use std::{sync::Arc, time::Instant};

pub(super) struct GpuDesktopShell {
    session: GpuSession,
    pipeline: wgpu::ComputePipeline,
}
impl GpuDesktopShell {
    pub(super) fn new(root: &GpuSession) -> Result<Self> {
        let session = root.fork_render_context()?;
        let pipeline = session.with_gpu(|gpu| {
            let status = gpu.status.clone();
            super::native_call::call(&status, || {
                let module = gpu
                    .device
                    .create_shader_module(wgpu::ShaderModuleDescriptor {
                        label: Some("Trusted desktop shell"),
                        source: wgpu::ShaderSource::Wgsl(include_str!("desktop_shell.wgsl").into()),
                    });
                Ok(gpu
                    .device
                    .create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
                        label: Some("Ming shell chrome and Dock"),
                        layout: None,
                        module: &module,
                        entry_point: Some("main"),
                        compilation_options: Default::default(),
                        cache: None,
                    }))
            })?
        })?;
        Ok(Self { session, pipeline })
    }
    pub(super) fn stats(&self) -> Result<GpuStats> {
        self.session.stats()
    }
    pub(super) fn draw(
        &self,
        root: &RenderedFrame,
        windows: &[ShellWindow],
        apps: &[ShellApp],
        prompt: Option<&StoragePrompt>,
    ) -> Result<RenderedFrame> {
        // All shape/identity validation ran in DesktopShell::draw. Backend and
        // device checks must also precede with_gpu's terminal error boundary.
        let FramePixels::Gpu(source) = &root.pixels else {
            bail!("DESKTOP_SHELL_GPU_FRAME_REQUIRED");
        };
        if !self.session.same_device(&source.session) {
            bail!("GPU_SESSION_MISMATCH");
        }
        let start = Instant::now();
        let parameters = pack(root, windows, apps, prompt);
        let pack_micros = start.elapsed().as_micros();
        let execute = Instant::now();
        let buffer = self.session.with_gpu(|gpu| {
            let status = gpu.status.clone();
            super::native_call::call(&status, || self.execute(gpu, source, &parameters))?
        })?;
        // Do not reenter either session inside with_gpu: driver -> inner lock.
        Ok(RenderedFrame {
            pixels: FramePixels::Gpu(Arc::new(GpuFrame {
                session: self.session.clone(),
                buffer,
                width: root.width,
                height: root.height,
            })),
            render_us: root.render_us.saturating_add(start.elapsed().as_micros()),
            gpu_timing: Some(GpuTiming {
                pack_micros,
                execute_readback_micros: execute.elapsed().as_micros(),
            }),
            ..root.clone()
        })
    }
    fn execute(
        &self,
        gpu: &mut Gpu,
        source: &GpuFrame,
        parameters: &[u32],
    ) -> Result<wgpu::Buffer> {
        let metadata = gpu.allocate_buffer(&wgpu::BufferDescriptor {
            label: Some("Shell geometry and ASCII glyph metadata"),
            size: std::mem::size_of_val(parameters) as u64,
            usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        })?;
        gpu.queue
            .write_buffer(&metadata, 0, bytemuck::cast_slice(parameters));
        let output = gpu.allocate_buffer(&wgpu::BufferDescriptor {
            label: Some("GPU resident trusted shell output"),
            size: (source.width * source.height * 4) as u64,
            usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC,
            mapped_at_creation: false,
        })?;
        let bindings = gpu.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("Root buffer and trusted shell metadata"),
            layout: &self.pipeline.get_bind_group_layout(0),
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: metadata.as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: source.buffer.as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 2,
                    resource: output.as_entire_binding(),
                },
            ],
        });
        let mut encoder = gpu.device.create_command_encoder(&Default::default());
        {
            let mut pass = encoder.begin_compute_pass(&wgpu::ComputePassDescriptor {
                label: Some("Trusted shell postprocess"),
                timestamp_writes: None,
            });
            pass.set_pipeline(&self.pipeline);
            pass.set_bind_group(0, &bindings, &[]);
            pass.dispatch_workgroups(
                (source.width as u32).div_ceil(8),
                (source.height as u32).div_ceil(8),
                1,
            );
        }
        let submission = gpu.queue.submit([encoder.finish()]);
        gpu.stats.render_submissions += 1;
        gpu.stats.scene_upload_bytes += std::mem::size_of_val(parameters) as u64;
        gpu.wait(submission)?;
        Ok(output)
    }
}

// Base layout: header[4], windows[4][36], apps[4][16], ASCII[95][7].
// An optional bounded prompt extends the metadata only while visible.
// Contains only coordinates, flags, character codes and font::glyph rows.
fn pack(root: &RenderedFrame, windows: &[ShellWindow], apps: &[ShellApp], prompt: Option<&StoragePrompt>) -> Vec<u32> {
    let mut p = vec![0; if prompt.is_some() { 1656 } else { 877 }];
    p[..4].copy_from_slice(&[
        root.width as u32,
        root.height as u32,
        windows.len() as u32,
        apps.len() as u32,
    ]);
    for (i, w) in windows.iter().enumerate() {
        let a = shell::app_for(w, apps);
        let title = shell::ascii_title(&a.title, shell::TITLE_CHARS);
        let r = w.view.outer;
        let base = 4 + i * 36;
        p[base..base + 9].copy_from_slice(&[
            r.x as u32,
            r.y as u32,
            r.width as u32,
            r.height as u32,
            u32::from(w.view.minimized),
            u32::from(w.has_content),
            u32::from(w.view.focused),
            u32::from(a.state == ShellAppState::Failed),
            title.len() as u32,
        ]);
        for (j, c) in title.iter().enumerate() {
            p[base + 9 + j] = u32::from(*c);
        }
    }
    for (i, a) in apps.iter().enumerate() {
        let r = shell::dock_cell(root.width as i32, root.height as i32, i, apps.len());
        let title = shell::ascii_title(&a.title, shell::LABEL_CHARS);
        let active = windows
            .iter()
            .any(|w| w.view.app == a.id && w.view.focused && !w.view.minimized);
        let state = match a.state {
            ShellAppState::Closed => 0,
            ShellAppState::Starting => 1,
            ShellAppState::Running => 2,
            ShellAppState::Minimized => 3,
            ShellAppState::Failed => 4,
        };
        let base = 148 + i * 16;
        p[base..base + 5].copy_from_slice(&[
            r.x as u32,
            r.y as u32,
            state,
            u32::from(active),
            title.len() as u32,
        ]);
        for (j, c) in title.iter().enumerate() {
            p[base + 5 + j] = u32::from(*c);
        }
        p[base + 13] = u32::from(cfg!(target_os = "linux"));
    }
    for code in 32..=126u8 {
        let base = 212 + (code as usize - 32) * 7;
        for (row, bits) in super::font::glyph(code as char).into_iter().enumerate() {
            p[base + row] = u32::from(bits);
        }
    }
    if let Some(prompt) = prompt {
        let lines = prompt.lines((root.width as i32 - 16).min(600));
        p[877] = 1;
        p[878] = prompt.mode;
        p[879] = lines.len() as u32;
        for (i, line) in lines.iter().enumerate() {
            let base = 880 + i * 97;
            p[base] = line.len() as u32;
            for (j, byte) in line.iter().enumerate() { p[base + 1 + j] = u32::from(*byte); }
        }
    }
    p
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::desktop_model::{Rect, WindowId, WindowView};
    use crate::graphics::{compositor::Compositor, desktop_shell::DesktopShell};
    use anyhow::Context;
    use std::{sync::mpsc, time::Duration};

    #[test]
    fn trusted_shell_shader_parses_and_validates() {
        let module = naga::front::wgsl::parse_str(include_str!("desktop_shell.wgsl")).unwrap();
        naga::valid::Validator::new(naga::valid::ValidationFlags::all(), naga::valid::Capabilities::all())
            .validate(&module).unwrap();
    }

    #[test]
    #[ignore = "requires hardware GPU; offscreen compatibility with the production presenter shader"]
    fn hardware_shell_buffer_works_with_existing_rgba_bgra_present_shader() -> Result<()> {
        let session = GpuSession::new()?;
        assert!(matches!(
            session.info.device_type.as_str(),
            "DiscreteGpu" | "IntegratedGpu"
        ));
        let mut compositor = Compositor::gpu(&session)?;
        let root = compositor.compose(320, 240, 0xff123456, &[])?;
        let cpu_root = RenderedFrame {
            pixels: FramePixels::Cpu(Arc::new(vec![0xff123456; 320 * 240])),
            ..root.clone()
        };
        let apps = vec![
            ShellApp {
                id: 0,
                title: "Ming".into(),
                state: ShellAppState::Running,
            },
            ShellApp {
                id: 1,
                title: "Fail".into(),
                state: ShellAppState::Failed,
            },
            ShellApp {
                id: 2,
                title: "Mini".into(),
                state: ShellAppState::Minimized,
            },
            ShellApp {
                id: 3,
                title: "Closed".into(),
                state: ShellAppState::Closed,
            },
        ];
        let windows = vec![ShellWindow {
            view: WindowView {
                id: WindowId(1),
                app: 0,
                outer: Rect {
                    x: 20,
                    y: 32,
                    width: 240,
                    height: 140,
                },
                minimized: false,
                maximized: false,
                focused: true,
                input_ready: false,
                presented_sequence: 0,
            },
            has_content: true,
        }];
        let mut shell = DesktopShell::gpu(&session)?;
        let output = shell.draw(&root, &windows, &apps)?;
        let expected = DesktopShell::cpu().draw(&cpu_root, &windows, &apps)?;
        let FramePixels::Gpu(frame) = &output.pixels else {
            bail!("GPU output required");
        };
        // A third render context presents the shell buffer, exactly as the
        // native presenter does. No context is reentered while its driver is held.
        let presenter = session.fork_render_context()?;
        for format in [
            wgpu::TextureFormat::Rgba8Unorm,
            wgpu::TextureFormat::Bgra8Unorm,
        ] {
            let bytes = presenter.with_gpu(|gpu| inspect_present_shader(gpu, frame, format))?;
            for y in 0..240usize {
                for x in 0..64usize {
                    let c = expected.cpu_pixels().unwrap()[y * 320 + x * 5];
                    let mut rgba = [(c >> 16) as u8, (c >> 8) as u8, c as u8, 255];
                    if format == wgpu::TextureFormat::Bgra8Unorm {
                        rgba.swap(0, 2);
                    }
                    assert_eq!(
                        &bytes[(y * 64 + x) * 4..(y * 64 + x + 1) * 4],
                        &rgba,
                        "{format:?} at {x},{y}"
                    );
                }
            }
        }
        assert_eq!(output.sequence, root.sequence);
        assert_eq!(session.stats()?.readback_bytes, 0);
        assert_eq!(compositor.gpu_stats()?.unwrap().readback_bytes, 0);
        assert_eq!(shell.gpu_stats()?.unwrap().readback_bytes, 0);
        assert_eq!(shell.gpu_stats()?.unwrap().presentation_upload_bytes, 0);
        assert_eq!(presenter.stats()?.readback_bytes, 0);
        Ok(())
    }

    // Uses the unchanged production present.wgsl, not an alternative pixel
    // conversion shader. Texture mapping below is explicit test-only inspection.
    fn inspect_present_shader(
        gpu: &mut Gpu,
        frame: &GpuFrame,
        format: wgpu::TextureFormat,
    ) -> Result<Vec<u8>> {
        let shader = gpu
            .device
            .create_shader_module(wgpu::ShaderModuleDescriptor {
                label: Some("Existing presenter shader compatibility"),
                source: wgpu::ShaderSource::Wgsl(include_str!("present.wgsl").into()),
            });
        let pipeline = gpu
            .device
            .create_render_pipeline(&wgpu::RenderPipelineDescriptor {
                label: Some("Shell presenter compatibility"),
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
            });
        let uniform = gpu.allocate_buffer(&wgpu::BufferDescriptor {
            label: Some("Test presenter dimensions"),
            size: 32,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        })?;
        let params = [frame.width as u32, frame.height as u32, 64, 240, 0, 0, 0, 0];
        gpu.queue
            .write_buffer(&uniform, 0, bytemuck::cast_slice(&params));
        let group = gpu.device.create_bind_group(&wgpu::BindGroupDescriptor {
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
        let extent = wgpu::Extent3d {
            width: 64,
            height: 240,
            depth_or_array_layers: 1,
        };
        let texture = gpu.device.create_texture(&wgpu::TextureDescriptor {
            label: Some("Offscreen RGBA/BGRA shell target"),
            size: extent,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
            view_formats: &[],
        });
        let view = texture.create_view(&Default::default());
        let staging = gpu.allocate_buffer(&wgpu::BufferDescriptor {
            label: Some("Explicit test texture inspection"),
            size: 256 * 240,
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        })?;
        let mut encoder = gpu.device.create_command_encoder(&Default::default());
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("Production presenter shader on shell output"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
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
            pass.set_pipeline(&pipeline);
            pass.set_bind_group(0, &group, &[]);
            pass.draw(0..3, 0..1);
        }
        encoder.copy_texture_to_buffer(
            wgpu::TexelCopyTextureInfo {
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            wgpu::TexelCopyBufferInfo {
                buffer: &staging,
                layout: wgpu::TexelCopyBufferLayout {
                    offset: 0,
                    bytes_per_row: Some(256),
                    rows_per_image: Some(240),
                },
            },
            extent,
        );
        let submission = gpu.queue.submit([encoder.finish()]);
        let (tx, rx) = mpsc::sync_channel(1);
        staging
            .slice(..)
            .map_async(wgpu::MapMode::Read, move |result| {
                let _ = tx.send(result);
            });
        gpu.wait(submission)?;
        rx.recv_timeout(Duration::from_secs(2))
            .context("present test map callback")?
            .context("present test map")?;
        let bytes = staging.slice(..).get_mapped_range().to_vec();
        staging.unmap();
        Ok(bytes)
    }
}
