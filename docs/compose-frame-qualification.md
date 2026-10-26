# Compose coroutine/frame 确定性 sidecar qualification

本轮结论：**真实异步编程模型在明确条件下通过28组检查**。范围是新的6D资格探针，不是生产HostFrame SDK实施，不是Native呈现或真实GUI帧钟证明。

## 结果与可重放产物

- [机器结果](../artifacts/compose-frame-qualification/result.json)、[完整报告](../artifacts/compose-frame-qualification/report.md)、[28项case](../artifacts/compose-frame-qualification/cases.json)、[证据核验](../artifacts/compose-frame-qualification/verification.json)。
- [实际GREEN Wasm](../artifacts/compose-frame-qualification/green-parent-clock/main.wasm)、[app.json](../artifacts/compose-frame-qualification/green-parent-clock/app.json)、[宿主原始输出](../artifacts/compose-frame-qualification/green-parent-clock/host.stdout.log)、[实际import类型](../artifacts/compose-frame-qualification/actual-import-types.json)。
- [RED异常与测试评价](../artifacts/compose-frame-qualification/red-adapted/evaluation.json)、[仅Mode不同的证据](../artifacts/compose-frame-qualification/red-green-diff.json)、[published依赖阻断产物](../artifacts/compose-frame-qualification/red-parent-clock/module.json)。

GREEN packaged SHA-256：

```text
bdd3cf87d9846d8577d6eae444c6a5d137c448f3e69a0162ea6d3d8029c4d529
```

实际运行于复制冻结的优化Rust/Wasmtime Ming host：exit0、31事件、28组通过，fuel1167606，端到端durationMs3114，framesSubmitted/Presented均0。预算为60000ms/300M fuel；未使用120000ms，也未改worker限制。时间统计包含加载/编译/执行，不能当作生产帧性能。

## 核心上下文结论与RED→GREEN

官方Runtime1.10源码与真实测试均确认：Recomposer.effectCoroutineContext把输入clock替换为内部BroadcastFrameClock。外部parent clock必须通过运行runRecomposeAndApplyChanges的协程上下文进入。

RED只给构造器传clock，runner没有clock，实际case302失败并收集到“MonotonicFrameClock is not available in this CoroutineContext”。测试程序正常结束以保留结构化诊断，host exit0不代表测试通过；评价器为exit1/failed。

GREEN仅把Mode.kt中的OMIT_PARENT_CLOCK改为false。共享构建输入唯一差异即此文件，Main相同，随后真实host与评价器28组通过。没有通过手工ControlledComposition.recompose循环伪造异步结果。

## 必须明确的依赖条件

**未经适配的published coroutines1.9 KLIB，单靠自定义Dispatcher/Delay仍保留forbidden WASI fallback imports。** 实际编译发现clock_time_get、poll_oneoff、fd_write、proc_exit，原adapter拒绝。不能将本轮GREEN解读为该发布物已直接Ming-only可用。

GREEN使用新sidecar内的官方coroutines1.9源码资格变体：

- source JAR的132个文件原样保留。
- 两处WASI平台actual替换，缺省event loop/time/wait/abort明确失败并产生不允许的测试标记，而非伪造时间或静默no-op。
- 四份common注解兼容副本仅为Kotlin2.3 stdlib将markerClass单值语法改为单元素数组；算法和API意图不变，有显著Derived from/Modified for MingOS通知。
- Default dispatcher、runBlocking/suspend-main及通用平台退出等能力没有实现。所有被资格验证的coroutine都拥有显式dispatcher、Delay、error handler与相应clock。

[源码来源与notice](../artifacts/compose-frame-qualification/coroutines-source-provenance.json)、[四处兼容记录](../artifacts/compose-frame-qualification/coroutines-annotation-compatibility.json)、[来源审计](../artifacts/compose-frame-qualification/source-audit.json)和[平台patch](../artifacts/compose-frame-qualification/patches/event-loop.patch)保留完整证据。Source JAR未内嵌LICENSE，已补取官方固定1.9.0标签原LICENSE并记录hash。内部API、契约及兼容性警告原样保存，不宣称完整库的生产兼容。

## 时间与调度实际覆盖

使用真正CoroutineDispatcher/Delay实现及可取消MonotonicFrameClock。**所有guest/test时间由main.advanceTo/sendFrame人工推进**；host的看门狗和elapsed统计仍是独立资源管理，不是guest帧时间。

检查包括：delay截止前/到期、delay0、withTimeout显式invokeOnTimeout、同值时间、取消与finally、队列/timer/frame-awaiters上限、异常收集后继续执行、重复与倒退frame timestamp、关闭后任务取消/新body不能运行、真实LaunchedEffect与remember state/node变化。

实际Compose流程为state0 → frame触发1 → delay后2 → 后续frame后3，最终node/state/remember token身份保持。同值state写入无新提交；key restart取消delay；dispose取消frame挂起effect并运行finally、清空节点。

默认界限：ready queue128、timer64、frame waiters32、errors16、单次pump4096步。小容量fixture触发真实溢出。Scope-owned关闭路径已通过；不外推到任意NonCancellable用户代码、并发或长期GC安全。

main刷新Snapshot apply notifications；真正重组与apply由Recomposer执行。没有应用modified-state集合，也没有直接调用ControlledComposition.recompose。

## Runtime复用与notice时点

冻结复用Compiler2.3.20/官方Runtime1.10 port的non-packed KLIB，不触发旧port重编。Runtime树SHA为d7c45ffc48662bf69fb33d879bdffc65649f20c93bc5a780083211554950c648，notice source SHA为03886038a7e450a42002564bb6aead371bc16aed02e7cd41f5cd46e6ced6782a，与6C成功构建receipt输入一致。来源receipt、notice源码副本和每个文件hash保存在[Runtime provenance](../artifacts/compose-frame-qualification/runtime-klib-provenance.json)。

来源审计已确认冻结KLIB与旧共享port输出哈希未变；root SDK/Rust/settings/gradle、ming-compose、旧qualification和现有包均未修改。构建仅用新项目与新includedBuild，使用独立project cache和既有项目依赖缓存。

底层Runtime的strong weakref fallback、single-thread假设和不稳定pinned内部identity仍保留。本轮没有证明长期弱引用回收；显式测试clock不替代生产clock。

## Ming边界与下一步HostFrame建议

最终模块只导入既有ming_v2.random_fill及ming_v1.log，均(i32,i32)->i32。adapter仅做原random_get映射，未放宽任何WASI白名单。

[HostFrame最小能力提案](../artifacts/compose-frame-qualification/hostframe-abi-proposal.md)建议app/session-owned的monotonic_ns查询与有界frame/timer/close事件等待。frame机会、timer唤醒和真正present应区分；重复时间、close、取消、配额和剩余app预算需有明确契约。候选名称/ABI不是已实施函数，父线程统一决定生产SDK和worker实现。

## 重放

在MingOS工作区根目录直接运行冻结GREEN包：

```powershell
node probes/compose-frame/run-host.mjs green-parent-clock
node probes/compose-frame/evaluate.mjs green-parent-clock
```

源码重编与新label保存方法见[probe README](../probes/compose-frame/README.md)。已有labels覆盖自己的日志，重放前应保留原证据或使用新label。

本轮没有执行GUI、物理鼠标、Native帧呈现或系统时钟精度检查，没有重启用户Esc停止的交互，也不把6C或Linux路径验收转算为本轮真实Host计时结论。
