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
