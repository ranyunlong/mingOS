# ADR 0002：验证 Rust/Wasmtime 原生宿主

日期：2026-10-23。状态：开发宿主迁移验收通过，生产系统隔离仍待实现。

继承 ADR 0001 的 SDK ABI 和能力模型，把宿主从 Node 原型迁移为 Rust 可执行文件；Node 保留作参考与测试工具。选择 Wasmtime 50.0.0 作为第一候选，必须实际验证 Kotlin 2.3.20 产物的 GC/异常特性、授权、故障和资源处理。

即使成功，此举也不等于 Linux 内核沙箱完成。OS 隔离、硬件、GUI 和应用运行时的长期选择仍需后续验证。当前开发机为 Windows，工具链安装只写项目 .cache。

依据：
- https://docs.wasmtime.dev/api/wasmtime/struct.Config.html
- https://docs.wasmtime.dev/api/wasmtime/struct.StoreLimitsBuilder.html
- https://docs.wasmtime.dev/security.html
- https://www.rust-lang.org/tools/install

## 验证结果

同一份 Kotlin 2.3.20 Wasm（7632 字节）在 Rust/Wasmtime 50.0.0 中实际运行成功，无需修改 Kotlin 源码或 ming_v1 ABI。原生/参考宿主的 appId、events、exitCode、requests 完全一致。12 项 Rust 测试通过，Clippy 零警告检查通过。详见 ../native-verification.md。

Wasmtime 50 使用自己的 Error/Result；接入时显式转换 anyhow 错误。线程能力未编译进引擎，不调用该 feature 才提供的配置方法。
