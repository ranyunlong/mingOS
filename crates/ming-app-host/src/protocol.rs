use anyhow::{Context, Result, bail};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::io::{BufRead, BufWriter, Read, Write};

pub fn read_json<T: DeserializeOwned>(
    reader: &mut impl BufRead,
    limit: usize,
) -> Result<Option<T>> {
    let mut bytes = Vec::new();
    reader
        .take((limit + 1) as u64)
        .read_until(b'\n', &mut bytes)?;
    if bytes.len() > limit {
        bail!("PROTOCOL_LIMIT");
    }
    if bytes.is_empty() {
        return Ok(None);
    }
    if bytes.last() != Some(&b'\n') {
        bail!("PROTOCOL_TRUNCATED");
    }
    Ok(Some(
        serde_json::from_slice(&bytes).context("PROTOCOL_INVALID")?,
    ))
}

pub fn write_json(writer: &mut impl Write, value: &impl Serialize) -> Result<()> {
    // Coalesce serializer fragments before touching an unbuffered pipe, while
    // bounding temporary storage and flushing every complete protocol line.
    let mut buffered = BufWriter::with_capacity(8192, writer);
    serde_json::to_writer(&mut buffered, value)?;
    buffered.write_all(b"\n")?;
    buffered.flush()?;
    Ok(())
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Init {
    pub wasm: String,
    pub fuel: u64,
    pub memory_bytes: usize,
    #[serde(default = "legacy_sdk")]
    pub sdk_version: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fuel_capacity: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_requests: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub isolation: Option<IsolationPolicy>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Reply {
    pub id: u64,
    pub value: i32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payload: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fuel_grant: Option<u64>,
}
#[derive(Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum WorkerMessage {
    /// Persistent worker has completed instantiation; never emitted in bounded mode.
    Ready {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        isolation: Option<IsolationReport>,
    },
    Call {
        id: u64,
        method: String,
        args: Vec<i32>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        payload: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        remaining_fuel: Option<u64>,
    },
    Result {
        #[serde(rename = "exitCode")]
        exit_code: i32,
        #[serde(rename = "fuelConsumed")]
        fuel_consumed: u64,
        #[serde(default, rename = "totalGranted", skip_serializing_if = "Option::is_none")]
        total_granted: Option<u64>,
        #[serde(default, rename = "entropyBytes")]
        entropy_bytes: usize,
    },
    Error {
        code: String,
        message: String,
    },
}

fn legacy_sdk() -> u32 {
    1
}

/// Private trusted parent policy. Never sourced from a guest RPC or package.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all="camelCase", deny_unknown_fields)]
pub struct IsolationPolicy {
    pub app:u16, pub generation:u64, pub uid:u32, pub gid:u32,
    pub expected_parent:u32, pub worker_pid:u32, pub memory_bytes:u64, pub pids:u32,
}
impl IsolationPolicy {
    pub fn to_native(&self)->Result<crate::linux_isolation::ChildPolicy> {
        use crate::linux_isolation::{WorkerKey,Limits,ChildPolicy};
        Ok(ChildPolicy::new(WorkerKey::new(self.app,self.generation)?,self.uid,self.gid,self.expected_parent,self.worker_pid,Limits::new(self.memory_bytes,self.pids)?)?)
    }
    pub fn from_native(p:&crate::linux_isolation::ChildPolicy)->Self {
        Self{app:p.key().app(),generation:p.key().generation(),uid:p.uid(),gid:p.gid(),expected_parent:p.expected_parent(),worker_pid:p.worker_pid(),memory_bytes:p.limits().memory_bytes(),pids:p.limits().pids()}
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all="camelCase", deny_unknown_fields)]
pub struct IsolationReport {
    pub uid:u32,pub gid:u32,pub pid:u32,pub parent:u32,
    pub pid_namespace:bool,pub namespace_ids:[u64;4],
}
impl IsolationReport {
    pub fn from_applied(p:crate::linux_isolation::Applied)->Self {Self{uid:p.uid,gid:p.gid,pid:p.pid,parent:p.parent,pid_namespace:p.pid_namespace,namespace_ids:p.namespace_ids}}
    pub fn validate_for(&self,p:&IsolationPolicy)->Result<()> {
        p.to_native()?;
        if self.uid!=p.uid||self.gid!=p.gid||self.pid!=p.worker_pid||self.parent!=p.expected_parent||self.pid_namespace||self.namespace_ids.contains(&0) {
            bail!("PROTOCOL_ISOLATION_REPORT");
        }
        Ok(())
    }
}
