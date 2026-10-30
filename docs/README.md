# MingOS 文档

更新：2026-10-25。Kotlin/Wasm 应用平台、图形/动画、Ming 多窗口桌面和 SDK6 输入/数据已接通；严格隔离 Linux kernel VM 短诊断和正式两次冷启动通过各自轮次，产物已导出；关闭阶段保护与可信系统镜像预编译已通过原完整双Kotlin kernel资格和同盘冷启动；当前继续kernel生命周期恢复，完整系统尚未验收。

- [当前系统验收状态](../artifacts/current-system-status-20261026.md)：当前构建、历史冻结、真实 VM 结果与剩余门槛。

- [需求与决策记录](requirements.md)：区分用户确认、架构建议和待验证事项。
- [总体架构设计](superpowers/specs/2026-10-23-mingos-architecture-design.md)：系统分层、信任边界和路线图。
- [第一阶段实施计划](superpowers/plans/2026-10-23-app-host.md)：开发范围、步骤及验收。
- [运行时选择 ADR](adr/0001-capability-wasm-host.md)：为何先验证 Kotlin/Wasm、原型限制。
- [SDK v0 协议](sdk-v0.md)：模块导入、能力句柄、错误码和进程协议。
- [验证记录](verification.md)：实际执行的测试与尚未验证的系统属性。

用户已于 2026-10-23 授权：先整理既有讨论，再按已讨论方向实施第一步；不需要再次审批文档后才开始该步骤。后续平台选择仍以验证结果为依据。

- [Rust 原生宿主说明](native-host.md)：项目局部工具链、原生 CLI 与资源预算。
- [Rust 迁移设计](superpowers/specs/2026-10-23-rust-app-host-design.md) 与 [迁移计划](superpowers/plans/2026-10-23-rust-app-host.md)。
- [Rust 宿主 ADR](adr/0002-native-rust-host.md)：引擎选择、验收边界与尚待验证项。

- [Rust 迁移验证记录](native-verification.md)：真实 Kotlin、资源故障测试、跨宿主一致性与平台边界。

- [图片查看器](graphics-viewer.md)、[SDK v2](sdk-v2.md)、[图形验证记录](graphics-verification.md)。
- [原生参考窗口 ADR](adr/0003-graphics-reference-backend.md)：事件驱动后端决策。

- [GPU 验证记录](gpu-verification.md)、[Linux 执行说明](linux-runtime.md)。
- [GPU 命令后端 ADR](adr/0004-gpu-command-backend.md)。
- [Linux/GPU 设计](superpowers/specs/2026-10-24-linux-gpu-design.md) 与 [实施计划](superpowers/plans/2026-10-24-linux-gpu.md)。

- [GPU直接呈现验收](gpu-surface-verification.md)、[Surface/缓存ADR](adr/0005-gpu-surface-cache.md)。
- [连续开发路线](continuous-development.md)、[字体预研](typography-research.md)。

- [SDK3文本API](sdk-v3.md)、[字体布局ADR](adr/0006-sdk3-typography.md)、[字体实现计划](superpowers/plans/2026-10-24-typography.md)。
- [字体阶段验证记录](typography-verification.md)：真实样例、CPU/GPU对照、内存资格与平台验收状态。

- [SDK4实验接口](sdk-v4.md)、[UI基础设计](superpowers/specs/2026-10-24-ui-foundation-design.md)、[AA阶段验证](ui-aa-verification.md)。

- [应用运行时复评](runtime-review-2026-10-24.md)：按用户问题比较JVM/ART与Wasm，区分Compose可用性、隔离成本和实际结果。

- [有界阴影验收](shadow-verification.md)、[Compose组件桥接设计](superpowers/specs/2026-10-24-compose-ming-bridge-design.md)与[实施计划](superpowers/plans/2026-10-24-compose-ming-bridge.md)。

- [官方Compose WASI资格与限制](compose-wasi-port.md)：固定源码/Compiler、真实counter、生命周期与后续桥接边界。

- [真实Compose组件验证](compose-verification.md)、[Ming Compose用法](../ming-compose/README.md)与[真实示例](../samples/compose-demo/README.md)。

- [Compose帧/协程确定性资格](compose-frame-qualification.md)、[HostFrame设计](superpowers/specs/2026-10-25-host-frame-design.md)与[实施计划](superpowers/plans/2026-10-25-host-frame.md)：真实宿主时钟/SDK5集成中。

- [SDK5 HostFrame](sdk-v5.md)、[真实异步动画验证](animated-verification.md)。

- [最小桌面设计](superpowers/specs/2026-10-25-minimal-desktop-design.md)、[GPU应用上下文前置计划](superpowers/plans/2026-10-25-gpu-render-contexts.md)。

- [桌面合成器验证](desktop-compositor-verification.md)、[第7B合成器实施计划](superpowers/plans/2026-10-25-desktop-compositor.md)：多context GPU直合成、CPU精确参照及窗口scene衔接。

- [帧应用异常链修复与复核](../artifacts/framed-kotlin-fix-20261025/closure.json)、[当前动画包回归](../artifacts/animated-framed-fix-20261025/result.json)：历史漏审已补齐，P2已关闭。

- [原生桌面与真实多worker验收](desktop-runtime-verification.md)、[桌面开发会话](../samples/desktop/README.md)、[持续会话设计](superpowers/specs/2026-10-25-persistent-session-design.md)：7C基线已验，7D与启动系统前置继续。

- [严格隔离 Linux 开发系统冷启动验收](boot-system-verification.md)：同bundle同盘两次实际kernel启动，SDK数据/文本、自然退出、隔离与关机的原始证据。

- [可信系统镜像与实际冷启动](system-image-verification.md)：静态可信Module、原Wasm/SDK边界、完整kernel和同盘双启动证据。

- [持续会话取消与异常恢复验证](persistent-recovery-verification.md)：快速收尾取消修复、真实worker130项、Kotlin/多代trap/RPC限额资格及原时序RED边界。

- [关闭生命周期时间线](close-lifecycle-verification.md)：统一父侧时钟的 Close、SDK5+ 回复写出、Result 接受、期限决策与原生回收收据。

- [AppData写入进程崩溃与恢复验证](app-data-process-crash-verification.md)：生产存储API的8个SIGKILL场景、16个子进程回收、完整文件与孤儿元数据审计。

- [交付主线恢复检查点](boot-delivery-recovery-20261027.md)：离线工具镜像、当前系统Host、kernel候选输入和仍缺的原冻结输入。
