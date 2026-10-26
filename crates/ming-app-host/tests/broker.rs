use ming_app_host::{broker::Broker, package::read_bounded};
use std::{collections::BTreeMap, fs};

#[test]
fn granted_resources_only_and_handles_are_instance_scoped() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("private.txt");
    fs::write(&file, "MingOS\n").unwrap();
    let grants = BTreeMap::from([(1, file)]);
    let mut a = Broker::new(&grants).unwrap();
    let mut b = Broker::new(&grants).unwrap();
    let h = a.dispatch("file_open", &[1]);
    assert!(h > 0);
    assert_eq!(a.dispatch("file_open", &[2]), -1);
    assert_eq!(a.dispatch("file_length", &[h]), 7);
    assert_eq!(a.dispatch("file_read_byte", &[h, 0]), 77);
    assert_eq!(b.dispatch("file_length", &[h]), -2);
    assert_eq!(a.dispatch("file_close", &[h]), 0);
    assert_eq!(a.dispatch("file_length", &[h]), -2);
}
#[test]
fn invalid_arguments_and_limits_fail_closed() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("data");
    fs::write(&file, b"abc").unwrap();
    let mut b = Broker::new(&BTreeMap::from([(1, file)])).unwrap();
    let h = b.dispatch("file_open", &[1]);
    for args in [vec![h, -1], vec![h, 3], vec![h, 0, 1]] {
        assert_eq!(b.dispatch("file_read_byte", &args), -2);
    }
    assert_eq!(b.dispatch("file_open", &[1, 2]), -2);
    assert_eq!(b.dispatch("fs.readFile", &[1]), -2);
    for _ in 1..32 {
        assert!(b.dispatch("file_open", &[1]) > 0);
    }
    assert_eq!(b.dispatch("file_open", &[1]), -4);
    for _ in 0..4096 {
        assert_eq!(b.dispatch("log", &[1, 2]), 0);
    }
    assert_eq!(b.dispatch("log", &[1, 2]), -4);
    assert_eq!(b.events().len(), 4096);
}
#[test]
fn ordinary_files_only_and_size_is_bounded() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("large");
    fs::write(&file, vec![0; 262145]).unwrap();
    assert!(
        Broker::new(&BTreeMap::from([(1, file)]))
            .err()
            .unwrap()
            .to_string()
            .contains("RESOURCE_TOO_LARGE")
    );
    assert!(
        read_bounded(dir.path(), 1024, "MODULE")
            .unwrap_err()
            .to_string()
            .contains("NOT_FILE")
    );
}
#[test]
fn bounded_reader_rejects_large_inputs() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("data");
    fs::write(&file, b"abc").unwrap();
    assert_eq!(read_bounded(&file, 3, "MODULE").unwrap(), b"abc");
    assert!(
        read_bounded(&file, 2, "MODULE")
            .unwrap_err()
            .to_string()
            .contains("TOO_LARGE")
    );
}
