use ming_app_host::desktop_model::{Rect, WindowId, WindowView};
use ming_app_host::graphics::desktop_shell::{DesktopShell, ShellApp, ShellAppState, ShellWindow};
use ming_app_host::graphics::{FramePixels, RenderedFrame};
use std::sync::Arc;

fn root(w: usize, h: usize) -> RenderedFrame {
    RenderedFrame {
        width: w,
        height: h,
        pixels: FramePixels::Cpu(Arc::new(vec![0xff123456; w * h])),
        sequence: 73,
        render_us: 0,
        text_prepare_us: None,
        gpu_timing: None,
        commands: 0,
    }
}
fn app(id: u16, state: ShellAppState) -> ShellApp {
    ShellApp {
        id,
        title: format!("App {id}"),
        state,
    }
}
fn window(id: u64, app: u16, x: i32, y: i32, has_content: bool) -> ShellWindow {
    ShellWindow {
        view: WindowView {
            id: WindowId(id),
            app,
            outer: Rect {
                x,
                y,
                width: 240,
                height: 160,
            },
            minimized: false,
            maximized: false,
            focused: false,
            input_ready: false,
            presented_sequence: 0,
        },
        has_content,
    }
}
fn pixel(f: &RenderedFrame, x: usize, y: usize) -> u32 {
    f.cpu_pixels().unwrap()[y * f.width + x]
}

#[test]
fn top_title_covers_lower_content_and_keeps_receipt() {
    let src = root(640, 480);
    let windows = [window(1, 1, 30, 40, true), window(2, 2, 80, 100, true)];
    let out = DesktopShell::cpu()
        .draw(
            &src,
            &windows,
            &[
                app(1, ShellAppState::Running),
                app(2, ShellAppState::Running),
            ],
        )
        .unwrap();
    assert_eq!(pixel(&out, 200, 103), 0xffe2e5e9);
    assert_eq!(pixel(&out, 200, 180), 0xff123456);
    assert_eq!((out.width, out.height, out.sequence), (640, 480, 73));
    assert_eq!(pixel(&src, 300, 103), 0xff123456);
}
#[test]
fn upper_content_never_gets_a_lower_title() {
    let src = root(640, 480);
    let windows = [window(1, 1, 80, 100, true), window(2, 2, 30, 40, true)];
    let out = DesktopShell::cpu()
        .draw(
            &src,
            &windows,
            &[
                app(1, ShellAppState::Running),
                app(2, ShellAppState::Running),
            ],
        )
        .unwrap();
    assert_eq!(pixel(&out, 200, 110), 0xff123456);
    assert_eq!(pixel(&out, 310, 103), 0xffe2e5e9);
}
#[test]
fn missing_content_occludes_lower_pixels_and_failed_is_distinct() {
    let src = root(640, 480);
    let windows = [window(1, 1, 30, 40, true), window(2, 2, 80, 100, false)];
    let mut shell = DesktopShell::cpu();
    let loading = shell
        .draw(
            &src,
            &windows,
            &[
                app(1, ShellAppState::Running),
                app(2, ShellAppState::Starting),
            ],
        )
        .unwrap();
    let failed = shell
        .draw(
            &src,
            &windows,
            &[
                app(1, ShellAppState::Running),
                app(2, ShellAppState::Failed),
            ],
        )
        .unwrap();
    assert_eq!(pixel(&loading, 200, 180), 0xfff0f2f5);
    assert_eq!(pixel(&failed, 200, 180), 0xfff9e9e9);
    assert_eq!(pixel(&loading, 92, 148), 0xfff0f2f5); // Starting: S leftmost bit is clear.
    assert_eq!(pixel(&failed, 92, 148), 0xff26313f); // Failed: F leftmost bit is set.
}
#[test]
fn minimized_window_is_hidden_and_dock_states_are_distinct() {
    let src = root(320, 240);
    let apps = [
        app(1, ShellAppState::Closed),
        app(2, ShellAppState::Minimized),
        app(3, ShellAppState::Running),
        app(4, ShellAppState::Failed),
    ];
    let mut win = window(1, 2, 0, 24, false);
    win.view.outer.height = 152;
    win.view.minimized = true;
    let out = DesktopShell::cpu().draw(&src, &[win], &apps).unwrap();
    assert_eq!(pixel(&out, 120, 100), 0xff123456);
    // Four 64px slots with 8px gaps, centered; state point at slot x+29, dock y+50.
    assert_eq!(pixel(&out, 49, 226), 0xffe8ebef);
    assert_eq!(pixel(&out, 121, 226), 0xffe5b64b);
    assert_eq!(pixel(&out, 193, 226), 0xff367be8);
    assert_eq!(pixel(&out, 265, 226), 0xffdf625e);
}
#[test]
fn known_bar_glyph_traffic_lights_and_half_open_edges() {
    let src = root(640, 480);
    let win = window(1, 1, 30, 40, true);
    let out = DesktopShell::cpu()
        .draw(&src, &[win], &[app(1, ShellAppState::Running)])
        .unwrap();
    assert_eq!(pixel(&out, 0, 0), 0xffe8ebef);
    assert_eq!(pixel(&out, 12, 8), 0xff26313f); // M glyph
    assert_eq!(pixel(&out, 46, 56), 0xffdf625e);
    assert_eq!(pixel(&out, 64, 56), 0xffe5b64b);
    assert_eq!(pixel(&out, 82, 56), 0xff56ae79);
    assert_eq!(pixel(&out, 270, 40), 0xff123456);
    assert_eq!(pixel(&out, 30, 72), 0xff123456);
    assert_eq!(pixel(&out, 0, 416), 0xffe8ebef);
}
#[test]
fn invalid_inputs_reject_without_mutating_source_or_next_result() {
    let src = root(640, 480);
    let apps = [app(1, ShellAppState::Running)];
    let win = window(1, 1, 30, 40, true);
    let mut shell = DesktopShell::cpu();
    let before = shell.draw(&src, std::slice::from_ref(&win), &apps).unwrap();
    for (w, h) in [
        (319, 240),
        (320, 239),
        (4097, 240),
        (4096, 4096),
        (usize::MAX, 240),
    ] {
        let mut bad = root(320, 240);
        bad.width = w;
        bad.height = h;
        assert!(shell.draw(&bad, &[], &[]).is_err());
    }
    let mut bad = src.clone();
    bad.pixels = FramePixels::Cpu(Arc::new(vec![0; 1]));
    assert!(shell.draw(&bad, &[], &[]).is_err());
    assert!(
        shell
            .draw(&src, &[win.clone(), win.clone()], &apps)
            .is_err()
    );
    assert!(
        shell
            .draw(
                &src,
                std::slice::from_ref(&win),
                &[apps[0].clone(), apps[0].clone()]
            )
            .is_err()
    );
    assert!(shell.draw(&src, std::slice::from_ref(&win), &[]).is_err());
    assert!(shell.draw(&src, &vec![win.clone(); 5], &apps).is_err());
    assert!(shell.draw(&src, &[], &vec![apps[0].clone(); 5]).is_err());
    for r in [
        Rect {
            x: i32::MAX,
            y: 24,
            width: 240,
            height: 160,
        },
        Rect {
            x: 0,
            y: 23,
            width: 240,
            height: 160,
        },
        Rect {
            x: 0,
            y: 24,
            width: 0,
            height: 160,
        },
        Rect {
            x: 0,
            y: 24,
            width: 240,
            height: 32,
        },
        Rect {
            x: 0,
            y: 24,
            width: 240,
            height: i32::MAX,
        },
        Rect {
            x: 600,
            y: 24,
            width: 240,
            height: 160,
        },
    ] {
        let mut bad = win.clone();
        bad.view.outer = r;
        assert!(shell.draw(&src, &[bad], &apps).is_err());
    }
    let after = shell.draw(&src, &[win], &apps).unwrap();
    assert_eq!(before.cpu_pixels(), after.cpu_pixels());
    assert_eq!(after.sequence, 73);
}
#[test]
fn bounds_four_megapixels_and_ascii_titles_are_bounded() {
    let mut shell = DesktopShell::cpu();
    for (w, h) in [(320, 240), (4096, 1024), (1024, 4096)] {
        let src = root(w, h);
        assert_eq!(shell.draw(&src, &[], &[]).unwrap().sequence, 73);
    }
    let src = root(320, 240);
    let mut win = window(1, 0, 0, 24, true);
    win.view.outer.width = 96;
    win.view.outer.height = 152;
    let a = ShellApp {
        id: 0,
        title: "A\n中".repeat(1000),
        state: ShellAppState::Running,
    };
    let out = shell.draw(&src, &[win], &[a]).unwrap();
    assert_eq!(pixel(&out, 96, 40), 0xff123456);
}

#[test]
fn dock_geometry_matches_rendered_cells_and_invalid_is_empty() {
    use ming_app_host::graphics::desktop_shell::dock_cell;
    assert_eq!(
        dock_cell(320, 240, 0, 4),
        Rect {
            x: 20,
            y: 176,
            width: 64,
            height: 64
        }
    );
    assert_eq!(
        dock_cell(320, 240, 3, 4),
        Rect {
            x: 236,
            y: 176,
            width: 64,
            height: 64
        }
    );
    assert_eq!(
        dock_cell(640, 480, 0, 1),
        Rect {
            x: 288,
            y: 416,
            width: 64,
            height: 64
        }
    );
    for (w, h, i, n) in [
        (319, 240, 0, 1),
        (320, 239, 0, 1),
        (-1, 240, 0, 1),
        (320, 240, 0, 0),
        (320, 240, 0, 5),
        (320, 240, 4, 4),
        (4096, 4096, 0, 1),
    ] {
        let r = dock_cell(w, h, i, n);
        assert_eq!((r.width, r.height), (0, 0));
    }
    let r = dock_cell(320, 240, 0, 4);
    assert!(r.contains(20, 176));
    assert!(!r.contains(84, 176));
    assert!(!r.contains(20, 240));
}

#[cfg(feature = "gpu")]
#[test]
#[ignore = "requires hardware GPU; main owns GPU validation and presenter checks"]
fn hardware_gpu_matches_scalar_oracle_without_implicit_readback() -> anyhow::Result<()> {
    use ming_app_host::graphics::{GpuSession, compositor::Compositor};
    let session = GpuSession::new()?;
    let mut source_compositor = Compositor::gpu(&session)?;
    let source = source_compositor.compose(640, 480, 0xff123456, &[])?;
    let cpu_source = RenderedFrame {
        pixels: root(640, 480).pixels,
        ..source.clone()
    };
    let mut gpu = DesktopShell::gpu(&session)?;
    let mut cpu = DesktopShell::cpu();
    let mut apps = vec![
        app(1, ShellAppState::Running),
        app(2, ShellAppState::Starting),
        app(3, ShellAppState::Minimized),
        app(4, ShellAppState::Closed),
    ];
    apps[0].title = "Ming shell / title 0123456789 OVERFLOW".into();
    apps[1].title = "Long中\nLabel".into();
    let mut windows = vec![
        window(1, 1, 30, 40, true),
        window(2, 2, 80, 100, false),
        window(3, 3, 110, 160, true),
    ];
    windows[0].view.focused = true;
    windows[2].view.minimized = true;
    let source_stats = source_compositor.gpu_stats()?.unwrap();
    let mut held = None;
    for step in 0..6 {
        match step {
            1 => apps[1].state = ShellAppState::Failed,
            2 => windows[1].has_content = true,
            3 => {
                windows[1].view.minimized = true;
                apps[1].state = ShellAppState::Minimized;
            }
            4 => {
                windows.reverse();
                windows[0].view.minimized = false;
            }
            5 => {
                windows.clear();
                apps.clear();
            }
            _ => {}
        }
        let before = gpu.gpu_stats()?.unwrap();
        let out = gpu.draw(&source, &windows, &apps)?;
        let after = gpu.gpu_stats()?.unwrap();
        assert!(out.cpu_pixels().is_none());
        assert_eq!(
            (out.width, out.height, out.sequence),
            (source.width, source.height, source.sequence)
        );
        assert_eq!(after.readback_bytes, before.readback_bytes);
        assert_eq!(after.image_upload_bytes, 0);
        assert_eq!(after.presentation_upload_bytes, 0);
        assert_eq!(after.render_submissions, before.render_submissions + 1);
        assert_eq!(after.scene_upload_bytes - before.scene_upload_bytes, 3508);
        if step == 0 {
            held = Some((
                out.clone(),
                cpu.draw(&cpu_source, &windows, &apps)?.read_pixels()?,
            ));
        }
        assert_eq!(
            out.read_pixels()?.as_slice(),
            cpu.draw(&cpu_source, &windows, &apps)?
                .cpu_pixels()
                .unwrap()
        );
        assert_eq!(
            source_compositor.gpu_stats()?.unwrap().readback_bytes,
            source_stats.readback_bytes
        );
    }
    let (held_frame, held_pixels) = held.unwrap();
    assert_eq!(held_frame.read_pixels()?.as_slice(), held_pixels.as_slice());
    assert_eq!(session.stats()?.readback_bytes, 0);
    assert_eq!(session.stats()?.render_submissions, 0);
    assert_eq!(
        source_compositor.gpu_stats()?.unwrap().render_submissions,
        source_stats.render_submissions
    );
    // Same u32 ARGB buffer storage as Compositor; existing RGBA/BGRA presenter
    // tests can consume this output without an upload or packing adaptation.
    let before = gpu.gpu_stats()?.unwrap();
    assert!(gpu.draw(&cpu_source, &[], &[]).is_err());
    assert!(cpu.draw(&source, &[], &[]).is_err());
    let mut malformed = source.clone();
    malformed.width = 320;
    assert!(gpu.draw(&malformed, &[], &[]).is_err());
    assert!(
        gpu.draw(&source, &[window(1, 99, 30, 40, true)], &[])
            .is_err()
    );
    assert!(
        gpu.draw(
            &source,
            &[window(1, 1, 30, 40, true), window(1, 1, 30, 40, true)],
            &[app(1, ShellAppState::Running)]
        )
        .is_err()
    );
    assert!(
        gpu.draw(
            &source,
            &[],
            &[
                app(1, ShellAppState::Running),
                app(1, ShellAppState::Closed)
            ]
        )
        .is_err()
    );
    let other = GpuSession::new()?;
    let wrong = Compositor::gpu(&other)?.compose(640, 480, 0xff123456, &[])?;
    assert!(gpu.draw(&wrong, &[], &[]).is_err());
    let after = gpu.gpu_stats()?.unwrap();
    assert_eq!(before.render_submissions, after.render_submissions);
    assert_eq!(before.scene_upload_bytes, after.scene_upload_bytes);
    assert_eq!(before.readback_bytes, after.readback_bytes);
    assert!(gpu.draw(&source, &[], &[]).is_ok());
    Ok(())
}
#[test]
fn preserved_pixels_are_exact_and_outputs_remain_immutable() {
    let mut src = root(640, 480);
    src.pixels = FramePixels::Cpu(Arc::new(
        (0..640 * 480).map(|i| 0x80000000u32 | i as u32).collect(),
    ));
    let windows = [window(1, 1, 80, 100, true), window(2, 2, 30, 40, true)];
    let apps = [
        app(1, ShellAppState::Failed),
        app(2, ShellAppState::Running),
    ];
    let mut shell = DesktopShell::cpu();
    let a = shell.draw(&src, &windows, &apps).unwrap();
    for y in 24..416 {
        for x in 0..640 {
            let top = windows.iter().rev().find(|w| w.view.outer.contains(x, y));
            if top.is_none_or(|w| y >= w.view.outer.y + 32) {
                assert_eq!(
                    pixel(&a, x as usize, y as usize),
                    pixel(&src, x as usize, y as usize)
                );
            }
        }
    }
    let saved = a.read_pixels().unwrap();
    let _b = shell.draw(&src, &[], &[]).unwrap();
    assert_eq!(a.cpu_pixels().unwrap(), saved.as_slice());
    let source_pixels = src.read_pixels().unwrap();
    assert!(!Arc::ptr_eq(&source_pixels, &saved));
}
#[test]
fn shell_keeps_scene_receipt_and_never_opens_the_source_viewport_gate() {
    use ming_app_host::{desktop_scene::DesktopScene, graphics::compositor::Compositor};
    let mut scene = DesktopScene::new(640, 480, Compositor::cpu()).unwrap();
    let id = scene.model_mut().open(0).unwrap().0;
    let mut shell = DesktopShell::cpu();
    // A cached source with the wrong viewport may be scaled visually; its
    // original dimensions must still reach DesktopModel::presented.
    scene.set_frame(id, root(320, 240)).unwrap();
    let composed = scene.compose().unwrap();
    let windows = scene
        .model()
        .views()
        .into_iter()
        .map(|view| ShellWindow {
            view,
            has_content: true,
        })
        .collect::<Vec<_>>();
    let out = shell
        .draw(&composed, &windows, &[app(0, ShellAppState::Running)])
        .unwrap();
    assert_eq!(out.sequence, composed.sequence);
    assert!(!scene.model().view(id).unwrap().input_ready);
    scene.presented(out.sequence).unwrap();
    assert!(!scene.model().view(id).unwrap().input_ready);
    assert!(scene.presented(out.sequence).is_err());
    let size = scene.model().view(id).unwrap().outer.content();
    let mut matching = root(size.width as usize, size.height as usize);
    matching.sequence = 74;
    scene.set_frame(id, matching).unwrap();
    let composed = scene.compose().unwrap();
    let out = shell
        .draw(&composed, &windows, &[app(0, ShellAppState::Running)])
        .unwrap();
    assert!(!scene.model().view(id).unwrap().input_ready);
    scene.presented(out.sequence).unwrap();
    assert!(scene.model().view(id).unwrap().input_ready);
}
#[test]
fn titles_and_dock_labels_truncate_before_layout_and_sanitize_controls() {
    let src = root(640, 480);
    let win = window(1, 0, 30, 40, true);
    let prefix = "A?B?CDEFGHIJKLMNOPQRSTUV";
    let mut shell = DesktopShell::cpu();
    let a = ShellApp {
        id: 0,
        title: format!("{prefix} AFTER"),
        state: ShellAppState::Running,
    };
    let b = ShellApp {
        id: 0,
        title: "A\nB中CDEFGHIJKLMNOPQRSTUV DIFFERENT".to_string(),
        state: ShellAppState::Running,
    };
    let one = shell.draw(&src, std::slice::from_ref(&win), &[a]).unwrap();
    let two = shell.draw(&src, &[win], &[b]).unwrap();
    assert_eq!(one.cpu_pixels(), two.cpu_pixels());
    assert_eq!(pixel(&one, 104, 49), 0xff26313f); // Title A starts at outer.x+72.
    assert_eq!(pixel(&one, 568, 5), 0xff26313f); // Right bounded POWER.
    assert_eq!(pixel(&one, 200, 71), 0xffc8ced6);
}

#[test]
fn topbar_and_title_have_exact_integer_double_size_glyphs() {
    let src = root(320, 240);
    let mut win = window(1, 0, 0, 24, true);
    win.view.outer.height = 152;
    let a = ShellApp {
        id: 0,
        title: "M".into(),
        state: ShellAppState::Running,
    };
    let out = DesktopShell::cpu().draw(&src, &[win], &[a]).unwrap();
    let m = [17u8, 27, 21, 21, 17, 17, 17];
    let p = [30u8, 17, 17, 30, 16, 16, 16];
    for (left, top, glyph, background) in [
        (12, 5, m, 0xffe8ebef),
        (248, 5, p, 0xffe8ebef),
        (72, 33, m, 0xffe2e5e9),
    ] {
        for y in 0..14usize {
            for x in 0..12usize {
                let ink = x < 10 && glyph[y / 2] & (1 << (4 - x / 2)) != 0;
                assert_eq!(
                    pixel(&out, left + x, top + y),
                    if ink { 0xff26313f } else { background },
                    "2x glyph at {left}+{x},{top}+{y}"
                );
            }
        }
    }
    assert_eq!(pixel(&out, 12, 4), 0xffe8ebef);
    assert_eq!(pixel(&out, 12, 19), 0xffe8ebef);
    assert_eq!(pixel(&out, 72, 32), 0xffe2e5e9);
    assert_eq!(pixel(&out, 72, 47), 0xffe2e5e9);
    assert_eq!(pixel(&out, 308, 5), 0xffe8ebef);
    assert_eq!(pixel(&out, 309, 18), 0xffe8ebef);
    assert_eq!((out.width, out.height, out.sequence), (320, 240, 73));
}

#[test]
fn double_size_title_truncates_by_twelve_pixel_advance_without_touching_buttons() {
    let src = root(640, 480);
    let a = ShellApp {
        id: 0,
        title: "M".repeat(100),
        state: ShellAppState::Running,
    };
    let m = [17u8, 27, 21, 21, 17, 17, 17];
    for width in [
        80, 81, 90, 91, 92, 93, 103, 104, 115, 116, 239, 240, 379, 380, 640,
    ] {
        let mut win = window(1, 0, 0, 24, true);
        win.view.outer.width = width;
        let out = DesktopShell::cpu()
            .draw(&src, &[win], std::slice::from_ref(&a))
            .unwrap();
        let count = ((width - 80) / 12).min(24) as usize;
        for y in 0..32usize {
            for x in 72..width as usize {
                let ink = (9..23).contains(&y)
                    && x - 72 < count * 12
                    && (x - 72) % 12 < 10
                    && m[(y - 9) / 2] & (1 << (4 - ((x - 72) % 12) / 2)) != 0;
                let expected = if y == 31 {
                    0xffc8ced6
                } else if ink {
                    0xff26313f
                } else {
                    0xffe2e5e9
                };
                assert_eq!(
                    pixel(&out, x, 24 + y),
                    expected,
                    "width={width} title at {x},{y}"
                );
            }
        }
        for (x, color) in [(16, 0xffdf625e), (34, 0xffe5b64b), (52, 0xff56ae79)] {
            assert_eq!(pixel(&out, x, 40), color);
        }
        if width < 640 {
            assert_eq!(pixel(&out, width as usize, 33), 0xff123456);
        }
    }
}

#[test]
fn dock_icon_doubles_while_label_and_state_point_keep_original_geometry() {
    use ming_app_host::graphics::desktop_shell::dock_cell;
    let src = root(320, 240);
    let a = ShellApp {
        id: 0,
        title: "M".repeat(8),
        state: ShellAppState::Running,
    };
    let out = DesktopShell::cpu().draw(&src, &[], &[a]).unwrap();
    let cell = dock_cell(320, 240, 0, 1);
    assert_eq!(
        cell,
        Rect {
            x: 128,
            y: 176,
            width: 64,
            height: 64
        }
    );
    let m = [17u8, 27, 21, 21, 17, 17, 17];
    for y in 0..14usize {
        for x in 0..12usize {
            let ink = x < 10 && m[y / 2] & (1 << (4 - x / 2)) != 0;
            assert_eq!(
                pixel(&out, 155 + x, 190 + y),
                if ink { 0xff26313f } else { 0xfff8f9fb }
            );
        }
    }
    for (y, &row) in m.iter().enumerate() {
        for x in 0..48usize {
            let ink = x % 6 < 5 && row & (1 << (4 - x % 6)) != 0;
            assert_eq!(
                pixel(&out, 136 + x, 216 + y),
                if ink { 0xff26313f } else { 0xffe8ebef }
            );
        }
    }
    for y in 223..226 {
        for x in 136..184 {
            assert_eq!(pixel(&out, x, y), 0xffe8ebef);
        }
    }
    for y in 226..230 {
        for x in 157..163 {
            assert_eq!(pixel(&out, x, y), 0xff367be8);
        }
    }
}
