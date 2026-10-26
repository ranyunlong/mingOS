//! SDK6 native input routing. It never chooses a guest-supplied application id.
use crate::{desktop_model::WindowId, ming_desktop::Session, ui::ViewBridge};
use winit::{
    dpi::{PhysicalPosition, PhysicalSize},
    event::{Ime, KeyEvent},
    keyboard::{Key, ModifiersState, NamedKey},
    window::Window,
};
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Owner {
    window: WindowId,
    epoch: u64,
}
#[derive(Default)]
pub(crate) struct NativeInput {
    modifiers: ModifiersState,
    ime_owner: Option<Owner>,
    composition: Option<Owner>,
    composing: bool,
    reject_unpaired_commit: bool,
    caret: Option<(i32, i32, u32, u32)>,
}
pub(crate) fn refresh_presentations(session: &Session) {
    for view in session.views() {
        if let Some(bridge) = session.input_bridge(view.id) {
            if view.input_ready && !view.minimized {
                let content = view.outer.content();
                bridge.input_presented(
                    view.presented_sequence,
                    content.width as u32,
                    content.height as u32,
                );
            } else {
                bridge.input_unpresented();
            }
        }
    }
}
impl NativeInput {
    pub fn modifiers(&mut self, value: ModifiersState) {
        self.modifiers = value;
    }
    pub fn shutdown_shortcut(&self, event: &KeyEvent) -> bool {
        event.state.is_pressed()
            && !event.repeat
            && self.modifiers.control_key()
            && self.modifiers.alt_key()
            && matches!(&event.logical_key,Key::Character(c)if c.eq_ignore_ascii_case("q"))
    }
    pub fn modifier_bits(&self) -> u32 {
        self.flags()
    }
    fn flags(&self) -> u32 {
        u32::from(self.modifiers.shift_key())
            | (u32::from(self.modifiers.control_key()) << 1)
            | (u32::from(self.modifiers.alt_key()) << 2)
            | (u32::from(self.modifiers.super_key()) << 3)
    }
    fn focused(session: &Session) -> Option<(Owner, ViewBridge)> {
        if !session.pointer_sink_ready() {
            return None;
        }
        let id = session.focused()?;
        let view = session.scene.model().view(id).ok()?;
        if !view.input_ready || view.minimized {
            return None;
        }
        let bridge = session.input_bridge(id)?;
        if !bridge.text_input_enabled() {
            return None;
        }
        Some((
            Owner {
                window: id,
                epoch: bridge.text_input_epoch(),
            },
            bridge,
        ))
    }
    fn deliver(
        session: &mut Session,
        owner: Owner,
        result: Result<(), crate::text_input::InputError>,
    ) {
        if result.is_err() {
            session.note_input_drop(owner.window, "typed_input_rejected");
        }
    }
    pub fn key(&mut self, event: &KeyEvent, session: &mut Session) -> bool {
        let Some((owner, bridge)) = Self::focused(session) else {
            return false;
        };
        if self.composing && self.composition == Some(owner) {
            return true;
        }
        let key = match &event.logical_key {
            Key::Named(NamedKey::Backspace) => Some(1),
            Key::Named(NamedKey::Delete) => Some(2),
            Key::Named(NamedKey::ArrowLeft) => Some(3),
            Key::Named(NamedKey::ArrowRight) => Some(4),
            Key::Named(NamedKey::Home) => Some(5),
            Key::Named(NamedKey::End) => Some(6),
            Key::Named(NamedKey::Enter) => Some(7),
            Key::Named(NamedKey::Escape) => Some(8),
            Key::Named(NamedKey::Tab) => Some(9),
            Key::Named(NamedKey::ArrowUp) => Some(14),
            Key::Named(NamedKey::ArrowDown) => Some(15),
            Key::Character(c) if c.eq_ignore_ascii_case("a") => Some(10),
            Key::Character(c) if c.eq_ignore_ascii_case("c") => Some(11),
            Key::Character(c) if c.eq_ignore_ascii_case("v") => Some(12),
            Key::Character(c) if c.eq_ignore_ascii_case("x") => Some(13),
            _ => None,
        };
        if let Some(key) = key {
            Self::deliver(
                session,
                owner,
                bridge.text_key(key, event.state.is_pressed(), event.repeat, self.flags()),
            );
        }
        if event.state.is_pressed()
            && self.flags() & 14 == 0
            && let Some(text) = event.text.as_deref()
            && !text.is_empty()
            && text.chars().all(|c| !c.is_control())
        {
            Self::deliver(session, owner, bridge.text_commit(text, self.flags()));
        }
        true
    }
    pub fn ime(&mut self, event: Ime, session: &mut Session) {
        match event {
            Ime::Preedit(text, cursor) => {
                let Some((owner, bridge)) = Self::focused(session) else {
                    return;
                };
                if self.ime_owner!=Some(owner){return;}
                if !self.preedit_owner(owner, !text.is_empty()) {
                    return;
                }
                Self::deliver(
                    session,
                    owner,
                    bridge.text_preedit(&text, cursor, self.flags()),
                );
            }
            Ime::Commit(text) => {
                let Some((owner, bridge)) = Self::focused(session) else {
                    self.composing = false;
                    self.composition = None;
                    self.reject_unpaired_commit = false;
                    return;
                };
                if self.composition.is_none() && self.ime_owner!=Some(owner){return;}
                if !self.commit_owner(owner) {
                    return;
                }
                if !text.is_empty() {
                    Self::deliver(session, owner, bridge.text_commit(&text, self.flags()));
                }
            }
            Ime::Disabled => {
                if let Some(old) = self.composition.take() {
                    self.reject_unpaired_commit = true;
                    if let Some(bridge) = session.input_bridge(old.window)
                        && bridge.text_input_epoch() == old.epoch
                    {
                        bridge.cancel_text_input(1);
                    }
                }
                self.composing = false;
            }
            Ime::Enabled => {}
        }
    }
    pub fn refresh(&mut self, window: &Window, session: &Session) {
        let desired = Self::focused(session).and_then(|(owner, bridge)| {
            let target = bridge.text_target()?;
            let view = session.scene.model().view(owner.window).ok()?;
            let content = view.outer.content();
            if !target.caret.active
                || target.caret.x < 0
                || target.caret.y < 0
                || target.caret.x + target.caret.width > content.width
                || target.caret.y + target.caret.height > content.height
            {
                return None;
            }
            Some((owner, content, target.caret))
        });
        let owner = desired.as_ref().map(|(owner, _, _)| *owner);
        if owner != self.ime_owner {
            if self.composition.is_some() {
                self.reject_unpaired_commit = true;
            }
            window.set_ime_allowed(false);
            self.ime_owner = owner;
            self.caret = None;
            if owner.is_some() {
                window.set_ime_allowed(true);
            }
        }
        if let Some((_, content, target)) = desired {
            let size = window.inner_size();
            if size.width == 0 || size.height == 0 {
                return;
            }
            let sx = f64::from(size.width) / f64::from(session.options.width);
            let sy = f64::from(size.height) / f64::from(session.options.height);
            let rect = (
                ((content.x + target.x) as f64 * sx).round() as i32,
                ((content.y + target.y) as f64 * sy).round() as i32,
                (f64::from(target.width) * sx).round().max(1.0) as u32,
                (f64::from(target.height) * sy).round().max(1.0) as u32,
            );
            if self.caret != Some(rect) {
                window.set_ime_cursor_area(
                    PhysicalPosition::new(rect.0, rect.1),
                    PhysicalSize::new(rect.2, rect.3),
                );
                self.caret = Some(rect);
            }
        }
    }
    fn preedit_owner(&mut self, owner: Owner, nonempty: bool) -> bool {
        if nonempty {
            self.composition = Some(owner);
            self.composing = true;
            self.reject_unpaired_commit = false;
            true
        } else if self.composition.is_some_and(|old| old != owner) {
            false
        } else {
            self.composing = false;
            true
        }
    }
    fn commit_owner(&mut self, owner: Owner) -> bool {
        let previous = self.composition.take();
        self.composing = false;
        if previous.is_some_and(|old| old != owner)
            || (previous.is_none() && self.reject_unpaired_commit)
        {
            self.reject_unpaired_commit = false;
            false
        } else {
            true
        }
    }
    pub fn lost_focus(&mut self) {
        self.composing = false;
        if self.composition.is_some() {
            self.reject_unpaired_commit = true;
        }
        self.modifiers = ModifiersState::empty();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn owner(window: u64, epoch: u64) -> Owner {
        Owner {
            window: WindowId(window),
            epoch,
        }
    }
    #[test]
    fn empty_preedit_stops_keyboard_suppression_and_keeps_commit_owner() {
        let mut input = NativeInput::default();
        let a = owner(1, 1);
        assert!(input.preedit_owner(a, true));
        assert!(input.composing);
        assert!(input.preedit_owner(a, false));
        assert!(!input.composing);
        assert_eq!(input.composition, Some(a));
        assert!(input.commit_owner(a));
    }
    #[test]
    fn cancelled_composition_cannot_commit_into_another_window_or_epoch() {
        for next in [owner(2, 1), owner(1, 2)] {
            let mut input = NativeInput::default();
            input.preedit_owner(owner(1, 1), true);
            input.lost_focus();
            assert!(!input.commit_owner(next));
            input.preedit_owner(next, true);
            assert!(input.commit_owner(next));
        }
    }
    #[test]
    fn direct_commit_without_preedit_is_supported_without_crossing_cancellation() {
        let mut input = NativeInput::default();
        assert!(input.commit_owner(owner(1, 1)));
        input.preedit_owner(owner(1, 1), true);
        input.lost_focus();
        assert!(!input.commit_owner(owner(2, 1)));
        assert!(input.commit_owner(owner(2, 1)));
    }
}
