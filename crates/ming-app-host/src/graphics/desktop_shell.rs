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
        validate(root, windows, apps)?;
        #[cfg(feature = "gpu")]
        if let Some(gpu) = &self.gpu {
            return gpu.draw(root, windows, apps);
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
