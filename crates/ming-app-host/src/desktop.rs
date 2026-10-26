use crate::{
    supervisor::{self, Options, Outcome},
    ui::{InputEvent, KeyLatch, PointerSample, ViewBridge},
};
use anyhow::{Context, Result, bail};
use std::{
    num::NonZeroU32,
    sync::{Arc, atomic::Ordering, mpsc},
    thread,
    time::{Duration, Instant},
};
use winit::{
    application::ApplicationHandler,
    dpi::LogicalSize,
    event::{ElementState, Ime, MouseButton, MouseScrollDelta, TouchPhase, WindowEvent},
    event_loop::{ActiveEventLoop, ControlFlow, EventLoop},
    keyboard::{Key, KeyCode, NamedKey, PhysicalKey},
    window::{Window, WindowId},
};

struct Viewer {
    #[cfg(feature = "gpu")]
    gpu_presenter: Option<crate::graphics::GpuPresenter>,
    // The surface must be released before its display context/window.
    surface: Option<softbuffer::Surface<Arc<Window>, Arc<Window>>>,
    context: Option<softbuffer::Context<Arc<Window>>>,
    window: Option<Arc<Window>>,
    options: Option<Options>,
    bridge: ViewBridge,
    events: mpsc::SyncSender<InputEvent>,
    receiver: Option<mpsc::Receiver<Result<Outcome>>>,
    worker: Option<thread::JoinHandle<()>>,
    result: Option<Result<Outcome>>,
    started: Instant,
    closing: Option<Instant>,
    auto_close: Option<Duration>,
    pointer: PointerSample,
    mouse: Option<(i32, i32)>,
    left: bool,
    keys: KeyLatch,
    active_touch: Option<u64>,
    last_sequence: u64,
    last_presented_viewport: Option<(usize, usize)>,
    needs_redraw: bool,
    frames_presented: u64,
    input_gate: crate::ui::ViewportGate,
}
impl Viewer {
    fn fail(&mut self, error: anyhow::Error, event_loop: &ActiveEventLoop) {
        self.bridge.cancel.store(true, Ordering::SeqCst);
        if self.result.is_none() {
            self.result = Some(Err(error));
        }
        event_loop.exit();
    }
    fn close(&mut self) {
        if self.closing.is_none() {
            self.needs_redraw = false;
            self.bridge.request_close();
            self.closing = Some(Instant::now());
            if let Some(window) = &self.window {
                window.set_visible(false);
            }
        }
    }
    fn dispatch(&self, event: InputEvent) {
        match event[0] {
            0 => self.bridge.request_close(),
            2 => self.bridge.zoom(event[1]),
            3 => self.bridge.pan(event[1], event[2]),
            _ => {
                let _ = self.events.try_send(event);
            }
        }
    }
    fn pointer_update(&mut self, position: Option<(i32, i32)>, down: bool) {
        if let Some(event) = self.pointer.sample(position, down) {
            self.dispatch(event);
        }
    }
    fn resize(&mut self) {
        if let Some(window) = self.window.as_ref().cloned() {
            let size = window.inner_size().to_logical::<f64>(window.scale_factor());
            let viewport = crate::ui::viewport(size.width, size.height);
            self.apply_resize(viewport);
            window.request_redraw();
        }
    }
    fn apply_resize(&mut self, viewport: (usize, usize)) {
        self.reset_input();
        self.input_gate.resize(viewport);
        self.needs_redraw = true;
        self.bridge.resize(viewport.0 as i32, viewport.1 as i32);
    }
    fn record_presented(&mut self, sequence: u64, viewport: (usize, usize)) {
        self.last_sequence = sequence;
        self.last_presented_viewport = Some(viewport);
        self.input_gate.present(viewport);
        self.frames_presented += 1;
        self.needs_redraw = false;
    }
    fn wants_redraw(&self, sequence: u64, width: u32, height: u32) -> bool {
        self.closing.is_none()
            && width > 0
            && height > 0
            && (self.needs_redraw || sequence != self.last_sequence)
    }
    fn project(&self, x: f64, y: f64) -> Option<(i32, i32)> {
        let viewport = self.input_gate.viewport()?;
        let window = self.window.as_ref()?;
        let physical = window.inner_size();
        crate::ui::project_pointer(x, y, physical.width, physical.height, viewport)
    }
    fn present(&mut self) -> Result<()> {
        let Some(window) = self.window.as_ref().cloned() else {
            return Ok(());
        };
        let size = window.inner_size();
        if size.width == 0 || size.height == 0 {
            return Ok(());
        }
        if u64::from(size.width) * u64::from(size.height) > 16_777_216
            || size.width > 8192
            || size.height > 8192
        {
            bail!("WINDOW_SURFACE_LIMIT");
        }
        let frame = self.bridge.latest.lock().expect("frame slot").clone();
        #[cfg(feature = "gpu")]
        if let Some(presenter) = &mut self.gpu_presenter {
            if let Some(frame) = frame
                && presenter.present(&frame, size.width, size.height)?
            {
                self.record_presented(frame.sequence, (frame.width, frame.height));
            }
            return Ok(());
        }
        let frame_viewport = frame.as_ref().map(|f| (f.sequence, (f.width, f.height)));
        let surface = self.surface.as_mut().context("WINDOW_SURFACE")?;
        surface
            .resize(
                NonZeroU32::new(size.width).context("WINDOW_SIZE")?,
                NonZeroU32::new(size.height).context("WINDOW_SIZE")?,
            )
            .map_err(window_error)?;
        let mut buffer = surface.buffer_mut().map_err(window_error)?;
        let width = size.width as usize;
        let height = size.height as usize;
        if let Some(frame) = frame {
            let pixels = frame.cpu_pixels().context("CPU_SURFACE_GPU_FRAME")?;
            let xs: Vec<usize> = (0..width).map(|x| x * frame.width / width).collect();
            for y in 0..height {
                let source = (y * frame.height / height) * frame.width;
                let row = &mut buffer[y * width..(y + 1) * width];
                for (pixel, &x) in row.iter_mut().zip(&xs) {
                    *pixel = pixels[source + x] & 0x00ffffff;
                }
            }
        } else {
            buffer.fill(0x0011151b);
        }
        buffer.present().map_err(window_error)?;
        if let Some((sequence, viewport)) = frame_viewport {
            self.record_presented(sequence, viewport);
        }
        Ok(())
    }
    fn key(&mut self, event: winit::event::KeyEvent) {
        let physical = match event.physical_key {
            PhysicalKey::Code(KeyCode::KeyF) => Some(0),
            PhysicalKey::Code(KeyCode::KeyR) => Some(1),
            PhysicalKey::Code(KeyCode::Digit1 | KeyCode::Numpad1) => Some(2),
            PhysicalKey::Code(KeyCode::Escape) => Some(3),
            _ => None,
        };
        if let Some(index) = physical {
            if let Some(message) = self.keys.edge(index, event.state.is_pressed()) {
                self.dispatch(message);
                if message[0] == 0 {
                    self.close();
                }
            }
            return;
        }
        if event.state != ElementState::Pressed || event.repeat {
            return;
        }
        let index = match &event.logical_key {
            Key::Character(c) if c.eq_ignore_ascii_case("f") => Some(0),
            Key::Character(c) if c.eq_ignore_ascii_case("r") => Some(1),
            Key::Character(c) if c == "1" => Some(2),
            Key::Named(NamedKey::Escape) => Some(3),
            _ => None,
        };
        if let Some(index) = index {
            if index == 3 {
                self.close();
            } else if let Some(message) = self.keys.character(index) {
                self.dispatch(message);
            }
        }
    }
}
impl ApplicationHandler for Viewer {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let result = (|| -> Result<()> {
            let window = Arc::new(
                event_loop.create_window(
                    Window::default_attributes()
                        .with_title("Ming Photo")
                        .with_inner_size(LogicalSize::new(1100.0, 760.0))
                        .with_min_inner_size(LogicalSize::new(320.0, 240.0)),
                )?,
            );
            window.set_ime_allowed(false);
            if let Some(monitor) = window
                .current_monitor()
                .or_else(|| event_loop.primary_monitor())
            {
                let bounds = monitor.size();
                let outer = window.outer_size();
                let origin = monitor.position();
                window.set_outer_position(winit::dpi::PhysicalPosition::new(
                    origin.x + (bounds.width.saturating_sub(outer.width) / 2) as i32,
                    origin.y + (bounds.height.saturating_sub(outer.height) / 2) as i32,
                ));
            }
            let mut options = self.options.take().context("VIEWER_OPTIONS")?;
            #[cfg(feature = "gpu")]
            if options.renderer == crate::graphics::RendererKind::Gpu {
                let presenter = crate::graphics::GpuPresenter::new(window.clone())?;
                options.gpu_session = Some(presenter.session.clone());
                self.gpu_presenter = Some(presenter);
            } else {
                let context = softbuffer::Context::new(window.clone()).map_err(window_error)?;
                self.surface =
                    Some(softbuffer::Surface::new(&context, window.clone()).map_err(window_error)?);
                self.context = Some(context);
            }
            #[cfg(not(feature = "gpu"))]
            {
                if options.renderer == crate::graphics::RendererKind::Gpu {
                    bail!("GPU_FEATURE_REQUIRED");
                }
                let context = softbuffer::Context::new(window.clone()).map_err(window_error)?;
                self.surface =
                    Some(softbuffer::Surface::new(&context, window.clone()).map_err(window_error)?);
                self.context = Some(context);
            }
            self.window = Some(window);
            options.live = Some(self.bridge.clone());
            let (tx, rx) = mpsc::sync_channel(1);
            self.receiver = Some(rx);
            self.worker = Some(thread::spawn(move || {
                let _ = tx.send(supervisor::run(options));
            }));
            self.resize();
            Ok(())
        })();
        if let Err(error) = result {
            self.fail(error, event_loop);
        }
    }
    fn window_event(&mut self, event_loop: &ActiveEventLoop, id: WindowId, event: WindowEvent) {
        if self.result.is_some() {
            return;
        }
        if !self.window.as_ref().is_some_and(|w| w.id() == id) {
            return;
        }
        match event {
            WindowEvent::CloseRequested => self.close(),
            WindowEvent::Focused(false) => self.reset_input(),
            WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. } => self.resize(),
            WindowEvent::RedrawRequested => {
                self.needs_redraw = true;
                if self.closing.is_none()
                    && let Err(error) = self.present()
                {
                    self.fail(error, event_loop);
                }
            }
            WindowEvent::KeyboardInput { event, .. } => self.key(event),
            WindowEvent::Ime(Ime::Commit(text)) => {
                for c in text.chars().take(16) {
                    let index = match c {
                        'f' | 'F' => 0,
                        'r' | 'R' => 1,
                        '1' => 2,
                        _ => continue,
                    };
                    if let Some(message) = self.keys.character(index) {
                        self.dispatch(message);
                    }
                }
            }
            WindowEvent::CursorMoved { position, .. } if self.active_touch.is_none() => {
                self.mouse = self.project(position.x, position.y);
                if self.input_gate.viewport().is_some() {
                    self.pointer_update(self.mouse, self.left);
                } else {
                    self.pointer.cancel();
                    self.left = false;
                }
            }
            WindowEvent::MouseInput {
                state,
                button: MouseButton::Left,
                ..
            } if self.active_touch.is_none() => self.mouse_button(state.is_pressed()),
            WindowEvent::MouseWheel { delta, .. } => {
                if self.input_gate.viewport().is_none() {
                    return;
                }
                let value = match delta {
                    MouseScrollDelta::LineDelta(_, y) => y as f64 * 100.0,
                    MouseScrollDelta::PixelDelta(p) => p.y,
                };
                let delta = value.round().clamp(-2000.0, 2000.0) as i32;
                if delta != 0 {
                    self.bridge.zoom(delta);
                }
            }
            WindowEvent::Touch(touch) => {
                self.touch_input(touch.id, touch.phase, (touch.location.x, touch.location.y))
            }

            _ => {}
        }
    }
    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        if let Some(receiver) = &self.receiver
            && crate::ui::poll_completion(&mut self.result, receiver)
        {
            event_loop.exit();
            return;
        }
        if self.auto_close.is_some_and(|d| self.started.elapsed() >= d) {
            self.close();
        }
        if let Some(clock) = self.closing {
            if clock.elapsed() > Duration::from_millis(200) {
                self.bridge.cancel.store(true, Ordering::SeqCst);
            }
        } else {
            let sequence = self
                .bridge
                .latest
                .lock()
                .expect("frame slot")
                .as_ref()
                .map_or(0, |f| f.sequence);
            if let Some(window) = &self.window {
                let size = window.inner_size();
                if self.wants_redraw(sequence, size.width, size.height) {
                    window.request_redraw();
                }
            }
        }
        event_loop.set_control_flow(ControlFlow::WaitUntil(
            Instant::now() + Duration::from_millis(16),
        ));
    }
}
pub fn view(options: Options) -> Result<Outcome> {
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
    #[cfg(not(any(target_os = "linux", target_os = "windows")))]
    let window_system = "other";
    let mut app = Viewer::new(options);
    let loop_result = event_loop.run_app(&mut app);
    app.bridge.cancel.store(true, Ordering::SeqCst);
    if let Some(worker) = app.worker.take() {
        let _ = worker.join();
    }
    #[cfg(feature = "gpu")]
    app.gpu_presenter.take();
    app.surface.take();
    app.context.take();
    app.window.take();
    loop_result?;
    let mut outcome = app.result.context("VIEWER_ENDED_WITHOUT_RESULT")??;
    outcome.window_system = window_system;
    outcome.frames_presented = app.frames_presented;
    outcome.presented_frame =
        app.last_presented_viewport
            .map(|(width, height)| supervisor::PresentedFrameSummary {
                sequence: app.last_sequence,
                width,
                height,
            });
    outcome.presentation_backend = if outcome.gpu.is_some() {
        "wgpu-surface"
    } else {
        "softbuffer"
    };
    outcome.refresh_gpu_stats()?;
    Ok(outcome)
}

fn window_error(error: softbuffer::SoftBufferError) -> anyhow::Error {
    anyhow::anyhow!("WINDOW_BACKEND: {error}")
}

impl Viewer {
    fn reset_input(&mut self) {
        self.left = false;
        self.mouse = None;
        self.active_touch = None;
        self.pointer.cancel();
        self.keys.cancel();
        self.bridge.clear_input();
    }
}

impl Viewer {
    fn new(options: Options) -> Self {
        let (bridge, events) = ViewBridge::new();
        let auto_close = options.auto_close_ms.map(Duration::from_millis);
        Self {
            #[cfg(feature = "gpu")]
            gpu_presenter: None,
            surface: None,
            context: None,
            window: None,
            options: Some(options),
            bridge,
            events,
            receiver: None,
            worker: None,
            result: None,
            started: Instant::now(),
            closing: None,
            auto_close,
            pointer: PointerSample::default(),
            mouse: None,
            left: false,
            keys: KeyLatch::default(),
            active_touch: None,
            last_sequence: 0,
            last_presented_viewport: None,
            needs_redraw: true,
            frames_presented: 0,
            input_gate: crate::ui::ViewportGate::default(),
        }
    }
    fn mouse_button(&mut self, down: bool) {
        if self.input_gate.viewport().is_none() {
            return;
        }
        self.left = down;
        self.pointer_update(self.mouse, self.left);
    }
    fn touch_input(&mut self, id: u64, phase: TouchPhase, position: (f64, f64)) {
        if self.input_gate.viewport().is_none() {
            return;
        }
        if phase == TouchPhase::Started && self.active_touch.is_none() {
            self.active_touch = Some(id);
        }
        if self.active_touch == Some(id) {
            self.mouse = self.project(position.0, position.1);
            self.left = matches!(phase, TouchPhase::Started | TouchPhase::Moved);
            self.pointer_update(self.mouse, self.left);
            if !self.left {
                self.active_touch = None;
            }
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn skipped_redraw_after_same_logical_resize_retries_existing_sequence() {
        let mut app = Viewer::new(Options::new("unused.json".into()));
        let viewport = (1100, 760);
        app.apply_resize(viewport);
        app.record_presented(7, viewport);
        assert!(!app.wants_redraw(7, 1100, 760));
        app.apply_resize(viewport);
        assert!(app.input_gate.viewport().is_none());
        // A transient Surface failure leaves the invalidated frame pending.
        assert!(app.wants_redraw(7, 1100, 760));
        assert!(app.wants_redraw(7, 1100, 760));
        assert!(!app.wants_redraw(7, 0, 0));
        app.record_presented(7, viewport);
        assert!(!app.wants_redraw(7, 1100, 760));
        assert_eq!(app.input_gate.viewport(), Some(viewport));
        app.apply_resize(viewport);
        app.close();
        assert!(!app.wants_redraw(7, 1100, 760));
    }
    #[test]
    fn mouse_press_during_resize_does_not_latch() {
        let mut app = Viewer::new(Options::new("unused.json".into()));
        app.input_gate.resize((1100, 760));
        app.mouse = Some((10, 10));
        app.mouse_button(true);
        assert!(!app.left);
        assert!(app.pointer.sample(Some((20, 20)), false).is_none());
    }
    #[test]
    fn touch_during_resize_does_not_latch() {
        let mut app = Viewer::new(Options::new("unused.json".into()));
        app.input_gate.resize((1100, 760));
        app.touch_input(1, TouchPhase::Started, (10.0, 10.0));
        assert!(app.active_touch.is_none());
        assert!(!app.left);
    }
}
