//! Hardware checks for the actual fullscreen presentation pass, without a window.

use super::*;
use crate::graphics::Graphics;
use std::{sync::mpsc, time::Duration};

// Row-major 3x2 oracle: asymmetric colors expose channel swaps, flipped axes,
// interpolation, and incorrect sRGB handling, including its low-value branch.
const GRID: [[u8; 4]; 6] = [
    [3, 10, 11, 255],
    [19, 83, 147, 255],
    [230, 127, 42, 255],
    [255, 0, 73, 255],
    [8, 201, 164, 255],
    [64, 32, 254, 255],
];

fn color_grid_frame() -> Vec<u8> {
    let mut words = vec![0x4d474632_i32, 1, 3, 2, 6, 0];
    for (index, &[r, g, b, a]) in GRID.iter().enumerate() {
        // SDK v2 rectangle: opcode, x, y, width, height, ARGB, reserved, reserved.
        words.extend_from_slice(&[
            1,
            (index % 3) as i32,
            (index / 3) as i32,
            1,
            1,
            u32::from_be_bytes([a, r, g, b]) as i32,
            0,
            0,
        ]);
    }
    words.into_iter().flat_map(i32::to_le_bytes).collect()
}

#[test]
#[ignore = "requires hardware GPU; unavailable hardware is a failure when explicitly run"]
fn hardware_presenter_scales_and_preserves_color_for_unorm_and_srgb_targets() -> Result<()> {
    let session = GpuSession::new().context("hardware GPU required for presenter test")?;
    let mut graphics = Graphics::with_gpu_session(session.clone());
    graphics.submit(&color_grid_frame())?;
    let rendered = graphics.last_frame().context("missing direct GPU frame")?;
    assert!(rendered.cpu_pixels().is_none());
    let FramePixels::Gpu(frame) = &rendered.pixels else {
        panic!("direct presentation must use FramePixels::Gpu");
    };
    assert_eq!((frame.width, frame.height), (3, 2));
    assert!(frame.session.same_device(&session));
    assert_eq!(session.stats()?.readback_bytes, 0);

    for format in [
        wgpu::TextureFormat::Rgba8Unorm,
        wgpu::TextureFormat::Rgba8UnormSrgb,
        wgpu::TextureFormat::Bgra8Unorm,
        wgpu::TextureFormat::Bgra8UnormSrgb,
    ] {
        // Uneven enlargement and mixed reduction/enlargement catch coordinate
        // rounding and accidental use of logical dimensions for the viewport.
        for (width, height) in [(7_u32, 5_u32), (2, 9)] {
            let bytes = session
                .with_gpu(|gpu| {
                    assert!(
                        matches!(
                            gpu.adapter.get_info().device_type,
                            wgpu::DeviceType::DiscreteGpu | wgpu::DeviceType::IntegratedGpu
                        ),
                        "presenter test requires an actual hardware adapter"
                    );
                    draw_and_inspect(gpu, frame, format, width, height)
                })
                .with_context(|| format!("offscreen presenter {format:?} at {width}x{height}"))?;
            assert_eq!(bytes.len(), (width * height * 4) as usize);

            for y in 0..height {
                for x in 0..width {
                    let offset = ((y * width + x) * 4) as usize;
                    let mut actual: [u8; 4] = bytes[offset..offset + 4].try_into()?;
                    if matches!(
                        format,
                        wgpu::TextureFormat::Bgra8Unorm | wgpu::TextureFormat::Bgra8UnormSrgb
                    ) {
                        actual.swap(0, 2);
                    }
                    let expected = GRID[((y * 2 / height) * 3 + x * 3 / width) as usize];
                    for channel in 0..4 {
                        // sRGB decode/encode may round RGB by one byte across
                        // hardware implementations. Alpha and UNORM stay exact.
                        let tolerance = u8::from(format.is_srgb() && channel < 3);
                        assert!(
                            actual[channel].abs_diff(expected[channel]) <= tolerance,
                            "{format:?} {width}x{height} at ({x},{y}), channel {channel}: \
                             actual {actual:?}, expected {expected:?}, tolerance {tolerance}"
                        );
                    }
                }
            }
        }
    }
    Ok(())
}

fn draw_and_inspect(
    gpu: &mut Gpu,
    frame: &GpuFrame,
    format: wgpu::TextureFormat,
    width: u32,
    height: u32,
) -> Result<Vec<u8>> {
    let extent = wgpu::Extent3d {
        width,
        height,
        depth_or_array_layers: 1,
    };
    let target = gpu.device.create_texture(&wgpu::TextureDescriptor {
        label: Some("presenter hardware test target"),
        size: extent,
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format,
        usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
        view_formats: &[],
    });
    let view = target.create_view(&Default::default());
    let pipeline = create_pipeline(gpu, format);
    draw(
        gpu,
        &pipeline,
        frame,
        &view,
        width,
        height,
        format.is_srgb(),
    )?;
    assert_eq!(
        gpu.stats.readback_bytes, 0,
        "presentation must not read back"
    );

    // Explicit test-only inspection. Each tiny target row uses a 256-byte pitch;
    // padding is discarded before comparison and never treated as pixel data.
    let row_pitch = wgpu::COPY_BYTES_PER_ROW_ALIGNMENT;
    assert!(width * 4 <= row_pitch);
    let staging = gpu.allocate_buffer(&wgpu::BufferDescriptor {
        label: Some("presenter hardware test readback"),
        size: u64::from(row_pitch) * u64::from(height),
        usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    })?;
    let mut encoder = gpu.device.create_command_encoder(&Default::default());
    encoder.copy_texture_to_buffer(
        wgpu::TexelCopyTextureInfo {
            texture: &target,
            mip_level: 0,
            origin: wgpu::Origin3d::ZERO,
            aspect: wgpu::TextureAspect::All,
        },
        wgpu::TexelCopyBufferInfo {
            buffer: &staging,
            layout: wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(row_pitch),
                rows_per_image: Some(height),
            },
        },
        extent,
    );
    let submission = gpu.queue.submit([encoder.finish()]);
    let (sender, receiver) = mpsc::sync_channel(1);
    staging
        .slice(..)
        .map_async(wgpu::MapMode::Read, move |result| {
            let _ = sender.send(result);
        });
    gpu.device
        .poll(wgpu::PollType::Wait {
            submission_index: Some(submission),
            timeout: Some(Duration::from_secs(2)),
        })
        .context("presenter test readback GPU wait")?;
    receiver
        .recv_timeout(Duration::from_secs(2))
        .context("presenter test map callback timeout")?
        .context("presenter test buffer mapping")?;
    let pixels = {
        let mapped = staging.slice(..).get_mapped_range();
        mapped
            .chunks_exact(row_pitch as usize)
            .take(height as usize)
            .flat_map(|row| row[..(width * 4) as usize].iter().copied())
            .collect()
    };
    staging.unmap();
    Ok(pixels)
}

#[test]
#[ignore = "requires hardware GPU; verifies composed fork frames use the existing presenter"]
fn hardware_composed_frames_present_without_source_or_compositor_readback() -> Result<()> {
    use crate::graphics::compositor::{Compositor, Layer, Rect};
    let session = GpuSession::new()?;
    let app = session.fork_render_context()?;
    let mut graphics = Graphics::with_gpu_session(app.clone());
    graphics.submit(&color_grid_frame())?;
    let source = graphics.last_frame().unwrap();
    let mut compositor = Compositor::gpu(&session)?;
    let rect = Rect {
        x: 0,
        y: 0,
        width: 3,
        height: 2,
    };
    let composed = compositor.compose(
        3,
        2,
        0xff000000,
        &[Layer {
            frame: &source,
            destination: rect,
            clip: rect,
            opacity: 255,
        }],
    )?;
    let FramePixels::Gpu(frame) = &composed.pixels else {
        panic!("GPU output required");
    };
    assert!(session.same_device(&frame.session));
    for format in [
        wgpu::TextureFormat::Rgba8Unorm,
        wgpu::TextureFormat::Bgra8Unorm,
    ] {
        let bytes = session.with_gpu(|gpu| {
            assert!(matches!(
                gpu.adapter.get_info().device_type,
                wgpu::DeviceType::DiscreteGpu | wgpu::DeviceType::IntegratedGpu
            ));
            draw_and_inspect(gpu, frame, format, 7, 5)
        })?;
        for y in 0..5 {
            for x in 0..7 {
                let mut expected = GRID[(y * 2 / 5) * 3 + x * 3 / 7];
                if format == wgpu::TextureFormat::Bgra8Unorm {
                    expected.swap(0, 2);
                }
                assert_eq!(&bytes[(y * 7 + x) * 4..(y * 7 + x + 1) * 4], &expected);
            }
        }
    }
    assert_eq!(app.stats()?.readback_bytes, 0);
    assert_eq!(compositor.gpu_stats()?.unwrap().readback_bytes, 0);
    Ok(())
}
