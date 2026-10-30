# 持续会话取消与异常恢复验证

更新：2026-10-26。本轮为 Linux x86_64 release、CPU offscreen 的本机运行时推进，
不替代严格 kernel/QMP/Dock、原数据盘、Wayland 按键重复或物理输入/GPU 资格。
历史 v23 恢复验收仍为 RED。

## 显式取消的快速收尾竞态

看门狗每 10ms 检查取消。旧实现只在最终结果判断中读取看门狗记录的 `cancelled`，
已经处理 Cancel 事件的 worker 若在下一次采样前正常退出，可能仍交付成功 Outcome。

新增开发验证工具 `crates/ming-app-host/examples/supervisor_lifecycle.rs`：
真实 SDK5 Wasm 先提交 1×1 帧，由父侧驱动线程置取消标志；Wasm 仅在读取到 kind=5
的 Cancel 事件后退出。它使用原 `engine::worker`、原 IPC、Result 校验和原生进程回收，
不是模拟 WorkerMessage 的替身，也不是应用的新权限。

- 初版启动前取消检查 66/66 通过；启动时间超过采样周期，没有覆盖目标收尾窗口。
- 首帧后版本共 130 次检查，旧代码 64 次失败：取消标志 true，Outcome 的
  frameClock.cancels=1，但 error=null，证明取消已经送达而完成判断漏报。
- 修复仅在交付结果前同时核对原始 `ViewBridge.cancel`；看门狗观察到的取消仍保留。
  普通 Close 不等于显式 Cancel；timeout、Strict2s/Off200ms、fuel 和资源限额未改。
- 修复后 130/130 通过，包含正常完成、正常 Close、启动前取消、首帧后取消及 Close 后取消。

已配置项目局部 Rust 工具链后可复现：

```sh
cargo run --release --locked --example supervisor_lifecycle
cargo test --release --locked --lib --test persistent_session --test desktop_runtime --test frame_clock_host
```

第二条命令当前仍因下述原有时序用例返回非零，不应把它写成完整回归已通过。
Docker 实际执行使用固定 Rust 1.99.1 镜像、无网络/只读根、UID501、cap-drop ALL、
2CPU/4GiB，并把构建缓存迁移到任务专用 Linux volume；未修改依赖版本。

## release 回归范围

| 范围 | 本轮结果 |
| --- | --- |
| 默认 features 库测试（含原关闭/Result 保护） | 196/196，通过 |
| frame_clock_host | 7/7，通过 |
| desktop_runtime | 1/1，通过 |
| persistent_session | 10/11，未全绿 |
| 真实 worker 取消交付探针 | 130/130，通过 |

原失败项 `real_desktop_shutdown_reaps_idle_and_failure_is_isolated_to_one_app`
的三份独立结果已保存：idle 正常、fuel 返回 FUEL_EXHAUSTED，requests 在固定
1800ms shutdown 下返回 APP_CANCELLED，而非断言要求的 REQUEST_LIMIT。
本轮未调整它的时限、断言或模式。此前 debug 基线也曾复现同类失败，release 并未消除它。

单独的 30s 持续资格场景实际在启动后 3115ms 记录到 `requests=32769: REQUEST_LIMIT`；
该时间包含 startup、IPC 和收尾，不是 CPU 计量或通用性能承诺。此新场景的通过不替换原用例的 RED。

## 真实应用和故障矩阵

Counter/Text 使用前轮已冻结的真实 Kotlin/Wasm 字节，未重编译或替换为小探针。
Kotlin/SDK/Compose/Gradle 源码哈希复核不变；只有单独记录的 Rust Host 修复发生变化。
发生变化的原 Host 源码另存档，与新 Host 的源码、ELF SHA-256 分开绑定。

| 独立场景 | 结果与边界 |
| --- | --- |
| 20s 持续会话，Text 强制终止后启动新代 | 24/24；3实例，Text1失败、Text2恢复，Counter保持原PID/代际，0→1写入/readback |
| 20s，另加两代 unreachable Wasm trap | 27/27；5实例，两个APP_TRAP均隔离，随后Text输入 `recovered-alive` 精确，Counter1→2 |
| 独立30s，另加SDK RPC洪泛 | 29/29；6实例，REQUEST_LIMIT精确计数32769，仅洪泛实例失败，其他故障同样隔离，故障后输入与Counter2→3通过 |

故障注入程序是明确标注的 Wasm fixture；健康及重启的应用是实际 Kotlin Counter/Text。
所有场景均为 `desktop --duration-ms 0`，由可信 Host action shutdown 收尾。
故意终止/trap/超限实例应失败，健康实例 Close1/Cancel0、error=null、finally/dispose 完成；
全部 worker 回收。新任务私有卷的数据根0700、slot0600、UID501，最终4B值3及SHA-256已只读核对。
这些是进程持续会话和新数据卷证据，不是内核冷启动、原盘或崩溃耐久性证明。

证据位于 `artifacts/persistent-recovery-20261026/`：

- `cancellation-frame-red.json`、`cancellation-green.json` 与修改前源码/输入SHA。
- `release-regressions-verified.log`、`persistent-timing-cases/`，包括原失败场景。
- `host/freeze.json` 与冻结 release ELF。
- `native-fault-recovery/independent-audit.json`、`native-fault-matrix/independent-audit.json`、
  `native-rpc-budget/independent-audit.json`，各自的 frozen 包、输入脚本、Host 输出和最终状态。

严格 kernel 复验仍需要原冻结探针及可用工具镜像；本轮没有更改既有探针哈希门槛。


## 2026-10-27 时间线续进

父侧关闭诊断已补齐，最终版本 release 库203/203、desktop_runtime1/1、frame_clock_host7/7，真实取消探针130/130。
同一冻结 Kotlin Text 包另完成35/35关闭/重启/输入审计，包含成功写出的 SDK5+ Close 回复、Result 接受及原生 Child 回收。
这不扩大前述系统资格，也不以新场景取代1800ms原用例；具体语义、时钟边界和证据见[关闭生命周期时间线](close-lifecycle-verification.md)。

本轮另按原时限重跑 persistent_session，仍10/11。requests 在 Close 后209ms触发200ms期限取消，4ms后原生回收，没有Result接受收据；原始场景和摘要另存20261027证据目录，未替换旧RED。


## 2026-10-27 IPC 写入与扩大回归

公共 `protocol::write_json` 现在使用请求容量为8192字节的 `BufWriter`，完整JSON加换行后仍逐条flush。
新增计数测试先复现：一个小的file_length Call调用writer32次，Reply14次；合并后均为1次，精确协议字节与一次flush保持。
大消息继续分段发送，不把整条消息额外积存在内存中；新增测试覆盖超过缓冲容量的转义字符串、17字节短写及write/flush错误传播。
这是公共IPC写入优化，本轮证据不足以把它称为原持续会话失败项的根因修复。

同一SDK2洪泛包、相同2CPU/4GiB限制，交错执行两组修改前后对照：

| 版本 | 第1次 launch→REQUEST_LIMIT | 第2次 launch→REQUEST_LIMIT |
| --- | --- | --- |
| 上轮冻结Host | 3163ms | 3119ms |
| 有界写缓冲Host | 3076ms | 3053ms |

四次均在第32769次请求拒绝，全部worker回收。这是独立5s诊断，洪泛包没有正常窗口帧或兄弟应用，不替代原1800ms关机用例。
样本量不足以作通用性能承诺；它表明写入合并未消除该环境中约3秒的到限耗时。
额外一次容器资源诊断记录usage_usec=2571050、system_usec=1930481、nr_throttled=0；宿主、Docker VM及固定镜像均报告x86_64/amd64。
只读/proc采样另确认父侧正常RPC阶段约每次一读一写；采样脚本只取得父进程，未取得创建于其他线程的子进程明细，不能当作完整worker性能剖析。

原回归的1800ms关机及Off200ms关闭宽限保留；它隐含了约2s内完成32769次同步往返的吞吐门槛，不能仅从32768/min预算推得这一最低吞吐保证。
本轮再次运行仍为10/11：目标尚未接受Result即到期取消，返回APP_CANCELLED。请求预算算法、计数、原断言、时限及资源上限均未修改，继续登记RED。

扩大默认features回归最初为333通过、6失败。逐项排查后修复3个AppData测试目录的准备方式：Unix下显式设为0700，生产owner/mode校验及0777拒绝断言保持。
另补建缺少的真实Kotlin read-file和image-viewer，再执行原Host/graphics测试；原样例断言未改。

| 最终证据范围 | 结果 |
| --- | --- |
| 默认features回归合并结果 | 338/339；一次全量加受修正影响目标的定向复验，非第二次全量调用 |
| release库 | 203/203 |
| protocol | 4/4 |
| data_host | 9/9 |
| host / graphics_host | 各6/6，包含补建后的真实Kotlin应用 |
| persistent_session | 10/11，原时序项仍RED |
| 无默认features编译 | 通过，有既有未使用代码警告 |
| 新版真实worker取消交付 | 130/130 |
| 新版冻结Kotlin Text关闭/重启/输入独立审计 | 35/35 |

新版ELF SHA-256：`b2cd38e312e441dfbc65f6b8d69f59dfade049a90d71d376fc4212156af05317`。
证据位于 `artifacts/ipc-write-20261027/`：`red-corrected.log`、`green-protocol.log`、`full-regressions.log`、
`aggregate-regressions.json`、定向复验与真实样例构建日志、`throughput/comparison.json`、CPU/I/O原始收据、
`host/freeze.json`、`source-change.patch`、`cancellation.json`、`text-relaunch/independent-audit.json` 和 `summary.json`。
初次RED中有一条测试错误地假设底层I/O异常不会被serde_json包装，已先修正测试假设，再对未改实现复现唯一的写入拆分失败；两份日志均保留。
独立只读复核未发现IPC补丁的必须修复项。这里仍限于Linux CPU offscreen；严格kernel、QMP、物理输入/GPU及数据盘冷启动资格不扩大。


## 2026-10-27 存储进程崩溃边界续进

另完成生产Parent AppData API的8个真实SIGKILL场景，每轮由新OS进程重新打开并继续写入，16个子进程全部回收；终态全字节/元数据审计90/90，13组审计反例均拒绝。
生产存储源码不变，原Counter数据卷未挂载；本轮不经过Wasm/SDK/桌面，不替代前述完整应用、1800ms时序或kernel资格。
边界、孤儿保留和证据见[AppData写入进程崩溃与恢复验证](app-data-process-crash-verification.md)。


## 2026-10-27 同步IPC系统调用与CPU局部性诊断

用独立调试镜像中的签名来源strace跟踪本任务自有Host/worker，1024次诊断限额在requests=1025拒绝。
汇总read2081、write2054；没有每RPC大量mprotect/时钟/文件访问，futex51和wait4为启动/收尾等待，汇总阻塞时间不能当作CPU计量。
同环境32768次四字节裸管道往返为2.908723s；只把自有诊断父子进程限制到同一已允许CPU后为0.133774s，容器CPU quota仍为2。
在同一受控亲和性下，原persistent_session源码、1800ms时限与断言均不变，11/11通过；这是诊断，不清除原未限定放置环境的RED。
这些测量支持当前跨vCPU同步往返成本触发原失败，不证明任何硬件上的普遍性能下限，也不据此擅自加入生产CPU调度策略。
生产代码未修改，先前冻结的94文件哈希仍全部一致。证据在 `artifacts/rpc-profile-20261027/summary.json`。
原严格kernel探针与盘快照仍缺，交付goal未完成，继续等待原导出输入。

## 2026-10-29 persistent RPC 缓存与原时限回归

1800ms RED 的根因保持为无 CPU 绑定环境中的同步 pipe 往返成本，不调整
shutdown 时限、Off200ms 关闭宽限、请求上限、fuel 或资源限制。修复只作用于
persistent worker：可信父侧 Init 下发同一 maxRequests，worker 对每一次 guest
import 独立执行滚动 RequestBudget；因此缓存命中不绕过 32768 次/分钟限制。

file_length(handle) 是父侧已确认 handle 的纯查询。persistent worker 缓存其返回值
（包括 -2），上限64条；file_open 成功返回的 handle 和任何已完成的 file_close 都会删除
对应缓存项。缓存命中不产生 parent RPC；父侧继续只审计实际 IPC。worker 若因包含
缓存命中的逻辑请求达到限额，错误精确报告为 REQUEST_LIMIT: requests=N，不把该计数
伪称为 parent 已观察到的请求数。bounded worker 不启用此缓存或 worker-side budget。

新增真实 worker 用例覆盖重复查询、open/close 失效、64条目淘汰，以及小上限下缓存命中的计数。
固定 Rust 1.99.1、本地镜像、禁网、只读根、2 CPU/4 GiB Docker 环境实测：

| 范围 | 结果 |
| --- | --- |
| persistent_session（包含原1800ms场景） | 14/14 |
| ming-app-host release 完整回归 | 342/342 |

固定镜像未提供 cargo-fmt，因此无法在该离线环境运行 cargo fmt --check；没有下载或
更改工具链。此回归通过不替代严格 kernel/QMP/Dock、legacy-v1 探针、旧盘连续性或历史
hash 资格，finalSystemAccepted 仍为 false。
