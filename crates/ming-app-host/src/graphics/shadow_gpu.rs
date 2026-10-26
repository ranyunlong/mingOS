//! GPU-only canonical masks: fixed 2 MiB cache and two 256 KiB scratch buffers.
use super::shadow_mask::ShadowKey;

pub(super) const CACHE_BYTES: u64 = 8 * 256 * 256 * 4;
const SCRATCH_BYTES: u64 = 256 * 256 * 4;
pub(super) const PARAMETER_BYTES: u64 = 32;

pub(super) struct ShadowGpu {
    pub cache: wgpu::Buffer,
    scratch: [wgpu::Buffer; 2],
    layout: wgpu::BindGroupLayout,
    pipelines: [wgpu::ComputePipeline; 3],
}
impl ShadowGpu {
    // The caller encloses creation/encoding in device error scopes.
    pub fn new(device: &wgpu::Device) -> Self {
        let buffer = |label, size, usage| {
            device.create_buffer(&wgpu::BufferDescriptor {
                label: Some(label),
                size,
                usage,
                mapped_at_creation: false,
            })
        };
        let cache = buffer(
            "bounded shadow cache",
            CACHE_BYTES,
            wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC,
        );
        let scratch = std::array::from_fn(|_| {
            buffer(
                "bounded shadow scratch",
                SCRATCH_BYTES,
                wgpu::BufferUsages::STORAGE,
            )
        });
        let entries = std::array::from_fn::<_, 4, _>(|i| wgpu::BindGroupLayoutEntry {
            binding: i as u32,
            visibility: wgpu::ShaderStages::COMPUTE,
            ty: wgpu::BindingType::Buffer {
                ty: if i == 0 {
                    wgpu::BufferBindingType::Uniform
                } else {
                    wgpu::BufferBindingType::Storage { read_only: false }
                },
                has_dynamic_offset: false,
                min_binding_size: wgpu::BufferSize::new(if i == 0 { PARAMETER_BYTES } else { 4 }),
            },
            count: None,
        });
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("shadow generation bindings"),
            entries: &entries,
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("shadow generation layout"),
            bind_group_layouts: &[&layout],
            push_constant_ranges: &[],
        });
        let module = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("integer three-pass shadows"),
            source: wgpu::ShaderSource::Wgsl(include_str!("shadow.wgsl").into()),
        });
        let pipelines = ["mask", "horizontal", "vertical"].map(|entry| {
            device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
                label: Some(entry),
                layout: Some(&pipeline_layout),
                module: &module,
                entry_point: Some(entry),
                compilation_options: Default::default(),
                cache: None,
            })
        });
        Self {
            cache,
            scratch,
            layout,
            pipelines,
        }
    }

    pub fn encode(
        &self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        encoder: &mut wgpu::CommandEncoder,
        misses: &[(ShadowKey, u32)],
    ) -> u64 {
        let mut uploaded_bytes = 0;
        for &(key, offset) in misses {
            let parameters = [
                key.patch_width() as u32,
                key.patch_height() as u32,
                key.core_width() as u32,
                key.core_height() as u32,
                key.radius() as u32,
                key.blur() as u32,
                offset,
                0,
            ]
            .map(u32::to_le);
            // Never rewrite a uniform referenced by an earlier dispatch in this submission.
            let uniform = device.create_buffer(&wgpu::BufferDescriptor {
                label: Some("immutable shadow key parameters"),
                size: PARAMETER_BYTES,
                usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
                mapped_at_creation: false,
            });
            let data = bytemuck::cast_slice(&parameters);
            queue.write_buffer(&uniform, 0, data);
            uploaded_bytes += data.len() as u64;
            let buffers = [&uniform, &self.scratch[0], &self.scratch[1], &self.cache];
            let entries = std::array::from_fn::<_, 4, _>(|i| wgpu::BindGroupEntry {
                binding: i as u32,
                resource: buffers[i].as_entire_binding(),
            });
            let bindings = device.create_bind_group(&wgpu::BindGroupDescriptor {
                label: Some("one shadow key"),
                layout: &self.layout,
                entries: &entries,
            });
            // Separate ordered passes provide global dependencies, including scratch reuse.
            for pipeline in &self.pipelines {
                let mut pass = encoder.begin_compute_pass(&wgpu::ComputePassDescriptor {
                    label: Some("shadow generation stage"),
                    timestamp_writes: None,
                });
                pass.set_pipeline(pipeline);
                pass.set_bind_group(0, &bindings, &[]);
                pass.dispatch_workgroups(
                    (key.patch_width() as u32).div_ceil(8),
                    (key.patch_height() as u32).div_ceil(8),
                    1,
                );
            }
        }
        uploaded_bytes
    }
}
