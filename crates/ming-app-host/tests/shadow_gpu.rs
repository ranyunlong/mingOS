#![cfg(feature = "gpu")]
// Include the production modules so canonical GPU tests do not need public style APIs.
#[path = "../src/graphics/aa.rs"]
mod aa;
#[path = "../src/graphics/shadow_gpu.rs"]
mod shadow_gpu;
#[path = "../src/graphics/shadow_mask.rs"]
mod shadow_mask;
use shadow_gpu::ShadowGpu;
use shadow_mask::{ShadowKey, generate_mask};

#[test]
#[ignore = "requires hardware GPU; every canonical A8 value must match the CPU oracle"]
fn three_gpu_passes_match_every_canonical_pixel_with_batched_immutable_uniforms() {
    let instance = wgpu::Instance::new(&wgpu::InstanceDescriptor {
        backends: if cfg!(windows) {
            wgpu::Backends::DX12
        } else {
            wgpu::Backends::VULKAN
        },
        ..Default::default()
    });
    let adapter = instance
        .enumerate_adapters(wgpu::Backends::all())
        .into_iter()
        .find(|a| {
            matches!(
                a.get_info().device_type,
                wgpu::DeviceType::DiscreteGpu | wgpu::DeviceType::IntegratedGpu
            )
        })
        .expect("hardware GPU required");
    println!("canonical hardware: {:?}", adapter.get_info());
    let (device, queue) =
        pollster::block_on(adapter.request_device(&wgpu::DeviceDescriptor::default())).unwrap();
    device.push_error_scope(wgpu::ErrorFilter::Validation);
    let gpu = ShadowGpu::new(&device);
    assert_eq!(gpu.cache.size(), 2 * 1024 * 1024);
    let mut keys = Vec::new();
    for radius in 0..=64 {
        for blur in [0, 1, 8, 16] {
            keys.push(
                ShadowKey::new(
                    2 * (radius + blur) + 3,
                    2 * (radius + blur) + 4,
                    radius,
                    blur,
                )
                .unwrap(),
            );
        }
    }
    for blur in 0..=16 {
        for (w, h, r) in [
            (1, 2, 64),
            (2, 1, 64),
            (3, 7, 64),
            (64, 31, 64),
            (71, 1000, 17),
            (1000, 73, 17),
            (1_000_000, 999_999, 64),
        ] {
            keys.push(ShadowKey::new(w, h, r, blur).unwrap());
        }
    }
    let staging = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("explicit test-only canonical readback"),
        size: shadow_gpu::CACHE_BYTES,
        usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });
    let mut pixels = 0usize;
    for batch in keys.chunks(8) {
        let misses: Vec<_> = batch
            .iter()
            .enumerate()
            .map(|(i, &key)| (key, i as u32 * 65536))
            .collect();
        let mut encoder = device.create_command_encoder(&Default::default());
        gpu.encode(&device, &queue, &mut encoder, &misses);
        encoder.copy_buffer_to_buffer(&gpu.cache, 0, &staging, 0, shadow_gpu::CACHE_BYTES);
        let index = queue.submit([encoder.finish()]);
        let (send, recv) = std::sync::mpsc::channel();
        staging
            .slice(..)
            .map_async(wgpu::MapMode::Read, move |r| send.send(r).unwrap());
        device
            .poll(wgpu::PollType::Wait {
                submission_index: Some(index),
                timeout: Some(std::time::Duration::from_secs(15)),
            })
            .unwrap();
        recv.recv_timeout(std::time::Duration::from_secs(15))
            .unwrap()
            .unwrap();
        let mapped = staging.slice(..).get_mapped_range();
        for (key, offset) in &misses {
            let expected = generate_mask(*key);
            for (i, &alpha) in expected.iter().enumerate() {
                let b = (*offset as usize + i) * 4;
                let got = u32::from_le_bytes(mapped[b..b + 4].try_into().unwrap());
                assert_eq!(
                    got,
                    u32::from(alpha),
                    "{key:?}, canonical pixel {i}, slot {offset}"
                );
            }
            pixels += expected.len();
        }
        drop(mapped);
        staging.unmap();
    }
    assert!(pollster::block_on(device.pop_error_scope()).is_none());
    println!(
        "canonical_exact_cases={} canonical_exact_pixels={pixels} scratch_bytes={} uniform_bytes_per_miss=32",
        keys.len(),
        2 * 256 * 256 * 4
    );
}
