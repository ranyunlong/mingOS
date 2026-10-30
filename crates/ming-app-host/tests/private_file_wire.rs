#![cfg(target_os = "linux")]
use ming_app_host::{app_data::AppDataError, private_file_wire::WireRequest, protocol::Reply};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::json;

fn field(bytes: &[u8]) -> Vec<u8> {
    let mut result = (bytes.len() as u32).to_le_bytes().to_vec();
    result.extend_from_slice(bytes);
    result
}
fn reply(value: i32, payload: Option<&str>) -> Reply {
    Reply { id: 1, value, payload: payload.map(str::to_owned), fuel_grant: None }
}

#[test]
fn read_request_is_bounded_and_preserves_escaped_paths() {
    let mut bytes = vec![1];
    bytes.extend(field("docs/a\".txt".as_bytes()));
    bytes.extend(3u32.to_le_bytes());
    bytes.extend(12u32.to_le_bytes());
    let parsed = WireRequest::parse(&bytes).unwrap();
    assert_eq!(serde_json::from_str::<serde_json::Value>(parsed.json()).unwrap(), json!({"op":"read","path":"docs/a\".txt","offset":3,"limit":12}));
    let converted = parsed.convert_reply(&reply(3, Some("YWJj"))).unwrap();
    assert_eq!(converted.value, 3);
    assert_eq!(converted.data, b"abc");
    assert!(parsed.convert_reply(&reply(2, Some("YWJj"))).is_err());
    assert!(parsed.convert_reply(&reply(-1, Some("x"))).is_err());
    bytes.push(0);
    assert_eq!(WireRequest::parse(&bytes).err(), Some(AppDataError::InvalidArgument));
}

#[test]
fn wire_rejects_truncation_oversized_fields_and_unknown_operations() {
    for bytes in [vec![], vec![255], vec![1, 1, 0, 0, 0], vec![1, 255, 255, 255, 255]] {
        assert_eq!(WireRequest::parse(&bytes).err(), Some(AppDataError::InvalidArgument));
    }
    let mut bytes = vec![2];
    bytes.extend(field(b"file"));
    bytes.extend(field(&vec![0; 65537]));
    assert_eq!(WireRequest::parse(&bytes).err(), Some(AppDataError::InvalidArgument));
}

#[test]
fn wire_rejects_invalid_paths_and_incomplete_messages() {
    for path in [b"../other".as_slice(), b"/absolute", b"a//b", b"a/../b", b".ming-data-secret", b"\xff"] {
        let mut request = vec![8];
        request.extend(field(path));
        assert_eq!(WireRequest::parse(&request).err(), Some(AppDataError::InvalidArgument));
    }
    let mut request = vec![1];
    request.extend(field("目录/文件".as_bytes()));
    request.extend(0u32.to_le_bytes());
    request.extend(65536u32.to_le_bytes());
    assert!(WireRequest::parse(&request).is_ok());
    for length in 0..request.len() {
        assert_eq!(WireRequest::parse(&request[..length]).err(), Some(AppDataError::InvalidArgument));
    }
}

#[test]
fn empty_read_reply_must_not_include_payload() {
    let mut request = vec![1];
    request.extend(field(b"file"));
    request.extend(0u32.to_le_bytes());
    request.extend(1u32.to_le_bytes());
    let parsed = WireRequest::parse(&request).unwrap();
    assert_eq!(parsed.convert_reply(&reply(0, None)).unwrap().data, b"");
    assert!(parsed.convert_reply(&reply(0, Some(""))).is_err());
}

#[test]
fn directory_reply_is_validated_and_encoded_without_json_in_the_guest() {
    let mut bytes = vec![7];
    bytes.extend(field(b""));
    bytes.extend(0u32.to_le_bytes());
    bytes.extend(2u32.to_le_bytes());
    let parsed = WireRequest::parse(&bytes).unwrap();
    let payload = STANDARD.encode(br#"[{"name":"a.txt","isDirectory":false}]"#);
    let converted = parsed.convert_reply(&reply(1, Some(&payload))).unwrap();
    assert_eq!(converted.value, 1);
    assert_eq!(converted.data, [5, 0, 0, b'a', b'.', b't', b'x', b't']);
    assert!(parsed.convert_reply(&reply(2, Some("W10="))).is_err());
    assert!(parsed.convert_reply(&reply(1, Some("IQ=="))).is_err());
    assert_eq!(parsed.convert_reply(&reply(-3, None)).unwrap().value, -3);
}

#[test]
fn all_write_and_metadata_operations_have_checked_requests_and_replies() {
    let mut write = vec![2]; write.extend(field(b"file")); write.extend(field(b"abc"));
    let mut begin = vec![3]; begin.extend(field(b"file")); begin.extend(3u32.to_le_bytes());
    let mut chunk = vec![4]; chunk.extend(0u32.to_le_bytes()); chunk.extend(field(b"abc"));
    let mut mkdir = vec![8]; mkdir.extend(field(b"dir"));
    let mut remove = vec![9]; remove.extend(field(b"dir"));
    let mut delete = vec![10]; delete.extend(field(b"file"));
    let mut rename = vec![11]; rename.extend(field(b"file")); rename.extend(field(b"other"));
    let cases = [
        (write, json!({"op":"write","path":"file","data":"YWJj"}), 3, Some("1"), vec![1]),
        (begin, json!({"op":"write_begin","path":"file","total":3}), 0, None, vec![]),
        (chunk, json!({"op":"write_chunk","offset":0,"data":"YWJj"}), 3, None, vec![]),
        (vec![5], json!({"op":"write_commit"}), 0, Some("0"), vec![0]),
        (vec![6], json!({"op":"write_abort"}), 0, None, vec![]),
        (mkdir, json!({"op":"mkdir","path":"dir"}), 1, None, vec![]),
        (remove, json!({"op":"remove_dir","path":"dir"}), 1, None, vec![]),
        (delete, json!({"op":"delete","path":"file"}), 1, None, vec![]),
        (rename, json!({"op":"rename","from":"file","to":"other"}), 1, None, vec![]),
    ];
    for (bytes, json, value, payload, data) in cases {
        let request = WireRequest::parse(&bytes).unwrap();
        assert_eq!(serde_json::from_str::<serde_json::Value>(request.json()).unwrap(), json);
        let result = request.convert_reply(&reply(value, payload)).unwrap();
        assert_eq!((result.value, result.data), (value, data));
        assert!(request.convert_reply(&reply(-5, None)).is_err());
        assert!(request.convert_reply(&reply(-1, Some("unexpected"))).is_err());
    }
    let request = WireRequest::parse(&[5]).unwrap();
    assert!(request.convert_reply(&reply(1, Some("1"))).is_err());
    assert!(request.convert_reply(&reply(0, Some("2"))).is_err());
}
