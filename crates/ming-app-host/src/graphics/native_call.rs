use super::backend::BackendStatus;
use anyhow::Result;
use std::panic::{AssertUnwindSafe, catch_unwind};

pub(super) fn call<T>(status: &BackendStatus, operation: impl FnOnce() -> T) -> Result<T> {
    status.check()?;
    cleanup(status, operation)
}
// Cleanup must still run after a terminal error; catch a second backend panic.
pub(super) fn cleanup<T>(status: &BackendStatus, operation: impl FnOnce() -> T) -> Result<T> {
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(value) => {
            status.check()?;
            Ok(value)
        }
        Err(payload) => {
            let message = payload
                .downcast_ref::<String>()
                .map(String::as_str)
                .or_else(|| payload.downcast_ref::<&str>().copied())
                .unwrap_or("non-string backend panic");
            status.record(format!("GPU_NATIVE_PANIC: {message}"));
            Err(status.check().expect_err("latched native failure"))
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fatal_native_call_latches_error_and_rejects_later_work() {
        let status = BackendStatus::default();
        let released = std::cell::Cell::new(false);
        struct Resource<'a>(&'a std::cell::Cell<bool>);
        impl Drop for Resource<'_> {
            fn drop(&mut self) {
                self.0.set(true);
            }
        }
        let error = call(&status, || {
            let _resource = Resource(&released);
            panic!("injected native presentation failure")
        })
        .unwrap_err()
        .to_string();
        assert!(released.get());
        assert!(error.contains("GPU_NATIVE_PANIC"));
        assert!(error.contains("injected native"));
        let mut called = false;
        assert!(
            call(&status, || {
                called = true;
            })
            .is_err()
        );
        assert!(!called);
        let mut cleaned = false;
        assert!(
            cleanup(&status, || {
                cleaned = true;
                panic!("injected discard failure");
            })
            .is_err()
        );
        assert!(cleaned);
        status.record("secondary loss".into());
        assert_eq!(status.check().unwrap_err().to_string(), error);
    }
}
