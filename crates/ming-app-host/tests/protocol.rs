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

#[derive(Default)]
struct RecordingWriter {
    bytes: Vec<u8>,
    writes: usize,
    flushes: usize,
    max_write: Option<usize>,
}
impl std::io::Write for RecordingWriter {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.writes += 1;
        let count = bytes.len().min(self.max_write.unwrap_or(bytes.len()));
        self.bytes.extend_from_slice(&bytes[..count]);
        Ok(count)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.flushes += 1;
        Ok(())
    }
}

#[test]
fn small_rpc_lines_reach_the_writer_in_one_write_and_one_flush() {
    use ming_app_host::protocol::{Reply, WorkerMessage};
    let mut call = RecordingWriter::default();
    write_json(&mut call, &WorkerMessage::Call {
        id: 7, method: "file_length".into(), args: vec![1], payload: None, remaining_fuel: None,
    }).unwrap();
    let mut reply = RecordingWriter::default();
    write_json(&mut reply, &Reply { id: 7, value: -1, payload: None, fuel_grant: None }).unwrap();
    assert_eq!(call.bytes, b"{\"type\":\"call\",\"id\":7,\"method\":\"file_length\",\"args\":[1]}\n");
    assert_eq!(reply.bytes, b"{\"id\":7,\"value\":-1}\n");
    assert_eq!((call.flushes, reply.flushes), (1, 1));
    assert_eq!((call.writes, reply.writes), (1, 1), "Avoid fragmenting small RPCs into many pipe writes");
}

#[test]
fn large_json_lines_keep_escaping_framing_and_short_write_handling() {
    let value = json!({"payload": "中文\n\"\\".repeat(2000)});
    let mut writer = RecordingWriter { max_write: Some(17), ..Default::default() };
    write_json(&mut writer, &value).unwrap();
    assert!(writer.bytes.len() > 8192);
    assert_eq!(writer.bytes.iter().filter(|&&byte| byte == b'\n').count(), 1);
    assert_eq!(writer.flushes, 1);
    let limit = writer.bytes.len();
    let decoded: Value = read_json(&mut Cursor::new(writer.bytes), limit).unwrap().unwrap();
    assert_eq!(decoded, value);
}

#[test]
fn json_write_and_flush_failures_are_propagated() {
    struct FailingWriter { fail_flush: bool }
    impl std::io::Write for FailingWriter {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            if self.fail_flush { Ok(bytes.len()) } else { Err(std::io::ErrorKind::BrokenPipe.into()) }
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Err(std::io::ErrorKind::BrokenPipe.into())
        }
    }
    for fail_flush in [false, true] {
        let error = write_json(&mut FailingWriter { fail_flush }, &json!({"value": 7})).unwrap_err();
        // Serialization can wrap an I/O failure in serde_json::Error.
        assert!(error.to_string().contains("broken pipe"), "{error:#}");
    }
}
