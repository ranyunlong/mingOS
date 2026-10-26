use ming_app_host::ui::ViewBridge;
#[test]
fn close_has_priority_even_when_motion_queue_is_full() {
    let (b, tx) = ViewBridge::new();
    for _ in 0..128 {
        tx.try_send([3, 1, 1, 0]).unwrap();
    }
    b.request_close();
    assert_eq!(b.next_event(), [0, 0, 0, 0]);
}
#[test]
fn latest_resize_is_coalesced_independently_of_full_queue() {
    let (b, tx) = ViewBridge::new();
    for _ in 0..128 {
        tx.try_send([3, 1, 1, 0]).unwrap();
    }
    b.resize(800, 600);
    b.resize(1000, 700);
    assert_eq!(b.next_event(), [1, 1000, 700, 0]);
    assert_eq!(b.next_event(), [3, 1, 1, 0]);
    b.request_close();
    assert_eq!(b.next_event(), [0, 0, 0, 0]);
}

#[test]
fn pointer_motion_is_accumulated_instead_of_queued() {
    let (b, _tx) = ViewBridge::new();
    for _ in 0..100 {
        b.pan(1, -2);
    }
    assert_eq!(b.next_event(), [3, 100, -200, 0]);
    b.zoom(100);
    b.zoom(100);
    assert_eq!(b.next_event(), [2, 200, 0, 0]);
    b.request_close();
    assert_eq!(b.next_event(), [0, 0, 0, 0]);
}

#[test]
fn short_key_edges_are_not_lost_between_frame_polls() {
    let mut keys = ming_app_host::ui::KeyLatch::default();
    assert_eq!(keys.edge(2, true), Some([6, 0, 0, 0]));
    assert_eq!(keys.edge(2, false), None);
    assert_eq!(keys.edge(0, true), Some([7, 0, 0, 0]));
    assert_eq!(keys.edge(0, true), None);
    assert_eq!(keys.edge(0, false), None);
    assert_eq!(keys.edge(3, true), Some([0, 0, 0, 0]));
}

#[test]
fn text_key_packets_work_without_double_firing_physical_keys() {
    let mut keys = ming_app_host::ui::KeyLatch::default();
    assert_eq!(keys.character(2), Some([6, 0, 0, 0]));
    assert_eq!(keys.edge(0, true), Some([7, 0, 0, 0]));
    assert_eq!(keys.character(0), None);
    keys.edge(0, false);
    assert_eq!(keys.character(0), Some([7, 0, 0, 0]));
}

#[test]
fn drag_release_keeps_the_last_pointer_delta() {
    let mut p = ming_app_host::ui::PointerSample::default();
    assert_eq!(p.sample(Some((10, 10)), true), Some([4, 10, 10, 1]));
    assert_eq!(p.sample(Some((30, 20)), false), Some([3, 20, 10, 0]));
    assert_eq!(p.sample(Some((30, 20)), false), None);
}

#[test]
fn a_completed_worker_result_survives_later_disconnect() {
    let (tx, rx) = std::sync::mpsc::channel();
    tx.send(Ok(7)).unwrap();
    drop(tx);
    let mut state = None;
    assert!(ming_app_host::ui::poll_completion(&mut state, &rx));
    assert!(ming_app_host::ui::poll_completion(&mut state, &rx));
    assert_eq!(state.unwrap().unwrap(), 7);
}

#[test]
fn large_viewports_stay_bounded_and_pointer_mapping_matches_the_surface() {
    let v = ming_app_host::ui::viewport(3840.0, 2160.0);
    assert!(v.0 * v.1 <= 4194304);
    assert!(((v.0 as f64 / v.1 as f64) - (3840.0 / 2160.0)).abs() < 0.01);
    let p = ming_app_host::ui::project_pointer(1920.0, 1080.0, 3840, 2160, v).unwrap();
    assert!((p.0 - v.0 as i32 / 2).abs() <= 1);
    assert!((p.1 - v.1 as i32 / 2).abs() <= 1);
    assert!(ming_app_host::ui::project_pointer(0.0, 0.0, 0, 0, v).is_none());
}

#[test]
fn focus_loss_cancels_pointer_and_key_holds() {
    let mut pointer = ming_app_host::ui::PointerSample::default();
    pointer.sample(Some((10, 10)), true);
    pointer.cancel();
    assert_eq!(pointer.sample(Some((30, 20)), false), None);
    let mut keys = ming_app_host::ui::KeyLatch::default();
    keys.edge(0, true);
    keys.cancel();
    assert_eq!(keys.edge(0, true), Some([7, 0, 0, 0]));
}
#[test]
fn pointer_input_waits_for_the_matching_presented_viewport() {
    let mut gate = ming_app_host::ui::ViewportGate::default();
    gate.resize((1100, 760));
    assert_eq!(gate.viewport(), None);
    gate.present((1100, 760));
    assert_eq!(gate.viewport(), Some((1100, 760)));
    gate.resize((1000, 700));
    gate.present((1100, 760));
    assert_eq!(gate.viewport(), None);
    gate.present((1000, 700));
    assert_eq!(gate.viewport(), Some((1000, 700)));
}
