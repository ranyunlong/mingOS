# Graphics Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. 用户已授权下一阶段，按当前会话实施；无 Git 仓库，不创建分支或提交。

**Goal:** 真实 Kotlin 应用经 SDK v2 批量读取与提交绘制，在 Rust 原生窗口查看 PNG 并处理输入。

**Architecture:** worker 验证 guest memory，父进程管理文件/图像资源与帧验证；CPU 参考渲染生成有界帧，原生预览和 headless 共用渲染器。

**Tech Stack:** Kotlin 2.3.20、Rust 1.99.1、Wasmtime 50、png 0.18、winit 0.30.13、softbuffer 0.4.8。

**Spec:** ../specs/2026-10-24-graphics-viewer-design.md

## Global Constraints

- v1 ABI/示例不变；v2 需显式清单版本。
- 每批 64 KiB；每帧 64 KiB/256 命令；PNG/帧最多 4,194,304 像素；图像总预算 32 MiB。
- 不新增 WASI 或 guest 文件路径；快照和脚本路径只属于可信 CLI。
- CPU 参考后端不代表 GPU 或生产 UI 性能；原生预览默认 60 秒，可信 CLI 显式验收时限最多 300 秒。
- 不声称 Linux 沙箱或 Unicode/无障碍完整实现。

## Task 1: 数据和图形核心

Files: src/broker.rs, src/graphics/{mod,frame,raster,font}.rs, tests/graphics.rs。

- [x] 先写批量读取/越界/EOF、PNG 尺寸预算、帧长度/句柄/颜色裁剪测试并观察失败。
- [x] 实现 PNG 资源、帧解析、CPU 渲染与有界文件读取。
- [x] 执行核心测试通过。

~~~rust
assert_eq!(broker.read_chunk(handle, 0, 65537).value, -2);
assert!(Frame::decode(&malformed).is_err());
~~~

## Task 2: SDK v2 与真实 Kotlin

Files: src/{engine,protocol,supervisor,package}.rs, ming-sdk/.../Graphics.kt, samples/image-viewer, scripts/build-kotlin.mjs。

- [x] 写 SDK 版本/签名、无效 guest memory、真实 Kotlin 新示例测试并观察失败。
- [x] 接通有界 payload 与 memory 校验、图像/绘制/输入接口。
- [x] 编译并实际执行 Kotlin，验证首帧、批量读取、脚本缩放/平移和旧版回归。

~~~rust
assert!(!run_sdk1_with_v2_import().success());
assert!(run_real_viewer().frames_submitted >= 1);
~~~

## Task 3: 原生预览与视觉验证

Files: src/{desktop,cli,main}.rs, scripts/generate-viewer-fixture.mjs, docs/graphics-viewer.md。

- [x] 实现有界输入和单槽最新帧、窗口退出/取消、快照输出。
- [x] 验证滚轮、拖拽、键盘与窗口尺寸，检查原生截图。
- [x] 导出帧时间/请求量诊断，明确测试负载和后端。

## Task 4: 收尾

- [x] cargo fmt/clippy/tests、Kotlin 构建、v1 Node/Rust 回归。
- [x] 独立评审权限/预算/关闭路径，处理重要问题。
- [x] 更新 SDK 文档、运行入口、截图和实际验证记录。

## 完成记录

2026-10-24 四项任务完成。38 项 Rust、27 项 Node（3 平台跳过）、Kotlin 双示例构建、格式/Clippy、实窗输入和退出均已执行。最终窗口后端变更为 winit/softbuffer，见 ADR 0003；详细证据见 ../../graphics-verification.md。没有提交或推送。
