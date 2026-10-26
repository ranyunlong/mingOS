//! Per-instance SDK6 input channel. No OS handles, host paths or cross-app identities.
use std::{collections::VecDeque, fmt};
pub const HEADER: usize = 56;
pub const MAX_TEXT: usize = 512;
pub const MAX_QUEUE: usize = 128;
const MAX_SERIAL: u64 = i64::MAX as u64;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InputError {
    Invalid,
    Disabled,
    NotReady,
    Full,
    Limit,
}
impl fmt::Display for InputError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "TEXT_INPUT_{self:?}")
    }
}
impl std::error::Error for InputError {}
type Result<T> = std::result::Result<T, InputError>;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u32)]
pub enum Kind {
    Commit = 1,
    Preedit = 2,
    Key = 3,
    Pointer = 4,
    Cancel = 5,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Packet {
    pub kind: Kind,
    pub sequence: u64,
    pub epoch: u64,
    pub frame_sequence: u64,
    pub a: i32,
    pub b: i32,
    pub c: i32,
    pub modifiers: u32,
    pub text: String,
}
impl Packet {
    fn validate(&self) -> Result<()> {
        if self.sequence == 0
            || self.sequence > MAX_SERIAL
            || self.epoch == 0
            || self.epoch > MAX_SERIAL
            || self.frame_sequence > MAX_SERIAL
            || (self.frame_sequence == 0 && self.kind != Kind::Cancel)
            || self.modifiers & !15 != 0
            || self.text.len() > MAX_TEXT
        {
            return Err(InputError::Invalid);
        }
        let empty = self.text.is_empty();
        let valid = match self.kind {
            Kind::Commit => !empty && self.a == 0 && self.b == 0 && self.c == 0,
            Kind::Preedit => {
                self.c == 0
                    && ((self.a == -1 && self.b == -1)
                        || (self.a >= 0
                            && self.b >= 0
                            && (self.a as usize) <= self.text.len()
                            && (self.b as usize) <= self.text.len()
                            && self.text.is_char_boundary(self.a as usize)
                            && self.text.is_char_boundary(self.b as usize)))
            }
            Kind::Key => {
                empty
                    && (1..=15).contains(&self.a)
                    && (0..=1).contains(&self.b)
                    && (0..=1).contains(&self.c)
            }
            Kind::Pointer => {
                empty
                    && (0..=3).contains(&self.c)
                    && ((self.a == -1 && self.b == -1)
                        || ((0..4096).contains(&self.a) && (0..4096).contains(&self.b)))
            }
            Kind::Cancel => {
                empty
                    && (1..=4).contains(&self.a)
                    && self.b == 0
                    && self.c == 0
                    && self.modifiers == 0
            }
        };
        if !valid {
            return Err(InputError::Invalid);
        }
        Ok(())
    }
    pub fn encode(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let mut out = vec![0u8; HEADER];
        for (offset, value) in [
            (0, 1u32),
            (4, self.kind as u32),
            (32, self.a as u32),
            (36, self.b as u32),
            (40, self.c as u32),
            (44, self.modifiers),
            (48, self.text.len() as u32),
        ] {
            out[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
        }
        for (offset, value) in [
            (8, self.sequence),
            (16, self.epoch),
            (24, self.frame_sequence),
        ] {
            out[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
        }
        out.extend_from_slice(self.text.as_bytes());
        Ok(out)
    }
    pub fn decode(bytes: &[u8]) -> Result<Self> {
        if !(HEADER..=HEADER + MAX_TEXT).contains(&bytes.len()) {
            return Err(InputError::Invalid);
        }
        let word =
            |at: usize| u32::from_le_bytes(bytes[at..at + 4].try_into().expect("checked header"));
        let long =
            |at: usize| u64::from_le_bytes(bytes[at..at + 8].try_into().expect("checked header"));
        if word(0) != 1 || word(52) != 0 || word(48) as usize != bytes.len() - HEADER {
            return Err(InputError::Invalid);
        }
        let kind = match word(4) {
            1 => Kind::Commit,
            2 => Kind::Preedit,
            3 => Kind::Key,
            4 => Kind::Pointer,
            5 => Kind::Cancel,
            _ => return Err(InputError::Invalid),
        };
        let text = std::str::from_utf8(&bytes[HEADER..])
            .map_err(|_| InputError::Invalid)?
            .to_owned();
        let packet = Self {
            kind,
            sequence: long(8),
            epoch: long(16),
            frame_sequence: long(24),
            a: word(32) as i32,
            b: word(36) as i32,
            c: word(40) as i32,
            modifiers: word(44),
            text,
        };
        packet.validate()?;
        Ok(packet)
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FocusRequest {
    pub active: bool,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}
impl FocusRequest {
    pub fn decode(bytes: &[u8]) -> Result<Self> {
        if bytes.len() != 24 {
            return Err(InputError::Invalid);
        }
        let w = |at| i32::from_le_bytes(bytes[at..at + 4].try_into().expect("checked focus"));
        if w(0) != 1 || !(0..=1).contains(&w(4)) {
            return Err(InputError::Invalid);
        }
        let r = Self {
            active: w(4) == 1,
            x: w(8),
            y: w(12),
            width: w(16),
            height: w(20),
        };
        r.validate()?;
        Ok(r)
    }
    fn validate(self) -> Result<()> {
        if !self.active {
            if self.x != 0 || self.y != 0 || self.width != 0 || self.height != 0 {
                return Err(InputError::Invalid);
            }
            return Ok(());
        }
        if !(0..4096).contains(&self.x)
            || !(0..4096).contains(&self.y)
            || !(1..=4096).contains(&self.width)
            || !(1..=4096).contains(&self.height)
            || self.x + self.width > 4096
            || self.y + self.height > 4096
        {
            return Err(InputError::Invalid);
        }
        Ok(())
    }
    fn fits(self, width: u32, height: u32) -> bool {
        self.active
            && self.x >= 0
            && self.y >= 0
            && (self.x + self.width) as u32 <= width
            && (self.y + self.height) as u32 <= height
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FrameInfo {
    pub sequence: u64,
    pub width: u32,
    pub height: u32,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TextTarget {
    pub source_sequence: u64,
    pub frame_width: u32,
    pub frame_height: u32,
    pub caret: FocusRequest,
}
pub struct InputChannel {
    enabled: bool,
    sequence: u64,
    epoch: u64,
    latest: Option<FrameInfo>,
    presentation: Option<FrameInfo>,
    target: Option<TextTarget>,
    queue: VecDeque<Packet>,
    pending_cancel: Option<Packet>,
    button_down: bool,
}
impl fmt::Debug for InputChannel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("InputChannel")
            .field("enabled", &self.enabled)
            .field("epoch", &self.epoch)
            .field("queued", &self.queue.len())
            .field("pending_cancel", &self.pending_cancel.is_some())
            .field("presentation", &self.presentation)
            .finish_non_exhaustive()
    }
}
impl Default for InputChannel {
    fn default() -> Self {
        Self::new()
    }
}
impl InputChannel {
    pub fn new() -> Self {
        Self {
            enabled: false,
            sequence: 0,
            epoch: 1,
            latest: None,
            presentation: None,
            target: None,
            queue: VecDeque::new(),
            pending_cancel: None,
            button_down: false,
        }
    }
    pub fn enable(&mut self) {
        self.enabled = true;
    }
    pub fn enabled(&self) -> bool {
        self.enabled
    }
    pub fn epoch(&self) -> u64 {
        self.epoch
    }
    pub fn len(&self) -> usize {
        self.queue.len()
    }
    pub fn is_empty(&self) -> bool {
        self.queue.is_empty() && self.pending_cancel.is_none()
    }
    pub fn presentation(&self) -> Option<FrameInfo> {
        self.presentation
    }
    pub fn latest_frame(&self) -> Option<FrameInfo> {
        self.latest
    }
    pub fn target(&self) -> Option<TextTarget> {
        self.target
    }
    fn frame(sequence: u64, width: u32, height: u32) -> Result<FrameInfo> {
        if sequence == 0
            || sequence > MAX_SERIAL
            || !(1..=4096).contains(&width)
            || !(1..=4096).contains(&height)
            || u64::from(width) * u64::from(height) > 4_194_304
        {
            return Err(InputError::Invalid);
        }
        Ok(FrameInfo {
            sequence,
            width,
            height,
        })
    }
    pub fn publish(&mut self, sequence: u64, width: u32, height: u32) -> Result<()> {
        let f = Self::frame(sequence, width, height)?;
        if self
            .latest
            .is_some_and(|old| sequence < old.sequence || (sequence == old.sequence && f != old))
        {
            return Err(InputError::Invalid);
        }
        self.latest = Some(f);
        Ok(())
    }
    pub fn present(&mut self, sequence: u64, width: u32, height: u32) -> Result<()> {
        if !self.enabled {
            return Err(InputError::Disabled);
        }
        let f = Self::frame(sequence, width, height)?;
        let latest = self.latest.ok_or(InputError::NotReady)?;
        if sequence > latest.sequence
            || (sequence == latest.sequence && f != latest)
            || self.presentation.is_some_and(|old| {
                sequence < old.sequence || (sequence == old.sequence && f != old)
            })
        {
            return Err(InputError::Invalid);
        }
        self.presentation = Some(f);
        Ok(())
    }
    pub fn invalidate_presentation(&mut self) {
        self.presentation = None;
        self.button_down = false;
    }
    pub fn text_focus(&mut self, request: FocusRequest) -> Result<()> {
        if !self.enabled {
            return Err(InputError::Disabled);
        }
        request.validate()?;
        if !request.active {
            if self.target.is_some() {
                self.cancel(1)?;
            }
            self.target = None;
            return Ok(());
        }
        let frame = self.latest.ok_or(InputError::NotReady)?;
        if !request.fits(frame.width, frame.height) {
            return Err(InputError::Invalid);
        }
        self.target = Some(TextTarget {
            source_sequence: frame.sequence,
            frame_width: frame.width,
            frame_height: frame.height,
            caret: request,
        });
        Ok(())
    }
    fn serial(&self) -> Result<u64> {
        self.sequence
            .checked_add(1)
            .filter(|s| *s <= MAX_SERIAL)
            .ok_or(InputError::Limit)
    }
    fn flush_cancel(&mut self) {
        if self.queue.len() < MAX_QUEUE
            && let Some(cancel) = self.pending_cancel.take()
        {
            self.queue.push_back(cancel);
        }
    }
    fn push(
        &mut self,
        kind: Kind,
        a: i32,
        b: i32,
        c: i32,
        modifiers: u32,
        text: &str,
    ) -> Result<()> {
        if !self.enabled {
            return Err(InputError::Disabled);
        }
        let frame = self.presentation.ok_or(InputError::NotReady)?;
        if text.len() > MAX_TEXT {
            return Err(InputError::Limit);
        }
        let mut event = Packet {
            kind,
            sequence: 1,
            epoch: self.epoch,
            frame_sequence: frame.sequence,
            a,
            b,
            c,
            modifiers,
            text: text.into(),
        };
        event.validate()?;
        self.flush_cancel();
        let coalesce = self.queue.back().is_some_and(|last| {
            last.epoch == event.epoch
                && last.frame_sequence == event.frame_sequence
                && last.kind == event.kind
                && ((event.kind == Kind::Preedit)
                    || (event.kind == Kind::Pointer && (c == 0 || c == 2) && last.c == c))
        });
        if !coalesce && self.queue.len() >= MAX_QUEUE {
            return Err(InputError::Full);
        }
        event.sequence = self.serial()?;
        self.sequence = event.sequence;
        if coalesce {
            *self.queue.back_mut().expect("checked last event") = event;
        } else {
            self.queue.push_back(event);
        }
        Ok(())
    }
    pub fn commit(&mut self, text: &str, modifiers: u32) -> Result<()> {
        self.push(Kind::Commit, 0, 0, 0, modifiers, text)
    }
    pub fn preedit(
        &mut self,
        text: &str,
        cursor: Option<(usize, usize)>,
        modifiers: u32,
    ) -> Result<()> {
        if cursor.is_some_and(|(a, b)| a > MAX_TEXT || b > MAX_TEXT) {
            return Err(InputError::Invalid);
        }
        let (a, b) = cursor.map_or((-1, -1), |(a, b)| (a as i32, b as i32));
        self.push(Kind::Preedit, a, b, 0, modifiers, text)
    }
    pub fn key(&mut self, key: i32, down: bool, repeat: bool, modifiers: u32) -> Result<()> {
        if !(1..=15).contains(&key)||modifiers&!15!=0{return Err(InputError::Invalid);}
        if !self.enabled{return Err(InputError::Disabled);}
        if self.presentation.is_none(){return Err(InputError::NotReady);}
        if key==9&&down&&modifiers&14==0{self.cancel(1)?;}
        self.push(
            Kind::Key,
            key,
            i32::from(down),
            i32::from(repeat),
            modifiers,
            "",
        )
    }
    pub fn pointer(
        &mut self,
        position: Option<(i32, i32)>,
        down: bool,
        modifiers: u32,
    ) -> Result<()> {
        if !self.enabled{return Err(InputError::Disabled);}
        if modifiers&!15!=0{return Err(InputError::Invalid);}
        let frame = self.presentation.ok_or(InputError::NotReady)?;
        let (a, b) = position.unwrap_or((-1, -1));
        if position.is_some()
            && (a < 0 || b < 0 || a as u32 >= frame.width || b as u32 >= frame.height)
        {
            return Err(InputError::Invalid);
        }
        let phase = match (down, self.button_down) {
            (true, false) => 1,
            (true, true) => 2,
            (false, true) => 3,
            (false, false) => 0,
        };
        if phase==1 {self.cancel(1)?;}
        self.push(Kind::Pointer, a, b, phase, modifiers, "")?;
        self.button_down = down;
        Ok(())
    }
    pub fn cancel(&mut self, reason: i32) -> Result<()> {
        if !self.enabled {
            return Ok(());
        }
        if !(1..=4).contains(&reason) {
            return Err(InputError::Invalid);
        }
        let epoch = self
            .epoch
            .checked_add(1)
            .filter(|e| *e <= MAX_SERIAL)
            .ok_or(InputError::Limit)?;
        let sequence = self.serial()?;
        let event = Packet {
            kind: Kind::Cancel,
            sequence,
            epoch,
            frame_sequence: self.presentation.map_or(0, |f| f.sequence),
            a: reason,
            b: 0,
            c: 0,
            modifiers: 0,
            text: String::new(),
        };
        self.epoch = epoch;
        self.sequence = sequence;
        self.button_down = false;
        if self
            .queue
            .back()
            .is_some_and(|last| last.kind == Kind::Cancel)
        {
            *self.queue.back_mut().expect("checked cancel") = event;
        } else if self.queue.len() < MAX_QUEUE {
            self.queue.push_back(event);
        } else {
            self.pending_cancel = Some(event);
        }
        Ok(())
    }
    pub fn poll(&mut self) -> Option<Packet> {
        if self.queue.is_empty() {
            self.flush_cancel();
        }
        let event = self.queue.pop_front();
        self.flush_cancel();
        event
    }
}
