use super::{
    FramePixels, GpuFrame, GpuSession, GpuStats, GpuTiming, RenderedFrame, compositor::Layer,
    gpu::Gpu,
};
use anyhow::{Result, bail};
use std::{sync::Arc, time::Instant};

pub(super) struct GpuCompositor {
    session: GpuSession,
    pipeline: wgpu::ComputePipeline,
    dummy: wgpu::Buffer,
}
impl GpuCompositor {
    pub(super) fn new(root: &GpuSession) -> Result<Self> {
        let session = root.fork_render_context()?;
        let (pipeline, dummy) = session.with_gpu(|gpu| {
            let status = gpu.status.clone();
            super::native_call::call(&status, || {
                let shader = gpu
                    .device
                    .create_shader_module(wgpu::ShaderModuleDescriptor {
                        label: Some("Ming multi-window compositor"),
                        source: wgpu::ShaderSource::Wgsl(include_str!("compositor.wgsl").into()),
                    });
                let pipeline =
                    gpu.device
                        .create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
                            label: Some("Ming bounded window composition"),
                            layout: None,
                            module: &shader,
                            entry_point: Some("main"),
                            compilation_options: Default::default(),
                            cache: None,
                        });
                let dummy = gpu.allocate_buffer(&wgpu::BufferDescriptor {
                    label: Some("Empty compositor source"),
                    size: 4,
                    usage: wgpu::BufferUsages::STORAGE,
                    mapped_at_creation: false,
                })?;
                Ok((pipeline, dummy))
            })?
        })?;
        Ok(Self {
            session,
            pipeline,
            dummy,
        })
    }
    pub(super) fn check_source(&self, source: &RenderedFrame) -> Result<()> {
        let FramePixels::Gpu(source) = &source.pixels else {
            bail!("COMPOSITOR_GPU_FRAME_REQUIRED");
        };
        if !self.session.same_device(&source.session) {
            bail!("GPU_SESSION_MISMATCH");
        }
        Ok(())
    }
    pub(super) fn stats(&self) -> Result<GpuStats> {
        self.session.stats()
    }
    pub(super) fn compose(
        &self,
        width: usize,
        height: usize,
        background: u32,
        layers: &[Layer<'_>],
        sequence: u64,
    ) -> Result<RenderedFrame> {
        // Reject caller errors before the terminal GPU error boundary.
        for layer in layers {
            self.check_source(layer.frame)?;
        }
        let start = Instant::now();
        let mut parameters = vec![width as u32, height as u32, background, layers.len() as u32];
        for layer in layers {
            let d = layer.destination;
            let c = layer.clip;
            parameters.extend_from_slice(&[
                d.x as u32,
                d.y as u32,
                d.width as u32,
                d.height as u32,
                layer.frame.width as u32,
                layer.frame.height as u32,
                c.x as u32,
                c.y as u32,
                (c.x + c.width) as u32,
                (c.y + c.height) as u32,
                u32::from(layer.opacity),
                0,
            ]);
        }
        let pack_micros = start.elapsed().as_micros();
        let execute = Instant::now();
        let buffer = self.session.with_gpu(|gpu| {
            let status = gpu.status.clone();
            super::native_call::call(&status, || {
                self.execute(gpu, width, height, layers, &parameters)
            })?
        })?;
        Ok(RenderedFrame {
            width,
            height,
            sequence,
            commands: layers.len(),
            text_prepare_us: None,
            render_us: start.elapsed().as_micros(),
            gpu_timing: Some(GpuTiming {
                pack_micros,
                execute_readback_micros: execute.elapsed().as_micros(),
            }),
            pixels: FramePixels::Gpu(Arc::new(GpuFrame {
                session: self.session.clone(),
                buffer,
                width,
                height,
            })),
        })
    }
    fn execute(
        &self,
        gpu: &mut Gpu,
        width: usize,
        height: usize,
        layers: &[Layer<'_>],
        parameters: &[u32],
    ) -> Result<wgpu::Buffer> {
        let input = gpu.allocate_buffer(&wgpu::BufferDescriptor {
            label: Some("Compositor layout parameters"),
            size: std::mem::size_of_val(parameters) as u64,
            usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        })?;
        gpu.queue
            .write_buffer(&input, 0, bytemuck::cast_slice(parameters));
        let output = gpu.allocate_buffer(&wgpu::BufferDescriptor {
            label: Some("GPU resident composed desktop"),
            size: (width * height * 4) as u64,
            usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC,
            mapped_at_creation: false,
        })?;
        let mut entries = vec![
            wgpu::BindGroupEntry {
                binding: 0,
                resource: input.as_entire_binding(),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: output.as_entire_binding(),
            },
        ];
        for index in 0..5 {
            let source = layers
                .get(index)
                .map(|layer| {
                    let FramePixels::Gpu(frame) = &layer.frame.pixels else {
                        unreachable!("validated GPU source")
                    };
                    &frame.buffer
                })
                .unwrap_or(&self.dummy);
            entries.push(wgpu::BindGroupEntry {
                binding: 2 + index as u32,
                resource: source.as_entire_binding(),
            });
        }
        let bindings = gpu.device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("Independent app output buffers"),
            layout: &self.pipeline.get_bind_group_layout(0),
            entries: &entries,
        });
        let mut encoder = gpu.device.create_command_encoder(&Default::default());
        {
            let mut pass = encoder.begin_compute_pass(&wgpu::ComputePassDescriptor {
                label: Some("Compose windows"),
                timestamp_writes: None,
            });
            pass.set_pipeline(&self.pipeline);
            pass.set_bind_group(0, &bindings, &[]);
            pass.dispatch_workgroups((width as u32).div_ceil(8), (height as u32).div_ceil(8), 1);
        }
        let submission = gpu.queue.submit([encoder.finish()]);
        gpu.stats.render_submissions += 1;
        gpu.stats.scene_upload_bytes += std::mem::size_of_val(parameters) as u64;
        gpu.wait(submission)?;
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graphics::compositor::{Compositor, Layer, Rect};
    #[test]
    #[ignore = "requires hardware GPU; checks raw source alpha and layer opacity rounding"]
    fn hardware_source_alpha_and_layer_opacity_match_cpu_for_edge_values() -> Result<()> {
        let root = GpuSession::new()?;
        assert!(matches!(
            root.info.device_type.as_str(),
            "DiscreteGpu" | "IntegratedGpu"
        ));
        let pixels: Vec<u32> = [0, 1, 2, 127, 128, 254, 255]
            .into_iter()
            .map(|alpha| (alpha << 24) | 0x00e37119)
            .collect();
        let source_buffer = root.with_gpu(|gpu| {
            let buffer = gpu.allocate_buffer(&wgpu::BufferDescriptor {
                label: Some("Test-only raw alpha fixture"),
                size: 28,
                usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
                mapped_at_creation: false,
            })?;
            gpu.queue
                .write_buffer(&buffer, 0, bytemuck::cast_slice(&pixels));
            Ok(buffer)
        })?;
        let source = RenderedFrame {
            width: 7,
            height: 1,
            sequence: 1,
            render_us: 0,
            text_prepare_us: None,
            gpu_timing: None,
            commands: 0,
            pixels: FramePixels::Gpu(Arc::new(GpuFrame {
                session: root.clone(),
                buffer: source_buffer,
                width: 7,
                height: 1,
            })),
        };
        let cpu_source = RenderedFrame {
            pixels: FramePixels::Cpu(Arc::new(pixels)),
            ..source.clone()
        };
        let mut gpu = Compositor::gpu(&root)?;
        let mut cpu = Compositor::cpu();
        let rect = Rect {
            x: 0,
            y: 0,
            width: 7,
            height: 1,
        };
        for opacity in [0, 1, 2, 127, 128, 254, 255] {
            let before = gpu.gpu_stats()?.unwrap().readback_bytes;
            let actual = gpu.compose(
                7,
                1,
                0xff1739ad,
                &[Layer {
                    frame: &source,
                    destination: rect,
                    clip: rect,
                    opacity,
                }],
            )?;
            assert_eq!(gpu.gpu_stats()?.unwrap().readback_bytes, before);
            let expected = cpu.compose(
                7,
                1,
                0xff1739ad,
                &[Layer {
                    frame: &cpu_source,
                    destination: rect,
                    clip: rect,
                    opacity,
                }],
            )?;
            let actual_pixels = actual.read_pixels()?;
            assert_eq!(actual_pixels, expected.read_pixels()?);
            assert_eq!(actual_pixels[0], 0xff1739ad);
            if opacity == 128 {
                assert_eq!(actual_pixels[1], 0xff1839ac);
            }
            if opacity == 255 {
                assert_eq!(actual_pixels[6], 0xffe37119);
            }
        }
        assert_eq!(root.stats()?.readback_bytes, 0);
        Ok(())
    }
}
