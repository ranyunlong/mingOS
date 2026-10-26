# Rust App Host 迁移设计

日期：2026-10-23。用户已授权继续后续任务；本阶段落实 ADR 0001 中进入图形阶段前验证 Rust 宿主的迁移条件。

## 范围与成功条件

交付独立 Rust CLI/worker，不经 Node 执行应用；运行第一阶段已经编译的同一个 Kotlin Wasm 文件，保持 ming_v1 ABI、授权语义和诊断事件。通过拒绝未授权资源、非法导入/签名、陷阱、死循环、请求限额与线性内存上限测试。Node 原型保留为交叉验证参考。

本阶段不实现图形、合成器、Linux 内核沙箱或签名安装。父进程被 OS 强杀的托管、RSS/GC 总内存硬限制及 engine 漏洞隔离仍需后续操作系统级机制。

## 执行与信任边界

父进程验证清单、以 no-follow/nonblocking（可用时）方式读取普通文件并持有资源授权。worker 是同一个 Rust 可执行文件的内部入口，清理环境后只通过 stdin/stdout 交换有限 JSON；没有 WASI linker。AppID 只作包标识，未认证发布者，同名 ID 不共享授权。

worker 使用 Wasmtime 编译和实例化，先校验导入名字、模块、函数参数/返回签名与 _start 签名；仅链接 ming_v1 五个函数。支持实际 Kotlin 产物所需的 Wasm GC/异常特性必须实测。若引擎不支持，记录失败并评估编译选项，不偷换示例成手写 Wasm。

父进程负责 wall-clock 超时和请求预算；worker 提供 Wasmtime fuel 执行预算与 StoreLimits 线性内存/表上限。fuel 不是毫秒配额，线性内存上限不是 RSS 或 GC 总内存上限；编译时间由父进程截止期限覆盖。

## 代码边界

- package.rs：清单、包路径和有界普通文件读取。
- broker.rs：授权文件、运行实例句柄、日志与整数错误码。
- protocol.rs：有界消息、内部请求/响应结构。
- engine.rs：Wasmtime 配置、导入验证与 SDK 函数链接。
- supervisor.rs：worker 启动、超时、协议校验与回收。
- cli.rs/main.rs：命令参数、JSON 输出和内部 worker 入口。
- tests：Rust 单元/真实子进程攻击测试、真实 Kotlin 跨宿主集成。

工具链和依赖固定在 rust-toolchain.toml、Cargo.toml/Cargo.lock。当前机器的工具链、包缓存与产物均置于 .cache；不修改全局 PATH，不依赖已有的全局 Rust 工具链。

## CLI

ming-app-host run <app.json> [--grant <id>=<path>] [--timeout-ms <n>] [--max-requests <n>] [--fuel <n>] [--memory-bytes <n>]

输出兼容 appId/events/exitCode/requests，额外注明 runtime 和 durationMs。失败使用明确错误码并以非零退出；内部 worker 协议不作为第三方应用 API。

## 验证

先写 Rust 行为测试并观察失败，再实现。Rust CLI 实际运行 Kotlin 产物，检查 UTF-8 内容、未授权返回值与无授权行为；Node 原型相同测试回归通过。Linux 专属 FIFO/符号链接检查在可用 Linux 环境前只记录为未验证，不以 Windows 结果替代。
