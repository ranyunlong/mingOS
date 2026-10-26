# Rust App Host Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. 用户授权在当前会话继续；不再重复询问是否实施。仓库未初始化 Git，不创建分支、不提交、不推送。

**Goal:** Rust 宿主运行现有 Kotlin Wasm，验证 SDK 能力边界与故障/资源处理。

**Architecture:** Rust 父进程管理清单、资源和截止期限，同一可执行文件的 worker 使用 Wasmtime，只链接明确 SDK 导入。协议通过有界匿名管道传递。

**Tech Stack:** Rust 1.99.1、Wasmtime 50.0.0、serde/serde_json/base64、现有 Kotlin 2.3.20 产物。

**Spec:** ../specs/2026-10-23-rust-app-host-design.md

## Global Constraints

- Kotlin 源码和 ming_v1 ABI 保持兼容，实际运行已有编译产物。
- 不添加 WASI；所有函数导入须匹配 i32 参数/结果，_start 为 () -> ()。
- 私有启动配置提供授权，包不能自行授予权限。
- 线性内存预算、fuel 与 wall-clock 分开配置，不声称生产隔离。
- 工具链缓存位于 .cache；依赖用 Cargo.lock 固定；不更改全局 PATH。

## Task 1: 基础与权限服务

Files: Cargo.toml, rust-toolchain.toml, crates/ming-app-host/{Cargo.toml,src/lib.rs,src/package.rs,src/broker.rs}, crates/ming-app-host/tests/broker.rs。

Interfaces: Broker::new(&BTreeMap<u16, PathBuf>) -> Result<Broker>；Broker::dispatch(&str, &[i32]) -> i32；load_package(&Path) -> Result<Package>。

- [x] 安装项目局部工具链并固定版本，写入独立 workspace。
- [x] 写授权、无效句柄、限额与非普通文件测试；建立最小接口并运行，观察失败。
- [x] 实现 broker 与有界包加载，运行对应测试通过。

测试核心：

~~~rust
assert_eq!(broker.dispatch("file_open", &[2]), -1);
assert_eq!(broker.dispatch("file_length", &[0]), -2);
~~~

## Task 2: 引擎与独立进程

Files: src/{engine,protocol,supervisor,cli,main}.rs, tests/host.rs。

Interfaces: run(Options) -> Result<Outcome>；worker() -> Result<()>；validate_imports(&Module) -> Result<()>。

- [x] 写恶意导入、错误签名、陷阱、死循环和内存增长测试，确认缺失行为失败。
- [x] 实现 engine 导入白名单、typed func、fuel 与 StoreLimits。
- [x] 实现 supervisor 的有界通信、wall-clock watchdog、请求计数、退出确认与 finally 清理。
- [x] 实现 CLI，并运行真实子进程测试。

测试核心：

~~~rust
assert!(run_app(loop_module, limits).unwrap_err().to_string().contains("FUEL_EXHAUSTED"));
assert!(run_app(wasi_module, limits).unwrap_err().to_string().contains("IMPORT_DENIED"));
~~~

## Task 3: 真实 Kotlin 与交叉验收

Files: tests/kotlin.rs, scripts/native-demo.mjs, README.md, docs/native-host.md。

- [x] 先写使用现有 Kotlin 产物的集成测试，确认尚未接通时失败。
- [x] 运行 Rust CLI，验证 UTF-8、DENIED、无授权结果，记录同一 Wasm 摘要。
- [x] 运行现有 Node 测试，记录跨宿主一致性；不给未执行的 Linux 检查标绿。

## Task 4: 收尾

- [x] cargo fmt --check、cargo test --locked、cargo clippy --locked --all-targets -- -D warnings。
- [x] 独立代码评审并修复重要问题；补充运行说明、验证数据和 ADR 状态。
- [x] 标记实际完成范围、剩余图形与 Linux 任务，不把开发宿主当作完整 OS。

## 完成记录

2026-10-23：四项任务均完成。真实 Kotlin 产物在原生宿主运行；12 项 Rust 测试、格式、Clippy、跨宿主对照和 Node 回归已执行。完整结果与平台限制见 ../../native-verification.md。没有 Git 仓库，未提交或推送。
