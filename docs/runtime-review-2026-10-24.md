# 应用运行时复评：Wasm、JVM与ART

日期：2026-10-24。用户已确认“真正Compose编程模型配MingOS组件”，随后询问为什么不能像Android那样编译JAR执行，并同意先比较。正式应用运行时尚未迁移；当前Wasm/SDK1..4继续保留可运行基线。

**最终用户决定：放弃JVM，继续Wasm。** 以下比较保留为历史证据；不再推进JVM主线或正式JAR运行器。

## 问题拆开

JAR是Java class及资源的容器，不提供独立沙箱。Android构建通常由D8将Java字节码转为DEX，DEX随APK分发并由ART运行。ART支持JIT与AOT/profile优化；选择Kotlin和高性能UI不要求使用Wasm。

当初选择Wasm，是为了在Linux完整应用沙箱尚未实现时，先把文件、网络、窗口等外部能力收敛为明确的Ming导入。Wasm语言运行时、字体和GPU后端是不同层；加入真实Compose后，必须重新衡量组件兼容成本与隔离成本，不能把早期运行时选择当成已证明的生产最优解。

## 已有直接证据

- 当前Kotlin/Wasm经Rust/Wasmtime和Ming SDK运行，SDK4已完成真实AA/字体、九组CPU/GPU精确图片、原生最大化点击和Linux七路径验收。见ui-aa-verification.md。
- 官方Compose Compiler2.3.20已成功解析。固定Runtime1.10.0的真实wasmWasiCompileClasspath失败，官方发布metadata与实物KLIB均为wasm-js；annotation/collection也有WASI缺口，coroutines1.9/atomicfu0.27有可解析的WASI发布物。这是最初对固定发布物的资格结论。随后官方源码WASI port已编译并在真实宿主通过counter和有界生命周期测试，父线程独立重放counter；见compose-wasi-port.md。初始发布物失败记录保留在../artifacts/compose-qualification/report.md。
- JVM对照使用真实Compiler2.3.20和官方Runtime1.10.0，已编译/执行同一Counter并由父线程独立重放，状态、手动驱动重组、节点移除及销毁通过；它未接MingOS控件、实际输入或SDK-only隔离。见../artifacts/compose-jvm-qualification/report.md与parent-run.command.json。
- 用户随后明确放弃JVM；官方源码WASI适配已通过无窗口编程模型资格，目前接入真正Compose驱动的Ming组件。原JVM与WASI探针记录均保留，不把比较中的候选建议当作当前生产决策。

## 主要取舍

| 项目 | Kotlin/Wasm + Wasmtime | Kotlin/JVM | DEX + ART |
| --- | --- | --- | --- |
| 当前可运行基线 | 已有Ming SDK与多平台图形样例 | 真实Compose Counter已通过，未接正式SDK | 尚未集成 |
| Compose编程模型 | 官方源码WASI port已通过headless；Ming组件桥接正在实施 | 使用官方JVM发布物，counter已实测，仅保留历史对照 | 需要DEX构建与ART运行环境；不等于复用完整AndroidUI |
| 外部能力边界 | Wasm函数导入白名单、受限memory/fuel，仍需最终Linux防御 | JAR/类加载器自身不足；需要Linux隔离及受控SDK/原生扩展策略 | Android沙箱是平台内核与服务共同提供，不能只复制DEX格式 |
| Linux应用生态 | 可保持自己的包、SDK、窗口和服务 | 同样可保持自己的应用生态；JVM不要求使用Linux桌面应用API | 是否兼容Android应用是独立产品决定，不由ART自动决定 |
| 已有Rust图形/SDK资产 | 当前路径 | 场景协议/父进程授权可设计为共用，实际JVM桥接尚未实现 | 也需单独桥接，不自动拥有Ming窗口/字体 |
| 性能结论 | 有当前图形样例数据 | 单个counter不能证明UI帧率/内存/启动优势 | 未测量 |

## SDK-only仍是硬约束

Android通过每应用UID、SELinux和seccomp等内核机制建立沙箱，原生代码也在其中。它证明应用能力限制不必依靠Wasm，但MingOS必须亲自实现相应边界。

若选JVM，应用进程不得直接继承宿主环境、文件系统、网络、显示服务或任意原生扩展权限。需要独立身份/进程、最小可见文件系统、受控网络/IPC、系统调用和cgroup资源限制；资源授权仍由父进程按句柄检查。类加载器/API裁剪只是补充，不应代替内核隔离。不能把普通JVM进程标为“已受Ming沙箱保护”。

JDK24起SecurityManager已永久禁用；本项目测试用JDK21，不能误称21也已禁用，但也不应以这一已退出的机制作为长期安全架构。Wasm也不免除引擎漏洞防御和Linux系统级隔离工作。

## 当前执行方向

按用户最终决定继续Wasm：官方annotation/collection/Runtime源码的WASI port及真实Compose counter已实际编译并在Ming host执行，接着完成自动Snapshot订阅和Ming组件。组件仍接自定义Applier、Ming布局/绘制/输入，外部能力继续Ming SDK；不放宽WASI导入，不用自造Runtime替身。

JVM对照证明了另一平台的编程模型可用，不能替代Wasm移植、异步调度、Ming组件映射或Linux隔离验收。两种探针的数据也不能作为相对UI性能结论。

## 官方参考

- Android D8：https://developer.android.com/tools/d8
- Android ART：https://source.android.com/docs/core/runtime
- Android应用沙箱：https://source.android.com/docs/security/app-sandbox
- JAR规范：https://docs.oracle.com/en/java/javase/25/docs/specs/jar/jar.html
- JDK24迁移说明（SecurityManager）：https://docs.oracle.com/en/java/javase/24/security/security-manager-is-permanently-disabled.html
- Java安全编码指南：https://www.oracle.com/java/technologies/javase/seccodeguide.html

参考用于架构事实；本项目是否通过以实际命令、依赖、编译产物、运行结果和隔离测试为准。
