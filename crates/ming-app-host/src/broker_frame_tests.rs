use super::*;

fn frame(width: i32, height: i32, commands: &[[i32; 8]]) -> Vec<u8> {
    [0x4d474632, 1, width, height, commands.len() as i32, 0]
        .into_iter()
        .chain(commands.iter().flatten().copied())
        .flat_map(i32::to_le_bytes)
        .collect()
}

fn baseline() -> Vec<u8> {
    frame(128, 64, &[[1, 0, 0, 128, 64, -1, 0, 0]])
}

fn label(handle: i32) -> [i32; 8] {
    [5, handle, 64, 24 * 64, -1, 0, 0, 0]
}

fn assert_recoverable_rejection(broker: &mut Broker, rejected: &[u8], expected: i32) {
    assert_eq!(broker.submit_frame(&baseline()), 0);
    let before = broker.last_frame().unwrap();
    let pixels = before.read_pixels().unwrap();
    assert_eq!(broker.submit_frame(rejected), expected);
    let after = broker.last_frame().unwrap();
    assert_eq!(after.sequence, before.sequence);
    assert_eq!(after.read_pixels().unwrap(), pixels);
    broker.check_render_backend().unwrap();
    assert_eq!(broker.submit_frame(&baseline()), 0);
    assert_eq!(broker.last_frame().unwrap().sequence, before.sequence + 1);
}

fn assert_quota_rejection(broker: &mut Broker, rejected: &[u8], reason: &str) {
    let error = broker
        .graphics
        .submit_with_text(rejected, broker.text.as_mut())
        .unwrap_err();
    assert_eq!(error.root_cause().to_string(), reason);
    assert_recoverable_rejection(broker, rejected, -4);
}

#[test]
fn sdk3_glyph_instance_quota_returns_minus_four_and_preserves_session() {
    let mut broker = Broker::new_with_sdk(&BTreeMap::new(), 3).unwrap();
    let handle = broker.text_create(&[b'A'; 512], 1, 8 * 64, 0).unwrap();
    assert!(handle > 0);
    // Five draws of 512 glyphs exceed 2048 instances, with only 512 unique bytes.
    assert_quota_rejection(
        &mut broker,
        &frame(128, 64, &[label(handle); 5]),
        "TEXT_FRAME_LIMIT",
    );
}

#[test]
fn sdk3_unique_input_quota_returns_minus_four_and_preserves_session() {
    let mut broker = Broker::new_with_sdk(&BTreeMap::new(), 3).unwrap();
    let text = format!("{}  ", "A\u{301}".repeat(170));
    assert_eq!(text.len(), 512);
    let commands: Vec<_> = (0..9)
        .map(|_| {
            let handle = broker.text_create(text.as_bytes(), 1, 8 * 64, 0).unwrap();
            assert!(handle > 0);
            label(handle)
        })
        .collect();
    // Distinct handles charge 4608 bytes; composed glyphs stay below 2048 instances.
    assert_quota_rejection(&mut broker, &frame(128, 64, &commands), "TEXT_FRAME_LIMIT");
}

#[test]
fn sdk3_tile_candidate_quota_returns_minus_four_and_preserves_session() {
    let mut broker = Broker::new_with_sdk(&BTreeMap::new(), 3).unwrap();
    // Zero-advance combining marks share tiles within one text command.
    let text = format!("A{}", "\u{301}".repeat(66));
    let handle = broker.text_create(text.as_bytes(), 1, 16 * 64, 0).unwrap();
    assert!(handle > 0);
    assert_quota_rejection(
        &mut broker,
        &frame(128, 64, &[label(handle)]),
        "TEXT_TILE_CANDIDATE_LIMIT",
    );
}

#[test]
fn sdk3_work_quota_is_minus_four_while_sdk1_and_sdk2_keep_minus_two() {
    let expensive = frame(4096, 1024, &[[1, 0, 0, 4096, 1024, -1, 0, 0]; 8]);
    for (sdk, expected) in [(2, -2), (3, -4)] {
        let mut broker = Broker::new_with_sdk(&BTreeMap::new(), sdk).unwrap();
        assert_recoverable_rejection(&mut broker, &expensive, expected);
    }
    let mut sdk1 = Broker::new_with_sdk(&BTreeMap::new(), 1).unwrap();
    assert_eq!(sdk1.submit_frame(&expensive), -2);
    assert_eq!(sdk1.submit_frame(&baseline()), -2);
    assert_eq!(sdk1.frames_submitted(), 0);
}

#[test]
fn sdk3_syntax_and_stale_handles_stay_minus_two() {
    let mut broker = Broker::new_with_sdk(&BTreeMap::new(), 3).unwrap();
    assert_recoverable_rejection(&mut broker, b"invalid", -2);
    let handle = broker.text_create(b"A", 1, 16 * 64, 0).unwrap();
    assert!(handle > 0);
    let mut invalid = label(handle);
    invalid[5] = 1; // Reserved word must be zero.
    assert_recoverable_rejection(&mut broker, &frame(128, 64, &[invalid]), -2);
    assert_eq!(broker.dispatch("text_layout_close", &[handle]), 0);
    assert_recoverable_rejection(&mut broker, &frame(128, 64, &[label(handle)]), -2);
    let mut sdk2 = Broker::new_with_sdk(&BTreeMap::new(), 2).unwrap();
    assert_recoverable_rejection(&mut sdk2, &frame(128, 64, &[label(handle)]), -2);
}

#[test]
fn sdk3_terminal_backend_error_is_not_a_quota_even_when_message_names_a_limit() {
    let mut broker = Broker::new_with_sdk(&BTreeMap::new(), 3).unwrap();
    assert_eq!(broker.submit_frame(&baseline()), 0);
    let before = broker.last_frame().unwrap();
    broker
        .graphics
        .status
        .record("GPU_MAP: TEXT_GPU_UPLOAD_LIMIT".into());
    assert_eq!(broker.submit_frame(&baseline()), -2);
    assert_eq!(broker.last_frame().unwrap().sequence, before.sequence);
    assert!(
        broker
            .check_render_backend()
            .unwrap_err()
            .to_string()
            .contains("GPU_MAP")
    );
    assert_eq!(broker.submit_frame(&baseline()), -2);
}

#[test]
fn shared_text_classification_preserves_service_codes_and_wrapped_quota_families() {
    for (message, expected) in [
        ("TEXT_MISSING_GLYPH", -5),
        ("TEXT_UNSUPPORTED_CONTROL", -6),
        ("TEXT_UNSUPPORTED_DIRECTION", -6),
        ("TEXT_UNSUPPORTED_MASK", -6),
        ("TEXT_LIMIT", -4),
        ("TEXT_GLYPH_LIMIT", -4),
        ("TEXT_MASK_LIMIT", -4),
        ("TEXT_CACHE_LIMIT", -4),
        ("TEXT_METADATA_LIMIT", -4),
        ("TEXT_FRAME_LIMIT", -4),
        ("TEXT_WORK_LIMIT", -4),
        ("TEXT_STAGING_LIMIT", -4),
        ("TEXT_FRAME_STAGING_LIMIT", -4),
        ("TEXT_TILE_REFERENCE_LIMIT", -4),
        ("TEXT_TILE_CANDIDATE_LIMIT", -4),
        ("TEXT_GPU_TICK_LIMIT", -4),
        ("TEXT_GPU_METADATA_LIMIT", -4),
        ("TEXT_GPU_ENTRY_LIMIT", -4),
        ("TEXT_GPU_RESIDENCY_LIMIT", -4),
        ("TEXT_GPU_UPLOAD_LIMIT", -4),
        ("TEXT_GPU_PINNED_LIMIT", -4),
        ("TEXT_GPU_MASK_LIMIT", -4),
        ("FRAME_WORK_LIMIT", -4),
        ("GPU_CANDIDATE_WORK_LIMIT", -4),
        ("GPU_SCENE_LIMIT", -4),
        ("TEXT_HANDLE_INVALID", -2),
        ("FRAME_INVALID_COMMAND", -2),
        ("GPU_MAP: TEXT_GPU_UPLOAD_LIMIT", -2),
        ("GPU_VALIDATION: FRAME_WORK_LIMIT", -2),
        ("RENDERER_FAILED: TEXT_CACHE_LIMIT", -2),
        ("driver diagnostic mentions TEXT_GPU_UPLOAD_LIMIT", -2),
        ("TEXT_UNKNOWN_LIMIT_DIAGNOSTIC", -2),
    ] {
        let error = anyhow::anyhow!(message).context("preparing SDK3 frame");
        assert_eq!(text_error_code(&error), expected, "{message}");
    }
    let terminal = anyhow::anyhow!("TEXT_GPU_UPLOAD_LIMIT").context("GPU_MAP: device lost");
    assert_eq!(text_error_code(&terminal), -2);
    let terminal = anyhow::anyhow!("TEXT_CACHE_LIMIT").context("RENDERER_FAILED: device lost");
    assert_eq!(text_error_code(&terminal), -2);
}

fn reserve_error_with_context(context: &'static str) -> anyhow::Error {
    let allocation = Vec::<u8>::new().try_reserve(usize::MAX).unwrap_err();
    anyhow::Error::new(allocation).context(context)
}

#[test]
fn allocation_error_with_known_quota_context_returns_minus_four() {
    for quota in [
        "TEXT_GPU_METADATA_LIMIT",
        "TEXT_CACHE_LIMIT",
        "TEXT_STAGING_LIMIT",
        "GPU_SCENE_LIMIT",
    ] {
        let error = reserve_error_with_context(quota).context("preparing SDK3 frame");
        assert!(error.root_cause().is::<std::collections::TryReserveError>());
        assert_eq!(text_error_code(&error), -4, "{quota}");
    }
}

#[test]
fn allocation_error_with_unknown_or_inexact_quota_context_stays_minus_two() {
    for context in [
        "TEXT_UNKNOWN_LIMIT_DIAGNOSTIC",
        "TEXT_GPU_METADATA_LIMIT: allocation failed",
        "preparing TEXT_CACHE_LIMIT",
        "allocator failed",
    ] {
        let error = reserve_error_with_context(context).context("preparing SDK3 frame");
        assert_eq!(text_error_code(&error), -2, "{context}");
    }
}

#[test]
fn terminal_context_overrides_allocation_quota_at_any_chain_position() {
    for terminal in ["GPU_MAP: device lost", "RENDERER_FAILED: device lost"] {
        let error = reserve_error_with_context("TEXT_GPU_METADATA_LIMIT")
            .context(terminal)
            .context("preparing SDK3 frame");
        assert_eq!(text_error_code(&error), -2);
        let error = reserve_error_with_context(terminal)
            .context("TEXT_CACHE_LIMIT")
            .context("preparing SDK3 frame");
        assert_eq!(text_error_code(&error), -2);
    }
}

#[test]
fn sdk4_shadow_quotas_are_exact_recoverable_and_preserve_output() {
    for message in [
        "SHADOW_FRAME_LIMIT",
        "SHADOW_CACHE_LIMIT",
        "SHADOW_WORK_LIMIT",
        "SHADOW_TICK_LIMIT",
    ] {
        assert_eq!(text_error_code(&anyhow::anyhow!(message)), -4, "{message}");
        assert_eq!(
            text_error_code(&anyhow::anyhow!(format!("driver: {message}"))),
            -2
        );
        assert_eq!(
            text_error_code(&anyhow::anyhow!(message).context("GPU_VALIDATION: device failure")),
            -2
        );
    }
    let mut broker = Broker::new_with_sdk(&BTreeMap::new(), 4).unwrap();
    let h = broker.dispatch("shadow_style_create", &[64, 16]);
    assert!(h > 0);
    assert_eq!(broker.submit_frame_v4(&baseline()), 0);
    let old = broker.last_frame().unwrap();
    let rejected = frame(128, 64, &[[7, h, -1000, -1000, 1, 1, 0, 0]; 9]);
    assert_eq!(broker.submit_frame_v4(&rejected), -4);
    assert_eq!(broker.last_frame().unwrap().sequence, old.sequence);
    assert_eq!(
        broker.last_frame().unwrap().read_pixels().unwrap(),
        old.read_pixels().unwrap()
    );
    broker.check_render_backend().unwrap();
    let valid = frame(128, 64, &[[7, h, 4, 4, 100, 40, -1, 0]]);
    assert_eq!(broker.submit_frame(&valid), -2); // Old entry never opts into op7.
    assert_eq!(broker.submit_frame_v4(&valid), 0);
    assert_eq!(broker.dispatch("shadow_style_close", &[h]), 0);
    assert_eq!(broker.submit_frame_v4(&valid), -2);
    assert_eq!(broker.submit_frame_v4(&baseline()), 0);
}
