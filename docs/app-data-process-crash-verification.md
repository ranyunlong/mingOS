# AppData 写入进程崩溃与恢复验证

日期：2026-10-27。范围为 Linux 上生产 `AppDataRoot` / `AppDataGrant` 实现的真实进程强杀和重新打开。
验证工具是 `crates/ming-app-host/examples/app_data_crash.rs`，使用最小 features 构建；生产 `app_data.rs` 源码哈希与本轮开始前一致。
本轮不经过 Wasm、SDK RPC 或 MingDesktop，不替代整机恢复、kernel/QMP、物理输入/GPU或断电耐久性验收。

## 场景与结果

每个场景使用独立的目标 key 和兄弟 key。先提交65536字节的旧值，再启动持有生产存储能力的独立进程。
数据包含固定头、序列号与逐字节确定的内容，避免仅检查长度就漏掉半写、混合值或旧新片段。

| 场景 | 实际执行 | 崩溃后读回 |
| --- | --- | --- |
| 观察到写进程的临时文件后发送 SIGKILL | 4次 | 4次均为完整旧值，序列0 |
| 生产 write 返回且确认目录同步，完整提交回执被观察后发送 SIGKILL | 4次 | 4次均为完整新值，序列1 |

临时文件的观察不能确定信号到达时的精确系统调用位置，也不保证当时是部分写或 precommit；本轮结论由终止后实际读回的数据支持。
每轮强杀后另启一个新OS进程：重新打开root、读slot1、写入并回读slot2，随后正常退出。保留slot1而不是覆盖它，使导出的文件仍包含实际崩溃后值。
新进程退出后，控制进程再次核对slot1、slot2和兄弟key；8个强杀进程与8个恢复进程全部确认回收。

最终结果：

- 8/8场景通过，16个不同子进程确认退出；强杀状态为SIGKILL，恢复状态为exit0。
- 所有目标slot1、恢复slot2与兄弟slot字节完整，文件为UID501/GID20、0600、单链接，目录为0700。
- 4个孤儿临时文件实际保留。恢复前后比较完整字节，以及长度、mode、UID/GID和链接数；没有自动清扫。
- 离线审计直接读取导出tar内的真实文件字节和权限元数据，90/90检查通过。
- 13组内存中构造的反例均被审计拒绝，覆盖损坏值、丢失已确认提交、恢复值截断、兄弟数据被改、错误信号、缺少场景、非私有文件、漏报文件、孤儿丢失/属组改变、容器退出超时、宿主等待超时和清理未确认。
- 最终工具实际拒绝0755根目录与已使用的测试根目录，2/2保护条件符合预期。

## 回收与运行边界

工具只对自身持有且未确认回收的 `Child` 请求kill。`try_wait`确认退出后才设置reaped，不用超时伪造退出状态。
正常回收和错误清理均采用两秒有限轮询；未确认回收时报告错误。独立复核先发现阻塞wait，真实单进程回归先RED，再以有限等待得到1/1 GREEN。
复核又发现恢复前metadata未进入比较，最终快照已包含完整字节及上述五项属性；读取会改变的atime不纳入比较。

容器内有30秒timeout及2秒后kill；宿主运行器对Docker客户端另设35秒等待期限。超时、非零退出、运行错误或清理未确认均不接受为通过。
宿主超时后只尝试清理带本任务标签的确切容器；内核I/O卡死时不承诺强制回收已经完成。实际的永久I/O卡死并未在本轮注入，回收期限分支用真实存活子进程检查。

使用新建卷 `mingos-app-data-crash-20261027-48cb8407`。初始轮、中间轮和最终轮分别使用 `/data/session`、`/data/final`、`/data/verified`；前两轮目录和对应源码/ELF/结果均保留。
最终目录属UID501/GID20、0700，实际程序以UID501、无capabilities、无网络、2CPU/4GiB/pids256运行；root只用于新卷目录的准备，未挂载原Counter数据卷或原系统盘。
场景使用不同key，不能据此宣称同key无限次崩溃、孤儿物理配额回收或整盘冷启动已验收。孤儿处理边界沿用[既有AppData合同](superpowers/specs/2026-10-25-app-data-design.md)。

## 复现与证据

Linux环境构建与启动示例：

```sh
cargo build --offline --release -p ming-app-host --no-default-features --example app_data_crash
cargo test --offline --release -p ming-app-host --no-default-features --example app_data_crash
# root须为本用户拥有、已准备、绝对路径、0700且为空的专用测试目录。
target/release/examples/app_data_crash /absolute/private/empty/root
```

本轮固定Rust镜像、具体卷与运行参数见 `artifacts/app-data-crash-20261027/configuration.json`。
工具会拒绝复用已有数据的root；再次执行需要准备新的专用空目录，不能直接覆盖已冻结结果。

证据目录 `artifacts/app-data-crash-20261027/`：

- `verified-build.log`、`reap-red.log`：最终最小features构建/单测和回收期限RED。
- `run.py`、`run-status.json`、`run.stdout.json`、`run.stderr.log`：宿主运行器、真实退出码及原始结果。
- `probe`、`source-final.rs`、`app_data.rs`、`freeze.json`：最终ELF、工具/生产源码快照和输入SHA-256。
- `final-state.tar`、`audit.py`、`independent-audit.json`：真实终态文件和可重跑的独立审计；审计不解包到文件系统。
- `native-guards.json`、`review.json`、`summary.json`：根目录保护、独立复核与范围汇总。
- `initial-*`、`pre-metadata-*`及对应源码快照：保留的中间版本证据，不替代最终版本。

本轮只新增开发验证工具及文档，没有调整生产存储实现、请求预算或关闭期限。
原持续会话1800ms时序用例仍为上轮记录的RED；本轮未重跑它，也不以存储场景通过替代它，见[持续会话验证](persistent-recovery-verification.md)。
