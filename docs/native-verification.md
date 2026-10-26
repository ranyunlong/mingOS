# Rust 宿主验证记录

后续 SDK v2/图形阶段见 [图形验证记录](graphics-verification.md)。本文保留原生迁移阶段的历史结果。

日期：2026-10-23。状态：Rust/Wasmtime 开发宿主迁移验收完成。

## 环境与依赖

Windows；Rust 1.99.1 (58cf8f9c3 2026-10-13)；Wasmtime 50.0.0；MSVC C++ Build Tools/Windows SDK。工具链安装、包缓存和编译产物放在项目 .cache。Rustup 未修改全局 PATH；构建入口只为当前进程设置工具链/编译器环境。

Rustup、channel、rustc/cargo/std/rustfmt/clippy 分发核对官方 SHA-256。Cargo.lock 固定 117 个依赖包的解析与校验信息。应用运行不需要 Node。

## 最终实际结果

| 检查 | 结果 |
| --- | --- |
| cargo test --locked --no-fail-fast | 12 项通过、0 失败：broker 4、host 6、package 1、protocol 1 |
| cargo fmt --all -- --check | 通过 |
| cargo clippy --locked --all-targets -- -D warnings | 通过，无警告 |
| node --test tests/*.test.mjs tests/kotlin.integration.mjs | 25 项中 22 通过、0 失败、3 平台跳过 |
| node scripts/native-demo.mjs | 原生与参考宿主 appId/events/exitCode/requests 完全一致 |
| node scripts/check.mjs | 通过 |
| 直接运行 ming-app-host.exe | 成功，无 Node 参与宿主执行链 |

Windows 上通过 scripts/rust.ps1 配置环境；含 -- 的格式/Clippy 参数用显式 CargoArgs 数组，避免 PowerShell 消费分隔符。

## 真实 Kotlin 产物

未修改 Kotlin 源码与 ming_v1 ABI，运行原始 artifacts/read-file/main.wasm：7632 字节，SHA-256 83c17b2df1bf7872f3fd8fa5bf64450eaaf33425df08678219d1ebdaf6306c49。

授权内容 Hello from Kotlin on MingOS! 你好 加换行，未授权资源返回 -1，无任何启动授权也返回 -1。成功运行 79 次 SDK 请求，fuelConsumed=1587；同一产物在两个宿主的语义结果完全一致。

观察到的一次直接原生执行 durationMs=212；一次对照演示为 254。它们是 Windows debug 构建的启动、JIT 编译及少量文件 RPC 的诊断样本，不是图形性能、吞吐量或生产优化结论。

## 测试先行与修正

权限核心 4 项、包加载 1 项、协议 1 项在 NOT_IMPLEMENTED 状态分别失败，补齐后通过。原生进程/真实 Kotlin 的 6 项先确认失败，接入实际引擎后全部通过。

Wasmtime 50 使用独立 Error/Result，需要显式转换 anyhow 错误与 host 回调结果；线程特性未编译，不调用仅在线程 feature 下提供的配置方法。依据实际编译错误和本地依赖源码完成适配，未修改示例绕过引擎兼容性验证。

覆盖：未授权资源、无效句柄/偏移、资源/日志限额、清单自授权拒绝、未知/WASI/非函数导入、错误函数/入口签名、陷阱、fuel 耗尽、父进程 wall-clock、请求超限、线性内存增长拒绝、失败后新应用继续运行、真实 Kotlin UTF-8 内容。

独立静态评审未发现本原型声明范围内的可操作问题。实际编译、运行与测试均由主任务完成。

## 尚未覆盖的系统属性

没有 Linux 内核隔离、Linux FIFO/命名空间/cgroup 测试、发布者签名认证、父进程强杀后的 OS 托管、GUI、真实硬件或 UI 帧时间结论。Node 参考测试中的 3 项跳过为 Windows 符号链接权限与 POSIX FIFO 条件。

fuel 是指令预算，线性内存限额不是 RSS 或 GC 总内存限额；引擎原生代码和平台服务属于可信计算基础。包目录/启动授权仍为可信输入。文档中的 Linux 命令是后续移植入口，不代表本次已在 Linux 运行。
