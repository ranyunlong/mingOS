# Worker Result 与关闭期限的阶段修复

日期：2026-10-26。目标保持Linux/Kotlin/Wasm/SDK-only整体验收。本次只修复已经复现的终态后取消，不更改fuel或隔离限额。

v11诊断证明Counter的finally/dispose/final logs均已执行，父进程解析并验证了Result（fuelConsumed2617270，firstError=null，resultWasError=false），仍在closingElapsed2011863us置cancel，随后因watcher取消标志丢掉Outcome。缺少native ExitStatus，不能断言该轮原生进程已经正常退出，也不把慢收尾确定归因于某个Wasmtime析构函数。该轮raw/私有失败盘均保留。

采用最小三文件方案：

- ui.rs：ViewBridge内增加父进程专用、clone共享的Result接受标记；其Mutex同时保护deadline条件检查和cancel置位，避免接受后检查/置位竞态。接受Result不清除任何已有cancel。
- supervisor.rs：只在既有完整Result验证通过后标记接受；PROTOCOL_AFTER_RESULT原有终态规则不变。继续检查native退出状态、现有watchdog和严格清理，不能以标记代替成功。
- ming_desktop.rs：2s Strict/200ms Off期限与首次Close起点不变，只在未接受Result时触发deadline取消。直接显式cancel和析构/故障kill路径不变。

无需新CLI/Options/wire/SDK配置。应用不能设置标记；没有新fuel授信、请求或CPU/memory/pids豁免。native进程若退出收尾卡住，既有看门狗仍终止，不能报告自然成功。

回归先保留旧期限行为做RED：模拟已接受Result但结果channel尚在等待reap，分别在Strict2.5s和Off250ms验证不应置cancel。另验证未接受Result仍到期取消、已发生的取消不会被后到Result抹除、显式cancel不被完成标记覆盖。随后只修条件与实际接受点，做定向及Windows/Linux回归，再以真实Kotlin/kernel/私有快照复验。

诊断先保留为历史；生产修复通过后再生成新的冻结包。原v8/v9、追加replay和v11 RED不改称通过。窗口/Dock恢复测试仍等待此根因修复。

补充：Result接受后的native退出阶段沿用桌面既有300000ms进度看门狗。它不是整个进程必须2秒退出的硬实时承诺；Close2s/200ms只约束尚未接受Result的执行阶段。原生故障探针使用1秒测试配置，不是生产超时值。


后续快速收尾修复：真实SDK5 worker已接收Cancel后仍可能在看门狗下次10ms采样前退出，
旧完成判断只读watcher标记而交付成功。最终交付现在同时检查原始显式cancel标志，
不将普通Close作为Cancel，不改变已接受Result后的期限保护。原问题130次中64次复现，修复后130/130；
完整范围与未通过项见[持续会话验证](../../persistent-recovery-verification.md)。


2026-10-27 关闭时间线补充：父侧共享状态同时保留首次 SDK5+ 终态回复写出、Result 接受和 Child 回收的 Instant。
Result 接受与期限取消仍使用同一 Mutex；取消决策时间也在锁内采集，避免解锁后的接受造成日志顺序误导。
桌面保留 Close 请求起点，并在轮询及最终 join 后读取收据，每代只记录一次，沿用 trace512 上限。
收据不等于自然成功，最终 Outcome、显式取消、ExitStatus、故障分类与 watchdog 继续按原路径检查。
完整含义与验证范围见[关闭生命周期时间线](../../close-lifecycle-verification.md)。
