use crate::{package::read_bounded, supervisor::Options};
use anyhow::{Context, Result, bail};
use std::{collections::BTreeSet, ffi::OsString, path::PathBuf};
pub fn parse(arguments: impl IntoIterator<Item = OsString>) -> Result<Options> {
    let mut args = arguments.into_iter();
    let command = args
        .next()
        .context("USAGE: ming-app-host run|view <app.json>")?;
    if command != "run" && command != "view" {
        bail!("USAGE: ming-app-host run|view <app.json>");
    }
    let manifest = args.next().context("USAGE: missing app.json")?;
    let mut options = Options::new(PathBuf::from(manifest));
    options.preview = command == "view";
    if options.preview {
        options.timeout_ms = 60000;
        options.fuel = 1_000_000_000;
    }
    let mut seen = BTreeSet::new();
    while let Some(name) = args.next() {
        let name = name.to_str().context("INVALID_OPTION_ENCODING")?;
        let value = args.next().context("INVALID_OPTION: missing value")?;
        if name == "--grant" {
            let text = value.to_str().context("INVALID_GRANT_ENCODING")?;
            let (id, file) = text.split_once('=').context("INVALID_GRANT")?;
            if file.is_empty() || id.is_empty() || !id.bytes().all(|b| b.is_ascii_digit()) {
                bail!("INVALID_GRANT");
            }
            let id: u16 = id.parse().context("INVALID_GRANT")?;
            if id == 0 {
                bail!("INVALID_GRANT");
            }
            if options.grants.insert(id, PathBuf::from(file)).is_some() {
                bail!("DUPLICATE_GRANT");
            }
            continue;
        }
        if !seen.insert(name.to_owned()) {
            bail!("DUPLICATE_OPTION");
        }
        if name == "--renderer" {
            options.renderer = match value.to_str() {
                Some("cpu") => crate::graphics::RendererKind::Cpu,
                Some("gpu") => crate::graphics::RendererKind::Gpu,
                _ => bail!("INVALID_RENDERER: expected cpu or gpu"),
            };
            continue;
        }
        if name == "--snapshot" {
            options.snapshot = Some(value.into());
            continue;
        }
        if name == "--events" {
            let bytes = read_bounded(&PathBuf::from(value), 16384, "EVENTS")?;
            options.input_events = serde_json::from_slice(&bytes).context("INVALID_EVENTS")?;
            continue;
        }
        let text = value.to_str().context("INVALID_OPTION_ENCODING")?;
        if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
            bail!("INVALID_OPTION_VALUE");
        }
        match name {
            "--timeout-ms" => options.timeout_ms = text.parse().context("INVALID_TIMEOUT")?,
            "--max-requests" => {
                options.max_requests = text.parse().context("INVALID_REQUEST_LIMIT")?
            }
            "--fuel" => options.fuel = text.parse().context("INVALID_FUEL")?,
            "--memory-bytes" => {
                options.memory_bytes = text.parse().context("INVALID_MEMORY_LIMIT")?
            }
            "--auto-close-ms" => {
                options.auto_close_ms = Some(text.parse().context("INVALID_CLOSE_TIME")?)
            }
            _ => bail!("INVALID_OPTION: {name}"),
        }
    }
    if options.preview && !options.input_events.is_empty()
        || !options.preview && options.auto_close_ms.is_some()
    {
        bail!("INVALID_OPTIONS");
    }
    options.validate()?;
    Ok(options)
}
