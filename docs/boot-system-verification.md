> 最新修复与v18/v19通过结果见 [可信系统镜像验收](system-image-verification.md)。以下保留v9及追加RED的历史记录。

# 严格隔离 Linux 开发系统：真实冷启动验收

日期：2026-10-25。此记录确认一个可启动开发原型的阶段结果。整体 MingOS 目标仍为 active，物理鼠标 / Computer Use 保持暂停。

## 追加复现 RED

使用导出的相同bundle及数据盘私有tmpfs副本进行独立复现时，Text自然完成，但Counter再次APP_CANCELLED。runner/launcher均exit0，不能作为应用自然完成的成功标准。Counter2秒wall关闭策略尚未稳定解决，暂停后续恢复测试，保留[本轮RED审计](../artifacts/strict-close-20261025/main-export-replay-red/independent-audit.json)。原盘与导出快照保持Counter8；私有副本记录了4字节写入/fsync，预期值9缺少最终SDK收据，退出后副本释放。以下v8/v9通过是准确的历史轮次结果，不外推到稳定性。

## 运行与产物身份

真实 Linux 6.12.113+deb13-amd64 由两个独立 QEMU 10.0.13 进程启动，q35 / TCG / 2 vCPU / 2048MiB；guest PID1 启动 udev、seatd、Weston DRM/Pixman kiosk，再启动全屏 MingDesktop。MingDesktop 内部管理窗口、输入、Dock 和两个真实 Kotlin/Wasm/Wasmtime worker。普通应用只使用 Ming SDK，未获得 Wayland socket、GPU 对象或启动权限。

- Linux Host SHA-256：`e5fdcb1f0841229f70ef680ad9b467cf34f42d040e5ad3b5e093216ea1e20108`。
- 精确 bundle.json SHA-256：`0edec108be81468c6ce1429b8eac34139906db4fcd06f30f9641e90d05d40b81`。正式两轮直接复用已通过 v8 短诊断的不可变 bundle，没有重建。
- review SHA-256：`87e54454847cf2974f357a3e0a08360c5e01670ce3b4de4ddfe218debda66fd0`。
- 数据盘 UUID：`1bb342cc-5448-4ae6-8bd6-108a15b2cafc`。保留旧失败轮写入；独占使用前以只读 debugfs 读取 `counter-main/slot1`，基线为6。
- Counter Wasm SHA-256：`6ac9a58c8d765f3deeeacc38dbbda564216e5a2abf3f4f250cfeb7d449dc6b30`；Text Wasm：`99f862f47ee56367875fc16190484d2e7138b96f0239eee56aa825d316f5a1ca`。两个包逐字节复用原冻结；本轮未重新编译 Kotlin。

## 正式两次冷启动

| 项目 | 第一次 | 第二次 |
| --- | --- | --- |
| 实际 bootId | 4e300428-5945-46ea-a764-8ae605398226 | 2667d9d0-3077-4a7c-9d00-93217716d37e |
| 实际 sessionId | ming-209-1792935239646784818 | ming-207-1792935385509743736 |
| 应用 Counter | 6→7 | 7→8 |
| guest worker PIDs（各自boot命名空间） | 214、212 | 211、210 |
| 两应用 error / FrameClock | 均null，Close=1、Cancel=0 | 均null，Close=1、Cancel=0 |
| Main独立原始证据检查 | 23项通过 | 23项通过 |

每轮从实际ready/presented状态开始，通过QMP虚拟输入聚焦姓名框、输入mingos、点击POWER；首次ABS后必须取得当前boot/session/DesktopPID及新logOffset的Wayland pointer enter或motion/frame收据，随后发送独立的down/up事务。没有固定sleep或输入重放替代该握手。

最终Text日志640=-1074040583、641=-543219588，对应mingos与你好 MingOS；642表明正常返回。Counter的9304/9305证明前值/后值，9306..9310证明写4字节后读取与值匹配；未授权slot2被拒绝。9325/9326及9321/9322/9324均为1，9323=0，证明finally、dispose和正常返回。截图仅作视觉核对，文字结论使用精确SDK事件。

两应用实际UID/GID为20001/20002，独立namespace/cgroup；memory.max=512MiB、pids.max=64、ready后cpu.max=25000 100000。两轮应用OOM计数为0，关闭后cgroup pids.current=0。guest独立进程检查确认无worker后，才运行内存、pids、CPU三个实际资源压力case并验证reap。随后sync/unmount数据盘，内核Power down。两轮QMP均接收到guest-shutdown，没有发送quit、system_reset或system_powerdown。

正式pair和Docker命令实际退出码均为0。pair共294.535秒；这是TCG测试总耗时，不是系统性能或帧率指标。

## 本轮修复和反例

v7原记录保留：pointer握手和kernel关机成功，但两应用APP_CANCELLED、events/packageId为null，不能当自然完成。Host对Strict的固定关闭wall grace从200ms改为2秒，Off仍200ms；重复Close/Shutdown不能续期，不改变fuel/CPU/内存/pids或Close后零授信规则。唯一生产文件为ming_desktop.rs。原timer RED、GREEN、独立源码复核及包绑定复核全部保留。

当前Windows全功能含硬件回归394/0/0；Linux release allfeatures/offline构建成功，Linux CPU/边界218/0/0。旧minimal、长时空闲、GPU/字体/阴影及容器Wayland证据各自保持原冻结范围。

## 证据入口

- [Main独立pair审计](../artifacts/strict-close-20261025/main-pair-v9/independent-pair-audit.json)，包含各boot审计路径和原始文件SHA。
- [Boot原始pair结果](../artifacts/boot-data-text-20261025/pair-v9/run-data/result.json)、[正式执行命令](../artifacts/boot-data-text-20261025/pair-v9/formal-pair.command.json)、[启动前只读基线](../artifacts/boot-data-text-20261025/pair-v9/pre-pair-baseline.json)。
- [Host1](../artifacts/strict-close-20261025/main-pair-v9/boot-1/host-outcome.json)、[Host2](../artifacts/strict-close-20261025/main-pair-v9/boot-2/host-outcome.json)由Main按base64与连续byte offset独立还原，原始guest.jsonl未改。
- [实际QMP画面](../artifacts/strict-close-20261025/main-pair-v9/boot-2/qmp-input-confirmed-02.png)与同目录scanout-provenance.json记录原始PPM到PNG的无损转换及SHA。两轮该画面字节一致，显示mingos、你好 MingOS、caret、双窗口、POWER和Dock。
- [冻结源码复核](../artifacts/strict-close-20261025/review.md)、[payload独立复核](../artifacts/strict-close-20261025/payload-review-v3.md)、[新包manifest](../artifacts/system-payload-20261025/data-text-v3/payload-manifest.json)。

## 验证边界与后续

本轮是带被动guest诊断的虚拟输入kernel资格，显示路径为CPU/softbuffer + Weston/Pixman。Windows硬件GPU、历史Linux容器、这里的虚拟DRM和物理硬件/输入分别记录。没有把它称作Linux物理GPU、物理IME、无诊断运行或完整日常系统验收。

完整系统仍需覆盖实际kernel中的窗口/launcher与故障恢复交互、更多平台服务及对应边界；更广的日常任务与硬件范围以需求文件为准。可启动bundle字节、原盘快照和复现命令已导出到[带语义门槛的交付目录](../artifacts/boot-data-text-20261025/final-bundle-v9-gated/README.md)；当前已知关闭RED与验证边界见其说明，最终状态见[当前状态](../artifacts/current-system-status-20261025.md)。
