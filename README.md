# MingOS

基于 Linux 的独立桌面与应用平台探索。目标是流畅、一致的桌面体验，以 Kotlin 等高级语言开发应用，并通过 Ming SDK 控制外部系统能力。

当前已接通 **SDK6、真实 Kotlin/Wasm Compose 应用、GPU 直接呈现与缓存、中文字体/阴影、Ming 多窗口桌面、持续会话、文本输入和应用数据**。应用经 Rust/Wasmtime 调用 Ming SDK，父进程持有授权和系统资源；Node 用于构建与早期参考实现，应用执行不依赖 Node。

真实 Linux kernel VM 已启动到 MingDesktop；最新严格隔离短诊断确认两个 Kotlin 应用自然关闭、精确文字、数据写入/readback、worker 回收及内核关机。关闭阶段保护与可信系统镜像预编译已完成实现，原完整双Kotlin脚本及同盘两次fresh QEMU冷启动已通过；当前继续实际kernel中的关闭/Dock重启与恢复，整体目标保持进行中。当前统计和证据范围见[系统验收状态](artifacts/current-system-status-20261026.md)，历史阶段记录保留其冻结版本范围。

## 文档

从 [文档索引](docs/README.md) 开始。已确认需求、建议和待验证事项分别记录，正式应用路线为 Kotlin/Wasm；Node 仅用于构建工具和早期参考宿主。

## 图片查看器

使用说明见 [图片查看器](docs/graphics-viewer.md)，契约见 [SDK v2](docs/sdk-v2.md)，实际结果见 [图形验证记录](docs/graphics-verification.md)。

~~~powershell
node scripts/generate-viewer-fixture.mjs
node scripts/build-kotlin.mjs all
.\scripts\rust.ps1 build --locked
.\.cache\rust-target\debug\ming-app-host.exe view artifacts/image-viewer/app.json --grant 1=samples/image-viewer/data/coastal.png
~~~

查看器支持滚轮、拖拽、窗口适配、1/F/R 和 Esc。默认预览为有界 60 秒会话；它不是完整桌面或生产应用运行器。

## 中文字体与文本布局

[SDK3](docs/sdk-v3.md)支持内置中文/拉丁字体、Q6精确测量和可复用布局；[阶段验收](docs/typography-verification.md)记录真实Kotlin、CPU/GPU像素一致性、原生零读回与Linux/Wayland运行。完整IME、系统合成器与启动镜像仍在后续路线中。

~~~powershell
node scripts/build-kotlin.mjs text-demo
.\scripts\rust.ps1 build --locked --features gpu
.\.cache\rust-target\debug\ming-app-host.exe view artifacts/text-demo/app.json --renderer gpu
~~~

## UI基础与Compose路线

SDK4的圆角覆盖率、透明叠放和响应式示例已完成CPU/GPU精确对照、原生点击和Linux/Wayland验收，见[SDK4](docs/sdk-v4.md)与[AA验证](docs/ui-aa-verification.md)。有界阴影也已完成8组真实Kotlin CPU/GPU精确对照、原生零读回和Linux九路径，见[阴影验收](docs/shadow-verification.md)。用户最终选择继续Wasm；官方Runtime源码的无窗口Compose counter已实际跑通并由父线程独立重放，目前进入真正Compose驱动Ming组件的桥接，尚未宣称完整UI框架已交付。

~~~powershell
node scripts/build-kotlin.mjs ui-demo
.\scripts\rust.ps1 build --locked --features gpu
.\.cache\rust-target\debug\ming-app-host.exe view artifacts/ui-demo/app.json --renderer gpu
~~~

## Ming Desktop 开发会话

原生Ming Desktop、可信Dock与真实Kotlin多worker已通过Windows GPU Surface和Linux/Wayland开发会话验收，两个示例可独立关闭、异常退出和重新启动。运行说明见[samples/desktop](samples/desktop/README.md)，阶段证据见[桌面运行时验收](docs/desktop-runtime-verification.md)。当前继续7D持续会话/输入和Linux启动系统；已交付可启动开发镜像和同盘冷启动证据；kernel生命周期恢复继续，物理鼠标专项仍暂停。

## GPU 与 Linux 验收

GPU 的当前行为、实测与限制见 [直接呈现验收](docs/gpu-surface-verification.md) 和 [ADR 0005](docs/adr/0005-gpu-surface-cache.md)；[上一阶段](docs/gpu-verification.md) 保留为计算/读回基线。CPU 继续作为默认和像素参考；没有硬件设备或未编译 GPU feature 时，显式请求 GPU 会失败。

~~~powershell
.\scripts\rust.ps1 build --locked --features gpu
.\.cache\rust-target\debug\ming-app-host.exe view artifacts/image-viewer/app.json --grant 1=samples/image-viewer/data/coastal.png --renderer gpu
.\scripts\gpu-verify.ps1 -Samples 5
~~~

Linux 原生和 Docker/Weston 验收见 [Linux 执行说明](docs/linux-runtime.md)。无窗口、Wayland headless compositor 和实际硬件桌面是不同验证范围；以保存的结果为准。

## 原生宿主入口

使用说明见 [Rust App Host](docs/native-host.md)，实际结果见 [原生验证记录](docs/native-verification.md)。

Windows（PowerShell 7，需 MSVC Build Tools 与 Windows SDK）：

~~~powershell
.\scripts\bootstrap-rust.ps1
.\scripts\rust.ps1 build --locked
.\.cache\rust-target\debug\ming-app-host.exe run artifacts/read-file/app.json --grant 1=samples/read-file/data/hello.txt
~~~

原生宿主运行需要已经构建的 Kotlin 应用包；下面的 Node/JDK 命令用于构建 Kotlin 与运行参考实现。

## Kotlin 构建与参考宿主前提

- Node.js 24.x。
- JDK 21，JAVA_HOME 指向 JDK，或 java 已位于 PATH。
- 首次构建需要联网下载固定版本 Gradle 8.14.5 与 Kotlin 2.3.20 依赖。
- Windows / Linux 上可开发；当前实际验证平台见 [验证记录](docs/verification.md)。

## 运行

PowerShell 中先确认 JAVA_HOME 指向 JDK 21。本机已验证的 JDK 为 Android Studio 自带 JBR：

~~~powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
~~~

Linux 或其他设备请将 JAVA_HOME 指向实际安装的 JDK 21。Windows 的 Java socket 临时目录问题由构建脚本在子进程环境中处理。

在项目根目录依次执行：

~~~sh
node --test tests/*.test.mjs
node scripts/build-kotlin.mjs all
node --test tests/kotlin.integration.mjs
node scripts/demo.mjs
node scripts/check.mjs
~~~

第一条只检查宿主与攻击夹具；真实 Kotlin 集成测试另行执行，并且没有编译产物就会失败，不会跳过或替换成手写 Wasm。

构建产物位于 artifacts/read-file，构建缓存位于 .cache。应用包包含 app.json 和 main.wasm。

手动运行并授予一个文件：

~~~sh
node src/cli.mjs run artifacts/read-file/app.json --grant 1=samples/read-file/data/hello.txt
~~~

省略 --grant 则同一个 Kotlin 应用无法读取该资源：

~~~sh
node src/cli.mjs run artifacts/read-file/app.json
~~~

路径含空格时，请把完整的 id=path 参数作为一个 shell 参数传入。授权参数是可信启动者权限，不属于普通应用配置。

## 当前边界（参考宿主与原生宿主）

- Wasm 不获得 Node 对象或完整 WASI；外部函数导入由白名单控制。
- 文件授权和描述符留在父进程；应用使用本次运行的资源句柄。
- 原生宿主使用独立 Rust worker、固定类型 SDK 导入、fuel、线性内存限额和父进程截止期限。参考宿主使用 Node 权限模式；两者都不是完整 Linux 沙箱。
- 已有 CPU/GPU 图片查看器与原生 GPU 直接呈现；完整字体/无障碍、系统合成器、Linux 系统镜像、签名分发和生产级资源隔离尚待后续。

后续按 [连续开发路线](docs/continuous-development.md) 自动推进字体、视觉效果、合成器和桌面。详见 [总体架构](docs/superpowers/specs/2026-10-23-mingos-architecture-design.md)。
