use super::{Frame, shadow_mask::ShadowKey, shadow_service::Styles};
use anyhow::{Context, Result, bail};
use std::collections::BTreeMap;
#[derive(Clone, Copy, Debug)]
pub(crate) struct Draw {
    pub key: ShadowKey,
    pub visible: bool,
}
#[derive(Default, Debug)]
pub(crate) struct PreparedShadows {
    pub draws: BTreeMap<usize, Draw>,
    pub keys: Vec<ShadowKey>,
    pub area_work: usize,
}
impl PreparedShadows {
    pub fn prepare(frame: &Frame, styles: &Styles) -> Result<Self> {
        let mut result = Self::default();
        let mut clip = [0, 0, frame.width as i32, frame.height as i32];
        for (index, command) in frame.commands.iter().enumerate() {
            let a = command.a;
            if command.op == 4 {
                clip = [a[0], a[1], a[0] + a[2], a[1] + a[3]];
                continue;
            }
            if command.op != 7 {
                continue;
            }
            if result.draws.len() >= 8 {
                bail!("SHADOW_FRAME_LIMIT");
            }
            let style = styles.get(a[0]).context("SHADOW_HANDLE_INVALID")?;
            let key = ShadowKey::new(a[3], a[4], style.radius, style.blur)
                .context("SHADOW_GEOMETRY_INVALID")?;
            let halo = key.blur();
            let x0 = (a[1] - halo).max(clip[0]).max(0).min(frame.width as i32);
            let y0 = (a[2] - halo).max(clip[1]).max(0).min(frame.height as i32);
            let x1 = (a[1] + a[3] + halo)
                .min(clip[2])
                .min(frame.width as i32)
                .max(x0);
            let y1 = (a[2] + a[4] + halo)
                .min(clip[3])
                .min(frame.height as i32)
                .max(y0);
            let area = ((x1 - x0) as usize)
                .checked_mul((y1 - y0) as usize)
                .context("SHADOW_WORK_LIMIT")?;
            result.area_work = result
                .area_work
                .checked_add(area)
                .context("SHADOW_WORK_LIMIT")?;
            if result.area_work > 32_000_000 {
                bail!("SHADOW_WORK_LIMIT");
            }
            let visible = area > 0;
            if visible && !result.keys.contains(&key) {
                result.keys.push(key);
            }
            result.draws.insert(index, Draw { key, visible });
        }
        Ok(result)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::graphics::frame::Command;
    fn frame(commands: &[(i32, [i32; 7])]) -> Frame {
        Frame {
            width: 128,
            height: 128,
            commands: commands
                .iter()
                .map(|&(op, a)| Command {
                    op,
                    a,
                    text: String::new(),
                })
                .collect(),
        }
    }
    #[test]
    fn halo_area_is_clipped_and_positions_colors_and_large_resize_reuse_keys() {
        let mut styles = Styles::default();
        let h = styles.create(15, 3);
        let first =
            PreparedShadows::prepare(&frame(&[(7, [h, 10, 20, 100, 80, -1, 0])]), &styles).unwrap();
        assert_eq!(first.area_work, 106 * 86);
        assert_eq!(first.keys.len(), 1);
        assert_eq!(first.draws[&0].key, first.keys[0]);
        assert!(first.draws[&0].visible);
        let clipped = PreparedShadows::prepare(
            &frame(&[
                (4, [0, 0, 20, 25, 0, 0, 0]),
                (7, [h, 10, 20, 100, 80, 0, 0]),
            ]),
            &styles,
        )
        .unwrap();
        assert_eq!(clipped.area_work, 13 * 8);
        assert_eq!(clipped.keys, first.keys);
        let repeated = PreparedShadows::prepare(
            &frame(&[
                (7, [h, 0, 0, 1000, 900, -1, 0]),
                (7, [h, 5, 7, 700, 800, 0x80000000u32 as i32, 0]),
            ]),
            &styles,
        )
        .unwrap();
        assert_eq!(repeated.keys, first.keys);
        assert_eq!(repeated.draws.len(), 2);
    }
    #[test]
    fn invisible_draws_validate_style_and_consume_count_without_cold_keys() {
        let mut styles = Styles::default();
        let h = styles.create(64, 16);
        let empty = PreparedShadows::prepare(
            &frame(&[(4, [0, 0, 0, 0, 0, 0, 0]), (7, [h, 0, 0, 40, 40, -1, 0])]),
            &styles,
        )
        .unwrap();
        assert_eq!(empty.area_work, 0);
        assert!(empty.keys.is_empty());
        assert!(!empty.draws[&1].visible);
        let malformed = frame(&[(4, [0, 0, 0, 0, 0, 0, 0]), (7, [1, 0, 0, 40, 40, -1, 0])]);
        assert_eq!(
            PreparedShadows::prepare(&malformed, &styles)
                .unwrap_err()
                .to_string(),
            "SHADOW_HANDLE_INVALID"
        );
        let count = frame(&[(7, [h, -1_000_000, -1_000_000, 1, 1, 0, 0]); 9]);
        assert_eq!(
            PreparedShadows::prepare(&count, &styles)
                .unwrap_err()
                .to_string(),
            "SHADOW_FRAME_LIMIT"
        );
    }
    #[test]
    fn closed_styles_and_invalid_geometry_cannot_reach_cache_planning() {
        let mut styles = Styles::default();
        let h = styles.create(8, 4);
        let bytes = frame(&[(7, [h, 0, 0, 20, 20, -1, 0])]);
        assert!(styles.close(h));
        assert!(PreparedShadows::prepare(&bytes, &styles).is_err());
        let h = styles.create(8, 4);
        assert_eq!(
            PreparedShadows::prepare(&frame(&[(7, [h, 0, 0, 0, 20, -1, 0])]), &styles)
                .unwrap_err()
                .to_string(),
            "SHADOW_GEOMETRY_INVALID"
        );
    }
}
