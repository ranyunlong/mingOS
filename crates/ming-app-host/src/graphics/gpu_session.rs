use super::{
    Frame, GpuInfo, GpuStats, Image, RenderedFrame,
    backend::BackendStatus,
    gpu::{self, Gpu},
};
use anyhow::{Context, Result};
use std::{
    collections::BTreeMap,
    fmt,
    sync::{Arc, Mutex, MutexGuard},
};
#[derive(Clone)]
pub struct GpuSession {
    inner: Arc<Mutex<Gpu>>,
    // One error-scope stack and driver identity per device; always lock before inner.
    driver: Arc<Mutex<()>>,
    pub(super) info: GpuInfo,
    pub(super) status: BackendStatus,
}
impl fmt::Debug for GpuSession {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("GpuSession")
            .field("adapter", &self.info.name)
            .finish_non_exhaustive()
    }
}
impl GpuSession {
    pub fn new() -> Result<Self> {
        let status = BackendStatus::default();
        Self::from_gpu(Gpu::new(status)?)
    }
    pub(super) fn from_gpu(gpu: Gpu) -> Result<Self> {
        Ok(Self {
            info: gpu.info.clone(),
            status: gpu.status.clone(),
            inner: Arc::new(Mutex::new(gpu)),
            driver: Arc::new(Mutex::new(())),
        })
    }
    /// Create an independent application render context on this device.
    /// `Clone` remains an alias of the current context, including its caches and statistics.
    pub fn fork_render_context(&self) -> Result<Self> {
        let _driver = self.lock_driver()?;
        let gpu = self.lock_inner()?;
        let fork = gpu.fork_render_context();
        Ok(Self {
            info: fork.info.clone(),
            status: fork.status.clone(),
            inner: Arc::new(Mutex::new(fork)),
            driver: self.driver.clone(),
        })
    }
    fn lock_driver(&self) -> Result<MutexGuard<'_, ()>> {
        self.status.check()?;
        let driver = self.driver.lock().map_err(|_| {
            self.status.record("GPU_DRIVER_MUTEX_POISONED".into());
            anyhow::anyhow!("GPU_DRIVER_MUTEX_POISONED")
        })?;
        self.status.check()?;
        Ok(driver)
    }
    fn lock_inner(&self) -> Result<MutexGuard<'_, Gpu>> {
        self.inner.lock().map_err(|_| {
            self.status.record("GPU_MUTEX_POISONED".into());
            anyhow::anyhow!("GPU_MUTEX_POISONED")
        })
    }
    pub(super) fn with_gpu<T>(&self, call: impl FnOnce(&mut Gpu) -> Result<T>) -> Result<T> {
        let _driver = self.lock_driver()?;
        let mut gpu = self.lock_inner()?;
        self.status.check()?;
        gpu::push_error_scopes(&gpu.device);
        let result = call(&mut gpu);
        gpu::collect_error_scopes(&gpu.device, &self.status);
        if let Err(e) = &result {
            self.status.record(format!("{e:#}"));
        }
        self.status.check()?;
        result
    }
    pub(super) fn render(
        &self,
        frame: &Frame,
        images: &BTreeMap<i32, Image>,
        sequence: u64,
        direct: bool,
    ) -> Result<RenderedFrame> {
        self.render_prepared(
            frame,
            images,
            sequence,
            direct,
            #[cfg(feature = "text")]
            None,
            &super::shadow_frame::PreparedShadows::default(),
            usize::MAX,
        )
    }
    #[cfg(feature = "text")]
    pub(super) fn render_text(
        &self,
        frame: &Frame,
        images: &BTreeMap<i32, Image>,
        sequence: u64,
        direct: bool,
        text: &crate::text::frame::PreparedTextFrame,
    ) -> Result<RenderedFrame> {
        self.render_prepared(
            frame,
            images,
            sequence,
            direct,
            Some(text),
            &super::shadow_frame::PreparedShadows::default(),
            usize::MAX,
        )
    }
    #[allow(
        clippy::too_many_arguments,
        reason = "Keeps prepared text, shadows and admission budget explicit across feature combinations"
    )]
    pub(super) fn render_prepared(
        &self,
        frame: &Frame,
        images: &BTreeMap<i32, Image>,
        sequence: u64,
        direct: bool,
        #[cfg(feature = "text")] text: Option<&crate::text::frame::PreparedTextFrame>,
        shadows: &super::shadow_frame::PreparedShadows,
        cold_budget: usize,
    ) -> Result<RenderedFrame> {
        if frame.width * frame.height * frame.commands.len().max(1) > 64_000_000 {
            anyhow::bail!("GPU_CANDIDATE_WORK_LIMIT");
        }
        let _driver = self.lock_driver()?;
        let mut gpu = self.lock_inner()?;
        gpu.render(
            frame,
            images,
            sequence,
            self,
            direct,
            #[cfg(feature = "text")]
            text,
            shadows,
            cold_budget,
        )
    }
    pub fn stats(&self) -> Result<GpuStats> {
        let _driver = self.lock_driver()?;
        let gpu = self.lock_inner()?;
        // Device-loss callbacks are delivered by polling, including when no new frame arrives.
        gpu.poll_device()?;
        Ok(gpu.stats.clone())
    }
    pub(super) fn release_image(&self, id: i32) {
        let Ok(_driver) = self.lock_driver() else {
            return;
        };
        if let Ok(mut gpu) = self.lock_inner() {
            gpu.release_image(id);
        }
    }
    pub(super) fn same_device(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.driver, &other.driver)
    }
}
#[derive(Debug)]
pub struct GpuFrame {
    pub(super) session: GpuSession,
    pub(super) buffer: wgpu::Buffer,
    pub(super) width: usize,
    pub(super) height: usize,
}
impl GpuFrame {
    pub fn read_pixels(&self) -> Result<Arc<Vec<u32>>> {
        self.session
            .with_gpu(|gpu| gpu.readback(&self.buffer, self.width, self.height))
            .context("GPU_SNAPSHOT")
            .map(Arc::new)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[ignore = "requires hardware GPU; device destruction must close the session"]
    fn device_loss_rejects_rendering_and_snapshots_without_replacing_first_error() {
        let session = GpuSession::new().unwrap();
        let mut graphics = super::super::Graphics::with_gpu_session(session.clone());
        let bytes: Vec<u8> = [0x4d474632i32, 1, 1, 1, 0, 0]
            .into_iter()
            .flat_map(i32::to_le_bytes)
            .collect();
        graphics.submit(&bytes).unwrap();
        let rendered = graphics.last_frame().unwrap();
        let _ = session.with_gpu(|gpu| {
            gpu.device.destroy();
            Ok(())
        });
        assert!(
            session.stats().is_err(),
            "final statistics must deliver idle device loss"
        );
        let first = session
            .status
            .check()
            .expect_err("device loss must latch")
            .to_string();
        assert!(first.contains("GPU_DEVICE_LOST"), "{first}");
        assert!(graphics.submit(&bytes).is_err());
        assert!(rendered.read_pixels().is_err());
        session.status.record("second failure".into());
        assert_eq!(session.status.check().unwrap_err().to_string(), first);
    }
}

#[cfg(all(test, feature = "text"))]
mod shadow_tests {
    use super::*;
    use crate::graphics::{
        frame::Command, shadow_frame::PreparedShadows, shadow_mask::generate_mask,
        shadow_service::Styles,
    };

    fn scene(width: usize, height: usize, commands: &[(i32, [i32; 7])]) -> Frame {
        Frame {
            width,
            height,
            commands: commands
                .iter()
                .map(|&(op, a)| Command {
                    op,
                    a,
                    text: if op == 3 { "A".into() } else { String::new() },
                })
                .collect(),
        }
    }
    fn draw(
        session: &GpuSession,
        frame: &Frame,
        prepared: &PreparedShadows,
        images: &BTreeMap<i32, Image>,
        sequence: u64,
        budget: usize,
    ) -> Result<RenderedFrame> {
        session.render_prepared(
            frame,
            images,
            sequence,
            true,
            #[cfg(feature = "text")]
            None,
            prepared,
            budget,
        )
    }
    fn compare(
        frame: &Frame,
        prepared: &PreparedShadows,
        images: &BTreeMap<i32, Image>,
        rendered: &RenderedFrame,
    ) {
        let masks = prepared
            .keys
            .iter()
            .map(|&key| (key, Arc::new(generate_mask(key))))
            .collect();
        let expected = crate::graphics::raster::render_prepared(
            frame,
            images,
            1,
            #[cfg(feature = "text")]
            None,
            prepared,
            &masks,
        )
        .unwrap();
        assert_eq!(
            rendered.read_pixels().unwrap(),
            expected.read_pixels().unwrap()
        );
    }
    #[test]
    #[ignore = "requires hardware GPU; validates full scene pixels and direct output"]
    fn shadow_gpu_scene_matches_cpu_for_all_radii_clips_and_painter_order() {
        let session = GpuSession::new().unwrap();
        println!(
            "scene hardware: {}",
            serde_json::to_string(&session.info).unwrap()
        );
        let images = BTreeMap::from([(
            1,
            Image {
                width: 2,
                height: 1,
                pixels: vec![0x809944cc, 0xff218877],
            },
        )]);
        let mut frames = 0usize;
        let mut pixel_count = 0usize;
        // Legacy scenes must work on a fresh device before any shadow cache exists.
        let legacy = scene(2, 1, &[(1, [0, 0, 2, 1, 0xff123456u32 as i32, 0, 0])]);
        let baseline = session.render(&legacy, &BTreeMap::new(), 0, true).unwrap();
        assert!(baseline.cpu_pixels().is_none());
        assert_eq!(session.stats().unwrap().readback_bytes, 0);
        assert_eq!(&**baseline.read_pixels().unwrap(), &[0xff123456; 2]);
        for radius in 0..=64 {
            for blur in [0, 1, 8, 16] {
                let mut styles = Styles::default();
                let style = styles.create(radius, blur);
                let hidden = styles.create(64, 16);
                let w = (2 * radius + 3).max(3);
                let h = (2 * radius - 1).max(1);
                let frame = scene(
                    211,
                    197,
                    &[
                        (1, [0, 0, 211, 197, 0xff17293bu32 as i32, 0, 0]),
                        (7, [style, 19, 17, w, h, 0x98e79b4fu32 as i32, 0]),
                        (6, [21, 19, w, h, 0x70335599, radius, 0]),
                        (4, [12, 11, 100, 113, 0, 0, 0]),
                        (7, [style, -3, -7, 1_000_000, 999_999, 0x403377cc, 0]),
                        (2, [1, 17, 15, 63, 47, 0, 0]),
                        (3, [4, 3, 2, 0xfffedcbau32 as i32, 0, 1, 0]),
                        (4, [0, 0, 0, 0, 0, 0, 0]),
                        (7, [hidden, 10, 10, 183, 183, -1, 0]),
                    ],
                );
                let prepared = PreparedShadows::prepare(&frame, &styles).unwrap();
                let before = session.stats().unwrap();
                let rendered = draw(
                    &session,
                    &frame,
                    &prepared,
                    &images,
                    frames as u64 + 1,
                    usize::MAX,
                )
                .unwrap();
                assert!(rendered.cpu_pixels().is_none());
                let after = session.stats().unwrap();
                assert_eq!(after.readback_bytes, before.readback_bytes);
                assert_eq!(
                    after.shadow_parameter_upload_bytes,
                    after.shadow_cache_misses * 32
                );
                assert_eq!(after.presentation_upload_bytes, 0);
                assert_eq!(after.image_uploads, 1);
                compare(&frame, &prepared, &images, &rendered);
                frames += 1;
                pixel_count += frame.width * frame.height;
            }
        }
        // Eight simultaneously visible keys exercise separate uniforms and painter order.
        let mut styles = Styles::default();
        let mut commands = Vec::new();
        for i in 0..8 {
            let style = styles.create(i * 8, i * 2);
            commands.push((
                7,
                [
                    style,
                    5 + i * 12,
                    3 + i * 9,
                    83 + i * 2,
                    97 - i * 3,
                    (0x30000000u32 + i as u32 * 0x100a0703) as i32,
                    0,
                ],
            ));
        }
        let frame = scene(240, 180, &commands);
        let prepared = PreparedShadows::prepare(&frame, &styles).unwrap();
        assert_eq!(prepared.keys.len(), 8);
        let rendered = draw(&session, &frame, &prepared, &images, 999, usize::MAX).unwrap();
        compare(&frame, &prepared, &images, &rendered);
        println!(
            "scene_exact_frames={} scene_exact_pixels={} stats={}",
            frames + 1,
            pixel_count + 240 * 180,
            serde_json::to_string(&session.stats().unwrap()).unwrap()
        );
    }

    #[test]
    #[ignore = "requires hardware GPU; validates warm reuse, eviction and nonterminal quotas"]
    fn shadow_gpu_reuses_keys_and_quota_rejection_preserves_cache_and_old_frame() {
        let session = GpuSession::new().unwrap();
        let images = BTreeMap::new();
        let mut styles = Styles::default();
        let handle = styles.create(23, 7);
        let first = scene(
            200,
            180,
            &[(7, [handle, 13, 17, 121, 115, 0x90e79b4fu32 as i32, 0])],
        );
        let prepared = PreparedShadows::prepare(&first, &styles).unwrap();
        let key = prepared.keys[0];
        // Even the initial rejection allocates/uploads/submits nothing and does not kill the session.
        assert_eq!(
            draw(&session, &first, &prepared, &images, 1, 0)
                .unwrap_err()
                .to_string(),
            "SHADOW_WORK_LIMIT"
        );
        let empty = session.stats().unwrap();
        assert_eq!(empty.render_submissions, 0);
        assert_eq!(empty.scene_upload_bytes, 0);
        assert_eq!(empty.shadow_entries, 0);
        assert_eq!(empty.shadow_cache_misses, 0);
        let original = draw(
            &session,
            &first,
            &prepared,
            &images,
            1,
            key.generation_work(),
        )
        .unwrap();
        assert_eq!(session.stats().unwrap().readback_bytes, 0);
        let original_pixels = original.read_pixels().unwrap();
        let cold = session.stats().unwrap();
        assert_eq!(cold.shadow_parameter_upload_bytes, 32);
        assert_eq!(cold.shadow_cache_misses, 1);
        assert_eq!(cold.shadow_generation_work, key.generation_work() as u64);
        assert_eq!(
            cold.shadow_resident_bytes,
            key.patch_width() * key.patch_height() * 4
        );
        for (i, (x, y, w, h, color)) in [
            (17, 23, 121, 115, 0x50aaccff),
            (3, 9, 1000, 900, 0u32),
            (-999_975, -999_982, 1_000_000, 1_000_000, 0xffabcdef),
        ]
        .into_iter()
        .enumerate()
        {
            let frame = scene(200, 180, &[(7, [handle, x, y, w, h, color as i32, 0])]);
            let prepared = PreparedShadows::prepare(&frame, &styles).unwrap();
            assert_eq!(prepared.keys, [key]);
            let before = session.stats().unwrap();
            let rendered = draw(&session, &frame, &prepared, &images, 2 + i as u64, 0).unwrap();
            let after = session.stats().unwrap();
            assert_eq!(
                after.shadow_parameter_upload_bytes,
                cold.shadow_parameter_upload_bytes
            );
            assert_eq!(after.shadow_cache_misses, cold.shadow_cache_misses);
            assert_eq!(after.shadow_generation_work, cold.shadow_generation_work);
            assert_eq!(after.shadow_cache_hits, before.shadow_cache_hits + 1);
            assert_eq!(after.readback_bytes, before.readback_bytes);
            assert_eq!(
                after.scene_upload_bytes - before.scene_upload_bytes,
                (4 + 20) * 4
            );
            compare(&frame, &prepared, &images, &rendered);
        }
        // Populate all slots, then reject a ninth key without touching GPU or LRU state.
        let mut styles = Styles::default();
        let mut commands = Vec::new();
        for i in 0..8 {
            let h = styles.create(i, 1);
            commands.push((7, [h, 2 + i, 3 + i, 40, 40, -1, 0]));
        }
        let full = scene(80, 80, &commands);
        let prepared = PreparedShadows::prepare(&full, &styles).unwrap();
        draw(&session, &full, &prepared, &images, 7, usize::MAX).unwrap();
        assert_eq!(session.stats().unwrap().shadow_entries, 8);
        let mut other = Styles::default();
        let h = other.create(64, 16);
        let ninth = scene(80, 80, &[(7, [h, 10, 10, 145, 145, -1, 0])]);
        let ninth_prepared = PreparedShadows::prepare(&ninth, &other).unwrap();
        let before = session.stats().unwrap();
        assert_eq!(
            draw(&session, &ninth, &ninth_prepared, &images, 8, 0)
                .unwrap_err()
                .to_string(),
            "SHADOW_WORK_LIMIT"
        );
        let after = session.stats().unwrap();
        assert_eq!(
            serde_json::to_value(&before).unwrap(),
            serde_json::to_value(&after).unwrap()
        );
        assert_eq!(
            original.read_pixels().unwrap(),
            original_pixels,
            "previous GPU outputs are immutable"
        );
        // Every previous slot remains warm after rejection.
        let warm = draw(&session, &full, &prepared, &images, 8, 0).unwrap();
        compare(&full, &prepared, &images, &warm);
        let rendered = draw(&session, &ninth, &ninth_prepared, &images, 9, usize::MAX).unwrap();
        compare(&ninth, &ninth_prepared, &images, &rendered);
        assert_eq!(
            session.stats().unwrap().shadow_cache_evictions,
            after.shadow_cache_evictions + 1
        );
        assert_eq!(session.stats().unwrap().shadow_entries, 8);
        assert_eq!(session.stats().unwrap().presentation_upload_bytes, 0);
        println!(
            "reuse_quota_eviction_stats={}",
            serde_json::to_string(&session.stats().unwrap()).unwrap()
        );
    }
    #[cfg(feature = "text")]
    #[test]
    #[ignore = "requires hardware GPU; uses SDK4 internal Styles and shaped text APIs"]
    fn shadow_gpu_sdk4_text_images_transparency_and_warm_resize_match_cpu() {
        use crate::{graphics::Graphics, text::TextService};
        fn encode(w: i32, h: i32, commands: &[[i32; 8]]) -> Vec<u8> {
            [0x4d474632, 1, w, h, commands.len() as i32, 1]
                .into_iter()
                .chain(commands.iter().flatten().copied())
                .flat_map(i32::to_le_bytes)
                .chain(*b"A")
                .collect()
        }
        let session = GpuSession::new().unwrap();
        let mut cpu = Graphics::new();
        let mut gpu = Graphics::with_gpu_session(session.clone());
        let mut service = TextService::new().unwrap();
        let text = service.create("MingOS 阴影".as_bytes(), 1, 16 * 64, 0);
        assert!(text > 0);
        let mut png = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut png, 2, 1);
            encoder.set_color(png::ColorType::Rgba);
            encoder.set_depth(png::BitDepth::Eight);
            encoder
                .write_header()
                .unwrap()
                .write_image_data(&[255, 80, 0, 128, 0, 128, 240, 255])
                .unwrap();
        }
        for g in [&mut cpu, &mut gpu] {
            assert_eq!(g.create_shadow_style(23, 7), 0x40000001);
            assert_eq!(g.create_shadow_style(64, 16), 0x40000002);
            assert_eq!(g.open_png(&png).unwrap(), 1);
        }
        let mut commands = [
            [1, 0, 0, 256, 160, 0xff17293bu32 as i32, 0, 0],
            [7, 0x40000001, 11, 13, 153, 127, 0, 0], // Visible alpha zero must still generate.
            [6, 17, 19, 147, 121, 0xfff0e9ddu32 as i32, 23, 0],
            [5, text, 24 * 64, 82 * 64, 0xff123456u32 as i32, 0, 0, 0],
            [2, 1, 11, 14, 63, 47, 0, 0],
            [7, 0x40000001, 45, 63, 89, 85, 0x80e79b4fu32 as i32, 0],
            [3, 4, 3, 2, 0xfffedcbau32 as i32, 0, 1, 0],
            [4, 0, 0, 0, 0, 0, 0, 0],
            [7, 0x40000002, 5, 5, 193, 193, -1, 0], // No slot for a fully clipped key.
        ];
        let mut glyph_bytes = None;
        for i in 0..3 {
            if i > 0 {
                commands[1][2] += 7;
                commands[1][3] += 3;
                commands[1][4] = 1000;
                commands[1][5] = 900;
                commands[1][6] = 0x70aaccff;
                commands[3][2] += 8 * 64;
            }
            let bytes = encode(256, 160, &commands);
            cpu.submit_with_sdk(&bytes, Some(&mut service), 4).unwrap();
            let before = session.stats().unwrap();
            gpu.submit_with_sdk(&bytes, Some(&mut service), 4).unwrap();
            let after = session.stats().unwrap();
            assert_eq!(after.readback_bytes, before.readback_bytes);
            assert_eq!(
                after.shadow_parameter_upload_bytes,
                after.shadow_cache_misses * 32
            );
            assert_eq!(after.shadow_cache_misses, 1);
            assert_eq!(after.shadow_entries, 1);
            assert_eq!(after.shadow_cache_hits, i);
            assert_eq!(after.image_uploads, 1);
            assert_eq!(after.presentation_upload_bytes, 0);
            if let Some(bytes) = glyph_bytes {
                assert_eq!(after.glyph_upload_bytes, bytes);
            }
            assert!(after.glyph_uploads > 0);
            glyph_bytes = Some(after.glyph_upload_bytes);
            let rendered = gpu.last_frame().unwrap();
            assert!(rendered.cpu_pixels().is_none());
            assert_eq!(
                rendered.read_pixels().unwrap(),
                cpu.last_frame().unwrap().read_pixels().unwrap()
            );
        }
        let old = gpu.last_frame().unwrap();
        let old_pixels = old.read_pixels().unwrap();
        assert!(gpu.close_shadow_style(0x40000001));
        let before = session.stats().unwrap();
        assert_eq!(
            gpu.submit_with_sdk(&encode(256, 160, &commands), Some(&mut service), 4)
                .unwrap_err()
                .to_string(),
            "SHADOW_HANDLE_INVALID"
        );
        assert_eq!(gpu.last_frame().unwrap().sequence, old.sequence);
        assert_eq!(gpu.last_frame().unwrap().read_pixels().unwrap(), old_pixels);
        assert_eq!(
            session.stats().unwrap().render_submissions,
            before.render_submissions
        );
        assert!(session.stats().unwrap().shadow_resident_bytes <= 2 * 1024 * 1024);
        println!(
            "sdk4_text_exact_frames=3 sdk4_text_exact_pixels={} stats={}",
            3 * 256 * 160,
            serde_json::to_string(&session.stats().unwrap()).unwrap()
        );
    }

    #[cfg(feature = "text")]
    #[test]
    #[ignore = "requires hardware GPU; checks combined 32M admission through Graphics"]
    fn shadow_gpu_combined_cold_quota_rejects_then_warm_frame_recovers() {
        use crate::graphics::Graphics;
        fn encode(w: i32, h: i32, commands: &[[i32; 8]]) -> Vec<u8> {
            [0x4d474632, 1, w, h, commands.len() as i32, 0]
                .into_iter()
                .chain(commands.iter().flatten().copied())
                .flat_map(i32::to_le_bytes)
                .collect()
        }
        let session = GpuSession::new().unwrap();
        let mut graphics = Graphics::with_gpu_session(session.clone());
        let style = graphics.create_shadow_style(64, 16);
        let small = encode(96, 96, &[[1, 0, 0, 96, 96, 0xff17293bu32 as i32, 0, 0]]);
        graphics.submit_with_sdk(&small, None, 4).unwrap();
        let previous = graphics.last_frame().unwrap();
        let pixels = previous.read_pixels().unwrap();
        let mut commands = vec![[1, 0, 0, 4096, 1024, 0xff17293bu32 as i32, 0, 0]];
        commands.extend([[7, style, 0, 0, 1_000_000, 1_000_000, 0, 0]; 6]);
        let large = encode(4096, 1024, &commands);
        let before = session.stats().unwrap();
        assert_eq!(
            graphics
                .submit_with_sdk(&large, None, 4)
                .unwrap_err()
                .to_string(),
            "SHADOW_WORK_LIMIT"
        );
        assert_eq!(graphics.frames_submitted(), 1);
        assert_eq!(graphics.last_frame().unwrap().sequence, previous.sequence);
        assert_eq!(
            serde_json::to_value(session.stats().unwrap()).unwrap(),
            serde_json::to_value(&before).unwrap()
        );
        assert_eq!(previous.read_pixels().unwrap(), pixels);
        // Same key on a small frame is admissible, including a visible alpha-zero draw.
        graphics
            .submit_with_sdk(
                &encode(96, 96, &[[7, style, 0, 0, 1_000_000, 1_000_000, 0, 0]]),
                None,
                4,
            )
            .unwrap();
        let cold = session.stats().unwrap();
        assert_eq!(cold.shadow_cache_misses, 1);
        assert_eq!(cold.shadow_parameter_upload_bytes, 32);
        graphics.submit_with_sdk(&large, None, 4).unwrap();
        let warm = session.stats().unwrap();
        assert_eq!(warm.shadow_cache_misses, 1);
        assert_eq!(warm.shadow_cache_hits, 1);
        assert_eq!(warm.shadow_generation_work, cold.shadow_generation_work);
        assert_eq!(warm.shadow_parameter_upload_bytes, 32);
        assert_eq!(warm.readback_bytes, cold.readback_bytes);
        assert_eq!(graphics.frames_submitted(), 3);
        assert!(graphics.last_frame().unwrap().cpu_pixels().is_none());
        println!(
            "combined_quota_cold_rejected_warm_recovered={} stats={}",
            4096 * 1024,
            serde_json::to_string(&warm).unwrap()
        );
    }
}

#[cfg(test)]
mod context_tests {
    use super::*;
    use std::sync::{
        Barrier,
        atomic::{AtomicUsize, Ordering},
    };

    #[test]
    #[ignore = "requires hardware GPU; tests device identity without creating a Surface"]
    fn fork_identity_is_shared_but_context_and_counters_are_fresh() {
        let root = GpuSession::new().unwrap();
        let alias = root.clone();
        let fork = root.fork_render_context().unwrap();
        let other = GpuSession::new().unwrap();
        assert!(Arc::ptr_eq(&root.inner, &alias.inner));
        assert!(!Arc::ptr_eq(&root.inner, &fork.inner));
        assert!(Arc::ptr_eq(&root.driver, &fork.driver));
        assert!(!Arc::ptr_eq(&root.driver, &other.driver));
        assert!(root.same_device(&alias));
        assert!(root.same_device(&fork));
        assert!(!root.same_device(&other));
        assert_eq!(
            serde_json::to_value(fork.stats().unwrap()).unwrap(),
            serde_json::to_value(GpuStats::default()).unwrap()
        );
    }

    #[test]
    #[ignore = "requires hardware GPU; concurrent same-device error scopes must not interleave"]
    fn shared_driver_serializes_context_error_scope_operations() {
        let root = GpuSession::new().unwrap();
        let contexts = [
            root.fork_render_context().unwrap(),
            root.fork_render_context().unwrap(),
        ];
        let start = Arc::new(Barrier::new(3));
        let active = Arc::new(AtomicUsize::new(0));
        let maximum = Arc::new(AtomicUsize::new(0));
        let workers: Vec<_> = contexts
            .into_iter()
            .map(|context| {
                let start = start.clone();
                let active = active.clone();
                let maximum = maximum.clone();
                std::thread::spawn(move || {
                    start.wait();
                    for _ in 0..24 {
                        context
                            .with_gpu(|gpu| {
                                let current = active.fetch_add(1, Ordering::SeqCst) + 1;
                                maximum.fetch_max(current, Ordering::SeqCst);
                                gpu.device.push_error_scope(wgpu::ErrorFilter::Validation);
                                let buffer = gpu.device.create_buffer(&wgpu::BufferDescriptor {
                                    label: Some("fork scope serialization test"),
                                    size: 4,
                                    usage: wgpu::BufferUsages::STORAGE,
                                    mapped_at_creation: false,
                                });
                                std::thread::yield_now();
                                drop(buffer);
                                assert!(pollster::block_on(gpu.device.pop_error_scope()).is_none());
                                active.fetch_sub(1, Ordering::SeqCst);
                                Ok(())
                            })
                            .unwrap();
                    }
                    context.stats().unwrap();
                })
            })
            .collect();
        start.wait();
        for worker in workers {
            worker.join().unwrap();
        }
        assert_eq!(maximum.load(Ordering::SeqCst), 1);
        assert_eq!(active.load(Ordering::SeqCst), 0);
        root.stats().unwrap();
    }

    #[test]
    #[ignore = "requires hardware GPU; poisoned driver must prevent any further scope operations"]
    fn driver_poison_is_terminal_for_every_fork() {
        let root = GpuSession::new().unwrap();
        let fork = root.fork_render_context().unwrap();
        let unwind = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _: Result<()> = root.with_gpu(|_| panic!("injected scoped operation panic"));
        }));
        assert!(unwind.is_err());
        assert!(root.driver.is_poisoned());
        assert!(
            fork.stats()
                .unwrap_err()
                .to_string()
                .contains("GPU_DRIVER_MUTEX_POISONED")
        );
        let called = std::cell::Cell::new(false);
        assert!(
            fork.with_gpu(|_| {
                called.set(true);
                Ok(())
            })
            .is_err()
        );
        assert!(!called.get());
        assert!(root.fork_render_context().is_err());
        let first = root.status.check().unwrap_err().to_string();
        fork.status.record("second failure".into());
        assert_eq!(fork.status.check().unwrap_err().to_string(), first);
    }

    #[test]
    #[ignore = "requires hardware GPU; inner poison is detected before creating device scopes"]
    fn context_poison_latches_shared_failure_without_poisoning_driver() {
        let root = GpuSession::new().unwrap();
        let fork = root.fork_render_context().unwrap();
        let unwind = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _inner = root.inner.lock().unwrap();
            panic!("injected context state panic");
        }));
        assert!(unwind.is_err());
        assert!(
            root.stats()
                .unwrap_err()
                .to_string()
                .contains("GPU_MUTEX_POISONED")
        );
        assert!(!root.driver.is_poisoned());
        let called = std::cell::Cell::new(false);
        assert!(
            fork.with_gpu(|_| {
                called.set(true);
                Ok(())
            })
            .is_err()
        );
        assert!(!called.get());
    }

    #[test]
    #[ignore = "requires hardware GPU; actual device destruction must invalidate all contexts and old frames"]
    fn device_loss_reaches_forks_and_saved_outputs() {
        let root = GpuSession::new().unwrap();
        let fork = root.fork_render_context().unwrap();
        let independent = GpuSession::new().unwrap();
        let mut graphics = super::super::Graphics::with_gpu_session(fork.clone());
        let bytes: Vec<u8> = [0x4d474632i32, 1, 1, 1, 0, 0]
            .into_iter()
            .flat_map(i32::to_le_bytes)
            .collect();
        graphics.submit(&bytes).unwrap();
        let saved = graphics.last_frame().unwrap();
        let _ = root.with_gpu(|gpu| {
            gpu.device.destroy();
            Ok(())
        });
        assert!(fork.stats().is_err());
        let first = root.status.check().unwrap_err().to_string();
        assert!(first.contains("GPU_DEVICE_LOST"), "{first}");
        assert!(saved.read_pixels().is_err());
        assert!(graphics.submit(&bytes).is_err());
        assert!(root.fork_render_context().is_err());
        assert_eq!(fork.status.check().unwrap_err().to_string(), first);
        independent.stats().unwrap();
    }

    #[cfg(feature = "text")]
    #[test]
    #[ignore = "requires hardware GPU; warmed glyph and shadow state must not be inherited by a fork"]
    fn fork_of_warm_context_has_independent_glyph_and_shadow_residency() {
        let root = GpuSession::new().unwrap();
        let mut graphics = super::super::Graphics::with_gpu_session(root.clone());
        let mut cpu = super::super::Graphics::new();
        let mut service = crate::text::TextService::new().unwrap();
        let text = service.create(b"AB", 1, 16 * 64, 0);
        assert!(text > 0);
        let style = graphics.create_shadow_style(4, 2);
        assert_eq!(cpu.create_shadow_style(4, 2), style);
        let commands = [
            [1, 0, 0, 64, 64, 0xff203040u32 as i32, 0, 0],
            [7, style, 8, 8, 40, 40, 0x80304050u32 as i32, 0],
            [5, text, 4 * 64, 28 * 64, -1, 0, 0, 0],
        ];
        let bytes: Vec<u8> = [0x4d474632, 1, 64, 64, commands.len() as i32, 0]
            .into_iter()
            .chain(commands.into_iter().flatten())
            .flat_map(i32::to_le_bytes)
            .collect();
        graphics
            .submit_with_sdk(&bytes, Some(&mut service), 4)
            .unwrap();
        let warm = root.stats().unwrap();
        assert!(warm.glyph_uploads > 0);
        assert_eq!(warm.shadow_cache_misses, 1);
        let fork = root.fork_render_context().unwrap();
        assert_eq!(
            serde_json::to_value(fork.stats().unwrap()).unwrap(),
            serde_json::to_value(GpuStats::default()).unwrap()
        );
        let mut child = super::super::Graphics::with_gpu_session(fork.clone());
        assert_eq!(child.create_shadow_style(4, 2), style);
        child
            .submit_with_sdk(&bytes, Some(&mut service), 4)
            .unwrap();
        let first = fork.stats().unwrap();
        assert!(first.glyph_uploads > 0);
        assert_eq!(first.shadow_cache_misses, 1);
        assert_eq!(first.readback_bytes, 0);
        assert_eq!(
            serde_json::to_value(root.stats().unwrap()).unwrap(),
            serde_json::to_value(&warm).unwrap()
        );
        cpu.submit_with_sdk(&bytes, Some(&mut service), 4).unwrap();
        assert_eq!(
            child.last_frame().unwrap().read_pixels().unwrap(),
            cpu.last_frame().unwrap().read_pixels().unwrap()
        );
        child
            .submit_with_sdk(&bytes, Some(&mut service), 4)
            .unwrap();
        let second = fork.stats().unwrap();
        assert_eq!(second.glyph_uploads, first.glyph_uploads);
        assert_eq!(second.shadow_cache_misses, 1);
        assert_eq!(second.shadow_cache_hits, 1);
        assert_eq!(
            serde_json::to_value(root.stats().unwrap()).unwrap(),
            serde_json::to_value(warm).unwrap()
        );
    }
}
