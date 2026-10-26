# Kotlin/Wasm shadow-demo

真实 Kotlin 2.3.20 / wasmWasi / SDK4 示例。运行入口是编译后的 `_start`，由 Wasmtime 执行；JavaScript 仅用于构建、测试和归档证据。

从仓库根目录执行（使用现有 JDK 和已缓存的 Gradle 8.14.5 / Kotlin 依赖，不联网下载）：

```powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
node samples/shadow-demo/build.mjs --contract --label=green-build
node samples/shadow-demo/build.mjs --label=demo-build
node samples/shadow-demo/test.mjs
node samples/shadow-demo/verify-host.mjs
```

应用包位于 `artifacts/shadow-demo/app.json`，合约包位于 `artifacts/shadow-kotlin/contract`。独立 Gradle settings 引用现有 ming-sdk 项目，SDK 编译输出重定向到本示例的 build/sdk；Gradle 缓存和证据放在 artifacts/shadow-kotlin，不占用根构建输出。首次运行会复制已有 modules-2 缓存到该专用目录。

事件合同见 `event-contract.json`：初始 1100×760，close=0、resize=1、click=4；完成日志为 `code40=framesSubmitted`，文字准备时间沿用宿主 `frame.textPrepareMicros`。移动点击 `[4,40,40,0]`，变色点击 `[4,144,40,0]`。

交互：左上“移动”按钮平移面板，“变色”按钮改变阴影 ARGB；resize 改变面板尺寸。两个 style 和五个文字 layout 仅在启动时创建，正常退出及创建、测量、提交失败均执行 cleanup。最多 15 条命令（3 个 shadow、5 个 AA），完整像素上限时 P*N=62,914,560。所有合法 1..4096 且 P<=4,194,304 的 viewport 保留原尺寸，小尺寸省略 UI 细节；没有固定缩小大 viewport。主面板常规及大窗口尺寸都超过 73px canonical core，两张卡片共享第二个 style，移动、颜色和大尺寸变化不改变其 canonical key。

SDK 测试执行真实 Kotlin Wasm，host.mjs 仅模拟 import 边界以检查 op7 二进制编码、局部拒绝、宿主拒绝、style 生命周期与各失败退出路径。abi.test.mjs 读取实际 Wasm 类型表，验证两个新增 import 的 i32 签名及完整 SDK-only 白名单；adapter 仍只改写 wasi_snapshot_preview1.random_get，其他 WASI import 负例保持拒绝。

verify-host.mjs 直接运行现有 Rust/Wasmtime 宿主，保存 8 组 CPU 截图和 JSON，检查移动/变色改变像素、切换恢复完全相同 PNG，以及大 resize、窄窗口、1x1、320x240。不会编译或修改 Rust。可指定宿主 `--host=C:/path/to/ming-app-host.exe`；宿主已构建 GPU 功能时使用 `--gpu` 同时执行 CPU/GPU 并比较 PNG。普通 native 零读回和 GPU 缓存计数验收由父线程负责，CPU 结果不冒充 GPU 缓存证据。

独立入口无需修改 root scripts/settings。若父线程希望加入统一构建，应自行注册 shadow-demo Gradle 项目、扩充 scripts/build-kotlin.mjs 的样例白名单，并接入统一检查；本任务未修改这些文件。
