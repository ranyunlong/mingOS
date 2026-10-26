//! Bounded host-owned window decisions; execution/GPU effects are separate.
use crate::ui::ViewportGate;
pub const MAX_WINDOWS: usize = 4;
pub const TITLE: i32 = 32;
pub const TOP: i32 = 24;
pub const DOCK: i32 = 64;
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct WindowId(pub u64);
/// Captured window, optional content-local position, and current button state.
pub type PointerRoute = (WindowId, Option<(i32, i32)>, bool);
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}
impl Rect {
    pub fn contains(self, x: i32, y: i32) -> bool {
        x >= self.x
            && y >= self.y
            && i64::from(x) < i64::from(self.x) + i64::from(self.width)
            && i64::from(y) < i64::from(self.y) + i64::from(self.height)
    }
    pub fn content(self) -> Self {
        Self {
            x: self.x,
            y: self.y + TITLE,
            width: self.width,
            height: self.height - TITLE,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ModelError {
    InvalidViewport,
    Limit,
    Unknown,
    Hidden,
    InvalidGeometry,
    StaleFrame,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Effect {
    Focus(Option<WindowId>),
    ResetInput(WindowId),
    Resize {
        window: WindowId,
        width: i32,
        height: i32,
    },
    Visibility {
        window: WindowId,
        visible: bool,
    },
    Close(WindowId),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WindowView {
    pub id: WindowId,
    pub app: u16,
    pub outer: Rect,
    pub minimized: bool,
    pub maximized: bool,
    pub focused: bool,
    pub input_ready: bool,
    pub presented_sequence: u64,
}
struct Window {
    id: WindowId,
    app: u16,
    outer: Rect,
    restore: Rect,
    minimized: bool,
    maximized: bool,
    gate: ViewportGate,
    presented_sequence: u64,
    presented_size: Option<(usize, usize)>,
}
pub struct DesktopModel {
    width: i32,
    height: i32,
    windows: Vec<Window>,
    z: Vec<WindowId>,
    focus: Option<WindowId>,
    capture: Option<WindowId>,
    next_id: u64,
}
impl DesktopModel {
    fn valid_screen(w: i32, h: i32) -> bool {
        (320..=4096).contains(&w)
            && (240..=4096).contains(&h)
            && i64::from(w) * i64::from(h) <= 4_194_304
    }
    pub fn new(width: i32, height: i32) -> Result<Self, ModelError> {
        if !Self::valid_screen(width, height) {
            return Err(ModelError::InvalidViewport);
        }
        Ok(Self {
            width,
            height,
            windows: Vec::new(),
            z: Vec::new(),
            focus: None,
            capture: None,
            next_id: 1,
        })
    }
    pub fn workarea(&self) -> Rect {
        Rect {
            x: 0,
            y: TOP,
            width: self.width,
            height: self.height - TOP - DOCK,
        }
    }
    fn index(&self, id: WindowId) -> Result<usize, ModelError> {
        self.windows
            .iter()
            .position(|w| w.id == id)
            .ok_or(ModelError::Unknown)
    }
    fn clamped(&self, r: Rect) -> Rect {
        let a = self.workarea();
        let w = r.width.clamp(240.min(a.width), a.width);
        let h = r.height.clamp(160.min(a.height), a.height);
        Rect {
            x: r.x.clamp(a.x, a.x + a.width - w),
            y: r.y.clamp(a.y, a.y + a.height - h),
            width: w,
            height: h,
        }
    }
    fn reset(&mut self, id: WindowId, e: &mut Vec<Effect>) {
        if self.capture == Some(id) {
            self.capture = None;
        }
        if !e.contains(&Effect::ResetInput(id)) {
            e.push(Effect::ResetInput(id));
        }
    }
    fn next_focus(&self) -> Option<WindowId> {
        self.z
            .iter()
            .rev()
            .copied()
            .find(|id| self.windows.iter().any(|w| w.id == *id && !w.minimized))
    }
    pub fn view(&self, id: WindowId) -> Result<WindowView, ModelError> {
        let w = &self.windows[self.index(id)?];
        Ok(WindowView {
            id: w.id,
            app: w.app,
            outer: w.outer,
            minimized: w.minimized,
            maximized: w.maximized,
            focused: self.focus == Some(id),
            input_ready: !w.minimized && w.gate.viewport().is_some(),
            presented_sequence: w.presented_sequence,
        })
    }
    pub fn views(&self) -> Vec<WindowView> {
        self.z
            .iter()
            .map(|&id| self.view(id).expect("owned z-order"))
            .collect()
    }
    pub fn open(&mut self, app: u16) -> Result<(WindowId, Vec<Effect>), ModelError> {
        if self.windows.len() >= MAX_WINDOWS || self.next_id == 0 || self.next_id > i64::MAX as u64
        {
            return Err(ModelError::Limit);
        }
        let id = WindowId(self.next_id);
        let offset = (self.windows.len() as i32) * 32;
        let rect = self.clamped(Rect {
            x: 48 + offset,
            y: TOP + 32 + offset,
            width: 720,
            height: 480,
        });
        let content = rect.content();
        let mut gate = ViewportGate::default();
        gate.resize((content.width as usize, content.height as usize));
        self.next_id += 1;
        self.windows.push(Window {
            id,
            app,
            outer: rect,
            restore: rect,
            minimized: false,
            maximized: false,
            gate,
            presented_sequence: 0,
            presented_size: None,
        });
        self.z.push(id);
        let mut effects = self.focus(Some(id))?;
        effects.push(Effect::Resize {
            window: id,
            width: content.width,
            height: content.height,
        });
        Ok((id, effects))
    }
    pub fn focus(&mut self, id: Option<WindowId>) -> Result<Vec<Effect>, ModelError> {
        if let Some(id) = id
            && self.windows[self.index(id)?].minimized
        {
            return Err(ModelError::Hidden);
        }
        let mut e = Vec::new();
        if self.focus != id {
            if let Some(old) = self.focus {
                self.reset(old, &mut e);
            }
            if let Some(capture) = self.capture {
                self.reset(capture, &mut e);
            }
            self.focus = id;
            e.push(Effect::Focus(id));
        }
        if let Some(id) = id {
            self.z.retain(|&v| v != id);
            self.z.push(id);
        }
        Ok(e)
    }
    fn apply_rect(
        &mut self,
        id: WindowId,
        rect: Rect,
        e: &mut Vec<Effect>,
    ) -> Result<(), ModelError> {
        let index = self.index(id)?;
        let old = self.windows[index].outer;
        if old == rect {
            return Ok(());
        }
        self.reset(id, e);
        self.windows[index].outer = rect;
        if old.width != rect.width || old.height != rect.height {
            let c = rect.content();
            self.windows[index]
                .gate
                .resize((c.width as usize, c.height as usize));
            e.push(Effect::Resize {
                window: id,
                width: c.width,
                height: c.height,
            });
        }
        Ok(())
    }
    pub fn geometry(&mut self, id: WindowId, rect: Rect) -> Result<Vec<Effect>, ModelError> {
        let index = self.index(id)?;
        if rect.width <= 0 || rect.height <= 0 {
            return Err(ModelError::InvalidGeometry);
        }
        let r = self.clamped(rect);
        let mut e = Vec::new();
        self.apply_rect(id, r, &mut e)?;
        self.windows[index].maximized = false;
        self.windows[index].restore = r;
        Ok(e)
    }
    pub fn minimize(&mut self, id: WindowId) -> Result<Vec<Effect>, ModelError> {
        let index = self.index(id)?;
        if self.windows[index].minimized {
            return Ok(Vec::new());
        }
        let c = self.windows[index].outer.content();
        self.windows[index].minimized = true;
        self.windows[index]
            .gate
            .resize((c.width as usize, c.height as usize));
        let mut e = Vec::new();
        self.reset(id, &mut e);
        e.push(Effect::Visibility {
            window: id,
            visible: false,
        });
        if self.focus == Some(id) {
            e.extend(self.focus(self.next_focus())?);
        }
        Ok(e)
    }
    pub fn restore(&mut self, id: WindowId) -> Result<Vec<Effect>, ModelError> {
        let index = self.index(id)?;
        let mut e = Vec::new();
        if self.windows[index].minimized {
            self.windows[index].minimized = false;
            let c = self.windows[index].outer.content();
            self.windows[index]
                .gate
                .resize((c.width as usize, c.height as usize));
            e.push(Effect::Visibility {
                window: id,
                visible: true,
            });
        } else if self.windows[index].maximized {
            let r = self.clamped(self.windows[index].restore);
            self.windows[index].maximized = false;
            self.apply_rect(id, r, &mut e)?;
            self.windows[index].restore = r;
        }
        e.extend(self.focus(Some(id))?);
        Ok(e)
    }
    pub fn maximize(&mut self, id: WindowId) -> Result<Vec<Effect>, ModelError> {
        let index = self.index(id)?;
        let mut e = Vec::new();
        if self.windows[index].minimized {
            self.windows[index].minimized = false;
            e.push(Effect::Visibility {
                window: id,
                visible: true,
            });
        }
        if !self.windows[index].maximized {
            self.windows[index].restore = self.windows[index].outer;
            self.windows[index].maximized = true;
        }
        self.apply_rect(id, self.workarea(), &mut e)?;
        e.extend(self.focus(Some(id))?);
        Ok(e)
    }
    pub fn resize_screen(&mut self, w: i32, h: i32) -> Result<Vec<Effect>, ModelError> {
        if !Self::valid_screen(w, h) {
            return Err(ModelError::InvalidViewport);
        }
        self.width = w;
        self.height = h;
        let ids = self.z.clone();
        let mut e = Vec::new();
        for id in ids {
            let i = self.index(id)?;
            let r = if self.windows[i].maximized {
                self.workarea()
            } else {
                self.clamped(self.windows[i].outer)
            };
            let saved = self.clamped(self.windows[i].restore);
            self.apply_rect(id, r, &mut e)?;
            self.windows[i].restore = saved;
            let c = r.content();
            self.windows[i]
                .gate
                .resize((c.width as usize, c.height as usize));
            self.reset(id, &mut e);
        }
        Ok(e)
    }
    pub fn close(&mut self, id: WindowId) -> Result<Vec<Effect>, ModelError> {
        let index = self.index(id)?;
        let mut effects = Vec::new();
        self.reset(id, &mut effects);
        self.windows.remove(index);
        self.z.retain(|&value| value != id);
        if self.focus == Some(id) {
            let changed = self.focus(self.next_focus())?;
            for effect in changed {
                if !effects.contains(&effect) {
                    effects.push(effect);
                }
            }
        }
        // This is terminal for the instance: no later effect may address it.
        effects.push(Effect::Close(id));
        Ok(effects)
    }
    pub fn presented(
        &mut self,
        id: WindowId,
        seq: u64,
        w: i32,
        h: i32,
    ) -> Result<Vec<Effect>, ModelError> {
        let index = self.index(id)?;
        if self.windows[index].minimized {
            return Err(ModelError::Hidden);
        }
        if w <= 0 || h <= 0 || w > 4096 || h > 4096 || i64::from(w) * i64::from(h) > 4_194_304 {
            return Err(ModelError::InvalidGeometry);
        }
        let size = (w as usize, h as usize);
        let window = &mut self.windows[index];
        if seq == 0
            || seq < window.presented_sequence
            || (seq == window.presented_sequence && window.presented_size != Some(size))
        {
            return Err(ModelError::StaleFrame);
        }
        let expected = window.outer.content();
        let mismatch = size != (expected.width as usize, expected.height as usize);
        window.presented_sequence = seq;
        window.presented_size = Some(size);
        window.gate.present(size);
        let mut effects = Vec::new();
        if mismatch {
            self.reset(id, &mut effects);
        }
        Ok(effects)
    }
    pub fn hit_content(&self, x: i32, y: i32) -> Option<WindowId> {
        // Select the top visible outer window first. Its chrome occludes lower app content.
        let window = self.z.iter().rev().find_map(|id| {
            self.windows
                .iter()
                .find(|w| w.id == *id && !w.minimized && w.outer.contains(x, y))
        })?;
        window.outer.content().contains(x, y).then_some(window.id)
    }
    pub fn begin_content(&mut self, id: WindowId) -> Result<Vec<Effect>, ModelError> {
        let v = self.view(id)?;
        if v.minimized || !v.input_ready {
            return Err(ModelError::Hidden);
        }
        let mut e = self.focus(Some(id))?;
        e.extend(self.cancel_capture());
        self.capture = Some(id);
        Ok(e)
    }
    pub fn route_pointer(
        &mut self,
        x: i32,
        y: i32,
        down: bool,
    ) -> Option<PointerRoute> {
        let id = self.capture?;
        let v = self.view(id).ok()?;
        if v.minimized || !v.input_ready || self.focus != Some(id) {
            self.capture = None;
            return None;
        }
        let content = v.outer.content();
        let position = content
            .contains(x, y)
            .then(|| (x - content.x, y - content.y));
        if !down {
            self.capture = None;
        }
        Some((id, position, down))
    }
    pub fn cancel_capture(&mut self) -> Vec<Effect> {
        self.capture
            .take()
            .map_or_else(Vec::new, |id| vec![Effect::ResetInput(id)])
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn model() -> DesktopModel {
        DesktopModel::new(1100, 760).unwrap()
    }
    fn open(m: &mut DesktopModel, a: u16) -> WindowId {
        m.open(a).unwrap().0
    }
    fn ready(m: &mut DesktopModel, id: WindowId, seq: u64) {
        let c = m.view(id).unwrap().outer.content();
        m.presented(id, seq, c.width, c.height).unwrap();
    }
    #[test]
    fn screen_validation_and_reserved_workarea() {
        for (w, h) in [(0, 760), (319, 240), (320, 239), (4097, 240), (4096, 4096)] {
            assert!(matches!(
                DesktopModel::new(w, h),
                Err(ModelError::InvalidViewport)
            ));
        }
        let m = model();
        assert_eq!(
            m.workarea(),
            Rect {
                x: 0,
                y: 24,
                width: 1100,
                height: 672
            }
        );
        assert_eq!(DesktopModel::new(320, 240).unwrap().workarea().height, 152);
    }
    #[test]
    fn open_capacity_and_monotonic_ids() {
        let mut m = model();
        let ids: Vec<_> = (0..4).map(|a| open(&mut m, a)).collect();
        assert_eq!(
            ids,
            vec![WindowId(1), WindowId(2), WindowId(3), WindowId(4)]
        );
        assert_eq!(m.open(4), Err(ModelError::Limit));
        m.close(ids[1]).unwrap();
        assert_eq!(open(&mut m, 4), WindowId(5));
        assert_eq!(m.views().len(), MAX_WINDOWS);
    }
    #[test]
    fn focus_raises_and_invalid_focus_is_atomic() {
        let mut m = model();
        let a = open(&mut m, 0);
        let b = open(&mut m, 1);
        assert!(m.view(b).unwrap().focused);
        m.focus(Some(a)).unwrap();
        assert_eq!(m.views().last().unwrap().id, a);
        assert!(m.view(a).unwrap().focused);
        let old = m.views();
        assert_eq!(m.focus(Some(WindowId(999))), Err(ModelError::Unknown));
        assert_eq!(m.views(), old);
    }
    #[test]
    fn minimize_restore_and_close_choose_visible_focus() {
        let mut m = model();
        let a = open(&mut m, 0);
        let b = open(&mut m, 1);
        let rect = m.view(b).unwrap().outer;
        let e = m.minimize(b).unwrap();
        assert!(e.contains(&Effect::ResetInput(b)));
        assert!(m.view(a).unwrap().focused);
        assert!(m.view(b).unwrap().minimized);
        assert_eq!(m.focus(Some(b)), Err(ModelError::Hidden));
        m.restore(b).unwrap();
        assert_eq!(m.view(b).unwrap().outer, rect);
        assert!(m.view(b).unwrap().focused);
        let e = m.close(b).unwrap();
        assert!(e.contains(&Effect::Close(b)));
        assert!(m.view(a).unwrap().focused);
        m.close(a).unwrap();
        assert!(m.views().is_empty());
    }
    #[test]
    fn maximize_and_restore_keep_saved_geometry() {
        let mut m = model();
        let a = open(&mut m, 0);
        let initial = m.view(a).unwrap().outer;
        m.maximize(a).unwrap();
        assert_eq!(m.view(a).unwrap().outer, m.workarea());
        assert!(m.view(a).unwrap().maximized);
        m.restore(a).unwrap();
        assert_eq!(m.view(a).unwrap().outer, initial);
        assert!(!m.view(a).unwrap().maximized);
    }
    #[test]
    fn geometry_clamps_but_invalid_extent_is_atomic() {
        let mut m = model();
        let a = open(&mut m, 0);
        m.geometry(
            a,
            Rect {
                x: -9999,
                y: 9999,
                width: 9999,
                height: 9999,
            },
        )
        .unwrap();
        assert_eq!(m.view(a).unwrap().outer, m.workarea());
        let old = m.views();
        assert_eq!(
            m.geometry(
                a,
                Rect {
                    x: 0,
                    y: 0,
                    width: 0,
                    height: 200
                }
            ),
            Err(ModelError::InvalidGeometry)
        );
        assert_eq!(m.views(), old);
    }
    #[test]
    fn resize_screen_reflows_maximized_and_restore_rects() {
        let mut m = model();
        let a = open(&mut m, 0);
        m.maximize(a).unwrap();
        m.resize_screen(320, 240).unwrap();
        assert_eq!(m.view(a).unwrap().outer, m.workarea());
        m.restore(a).unwrap();
        let r = m.view(a).unwrap().outer;
        assert!(r.width <= 320 && r.height <= 152 && r.y >= TOP);
        let old = m.views();
        assert_eq!(
            m.resize_screen(4096, 4096),
            Err(ModelError::InvalidViewport)
        );
        assert_eq!(m.views(), old);
    }
    #[test]
    fn presentation_gate_matches_viewport_and_rejects_stale_sequence() {
        let mut m = model();
        let a = open(&mut m, 0);
        assert!(!m.view(a).unwrap().input_ready);
        ready(&mut m, a, 2);
        assert!(m.view(a).unwrap().input_ready);
        assert_eq!(m.presented(a, 1, 10, 10), Err(ModelError::StaleFrame));
        assert!(m.view(a).unwrap().input_ready);
        let r = m.view(a).unwrap().outer;
        m.geometry(
            a,
            Rect {
                width: r.width - 20,
                ..r
            },
        )
        .unwrap();
        assert!(!m.view(a).unwrap().input_ready);
        m.presented(a, 3, r.width, r.height - TITLE).unwrap();
        assert!(!m.view(a).unwrap().input_ready);
        ready(&mut m, a, 4);
        assert!(m.view(a).unwrap().input_ready);
    }
    #[test]
    fn capture_never_routes_release_to_another_window() {
        let mut m = model();
        let a = open(&mut m, 0);
        let b = open(&mut m, 1);
        ready(&mut m, a, 1);
        ready(&mut m, b, 1);
        m.begin_content(a).unwrap();
        let c = m.view(a).unwrap().outer.content();
        assert_eq!(
            m.route_pointer(c.x + 7, c.y + 9, true),
            Some((a, Some((7, 9)), true))
        );
        let routed = m.route_pointer(1099, 695, false).unwrap();
        assert_eq!(routed.0, a);
        assert_eq!(routed.1, None);
        assert!(m.route_pointer(c.x, c.y, false).is_none());
    }
    #[test]
    fn capture_is_cancelled_by_focus_geometry_hide_and_close() {
        let mut m = model();
        let a = open(&mut m, 0);
        let b = open(&mut m, 1);
        ready(&mut m, a, 1);
        m.begin_content(a).unwrap();
        m.focus(Some(b)).unwrap();
        assert!(m.route_pointer(20, 80, false).is_none());
        m.focus(Some(a)).unwrap();
        ready(&mut m, a, 2);
        m.begin_content(a).unwrap();
        let r = m.view(a).unwrap().outer;
        let e = m.geometry(a, Rect { x: r.x + 1, ..r }).unwrap();
        assert!(e.contains(&Effect::ResetInput(a)));
        assert!(m.route_pointer(20, 80, false).is_none());
        m.minimize(a).unwrap();
        assert_eq!(m.begin_content(a), Err(ModelError::Hidden));
        m.close(a).unwrap();
        assert_eq!(m.view(a), Err(ModelError::Unknown));
    }
    #[test]
    fn half_open_hit_test_obeys_z_and_hidden_state() {
        let mut m = model();
        let a = open(&mut m, 0);
        let b = open(&mut m, 1);
        let c = m.view(b).unwrap().outer.content();
        assert_eq!(m.hit_content(c.x + 1, c.y + 1), Some(b));
        assert_ne!(m.hit_content(c.x + c.width, c.y), Some(b));
        m.minimize(b).unwrap();
        assert_ne!(m.hit_content(c.x + 1, c.y + 1), Some(b));
        m.focus(Some(a)).unwrap();
        assert_eq!(
            m.hit_content(
                m.view(a).unwrap().outer.x + 1,
                m.view(a).unwrap().outer.y + TITLE + 1
            ),
            Some(a)
        );
    }
    #[test]
    fn counter_exhaustion_and_capture_before_present_fail_without_mutation() {
        let mut m = model();
        m.next_id = i64::MAX as u64 + 1;
        assert_eq!(m.open(0), Err(ModelError::Limit));
        assert!(m.views().is_empty());
        m.next_id = 1;
        let a = open(&mut m, 0);
        assert_eq!(m.begin_content(a), Err(ModelError::Hidden));
        assert!(m.cancel_capture().is_empty());
    }
}

#[cfg(test)]
mod mismatched_presentation_tests {
    use super::*;
    #[test]
    fn a_mismatched_presented_viewport_cancels_existing_content_capture() {
        let mut model = DesktopModel::new(1100, 760).unwrap();
        let id = model.open(0).unwrap().0;
        let content = model.view(id).unwrap().outer.content();
        model
            .presented(id, 1, content.width, content.height)
            .unwrap();
        model.begin_content(id).unwrap();
        let effects = model.presented(id, 2, 1, 1).unwrap();
        assert!(effects.contains(&Effect::ResetInput(id)));
        assert_eq!(model.capture, None);
        assert!(!model.view(id).unwrap().input_ready);
    }
}

#[cfg(test)]
mod transition_invariants {
    use super::*;
    #[test]
    fn bounded_transition_sequences_preserve_geometry_focus_identity_and_capture() {
        let mut model = DesktopModel::new(1100, 760).unwrap();
        let mut seed = 0x6d696e67u32;
        for step in 0..10_000u64 {
            seed ^= seed << 13;
            seed ^= seed >> 17;
            seed ^= seed << 5;
            let views = model.views();
            let id = if views.is_empty() {
                WindowId(999)
            } else {
                views[(seed as usize >> 8) % views.len()].id
            };
            match seed % 13 {
                0 => {
                    let _ = model.open((step % 8) as u16);
                }
                1 => {
                    let _ = model.focus(Some(id));
                }
                2 => {
                    let _ = model.minimize(id);
                }
                3 => {
                    let _ = model.restore(id);
                }
                4 => {
                    let _ = model.maximize(id);
                }
                5 => {
                    let _ = model.geometry(
                        id,
                        Rect {
                            x: (seed as i32) % 2000,
                            y: ((seed >> 16) as i32) % 1000,
                            width: 100 + (seed % 2000) as i32,
                            height: 80 + (seed % 1500) as i32,
                        },
                    );
                }
                6 => {
                    let screens = [(320, 240), (1100, 760), (1920, 1080), (2048, 2048)];
                    let (w, h) = screens[(seed as usize >> 4) % screens.len()];
                    model.resize_screen(w, h).unwrap();
                }
                7 => {
                    let _ = model.close(id);
                }
                8 => {
                    if let Ok(view) = model.view(id)
                        && !view.minimized
                    {
                        let c = view.outer.content();
                        let _ = model.presented(id, step + 1, c.width, c.height);
                    }
                }
                9 => {
                    let _ = model.begin_content(id);
                }
                10 => {
                    let _ = model.route_pointer(
                        (seed % 2200) as i32,
                        ((seed >> 16) % 1200) as i32,
                        seed & 0x100 != 0,
                    );
                }
                11 => {
                    model.cancel_capture();
                }
                _ => {
                    model.focus(None).unwrap();
                }
            }
            let now = model.views();
            let area = model.workarea();
            assert!(now.len() <= MAX_WINDOWS);
            let mut ids = std::collections::BTreeSet::new();
            for view in &now {
                assert!(ids.insert(view.id));
                assert!(view.id.0 > 0 && view.id.0 < model.next_id);
                let r = view.outer;
                assert!(r.width > 0 && r.height > TITLE);
                assert!(
                    r.x >= area.x
                        && r.y >= area.y
                        && r.x + r.width <= area.x + area.width
                        && r.y + r.height <= area.y + area.height
                );
                assert!(!view.minimized || (!view.focused && !view.input_ready));
                if view.maximized {
                    assert_eq!(r, area);
                }
            }
            let focused: Vec<_> = now.iter().filter(|v| v.focused).collect();
            assert!(focused.len() <= 1);
            if let Some(view) = focused.first() {
                assert_eq!(now.iter().rev().find(|v| !v.minimized).unwrap().id, view.id);
            }
            if let Some(capture) = model.capture {
                let view = model.view(capture).unwrap();
                assert!(view.focused && view.input_ready && !view.minimized);
            }
        }
    }
}

#[cfg(test)]
mod chrome_occlusion_tests {
    use super::*;
    #[test]
    fn top_window_title_does_not_route_through_to_lower_content() {
        let mut model = DesktopModel::new(1100, 760).unwrap();
        let a = model.open(0).unwrap().0;
        let b = model.open(1).unwrap().0;
        let top = model.view(b).unwrap().outer;
        let point = (top.x + 10, top.y + 8);
        assert!(
            model
                .view(a)
                .unwrap()
                .outer
                .content()
                .contains(point.0, point.1)
        );
        assert_eq!(model.hit_content(point.0, point.1), None);
    }
}

#[cfg(test)]
mod close_effect_order_tests {
    use super::*;
    #[test]
    fn focused_close_transfers_focus_and_resets_before_final_close() {
        let mut model = DesktopModel::new(1100, 760).unwrap();
        let a = model.open(0).unwrap().0;
        let b = model.open(1).unwrap().0;
        assert_eq!(
            model.close(b).unwrap(),
            vec![
                Effect::ResetInput(b),
                Effect::Focus(Some(a)),
                Effect::Close(b)
            ]
        );
        assert_eq!(
            model.close(a).unwrap(),
            vec![Effect::ResetInput(a), Effect::Focus(None), Effect::Close(a)]
        );
    }
}
