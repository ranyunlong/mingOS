use ming_app_host::graphics::compositor::{Compositor, Layer, Rect};
use ming_app_host::graphics::{FramePixels, RenderedFrame};
use std::sync::Arc;

fn frame(width: usize, height: usize, pixels: Vec<u32>) -> RenderedFrame {
    RenderedFrame {
        width,
        height,
        pixels: FramePixels::Cpu(Arc::new(pixels)),
        sequence: 7,
        render_us: 0,
        text_prepare_us: None,
        gpu_timing: None,
        commands: 0,
    }
}
fn rect(x: i32, y: i32, width: i32, height: i32) -> Rect {
    Rect {
        x,
        y,
        width,
        height,
    }
}
fn layer(frame: &RenderedFrame, destination: Rect, clip: Rect, opacity: u8) -> Layer<'_> {
    Layer {
        frame,
        destination,
        clip,
        opacity,
    }
}
#[test]
fn empty_desktop_and_successful_sequences_are_deterministic() {
    let mut c = Compositor::cpu();
    let a = c.compose(3, 2, 0xff112233, &[]).unwrap();
    assert_eq!(a.cpu_pixels().unwrap(), &[0xff112233; 6]);
    assert_eq!((a.width, a.height, a.sequence), (3, 2, 1));
    assert_eq!(c.compose(1, 1, 0xff000000, &[]).unwrap().sequence, 2);
    assert_eq!(a.cpu_pixels().unwrap(), &[0xff112233; 6]);
}
#[test]
fn z_order_opacity_and_source_alpha_have_known_pixels() {
    let red = frame(1, 1, vec![0xffff0000]);
    let blue = frame(1, 1, vec![0xff0000ff]);
    let half = frame(1, 1, vec![0x8000ff00]);
    let r = rect(0, 0, 1, 1);
    let mut c = Compositor::cpu();
    let a = c
        .compose(
            1,
            1,
            0xff000000,
            &[layer(&red, r, r, 255), layer(&blue, r, r, 128)],
        )
        .unwrap();
    assert_eq!(a.cpu_pixels().unwrap(), &[0xff7f0080]);
    let b = c
        .compose(
            1,
            1,
            0xff000000,
            &[layer(&blue, r, r, 255), layer(&red, r, r, 255)],
        )
        .unwrap();
    assert_eq!(b.cpu_pixels().unwrap(), &[0xffff0000]);
    let d = c
        .compose(1, 1, 0xff000000, &[layer(&half, r, r, 128)])
        .unwrap();
    assert_eq!(d.cpu_pixels().unwrap(), &[0xff004000]);
}
#[test]
fn scaling_negative_origin_and_half_open_clip_use_source_coordinates() {
    let p = frame(2, 2, vec![0xffff0000, 0xff00ff00, 0xff0000ff, 0xffffffff]);
    let mut c = Compositor::cpu();
    let a = c
        .compose(
            4,
            3,
            0xff121212,
            &[layer(&p, rect(-1, -1, 4, 4), rect(1, 0, 2, 2), 255)],
        )
        .unwrap();
    assert_eq!(
        a.cpu_pixels().unwrap(),
        &[
            0xff121212, 0xff00ff00, 0xff00ff00, 0xff121212, 0xff121212, 0xffffffff, 0xffffffff,
            0xff121212, 0xff121212, 0xff121212, 0xff121212, 0xff121212,
        ]
    );
}
#[test]
fn downscaling_and_invisible_layers_do_not_rebase_sampling() {
    let p = frame(4, 1, vec![0xffff0000, 0xff00ff00, 0xff0000ff, 0xffffffff]);
    let mut c = Compositor::cpu();
    let a = c
        .compose(
            2,
            1,
            0xff000000,
            &[layer(&p, rect(0, 0, 2, 1), rect(-100, 0, 102, 1), 255)],
        )
        .unwrap();
    assert_eq!(a.cpu_pixels().unwrap(), &[0xffff0000, 0xff0000ff]);
    for (dest, clip, opacity) in [
        (rect(1000000, 1000000, 4, 1), rect(0, 0, 2, 1), 255),
        (rect(-1000000, -1000000, 4, 1), rect(0, 0, 2, 1), 255),
        (rect(0, 0, 2, 1), rect(0, 0, 0, 0), 255),
        (rect(0, 0, 2, 1), rect(0, 0, 2, 1), 0),
    ] {
        assert_eq!(
            c.compose(2, 1, 0xff123456, &[layer(&p, dest, clip, opacity)])
                .unwrap()
                .cpu_pixels()
                .unwrap(),
            &[0xff123456; 2]
        );
    }
}
#[test]
fn invalid_bounds_storage_and_layer_count_reject_atomically() {
    let mut c = Compositor::cpu();
    let p = frame(1, 1, vec![0xffffffff]);
    let r = rect(0, 0, 1, 1);
    for (w, h) in [(0, 1), (1, 0), (4097, 1), (4096, 4096), (usize::MAX, 2)] {
        assert!(c.compose(w, h, 0xff000000, &[]).is_err());
    }
    assert!(c.compose(1, 1, 0x00000000, &[]).is_err());
    let bad = frame(2, 1, vec![0xffffffff]);
    assert!(
        c.compose(1, 1, 0xff000000, &[layer(&bad, r, r, 255)])
            .is_err()
    );
    for destination in [
        rect(0, 0, 0, 1),
        rect(0, 0, -1, 1),
        rect(i32::MAX, 0, 1, 1),
        rect(0, 0, 4097, 1),
    ] {
        assert!(
            c.compose(1, 1, 0xff000000, &[layer(&p, destination, r, 255)])
                .is_err()
        );
    }
    assert!(
        c.compose(1, 1, 0xff000000, &[layer(&p, r, rect(0, 0, -1, 1), 255)])
            .is_err()
    );
    assert!(
        c.compose(1, 1, 0xff000000, &[layer(&p, r, r, 255); 6])
            .is_err()
    );
    assert_eq!(
        c.compose(1, 1, 0xff000000, &[layer(&p, r, r, 255); 5])
            .unwrap()
            .sequence,
        1
    );
}
#[test]
fn maximum_screen_and_old_frames_stay_bounded_and_immutable() {
    let mut c = Compositor::cpu();
    let a = c.compose(4096, 1024, 0xffabcdef, &[]).unwrap();
    assert_eq!(a.cpu_pixels().unwrap().len(), 4_194_304);
    assert_eq!(a.cpu_pixels().unwrap()[4_194_303], 0xffabcdef);
    assert_eq!(c.compose(1, 1, 0xff123456, &[]).unwrap().sequence, 2);
    assert_eq!(a.cpu_pixels().unwrap()[0], 0xffabcdef);
}

#[cfg(feature = "gpu")]
mod hardware {
    use super::*;
    use ming_app_host::graphics::{GpuSession, Graphics};

    fn hardware_device() -> GpuSession {
        let session = GpuSession::new().unwrap();
        let graphics = Graphics::with_gpu_session(session.clone());
        let info = graphics.gpu_info().unwrap();
        assert!(
            matches!(info.device_type.as_str(), "DiscreteGpu" | "IntegratedGpu"),
            "actual hardware adapter required: {info:?}"
        );
        println!("adapter={}", serde_json::to_string(info).unwrap());
        session
    }
    fn source(session: &GpuSession, width: usize, height: usize, pixels: &[u32]) -> RenderedFrame {
        let mut bytes = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut bytes, width as u32, height as u32);
            encoder.set_color(png::ColorType::Rgba);
            encoder.set_depth(png::BitDepth::Eight);
            let rgba: Vec<u8> = pixels
                .iter()
                .flat_map(|p| [(p >> 16) as u8, (p >> 8) as u8, *p as u8, (p >> 24) as u8])
                .collect();
            encoder
                .write_header()
                .unwrap()
                .write_image_data(&rgba)
                .unwrap();
        }
        let mut graphics = Graphics::with_gpu_session(session.clone());
        let handle = graphics.open_png(&bytes).unwrap();
        let words = [
            0x4d474632i32,
            1,
            width as i32,
            height as i32,
            1,
            0,
            2,
            handle,
            0,
            0,
            width as i32,
            height as i32,
            0,
            0,
        ];
        graphics
            .submit(
                &words
                    .into_iter()
                    .flat_map(i32::to_le_bytes)
                    .collect::<Vec<_>>(),
            )
            .unwrap();
        graphics.last_frame().unwrap()
    }
    fn check(
        gpu: &mut Compositor,
        cpu: &mut Compositor,
        width: usize,
        height: usize,
        gl: &[Layer<'_>],
        cl: &[Layer<'_>],
    ) -> RenderedFrame {
        let before = gpu.gpu_stats().unwrap().unwrap();
        let actual = gpu.compose(width, height, 0xff102030, gl).unwrap();
        assert!(actual.cpu_pixels().is_none());
        let after = gpu.gpu_stats().unwrap().unwrap();
        assert_eq!(after.readback_bytes, before.readback_bytes);
        assert_eq!(after.image_upload_bytes, 0);
        assert_eq!(after.presentation_upload_bytes, 0);
        assert_eq!(
            after.scene_upload_bytes - before.scene_upload_bytes,
            (16 + 48 * gl.len()) as u64
        );
        assert_eq!(after.render_submissions, before.render_submissions + 1);
        let expected = cpu.compose(width, height, 0xff102030, cl).unwrap();
        assert_eq!(
            actual.read_pixels().unwrap(),
            expected.read_pixels().unwrap()
        );
        assert_eq!(actual.sequence, expected.sequence);
        assert_eq!(
            gpu.gpu_stats().unwrap().unwrap().readback_bytes,
            after.readback_bytes + (width * height * 4) as u64
        );
        actual
    }
    #[test]
    #[ignore = "requires hardware GPU; exact multi-application composition and no implicit readback"]
    fn five_contexts_scale_clip_and_order_match_independent_cpu() {
        let root = hardware_device();
        let mut contexts = Vec::new();
        let mut gf = Vec::new();
        let mut cf = Vec::new();
        for k in 0..5 {
            let (w, h) = (k + 2, 7 - k);
            let pixels: Vec<u32> = (0..w * h)
                .map(|i| {
                    0xff000000
                        | (((i * 31 + k * 47) as u32 & 255) << 16)
                        | (((i * 17 + k * 13) as u32 & 255) << 8)
                        | ((i * 71 + k * 29) as u32 & 255)
                })
                .collect();
            let context = root.fork_render_context().unwrap();
            gf.push(source(&context, w, h, &pixels));
            cf.push(frame(w, h, pixels));
            contexts.push(context);
        }
        let before: Vec<_> = contexts.iter().map(|s| s.stats().unwrap()).collect();
        let mut gpu = Compositor::gpu(&root).unwrap();
        let mut cpu = Compositor::cpu();
        let mut seed = 739u32;
        for round in 0..64 {
            let mut gl = Vec::new();
            let mut cl = Vec::new();
            for z in 0..5 {
                seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
                let i = (z + round) % 5;
                let d = rect(
                    (seed % 17) as i32 - 7,
                    ((seed >> 8) % 11) as i32 - 4,
                    1 + ((seed >> 16) % 23) as i32,
                    1 + ((seed >> 24) % 17) as i32,
                );
                let clip = if round % 7 == 0 {
                    rect(0, 0, 0, 0)
                } else {
                    rect((z % 3) as i32, (z % 2) as i32, 13, 11)
                };
                let opacity = if round % 9 == 0 {
                    0
                } else {
                    ((seed >> 5) % 256) as u8
                };
                gl.push(layer(&gf[i], d, clip, opacity));
                cl.push(layer(&cf[i], d, clip, opacity));
            }
            check(&mut gpu, &mut cpu, 17, 13, &gl, &cl);
        }
        for (s, old) in contexts.iter().zip(before) {
            let now = s.stats().unwrap();
            assert_eq!(now.readback_bytes, 0);
            assert_eq!(now.image_uploads, old.image_uploads);
            assert_eq!(now.render_submissions, old.render_submissions);
        }
        assert_eq!(root.stats().unwrap().render_submissions, 0);
    }
    #[test]
    #[ignore = "requires hardware GPU; immutable source/output survive owner destruction"]
    fn saved_frames_survive_apps_and_compositor_without_cross_context_accounting() {
        let root = hardware_device();
        let context = root.fork_render_context().unwrap();
        let red = source(&context, 1, 1, &[0xffff0000]);
        let mut gpu = Compositor::gpu(&root).unwrap();
        let r = rect(0, 0, 2, 2);
        let a = gpu
            .compose(2, 2, 0xff000000, &[layer(&red, r, r, 255)])
            .unwrap();
        let b = gpu.compose(2, 2, 0xff0000ff, &[]).unwrap();
        assert_eq!(gpu.gpu_stats().unwrap().unwrap().readback_bytes, 0);
        assert_eq!(context.stats().unwrap().readback_bytes, 0);
        drop(red);
        drop(context);
        drop(gpu);
        drop(root);
        assert_eq!(&*a.read_pixels().unwrap(), &[0xffff0000; 4]);
        assert_eq!(&*b.read_pixels().unwrap(), &[0xff0000ff; 4]);
    }
    #[test]
    #[ignore = "requires hardware GPU; rejected inputs do not poison the shared device"]
    fn foreign_devices_wrong_backends_and_forged_sizes_reject_before_gpu_work() {
        let root = hardware_device();
        let other = GpuSession::new().unwrap();
        let mut gpu = Compositor::gpu(&root).unwrap();
        let mut cpu = Compositor::cpu();
        let foreign = source(&other, 1, 1, &[0xffff0000]);
        let local = source(&root, 1, 1, &[0xff00ff00]);
        let cpu_frame = frame(1, 1, vec![0xffffffff]);
        let r = rect(0, 0, 1, 1);
        let old = gpu
            .compose(1, 1, 0xff000000, &[layer(&local, r, r, 255)])
            .unwrap();
        let before = serde_json::to_value(gpu.gpu_stats().unwrap().unwrap()).unwrap();
        assert!(
            gpu.compose(1, 1, 0xff000000, &[layer(&foreign, r, r, 0)])
                .is_err()
        );
        assert!(
            gpu.compose(1, 1, 0xff000000, &[layer(&cpu_frame, r, r, 255)])
                .is_err()
        );
        assert!(
            cpu.compose(1, 1, 0xff000000, &[layer(&local, r, r, 255)])
                .is_err()
        );
        let mut forged = local.clone();
        forged.width = 2;
        assert!(
            gpu.compose(1, 1, 0xff000000, &[layer(&forged, r, r, 255)])
                .is_err()
        );
        assert!(
            gpu.compose(1, 1, 0xff000000, &[layer(&local, r, r, 255); 6])
                .is_err()
        );
        let mut fifth = [layer(&local, r, r, 255); 5];
        fifth[4] = layer(&foreign, rect(100, 100, 1, 1), r, 0);
        assert!(gpu.compose(1, 1, 0xff000000, &fifth).is_err());
        assert_eq!(
            serde_json::to_value(gpu.gpu_stats().unwrap().unwrap()).unwrap(),
            before
        );
        assert_eq!(
            gpu.compose(1, 1, 0xff000000, &[layer(&local, r, r, 255)])
                .unwrap()
                .sequence,
            2
        );
        assert_eq!(gpu.gpu_stats().unwrap().unwrap().readback_bytes, 0);
        assert_eq!(&*old.read_pixels().unwrap(), &[0xff00ff00]);
    }
    #[test]
    #[ignore = "requires hardware GPU; immutable frame selection follows references across replacement and z changes"]
    fn old_and_new_app_frames_never_alias_window_slots_or_frame_sequences() {
        let root = hardware_device();
        let app = root.fork_render_context().unwrap();
        let mut graphics = Graphics::with_gpu_session(app.clone());
        let solid = |color: u32| {
            [
                0x4d474632i32,
                1,
                1,
                1,
                1,
                0,
                1,
                0,
                0,
                1,
                1,
                color as i32,
                0,
                0,
            ]
            .into_iter()
            .flat_map(i32::to_le_bytes)
            .collect::<Vec<_>>()
        };
        graphics.submit(&solid(0xffff0000)).unwrap();
        let a1 = graphics.last_frame().unwrap();
        graphics.submit(&solid(0xff00ff00)).unwrap();
        let a2 = graphics.last_frame().unwrap();
        let b1 = source(&root.fork_render_context().unwrap(), 1, 1, &[0xff0000ff]);
        assert_eq!((a1.sequence, a2.sequence, b1.sequence), (1, 2, 1));
        let mut gpu = Compositor::gpu(&root).unwrap();
        let r = rect(0, 0, 1, 1);
        for (bottom, top, expected) in [
            (&a1, &b1, 0xff0000ff),
            (&b1, &a2, 0xff00ff00),
            (&a2, &a1, 0xffff0000),
            (&a1, &a2, 0xff00ff00),
        ] {
            let before = gpu.gpu_stats().unwrap().unwrap().readback_bytes;
            let out = gpu
                .compose(
                    1,
                    1,
                    0xff000000,
                    &[layer(bottom, r, r, 255), layer(top, r, r, 255)],
                )
                .unwrap();
            assert_eq!(gpu.gpu_stats().unwrap().unwrap().readback_bytes, before);
            assert_eq!(&*out.read_pixels().unwrap(), &[expected]);
        }
        assert_eq!((a1.sequence, a2.sequence, b1.sequence), (1, 2, 1));
        assert_eq!(app.stats().unwrap().readback_bytes, 0);
    }
    #[test]
    #[ignore = "requires hardware GPU; maximum legal root and five input layers"]
    fn maximum_pixel_budget_and_five_layers_are_exact() {
        let root = hardware_device();
        let context = root.fork_render_context().unwrap();
        let p = source(&context, 1, 1, &[0xffabcd12]);
        let cf = frame(1, 1, vec![0xffabcd12]);
        let mut gpu = Compositor::gpu(&root).unwrap();
        let mut cpu = Compositor::cpu();
        let r = rect(0, 0, 4096, 1024);
        let out = check(
            &mut gpu,
            &mut cpu,
            4096,
            1024,
            &[layer(&p, r, r, 47); 5],
            &[layer(&cf, r, r, 47); 5],
        );
        assert_eq!(out.width * out.height, 4_194_304);
        assert_eq!(context.stats().unwrap().readback_bytes, 0);
    }
}
