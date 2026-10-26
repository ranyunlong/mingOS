use crate::{
    frame_clock::{ClockError, ClockState, EventKind, FrameEvent, WaitRequest},
    ui::{InputEvent, ViewBridge},
};
use serde::Serialize;
use std::{
    collections::VecDeque,
    sync::atomic::Ordering,
    thread,
    time::{Duration, Instant},
};
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FrameClockStats {
    pub source: &'static str,
    pub reads: u64,
    pub waits: u64,
    pub frames: u64,
    pub timers: u64,
    pub inputs: u64,
    pub closes: u64,
    pub cancels: u64,
    pub pending: u64,
}
impl Default for FrameClockStats {
    fn default() -> Self {
        Self {
            source: "host-monotonic-opportunity",
            reads: 0,
            waits: 0,
            frames: 0,
            timers: 0,
            inputs: 0,
            closes: 0,
            cancels: 0,
            pending: 0,
        }
    }
}
pub(crate) struct FrameClockService {
    origin: Instant,
    budget: Option<Duration>,
    state: ClockState,
    input_burst: u8,
    pub stats: FrameClockStats,
}
impl FrameClockService {
    pub fn new(origin: Instant, budget: Duration) -> Self {
        Self {
            origin,
            budget: Some(budget),
            state: ClockState::default(),
            input_burst: 0,
            stats: FrameClockStats::default(),
        }
    }
    pub fn persistent(origin: Instant) -> Self {
        let mut service = Self::new(origin, Duration::ZERO);
        service.budget = None;
        service
    }
    pub fn read(&mut self) -> Result<[u8; 8], ClockError> {
        self.ensure_budget()?;
        bump(&mut self.stats.reads)?;
        Ok(self
            .state
            .now(self.origin.elapsed().as_nanos())?
            .to_le_bytes())
    }
    pub fn wait(
        &mut self,
        request: WaitRequest,
        live: Option<&ViewBridge>,
        scripted: &mut VecDeque<InputEvent>,
        counts: &mut [u64; 9],
    ) -> Result<Option<FrameEvent>, ClockError> {
        self.ensure_budget()?;
        bump(&mut self.stats.waits)?;
        let mut had_idle = false;
        if let Some(event) = self.poll(request, live, scripted, counts, true, &mut had_idle)? {
            return Ok(Some(event));
        }
        let wait = self.remaining_wait_ns(request)?;
        if wait > 0 {
            thread::sleep(Duration::from_nanos(wait));
            if let Some(event) = self.poll(request, live, scripted, counts, false, &mut had_idle)? {
                return Ok(Some(event));
            }
        }
        bump(&mut self.stats.pending)?;
        Ok(None)
    }
    fn ensure_budget(&self) -> Result<(), ClockError> {
        if self.budget.is_some_and(|b| self.origin.elapsed() >= b) {
            Err(ClockError::Limit)
        } else {
            Ok(())
        }
    }
    fn remaining_wait_ns(&mut self, request: WaitRequest) -> Result<u64, ClockError> {
        let elapsed = self.origin.elapsed();
        let remaining = match self.budget {
            Some(budget) => budget.checked_sub(elapsed).filter(|d| !d.is_zero()).ok_or(ClockError::Limit)?,
            None => Duration::from_nanos(crate::frame_clock::MAX_WAIT_NS),
        };
        let wait = self.state.wait_ns(request, elapsed.as_nanos())?;
        Ok(wait.min(
            remaining
                .as_nanos()
                .min(u128::from(crate::frame_clock::MAX_WAIT_NS)) as u64,
        ))
    }
    fn deliver(
        &mut self,
        event: FrameEvent,
        counts: &mut [u64; 9],
    ) -> Result<Option<FrameEvent>, ClockError> {
        debug_assert_eq!(event.frame_sequence, self.state.frame_sequence());
        match event.kind {
            EventKind::Frame => bump(&mut self.stats.frames)?,
            EventKind::Timer => bump(&mut self.stats.timers)?,
            EventKind::Input => {
                bump(&mut self.stats.inputs)?;
                bump(&mut counts[event.input[0] as usize])?;
            }
            EventKind::Close => {
                bump(&mut self.stats.closes)?;
                bump(&mut counts[0])?;
            }
            EventKind::Cancel => {
                bump(&mut self.stats.cancels)?;
                bump(&mut counts[0])?;
            }
        }
        Ok(Some(event))
    }
    fn poll(
        &mut self,
        request: WaitRequest,
        live: Option<&ViewBridge>,
        scripted: &mut VecDeque<InputEvent>,
        counts: &mut [u64; 9],
        allow_script: bool,
        had_idle: &mut bool,
    ) -> Result<Option<FrameEvent>, ClockError> {
        self.ensure_budget()?;
        let raw = self.origin.elapsed().as_nanos();
        if let Some(bridge) = live {
            if bridge.cancel.load(Ordering::SeqCst) {
                let e = self.state.input(raw, [0; 4], true)?;
                return self.deliver(e, counts);
            }
            if bridge.closed() {
                let e = self.state.input(raw, [0; 4], false)?;
                return self.deliver(e, counts);
            }
        } else if allow_script && scripted.front().is_some_and(|e| e[0] == 0) {
            scripted.pop_front();
            let e = self.state.input(raw, [0; 4], false)?;
            return self.deliver(e, counts);
        }
        // Yield before pulling the ninth ordinary input. Both SDK event APIs
        // must observe the same queue; a private deferred slot would reorder them.
        let time_due = self.state.wait_ns(
            WaitRequest {
                nonblocking: false,
                ..request
            },
            raw,
        )? == 0;
        if self.input_burst >= 8
            && time_due
            && let Some(event) = self.state.poll_time(request, raw)?
        {
            self.input_burst = 0;
            return self.deliver(event, counts);
        }
        let incoming = live.and_then(ViewBridge::poll_event).or_else(|| {
            if live.is_none() && allow_script {
                scripted.pop_front()
            } else {
                None
            }
        });
        if let Some(input) = incoming {
            if input[0] == 0 {
                let event = self.state.input(raw, [0; 4], false)?;
                return self.deliver(event, counts);
            }
            if input[0] == 8 {
                *had_idle = true;
            } else {
                self.input_burst = self.input_burst.saturating_add(1).min(8);
                let event = self.state.input(raw, input, false)?;
                return self.deliver(event, counts);
            }
        }
        if let Some(event) = self.state.poll_time(request, raw)? {
            self.input_burst = 0;
            return self.deliver(event, counts);
        }
        if live.is_none()
            && allow_script
            && scripted.is_empty()
            && !*had_idle
            && !request.frame
            && request.deadline.is_none()
            && !request.nonblocking
        {
            let event = self.state.input(raw, [0; 4], false)?;
            return self.deliver(event, counts);
        }
        Ok(None)
    }
}
fn bump(value: &mut u64) -> Result<(), ClockError> {
    *value = value.checked_add(1).ok_or(ClockError::Limit)?;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    fn req(frame: bool, nonblocking: bool, deadline: Option<u64>) -> WaitRequest {
        WaitRequest {
            frame,
            nonblocking,
            deadline,
        }
    }
    #[test]
    fn read_and_nonblocking_are_scoped_and_do_not_invent_input() {
        let mut s = FrameClockService::new(Instant::now(), Duration::from_secs(60));
        let a = i64::from_le_bytes(s.read().unwrap());
        let b = i64::from_le_bytes(s.read().unwrap());
        assert!(a >= 0 && b >= a);
        let mut q = VecDeque::new();
        let mut counts = [0; 9];
        assert_eq!(
            s.wait(req(false, true, None), None, &mut q, &mut counts)
                .unwrap(),
            None
        );
        assert_eq!(counts, [0; 9]);
        assert_eq!(s.stats.reads, 2);
        assert_eq!(s.stats.waits, 1);
        assert_eq!(s.stats.pending, 1);
    }
    #[test]
    fn real_timer_frame_and_headless_idle_eof_are_distinct() {
        let mut s = FrameClockService::new(Instant::now(), Duration::from_secs(60));
        let mut q = VecDeque::new();
        let mut counts = [0; 9];
        assert_eq!(
            s.wait(req(false, false, Some(0)), None, &mut q, &mut counts)
                .unwrap()
                .unwrap()
                .kind,
            EventKind::Timer
        );
        assert_eq!(
            s.wait(req(true, false, None), None, &mut q, &mut counts)
                .unwrap()
                .unwrap()
                .kind,
            EventKind::Frame
        );
        let e = s
            .wait(req(false, false, None), None, &mut q, &mut counts)
            .unwrap()
            .unwrap();
        assert_eq!(e.kind, EventKind::Close);
        assert_eq!(e.input, [0; 4]);
        assert_eq!(counts[0], 1);
    }
    #[test]
    fn input_burst_is_bounded_without_consuming_queued_input() {
        let mut s = FrameClockService::new(Instant::now(), Duration::from_secs(60));
        let mut q: VecDeque<_> = (0..9).map(|i| [4, i, 20, 0]).collect();
        let mut counts = [0; 9];
        for i in 0..8 {
            let e = s
                .wait(req(true, false, None), None, &mut q, &mut counts)
                .unwrap()
                .unwrap();
            assert_eq!(e.kind, EventKind::Input);
            assert_eq!(e.input[1], i);
        }
        let f = s
            .wait(req(true, false, None), None, &mut q, &mut counts)
            .unwrap()
            .unwrap();
        assert_eq!(f.kind, EventKind::Frame);
        assert_eq!(f.frame_sequence, 1);
        let e = s
            .wait(req(true, false, None), None, &mut q, &mut counts)
            .unwrap()
            .unwrap();
        assert_eq!(e.kind, EventKind::Input);
        assert_eq!(e.input[1], 8);
        assert_eq!(counts[4], 9);
        assert_eq!(s.stats.frames, 1);
    }
    #[test]
    fn close_and_cancel_preempt_due_frame_and_queued_input() {
        let mut s = FrameClockService::new(Instant::now(), Duration::from_secs(60));
        let mut q = VecDeque::from([[0; 4], [4, 8, 9, 0]]);
        let mut counts = [0; 9];
        assert_eq!(
            s.wait(req(true, false, None), None, &mut q, &mut counts)
                .unwrap()
                .unwrap()
                .kind,
            EventKind::Close
        );
        let (bridge, _sender) = ViewBridge::new();
        bridge.cancel.store(true, Ordering::SeqCst);
        let e = s
            .wait(req(true, false, None), Some(&bridge), &mut q, &mut counts)
            .unwrap()
            .unwrap();
        assert_eq!(e.kind, EventKind::Cancel);
        assert_eq!(s.stats.closes, 1);
        assert_eq!(s.stats.cancels, 1);
        assert_eq!(counts[0], 2);
    }
    #[test]
    fn idle_test_token_yields_without_becoming_input_or_fake_time() {
        let mut s = FrameClockService::new(Instant::now(), Duration::from_secs(60));
        let mut q = VecDeque::from([[8, 0, 0, 0], [0; 4]]);
        let mut counts = [0; 9];
        assert_eq!(
            s.wait(req(false, true, None), None, &mut q, &mut counts)
                .unwrap(),
            None
        );
        assert_eq!(counts[8], 0);
        assert_eq!(q.front(), Some(&[0; 4]));
        assert_eq!(
            s.wait(req(false, false, None), None, &mut q, &mut counts)
                .unwrap()
                .unwrap()
                .kind,
            EventKind::Close
        );
    }
}

#[cfg(test)]
mod deadline_tests {
    use super::*;
    fn waiting() -> WaitRequest {
        WaitRequest {
            frame: false,
            nonblocking: false,
            deadline: Some(i64::MAX as u64),
        }
    }
    #[test]
    fn configured_remaining_budget_caps_every_sleep() {
        let budget = Duration::from_millis(1);
        let mut clock = FrameClockService::new(Instant::now(), budget);
        assert!(clock.remaining_wait_ns(waiting()).unwrap() <= budget.as_nanos() as u64);
    }
    #[test]
    fn expired_budget_does_not_deliver_clock_or_input() {
        let mut clock = FrameClockService::new(Instant::now(), Duration::ZERO);
        assert_eq!(clock.read(), Err(ClockError::Limit));
        let mut input = VecDeque::from([[4, 40, 120, 0]]);
        let mut counts = [0; 9];
        assert_eq!(
            clock.wait(waiting(), None, &mut input, &mut counts),
            Err(ClockError::Limit)
        );
        assert_eq!(input.len(), 1);
        assert_eq!(counts, [0; 9]);
    }
    #[test]
    fn waking_after_budget_expiry_cannot_publish_an_event() {
        let mut clock = FrameClockService::new(Instant::now(), Duration::from_millis(1));
        assert_eq!(
            clock.wait(waiting(), None, &mut VecDeque::new(), &mut [0; 9]),
            Err(ClockError::Limit)
        );
        assert_eq!(clock.stats.frames, 0);
        assert_eq!(clock.stats.timers, 0);
    }
}

#[cfg(test)]
mod mixed_live_tests {
    use super::*;
    #[test]
    fn inherited_live_event_reader_observes_unconsumed_fifo_head() {
        let mut service = FrameClockService::new(Instant::now(), Duration::from_secs(60));
        let (bridge, sender) = ViewBridge::new();
        for i in 0..10 {
            sender.send([4, i, 20, 0]).unwrap();
        }
        let r = WaitRequest {
            frame: true,
            nonblocking: true,
            deadline: None,
        };
        let mut scripted = VecDeque::new();
        let mut counts = [0; 9];
        for i in 0..8 {
            assert_eq!(
                service
                    .wait(r, Some(&bridge), &mut scripted, &mut counts)
                    .unwrap()
                    .unwrap()
                    .input[1],
                i
            );
        }
        assert_eq!(
            service
                .wait(r, Some(&bridge), &mut scripted, &mut counts)
                .unwrap()
                .unwrap()
                .kind,
            EventKind::Frame
        );
        assert_eq!(bridge.next_event()[1], 8);
        assert_eq!(
            service
                .wait(r, Some(&bridge), &mut scripted, &mut counts)
                .unwrap()
                .unwrap()
                .input[1],
            9
        );
    }
}
