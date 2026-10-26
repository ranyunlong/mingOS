# Kernel 桌面关闭与 Dock 重启验收实施计划

**Goal:** 在真实严格隔离Linux guest中证明关闭一个Kotlin应用后，另一个应用持续运行，并能从Dock启动新代worker。

**Architecture:** 保持Host/Kotlin冻结，先为Boot InputPlan补足有界应用终态/代际握手，再用新review和input生成独立bundle。所有QMP pointer保持现有真实client receipt策略。旧v8/v9导出包与数据盘保留。

**Tech Stack:** Python标准库、现有Boot工具镜像/QEMU/Weston、原Rust/Wasmtime/Kotlin包。

**Spec:** ../specs/2026-10-25-kernel-desktop-recovery-design.md

## 全局约束

- 普通应用仍仅通过Ming SDK，测试不能引入跨app启动权限。
- 不修改Host fuel、CPU、memory、pids或Close策略；有生产缺陷才按原始错误/RED单独修复。
- 保留原盘和所有RED；只读读取新轮Counter基线。
- 物理Computer Use暂停；仅QMP虚拟输入。
- 当前无.git，不创建仓库或提交。原Boot代理已关闭，后续Boot写域由主线程协调。

## Task 1：应用状态/代际握手

Files：scripts/boot/infra.py、scripts/boot/test_infra.py（或独立test_input_lifecycle.py）。

- [ ] 读完整InputPlan、state_ready和现有测试，冻结当前源码SHA。
- [ ] 先写RED：关闭步骤不能由同revision、错误app/状态、缺失app或错误generation提前满足；Dock重启不能由旧代ready冒充。覆盖脚本schema错误、bool/非整型、未知字段。
- [ ] 最小实现明确的app-state/generation expectation，继续使用当前boot/session/revision约束；下一步输入仍需新代inputReady和实际presented source。不得改用固定sleep。
- [ ] 运行新定向测试及所有Boot测试，保存原RED和GREEN。不得重写旧38测试结果。

## Task 2：冻结新输入、review与bundle

Files：新的artifacts/system-payload-20261025/review-kernel-recovery-*；新的artifacts/boot-data-text-20261025/recovery-*。

- [ ] 从现有实际root/outer/Dock几何确定关闭和Dock坐标，并保留假设来源；发生不匹配先读QMP/Wayland收据，不试坐标掩盖。
- [ ] 输入脚本：两app ready→Text1输入mingos→关闭Text1→Counter原代仍活→Dock启动Text2→新PID/Window/generation2 ready→输入不同固定文字→POWER。
- [ ] 新review绑定input SHA、新Boot源码、相同Host/Kotlin及原严格限额；验收明确3个应用实例（Counter1、Text1、Text2）、兄弟存活和两代精确SDK结果。旧profile/包/review不改。
- [ ] 只读读取当前盘基线，冻结新bundle；实际短诊断和正式恢复运行各保存原始证据，不自动重试失败。

## Task 3：独立审计与继续

- [ ] Main按原始guest.jsonl连续offset独立还原，核对Text1自然完成/reap、CounterPID/generation保持、Text2新身份/独立Strict、输入不跨代、最终正常关闭和数据只增加一次。
- [ ] 核对QMP实际消费与scanout、资源压力在worker回收后、sync/unmount/kernelPowerdown及无强制QMP退出。
- [ ] 保存设计、源码/输入/包身份、测试、运行结果和边界；通过后继续独立kernel异常崩溃恢复profile及其余平台服务。整体goal保持active。

2026-10-26检查点：Task1的InputPlan状态/代际观察已经实现，当前Boot44测试通过；Task2已生成v20/v21恢复bundle并实际运行。Task3未通过，不能将上方清单视为全部完成。v21为26/30；本轮v23顺序关闭仅是独立诊断，Counter自然完成但Text两代取消，原恢复资格仍RED。原始日志、全部失败盘与v19导出快照保留。后续先补同一父侧时钟的Close请求/送达诊断，不改变限额；详见artifacts/kernel-recovery-20261026/independent-review-v23.json。
