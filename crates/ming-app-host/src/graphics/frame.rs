use anyhow::{Result, bail};
pub const MAX_FRAME_BYTES: usize = 65536;
pub const MAX_PIXELS: usize = 4_194_304;
#[derive(Clone, Debug)]
pub struct Command {
    pub op: i32,
    pub a: [i32; 7],
    pub text: String,
}
#[derive(Clone, Debug)]
pub struct Frame {
    pub width: usize,
    pub height: usize,
    pub commands: Vec<Command>,
}
impl Frame {
    pub fn decode(bytes: &[u8]) -> Result<Self> {
        Self::decode_for_sdk(bytes, 2)
    }
    pub fn decode_for_sdk(bytes: &[u8], sdk_version: u32) -> Result<Self> {
        if bytes.len() < 24 || bytes.len() > MAX_FRAME_BYTES {
            bail!("FRAME_INVALID_LENGTH");
        }
        let ints: Vec<i32> = bytes
            .as_chunks::<4>()
            .0
            .iter()
            .map(|b| i32::from_le_bytes(*b))
            .collect();
        let (w, h, n, pool) = (ints[2], ints[3], ints[4], ints[5]);
        if ints[0] != 0x4d474632
            || ints[1] != 1
            || !(1..=4096).contains(&w)
            || !(1..=4096).contains(&h)
            || (w as usize) * (h as usize) > MAX_PIXELS
            || !(0..=256).contains(&n)
            || !(0..=4096).contains(&pool)
        {
            bail!("FRAME_INVALID_HEADER");
        }
        let data_end = 24 + n as usize * 32;
        if data_end + pool as usize != bytes.len() {
            bail!("FRAME_INVALID_LENGTH");
        }
        let pool = &bytes[data_end..];
        let mut commands = Vec::new();
        let coord = |x: i32| (-1_000_000..=1_000_000).contains(&x);
        for index in 0..n as usize {
            let v = &ints[6 + index * 8..14 + index * 8];
            let a: [i32; 7] = v[1..].try_into().expect("command width");
            let mut text = String::new();
            let valid = match v[0] {
                1 | 6 => {
                    (v[0] != 6 || sdk_version == 4)
                        && coord(a[0])
                        && coord(a[1])
                        && (1..=1_000_000).contains(&a[2])
                        && (1..=1_000_000).contains(&a[3])
                        && (0..=64).contains(&a[5])
                        && a[6] == 0
                }
                7 if sdk_version == 4 => {
                    (0x4000_0001..=0x4fff_ffff).contains(&a[0])
                        && coord(a[1])
                        && coord(a[2])
                        && (1..=1_000_000).contains(&a[3])
                        && (1..=1_000_000).contains(&a[4])
                        && a[6] == 0
                }
                2 => {
                    a[0] > 0
                        && coord(a[1])
                        && coord(a[2])
                        && (1..=1_000_000).contains(&a[3])
                        && (1..=1_000_000).contains(&a[4])
                        && a[5] == 0
                        && a[6] == 0
                }
                3 => {
                    if coord(a[0])
                        && coord(a[1])
                        && (1..=4).contains(&a[2])
                        && a[4] >= 0
                        && (0..=256).contains(&a[5])
                        && a[6] == 0
                    {
                        let end = (a[4] as usize).checked_add(a[5] as usize);
                        if let Some(end) = end.filter(|&e| e <= pool.len()) {
                            text = std::str::from_utf8(&pool[a[4] as usize..end])?.to_owned();
                            true
                        } else {
                            false
                        }
                    } else {
                        false
                    }
                }
                4 => {
                    coord(a[0])
                        && coord(a[1])
                        && (0..=1_000_000).contains(&a[2])
                        && (0..=1_000_000).contains(&a[3])
                        && a[4..].iter().all(|&v| v == 0)
                }
                5 if matches!(sdk_version, 3 | 4) && cfg!(feature = "text") => {
                    (0x3000_0001..=0x3fff_ffff).contains(&a[0])
                        && (-64_000_000..=64_000_000).contains(&a[1])
                        && (-64_000_000..=64_000_000).contains(&a[2])
                        && a[4..].iter().all(|&v| v == 0)
                }
                _ => false,
            };
            if !valid {
                bail!("FRAME_INVALID_COMMAND");
            }
            commands.push(Command { op: v[0], a, text });
        }
        Ok(Self {
            width: w as usize,
            height: h as usize,
            commands,
        })
    }
}
