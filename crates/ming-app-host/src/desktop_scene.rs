//! Content-only host composition, preceding the native runtime (no window chrome).
//!
//! Apply window operations through `model_mut`, execute their effects in the host,
//! and call `sync` afterwards to promptly release closed content. Compose
//! does not open input: only call `presented(root.sequence)` after the real
//! presenter accepts that exact output. Dropped/rejected outputs need no ack.
use crate::desktop_model::{DOCK, DesktopModel, Effect, ModelError, WindowId};
use crate::graphics::{
    RenderedFrame,
    compositor::{Compositor, Layer, Rect},
};
use anyhow::{Result, anyhow, bail};
use std::collections::BTreeMap;

const BACKGROUND: u32 = 0xff182028;

/// Bounded trusted-host bridge; not a guest API or a running desktop shell.
pub struct DesktopScene {
    model: DesktopModel,
    compositor: Compositor,
    latest: BTreeMap<WindowId, RenderedFrame>,
    pending: Option<PendingPresentation>,
}
struct PendingPresentation {
    root_sequence: u64,
    root_size: (usize, usize),
    contents: Vec<PresentedContent>,
}
struct PresentedContent {
    id: WindowId,
    sequence: u64,
    size: (i32, i32),
}
fn model_error(error: ModelError) -> anyhow::Error {
    anyhow!("DESKTOP_SCENE_MODEL: {error:?}")
}
impl DesktopScene {
    pub fn new(width: i32, height: i32, compositor: Compositor) -> Result<Self> {
        Ok(Self {
            model: DesktopModel::new(width, height).map_err(model_error)?,
            compositor,
            latest: BTreeMap::new(),
            pending: None,
        })
    }

    #[cfg(feature = "gpu")]
    pub fn gpu_stats(&self) -> Result<Option<crate::graphics::GpuStats>> { self.compositor.gpu_stats() }

    pub fn model(&self) -> &DesktopModel {
        &self.model
    }

    /// Conservatively invalidates the in-flight ack before exposing any mutation.
    /// This also catches resize/hide/restore roundtrips with identical end views.
    /// Even a focus-only access requires a fresh compose before another ack.
    /// Execute returned model effects, then `sync` for immediate frame cleanup.
    /// Prefer `resize_screen` for screen resizes (invalid resizes stay atomic).
    pub fn model_mut(&mut self) -> &mut DesktopModel {
        self.sync();
        self.pending = None;
        &mut self.model
    }

    /// Replaces one window's latest immutable content. Every sequence must be
    /// nonzero and strictly newer, including when the pixel Arc is identical.
    /// Hidden windows also cache updates for restore. Validation precedes mutation;
    /// GPU storage checks do not read back pixels.
    pub fn set_frame(&mut self, id: WindowId, frame: RenderedFrame) -> Result<()> {
        let view = self.model.view(id).map_err(model_error)?;
        self.compositor.check_source(&frame)?;
        let last = self.latest.get(&id).map_or(0, |frame| frame.sequence);
        if frame.sequence == 0 || frame.sequence <= last.max(view.presented_sequence) {
            bail!("DESKTOP_SCENE_STALE_FRAME");
        }
        self.sync();
        self.latest.insert(id, frame);
        Ok(())
    }

    fn screen_size(&self) -> (usize, usize) {
        let area = self.model.workarea();
        (area.width as usize, (area.y + area.height + DOCK) as usize)
    }

    /// Scales visible latest content into `outer.content()` in model z order.
    /// Only the most recent successful composition is eligible for an ack.
    /// The output and retained latest frames own pixels; pending owns metadata.
    pub fn compose(&mut self) -> Result<RenderedFrame> {
        self.sync();
        let area = self.model.workarea();
        let size = self.screen_size();
        let mut layers = Vec::new();
        let mut contents = Vec::new();
        for view in self
            .model
            .views()
            .into_iter()
            .filter(|view| !view.minimized)
        {
            let Some(frame) = self.latest.get(&view.id) else {
                continue;
            };
            let content = view.outer.content();
            let left = content.x.max(area.x);
            let top = content.y.max(area.y);
            let right = (content.x + content.width).min(area.x + area.width);
            let bottom = (content.y + content.height).min(area.y + area.height);
            if right <= left || bottom <= top {
                continue;
            }
            layers.push(Layer {
                frame,
                destination: Rect {
                    x: content.x,
                    y: content.y,
                    width: content.width,
                    height: content.height,
                },
                clip: Rect {
                    x: left,
                    y: top,
                    width: right - left,
                    height: bottom - top,
                },
                opacity: 255,
            });
            contents.push(PresentedContent {
                id: view.id,
                sequence: frame.sequence,
                size: (frame.width as i32, frame.height as i32),
            });
        }
        let root = self
            .compositor
            .compose(size.0, size.1, BACKGROUND, &layers)?;
        self.pending = Some(PendingPresentation {
            root_sequence: root.sequence,
            root_size: (root.width, root.height),
            contents,
        });
        Ok(root)
    }

    /// Ack ONLY after a real presenter accepts the latest compose output.
    /// Root sequence is a receipt identity, never an application sequence.
    /// Unknown, superseded, invalidated, future, and repeated receipts fail.
    pub fn presented(&mut self, root_sequence: u64) -> Result<Vec<Effect>> {
        self.sync();
        let valid = self.pending.as_ref().is_some_and(|pending| {
            pending.root_sequence == root_sequence && pending.root_size == self.screen_size()
        });
        if !valid {
            bail!("DESKTOP_SCENE_STALE_ROOT");
        }
        let pending = self.pending.take().expect("checked pending receipt");
        let mut effects = Vec::new();
        for content in pending.contents {
            effects.extend(
                self.model
                    .presented(content.id, content.sequence, content.size.0, content.size.1)
                    .map_err(model_error)?,
            );
        }
        Ok(effects)
    }

    /// Successful screen changes invalidate pending, even for identical sizes.
    /// Old content may still be scaled visually, but only its original source
    /// dimensions reach the model's input gate. Return effects to the runtime.
    pub fn resize_screen(&mut self, width: i32, height: i32) -> Result<Vec<Effect>> {
        let effects = self
            .model
            .resize_screen(width, height)
            .map_err(model_error)?;
        self.pending = None;
        self.sync();
        Ok(effects)
    }

    /// Releases closed windows, retaining at most four latest frames, including
    /// hidden windows so restore never requires a new app submit. Runs on compose,
    /// presentation, model mutation access, and successful frame replacement.
    pub fn sync(&mut self) {
        let views = self.model.views();
        self.latest
            .retain(|id, _| views.iter().any(|view| view.id == *id));
        if self.pending.as_ref().is_some_and(|pending| {
            pending.root_size != self.screen_size()
                || pending.contents.iter().any(|content| {
                    !views
                        .iter()
                        .any(|view| view.id == content.id && !view.minimized)
                })
        }) {
            self.pending = None;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::desktop_model::{MAX_WINDOWS, Rect};
    use crate::graphics::FramePixels;
    use std::sync::Arc;

    fn cpu_storage(frame: &RenderedFrame) -> &Arc<Vec<u32>> {
        match &frame.pixels {
            FramePixels::Cpu(pixels) => pixels,
            #[cfg(feature = "gpu")]
            FramePixels::Gpu(_) => panic!("expected CPU test fixture"),
        }
    }
    fn scene() -> DesktopScene {
        DesktopScene::new(800, 600, Compositor::cpu()).unwrap()
    }
    fn open(s: &mut DesktopScene) -> WindowId {
        s.model_mut().open(0).unwrap().0
    }
    fn frame(width: usize, height: usize, sequence: u64, color: u32) -> RenderedFrame {
        RenderedFrame {
            width,
            height,
            sequence,
            pixels: FramePixels::Cpu(Arc::new(vec![color; width * height])),
            render_us: 0,
            text_prepare_us: None,
            gpu_timing: None,
            commands: 0,
        }
    }
    fn matching(s: &DesktopScene, id: WindowId, seq: u64, color: u32) -> RenderedFrame {
        let c = s.model().view(id).unwrap().outer.content();
        frame(c.width as usize, c.height as usize, seq, color)
    }
    fn ready(s: &DesktopScene, id: WindowId) -> bool {
        s.model().view(id).unwrap().input_ready
    }
    fn pixel(root: &RenderedFrame, x: i32, y: i32) -> u32 {
        root.cpu_pixels().unwrap()[y as usize * root.width + x as usize]
    }

    #[test]
    fn app_sequences_seven_and_eleven_are_not_root_sequence_one() {
        let mut s = scene();
        let a = open(&mut s);
        let b = open(&mut s);
        s.set_frame(a, matching(&s, a, 7, 0xffff0000)).unwrap();
        s.set_frame(b, matching(&s, b, 11, 0xff00ff00)).unwrap();
        let root = s.compose().unwrap();
        assert_eq!(root.sequence, 1);
        assert!(!ready(&s, a) && !ready(&s, b));
        let pending = s.pending.as_ref().unwrap();
        assert_eq!(pending.root_size, (800, 600));
        assert_eq!(
            pending
                .contents
                .iter()
                .map(|c| (c.id, c.sequence))
                .collect::<Vec<_>>(),
            vec![(a, 7), (b, 11)]
        );
        assert!(s.presented(root.sequence).unwrap().is_empty());
        assert!(ready(&s, a) && ready(&s, b));
        assert_eq!(s.model().view(a).unwrap().presented_sequence, 7);
        assert_eq!(s.model().view(b).unwrap().presented_sequence, 11);
        assert!(s.pending.is_none());
    }

    #[test]
    fn pending_uses_original_content_even_when_latest_is_replaced() {
        let mut s = scene();
        let id = open(&mut s);
        s.set_frame(id, matching(&s, id, 7, 0xffff0000)).unwrap();
        let root = s.compose().unwrap();
        s.set_frame(id, frame(1, 1, 11, 0xff00ff00)).unwrap();
        s.presented(root.sequence).unwrap();
        assert!(ready(&s, id));
        assert_eq!(s.model().view(id).unwrap().presented_sequence, 7);
        let newer = s.compose().unwrap();
        let effects = s.presented(newer.sequence).unwrap();
        assert!(!ready(&s, id));
        assert!(effects.contains(&Effect::ResetInput(id)));
        assert_eq!(s.model().view(id).unwrap().presented_sequence, 11);
    }

    #[test]
    fn newer_matching_latest_cannot_unlock_ack_of_old_small_source() {
        let mut s = scene();
        let id = open(&mut s);
        s.set_frame(id, frame(1, 1, 7, 0xffff0000)).unwrap();
        let root = s.compose().unwrap();
        s.set_frame(id, matching(&s, id, 11, 0xff00ff00)).unwrap();
        s.presented(root.sequence).unwrap();
        assert!(!ready(&s, id));
        assert_eq!(s.model().view(id).unwrap().presented_sequence, 7);
        let root = s.compose().unwrap();
        assert!(!ready(&s, id));
        s.presented(root.sequence).unwrap();
        assert!(ready(&s, id));
    }

    #[test]
    fn resize_invalidates_old_root_and_scaled_old_source_never_unlocks() {
        let mut s = scene();
        let id = open(&mut s);
        s.model_mut().maximize(id).unwrap();
        s.set_frame(id, matching(&s, id, 7, 0xff123456)).unwrap();
        let old = s.compose().unwrap();
        assert!(!s.resize_screen(640, 480).unwrap().is_empty());
        assert!(s.presented(old.sequence).is_err());
        assert!(!ready(&s, id));
        let scaled = s.compose().unwrap();
        assert_eq!((scaled.width, scaled.height), (640, 480));
        s.presented(scaled.sequence).unwrap();
        assert!(!ready(&s, id));
        s.set_frame(id, matching(&s, id, 11, 0xffabcdef)).unwrap();
        let root = s.compose().unwrap();
        assert!(!ready(&s, id));
        s.presented(root.sequence).unwrap();
        assert!(ready(&s, id));
    }

    #[test]
    fn resize_same_screen_and_geometry_roundtrip_invalidate_pending() {
        let mut s = scene();
        let id = open(&mut s);
        s.set_frame(id, matching(&s, id, 7, 0xff123456)).unwrap();
        let root = s.compose().unwrap();
        s.resize_screen(800, 600).unwrap();
        assert!(s.presented(root.sequence).is_err());
        assert!(!ready(&s, id));
        let root = s.compose().unwrap();
        let r = s.model().view(id).unwrap().outer;
        let m = s.model_mut();
        m.geometry(
            id,
            Rect {
                width: r.width - 20,
                ..r
            },
        )
        .unwrap();
        m.geometry(id, r).unwrap();
        assert!(s.presented(root.sequence).is_err());
        assert!(!ready(&s, id));
    }

    #[test]
    fn minimize_restore_preserves_pixels_and_needs_no_new_submit() {
        let mut s = scene();
        let id = open(&mut s);
        let f = matching(&s, id, 7, 0xff123456);
        let weak = Arc::downgrade(cpu_storage(&f));
        s.set_frame(id, f).unwrap();
        let first = s.compose().unwrap();
        s.presented(first.sequence).unwrap();
        s.model_mut().begin_content(id).unwrap();
        let old_root = s.compose().unwrap();
        let effects = s.model_mut().minimize(id).unwrap();
        assert!(effects.contains(&Effect::ResetInput(id)));
        s.sync();
        assert!(
            weak.upgrade().is_some(),
            "hidden latest must survive until restore"
        );
        assert!(s.latest.contains_key(&id));
        assert!(!ready(&s, id));
        assert!(s.model_mut().route_pointer(50, 100, false).is_none());
        assert!(s.pending.is_none());
        assert!(s.presented(old_root.sequence).is_err());
        let empty = s.compose().unwrap();
        assert_eq!(empty.commands, 0);
        s.presented(empty.sequence).unwrap();
        assert!(!ready(&s, id));
        s.model_mut().restore(id).unwrap();
        assert!(s.presented(old_root.sequence).is_err());
        let root = s.compose().unwrap();
        let content = s.model().view(id).unwrap().outer.content();
        assert_eq!(pixel(&root, content.x, content.y), 0xff123456);
        assert_eq!(root.commands, 1);
        assert!(!ready(&s, id));
        s.presented(root.sequence).unwrap();
        assert!(ready(&s, id));
        assert_eq!(s.model().view(id).unwrap().presented_sequence, 7);
    }

    #[test]
    fn minimized_windows_accept_latest_updates_without_composition_or_unlocking() {
        let mut s = scene();
        let id = open(&mut s);
        s.set_frame(id, matching(&s, id, 7, 0xffff0000)).unwrap();
        s.model_mut().minimize(id).unwrap();
        s.set_frame(id, matching(&s, id, 11, 0xff00ff00)).unwrap();
        assert!(s.set_frame(id, matching(&s, id, 7, 0xffff0000)).is_err());
        let hidden = s.compose().unwrap();
        assert_eq!(hidden.commands, 0);
        s.presented(hidden.sequence).unwrap();
        assert!(!ready(&s, id));
        s.model_mut().restore(id).unwrap();
        let root = s.compose().unwrap();
        let content = s.model().view(id).unwrap().outer.content();
        assert_eq!(pixel(&root, content.x, content.y), 0xff00ff00);
        assert!(!ready(&s, id));
        s.presented(root.sequence).unwrap();
        assert_eq!(s.model().view(id).unwrap().presented_sequence, 11);
        assert!(ready(&s, id));
    }
    #[test]
    fn minimize_restore_without_sync_still_rejects_pre_hide_ack() {
        let mut s = scene();
        let id = open(&mut s);
        s.set_frame(id, matching(&s, id, 7, 0xff123456)).unwrap();
        let root = s.compose().unwrap();
        let m = s.model_mut();
        m.minimize(id).unwrap();
        m.restore(id).unwrap();
        assert!(s.presented(root.sequence).is_err());
        assert!(!ready(&s, id));
    }

    #[test]
    fn close_prunes_frame_and_watermark_and_new_id_has_independent_sequence() {
        let mut s = scene();
        let id = open(&mut s);
        s.set_frame(id, matching(&s, id, 7, 0xff123456)).unwrap();
        let root = s.compose().unwrap();
        s.model_mut().close(id).unwrap();
        s.sync();
        assert!(s.latest.is_empty());
        assert!(s.pending.is_none());
        assert!(s.presented(root.sequence).is_err());
        assert!(s.set_frame(id, frame(1, 1, 8, 0xff123456)).is_err());
        let next = open(&mut s);
        assert_ne!(id, next);
        s.set_frame(next, matching(&s, next, 1, 0xff123456))
            .unwrap();
    }

    #[test]
    fn composition_obeys_z_order_content_destination_and_opaque_reserved_background() {
        let mut s = scene();
        let a = open(&mut s);
        let b = open(&mut s);
        let outer = Rect {
            x: 20,
            y: 40,
            width: 300,
            height: 200,
        };
        s.model_mut().geometry(a, outer).unwrap();
        s.model_mut().geometry(b, outer).unwrap();
        s.set_frame(a, matching(&s, a, 7, 0xffff0000)).unwrap();
        s.set_frame(b, matching(&s, b, 11, 0xff00ff00)).unwrap();
        let root = s.compose().unwrap();
        let c = outer.content();
        assert_eq!(pixel(&root, c.x, c.y), 0xff00ff00);
        let background = pixel(&root, 0, 0);
        assert_eq!(background >> 24, 255);
        assert_eq!(pixel(&root, outer.x, outer.y), background);
        assert_eq!(pixel(&root, c.x + c.width, c.y), background);
        assert_eq!(pixel(&root, c.x, c.y + c.height), background);
        assert_eq!(pixel(&root, 0, 599), background);
        s.model_mut().focus(Some(a)).unwrap();
        let root = s.compose().unwrap();
        assert_eq!(pixel(&root, c.x + 1, c.y + 1), 0xffff0000);
    }

    #[test]
    fn stale_duplicate_zero_and_future_acks_do_not_consume_latest_pending() {
        let mut s = scene();
        let id = open(&mut s);
        s.set_frame(id, matching(&s, id, 7, 0xff123456)).unwrap();
        assert!(s.presented(1).is_err());
        let old = s.compose().unwrap();
        let current = s.compose().unwrap();
        for seq in [0, old.sequence, current.sequence + 1] {
            assert!(s.presented(seq).is_err());
            assert!(!ready(&s, id));
            assert_eq!(s.pending.as_ref().unwrap().root_sequence, current.sequence);
        }
        s.presented(current.sequence).unwrap();
        assert!(s.presented(current.sequence).is_err());
        assert!(ready(&s, id));
    }

    #[test]
    fn set_frame_rejections_are_atomic_and_do_not_poison_sequence_or_pending() {
        let mut s = scene();
        let id = open(&mut s);
        let good = matching(&s, id, 7, 0xff123456);
        let pixels = cpu_storage(&good);
        let original = pixels.clone();
        s.set_frame(id, good).unwrap();
        let root = s.compose().unwrap();
        let mut bad_storage = matching(&s, id, 100, 0xff123456);
        bad_storage.pixels = FramePixels::Cpu(Arc::new(vec![]));
        let mut bad_width = frame(1, 1, 100, 0xff123456);
        bad_width.width = usize::MAX;
        let mut bad_area = frame(1, 1, 100, 0xff123456);
        bad_area.width = 4096;
        bad_area.height = 4096;
        let mut bad_height = frame(1, 1, 100, 0xff123456);
        bad_height.height = 4097;
        for bad in [
            frame(0, 1, 100, 0),
            bad_width,
            bad_area,
            bad_height,
            bad_storage,
            matching(&s, id, 0, 0),
            matching(&s, id, 6, 0),
            matching(&s, id, 7, 0),
        ] {
            assert!(s.set_frame(id, bad).is_err());
            assert_eq!(s.latest[&id].sequence, 7);
            let stored = cpu_storage(&s.latest[&id]);
            assert!(Arc::ptr_eq(stored, &original));
            assert_eq!(s.pending.as_ref().unwrap().root_sequence, root.sequence);
            assert!(!ready(&s, id));
        }
        assert!(
            s.set_frame(WindowId(u64::MAX), frame(1, 1, 100, 0))
                .is_err()
        );
        assert_eq!(s.latest.len(), 1);
        s.presented(root.sequence).unwrap();
        assert!(ready(&s, id));
        s.set_frame(id, matching(&s, id, 8, 0xff123456)).unwrap();
    }

    #[test]
    fn invalid_resize_preserves_pending_and_model() {
        let mut s = scene();
        let id = open(&mut s);
        s.set_frame(id, matching(&s, id, 7, 0xff123456)).unwrap();
        let root = s.compose().unwrap();
        let views = s.model().views();
        assert!(s.resize_screen(0, 600).is_err());
        assert_eq!(s.model().views(), views);
        s.presented(root.sequence).unwrap();
        assert!(ready(&s, id));
    }

    #[test]
    fn empty_scene_and_bounded_latest_and_pending_metadata() {
        assert!(DesktopScene::new(1, 1, Compositor::cpu()).is_err());
        let mut s = scene();
        let empty = s.compose().unwrap();
        assert_eq!(empty.commands, 0);
        assert!(s.presented(empty.sequence).unwrap().is_empty());
        let ids: Vec<_> = (0..MAX_WINDOWS).map(|_| open(&mut s)).collect();
        assert!(s.model_mut().open(0).is_err());
        for seq in 1..=20 {
            for &id in &ids {
                s.set_frame(id, matching(&s, id, seq, 0xff123456)).unwrap();
            }
            let _ = s.compose().unwrap();
            assert_eq!(s.latest.len(), MAX_WINDOWS);
            assert_eq!(s.pending.as_ref().unwrap().contents.len(), MAX_WINDOWS);
        }
        for id in ids {
            s.model_mut().close(id).unwrap();
        }
        s.sync();
        assert!(s.latest.is_empty());
        assert!(s.pending.is_none());
    }
    #[test]
    fn geometry_resize_invalidates_pending_and_requires_matching_source() {
        let mut s = scene();
        let id = open(&mut s);
        s.set_frame(id, matching(&s, id, 7, 0xff123456)).unwrap();
        let old = s.compose().unwrap();
        let outer = s.model().view(id).unwrap().outer;
        s.model_mut()
            .geometry(
                id,
                Rect {
                    width: outer.width - 40,
                    ..outer
                },
            )
            .unwrap();
        assert!(s.presented(old.sequence).is_err());
        let stretched = s.compose().unwrap();
        s.presented(stretched.sequence).unwrap();
        assert!(!ready(&s, id));
        s.set_frame(id, matching(&s, id, 11, 0xff123456)).unwrap();
        let root = s.compose().unwrap();
        assert!(!ready(&s, id));
        s.presented(root.sequence).unwrap();
        assert!(ready(&s, id));
    }

    #[cfg(feature = "gpu")]
    #[test]
    #[ignore = "requires hardware GPU; validates scene storage checks without GPU readback"]
    fn hardware_gpu_storage_rejection_is_atomic_and_validation_does_not_readback() {
        use crate::graphics::{GpuSession, Graphics};
        let session = GpuSession::new().unwrap();
        let mut s = DesktopScene::new(800, 600, Compositor::gpu(&session).unwrap()).unwrap();
        let id = open(&mut s);
        let content = s.model().view(id).unwrap().outer.content();
        let mut graphics = Graphics::with_gpu_session(session.clone());
        let bytes: Vec<_> = [0x4d474632i32, 1, content.width, content.height, 0, 0]
            .into_iter()
            .flat_map(i32::to_le_bytes)
            .collect();
        graphics.submit(&bytes).unwrap();
        let mut source = graphics.last_frame().unwrap();
        source.sequence = 7;
        assert!(source.cpu_pixels().is_none());
        let before = session.stats().unwrap();
        let compositor_before = s.compositor.gpu_stats().unwrap().unwrap();
        s.set_frame(id, source.clone()).unwrap();
        let mut malformed = source.clone();
        malformed.sequence = 11;
        // Same pixel count, wrong GPU dimensions: CPU length checks alone miss this.
        std::mem::swap(&mut malformed.width, &mut malformed.height);
        assert!(s.set_frame(id, malformed).is_err());
        assert_eq!(s.latest[&id].sequence, 7);
        let FramePixels::Gpu(original) = &source.pixels else {
            panic!("GPU fixture")
        };
        let FramePixels::Gpu(retained) = &s.latest[&id].pixels else {
            panic!("GPU fixture")
        };
        assert!(Arc::ptr_eq(original, retained));
        let after = session.stats().unwrap();
        let compositor_after = s.compositor.gpu_stats().unwrap().unwrap();
        assert_eq!(after.readback_bytes, before.readback_bytes);
        assert_eq!(after.render_submissions, before.render_submissions);
        assert_eq!(after.scene_upload_bytes, before.scene_upload_bytes);
        assert_eq!(
            compositor_after.readback_bytes,
            compositor_before.readback_bytes
        );
        assert_eq!(
            compositor_after.render_submissions,
            compositor_before.render_submissions
        );
        assert_eq!(
            compositor_after.scene_upload_bytes,
            compositor_before.scene_upload_bytes
        );
        let root = s.compose().unwrap();
        assert_eq!(root.sequence, 1);
        assert!(root.cpu_pixels().is_none());
        assert!(!ready(&s, id));
        assert_eq!(
            session.stats().unwrap().readback_bytes,
            before.readback_bytes
        );
        assert_eq!(
            s.compositor.gpu_stats().unwrap().unwrap().readback_bytes,
            compositor_before.readback_bytes
        );
        s.presented(root.sequence).unwrap();
        assert!(ready(&s, id));
        assert_eq!(s.model().view(id).unwrap().presented_sequence, 7);
    }
}
