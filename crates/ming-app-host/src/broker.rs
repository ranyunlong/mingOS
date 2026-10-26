use crate::package::open_regular;
use anyhow::{Result, bail};
use serde::Serialize;
use std::{
    collections::BTreeMap,
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::PathBuf,
};

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Event {
    pub code: i32,
    pub value: i32,
}
struct Resource {
    file: File,
    size: i32,
}

pub struct Broker {
    #[cfg(feature = "text")]
    pub(crate) text: Option<crate::text::TextService>,
    sdk_version: u32,
    graphics: crate::graphics::Graphics,
    bulk_bytes_read: u64,
    resources: BTreeMap<u16, Resource>,
    handles: BTreeMap<i32, u16>,
    next_handle: i32,
    events: Vec<Event>,
}

impl Broker {
    pub fn new(grants: &BTreeMap<u16, PathBuf>) -> Result<Self> {
        Self::new_with_sdk(grants, 1)
    }
    pub fn new_with_sdk(grants: &BTreeMap<u16, PathBuf>, sdk_version: u32) -> Result<Self> {
        if !matches!(sdk_version, 1..=6) {
            bail!("INVALID_SDK_VERSION");
        }
        if grants.len() > 16 {
            bail!("TOO_MANY_RESOURCES");
        }
        let mut resources = BTreeMap::new();
        for (&id, path) in grants {
            if id == 0 {
                bail!("INVALID_GRANT");
            }
            let file = open_regular(path, "RESOURCE")?;
            let size = file.metadata()?.len();
            if size
                > if sdk_version == 1 {
                    262_144
                } else {
                    8 * 1024 * 1024
                }
            {
                bail!("RESOURCE_TOO_LARGE");
            }
            resources.insert(
                id,
                Resource {
                    file,
                    size: size as i32,
                },
            );
        }
        Ok(Self {
            #[cfg(feature = "text")]
            text: None,
            sdk_version,
            graphics: crate::graphics::Graphics::new(),
            bulk_bytes_read: 0,
            resources,
            handles: BTreeMap::new(),
            next_handle: 1,
            events: Vec::new(),
        })
    }
    pub fn events(&self) -> &[Event] {
        &self.events
    }
    pub fn dispatch(&mut self, method: &str, args: &[i32]) -> i32 {
        if self.sdk_version >= 4 {
            match (method, args) {
                ("shadow_style_create", [radius, blur]) => {
                    return self.graphics.create_shadow_style(*radius, *blur);
                }
                ("shadow_style_close", [handle]) => {
                    return if self.graphics.close_shadow_style(*handle) {
                        0
                    } else {
                        -2
                    };
                }
                _ => {}
            }
        }
        if matches!(self.sdk_version, 3..=6) && method == "text_layout_close" && args.len() == 1 {
            return self.text_close(args[0]);
        }
        if self.sdk_version >= 2 && method.starts_with("image_") {
            return self.image_call(method, args);
        }
        let arity = match method {
            "file_open" | "file_length" | "file_close" => 1,
            "file_read_byte" | "log" => 2,
            _ => return -2,
        };
        if args.len() != arity {
            return -2;
        }
        if method == "log" {
            if self.events.len() >= 4096 {
                return -4;
            }
            self.events.push(Event {
                code: args[0],
                value: args[1],
            });
            return 0;
        }
        if method == "file_open" {
            let Ok(id) = u16::try_from(args[0]) else {
                return -1;
            };
            if !self.resources.contains_key(&id) {
                return -1;
            }
            if self.handles.len() >= 32 || self.next_handle == i32::MAX {
                return -4;
            }
            let handle = self.next_handle;
            self.next_handle += 1;
            self.handles.insert(handle, id);
            return handle;
        }
        let Some(&id) = self.handles.get(&args[0]) else {
            return -2;
        };
        if method == "file_close" {
            self.handles.remove(&args[0]);
            return 0;
        }
        let resource = self
            .resources
            .get_mut(&id)
            .expect("handle resource invariant");
        if method == "file_length" {
            return resource.size;
        }
        if args[1] < 0 || args[1] >= resource.size {
            return -2;
        }
        let mut byte = [0];
        if resource.file.seek(SeekFrom::Start(args[1] as u64)).is_err()
            || resource.file.read_exact(&mut byte).is_err()
        {
            return -3;
        }
        i32::from(byte[0])
    }
}

#[derive(Debug)]
pub struct ChunkReply {
    pub value: i32,
    pub bytes: Option<Vec<u8>>,
}
impl Broker {
    pub fn set_renderer(&mut self, renderer: crate::graphics::RendererKind) -> Result<()> {
        self.graphics = crate::graphics::Graphics::with_renderer(renderer)?;
        Ok(())
    }
    #[cfg(all(feature = "gpu", feature = "desktop"))]
    pub(crate) fn set_gpu_session(&mut self, session: crate::graphics::GpuSession) {
        self.graphics = crate::graphics::Graphics::with_gpu_session(session);
    }
    #[cfg(all(feature = "gpu", feature = "desktop"))]
    pub(crate) fn gpu_session(&self) -> Option<crate::graphics::GpuSession> {
        self.graphics.gpu_session()
    }
    pub fn check_render_backend(&self) -> Result<()> {
        self.graphics.check_backend()
    }
    pub fn graphics_backend(&self) -> &'static str {
        self.graphics.backend()
    }
    pub fn gpu_info(&self) -> Option<&crate::graphics::GpuInfo> {
        self.graphics.gpu_info()
    }
    pub fn read_chunk(&mut self, h: i32, offset: i32, len: i32) -> ChunkReply {
        let invalid = || ChunkReply {
            value: -2,
            bytes: None,
        };
        if self.sdk_version < 2 || offset < 0 || !(0..=65536).contains(&len) {
            return invalid();
        }
        let Some(id) = self.handles.get(&h) else {
            return invalid();
        };
        let r = self.resources.get_mut(id).expect("resource invariant");
        if offset > r.size {
            return invalid();
        }
        let count = len.min(r.size - offset) as usize;
        let mut bytes = vec![0; count];
        if r.file.seek(SeekFrom::Start(offset as u64)).is_err()
            || r.file.read_exact(&mut bytes).is_err()
        {
            return ChunkReply {
                value: -3,
                bytes: None,
            };
        }
        self.bulk_bytes_read += count as u64;
        ChunkReply {
            value: count as i32,
            bytes: Some(bytes),
        }
    }
    fn image_call(&mut self, method: &str, args: &[i32]) -> i32 {
        if args.len() != 1 {
            return -2;
        }
        let h = args[0];
        match method {
            "image_open" => {
                let Some(id) = self.handles.get(&h) else {
                    return -2;
                };
                let r = self.resources.get_mut(id).expect("resource invariant");
                let mut bytes = vec![0; r.size as usize];
                if r.file.seek(SeekFrom::Start(0)).is_err()
                    || r.file.read_exact(&mut bytes).is_err()
                {
                    return -3;
                }
                match self.graphics.open_png(&bytes) {
                    Ok(id) => id,
                    Err(e) => {
                        if e.to_string().contains("LIMIT") {
                            -4
                        } else {
                            -3
                        }
                    }
                }
            }
            "image_width" => self.graphics.dimensions(h).map_or(-2, |d| d.0),
            "image_height" => self.graphics.dimensions(h).map_or(-2, |d| d.1),
            "image_close" if self.graphics.close_image(h) => 0,
            _ => -2,
        }
    }
    pub fn submit_frame(&mut self, bytes: &[u8]) -> i32 {
        if self.sdk_version < 2 {
            return -2;
        }
        #[cfg(feature = "text")]
        if matches!(self.sdk_version, 3..=6) {
            return match self.graphics.submit_with_text(bytes, self.text.as_mut()) {
                Ok(()) => 0,
                // The supervisor still propagates the latched backend failure.
                Err(_) if self.check_render_backend().is_err() => -2,
                Err(error) => text_error_code(&error),
            };
        }
        match self.graphics.submit(bytes) {
            Ok(()) => 0,
            Err(_) => -2,
        }
    }
    pub fn submit_frame_v4(&mut self, bytes: &[u8]) -> i32 {
        if self.sdk_version < 4 {
            return -2;
        }
        #[cfg(feature = "text")]
        {
            match self.graphics.submit_with_sdk(bytes, self.text.as_mut(), 4) {
                Ok(()) => 0,
                Err(_) if self.check_render_backend().is_err() => -2,
                Err(error) => text_error_code(&error),
            }
        }
        #[cfg(not(feature = "text"))]
        {
            let _ = bytes;
            -6
        }
    }
    pub fn last_frame(&self) -> Option<crate::graphics::RenderedFrame> {
        self.graphics.last_frame()
    }
    pub fn frames_submitted(&self) -> u64 {
        self.graphics.frames_submitted()
    }
    pub fn bulk_bytes_read(&self) -> u64 {
        self.bulk_bytes_read
    }
}

impl Broker {
    pub fn text_create(&mut self, bytes: &[u8], font: i32, size: i32, flags: i32) -> Result<i32> {
        if !matches!(self.sdk_version, 3..=6)
            || bytes.len() > 512
            || std::str::from_utf8(bytes).is_err()
        {
            return Ok(-2);
        }
        #[cfg(feature = "text")]
        {
            if self.text.is_none() {
                match crate::text::TextService::new() {
                    Ok(service) => self.text = Some(service),
                    Err(error) => {
                        return Err(anyhow::anyhow!("TEXT_SERVICE_INIT_FAILED: {error:#}"));
                    }
                }
            }
            Ok(self
                .text
                .as_mut()
                .expect("text initialized")
                .create(bytes, font, size, flags))
        }
        #[cfg(not(feature = "text"))]
        {
            let _ = (font, size, flags);
            Ok(-6)
        }
    }
    pub fn text_metrics(&self, handle: i32) -> Option<Vec<u8>> {
        if !matches!(self.sdk_version, 3..=6) {
            return None;
        }
        #[cfg(feature = "text")]
        {
            self.text
                .as_ref()?
                .metrics(handle)
                .map(|words| words.into_iter().flat_map(i32::to_le_bytes).collect())
        }
        #[cfg(not(feature = "text"))]
        {
            let _ = handle;
            None
        }
    }
    fn text_close(&mut self, handle: i32) -> i32 {
        if !matches!(self.sdk_version, 3..=6) {
            return -2;
        }
        #[cfg(feature = "text")]
        {
            if self.text.as_mut().is_some_and(|t| t.close(handle)) {
                0
            } else {
                -2
            }
        }
        #[cfg(not(feature = "text"))]
        {
            let _ = handle;
            -6
        }
    }
}

/// Shared SDK3 text error classification. Keep quota codes exact so a driver
/// diagnostic mentioning a limit cannot become a recoverable quota result.
#[cfg(feature = "text")]
pub(crate) fn text_error_code(error: &anyhow::Error) -> i32 {
    if error.chain().any(|cause| {
        let message = cause.to_string();
        message.starts_with("RENDERER_FAILED")
            || (message.starts_with("GPU_")
                && !matches!(
                    message.as_str(),
                    "GPU_CANDIDATE_WORK_LIMIT" | "GPU_SCENE_LIMIT"
                ))
    }) {
        return -2;
    }
    // Quota labels can wrap allocator errors rather than be the root cause.
    for cause in error.chain() {
        match cause.to_string().as_str() {
            "TEXT_MISSING_GLYPH" => return -5,
            "TEXT_UNSUPPORTED_CONTROL" | "TEXT_UNSUPPORTED_DIRECTION" | "TEXT_UNSUPPORTED_MASK" => {
                return -6;
            }
            "TEXT_LIMIT"
            | "TEXT_GLYPH_LIMIT"
            | "TEXT_MASK_LIMIT"
            | "TEXT_CACHE_LIMIT"
            | "TEXT_METADATA_LIMIT"
            | "TEXT_FRAME_LIMIT"
            | "TEXT_WORK_LIMIT"
            | "TEXT_STAGING_LIMIT"
            | "TEXT_FRAME_STAGING_LIMIT"
            | "TEXT_TILE_REFERENCE_LIMIT"
            | "TEXT_TILE_CANDIDATE_LIMIT"
            | "TEXT_GPU_TICK_LIMIT"
            | "TEXT_GPU_METADATA_LIMIT"
            | "TEXT_GPU_ENTRY_LIMIT"
            | "TEXT_GPU_RESIDENCY_LIMIT"
            | "TEXT_GPU_UPLOAD_LIMIT"
            | "TEXT_GPU_PINNED_LIMIT"
            | "TEXT_GPU_MASK_LIMIT"
            | "SHADOW_FRAME_LIMIT"
            | "SHADOW_CACHE_LIMIT"
            | "SHADOW_WORK_LIMIT"
            | "SHADOW_TICK_LIMIT"
            | "AA_FRAME_LIMIT"
            | "FRAME_WORK_LIMIT"
            | "GPU_CANDIDATE_WORK_LIMIT"
            | "GPU_SCENE_LIMIT" => return -4,
            _ => {}
        }
    }
    -2
}

#[cfg(test)]
mod render_failure_tests {
    use super::*;
    #[test]
    fn terminal_renderer_failure_cannot_be_reported_as_success_with_stale_pixels() {
        let mut broker = Broker::new_with_sdk(&BTreeMap::new(), 2).unwrap();
        let frame: Vec<u8> = [0x4d474632i32, 1, 1, 1, 0, 0]
            .into_iter()
            .flat_map(i32::to_le_bytes)
            .collect();
        assert_eq!(broker.submit_frame(&frame), 0);
        let sequence = broker.last_frame().unwrap().sequence;
        broker
            .graphics
            .status
            .record("GPU_MAP: injected device failure".into());
        assert_eq!(broker.submit_frame(&frame), -2);
        assert_eq!(broker.last_frame().unwrap().sequence, sequence);
        let error = broker.check_render_backend().unwrap_err().to_string();
        assert!(error.contains("GPU_MAP"));
        // Ordinary invalid input does not poison a healthy renderer.
        let mut healthy = Broker::new_with_sdk(&BTreeMap::new(), 2).unwrap();
        assert_eq!(healthy.submit_frame(b"invalid"), -2);
        healthy.check_render_backend().unwrap();
        assert_eq!(healthy.submit_frame(&frame), 0);
    }
}

#[cfg(all(test, feature = "text"))]
#[path = "broker_frame_tests.rs"]
mod frame_tests;
