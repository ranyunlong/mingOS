# 关闭生命周期时间线

日期：2026-10-27。范围为父侧关闭诊断和 Linux CPU offscreen 验证；严格 kernel/QMP/Dock、Wayland 按键重复、物理输入/GPU 和数据盘冷启动资格仍待复验。

## 收据含义

所有时间来自父进程 `Instant`，统一投影到 `Session.started` 的 `atMs`。每条收据绑定对应实例的 app、window、generation，避免关闭后窗口移除或应用重启导致身份丢失。

| trace kind | 记录位置与含义 |
| --- | --- |
| `close-request` | 首次处理该窗口的 Close，沿用同一关闭期限起点 |
| `terminal-reply-written` | SDK5+ frame wait 的 Close/Cancel 回复完成既有 `write_json`（含 flush），detail 为 close/cancel；不等于应用已经读取或处理 |
| `result-accepted` | 既有完整 Worker Result 校验通过后，保留首次接受时间 |
| `close-deadline-cancel` | 关闭宽限到期且 Result 尚未接受，执行既有取消检查 |
| `close-deadline-skipped` | 首次到期检查时 Result 已接受，保留原有收尾保护 |
| `worker-reaped` | 对应原生 Child 的 `try_wait` 实际返回 `Some(ExitStatus)`；故障后回收也记录，不等于成功退出 |
| `worker-result` | 桌面接收最终结果并 join 后的观察时间；失败时保留错误 |
| `closed` / `failed` | 桌面原有最终分类 |

收据为父侧固定大小状态，不引入 SDK、wire、CLI 或资源权限。旧 SDK 的 `event_next` 没有 `terminal-reply-written` 收据，不能把其缺失推断为未关闭。一个 worker 只保存首次终态回复；若其后又发生显式 Cancel，最终取消判断仍读取原有 cancel 标志，首次回复类型不能代表后续全部事件。

Result 状态和时间戳使用同一共享 Mutex，期限检查、cancel 置位及其决策时间仍在同一临界区。接受 Result 不清除任何已有取消。诊断的“已记录”状态只限制日志，不阻止下一次期限检查。Strict 2 秒、Off 200 毫秒和原有 fuel、watchdog、内存、pids、请求限额保持原值。

轮询读取收据，并在最终 join 后再补取一次，覆盖最后一次轮询与结果交付之间的接受/回收。trace 沿用 512 条上限及 `droppedTrace` 计数，重复轮询不重复计数。异步收据可能晚于其他事件插入 trace，分析阶段应按对应身份和 `atMs` 比较，不能假定数组全局时间有序；毫秒时间可能相等。`worker-reaped` 表示父侧观察到回收的时间，不是内核真实退出时刻或 CPU 用量。

## 本轮验证

新增 7 个库测试，覆盖首次时间戳、clone 共享、接收结果后的最终补取、重复期限检查、实例身份与时钟原点、trace 上限、失败不伪造收据，以及并发 Result/取消的时间顺序。并发测试包含 32 次实际调度，不能保证这 32 次覆盖两种调度顺序；既有顺序测试另覆盖接受前后到期和显式取消不被抹除。

先用中断前版本复现 3 个 RED：重复接受覆盖首次时间、join 后漏记 Result、日志标记阻止重复期限检查。最终版本使用相同固定 Rust 镜像、离线 Linux 缓存串行验证：

| 范围 | 结果 |
| --- | --- |
| release 默认 features 库测试 | 203/203，通过 |
| desktop_runtime | 1/1，通过 |
| frame_clock_host | 7/7，通过 |
| persistent_session（原时限与断言） | 10/11，原失败项仍为 RED |
| 无默认 features 编译检查 | 通过；有 5 组未使用代码警告，未宣称无警告 |
| 真实 worker 取消交付 | 130/130，通过 |
| 冻结 Kotlin Text 关闭/重启/输入独立审计 | 35/35，通过 |

真实应用场景使用原 Kotlin/Wasm 包，两应用同时启动，Text 输入 `mingos` 后关闭、重启新代并输入 `again`，最后由可信 Host action shutdown。三个实例全部自然关闭，Close1/Cancel0、error=null，原生回收与三组完整生命周期收据均核对；兄弟应用保持同一 PID/代际，note 保持 `你好 MingOS`。最终没有 trace/input 丢弃。这里的输入是 Host 合成动作，不是物理输入或 QMP。

| app / generation | Close→回复写出 | 回复写出→Result | Result→回收 | 回收→桌面观察 |
| --- | --- | --- | --- | --- |
| 1 / 1 | 14ms | 4ms | 12ms | 2ms |
| 2 / 1 | 7ms | 3ms | 10ms | 9ms |
| 2 / 2 | 14ms | 4ms | 12ms | 2ms |

这些是该次运行的墙钟观察，不是性能承诺，也不解释历史 kernel 下的延迟；本场景在期限内结束，没有触发期限决策。到期后的 cancel/skipped 行为由库测试覆盖。

最终执行结果及源代码/Host 哈希归档在 `artifacts/close-timeline-20261027/`：

- `red.log`：3 个原问题的失败记录。
- `final-regressions.log`、`cancellation.json`：最终版本回归和真实 worker 验证。
- `host/freeze.json` 与 `host/ming-app-host`：94 个文件的 SHA-256，含最终 ELF、Rust 源码、Cargo 配置和锁文件；本轮 ELF SHA-256 为 `0873701b80bb588dd30a2a2c444e0ad3d31b0e0313401d70095bf7d329fcefa9`。
- `text-relaunch/inputs-sha256.json`、`host.stdout.json`、`independent-audit.json` 及根目录 `audit.py`：冻结输入、原始结果及可重跑的审计。
- `review.json`：两轮只读独立复核，无遗留必须修复项。

中间版本编译因需修正 feature 类型与决策时间而主动停止，`green.log` 不作为通过证据。历史 `artifacts/persistent-recovery-20261026/` 的冻结 Host、RED 和恢复矩阵保持历史范围。


## 原持续会话 RED 的本轮证据

原用例 `real_desktop_shutdown_reaps_idle_and_failure_is_isolated_to_one_app` 按原1800ms shutdown脚本重跑，仍为10/11；idle正常、fuel为FUEL_EXHAUSTED、requests仍为APP_CANCELLED而非REQUEST_LIMIT。
requests目标实例的Close为1811ms，期限取消为2020ms（相差209ms），原生回收为2024ms，桌面结果观察为2031ms；没有Result接受收据，健康兄弟正常完成，最终activeWorkersAfterShutdown=0。
这证明该次失败在尚未接受Result的执行阶段到期；不能归为“接受Result后又取消”的旧竞态，也不修改原时限或断言来变绿。
原始结果在 `persistent-cases/`，测试日志为 `persistent-regressions.log`，本轮诊断摘要为 `persistent-audit.json`。


后续同日公共IPC写缓冲版本已另行冻结，并重做取消130/130与真实Text时间线35/35审计，见[持续会话验证](persistent-recovery-verification.md)。
本页旧ELF、时间线和源码哈希保留对应历史版本；后续源码发生修改时，不应把旧源码哈希校验失败误判为历史ELF运行失败。
