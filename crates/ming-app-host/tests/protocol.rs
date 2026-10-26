use ming_app_host::protocol::{read_json, write_json};
use serde_json::{Value, json};
use std::io::Cursor;
#[test]
fn protocol_is_line_framed_bounded_and_rejects_truncation() {
    let value: Value = read_json(&mut Cursor::new(b"{\"value\":7}\n"), 128)
        .unwrap()
        .unwrap();
    assert_eq!(value["value"], 7);
    assert!(read_json::<Value>(&mut Cursor::new(b"{}"), 128).is_err());
    assert!(
        read_json::<Value>(&mut Cursor::new(vec![b'x'; 129]), 128)
            .unwrap_err()
            .to_string()
            .contains("PROTOCOL_LIMIT")
    );
    assert!(
        read_json::<Value>(&mut Cursor::new(b""), 128)
            .unwrap()
            .is_none()
    );
    let mut output = Vec::new();
    write_json(&mut output, &json!({"value":7})).unwrap();
    assert_eq!(output, b"{\"value\":7}\n");
}
