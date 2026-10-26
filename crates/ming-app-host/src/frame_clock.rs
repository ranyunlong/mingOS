//! SDK5 app-owned frame/time decisions and fixed little-endian ABI packets.
use std::{error::Error, fmt};
pub(crate) const REQUEST_BYTES: usize = 16;
pub(crate) const EVENT_BYTES: usize = 48;
pub(crate) const FRAME_PERIOD_NS: u64 = 16_666_667;
pub(crate) const MAX_WAIT_NS: u64 = FRAME_PERIOD_NS;
pub(crate) const MAX_TIME: u64 = i64::MAX as u64;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ClockError {
    Invalid,
    Limit,
}
impl fmt::Display for ClockError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Invalid => "FRAME_CLOCK_INVALID",
            Self::Limit => "FRAME_CLOCK_LIMIT",
        })
    }
}
impl ClockError {
    pub fn code(self) -> i32 {
        match self {
            Self::Invalid => -2,
            Self::Limit => -4,
        }
    }
}
impl Error for ClockError {}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct WaitRequest {
    pub frame: bool,
    pub nonblocking: bool,
    pub deadline: Option<u64>,
}
impl WaitRequest {
    pub fn decode(bytes: &[u8]) -> Result<Self, ClockError> {
        if bytes.len() != REQUEST_BYTES {
            return Err(ClockError::Invalid);
        }
        let version = u32::from_le_bytes(bytes[..4].try_into().expect("request version"));
        let flags = u32::from_le_bytes(bytes[4..8].try_into().expect("request flags"));
        let deadline = i64::from_le_bytes(bytes[8..16].try_into().expect("request deadline"));
        if version != 1 || flags & !3 != 0 || deadline < -1 {
            return Err(ClockError::Invalid);
        }
        Ok(Self {
            frame: flags & 1 != 0,
            nonblocking: flags & 2 != 0,
            deadline: if deadline < 0 {
                None
            } else {
                Some(deadline as u64)
            },
        })
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u32)]
pub(crate) enum EventKind {
    Frame = 1,
    Timer = 2,
    Input = 3,
    Close = 4,
    Cancel = 5,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct FrameEvent {
    pub kind: EventKind,
    pub now_ns: u64,
    pub frame_sequence: u64,
    pub input: [i32; 4],
}
impl FrameEvent {
    pub fn encode(self) -> [u8; EVENT_BYTES] {
        let mut bytes = [0; EVENT_BYTES];
        bytes[..4].copy_from_slice(&1u32.to_le_bytes());
        bytes[4..8].copy_from_slice(&(self.kind as u32).to_le_bytes());
        bytes[8..16].copy_from_slice(&self.now_ns.to_le_bytes());
        bytes[16..24].copy_from_slice(&self.frame_sequence.to_le_bytes());
        for (i, value) in self.input.into_iter().enumerate() {
            bytes[24 + i * 4..28 + i * 4].copy_from_slice(&value.to_le_bytes());
        }
        bytes
    }
}
#[derive(Default, Clone, Debug, PartialEq, Eq)]
pub(crate) struct ClockState {
    last_now: u64,
    last_frame_ns: Option<u64>,
    frame_sequence: u64,
}
impl ClockState {
    pub fn now(&mut self, raw: u128) -> Result<u64, ClockError> {
        if raw > u128::from(MAX_TIME) {
            return Err(ClockError::Limit);
        }
        self.last_now = self.last_now.max(raw as u64);
        Ok(self.last_now)
    }
    pub fn poll_time(
        &mut self,
        request: WaitRequest,
        raw: u128,
    ) -> Result<Option<FrameEvent>, ClockError> {
        let now = self.now(raw)?;
        let kind = if request.frame
            && self
                .last_frame_ns
                .is_none_or(|last| now.saturating_sub(last) >= FRAME_PERIOD_NS)
        {
            let next = self
                .frame_sequence
                .checked_add(1)
                .filter(|&n| n <= MAX_TIME)
                .ok_or(ClockError::Limit)?;
            self.frame_sequence = next;
            self.last_frame_ns = Some(now);
            EventKind::Frame
        } else if request.deadline.is_some_and(|d| d <= now) {
            EventKind::Timer
        } else {
            return Ok(None);
        };
        Ok(Some(FrameEvent {
            kind,
            now_ns: now,
            frame_sequence: self.frame_sequence,
            input: [0; 4],
        }))
    }
    pub fn wait_ns(&mut self, request: WaitRequest, raw: u128) -> Result<u64, ClockError> {
        let now = self.now(raw)?;
        if request.nonblocking {
            return Ok(0);
        }
        let mut wait = MAX_WAIT_NS;
        if request.frame {
            wait = wait.min(self.last_frame_ns.map_or(0, |last| {
                FRAME_PERIOD_NS.saturating_sub(now.saturating_sub(last))
            }));
        }
        if let Some(deadline) = request.deadline {
            wait = wait.min(deadline.saturating_sub(now));
        }
        Ok(wait)
    }
    pub fn input(
        &mut self,
        raw: u128,
        input: [i32; 4],
        cancel: bool,
    ) -> Result<FrameEvent, ClockError> {
        if !cancel && !(0..=7).contains(&input[0]) {
            return Err(ClockError::Invalid);
        }
        let kind = if cancel {
            EventKind::Cancel
        } else if input[0] == 0 {
            EventKind::Close
        } else {
            EventKind::Input
        };
        Ok(FrameEvent {
            kind,
            now_ns: self.now(raw)?,
            frame_sequence: self.frame_sequence,
            input: if matches!(kind, EventKind::Close | EventKind::Cancel) {
                [0; 4]
            } else {
                input
            },
        })
    }
    pub fn frame_sequence(&self) -> u64 {
        self.frame_sequence
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn req(frame: bool, deadline: Option<u64>) -> WaitRequest {
        WaitRequest {
            frame,
            deadline,
            nonblocking: false,
        }
    }
    fn packet(flags: u32, deadline: i64) -> [u8; 16] {
        let mut p = [0; 16];
        p[..4].copy_from_slice(&1u32.to_le_bytes());
        p[4..8].copy_from_slice(&flags.to_le_bytes());
        p[8..].copy_from_slice(&deadline.to_le_bytes());
        p
    }
    #[test]
    fn request_roundtrip_flags_and_long_deadlines() {
        for flags in 0..=3 {
            for d in [-1, 0, 1, i64::MAX] {
                let r = WaitRequest::decode(&packet(flags, d)).unwrap();
                assert_eq!(r.frame, flags & 1 != 0);
                assert_eq!(r.nonblocking, flags & 2 != 0);
                assert_eq!(r.deadline, (d >= 0).then_some(d as u64));
            }
        }
    }
    #[test]
    fn invalid_request_shapes_versions_flags_and_negative_deadlines() {
        for n in [0, 1, 8, 15, 17, 32] {
            assert_eq!(WaitRequest::decode(&vec![0; n]), Err(ClockError::Invalid));
        }
        for version in [0u32, 2, u32::MAX] {
            let mut p = packet(0, -1);
            p[..4].copy_from_slice(&version.to_le_bytes());
            assert_eq!(WaitRequest::decode(&p), Err(ClockError::Invalid));
        }
        for flags in [4, 8, u32::MAX] {
            assert_eq!(
                WaitRequest::decode(&packet(flags, -1)),
                Err(ClockError::Invalid)
            );
        }
        for d in [-2, i64::MIN] {
            assert_eq!(WaitRequest::decode(&packet(0, d)), Err(ClockError::Invalid));
        }
    }
    #[test]
    fn event_encoding_is_exact_little_endian_and_reserved_zero() {
        for kind in [
            EventKind::Frame,
            EventKind::Timer,
            EventKind::Input,
            EventKind::Close,
            EventKind::Cancel,
        ] {
            let e = FrameEvent {
                kind,
                now_ns: 0x0102030405060708,
                frame_sequence: 0x1112131415161718,
                input: [1, -2, i32::MAX, i32::MIN],
            }
            .encode();
            assert_eq!(u32::from_le_bytes(e[0..4].try_into().unwrap()), 1);
            assert_eq!(u32::from_le_bytes(e[4..8].try_into().unwrap()), kind as u32);
            assert_eq!(&e[8..16], &0x0102030405060708u64.to_le_bytes());
            assert_eq!(&e[16..24], &0x1112131415161718u64.to_le_bytes());
            for (i, v) in [1i32, -2, i32::MAX, i32::MIN].into_iter().enumerate() {
                assert_eq!(&e[24 + i * 4..28 + i * 4], &v.to_le_bytes());
            }
            assert_eq!(&e[40..48], &[0; 8]);
        }
    }
    #[test]
    fn monotonic_read_clamps_backwards_and_rejects_domain_overflow_atomically() {
        let mut s = ClockState::default();
        assert_eq!(s.now(123), Ok(123));
        assert_eq!(s.now(45), Ok(123));
        assert_eq!(s.now(MAX_TIME as u128), Ok(MAX_TIME));
        let old = s.clone();
        assert_eq!(s.now(MAX_TIME as u128 + 1), Err(ClockError::Limit));
        assert_eq!(s, old);
    }
    #[test]
    fn first_frame_is_immediate_then_rate_limited_without_catchup() {
        let mut s = ClockState::default();
        let r = req(true, None);
        let first = s.poll_time(r, 100).unwrap().unwrap();
        assert_eq!(first.kind, EventKind::Frame);
        assert_eq!(first.frame_sequence, 1);
        assert_eq!(first.input, [0; 4]);
        assert!(
            s.poll_time(r, (100 + FRAME_PERIOD_NS - 1) as u128)
                .unwrap()
                .is_none()
        );
        let second = s
            .poll_time(r, (100 + FRAME_PERIOD_NS) as u128)
            .unwrap()
            .unwrap();
        assert_eq!(second.frame_sequence, 2);
        let late = s.poll_time(r, 10_000_000_000).unwrap().unwrap();
        assert_eq!(late.frame_sequence, 3);
        assert_eq!(late.now_ns, 10_000_000_000);
        assert!(s.poll_time(r, 10_000_000_000).unwrap().is_none());
        assert_eq!(s.frame_sequence(), 3);
    }
    #[test]
    fn timers_never_masquerade_as_frames() {
        let mut s = ClockState::default();
        let r = req(false, Some(10));
        assert!(s.poll_time(r, 9).unwrap().is_none());
        let e = s.poll_time(r, 10).unwrap().unwrap();
        assert_eq!(e.kind, EventKind::Timer);
        assert_eq!(e.frame_sequence, 0);
        assert_eq!(e.input, [0; 4]);
        assert_eq!(s.last_frame_ns, None);
        assert_eq!(s.poll_time(req(false, None), 1_000_000).unwrap(), None);
    }
    #[test]
    fn due_timer_precedes_not_yet_due_frame_but_real_due_frame_services_both() {
        let mut s = ClockState::default();
        s.poll_time(req(true, None), 0).unwrap();
        let e = s.poll_time(req(true, Some(10)), 10).unwrap().unwrap();
        assert_eq!(e.kind, EventKind::Timer);
        assert_eq!(s.frame_sequence(), 1);
        let e = s
            .poll_time(req(true, Some(10)), FRAME_PERIOD_NS as u128)
            .unwrap()
            .unwrap();
        assert_eq!(e.kind, EventKind::Frame);
        assert_eq!(e.frame_sequence, 2);
    }
    #[test]
    fn waits_are_bounded_and_nonblocking_never_waits() {
        let mut s = ClockState::default();
        assert_eq!(s.wait_ns(req(false, None), 0).unwrap(), MAX_WAIT_NS);
        assert_eq!(s.wait_ns(req(true, None), 0).unwrap(), 0);
        s.poll_time(req(true, None), 0).unwrap();
        assert_eq!(s.wait_ns(req(true, None), 1).unwrap(), FRAME_PERIOD_NS - 1);
        assert_eq!(s.wait_ns(req(true, Some(12)), 2).unwrap(), 10);
        assert_eq!(s.wait_ns(req(false, Some(0)), 2).unwrap(), 0);
        let mut r = req(false, Some(MAX_TIME));
        r.nonblocking = true;
        assert_eq!(s.wait_ns(r, 3).unwrap(), 0);
    }
    #[test]
    fn close_cancel_and_input_do_not_advance_frame_sequence() {
        let mut s = ClockState::default();
        s.poll_time(req(true, None), 0).unwrap();
        for event in [[1, 1100, 760, 0], [4, 40, 120, 0], [0; 4]] {
            let e = s.input(1, event, false).unwrap();
            assert_eq!(
                e.kind,
                if event[0] == 0 {
                    EventKind::Close
                } else {
                    EventKind::Input
                }
            );
            assert_eq!(e.input, event);
            assert_eq!(e.frame_sequence, 1);
        }
        let e = s.input(2, [0; 4], true).unwrap();
        assert_eq!(e.kind, EventKind::Cancel);
        assert_eq!(s.frame_sequence(), 1);
        for code in [-1, 8, 9, i32::MAX] {
            assert_eq!(s.input(3, [code, 0, 0, 0], false), Err(ClockError::Invalid));
        }
    }
    #[test]
    fn frame_sequence_domain_limit_does_not_publish_or_wrap() {
        let mut s = ClockState {
            last_now: 0,
            last_frame_ns: None,
            frame_sequence: MAX_TIME,
        };
        let old = s.clone();
        assert_eq!(s.poll_time(req(true, None), 0), Err(ClockError::Limit));
        assert_eq!(s, old);
    }
    #[test]
    fn near_maximum_time_waits_do_not_overflow() {
        let mut s = ClockState::default();
        s.poll_time(req(true, None), MAX_TIME as u128).unwrap();
        assert_eq!(
            s.wait_ns(req(true, None), MAX_TIME as u128).unwrap(),
            FRAME_PERIOD_NS
        );
        assert_eq!(
            s.poll_time(req(false, Some(MAX_TIME)), MAX_TIME as u128)
                .unwrap()
                .unwrap()
                .kind,
            EventKind::Timer
        );
    }
    #[test]
    fn errors_implement_standard_error_and_exact_labels() {
        fn check(_: &dyn Error) {}
        assert_eq!(ClockError::Invalid.code(), -2);
        assert_eq!(ClockError::Limit.code(), -4);
        check(&ClockError::Invalid);
        check(&ClockError::Limit);
        assert_eq!(ClockError::Invalid.to_string(), "FRAME_CLOCK_INVALID");
        assert_eq!(ClockError::Limit.to_string(), "FRAME_CLOCK_LIMIT");
    }
}
