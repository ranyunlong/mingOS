use crate::graphics::RenderedFrame;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
    mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TryRecvError},
};
use std::time::Duration;
pub type InputEvent = [i32; 4];
#[derive(Clone, Debug)]
pub struct ViewBridge {
    pub latest: Arc<Mutex<Option<RenderedFrame>>>,
    pub cancel: Arc<AtomicBool>,
    close: Arc<AtomicBool>,
    worker_result_accepted: Arc<Mutex<bool>>,
    text_input: Arc<Mutex<crate::text_input::InputChannel>>,
    accepted_input_frame: Arc<Mutex<Option<(u64, u32, u32)>>>,
    resize: Arc<Mutex<Option<InputEvent>>>,
    viewport: Arc<Mutex<Option<(i32, i32)>>>,
    motion: Arc<Mutex<[i32; 3]>>,
    events: Arc<Mutex<Receiver<InputEvent>>>,
}
impl ViewBridge {
    pub fn new() -> (Self, SyncSender<InputEvent>) {
        let (tx, rx) = mpsc::sync_channel(128);
        (
            Self {
                latest: Arc::new(Mutex::new(None)),
                cancel: Arc::new(AtomicBool::new(false)),
                close: Arc::new(AtomicBool::new(false)),
                worker_result_accepted: Arc::new(Mutex::new(false)),
                text_input: Arc::new(Mutex::new(crate::text_input::InputChannel::new())),
                accepted_input_frame: Arc::new(Mutex::new(None)),
                resize: Arc::new(Mutex::new(None)),
                viewport: Arc::new(Mutex::new(None)),
                motion: Arc::new(Mutex::new([0; 3])),
                events: Arc::new(Mutex::new(rx)),
            },
            tx,
        )
    }
    pub fn publish(&self, frame: RenderedFrame) {
        let _ = self.text_input.lock().expect("input channel").publish(
            frame.sequence,
            frame.width as u32,
            frame.height as u32,
        );
        *self.latest.lock().expect("frame slot") = Some(frame);
    }
    pub fn request_close(&self) {
        self.close.store(true, Ordering::SeqCst);
    }
    // Parent-only receipt; accepting it never clears an earlier explicit cancellation.
    pub(crate) fn accept_worker_result(&self) {
        *self.worker_result_accepted.lock().expect("worker result state") = true;
    }
    pub(crate) fn cancel_if_executing(&self) {
        // Keep the check and cancellation in one critical section with Result acceptance.
        let accepted = self.worker_result_accepted.lock().expect("worker result state");
        if !*accepted {
            self.cancel.store(true, Ordering::SeqCst);
        }
    }
    pub fn resize(&self, w: i32, h: i32) {
        *self.viewport.lock().expect("viewport slot") = Some((w, h));
        *self.resize.lock().expect("resize slot") = Some([1, w, h, 0]);
    }
    pub fn pan(&self, dx: i32, dy: i32) {
        let mut m = self.motion.lock().expect("motion slot");
        m[0] = (i64::from(m[0]) + i64::from(dx)).clamp(-50000, 50000) as i32;
        m[1] = (i64::from(m[1]) + i64::from(dy)).clamp(-50000, 50000) as i32;
    }
    pub fn zoom(&self, delta: i32) {
        let mut m = self.motion.lock().expect("motion slot");
        m[2] = (i64::from(m[2]) + i64::from(delta)).clamp(-2000, 2000) as i32;
    }
    pub(crate) fn closed(&self) -> bool {
        self.close.load(Ordering::SeqCst) || self.cancel.load(Ordering::SeqCst)
    }
    pub(crate) fn poll_event(&self) -> Option<InputEvent> {
        if self.closed() {
            return Some([0; 4]);
        }
        if let Some(event) = self.resize.lock().expect("resize slot").take() {
            return Some(event);
        }
        // A same-viewport resize is only a legacy wake. SDK6 drains its ordered
        // typed FIFO before recomposition; old SDKs never enable this channel.
        let requested = *self.viewport.lock().expect("viewport slot");
        let rich_wake = {
            let input = self.text_input.lock().expect("input channel");
            if input.enabled() && !input.is_empty() {
                requested
                    .or_else(|| {
                        input
                            .presentation()
                            .or(input.latest_frame())
                            .map(|f| (f.width as i32, f.height as i32))
                    })
                    .map(|(w, h)| [1, w, h, 0])
            } else {
                None
            }
        };
        if let Some(event) = rich_wake {
            return Some(event);
        }
        match self.events.lock().expect("input queue").try_recv() {
            Ok(event) => return Some(if self.closed() { [0; 4] } else { event }),
            Err(TryRecvError::Disconnected) => return Some([0; 4]),
            Err(TryRecvError::Empty) => {}
        }
        let mut motion = self.motion.lock().expect("motion slot");
        if motion[2] != 0 {
            let delta = motion[2];
            motion[2] = 0;
            return Some([2, delta, 0, 0]);
        }
        if motion[0] != 0 || motion[1] != 0 {
            let event = [3, motion[0], motion[1], 0];
            motion[0] = 0;
            motion[1] = 0;
            return Some(event);
        }
        None
    }
    pub fn next_event(&self) -> InputEvent {
        if let Some(event) = self.poll_event() {
            return event;
        }
        let event = match self
            .events
            .lock()
            .expect("input queue")
            .recv_timeout(Duration::from_millis(20))
        {
            Ok(event) => event,
            Err(RecvTimeoutError::Timeout) => [8, 0, 0, 0],
            Err(_) => [0; 4],
        };
        if self.closed() { [0; 4] } else { event }
    }
}

#[derive(Default)]
pub struct KeyLatch {
    pressed: [bool; 4],
}
impl KeyLatch {
    pub fn edge(&mut self, index: usize, down: bool) -> Option<InputEvent> {
        let held = self.pressed.get_mut(index)?;
        let first = down && !*held;
        *held = down;
        if first {
            Some([[7, 0, 0, 0], [7, 0, 0, 0], [6, 0, 0, 0], [0, 0, 0, 0]][index])
        } else {
            None
        }
    }
}
impl ViewBridge {
    pub fn close_requested(&self) -> bool {
        self.close.load(Ordering::SeqCst)
    }
}

impl KeyLatch {
    pub fn character(&mut self, index: usize) -> Option<InputEvent> {
        if index >= 3 || self.pressed[index] {
            None
        } else {
            Some([[7, 0, 0, 0], [7, 0, 0, 0], [6, 0, 0, 0]][index])
        }
    }
}

#[derive(Default)]
pub struct PointerSample {
    down: bool,
    last: Option<(i32, i32)>,
}
impl PointerSample {
    pub fn sample(&mut self, position: Option<(i32, i32)>, down: bool) -> Option<InputEvent> {
        let mut event = None;
        if let Some((x, y)) = position {
            if down && !self.down {
                event = Some([4, x, y, 1]);
            } else if self.down
                && let Some((px, py)) = self.last
            {
                let dx = x.saturating_sub(px);
                let dy = y.saturating_sub(py);
                if dx != 0 || dy != 0 {
                    event = Some([3, dx, dy, 0]);
                }
            }
            self.last = Some((x, y));
        }
        self.down = down;
        event
    }
}

pub fn poll_completion<T>(
    state: &mut Option<anyhow::Result<T>>,
    receiver: &std::sync::mpsc::Receiver<anyhow::Result<T>>,
) -> bool {
    if state.is_some() {
        return true;
    }
    match receiver.try_recv() {
        Ok(result) => {
            *state = Some(result);
            true
        }
        Err(std::sync::mpsc::TryRecvError::Empty) => false,
        Err(std::sync::mpsc::TryRecvError::Disconnected) => {
            *state = Some(Err(anyhow::anyhow!("VIEWER_WORKER_DISCONNECTED")));
            true
        }
    }
}

pub fn viewport(w: f64, h: f64) -> (usize, usize) {
    let w = if w.is_finite() { w.max(320.0) } else { 320.0 };
    let h = if h.is_finite() { h.max(240.0) } else { 240.0 };
    let scale = 1.0_f64
        .min(4096.0 / w)
        .min(4096.0 / h)
        .min((4194304.0 / (w * h)).sqrt());
    (
        (w * scale).floor().max(320.0) as usize,
        (h * scale).floor().max(240.0) as usize,
    )
}
pub fn project_pointer(
    x: f64,
    y: f64,
    w: u32,
    h: u32,
    viewport: (usize, usize),
) -> Option<(i32, i32)> {
    if w == 0 || h == 0 || !x.is_finite() || !y.is_finite() {
        return None;
    }
    Some((
        (x * viewport.0 as f64 / f64::from(w))
            .round()
            .clamp(-1000000.0, 1000000.0) as i32,
        (y * viewport.1 as f64 / f64::from(h))
            .round()
            .clamp(-1000000.0, 1000000.0) as i32,
    ))
}

impl PointerSample {
    pub fn cancel(&mut self) {
        *self = Self::default();
    }
}
impl KeyLatch {
    pub fn cancel(&mut self) {
        *self = Self::default();
    }
}
#[derive(Default)]
pub struct ViewportGate {
    expected: Option<(usize, usize)>,
    presented: Option<(usize, usize)>,
}
impl ViewportGate {
    pub fn resize(&mut self, size: (usize, usize)) {
        self.expected = Some(size);
        self.presented = None;
    }
    pub fn present(&mut self, size: (usize, usize)) {
        self.presented = Some(size);
    }
    pub fn viewport(&self) -> Option<(usize, usize)> {
        self.expected.filter(|s| Some(*s) == self.presented)
    }
}
impl ViewBridge {
    pub fn clear_input(&self) {
        *self.motion.lock().expect("motion slot") = [0; 3];
        let receiver = self.events.lock().expect("input queue");
        while receiver.try_recv().is_ok() {}
        let _ = self.text_input.lock().expect("input channel").cancel(1);
    }
}

impl ViewBridge {
    pub(crate) fn enable_text_input(&self) {
        let mut input = self.text_input.lock().expect("input channel");
        if input.enabled() {
            return;
        }
        input.enable();
        // Late subscribers retain only an actual accepted source, never the
        // latest published frame that may still be waiting for presentation.
        if let Some((sequence, width, height)) =
            *self.accepted_input_frame.lock().expect("accepted input frame")
        {
            let _ = input.present(sequence, width, height);
        }
    }
    pub(crate) fn text_input_enabled(&self) -> bool {
        self.text_input.lock().expect("input channel").enabled()
    }
    pub(crate) fn text_input_epoch(&self) -> u64 {
        self.text_input.lock().expect("input channel").epoch()
    }
    pub(crate) fn input_packet(&self) -> Option<crate::text_input::Packet> {
        self.text_input.lock().expect("input channel").poll()
    }
    pub(crate) fn text_target(&self) -> Option<crate::text_input::TextTarget> {
        self.text_input.lock().expect("input channel").target()
    }
    pub(crate) fn input_presented(&self, sequence: u64, width: u32, height: u32) {
        let mut input = self.text_input.lock().expect("input channel");
        *self.accepted_input_frame.lock().expect("accepted input frame") =
            Some((sequence, width, height));
        if input.enabled() {
            let _ = input.present(sequence, width, height);
        }
    }
    pub(crate) fn input_unpresented(&self) {
        let mut input = self.text_input.lock().expect("input channel");
        *self.accepted_input_frame.lock().expect("accepted input frame") = None;
        input.invalidate_presentation();
    }
    pub(crate) fn set_text_target(
        &self,
        request: crate::text_input::FocusRequest,
    ) -> Result<(), crate::text_input::InputError> {
        self.text_input
            .lock()
            .expect("input channel")
            .text_focus(request)
    }
    pub(crate) fn text_commit(
        &self,
        text: &str,
        modifiers: u32,
    ) -> Result<(), crate::text_input::InputError> {
        self.text_input
            .lock()
            .expect("input channel")
            .commit(text, modifiers)
    }
    pub(crate) fn text_preedit(
        &self,
        text: &str,
        cursor: Option<(usize, usize)>,
        modifiers: u32,
    ) -> Result<(), crate::text_input::InputError> {
        self.text_input
            .lock()
            .expect("input channel")
            .preedit(text, cursor, modifiers)
    }
    pub(crate) fn text_key(
        &self,
        key: i32,
        down: bool,
        repeat: bool,
        modifiers: u32,
    ) -> Result<(), crate::text_input::InputError> {
        self.text_input
            .lock()
            .expect("input channel")
            .key(key, down, repeat, modifiers)
    }
    pub(crate) fn text_pointer(
        &self,
        position: Option<(i32, i32)>,
        down: bool,
        modifiers: u32,
    ) -> Result<(), crate::text_input::InputError> {
        self.text_input
            .lock()
            .expect("input channel")
            .pointer(position, down, modifiers)
    }
    pub(crate) fn cancel_text_input(&self, reason: i32) {
        let _ = self
            .text_input
            .lock()
            .expect("input channel")
            .cancel(reason);
    }
}

#[cfg(test)]
mod text_subscription_tests {
    use super::*;
    fn frame(sequence: u64, size: usize) -> RenderedFrame {
        RenderedFrame {
            width: size, height: size,
            pixels: crate::graphics::FramePixels::Cpu(Arc::new(vec![0;size*size])),
            sequence, render_us:0, text_prepare_us:None, gpu_timing:None, commands:0,
        }
    }
    #[test]
    fn late_subscription_keeps_actual_accepted_source_instead_of_latest_unpainted_frame() {
        let (bridge, _sender)=ViewBridge::new();
        bridge.publish(frame(7,2));
        bridge.input_presented(7,2,2);
        bridge.publish(frame(8,3));
        assert!(!bridge.text_input_enabled());
        bridge.enable_text_input();
        bridge.text_pointer(Some((1,1)),false,0).unwrap();
        assert_eq!(bridge.input_packet().unwrap().frame_sequence,7);
        assert_eq!(bridge.text_pointer(Some((2,2)),false,0),Err(crate::text_input::InputError::Invalid));
    }
    #[test]
    fn late_subscription_cannot_restore_a_receipt_invalidated_by_resize_or_hide() {
        let (bridge, _sender)=ViewBridge::new();
        bridge.publish(frame(7,2));
        bridge.input_presented(7,2,2);
        bridge.input_unpresented();
        bridge.enable_text_input();
        assert_eq!(bridge.text_pointer(Some((1,1)),false,0),Err(crate::text_input::InputError::NotReady));
    }
}
