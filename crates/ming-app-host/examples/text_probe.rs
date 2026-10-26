#[cfg(feature = "text")]
fn main() -> anyhow::Result<()> {
    use ming_app_host::text::TextEngine;
    use std::{
        io::{self, Write},
        time::Instant,
    };
    println!("{}", serde_json::json!({"phase":"before"}));
    io::stdout().flush()?;
    let mut line = String::new();
    io::stdin().read_line(&mut line)?;
    let start = Instant::now();
    let mut engine = TextEngine::new()?;
    let initialized = start.elapsed().as_micros();
    let mut samples = Vec::new();
    for size in [12, 16, 24, 48, 96] {
        let t = Instant::now();
        let layout = engine.layout("MingOS 中文，标点。AV fi é A\u{0301}", 1, size * 64, 0)?;
        let masks = engine.raster_layout(&layout, 16 * 64, 100 * 64)?;
        samples.push(serde_json::json!({"sizePx":size,"micros":t.elapsed().as_micros(),"metrics":layout.metrics,"masks":masks.len(),"retainedBytes":layout.retained_bytes()}));
    }
    let stress: String = (0x4e00..0x4e00 + 170).filter_map(char::from_u32).collect();
    let t = Instant::now();
    let layout = engine.layout(&stress, 1, 96 * 64, 1)?;
    let masks = engine.raster_layout(&layout, 0, 100 * 64)?;
    samples.push(serde_json::json!({"case":"bounded CJK stress","bytes":stress.len(),"micros":t.elapsed().as_micros(),"metrics":layout.metrics,"masks":masks.len()}));
    if let Some(path) = std::env::args_os().nth(1) {
        let (width, height) = (1280usize, 640usize);
        let mut pixels = vec![0xfff5f3edu32; width * height];
        for (size, baseline) in [(12, 60), (16, 130), (24, 230), (48, 380)] {
            let layout = engine.layout("MingOS 中文，标点。AV fi é A\u{0301}", 1, size * 64, 0)?;
            for mask in engine.raster_layout(&layout, 32 * 64, baseline * 64)? {
                for y in 0..mask.height {
                    for x in 0..mask.width {
                        let px = mask.left + x as i32;
                        let py = mask.top + y as i32;
                        if px < 0 || py < 0 || px >= width as i32 || py >= height as i32 {
                            continue;
                        }
                        let alpha = u32::from(mask.data[y * mask.width + x]);
                        let dst = &mut pixels[py as usize * width + px as usize];
                        let mut color = 0xff000000;
                        for shift in [0, 8, 16] {
                            color |= ((((0xff20354fu32 >> shift) & 255) * alpha
                                + ((*dst >> shift) & 255) * (255 - alpha)
                                + 127)
                                / 255)
                                << shift;
                        }
                        *dst = color;
                    }
                }
            }
        }
        let frame = ming_app_host::graphics::RenderedFrame {
            width,
            height,
            pixels: ming_app_host::graphics::FramePixels::Cpu(std::sync::Arc::new(pixels)),
            sequence: 1,
            render_us: 0,
            text_prepare_us: None,
            gpu_timing: None,
            commands: 4,
        };
        ming_app_host::graphics::write_png(std::path::Path::new(&path), &frame)?;
    }
    let stats = engine.cache_stats();
    println!(
        "{}",
        serde_json::json!({"phase":"ready","initializationMicros":initialized,"maskBytes":stats.mask_bytes,"maskEntries":stats.entries,"hits":stats.hits,"misses":stats.misses,"samples":samples})
    );
    io::stdout().flush()?;
    io::stdin().read_line(&mut line)?;
    Ok(())
}
#[cfg(not(feature = "text"))]
fn main() {
    eprintln!("TEXT_FEATURE_REQUIRED");
    std::process::exit(1);
}
