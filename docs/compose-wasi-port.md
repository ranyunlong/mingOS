# Compose 官方源码 WASI port qualification

日期：2026-10-24。当前状态：**headless 编程模型资格通过；本任务收口于 qualification 交付。** 真实 Kotlin/Compose compiler2.3.20 已将官方 Runtime1.10.0 源码适配编译为 wasmWasi，counter-03 已由 port 固定宿主和父线程独立冻结宿主重放成功。此结论不等于原生 Ming 组件、GUI、完整异步调度或生产性能已通过。

## 稳定基线与交付入口

| 项目 | 位置 |
| --- | --- |
| 机器可读稳定入口 / 黄金16事件 | [stable-entry.json](../artifacts/compose-qualification/port/stable-entry.json) |
| 完整实际编译/执行报告 | [report.md](../artifacts/compose-qualification/port/report.md) |
| 组件桥接交接说明 | [handoff.md](../artifacts/compose-qualification/port/handoff.md) |
| 冻结应用包 | [app.json](../artifacts/compose-qualification/port/counter-03/app.json)、[main.wasm](../artifacts/compose-qualification/port/counter-03/main.wasm) |
| Runtime编译产物 | [non-packed KLIB manifest](../artifacts/compose-qualification/port/klibs/runtime/default/manifest) |
| 实际源码入口 | [counter Main.kt](../probes/compose-wasi/port/counter/src/wasmWasiMain/kotlin/Main.kt) |
| 依赖、compiler及KLIB哈希 | [compiled-artifacts.json](../artifacts/compose-qualification/port/compiled-artifacts.json) |
| 源归属与最小补丁 | [source-audit.json](../artifacts/compose-qualification/port/source-audit.json)、[日志actual patch](../artifacts/compose-qualification/port/patches/platform-logging.patch) |

冻结 Wasm SHA-256：

```text
453936b6926f59a2e394e8815cb6a0550a0c0c2e4a29ddc98f0ab61b5aebe09b
```

Runtime保留目录是 non-packed KLIB，根路径为 artifacts/compose-qualification/port/klibs/runtime；不是JAR，也不是官方Maven已发布的WASI variant。manifest明确 compiler_version=2.3.20、wasm_targets=wasm-wasi。当前已实测的消费方式是独立Gradle工程中的 project(":runtime")；后续root/composite build接入由组件bridge新任务验证。

## 固定工具链与来源

- JDK：Android Studio JBR21.0.6；Gradle8.14.5，复用项目wrapper和 .cache/gradle。
- 应用compiler：Kotlin2.3.20与 org.jetbrains.kotlin.plugin.compose2.3.20。JDK是构建工具环境，应用产物仍为wasmWasi。
- 官方源码：Runtime/runtime-annotation1.10.0、collection1.5.0、AndroidX annotation1.9.1。
- 最终链接：官方 coroutines-wasm-wasi1.9.0、atomicfu-wasm-wasi0.27.0、stdlib-wasm-wasi2.3.20。compile API图中的atomicfu0.25.0与最终implementation链接图分别保留，不能混淆。
- 287个上游文件逐字节核对，238个common Kotlin文件未改算法，原Apache许可证保留。

适配包括合法Contracts opt-in、恢复中间common source sets、保留上游语言层actual、固定Kotlin对象identity实现、显式拒绝默认clock，以及将两处非JVM诊断println换成受限Ming日志传输。没有替代Composable注解或自造Runtime。

原始命令参数、时间、退出码和分开的stdout/stderr均保留在证据目录；[toolchain.json](../artifacts/compose-qualification/port/toolchain.json)、[source provenance](../artifacts/compose-qualification/port/upstream-provenance.json)和[compiler lowering报告](../artifacts/compose-qualification/port/compiler-evidence/counter-composables.txt)可用于核对。

## 已证明的行为

counter-03在真实Rust/Wasmtime Ming host4中执行，精确验证：

1. initial count=0，建立两个内存树节点。
2. remembered mutableStateOf变化为1，经官方ControlledComposition/Snapshot API重组，state、remember token和原节点身份保持。
3. 条件节点移除，child DisposableEffect释放一次。
4. Composition.dispose清空树、释放parent effect；重复dispose不重复释放。三个SideEffect提交，remember初始化一次。
5. 512个SnapshotWeakSet wrapper被显式移除，槽位全部清空。**这不是自动弱引用GC或实际堆回收证明。**

当前counter由测试代码提供 modified-state集合并同步驱动ControlledComposition；它没有实现下一阶段的自动Snapshot订阅桥接或异步Recomposer循环。

父线程独立冻结宿主/包后重放同一Wasm，SHA和16事件精确一致。只读核对记录见 [parent-replay-evidence.json](../artifacts/compose-qualification/port/parent-replay-evidence.json)，原结果在 [父线程result.json](../artifacts/compose-qualification/parent-replay/result.json)与[stdout.json](../artifacts/compose-qualification/parent-replay/stdout.json)。可变/覆写hashCode的独立identity用例来自原checkpoint，不能误称counter本身新增了该用例。

## 独立有界稳定性

stability模块没有替换counter-03；它通过init script将全部build/cache输出隔离到自身目录。真实执行通过16个Composition生命周期、1024次状态更新、128次detail移除及重挂、128次keyed换序、208个RememberObserver正常记忆与释放，以及重复dispose、同值无变化重组与dispose后旧state写入检查。

首轮1亿fuel耗尽的失败已保留；第二轮在单独3亿fuel预算下通过，实际消耗195502045 fuel。此预算不改变root默认限制，不构成性能合格或长期内存稳定结论。见 [稳定性result](../artifacts/compose-qualification/port/stability/result.json)、[report](../artifacts/compose-qualification/port/stability/report.md)及[verification](../artifacts/compose-qualification/port/stability/verification.json)。

## 必须保留的四项限制

| 限制 | 当前实际边界 |
| --- | --- |
| **strong weakref fallback** | 上游web WeakReference actual仍保有强引用。仅证明显式wrapper清理；长期保留、真正弱引用GC和堆回收未证明。 |
| **explicit clock** | 默认MonotonicFrameClock明确抛UnsupportedOperationException，需提供合适clock；独立用例验证了拒绝路径。未提供完整异步时钟/调度。 |
| **single thread** | guest线程身份为0/main，依赖单线程执行；同步actual不保证并发/重入安全。 |
| **internal identity不稳定** | 固定Kotlin2.3.20内部_hashCode，String单独处理；没有全局强引用identity注册表。编译器明确警告不可保证未来兼容。 |

此外保留expect/actual及内部compiler参数的兼容性警告。诊断传输是实验协议：UTF-8行含换行最多2048 bytes，超限和负host acknowledgement明确报错，既有4096-event配额未改。这不是正式SDK字节日志API。已有诊断中文/边界用例和默认clock拒绝用例均实际执行通过。

## 实际imports与父线程intrinsics边界

counter-03的packaged模块只有两个既有符号（三个import声明，其中log重复声明）：

```text
ming_v2.random_fill(i32, i32) -> i32
ming_v1.log(i32, i32) -> i32
```

只使用原adapter已有的random_get映射，未放宽WASI白名单；实际类型见 [import-types.json](../artifacts/compose-qualification/port/counter-03/import-types.json)。**当前同步资格用例无需父线程新增intrinsic。**

独立保存的counter-02组合suspend变体带入了clock_time_get、poll_oneoff、fd_write、proc_exit。实际类型和未来monotonic时间、有限可取消等待、字节诊断、app-only abort的最小候选Ming接口见 [intrinsics-proposal.md](../artifacts/compose-qualification/port/intrinsics-proposal.md)。这些是下一阶段需验证的能力提案，不是已实现接口；不能直接重命名任意WASI import来替代契约适配。

## 可复现入口

在MingOS工作区根目录，用父线程冻结的host重放稳定包：

```powershell
& '.\artifacts\compose-qualification\parent-replay\ming-app-host.exe' run '.\artifacts\compose-qualification\port\counter-03\app.json' --timeout-ms 60000
```

成功为exitCode=0、16个事件，最后 code=299/value=1；完整黄金序列在stable-entry.json。默认5秒冷加载曾超时，本轮60秒预算仅用于资格执行。实际端到端耗时包含加载/编译/执行，未拆分，不应作为生产性能结论。

源码重编译：

```powershell
node probes/compose-wasi/port/qualify.mjs gradle bridge-baseline-rebuild :counter:compileProductionExecutableKotlinWasmWasi --stacktrace
```

这在独立counter/build内生成新raw Wasm，不会自动覆盖冻结counter-03。需要保留新包时给package.mjs使用新label。独立稳定性和最终核验的命令见 [port README](../probes/compose-wasi/port/README.md)与[stability README](../probes/compose-wasi/port/stability/README.md)。不要重复已有标签覆盖已冻结证据。

## 收口与下一阶段

本任务范围到qualification文档、产物与报告为止，不再扩展port功能。Ming组件bridge由父线程的新任务按[设计](superpowers/specs/2026-10-24-compose-ming-bridge-design.md)和[实施计划](superpowers/plans/2026-10-24-compose-ming-bridge.md)负责。自动Snapshot观察、正式组件/资源所有权、native输入/绘制、CPU/GPU与Linux/Wayland桥接验收都属于下一阶段。

本轮没有Root SDK/Rust/AA/scripts/samples变更，没有重新做Poincare的独立审查。其他Linux shadow/allfeature验收不能转算为本轮Compose GUI或Linux运行资格。历史qualification、port-v1和JVM对照证据保留。
