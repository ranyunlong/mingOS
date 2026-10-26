use anyhow::{Result, bail};
use std::sync::{Arc, Mutex};

// First terminal backend error wins, including errors raised on driver threads.
#[derive(Clone, Default)]
pub(crate) struct BackendStatus(Arc<Mutex<Option<String>>>);
impl BackendStatus {
    #[cfg(any(test, feature = "gpu"))]
    pub(crate) fn record(&self, message: String) {
        let mut failure = self.0.lock().unwrap_or_else(|poison| poison.into_inner());
        if failure.is_none() {
            *failure = Some(message.chars().take(4096).collect());
        }
    }
    pub(crate) fn check(&self) -> Result<()> {
        let failure = self.0.lock().unwrap_or_else(|poison| poison.into_inner());
        if let Some(message) = &*failure {
            bail!("RENDERER_FAILED: {message}");
        }
        Ok(())
    }
}
