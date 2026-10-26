# Rust App Host 开发说明

状态：Rust/Wasmtime 迁移验收通过，仍为开发宿主原型。SDK ABI 沿用 [SDK v0](sdk-v0.md)。设计与执行计划见 superpowers 目录。

## Windows 前提

PowerShell 7、MSVC C++ Build Tools 与 Windows SDK。执行测试或示例前，先按项目 README 构建真实 Kotlin 应用包（artifacts/read-file）。执行项目工具链引导会把固定 Rust 版本安装到 .cache，不修改全局 PATH。下载工具从官方源获取并核对 SHA-256。

~~~powershell
.\scripts\bootstrap-rust.ps1
.\scripts\rust.ps1 test --locked
.\scripts\rust.ps1 build --locked
.\.cache\rust-target\debug\ming-app-host.exe run artifacts/read-file/app.json --grant 1=samples/read-file/data/hello.txt
~~~

质量检查命令：

~~~powershell
.\scripts\rust.ps1 -CargoArgs @('fmt','--all','--','--check')
.\scripts\rust.ps1 -CargoArgs @('clippy','--locked','--all-targets','--','-D','warnings')
~~~

PowerShell 脚本可能消费独立的 -- 分隔符，因此 Clippy 编译器参数使用显式数组。

rust.ps1 只给当前构建进程配置 Cargo/Rustup/目标目录与 MSVC/SDK 路径。无需 Node 执行原生 App Host；Node 可以继续用于旧宿主对照测试。

## Linux

安装 rust-toolchain.toml 指定版本及本机 C 链接工具后：

~~~sh
cargo test --locked
cargo build --locked
.cache/rust-target/debug/ming-app-host run artifacts/read-file/app.json --grant 1=samples/read-file/data/hello.txt
~~~

如果真实 Kotlin 产物尚不存在，先按项目 README 构建；原生集成测试不会跳过缺失产物。

## 限制含义

- timeout-ms：父进程截止期限，包含 worker 编译与执行，不是 fuel 换算值。
- fuel：Wasmtime 执行预算；耗尽后应用失败，不代表 CPU 时间配额。
- memory-bytes：每个允许的线性内存上限，不是进程 RSS 或 GC 总内存上限。
- max-requests：一次应用运行的 SDK 请求总数。

普通应用没有 WASI linker、宿主路径或原生扩展接口。Rust 引擎与文件服务仍属于可信实现；进程隔离、Linux 命名空间/cgroup/seccomp 及父进程强杀后的托管需要后续 OS 验证。

## 已验证行为

Rust 原生测试 12 项通过，包括同一个真实 Kotlin 产物、授权与无授权、非法导入/签名、陷阱、fuel、wall-clock、请求限额和线性内存增长。Node 参考宿主的 25 项测试中 22 通过、3 项平台跳过。详细结果见 [原生验证记录](native-verification.md)。

可选的跨宿主演示：node scripts/native-demo.mjs。它比较同一 Wasm 的 appId、events、exitCode、requests；Node 只用于开发对照，不参与 Rust CLI 的执行链。

本次保留的原始 Kotlin 文件为 7632 字节，SHA-256 为 83c17b2df1bf7872f3fd8fa5bf64450eaaf33425df08678219d1ebdaf6306c49。

## 当前进展与后续

SDK v2、批量读取、PNG 图像资源、CPU 参考绘制和事件驱动原生图片查看器已接通，见 [图片查看器](graphics-viewer.md)。完整测试现包括两个真实 Kotlin 示例；清洁构建时先执行 node scripts/build-kotlin.mjs all。

下一阶段优先验证 Linux/Wayland 执行环境，接入 GPU 和字体基础，再推进合成器与 Shell。
