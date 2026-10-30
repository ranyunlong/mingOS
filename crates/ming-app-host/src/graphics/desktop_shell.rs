//! Trusted host chrome over a content-only DesktopScene root.
//! Windows are bottom-to-top, with half-open rectangular outers. This module
//! neither grants input readiness nor acknowledges presentation.
use super::{FramePixels, MAX_PIXELS, RenderedFrame, font};
use crate::desktop_model::{DOCK, MAX_WINDOWS, Rect, TITLE, TOP, WindowView};
use anyhow::{Result, bail};
use std::{sync::Arc, time::Instant};

#[derive(Clone, Debug)]
pub struct ShellWindow {
    pub view: WindowView,
    /// A latest scene content frame exists (not the model's input_ready gate).
    pub has_content: bool,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShellAppState {
    Closed,
    Starting,
    Running,
    Minimized,
    Failed,
}
#[derive(Clone, Debug)]
pub struct ShellApp {
    pub id: u16,
    pub title: String,
    pub state: ShellAppState,
}

#[derive(Clone, Debug)]
pub struct StoragePrompt { id: String, version: String, fingerprint: String, pub(super) replacement: bool, pub(super) mode: u32 }
#[derive(Clone, Copy, Debug)]
pub struct StoragePromptGeometry { pub dialog: Rect, pub deny: Rect, pub allow: Option<Rect> }

impl StoragePrompt {
    pub fn new(id: &str, version: &str, fingerprint: &str, replacement: bool) -> Result<Self> {
        if !crate::app_data::valid_package_id(id) || version.len() > 16_384 || version.is_empty()
            || !version.bytes().all(|b| b.is_ascii_digit() || b == b'.')
            || fingerprint.len() != 64 || !fingerprint.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
            bail!("DESKTOP_STORAGE_PROMPT_INVALID");
        }
        Ok(Self { id: id.into(), version: version.into(), fingerprint: fingerprint.into(), replacement, mode: u32::from(replacement) })
    }
    pub fn management(id: &str, fingerprint: Option<&str>, retry: bool) -> Result<Self> {
        if !crate::app_data::valid_package_id(id) || fingerprint.is_some_and(|value| value.len() != 64 || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))) {
            bail!("DESKTOP_STORAGE_PROMPT_INVALID");
        }
        Ok(Self { id: id.into(), version: String::new(), fingerprint: fingerprint.unwrap_or("LEGACY").into(), replacement: false, mode: if retry { 3 } else { 2 } })
    }
    pub fn public_management(id: &str, fingerprint: Option<&str>, retry: bool) -> Result<Self> {
        if !crate::app_data::valid_package_id(id) || fingerprint.is_some_and(|value| value.len() != 64 || !value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))) {
            bail!("DESKTOP_STORAGE_PROMPT_INVALID");
        }
        Ok(Self { id: id.into(), version: String::new(), fingerprint: fingerprint.unwrap_or("LEGACY").into(), replacement: false, mode: if retry { 9 } else { 8 } })
    }
    pub fn status(id: Option<&str>, available: bool) -> Result<Self> {
        if id.is_some_and(|value| !crate::app_data::valid_package_id(value)) { bail!("DESKTOP_STORAGE_PROMPT_INVALID"); }
        Ok(Self { id: id.unwrap_or("").into(), version: String::new(), fingerprint: String::new(), replacement: true, mode: if available { 4 } else { 5 } })
    }
    pub fn public_request(id: &str) -> Result<Self> {
        if !crate::app_data::valid_package_id(id) { bail!("DESKTOP_STORAGE_PROMPT_INVALID"); }
        Ok(Self { id: id.into(), version: String::new(), fingerprint: String::new(), replacement: false, mode: 6 })
    }
    pub fn public_retry(id: &str) -> Result<Self> {
        if !crate::app_data::valid_package_id(id) { bail!("DESKTOP_STORAGE_PROMPT_INVALID"); }
        Ok(Self { id: id.into(), version: String::new(), fingerprint: String::new(), replacement: false, mode: 7 })
    }
    pub fn uninstall(id: &str, version: &str, fingerprint: &str, retry: bool) -> Result<Self> {
        if !crate::app_data::valid_package_id(id) || version.is_empty() || version.len() > 16_384
            || !version.bytes().all(|b| b.is_ascii_digit() || b == b'.')
            || fingerprint.len() != 64 || !fingerprint.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
            bail!("DESKTOP_STORAGE_PROMPT_INVALID");
        }
        Ok(Self { id: id.into(), version: version.into(), fingerprint: fingerprint.into(), replacement: false, mode: if retry { 11 } else { 10 } })
    }
    pub fn uninstall_blocked(id: &str) -> Result<Self> {
        if !crate::app_data::valid_package_id(id) { bail!("DESKTOP_STORAGE_PROMPT_INVALID"); }
        Ok(Self { id: id.into(), version: String::new(), fingerprint: String::new(), replacement: true, mode: 12 })
    }
    pub(super) fn buttons(&self) -> (&'static [u8], Option<&'static [u8]>) {
        match self.mode {
            1 | 4 | 5 | 12 => (b"CLOSE", None),
            2 | 8 => (b"CANCEL", Some(b"REVOKE")),
            3 | 7 | 9 => (b"CANCEL", Some(b"RETRY")),
            10 => (b"CANCEL", Some(b"UNINSTALL")),
            11 => (b"CANCEL", Some(b"RETRY")),
            _ => (b"DENY", Some(b"ALLOW")),
        }
    }
    pub(crate) fn has_allow(&self) -> bool { self.buttons().1.is_some() }
    pub(crate) fn lines(&self, width: i32) -> Vec<Vec<u8>> {
        let capacity = ((width - 24) / 6) as usize;
        if self.mode == 8 || self.mode == 9 {
            let mut lines = vec![b"PUBLIC STORAGE ACCESS".to_vec()];
            lines.extend(format!("APP ID: {}", self.id).as_bytes().chunks(capacity).map(|part| part.to_vec()));
            lines.extend(format!("SHA256: {}", self.fingerprint).as_bytes().chunks(capacity).map(|part| part.to_vec()));
            lines.push(b"SHARED DATA WILL BE KEPT".to_vec());
            if self.mode == 9 { lines.push(b"REVOCATION NOT SAVED".to_vec()); }
            return lines;
        }
        if self.mode == 6 || self.mode == 7 {
            return vec![
                b"PUBLIC STORAGE ACCESS".to_vec(),
                format!("APP ID: {}", self.id).into_bytes(),
                b"SHARED FILES MAY BE READ AND WRITTEN".to_vec(),
                if self.mode == 6 { b"USER APPROVAL REQUIRED".to_vec() } else { b"AUTHORIZATION NOT SAVED".to_vec() },
            ];
        }
        if self.mode == 10 || self.mode == 11 {
            let mut lines = vec![b"UNINSTALL APP".to_vec()];
            lines.extend(format!("APP ID: {}", self.id).as_bytes().chunks(capacity).map(|part| part.to_vec()));
            lines.extend(format!("VERSION: {}", self.version).as_bytes().chunks(capacity).map(|part| part.to_vec()));
            lines.extend(format!("SHA256: {}", self.fingerprint).as_bytes().chunks(capacity).map(|part| part.to_vec()));
            lines.push(b"PRIVATE DATA WILL BE KEPT".to_vec());
            if self.mode == 11 { lines.push(b"UNINSTALL NOT SAVED".to_vec()); }
            return lines;
        }
        if self.mode == 12 {
            return vec![
                b"UNINSTALL APP".to_vec(),
                format!("APP ID: {}", self.id).into_bytes(),
                b"CLOSE APP FIRST".to_vec(),
            ];
        }
        if self.mode >= 4 {
            let mut lines = vec![b"PRIVATE STORAGE ACCESS".to_vec()];
            if !self.id.is_empty() { lines.extend(format!("APP ID: {}", self.id).as_bytes().chunks(capacity).map(|part| part.to_vec())); }
            lines.push(if self.mode == 4 { b"NO AUTHORIZATION".to_vec() } else { b"STORAGE UNAVAILABLE".to_vec() });
            return lines;
        }
        let version = if self.version.len() > 32 { format!("{}...", &self.version[..32]) } else { self.version.clone() };
        let mut lines = vec![if self.mode >= 2 { b"PRIVATE STORAGE ACCESS".to_vec() } else { b"PRIVATE STORAGE REQUEST".to_vec() }];
        let mut values = vec![format!("APP ID: {}", self.id)];
        if self.mode < 2 { values.push(format!("VERSION: {version}")); }
        values.push(format!("SHA256: {}", self.fingerprint));
        for value in values {
            lines.extend(value.as_bytes().chunks(capacity).map(|part| part.to_vec()));
        }
        if self.mode >= 2 {
            lines.push(b"DATA WILL BE KEPT".to_vec());
            if self.mode == 3 { lines.push(b"REVOCATION NOT SAVED".to_vec()); }
        } else {
            lines.push(if self.replacement { b"DIFFERENT PACKAGE: CLOSE".to_vec() } else { b"PUBLISHER NOT VERIFIED".to_vec() });
        }
        lines
    }
}
pub fn storage_prompt_geometry(width: i32, height: i32, replacement: bool) -> Option<StoragePromptGeometry> {
    if !valid_screen(width as usize, height as usize) { return None; }
    let dialog = Rect { x: (width - (width - 16).min(600)) / 2, y: (height - 208) / 2, width: (width - 16).min(600), height: 208 };
    let deny = Rect { x: dialog.x + dialog.width - 192, y: dialog.y + 168, width: 80, height: 28 };
    let allow = (!replacement).then_some(Rect { x: dialog.x + dialog.width - 100, y: dialog.y + 168, width: 80, height: 28 });
    Some(StoragePromptGeometry { dialog, deny, allow })
}

pub(super) const BAR: u32 = 0xffe8ebef;
pub(super) const INK: u32 = 0xff26313f;
pub(super) const TITLE_BG: u32 = 0xffe2e5e9;
pub(super) const FOCUSED: u32 = 0xffe5edfb;
pub(super) const LINE: u32 = 0xffc8ced6;
pub(super) const BLUE: u32 = 0xff367be8;
pub(super) const RED: u32 = 0xffdf625e;
pub(super) const YELLOW: u32 = 0xffe5b64b;
pub(super) const GREEN: u32 = 0xff56ae79;
pub(super) const STARTING_BG: u32 = 0xfff0f2f5;
pub(super) const FAILED_BG: u32 = 0xfff9e9e9;
pub(super) const TILE: u32 = 0xfff8f9fb;
pub(super) const TITLE_CHARS: usize = 24;
pub(super) const LABEL_CHARS: usize = 8;

/// Exact rendered Dock cell, also usable by a controller for hit testing.
/// 64x64 cells, 8px gaps, centered in the bottom DOCK=64 strip. Half-open
/// edges; invalid screen/count/index returns an empty rectangle. No input effect.
pub fn dock_cell(width: i32, height: i32, index: usize, count: usize) -> Rect {
    if !valid_screen(width as usize, height as usize)
        || count == 0
        || count > MAX_WINDOWS
        || index >= count
    {
        return Rect {
            x: 0,
            y: 0,
            width: 0,
            height: 0,
        };
    }
    let span = count as i32 * 64 + (count as i32 - 1) * 8;
    Rect {
        x: (width - span) / 2 + index as i32 * 72,
        y: height - DOCK,
        width: 64,
        height: DOCK,
    }
}

#[cfg(target_os = "linux")]
pub fn dock_storage_cell(width: i32, height: i32, index: usize, count: usize) -> Rect {
    let cell = dock_cell(width, height, index, count);
    if cell.width == 0 { return cell; }
    Rect { x: cell.x + 46, y: cell.y + 4, width: 16, height: 16 }
}

#[cfg(target_os = "linux")]
pub fn dock_uninstall_cell(width: i32, height: i32, index: usize, count: usize) -> Rect {
    let cell = dock_cell(width, height, index, count);
    if cell.width == 0 { return cell; }
    Rect { x: cell.x + 4, y: cell.y + 4, width: 16, height: 16 }
}

/// Trusted system control, in the same logical coordinates as the rendered header.
pub fn power_cell(width: i32, height: i32) -> Rect {
    if !valid_screen(width as usize, height as usize) {
        return Rect {
            x: 0,
            y: 0,
            width: 0,
            height: 0,
        };
    }
    Rect {
        x: width - 84,
        y: 0,
        width: 84,
        height: TOP,
    }
}
pub struct DesktopShell {
    #[cfg(feature = "gpu")]
    gpu: Option<super::desktop_shell_gpu::GpuDesktopShell>,
}
impl DesktopShell {
    pub fn cpu() -> Self {
        Self {
            #[cfg(feature = "gpu")]
            gpu: None,
        }
    }
    /// Shares only the device/driver; owns its output, stats and render context.
    #[cfg(feature = "gpu")]
    pub fn gpu(session: &super::GpuSession) -> Result<Self> {
        Ok(Self {
            gpu: Some(super::desktop_shell_gpu::GpuDesktopShell::new(session)?),
        })
    }
    #[cfg(feature = "gpu")]
    pub fn gpu_stats(&self) -> Result<Option<super::GpuStats>> {
        self.gpu.as_ref().map(|g| g.stats()).transpose()
    }
    /// Keeps the scene root's width, height and receipt sequence. Caller owns
    /// the source viewport gate and must ack the scene only after presentation.
    pub fn draw(
        &mut self,
        root: &RenderedFrame,
        windows: &[ShellWindow],
        apps: &[ShellApp],
    ) -> Result<RenderedFrame> {
        self.draw_with_storage_prompt(root, windows, apps, None)
    }
    pub fn draw_with_storage_prompt(
        &mut self,
        root: &RenderedFrame,
        windows: &[ShellWindow],
        apps: &[ShellApp],
        prompt: Option<&StoragePrompt>,
    ) -> Result<RenderedFrame> {
        validate(root, windows, apps)?;
        #[cfg(feature = "gpu")]
        if let Some(gpu) = &self.gpu {
            return gpu.draw(root, windows, apps, prompt);
        }
        let Some(source) = root.cpu_pixels() else {
            bail!("DESKTOP_SHELL_CPU_FRAME_REQUIRED");
        };
        let start = Instant::now();
        let titles: Vec<_> = windows
            .iter()
            .map(|w| ascii_title(&app_for(w, apps).title, TITLE_CHARS))
            .collect();
        let labels: Vec<_> = apps
            .iter()
            .map(|a| ascii_title(&a.title, LABEL_CHARS))
            .collect();
        let mut pixels = Vec::with_capacity(source.len());
        let overlay = prompt.map(|prompt| (storage_prompt_geometry(root.width as i32, root.height as i32, !prompt.has_allow()).expect("validated viewport"), prompt.lines((root.width as i32 - 16).min(600)), prompt.buttons()));
        // Independent scalar oracle: choose an outer first; never paint a
        // lower title through the selected window's preserved root content.
        for y in 0..root.height as i32 {
            for x in 0..root.width as i32 {
                let mut color = source[y as usize * root.width + x as usize];
                if y < TOP {
                    color = BAR;
                    if text_at_2x(b"Ming OS", x - 12, y - 5, 84)
                        || text_at_2x(b"POWER", x - (root.width as i32 - 72), y - 5, 60)
                    {
                        color = INK;
                    }
                } else if y >= root.height as i32 - DOCK {
                    color = BAR;
                    for (i, a) in apps.iter().enumerate() {
                        let cell = dock_cell(root.width as i32, root.height as i32, i, apps.len());
                        if !cell.contains(x, y) {
                            continue;
                        }
                        let (cx, cy) = (x - cell.x, y - cell.y);
                        let active = windows
                            .iter()
                            .any(|w| w.view.app == a.id && w.view.focused && !w.view.minimized);
                        if (8..56).contains(&cx) && (6..36).contains(&cy) {
                            color = if active { FOCUSED } else { TILE };
                        }
                        #[cfg(target_os = "linux")]
                        {
                            let uninstall = dock_uninstall_cell(root.width as i32, root.height as i32, i, apps.len());
                            let storage = dock_storage_cell(root.width as i32, root.height as i32, i, apps.len());
                            if uninstall.contains(x, y) || storage.contains(x, y) {
                                let (icon, offset) = if uninstall.contains(x, y) { (b"U".as_slice(), 4) } else { (b"S".as_slice(), 46) };
                                color = TILE;
                                if text_at_2x(icon, cx - offset, cy - 1, 12) { color = INK; }
                            }
                        }
                        if text_at_2x(&labels[i][..labels[i].len().min(1)], cx - 27, cy - 14, 12)
                            || text_at(&labels[i], cx - 8, cy - 40, 48)
                        {
                            color = INK;
                        }
                        if (29..35).contains(&cx) && (50..54).contains(&cy) {
                            color = match a.state {
                                ShellAppState::Closed => BAR,
                                ShellAppState::Starting | ShellAppState::Minimized => YELLOW,
                                ShellAppState::Running => BLUE,
                                ShellAppState::Failed => RED,
                            };
                        }
                        break;
                    }
                } else if let Some((i, w)) = windows
                    .iter()
                    .enumerate()
                    .rev()
                    .find(|(_, w)| !w.view.minimized && w.view.outer.contains(x, y))
                {
                    let (cx, cy) = (x - w.view.outer.x, y - w.view.outer.y);
                    if cy < TITLE {
                        color = if w.view.focused { FOCUSED } else { TITLE_BG };
                        if cy == TITLE - 1 {
                            color = LINE;
                        }
                        for (center, c) in [(16, RED), (34, YELLOW), (52, GREEN)] {
                            if (cx - center) * (cx - center) + (cy - 16) * (cy - 16) <= 25 {
                                color = c;
                            }
                        }
                        if text_at_2x(&titles[i], cx - 72, cy - 9, w.view.outer.width - 80) {
                            color = INK;
                        }
                    } else if !w.has_content {
                        let failed = app_for(w, apps).state == ShellAppState::Failed;
                        color = if failed { FAILED_BG } else { STARTING_BG };
                        let label = if failed {
                            b"Failed".as_slice()
                        } else {
                            b"Starting".as_slice()
                        };
                        if text_at(label, cx - 12, cy - TITLE - 16, w.view.outer.width - 24) {
                            color = INK;
                        }
                    }
                }
                if let Some((geometry, lines, buttons)) = &overlay {
                    let r = geometry.dialog;
                    if r.contains(x, y) {
                        color = if x == r.x || y == r.y || x == r.x + r.width - 1 || y == r.y + r.height - 1 { LINE } else { 0xfffafbfc };
                        for (i, line) in lines.iter().enumerate() {
                            if text_at(line, x - r.x - 12, y - r.y - 12 - i as i32 * 14, r.width - 24) { color = INK; }
                        }
                        if geometry.deny.contains(x, y) {
                            color = if text_at(buttons.0, x - geometry.deny.x - 22, y - geometry.deny.y - 10, 60) { INK } else { 0xffe2e5e9 };
                        }
                        if let Some(allow) = geometry.allow && allow.contains(x, y) {
                            color = if text_at(buttons.1.unwrap_or_default(), x - allow.x - 22, y - allow.y - 10, 60) { 0xffffffff } else { BLUE };
                        }
                    }
                }
                pixels.push(color);
            }
        }
        Ok(RenderedFrame {
            pixels: FramePixels::Cpu(Arc::new(pixels)),
            render_us: root.render_us.saturating_add(start.elapsed().as_micros()),
            ..root.clone()
        })
    }
}

fn valid_screen(width: usize, height: usize) -> bool {
    (320..=4096).contains(&width) && (240..=4096).contains(&height) && width * height <= MAX_PIXELS
}
fn validate(root: &RenderedFrame, windows: &[ShellWindow], apps: &[ShellApp]) -> Result<()> {
    if !valid_screen(root.width, root.height) {
        bail!("DESKTOP_SHELL_VIEWPORT_LIMIT");
    }
    super::compositor::validate_source(root)?;
    if windows.len() > MAX_WINDOWS || apps.len() > MAX_WINDOWS {
        bail!("DESKTOP_SHELL_ITEM_LIMIT");
    }
    for (i, a) in apps.iter().enumerate() {
        if apps[..i].iter().any(|other| other.id == a.id) {
            bail!("DESKTOP_SHELL_DUPLICATE_APP");
        }
    }
    for (i, w) in windows.iter().enumerate() {
        if w.view.id.0 == 0 || windows[..i].iter().any(|other| other.view.id == w.view.id) {
            bail!("DESKTOP_SHELL_WINDOW_ID");
        }
        if !apps.iter().any(|a| a.id == w.view.app) {
            bail!("DESKTOP_SHELL_UNKNOWN_APP");
        }
        let r = w.view.outer;
        if r.x < 0
            || r.y < TOP
            || r.width < 1
            || r.height <= TITLE
            || i64::from(r.x) + i64::from(r.width) > root.width as i64
            || i64::from(r.y) + i64::from(r.height) > root.height as i64 - i64::from(DOCK)
        {
            bail!("DESKTOP_SHELL_WINDOW_RECT");
        }
    }
    Ok(())
}
pub(super) fn app_for<'a>(w: &ShellWindow, apps: &'a [ShellApp]) -> &'a ShellApp {
    apps.iter()
        .find(|a| a.id == w.view.app)
        .expect("validated app identity")
}
pub(super) fn ascii_title(text: &str, cap: usize) -> Vec<u8> {
    text.chars()
        .take(cap)
        .map(|c| {
            if c.is_ascii() && !c.is_ascii_control() {
                c as u8
            } else {
                b'?'
            }
        })
        .collect()
}
fn text_at(text: &[u8], x: i32, y: i32, width: i32) -> bool {
    if x < 0 || !(0..7).contains(&y) || x >= width.max(0) / 6 * 6 {
        return false;
    }
    let Some(&c) = text.get(x as usize / 6) else {
        return false;
    };
    x % 6 < 5 && font::glyph(c as char)[y as usize] & (1 << (4 - x % 6)) != 0
}

// Fixed integer 2x sampling: 10x14 glyphs, 12px advance. Width remains in
// logical pixels; reject negatives before division truncates to zero.
fn text_at_2x(text: &[u8], x: i32, y: i32, width: i32) -> bool {
    x >= 0 && y >= 0 && text_at(text, x / 2, y / 2, width / 2)
}

#[cfg(test)]
mod storage_prompt_tests {
    use super::*;

    #[test]
    fn minimum_dialog_fits_maximum_identity_and_full_fingerprint() {
        let id = format!("a.{}", "a".repeat(126));
        let fingerprint = "f".repeat(64);
        let prompt = StoragePrompt::new(&id, &"1".repeat(100), &fingerprint, false).unwrap();
        let lines = prompt.lines(304);
        assert!(lines.len() <= 8);
        assert!(lines.iter().all(|line| line.len() <= 46));
        let joined = lines.concat();
        assert!(joined.windows(id.len()).any(|window| window == id.as_bytes()));
        assert!(joined.windows(fingerprint.len()).any(|window| window == fingerprint.as_bytes()));
        assert!(lines.last().unwrap() == b"PUBLISHER NOT VERIFIED");
    }

    #[test]
    fn management_prompt_shows_full_record_and_retained_data() {
        let id = format!("a.{}", "a".repeat(126));
        let fingerprint = "f".repeat(64);
        let prompt = StoragePrompt::management(&id, Some(&fingerprint), false).unwrap();
        let lines = prompt.lines(304);
        assert!(lines.len() <= 8);
        let joined = lines.concat();
        assert!(joined.windows(id.len()).any(|value| value == id.as_bytes()));
        assert!(joined.windows(fingerprint.len()).any(|value| value == fingerprint.as_bytes()));
        assert!(lines.iter().any(|line| line == b"DATA WILL BE KEPT"));
        assert_eq!(prompt.buttons(), (b"CANCEL".as_slice(), Some(b"REVOKE".as_slice())));
        let retry = StoragePrompt::management(&id, None, true).unwrap();
        assert!(retry.lines(304).iter().any(|line| line == b"REVOCATION NOT SAVED"));
        assert_eq!(retry.buttons(), (b"CANCEL".as_slice(), Some(b"RETRY".as_slice())));
    }

    #[test]
    fn public_management_prompt_has_revoke_and_retry_states() {
        let id = "dev.example.public";
        let fingerprint = "f".repeat(64);
        let prompt = StoragePrompt::public_management(id, Some(&fingerprint), false).unwrap();
        assert!(prompt.lines(304).iter().any(|line| line == b"PUBLIC STORAGE ACCESS"));
        assert!(prompt.lines(304).iter().any(|line| line == b"SHARED DATA WILL BE KEPT"));
        assert_eq!(prompt.buttons(), (b"CANCEL".as_slice(), Some(b"REVOKE".as_slice())));
        let retry = StoragePrompt::public_management(id, None, true).unwrap();
        assert!(retry.lines(304).iter().any(|line| line == b"REVOCATION NOT SAVED"));
        assert_eq!(retry.buttons(), (b"CANCEL".as_slice(), Some(b"RETRY".as_slice())));
    }

    #[test]
    fn uninstall_prompt_keeps_data_and_has_retry_and_blocked_states() {
        let id = "dev.example.app";
        let fingerprint = "f".repeat(64);
        let prompt = StoragePrompt::uninstall(id, "1.0.0", &fingerprint, false).unwrap();
        assert!(prompt.lines(304).iter().any(|line| line == b"UNINSTALL APP"));
        assert!(prompt.lines(304).iter().any(|line| line == b"PRIVATE DATA WILL BE KEPT"));
        assert_eq!(prompt.buttons(), (b"CANCEL".as_slice(), Some(b"UNINSTALL".as_slice())));
        let retry = StoragePrompt::uninstall(id, "1.0.0", &fingerprint, true).unwrap();
        assert!(retry.lines(304).iter().any(|line| line == b"UNINSTALL NOT SAVED"));
        assert_eq!(retry.buttons(), (b"CANCEL".as_slice(), Some(b"RETRY".as_slice())));
        let blocked = StoragePrompt::uninstall_blocked(id).unwrap();
        assert!(blocked.lines(304).iter().any(|line| line == b"CLOSE APP FIRST"));
        assert_eq!(blocked.buttons(), (b"CLOSE".as_slice(), None));
    }

    #[test]
    fn uninstall_and_storage_dock_cells_are_separate_and_bounded() {
        let uninstall = dock_uninstall_cell(1280, 800, 0, 1);
        let storage = dock_storage_cell(1280, 800, 0, 1);
        let cell = dock_cell(1280, 800, 0, 1);
        assert!(cell.contains(uninstall.x, uninstall.y));
        assert!(cell.contains(storage.x, storage.y));
        assert!(!uninstall.contains(storage.x, storage.y));
        assert!(!storage.contains(uninstall.x, uninstall.y));
    }
}
