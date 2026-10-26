# 图形阶段验证记录

日期：2026-10-24。状态：批量文件、SDK v2、Kotlin 图片查看器与原生参考窗口完成。

## 环境与实现

Windows 开发机，Rust 1.99.1、Wasmtime 50.0.0、Kotlin 2.3.20、png 0.18.1、winit 0.30.13、softbuffer 0.4.8。应用/运行时不需要 Node；Node 用于 Kotlin 构建、显式标准库导入适配和参考宿主对照。开发配置对 ming-app-host 包使用 opt-level=1，其余仍为 dev 配置。

## 实际检查

| 检查 | 结果 |
| --- | --- |
| node scripts/build-kotlin.mjs all | v1/v2 都从源码构建并打包成功 |
| cargo test --locked --no-fail-fast | 38 项通过，0 失败 |
| cargo clippy --locked --all-targets -- -D warnings | 通过，无警告 |
| cargo fmt --all -- --check | 通过 |
| node --test tests/*.test.mjs tests/kotlin.integration.mjs | 27 项：24 通过、0 失败、3 平台跳过 |
| node scripts/native-demo.mjs | 旧应用原生/参考宿主完全一致 |
| node scripts/check.mjs | JavaScript 语法检查通过 |
| 实际原生窗口 | 通过：显示、滚轮、拖拽、1、F、最大化与 Esc |
| 最终输入修复后短实窗 | exitCode=0，2 帧，7995ms 正常退出 |

Rust 分项：desktop 实际处理器 2、broker 4、graphics 7、graphics_host 6、host 6、package 1、preview_config 1、protocol 1、ui 10。Node 跳过项为 Windows 符号链接权限与 POSIX FIFO 条件，不能当成 Linux 已验证。

## 真实产物

v1 重新构建后仍为 7632 字节，SHA-256 83c17b2df1bf7872f3fd8fa5bf64450eaaf33425df08678219d1ebdaf6306c49，和上一阶段完全相同。

v2 图片查看器 116895 字节，打包 SHA-256 2eb888508c0fb4ac0a27df4bdbe8dfff1b402c46c1318dff56b6297c5988de21；原始 Kotlin 编译产物 SHA-256 d89ea609913b0962cd117ac7adb9d5283654578c49727394e573944eb7c77ecc。

唯一运行时导入适配是 wasi_snapshot_preview1.random_get → ming_v2.random_fill，记录在 artifacts/image-viewer/build-info.json。打包产物只有 Ming SDK 导入；宿主没有 WASI linker。测试覆盖熵内存范围、4096 字节单次上限与 1 MiB 会话上限。

## 原生验收证据

通过 Computer Use 选择唯一 Ming Photo 窗口，观察实际截图后操作并刷新状态。正常验收会话记录在 artifacts/image-viewer/acceptance-run.json：

- exitCode=0；45 帧；durationMs=164309，早于显式 270 秒自动关闭期限。
- 输入计数 [关闭1,尺寸2,滚轮1,位移39,点击2,保留0,原始比例1,适应窗口1,空闲5307]。
- bulkBytesRead=32；fuelConsumed=5243753；本示例实际 entropyBytes=0。
- Esc 关闭后窗口列表为空，宿主/worker 已退出。

[实际窗口截图](../artifacts/image-viewer/native-window.png) 与 [缩放截图](../artifacts/image-viewer/native-zoom.png) 来自选定原生窗口。[最终验收帧](../artifacts/image-viewer/acceptance-frame.png) 为应用渲染导出，不冒充 OS 截图。

## 修复与回归

测试先行覆盖：批量读取/EOF/越界；PNG 句柄与预算；帧结构、命令、裁剪和工作量；SDK 版本/签名；guest 内存范围；真实 Kotlin 首帧及脚本输入。

初始 minifb 试验暴露缓冲持有、输入队列及轮询丢失完整手势的问题。最终不保留 minifb，改用 winit 逐事件输入和 softbuffer 安全缓冲，见 ADR 0003。

额外回归：关闭独立优先级、最新尺寸、位移合并、短按键/字符去重、释放位移、完成通道终态、大视口映射、失焦取消以及等待匹配呈现尺寸；额外直接测试实际 mouse_button/touch_input 处理器在门闩关闭时不锁存状态。评审指出的两项最终输入边界已修复并通过专项与完整回归；修复后又执行了 8 秒实窗冒烟，正常退出。

## 性能与未覆盖项

CPU 参考渲染的样本：1100×760 一帧 renderMicros=46162；一次最大化的 1706×889 帧为 77821。它们是当前 dev 配置下的渲染样本，既不是 P99/输入延迟统计，也不支持宣称 60/120Hz 或 macOS 级效果。帧包批量提交、图像复用与有界输入已验证；GPU 和高质量字体仍是后续工作。

本次没有 Linux/Wayland 实测、内核命名空间/cgroup/seccomp、RSS/GC 总内存硬限制、发布者签名、完整 Unicode 字体/无障碍、编辑控件 IME、系统镜像或真实 Linux 硬件结论。

最终静态复核确认鼠标/触摸处理器在修改手势状态前检查呈现门闩，相关两项实际处理器回归先失败后通过；复核未留下该修复范围内的可操作问题。
