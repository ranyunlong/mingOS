//! Trusted system-image modules; no runtime native-byte constructor or file loader.
use anyhow::{Context,Result};
use wasmtime::{Engine,Module};

/// Only the trusted generated table in this module can create runtime entries.
/// native_code must be unmodified Module::serialize output for source + sdk_version.
pub(crate) struct SystemModule {
    sdk_version: u32,
    source: &'static [u8],
    native_code: &'static [u8],
}
impl SystemModule {
    pub(crate) fn load(&self, engine: &Engine) -> Result<Module> {
        // SAFETY: entries exist only in the trusted Host build's generated static table.
        // Its data is the exact compiler output, frozen with all other program inputs.
        // The generated source/native pair is compiled from the same read buffer and SDK.
        // No Init, package, AppData or other runtime input can construct native_code.
        unsafe { Module::deserialize(engine, self.native_code) }
            .map_err(anyhow::Error::from).context("SYSTEM_MODULE_INVALID")
    }
}
include!("system_module_table.rs");
fn find_in<'a>(entries: &'a [SystemModule], sdk: u32, source: &[u8]) -> Option<&'a SystemModule> {
    entries.iter().find(|entry| entry.sdk_version == sdk && entry.source == source)
}
pub(crate) fn lookup(sdk: u32, source: &[u8]) -> Option<&'static SystemModule> {
    if cfg!(all(target_os="linux",target_arch="x86_64",target_env="gnu")) { find_in(SYSTEM_MODULES,sdk,source) } else { None }
}
#[cfg(all(test, target_os="linux", target_arch="x86_64", target_env="gnu"))]
mod tests {
    use super::*;
    use wasmtime::{Engine,Linker,Store,Trap};
    fn trusted_image() -> SystemModule {
        let raw=wat::parse_str(r#"(module (global $n (mut i32) (i32.const 0)) (func (export "_start")) (func (export "bump") (result i32) global.get $n i32.const 1 i32.add global.set $n global.get $n) (func (export "spin") (loop br 0)))"#).unwrap();
        let native=crate::engine::precompile_system_module(&raw,6).unwrap();
        // Test entries obey the same invariant: exact fresh trusted compiler output.
        SystemModule { sdk_version:6, source:Box::leak(raw.into_boxed_slice()), native_code:Box::leak(native.into_boxed_slice()) }
    }
    fn engine() -> Engine {Engine::new(&crate::engine::module_engine_config(true).unwrap()).unwrap()}
    #[test] fn empty_registry_has_no_native_fallback() {
        assert!(find_in(&[],6,b"arbitrary source").is_none());
    }
    #[test] fn lookup_requires_exact_source_and_sdk() {
        let image=trusted_image();let source=image.source;let entries=[image];
        assert!(find_in(&entries,6,source).is_some());assert!(find_in(&entries,5,source).is_none());
        let mut changed=source.to_vec();*changed.last_mut().unwrap()^=1;assert!(find_in(&entries,6,&changed).is_none());
    }
    #[test] fn restored_module_keeps_each_store_state_independent() {
        let image=trusted_image();let e=engine();let module=image.load(&e).unwrap();let linker=Linker::<()>::new(&e);
        let mut first=Store::new(&e,());first.set_fuel(1000).unwrap();let a=linker.instantiate(&mut first,&module).unwrap();let bump=a.get_typed_func::<(),i32>(&mut first,"bump").unwrap();assert_eq!(bump.call(&mut first,()).unwrap(),1);assert_eq!(bump.call(&mut first,()).unwrap(),2);
        let mut second=Store::new(&e,());second.set_fuel(1000).unwrap();let b=linker.instantiate(&mut second,&module).unwrap();assert_eq!(b.get_typed_func::<(),i32>(&mut second,"bump").unwrap().call(&mut second,()).unwrap(),1);
    }
    #[test] fn restored_code_still_exhausts_finite_fuel() {
        let image=trusted_image();let e=engine();let module=image.load(&e).unwrap();let mut store=Store::new(&e,());store.set_fuel(1000).unwrap();let instance=Linker::<()>::new(&e).instantiate(&mut store,&module).unwrap();let error=instance.get_typed_func::<(),()>(&mut store,"spin").unwrap().call(&mut store,()).unwrap_err();assert!(matches!(error.downcast_ref::<Trap>(),Some(Trap::OutOfFuel)));
    }
    #[test] fn valid_compiler_output_with_wrong_configuration_fails_closed() {
        let image=trusted_image();let mut config=crate::engine::module_engine_config(true).unwrap();config.consume_fuel(false);let wrong=Engine::new(&config).unwrap();assert!(image.load(&wrong).is_err());
    }
    #[test] fn precompiler_rejects_unknown_imports_and_sdk() {
        let wasi=wat::parse_str(r#"(module (import "wasi_snapshot_preview1" "fd_write" (func)) (func (export "_start")))"#).unwrap();assert!(crate::engine::precompile_system_module(&wasi,6).is_err());
        let bare=wat::parse_str(r#"(module (func (export "_start")))"#).unwrap();assert!(crate::engine::precompile_system_module(&bare,7).is_err());
        let wrong=wat::parse_str(r#"(module (import "ming_v1" "file_open" (func (param i64) (result i32))) (func (export "_start")))"#).unwrap();assert!(crate::engine::precompile_system_module(&wrong,6).is_err());
    }
}
