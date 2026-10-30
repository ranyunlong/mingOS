//! Bounded worker-side conversion for the versioned private-file binary interface.
use crate::{app_data::{AppDataError, MAX_PRIVATE_FILE_BYTES, validate_private_path}, protocol::Reply};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde::Deserialize;
use serde_json::{Value, json};

pub const MAX_INPUT: usize = 70_000;
pub const MAX_OUTPUT: usize = 65_536;
const MAX_PATH: usize = 1024;

#[derive(Clone, Copy)]
enum Kind { Read(u32), Write(usize), Begin, Chunk(usize), Commit, Abort, List(u32), Sync }
pub struct WireRequest { kind: Kind, json: String }
pub struct WireReply { pub value: i32, pub data: Vec<u8> }

struct Reader<'a> { bytes: &'a [u8], position: usize }
impl<'a> Reader<'a> {
    fn take(&mut self, length: usize) -> Result<&'a [u8], AppDataError> {
        let end = self.position.checked_add(length).ok_or(AppDataError::InvalidArgument)?;
        let bytes = self.bytes.get(self.position..end).ok_or(AppDataError::InvalidArgument)?;
        self.position = end;
        Ok(bytes)
    }
    fn number(&mut self) -> Result<u32, AppDataError> {
        Ok(u32::from_le_bytes(self.take(4)?.try_into().map_err(|_| AppDataError::InvalidArgument)?))
    }
    fn field(&mut self, maximum: usize) -> Result<&'a [u8], AppDataError> {
        let length = self.number()? as usize;
        if length > maximum { return Err(AppDataError::InvalidArgument); }
        self.take(length)
    }
    fn path(&mut self, empty: bool) -> Result<&'a str, AppDataError> {
        let path = std::str::from_utf8(self.field(MAX_PATH)?).map_err(|_| AppDataError::InvalidArgument)?;
        if !empty || !path.is_empty() { validate_private_path(path)?; }
        Ok(path)
    }
}

impl WireRequest {
    pub fn parse(bytes: &[u8]) -> Result<Self, AppDataError> {
        if bytes.len() > MAX_INPUT { return Err(AppDataError::InvalidArgument); }
        let mut reader = Reader { bytes, position: 0 };
        let op = *reader.take(1)?.first().ok_or(AppDataError::InvalidArgument)?;
        let (kind, value): (Kind, Value) = match op {
            1 => {
                let path = reader.path(false)?; let offset = reader.number()?; let limit = reader.number()?;
                if limit as usize > MAX_OUTPUT { return Err(AppDataError::InvalidArgument); }
                (Kind::Read(limit), json!({"op":"read","path":path,"offset":offset,"limit":limit}))
            }
            2 => {
                let path = reader.path(false)?; let bytes = reader.field(MAX_OUTPUT)?;
                (Kind::Write(bytes.len()), json!({"op":"write","path":path,"data":STANDARD.encode(bytes)}))
            }
            3 => {
                let path = reader.path(false)?; let total = reader.number()?;
                if total as usize > MAX_PRIVATE_FILE_BYTES { return Err(AppDataError::InvalidArgument); }
                (Kind::Begin, json!({"op":"write_begin","path":path,"total":total}))
            }
            4 => {
                let offset = reader.number()?; let bytes = reader.field(MAX_OUTPUT)?;
                if bytes.is_empty() { return Err(AppDataError::InvalidArgument); }
                (Kind::Chunk(bytes.len()), json!({"op":"write_chunk","offset":offset,"data":STANDARD.encode(bytes)}))
            }
            5 => (Kind::Commit, json!({"op":"write_commit"})),
            6 => (Kind::Abort, json!({"op":"write_abort"})),
            7 => {
                let path = reader.path(true)?; let offset = reader.number()?; let limit = reader.number()?;
                if limit > 32 { return Err(AppDataError::InvalidArgument); }
                (Kind::List(limit), json!({"op":"list","path":path,"offset":offset,"limit":limit}))
            }
            8..=10 => {
                let path = reader.path(false)?;
                let op = match op { 8 => "mkdir", 9 => "remove_dir", _ => "delete" };
                (Kind::Sync, json!({"op":op,"path":path}))
            }
            11 => {
                let from = reader.path(false)?; let to = reader.path(false)?;
                (Kind::Sync, json!({"op":"rename","from":from,"to":to}))
            }
            _ => return Err(AppDataError::InvalidArgument),
        };
        if reader.position != bytes.len() { return Err(AppDataError::InvalidArgument); }
        Ok(Self { kind, json: value.to_string() })
    }
    pub fn json(&self) -> &str { &self.json }

    pub fn convert_reply(&self, reply: &Reply) -> Result<WireReply, AppDataError> {
        if reply.value < 0 {
            return if (-4..=-1).contains(&reply.value) && reply.payload.is_none() {
                Ok(WireReply { value: reply.value, data: Vec::new() })
            } else { Err(AppDataError::Io) };
        }
        let payload = reply.payload.as_deref();
        let data = match self.kind {
            Kind::Read(limit) => {
                if reply.value as u32 > limit { return Err(AppDataError::Io); }
                if reply.value == 0 && payload.is_some() { return Err(AppDataError::Io); }
                let bytes = decode_payload(payload, reply.value == 0)?;
                if bytes.len() != reply.value as usize { return Err(AppDataError::Io); }
                bytes
            }
            Kind::Write(expected) | Kind::Chunk(expected) if reply.value as usize != expected => return Err(AppDataError::Io),
            Kind::Write(_) | Kind::Commit => {
                if matches!(self.kind, Kind::Commit) && reply.value != 0 { return Err(AppDataError::Io); }
                vec![match payload { Some("0") => 0, Some("1") => 1, _ => return Err(AppDataError::Io) }]
            }
            Kind::Begin | Kind::Abort if reply.value != 0 || payload.is_some() => return Err(AppDataError::Io),
            Kind::Chunk(_) if payload.is_some() => return Err(AppDataError::Io),
            Kind::Begin | Kind::Chunk(_) | Kind::Abort => Vec::new(),
            Kind::Sync => {
                if !(0..=1).contains(&reply.value) || payload.is_some() { return Err(AppDataError::Io); }
                Vec::new()
            }
            Kind::List(limit) => {
                if reply.value as u32 > limit { return Err(AppDataError::Io); }
                if reply.value == 0 {
                    if payload.is_some() { return Err(AppDataError::Io); }
                    Vec::new()
                } else {
                    let bytes = decode_payload(payload, false)?;
                    let entries: Vec<DirectoryEntry> = serde_json::from_slice(&bytes).map_err(|_| AppDataError::Io)?;
                    if entries.len() != reply.value as usize { return Err(AppDataError::Io); }
                    let mut output = Vec::new();
                    for entry in entries {
                        if entry.name.len() > 255 || validate_private_path(&entry.name).map_or(true, |parts| parts.len() != 1) { return Err(AppDataError::Io); }
                        output.extend_from_slice(&(entry.name.len() as u16).to_le_bytes());
                        output.push(u8::from(entry.is_directory));
                        output.extend_from_slice(entry.name.as_bytes());
                    }
                    if output.len() > MAX_OUTPUT { return Err(AppDataError::Io); }
                    output
                }
            }
        };
        Ok(WireReply { value: reply.value, data })
    }
}

fn decode_payload(payload: Option<&str>, empty_allowed: bool) -> Result<Vec<u8>, AppDataError> {
    match payload {
        None if empty_allowed => Ok(Vec::new()),
        Some(value) if value.len() <= 87_384 => {
            let bytes = STANDARD.decode(value).map_err(|_| AppDataError::Io)?;
            if bytes.len() > MAX_OUTPUT { return Err(AppDataError::Io); }
            Ok(bytes)
        }
        _ => Err(AppDataError::Io),
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DirectoryEntry { name: String, is_directory: bool }
