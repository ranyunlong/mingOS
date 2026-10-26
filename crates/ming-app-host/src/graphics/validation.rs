use super::{Frame, Image};
use anyhow::{Context, Result, bail};
use std::collections::BTreeMap;

pub fn validate(frame: &Frame, images: &BTreeMap<i32, Image>) -> Result<()> {
    work(frame, images).map(|_| ())
}
pub fn work(frame: &Frame, images: &BTreeMap<i32, Image>) -> Result<usize> {
    let mut clip = [0, 0, frame.width as i32, frame.height as i32];
    let mut ops = 0usize;
    let mut aa_commands = 0usize;
    for c in &frame.commands {
        let a = c.a;
        if c.op == 2 && !images.contains_key(&a[0]) {
            bail!("IMAGE_HANDLE_INVALID");
        }
        let area = |x: i32, y: i32, w: i32, h: i32| {
            let x0 = x.max(clip[0]).max(0).min(frame.width as i32);
            let y0 = y.max(clip[1]).max(0).min(frame.height as i32);
            let x1 = (x + w).min(clip[2]).min(frame.width as i32).max(x0);
            let y1 = (y + h).min(clip[3]).min(frame.height as i32).max(y0);
            (x1 - x0) as usize * (y1 - y0) as usize
        };
        let work = match c.op {
            1 => area(a[0], a[1], a[2], a[3]),
            6 => {
                aa_commands += 1;
                if aa_commands > 16 {
                    bail!("AA_FRAME_LIMIT");
                }
                let r = a[5].min(a[2] / 2).min(a[3] / 2) as usize;
                let samples = r
                    .checked_mul(r)
                    .and_then(|v| v.checked_mul(64))
                    .context("FRAME_WORK_LIMIT")?;
                area(a[0], a[1], a[2], a[3])
                    .checked_add(samples)
                    .context("FRAME_WORK_LIMIT")?
            }
            2 => area(a[1], a[2], a[3], a[4]),
            3 => c.text.len() * 48 * (a[2] * a[2]) as usize,
            4 => {
                clip = [a[0], a[1], a[0] + a[2], a[1] + a[3]];
                0
            }
            _ => 0,
        };
        ops = ops.checked_add(work).context("FRAME_WORK_LIMIT")?;
        if ops > 32_000_000 {
            bail!("FRAME_WORK_LIMIT");
        }
    }
    Ok(ops)
}
