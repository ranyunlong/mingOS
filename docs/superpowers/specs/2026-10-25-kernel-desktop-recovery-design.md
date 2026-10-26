# 真实 kernel 中的桌面关闭与 Dock 重启验收

日期：2026-10-25。范围：把已有 MingDesktop 生命周期放到当前严格隔离 Linux guest 中验收；不是新应用能力或新运行时接口。既有data-text-v3 payload、review-v5与v8/v9成功结果保持不可变。

## 依据与选择

原boot计划Task4明确要求QMP窗口/launcher/关闭恢复。v9证明输入、自然关机、隔离与持久数据；7C已有Windows/Linux容器中的关闭/故障/重启。它们没有证明当前kernel中关闭一个应用后仍可由Dock启动新的实际worker。

采用一个独立instrumented profile与新review/input脚本，复用相同Host/Kotlin字节。测试只用QMP虚拟输入和现有Ming shell的关闭按钮/Dock；不进行物理Computer Use。不通过SDK或包名授予应用跨应用启动权限。原始磁盘继续保留，下一轮只读取得新基线。

## 流程与完成条件

1. 两app实际ready/presented，记录Counter与Text的PID/generation/window、UID/namespace/cgroup。
2. 用原pointer-ready握手聚焦Text并输入mingos，确认本代source前进。
3. 点击Text红色关闭按钮。要求Text generation1自然完成（640/641/642、Close=1/Cancel=0、error=null），旧PID被reap且cgroup pids0；Counter仍为原PID/generation并持续可呈现，没有被关闭或重启。
4. 点击Dock中的Text入口。等待Text generation2的新PID、新WindowId及匹配presented/inputReady；generation1记录保持可审计。核对独立的Strict收据与旧namespace/cgroup清理。初值应重建，不复用前代编辑状态。
5. 向generation2输入与第一代不同的固定文字，验证精确SDK hash，旧代事件不串入新代；中文默认备注保持。
6. 点击POWER，要求Counter及Text新代自然完成、无残留worker、数据directory fsync、guest sync/unmount和kernelPowerDown；Counter只增加一次。

所有pointer操作继续ABS→当前boot/session/DesktopPID、新logOffset、同root surface、坐标容差1px、enter/motion+frame收据→down/up。每一步按实际状态推进，不用固定sleep、坐标试错或重放掩盖失败。

## 最小实现范围

Host与SDK先保持冻结。Boot所有者先检查现有InputPlan的expectation能否表达关闭终态和generation2；确有缺口才增加有界的明确app-state/generation检查，配负例与正例，保持旧闭合profile不变。新增profile、验收脚本和新bundle均记录来源SHA。

优先只验自然关闭→Dock重启；外部强杀/崩溃注入是下一份独立profile，不能把自然关闭称作异常崩溃恢复。若当前冻结Host发生可复现生产缺陷，再由主线程读原始错误、RED、最小修复和重新冻结构建。

## 独立核验

主线程直接审raw guest.jsonl/QMP/Host结果，按连续byte offset无损解码；逐代核对身份、文字、生命周期、隔离、清理和兄弟应用存活。实际QMP scanout作视觉证据，不代替SDK事件及进程证据。保存全部RED和失败盘，不清零旧数据。通过后继续kernel异常故障恢复与剩余平台服务；不据此结束整体goal。

2026-10-26续记：当前复用已验cached系统Host ca6f970aedd07483b24fec648fbaf36cb0c04601e88bfcdf066a62c906e918c4 和全部原Wasm，完整输入/同盘冷启动/导出复现已过。新Boot expect.apps只做ID/generation/state观察，双平台44通过，几何按冻结代码核对；v20私有Counter10快照正在恢复资格，整体goal仍active。原始旧包与RED保持。
