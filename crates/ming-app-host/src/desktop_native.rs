//! Single-window native Ming desktop; offscreen uses the identical controller.
use crate::{
    desktop_catalog::{Catalog, DesktopAction, DesktopOptions},
    desktop_model::{Rect, TITLE, WindowId as MingWindowId},
    graphics::{
        RenderedFrame, RendererKind,
        desktop_shell::{DesktopShell, ShellApp, ShellAppState, ShellWindow, dock_cell},
    },
    ming_desktop::{DesktopOutcome, Session},
};
use anyhow::{Context, Result, bail};
use std::{
    num::NonZeroU32,
    sync::Arc,
    thread,
    time::{Duration, Instant},
};
use winit::{
    application::ApplicationHandler,
    dpi::LogicalSize,
    event::{ElementState, MouseButton, MouseScrollDelta, WindowEvent},
    event_loop::{ActiveEventLoop, ControlFlow, EventLoop},
    keyboard::{KeyCode, PhysicalKey},
    window::{Window, WindowId},
};
fn shell_for(
    renderer: RendererKind,
    #[cfg(feature = "gpu")] gpu: Option<&crate::graphics::GpuSession>,
) -> Result<DesktopShell> {
    match renderer {
        RendererKind::Cpu => Ok(DesktopShell::cpu()),
        RendererKind::Gpu => {
            #[cfg(feature = "gpu")]
            {
                DesktopShell::gpu(gpu.context("DESKTOP_GPU_SESSION")?)
            }
            #[cfg(not(feature = "gpu"))]
            {
                bail!("GPU_FEATURE_REQUIRED");
            }
        }
    }
}
fn render(session: &mut Session, shell: &mut DesktopShell) -> Result<RenderedFrame> {
    let root = session.compose()?;
    let windows: Vec<_> = session
        .views()
        .into_iter()
        .map(|view| ShellWindow {
            has_content: session.has_frame(view.id),
            view,
        })
        .collect();
    let apps: Vec<_> = session
        .catalog
        .apps
        .iter()
        .map(|app| ShellApp {
            id: app.id,
            title: app.title.clone(),
            state: match session.app_status(app.id) {
                "running" => ShellAppState::Running,
                "minimized" => ShellAppState::Minimized,
                "failed" => ShellAppState::Failed,
                "starting" => ShellAppState::Starting,
                _ => ShellAppState::Closed,
            },
        })
        .collect();
    shell.draw(&root, &windows, &apps)
}
fn shell_stats(shell: &DesktopShell) -> Result<Option<crate::graphics::GpuStats>> {
    #[cfg(feature = "gpu")]
    {
        shell.gpu_stats()
    }
    #[cfg(not(feature = "gpu"))]
    {
        let _ = shell;
        Ok(None)
    }
}
pub(crate) fn offscreen(options: DesktopOptions, catalog: Catalog) -> Result<DesktopOutcome> {
    #[cfg(feature = "gpu")]
    let gpu = if options.renderer == RendererKind::Gpu {
        Some(crate::graphics::GpuSession::new()?)
    } else {
        None
    };
    let mut shell = shell_for(
        options.renderer,
        #[cfg(feature = "gpu")]
        gpu.as_ref(),
    )?;
    let mut session = Session::new(
        options,
        catalog,
        #[cfg(feature = "gpu")]
        gpu,
    )?;
    let mut last = None;
    let result = (|| -> Result<()> {
        session.start()?;
        loop {
            if session.tick()? {
                break;
            }
            if session.closing.is_none() && session.dirty {
                let frame = render(&mut session, &mut shell)?;
                session.accepted(frame.sequence, true)?;
                crate::desktop_input::refresh_presentations(&session);
                last = Some(frame);
            }
            thread::sleep(Duration::from_millis(10));
        }
        Ok(())
    })();
    if let Err(error) = &result {
        session.note_state_failure(error);
    }
    let cleanup = session.finish();
    result?;
    cleanup?;
    if let Some(path) = &session.options.snapshot {
        crate::graphics::write_png(path, last.as_ref().context("DESKTOP_SNAPSHOT_NO_FRAME")?)?;
    }
    session.outcome("offscreen", "offscreen-buffer", shell_stats(&shell)?, None)
}
#[derive(Clone, Copy)]
struct Drag {
    window: MingWindowId,
    rect: Rect,
    anchor: (i32, i32),
    resize: bool,
}
struct NativeDesktop {
    #[cfg(feature = "gpu")]
    presenter: Option<crate::graphics::GpuPresenter>,
    surface: Option<softbuffer::Surface<Arc<Window>, Arc<Window>>>,
    context: Option<softbuffer::Context<Arc<Window>>>,
    window: Option<Arc<Window>>,
    pending: Option<(DesktopOptions, Catalog)>,
    session: Option<Session>,
    shell: Option<DesktopShell>,
    last_frame: Option<RenderedFrame>,
    error: Option<anyhow::Error>,
    mouse: Option<(i32, i32)>,
    left: bool,
    drag: Option<Drag>,
    active: bool,
    input: crate::desktop_input::NativeInput,
}
impl NativeDesktop {
    fn new(options: DesktopOptions, catalog: Catalog) -> Self {
        NativeDesktop {
            #[cfg(feature = "gpu")]
            presenter: None,
            surface: None,
            context: None,
            window: None,
            pending: Some((options, catalog)),
            session: None,
            shell: None,
            last_frame: None,
            error: None,
            mouse: None,
            left: false,
            drag: None,
            active: true,
            input: crate::desktop_input::NativeInput::default(),
        }
    }
    fn fail(&mut self, error: anyhow::Error, event_loop: &ActiveEventLoop) {
        if let Some(session) = &mut self.session {
            session.note_state_failure(&error);
        }
        if self.error.is_none() {
            self.error = Some(error);
        }
        if let Some(session) = &mut self.session {
            let _ = session.begin_shutdown();
        }
        event_loop.exit();
    }
    fn resize(&mut self) -> Result<()> {
        let Some(window) = &self.window else {
            return Ok(());
        };
        let size = window.inner_size();
        let scale = window.scale_factor();
        self.apply_resize(size, scale)
    }
    fn apply_resize(&mut self, size: winit::dpi::PhysicalSize<u32>, scale: f64) -> Result<()> {
        if size.width == 0 || size.height == 0 {
            if let Some(session) = &mut self.session {
                session.reset_input()?;
                session.changed();
            }
            self.drag = None;
            self.left = false;
            return Ok(());
        }
        if !scale.is_finite() || scale <= 0.0 {
            bail!("WINDOW_SCALE_INVALID");
        }
        let logical = size.to_logical::<f64>(scale);
        let (w, h) = crate::ui::viewport(logical.width, logical.height);
        if let Some(session) = &mut self.session {
            session.resize(w.max(320) as i32, h.max(240) as i32)?;
        }
        self.drag = None;
        self.left = false;
        Ok(())
    }
    fn project(&self, x: f64, y: f64) -> Option<(i32, i32)> {
        let window = self.window.as_ref()?;
        let session = self.session.as_ref()?;
        let size = window.inner_size();
        crate::ui::project_pointer(
            x,
            y,
            size.width,
            size.height,
            (
                session.options.width as usize,
                session.options.height as usize,
            ),
        )
    }
    fn present(&mut self) -> Result<()> {
        let size = self.window.as_ref().context("DESKTOP_WINDOW")?.inner_size();
        if size.width == 0 || size.height == 0 {
            return Ok(());
        }
        if u64::from(size.width) * u64::from(size.height) > 16_777_216
            || size.width > 8192
            || size.height > 8192
        {
            bail!("WINDOW_SURFACE_LIMIT");
        }
        let session = self.session.as_mut().context("DESKTOP_SESSION")?;
        if session.closing.is_some() {
            return Ok(());
        }
        let fresh = session.dirty || self.last_frame.is_none();
        let frame = if fresh {
            render(session, self.shell.as_mut().context("DESKTOP_SHELL")?)?
        } else {
            self.last_frame.as_ref().expect("cached root").clone()
        };
        #[cfg(feature = "gpu")]
        let accepted = if let Some(presenter) = &mut self.presenter {
            presenter.present(&frame, size.width, size.height)?
        } else {
            Self::present_cpu(
                self.surface.as_mut().context("DESKTOP_SURFACE")?,
                &frame,
                size.width,
                size.height,
            )?
        };
        #[cfg(not(feature = "gpu"))]
        let accepted = Self::present_cpu(
            self.surface.as_mut().context("DESKTOP_SURFACE")?,
            &frame,
            size.width,
            size.height,
        )?;
        if accepted {
            if fresh {
                session.accepted(frame.sequence, false)?;
                crate::desktop_input::refresh_presentations(session);
            } else {
                session.frames_presented += 1;
            }
            self.last_frame = Some(frame);
        }
        Ok(())
    }
    fn present_cpu(
        surface: &mut softbuffer::Surface<Arc<Window>, Arc<Window>>,
        frame: &RenderedFrame,
        width: u32,
        height: u32,
    ) -> Result<bool> {
        surface
            .resize(
                NonZeroU32::new(width).context("WINDOW_SIZE")?,
                NonZeroU32::new(height).context("WINDOW_SIZE")?,
            )
            .map_err(window_error)?;
        let pixels = frame.cpu_pixels().context("CPU_SURFACE_GPU_FRAME")?;
        let mut buffer = surface.buffer_mut().map_err(window_error)?;
        for y in 0..height as usize {
            for x in 0..width as usize {
                buffer[y * width as usize + x] = pixels[(y * frame.height / height as usize)
                    * frame.width
                    + x * frame.width / width as usize]
                    & 0x00ffffff;
            }
        }
        buffer.present().map_err(window_error)?;
        Ok(true)
    }
    fn action(&mut self, action: DesktopAction) -> Result<()> {
        self.session
            .as_mut()
            .context("DESKTOP_SESSION")?
            .user_action(&action)
    }
    fn down(&mut self) -> Result<()> {
        let Some((x, y)) = self.mouse else {
            return Ok(());
        };
        let session = self.session.as_mut().context("DESKTOP_SESSION")?;
        if !session.pointer_sink_ready() {
            return Ok(());
        }
        if crate::graphics::desktop_shell::power_cell(session.options.width, session.options.height)
            .contains(x, y)
        {
            return self.action(DesktopAction::Shutdown);
        }
        let apps = session.catalog.apps.clone();
        for (index, app) in apps.iter().enumerate() {
            if dock_cell(
                session.options.width,
                session.options.height,
                index,
                apps.len(),
            )
            .contains(x, y)
            {
                return self.action(DesktopAction::Launch { app: app.id });
            }
        }
        let Some(view) = session
            .views()
            .into_iter()
            .rev()
            .find(|view| !view.minimized && view.outer.contains(x, y))
        else {
            return Ok(());
        };
        if y < view.outer.y + TITLE {
            for (index, offset) in [16, 34, 52].iter().enumerate() {
                if (x - view.outer.x - offset).abs() <= 8 && (y - view.outer.y - 16).abs() <= 8 {
                    let action = match index {
                        0 => DesktopAction::Close { app: view.app },
                        1 => DesktopAction::Minimize { app: view.app },
                        _ if view.maximized => DesktopAction::Restore { app: view.app },
                        _ => DesktopAction::Maximize { app: view.app },
                    };
                    return self.action(action);
                }
            }
            session.reset_input()?;
            session.perform(&DesktopAction::Focus { app: view.app })?;
            if !view.maximized {
                self.drag = Some(Drag {
                    window: view.id,
                    rect: view.outer,
                    anchor: (x, y),
                    resize: false,
                });
            }
        } else if x >= view.outer.x + view.outer.width - 12
            && y >= view.outer.y + view.outer.height - 12
        {
            session.reset_input()?;
            session.perform(&DesktopAction::Focus { app: view.app })?;
            self.drag = Some(Drag {
                window: view.id,
                rect: view.outer,
                anchor: (x, y),
                resize: true,
            });
        } else {
            session.pointer_begin_modifiers(x, y, self.input.modifier_bits())?;
        }
        Ok(())
    }
    fn motion(&mut self) -> Result<()> {
        let Some((x, y)) = self.mouse else {
            return Ok(());
        };
        let session = self.session.as_mut().context("DESKTOP_SESSION")?;
        if let Some(drag) = self.drag
            && self.left
        {
            if session.scene.model().view(drag.window).is_err() {
                self.drag = None;
                self.left = false;
                session.reset_input()?;
                return Ok(());
            }
            let dx = x - drag.anchor.0;
            let dy = y - drag.anchor.1;
            let rect = if drag.resize {
                Rect {
                    width: (drag.rect.width + dx).max(1),
                    height: (drag.rect.height + dy).max(1),
                    ..drag.rect
                }
            } else {
                Rect {
                    x: drag.rect.x + dx,
                    y: drag.rect.y + dy,
                    ..drag.rect
                }
            };
            session.geometry(drag.window, rect)?;
        } else {
            session.pointer_update_modifiers(x, y, self.left, self.input.modifier_bits())?;
        }
        Ok(())
    }
}
impl ApplicationHandler for NativeDesktop {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let result = (|| -> Result<()> {
            let (options, catalog) = self.pending.take().context("DESKTOP_OPTIONS")?;
            let window = Arc::new(
                event_loop.create_window(
                    Window::default_attributes()
                        .with_title("Ming Desktop")
                        .with_inner_size(LogicalSize::new(
                            f64::from(options.width),
                            f64::from(options.height),
                        ))
                        .with_min_inner_size(LogicalSize::new(320.0, 240.0)),
                )?,
            );
            window.set_ime_allowed(false);
            #[cfg(target_os = "linux")]
            window.set_fullscreen(Some(winit::window::Fullscreen::Borderless(None)));
            #[cfg(feature = "gpu")]
            let gpu = if options.renderer == RendererKind::Gpu {
                let presenter = crate::graphics::GpuPresenter::new(window.clone())?;
                let session = presenter.session.clone();
                self.presenter = Some(presenter);
                Some(session)
            } else {
                None
            };
            #[cfg(not(feature = "gpu"))]
            if options.renderer == RendererKind::Gpu {
                bail!("GPU_FEATURE_REQUIRED");
            }
            if options.renderer == RendererKind::Cpu {
                let context = softbuffer::Context::new(window.clone()).map_err(window_error)?;
                self.surface =
                    Some(softbuffer::Surface::new(&context, window.clone()).map_err(window_error)?);
                self.context = Some(context);
            }
            self.shell = Some(shell_for(
                options.renderer,
                #[cfg(feature = "gpu")]
                gpu.as_ref(),
            )?);
            self.session = Some(Session::new(
                options,
                catalog,
                #[cfg(feature = "gpu")]
                gpu,
            )?);
            self.window = Some(window.clone());
            self.resize()?;
            self.session.as_mut().expect("created session").start()?;
            window.request_redraw();
            Ok(())
        })();
        if let Err(error) = result {
            self.fail(error, event_loop);
        }
    }
    fn window_event(&mut self, event_loop: &ActiveEventLoop, id: WindowId, event: WindowEvent) {
        if !self.window.as_ref().is_some_and(|w| w.id() == id) {
            return;
        }
        let result = (|| -> Result<()> {
            match event {
                WindowEvent::CloseRequested => {
                    self.session
                        .as_mut()
                        .context("DESKTOP_SESSION")?
                        .begin_shutdown()?;
                    if let Some(window) = &self.window {
                        window.set_visible(false);
                    }
                }
                WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. } => {
                    self.resize()?
                }
                WindowEvent::RedrawRequested => self.present()?,
                WindowEvent::Focused(active) => {
                    self.active = active;
                    if !active {
                        self.input.lost_focus();
                        self.left = false;
                        self.drag = None;
                        self.session
                            .as_mut()
                            .context("DESKTOP_SESSION")?
                            .reset_input()?;
                    }
                }
                WindowEvent::CursorMoved { position, .. } => {
                    self.mouse = self.project(position.x, position.y);
                    if self.active {
                        self.motion()?;
                    }
                }
                WindowEvent::CursorLeft { .. } => {
                    self.mouse = None;
                }
                WindowEvent::MouseInput {
                    state,
                    button: MouseButton::Left,
                    ..
                } if self.active => {
                    self.left = state == ElementState::Pressed;
                    if self.left {
                        self.down()?;
                    } else {
                        self.drag = None;
                        if let Some((x, y)) = self.mouse {
                            self.session
                                .as_mut()
                                .context("DESKTOP_SESSION")?
                                .pointer_update_modifiers(
                                    x,
                                    y,
                                    false,
                                    self.input.modifier_bits(),
                                )?;
                        } else {
                            self.session
                                .as_mut()
                                .context("DESKTOP_SESSION")?
                                .reset_input()?;
                        }
                    }
                }
                WindowEvent::MouseWheel { delta, .. } if self.active => {
                    if let Some((x, y)) = self.mouse {
                        let raw = match delta {
                            MouseScrollDelta::LineDelta(_, y) => f64::from(y) * 100.0,
                            MouseScrollDelta::PixelDelta(p) => p.y,
                        };
                        let value = raw.round().clamp(-2000.0, 2000.0) as i32;
                        if value != 0 {
                            self.session
                                .as_mut()
                                .context("DESKTOP_SESSION")?
                                .scroll(x, y, value)?;
                        }
                    }
                }
                WindowEvent::ModifiersChanged(modifiers) => self.input.modifiers(modifiers.state()),
                WindowEvent::Ime(event) if self.active => {
                    let session = self.session.as_mut().context("DESKTOP_SESSION")?;
                    self.input.ime(event, session);
                }
                WindowEvent::KeyboardInput { event, .. } if self.active => {
                    let session = self.session.as_mut().context("DESKTOP_SESSION")?;
                    if self.input.shutdown_shortcut(&event) {
                        session.perform(&DesktopAction::Shutdown)?;
                    } else if !self.input.key(&event, session)
                        && let PhysicalKey::Code(code) = event.physical_key
                    {
                        if code == KeyCode::Escape && event.state.is_pressed() {
                            if session.options.duration_ms == 0 {
                                session.reset_input()?;
                                self.drag = None;
                                self.left = false;
                            } else {
                                session.begin_shutdown()?;
                                if let Some(window) = &self.window {
                                    window.set_visible(false);
                                }
                            }
                        } else if let Some(index) = match code {
                            KeyCode::KeyF => Some(0),
                            KeyCode::KeyR => Some(1),
                            KeyCode::Digit1 | KeyCode::Numpad1 => Some(2),
                            _ => None,
                        } {
                            session.key(index, event.state.is_pressed())?;
                        }
                    }
                }
                _ => {}
            }
            Ok(())
        })();
        if let Err(error) = result {
            self.fail(error, event_loop);
        }
    }
    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        let result = (|| -> Result<bool> {
            let Some(session) = &mut self.session else {
                return Ok(false);
            };
            let done = session.tick()?;
            if let Some(window) = &self.window {
                self.input.refresh(window, session);
            }
            if session.dirty
                && session.closing.is_none()
                && let Some(window) = &self.window
            {
                window.request_redraw();
            }
            Ok(done)
        })();
        match result {
            Ok(true) => event_loop.exit(),
            Err(error) => self.fail(error, event_loop),
            Ok(false) => {}
        }
        event_loop.set_control_flow(ControlFlow::WaitUntil(
            Instant::now() + Duration::from_millis(10),
        ));
    }
}
pub(crate) fn native(options: DesktopOptions, catalog: Catalog) -> Result<DesktopOutcome> {
    let event_loop = EventLoop::new()?;
    #[cfg(target_os = "linux")]
    let window_system = {
        use winit::platform::wayland::EventLoopExtWayland;
        if event_loop.is_wayland() {
            "wayland"
        } else {
            "x11"
        }
    };
    #[cfg(target_os = "windows")]
    let window_system = "win32";
    #[cfg(not(any(target_os = "windows", target_os = "linux")))]
    let window_system = "other";
    let mut app = NativeDesktop::new(options, catalog);
    let loop_result = event_loop.run_app(&mut app);
    let cleanup = if let Some(session) = &mut app.session {
        session.finish()
    } else {
        Ok(())
    };
    loop_result?;
    if let Some(error) = app.error.take() {
        return Err(error);
    }
    cleanup?;
    let presentation_backend = if app.presenter_is_gpu() {
        "wgpu-surface"
    } else {
        "softbuffer"
    };
    let session = app.session.as_mut().context("DESKTOP_NO_SESSION")?;
    if let Some(path) = &session.options.snapshot {
        crate::graphics::write_png(
            path,
            app.last_frame
                .as_ref()
                .context("DESKTOP_SNAPSHOT_NO_FRAME")?,
        )?;
    }
    #[cfg(feature = "gpu")]
    let surface_stats = app
        .presenter
        .as_ref()
        .map(|p| p.session.stats())
        .transpose()?;
    #[cfg(not(feature = "gpu"))]
    let surface_stats = None;
    let outcome = session.outcome(
        window_system,
        presentation_backend,
        shell_stats(app.shell.as_ref().context("DESKTOP_SHELL")?)?,
        surface_stats,
    )?;
    #[cfg(feature = "gpu")]
    app.presenter.take();
    app.surface.take();
    app.context.take();
    app.window.take();
    Ok(outcome)
}
impl NativeDesktop {
    fn presenter_is_gpu(&self) -> bool {
        #[cfg(feature = "gpu")]
        {
            self.presenter.is_some()
        }
        #[cfg(not(feature = "gpu"))]
        {
            false
        }
    }
}
fn window_error(error: softbuffer::SoftBufferError) -> anyhow::Error {
    anyhow::anyhow!("WINDOW_BACKEND: {error}")
}

#[cfg(test)]
mod input_tests {
    use super::*;
    fn app() -> NativeDesktop {
        let options = DesktopOptions::parse(
            [
                "unused",
                "--renderer",
                "cpu",
                "--width",
                "800",
                "--height",
                "600",
            ]
            .into_iter()
            .map(std::ffi::OsString::from),
        )
        .unwrap();
        let catalog = Catalog {
            apps: vec![crate::desktop_catalog::CatalogEntry {
                id: 1,
                title: "One".into(),
                manifest_path: "unused".into(),
            }],
            autostart: vec![],
        };
        let session = Session::new(
            options,
            catalog.clone(),
            #[cfg(feature = "gpu")]
            None,
        )
        .unwrap();
        let other = DesktopOptions::parse(
            ["unused", "--renderer", "cpu"]
                .into_iter()
                .map(std::ffi::OsString::from),
        )
        .unwrap();
        let mut app = NativeDesktop::new(other, catalog);
        app.session = Some(session);
        app
    }
    #[test]
    fn unpresented_chrome_never_closes_or_launches_from_new_geometry() {
        let mut app = app();
        let (id, _) = app
            .session
            .as_mut()
            .unwrap()
            .scene
            .model_mut()
            .open(1)
            .unwrap();
        let outer = app
            .session
            .as_ref()
            .unwrap()
            .scene
            .model()
            .view(id)
            .unwrap()
            .outer;
        app.mouse = Some((outer.x + 16, outer.y + 16));
        assert!(
            app.down().is_ok(),
            "Unpresented chrome must be ignored before resolving an app instance"
        );
        assert_eq!(app.session.as_ref().unwrap().views().len(), 1);
    }
    #[test]
    fn topbar_power_control_can_end_a_persistent_desktop() {
        let mut app = app();
        let session = app.session.as_mut().unwrap();
        session.options.duration_ms = 0;
        let root = session.compose().unwrap();
        session.accepted(root.sequence, false).unwrap();
        let width = session.options.width;
        app.mouse = Some((width - 30, 12));
        app.down().unwrap();
        assert!(app.session.as_ref().unwrap().closing.is_some());
    }
    #[test]
    fn zero_sized_native_surface_preserves_desktop_and_window_geometry() {
        let mut app = app();
        let session = app.session.as_mut().unwrap();
        let (id, _) = session.scene.model_mut().open(1).unwrap();
        let before = session.scene.model().view(id).unwrap().outer;
        app.apply_resize(winit::dpi::PhysicalSize::new(0, 0), 1.0)
            .unwrap();
        let session = app.session.as_ref().unwrap();
        assert_eq!((session.options.width, session.options.height), (800, 600));
        assert_eq!(session.scene.model().view(id).unwrap().outer, before);
    }
    #[test]
    fn window_exit_during_host_drag_cancels_instead_of_failing_the_desktop() {
        let mut app = app();
        app.mouse = Some((10, 10));
        app.left = true;
        app.drag = Some(Drag {
            window: MingWindowId(999),
            rect: Rect {
                x: 0,
                y: 24,
                width: 400,
                height: 300,
            },
            anchor: (0, 0),
            resize: false,
        });
        app.motion().unwrap();
        assert!(app.drag.is_none());
        assert!(!app.left);
    }
}
