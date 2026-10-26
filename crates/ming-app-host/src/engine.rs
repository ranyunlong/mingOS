use crate::{
    package::MAX_MODULE_BYTES,
    protocol::{Init, Reply, WorkerMessage, read_json, write_json},
};
use anyhow::{Context, Result, bail};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use std::io::{self, BufReader};
use wasmtime::{
    Caller, Config, Engine, ExternType, Linker, Module, Store, StoreLimits, StoreLimitsBuilder,
    Trap, ValType,
};

struct Rpc {
    input: BufReader<io::Stdin>,
    output: io::Stdout,
    sequence: u64,
    fuel: crate::execution_budget::WorkerFuel,
    remaining: Option<u64>,
}
impl Rpc {
    fn exchange(&mut self, method: &str, args: &[i32], payload: Option<String>) -> Result<Reply> {
        self.sequence = self.sequence.checked_add(1).context("PROTOCOL_SEQUENCE")?;
        let remaining_fuel = self.remaining.take();
        write_json(
            &mut self.output,
            &WorkerMessage::Call {
                id: self.sequence,
                method: method.to_owned(),
                args: args.to_vec(),
                payload,
                remaining_fuel,
            },
        )?;
        let reply: Reply = read_json(&mut self.input, 100000)?.context("HOST_DISCONNECTED")?;
        if reply.id != self.sequence {
            bail!("PROTOCOL_SEQUENCE");
        }
        if reply.fuel_grant.is_some() && (self.fuel.cap.is_none() || remaining_fuel.is_none()) {
            bail!("PROTOCOL_UNEXPECTED_CREDIT");
        }
        Ok(reply)
    }
    fn call(&mut self, method: &str, args: &[i32]) -> Result<i32> {
        let reply = self.exchange(method, args, None)?;
        if reply.payload.is_some() {
            bail!("PROTOCOL_UNEXPECTED_PAYLOAD");
        }
        Ok(reply.value)
    }
}

struct State {
    entropy_bytes: usize,
    rpc: Rpc,
    limits: StoreLimits,
}

pub fn validate_imports(module: &Module, sdk_version: u32) -> Result<()> {
    for import in module.imports() {
        let expected = match (import.module(), import.name()) {
            ("ming_v1", "file_open" | "file_length" | "file_close") => 1,
            ("ming_v1", "file_read_byte" | "log") => 2,
            ("ming_v2", "file_read") if sdk_version >= 2 => 4,
            ("ming_v2", "frame_submit" | "event_next" | "random_fill") if sdk_version >= 2 => 2,
            ("ming_v2", "image_open" | "image_width" | "image_height" | "image_close")
                if sdk_version >= 2 =>
            {
                1
            }
            ("ming_v3", "text_layout_create") if matches!(sdk_version, 3..=6) => 5,
            ("ming_v3", "text_layout_metrics") if matches!(sdk_version, 3..=6) => 3,
            ("ming_v3", "text_layout_close") if matches!(sdk_version, 3..=6) => 1,
            ("ming_v3", "frame_submit") if matches!(sdk_version, 3..=6) => 2,
            ("ming_v4", "frame_submit" | "shadow_style_create") if sdk_version >= 4 => 2,
            ("ming_v4", "shadow_style_close") if sdk_version >= 4 => 1,
            ("ming_frame_v1", "clock_monotonic_ns") if sdk_version >= 5 => 2,
            ("ming_frame_v1", "wait") if sdk_version >= 5 => 4,
            ("ming_input_v1", "poll" | "text_focus") if sdk_version >= 6 => 2,
            ("ming_data_v1", "read") if sdk_version >= 6 => 4,
            ("ming_data_v1", "write") if sdk_version >= 6 => 3,
            ("ming_data_v1", "size" | "delete") if sdk_version >= 6 => 1,
            _ => bail!("IMPORT_DENIED: {}.{}", import.module(), import.name()),
        };
        let ExternType::Func(ty) = import.ty() else {
            bail!("IMPORT_DENIED: function imports only");
        };
        if ty.params().len() != expected
            || !ty.params().all(|t| matches!(t, ValType::I32))
            || ty.results().len() != 1
            || !ty.results().all(|t| matches!(t, ValType::I32))
        {
            bail!("SIGNATURE_DENIED: {}", import.name());
        }
    }
    let entry = module
        .exports()
        .find(|e| e.name() == "_start")
        .context("ENTRYPOINT_INVALID: missing _start")?;
    let ExternType::Func(ty) = entry.ty() else {
        bail!("ENTRYPOINT_INVALID");
    };
    if ty.params().len() != 0 || ty.results().len() != 0 {
        bail!("ENTRYPOINT_INVALID: expected () -> ()");
    }
    Ok(())
}

fn execution_error(error: wasmtime::Error) -> anyhow::Error {
    let text = format!("{error:#}");
    let code = if matches!(error.downcast_ref::<Trap>(), Some(Trap::OutOfFuel)) {
        "FUEL_EXHAUSTED"
    } else if text.contains("resource limit") || (text.contains("memory") && text.contains("limit"))
    {
        "RESOURCE_LIMIT"
    } else {
        "APP_TRAP"
    };
    anyhow::anyhow!("{code}: {text}")
}

pub(crate) fn module_engine_config(portable_system_image: bool) -> Result<Config> {
    let mut config = Config::new();
    config.consume_fuel(true).wasm_gc(true).wasm_function_references(true)
        .wasm_exceptions(true).wasm_multi_memory(false).wasm_memory64(false)
        .max_wasm_stack(512 * 1024);
    if portable_system_image {
        config.cranelift_opt_level(wasmtime::OptLevel::Speed);
        config.target("x86_64-unknown-linux-gnu").map_err(anyhow::Error::from)
            .context("SYSTEM_MODULE_TARGET")?;
    }
    Ok(config)
}
/// Native trusted-image tooling only. Compiles and validates, never instantiates/runs Wasm.
pub fn precompile_system_module(bytes: &[u8], sdk_version: u32) -> Result<Vec<u8>> {
    if !cfg!(all(target_os="linux",target_arch="x86_64",target_env="gnu")) { bail!("SYSTEM_MODULE_TARGET"); }
    if !matches!(sdk_version,1..=6) { bail!("SYSTEM_MODULE_SDK_UNSUPPORTED"); }
    if bytes.len()>MAX_MODULE_BYTES || bytes.len()<8 || &bytes[..8]!=b"\0asm\x01\0\0\0" { bail!("MODULE_INVALID"); }
    let engine=Engine::new(&module_engine_config(true)?).map_err(anyhow::Error::from)?;
    let module=Module::new(&engine,bytes).map_err(anyhow::Error::from).context("MODULE_INVALID")?;
    validate_imports(&module,sdk_version)?;
    let native=module.serialize().map_err(anyhow::Error::from).context("SYSTEM_MODULE_SERIALIZE")?;
    if native.len()>64*1024*1024 { bail!("SYSTEM_MODULE_TOO_LARGE"); }
    Ok(native)
}

pub fn worker() -> Result<()> {
    let mut input = BufReader::new(io::stdin());
    let init: Init = read_json(&mut input, 8 * 1024 * 1024)?.context("PROTOCOL_INIT")?;
    if !matches!(init.sdk_version, 1..=6)
        || init.fuel == 0
        || !(65536..=268_435_456).contains(&init.memory_bytes)
    {
        bail!("INVALID_LIMIT");
    }
    // Trusted policy is applied before any guest bytes are decoded or JIT'd.
    let isolation_report = if let Some(policy) = &init.isolation {
        let policy = policy.to_native().context("PROTOCOL_ISOLATION_POLICY")?;
        Some(crate::protocol::IsolationReport::from_applied(unsafe {
            crate::linux_isolation::apply_child(&policy)?
        }))
    } else { None };
    if matches!(init.sdk_version, 3 | 4) && !cfg!(feature = "text") {
        bail!("TEXT_FEATURE_REQUIRED");
    }
    let bytes = STANDARD.decode(init.wasm).context("MODULE_INVALID")?;
    if bytes.len() > MAX_MODULE_BYTES || bytes.len() < 8 || &bytes[..8] != b"\0asm\x01\0\0\0" {
        bail!("MODULE_INVALID");
    }
    let system_module = crate::system_modules::lookup(init.sdk_version, &bytes);
    let config = module_engine_config(system_module.is_some())?;
    let engine = Engine::new(&config)
        .map_err(anyhow::Error::from)
        .context("ENGINE_CONFIG")?;
    let module = if let Some(trusted) = system_module {
        trusted.load(&engine)?
    } else {
        Module::new(&engine, bytes).map_err(anyhow::Error::from).context("MODULE_INVALID")?
    };
    validate_imports(&module, init.sdk_version)?;
    if init.sdk_version >= 5
        && !cfg!(feature = "text")
        && module
            .imports()
            .any(|i| matches!(i.module(), "ming_v3" | "ming_v4"))
    {
        bail!("TEXT_FEATURE_REQUIRED");
    }
    let limits = StoreLimitsBuilder::new()
        .memory_size(init.memory_bytes)
        .memories(1)
        .tables(4)
        .table_elements(16384)
        .instances(1)
        .build();
    let mut store = Store::new(
        &engine,
        State {
            entropy_bytes: 0,
            rpc: Rpc {
                input,
                output: io::stdout(),
                sequence: 0,
                fuel: crate::execution_budget::WorkerFuel::new(init.fuel, init.fuel_capacity)
                    .map_err(anyhow::Error::msg)?,
                remaining: None,
            },
            limits,
        },
    );
    store.limiter(|state| &mut state.limits);
    store.set_fuel(init.fuel)?;
    let mut linker = Linker::new(&engine);
    linker.func_wrap(
        "ming_v1",
        "file_open",
        |mut caller: Caller<'_, State>, id: i32| -> wasmtime::Result<i32> {
            caller
                .data_mut()
                .rpc
                .call("file_open", &[id])
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    linker.func_wrap(
        "ming_v1",
        "file_length",
        |mut caller: Caller<'_, State>, h: i32| -> wasmtime::Result<i32> {
            caller
                .data_mut()
                .rpc
                .call("file_length", &[h])
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    linker.func_wrap(
        "ming_v1",
        "file_close",
        |mut caller: Caller<'_, State>, h: i32| -> wasmtime::Result<i32> {
            caller
                .data_mut()
                .rpc
                .call("file_close", &[h])
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    linker.func_wrap(
        "ming_v1",
        "file_read_byte",
        |mut caller: Caller<'_, State>, h: i32, offset: i32| -> wasmtime::Result<i32> {
            caller
                .data_mut()
                .rpc
                .call("file_read_byte", &[h, offset])
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    linker.func_wrap(
        "ming_v1",
        "log",
        |mut caller: Caller<'_, State>, code: i32, value: i32| -> wasmtime::Result<i32> {
            caller
                .data_mut()
                .rpc
                .call("log", &[code, value])
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    if init.sdk_version >= 2 {
        register_v2(&mut linker)?;
    }
    if init.sdk_version >= 3 {
        register_v3(&mut linker)?;
    }
    if init.sdk_version >= 4 {
        register_v4(&mut linker)?;
    }
    if init.sdk_version >= 5 {
        register_frame_clock(&mut linker)?;
    }
    if init.sdk_version >= 6 {
        register_text_input(&mut linker)?;
        register_app_data(&mut linker)?;
    }
    let instance = linker
        .instantiate(&mut store, &module)
        .map_err(execution_error)?;
    let entry = instance
        .get_typed_func::<(), ()>(&mut store, "_start")
        .map_err(anyhow::Error::from)
        .context("ENTRYPOINT_INVALID")?;
    if init.fuel_capacity.is_some() || isolation_report.is_some() {
        write_json(&mut io::stdout(), &WorkerMessage::Ready { isolation: isolation_report })?;
    }
    entry.call(&mut store, ()).map_err(execution_error)?;
    let total = store.data().rpc.fuel.total;
    let consumed = total
        .checked_sub(store.get_fuel()?)
        .context("PROTOCOL_FUEL_CONSUMED")?;
    write_json(
        &mut io::stdout(),
        &WorkerMessage::Result {
            exit_code: 0,
            fuel_consumed: consumed,
            total_granted: init.fuel_capacity.map(|_| total),
            entropy_bytes: store.data().entropy_bytes,
        },
    )?;
    Ok(())
}

pub fn report_worker_error(error: &anyhow::Error) {
    let text = format!("{error:#}");
    let code = text
        .split(':')
        .next()
        .filter(|s| s.len() <= 40 && s.bytes().all(|b| b.is_ascii_uppercase() || b == b'_'))
        .unwrap_or("WORKER_FAILED");
    let message: String = text.chars().take(512).collect();
    let _ = write_json(
        &mut io::stdout(),
        &WorkerMessage::Error {
            code: code.to_owned(),
            message,
        },
    );
}

fn guest_range(
    caller: &mut Caller<'_, State>,
    ptr: i32,
    len: i32,
) -> Option<(wasmtime::Memory, usize)> {
    if ptr < 0 || len < 0 {
        return None;
    }
    let memory = caller.get_export("memory")?.into_memory()?;
    let start = ptr as usize;
    let end = start.checked_add(len as usize)?;
    if end > memory.data_size(&*caller) {
        return None;
    }
    Some((memory, start))
}
fn register_v2(linker: &mut Linker<State>) -> Result<()> {
    linker.func_wrap(
        "ming_v2",
        "random_fill",
        |mut c: Caller<'_, State>, ptr: i32, len: i32| -> wasmtime::Result<i32> {
            if !(0..=4096).contains(&len) {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut c, ptr, len) else {
                return Ok(-2);
            };
            if c.data().entropy_bytes + len as usize > 1048576 {
                return Ok(-4);
            }
            let mut bytes = vec![0; len as usize];
            if getrandom::fill(&mut bytes).is_err() {
                return Ok(-3);
            }
            memory.write(&mut c, start, &bytes)?;
            c.data_mut().entropy_bytes += bytes.len();
            Ok(0)
        },
    )?;
    for method in ["image_open", "image_width", "image_height", "image_close"] {
        linker.func_wrap(
            "ming_v2",
            method,
            move |mut c: Caller<'_, State>, h: i32| -> wasmtime::Result<i32> {
                c.data_mut()
                    .rpc
                    .call(method, &[h])
                    .map_err(wasmtime::Error::from_anyhow)
            },
        )?;
    }
    linker.func_wrap(
        "ming_v2",
        "file_read",
        |mut c: Caller<'_, State>,
         h: i32,
         offset: i32,
         ptr: i32,
         len: i32|
         -> wasmtime::Result<i32> {
            if !(0..=65536).contains(&len) {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut c, ptr, len) else {
                return Ok(-2);
            };
            let reply = c
                .data_mut()
                .rpc
                .exchange("file_read", &[h, offset, len], None)
                .map_err(wasmtime::Error::from_anyhow)?;
            if reply.value < 0 {
                if reply.payload.is_some() {
                    return Err(wasmtime::Error::msg("PROTOCOL_READ"));
                }
                return Ok(reply.value);
            }
            let bytes = reply
                .payload
                .and_then(|s| STANDARD.decode(s).ok())
                .filter(|b| b.len() == reply.value as usize && b.len() <= len as usize)
                .ok_or_else(|| wasmtime::Error::msg("PROTOCOL_READ"))?;
            memory.write(&mut c, start, &bytes)?;
            Ok(reply.value)
        },
    )?;
    linker.func_wrap(
        "ming_v2",
        "frame_submit",
        |mut c: Caller<'_, State>, ptr: i32, len: i32| -> wasmtime::Result<i32> {
            if !(0..=65536).contains(&len) {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut c, ptr, len) else {
                return Ok(-2);
            };
            let bytes = memory.data(&c)[start..start + len as usize].to_vec();
            c.data_mut()
                .rpc
                .call_with_payload("frame_submit", STANDARD.encode(bytes))
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    linker.func_wrap(
        "ming_v2",
        "event_next",
        |mut c: Caller<'_, State>, ptr: i32, capacity: i32| -> wasmtime::Result<i32> {
            if capacity < 16 {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut c, ptr, 16) else {
                return Ok(-2);
            };
            let reply = cooperative_exchange(&mut c, "event_next", None)?;
            let bytes = reply
                .payload
                .and_then(|s| STANDARD.decode(s).ok())
                .filter(|b| b.len() == 16 && reply.value == 16)
                .ok_or_else(|| wasmtime::Error::msg("PROTOCOL_EVENT"))?;
            memory.write(&mut c, start, &bytes)?;
            Ok(16)
        },
    )?;
    Ok(())
}
impl Rpc {
    fn call_with_payload(&mut self, method: &str, payload: String) -> Result<i32> {
        let r = self.exchange(method, &[], Some(payload))?;
        if r.payload.is_some() {
            bail!("PROTOCOL_UNEXPECTED_PAYLOAD");
        }
        Ok(r.value)
    }
}

fn register_v3(linker: &mut Linker<State>) -> Result<()> {
    linker.func_wrap(
        "ming_v3",
        "text_layout_create",
        |mut c: Caller<'_, State>,
         ptr: i32,
         len: i32,
         font: i32,
         size: i32,
         flags: i32|
         -> wasmtime::Result<i32> {
            if !(0..=512).contains(&len) {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut c, ptr, len) else {
                return Ok(-2);
            };
            let bytes = memory.data(&c)[start..start + len as usize].to_vec();
            let reply = c
                .data_mut()
                .rpc
                .exchange(
                    "text_layout_create",
                    &[font, size, flags],
                    Some(STANDARD.encode(bytes)),
                )
                .map_err(wasmtime::Error::from_anyhow)?;
            if reply.payload.is_some() {
                return Err(wasmtime::Error::msg("PROTOCOL_TEXT_CREATE"));
            }
            Ok(reply.value)
        },
    )?;
    linker.func_wrap(
        "ming_v3",
        "text_layout_metrics",
        |mut c: Caller<'_, State>, handle: i32, ptr: i32, capacity: i32| -> wasmtime::Result<i32> {
            if !(48..=65536).contains(&capacity) {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut c, ptr, 48) else {
                return Ok(-2);
            };
            let reply = c
                .data_mut()
                .rpc
                .exchange("text_layout_metrics", &[handle], None)
                .map_err(wasmtime::Error::from_anyhow)?;
            if reply.value < 0 {
                if reply.payload.is_some() {
                    return Err(wasmtime::Error::msg("PROTOCOL_TEXT_METRICS"));
                }
                return Ok(reply.value);
            }
            let bytes = reply
                .payload
                .and_then(|s| STANDARD.decode(s).ok())
                .filter(|b| reply.value == 48 && b.len() == 48)
                .ok_or_else(|| wasmtime::Error::msg("PROTOCOL_TEXT_METRICS"))?;
            memory.write(&mut c, start, &bytes)?;
            Ok(48)
        },
    )?;
    linker.func_wrap(
        "ming_v3",
        "text_layout_close",
        |mut c: Caller<'_, State>, h: i32| -> wasmtime::Result<i32> {
            c.data_mut()
                .rpc
                .call("text_layout_close", &[h])
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    linker.func_wrap(
        "ming_v3",
        "frame_submit",
        |mut c: Caller<'_, State>, ptr: i32, len: i32| -> wasmtime::Result<i32> {
            if !(0..=65536).contains(&len) {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut c, ptr, len) else {
                return Ok(-2);
            };
            let bytes = memory.data(&c)[start..start + len as usize].to_vec();
            c.data_mut()
                .rpc
                .call_with_payload("frame_submit", STANDARD.encode(bytes))
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    Ok(())
}

fn register_v4(linker: &mut Linker<State>) -> Result<()> {
    linker.func_wrap(
        "ming_v4",
        "shadow_style_create",
        |mut c: Caller<'_, State>, radius: i32, blur: i32| -> wasmtime::Result<i32> {
            c.data_mut()
                .rpc
                .call("shadow_style_create", &[radius, blur])
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    linker.func_wrap(
        "ming_v4",
        "shadow_style_close",
        |mut c: Caller<'_, State>, handle: i32| -> wasmtime::Result<i32> {
            c.data_mut()
                .rpc
                .call("shadow_style_close", &[handle])
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    linker.func_wrap(
        "ming_v4",
        "frame_submit",
        |mut c: Caller<'_, State>, ptr: i32, len: i32| -> wasmtime::Result<i32> {
            if !(0..=65536).contains(&len) {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut c, ptr, len) else {
                return Ok(-2);
            };
            let bytes = memory.data(&c)[start..start + len as usize].to_vec();
            c.data_mut()
                .rpc
                .call_with_payload("frame_submit_v4", STANDARD.encode(bytes))
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    Ok(())
}

fn frame_reply(
    caller: &mut Caller<'_, State>,
    memory: wasmtime::Memory,
    start: usize,
    reply: Reply,
    expected: usize,
    pending_allowed: bool,
) -> wasmtime::Result<i32> {
    if reply.value < 0 || (reply.value == 0 && pending_allowed) {
        if reply.payload.is_some() {
            return Err(wasmtime::Error::msg("PROTOCOL_FRAME_CLOCK"));
        }
        return Ok(reply.value);
    }
    let bytes = reply
        .payload
        .and_then(|p| STANDARD.decode(p).ok())
        .filter(|b| reply.value == expected as i32 && b.len() == expected)
        .ok_or_else(|| wasmtime::Error::msg("PROTOCOL_FRAME_CLOCK"))?;
    memory.write(caller, start, &bytes)?;
    Ok(expected as i32)
}
fn register_frame_clock(linker: &mut Linker<State>) -> Result<()> {
    linker.func_wrap(
        "ming_frame_v1",
        "clock_monotonic_ns",
        |mut caller: Caller<'_, State>, ptr: i32, capacity: i32| -> wasmtime::Result<i32> {
            if capacity != 8 {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut caller, ptr, 8) else {
                return Ok(-2);
            };
            let reply = caller
                .data_mut()
                .rpc
                .exchange("frame_clock_read", &[], None)
                .map_err(wasmtime::Error::from_anyhow)?;
            frame_reply(&mut caller, memory, start, reply, 8, false)
        },
    )?;
    linker.func_wrap(
        "ming_frame_v1",
        "wait",
        |mut caller: Caller<'_, State>,
         request_ptr: i32,
         request_length: i32,
         event_ptr: i32,
         event_capacity: i32|
         -> wasmtime::Result<i32> {
            if request_length != 16 || event_capacity != 48 {
                return Ok(-2);
            }
            let Some((request_memory, request_start)) = guest_range(&mut caller, request_ptr, 16)
            else {
                return Ok(-2);
            };
            let Some((event_memory, event_start)) = guest_range(&mut caller, event_ptr, 48) else {
                return Ok(-2);
            };
            // Copy first: the two valid Wasm ranges may overlap.
            let request = request_memory.data(&caller)[request_start..request_start + 16].to_vec();
            if crate::frame_clock::WaitRequest::decode(&request).is_err() {
                return Ok(-2);
            }
            let decoded =
                crate::frame_clock::WaitRequest::decode(&request).expect("validated request");
            let reply = if !decoded.nonblocking {
                cooperative_exchange(
                    &mut caller,
                    "frame_clock_wait",
                    Some(STANDARD.encode(request)),
                )?
            } else {
                caller
                    .data_mut()
                    .rpc
                    .exchange("frame_clock_wait", &[], Some(STANDARD.encode(request)))
                    .map_err(wasmtime::Error::from_anyhow)?
            };
            frame_reply(&mut caller, event_memory, event_start, reply, 48, true)
        },
    )?;
    Ok(())
}

// Only validated SDK waits may report fuel and apply host credit. Ordinary RPCs
// (including log and clock reads) reject any credit in Rpc::exchange.
fn cooperative_exchange(
    caller: &mut Caller<'_, State>,
    method: &str,
    payload: Option<String>,
) -> wasmtime::Result<Reply> {
    let remaining = caller.get_fuel()?;
    if caller.data().rpc.fuel.cap.is_some() {
        caller.data_mut().rpc.remaining = Some(remaining);
    }
    let reply = caller
        .data_mut()
        .rpc
        .exchange(method, &[], payload)
        .map_err(wasmtime::Error::from_anyhow)?;
    let next = caller
        .data_mut()
        .rpc
        .fuel
        .apply(remaining, reply.fuel_grant)
        .map_err(wasmtime::Error::msg)?;
    if caller.data().rpc.fuel.cap.is_some() {
        caller.set_fuel(next)?;
    }
    Ok(reply)
}

fn register_text_input(linker: &mut Linker<State>) -> Result<()> {
    linker.func_wrap(
        "ming_input_v1",
        "poll",
        |mut caller: Caller<'_, State>, ptr: i32, capacity: i32| -> wasmtime::Result<i32> {
            if capacity != 568 {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut caller, ptr, capacity) else {
                return Ok(-2);
            };
            let reply = caller
                .data_mut()
                .rpc
                .exchange("input_poll", &[], None)
                .map_err(wasmtime::Error::from_anyhow)?;
            if reply.value <= 0 {
                if reply.payload.is_some() {
                    return Err(wasmtime::Error::msg("PROTOCOL_TEXT_INPUT"));
                }
                return Ok(reply.value);
            }
            let bytes = reply
                .payload
                .and_then(|p| STANDARD.decode(p).ok())
                .filter(|b| b.len() == reply.value as usize && (56..=568).contains(&b.len()))
                .ok_or_else(|| wasmtime::Error::msg("PROTOCOL_TEXT_INPUT"))?;
            crate::text_input::Packet::decode(&bytes)
                .map_err(|_| wasmtime::Error::msg("PROTOCOL_TEXT_INPUT"))?;
            memory.write(&mut caller, start, &bytes)?;
            Ok(reply.value)
        },
    )?;
    linker.func_wrap(
        "ming_input_v1",
        "text_focus",
        |mut caller: Caller<'_, State>, ptr: i32, length: i32| -> wasmtime::Result<i32> {
            if length != 24 {
                return Ok(-2);
            }
            let Some((memory, start)) = guest_range(&mut caller, ptr, length) else {
                return Ok(-2);
            };
            let request = memory.data(&caller)[start..start + 24].to_vec();
            if crate::text_input::FocusRequest::decode(&request).is_err() {
                return Ok(-2);
            }
            caller
                .data_mut()
                .rpc
                .call_with_payload("input_text_focus", STANDARD.encode(request))
                .map_err(wasmtime::Error::from_anyhow)
        },
    )?;
    Ok(())
}

// No cooperative exchange here: storage requests may not receive or apply fuel credit.
fn register_app_data(linker: &mut Linker<State>) -> Result<()> {
    linker.func_wrap("ming_data_v1", "read", |mut caller: Caller<'_, State>, slot: i32, offset: i32, ptr: i32, capacity: i32| -> wasmtime::Result<i32> {
        let Some(memory) = caller.get_export("memory").and_then(|e| e.into_memory()) else { return Ok(-2); };
        let Ok(range) = crate::app_data::validate_read(slot, offset, ptr, capacity, memory.data_size(&caller)) else { return Ok(-2); };
        let reply = caller.data_mut().rpc.exchange("app_data_read", &[slot, offset, capacity], None).map_err(wasmtime::Error::from_anyhow)?;
        if reply.value <= 0 {
            if reply.payload.is_some() { return Err(wasmtime::Error::msg("PROTOCOL_APP_DATA_READ")); }
            return Ok(reply.value);
        }
        if reply.value > capacity { return Err(wasmtime::Error::msg("PROTOCOL_APP_DATA_READ")); }
        let bytes = reply.payload.filter(|p| p.len() <= 87384).and_then(|p| STANDARD.decode(p).ok())
            .filter(|b| b.len() == reply.value as usize).ok_or_else(|| wasmtime::Error::msg("PROTOCOL_APP_DATA_READ"))?;
        memory.write(&mut caller, range.start, &bytes)?;
        Ok(reply.value)
    })?;
    linker.func_wrap("ming_data_v1", "write", |mut caller: Caller<'_, State>, slot: i32, ptr: i32, length: i32| -> wasmtime::Result<i32> {
        let Some(memory) = caller.get_export("memory").and_then(|e| e.into_memory()) else { return Ok(-2); };
        let Ok(range) = crate::app_data::validate_write(slot, ptr, length, memory.data_size(&caller)) else { return Ok(-2); };
        let bytes = memory.data(&caller)[range].to_vec();
        let reply = caller.data_mut().rpc.exchange("app_data_write", &[slot, length], Some(STANDARD.encode(bytes))).map_err(wasmtime::Error::from_anyhow)?;
        if reply.payload.is_some() || (reply.value >= 0 && reply.value != length) { return Err(wasmtime::Error::msg("PROTOCOL_APP_DATA_WRITE")); }
        Ok(reply.value)
    })?;
    for method in ["size", "delete"] {
        linker.func_wrap("ming_data_v1", method, move |mut caller: Caller<'_, State>, slot: i32| -> wasmtime::Result<i32> {
            if !(1..=8).contains(&slot) { return Ok(-2); }
            let name = if method == "size" { "app_data_size" } else { "app_data_delete" };
            let value = caller.data_mut().rpc.call(name, &[slot]).map_err(wasmtime::Error::from_anyhow)?;
            if (method == "size" && value > crate::app_data::MAX_SLOT_BYTES as i32) || (method == "delete" && value > 0) { return Err(wasmtime::Error::msg("PROTOCOL_APP_DATA_SCALAR")); }
            Ok(value)
        })?;
    }
    Ok(())
}
