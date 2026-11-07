# 当前宿主 Linux 持久卸载资格实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `dev.ming.install-demo`、版本 `1.0.0`、SDK6 完成独立 Linux 来源构建、普通卸载保留私有数据、同盘正常重启及可迁移原始证据重放。

**Architecture:** 复用 Windows 协调与 Linux 冻结、POSIX 存储、构建、runtime、pair、export 分层，新增封闭的 uninstall profile 合同。已有 update 资格、历史 driver/freeze/checker/输入及全部失败盘保持原字节。缺失的历史 uninstall prep 不能作为本路线的依赖或完成证据。

**Tech Stack:** Rust1.99.1、Kotlin2.3.20/Gradle8.14.5、JDK21、固定 Linux 镜像、Python3.12+、QEMU TCG、Weston/Pixman、ext4。

**Spec:** [原卸载设计](../specs/2026-11-04-persistent-uninstall-design.md)、[原卸载计划](2026-11-04-persistent-uninstall.md)。本计划落实其 Task6/7，并复核已实现 Task1..5 的实际证据；不把旧勾选状态或已有源码视为当前执行通过。

## 范围与不可放宽的边界

- 目标固定 app2；bootstrap app1、autostart `[1]`、签名历史包、removed 精确身份以及 `note.txt=alpha`（5字节无换行）保留。卸载不是 purge，不把本资格扩大为更新、重装、进程中断恢复或断电保证。
- 正式来源、build、runtime 独立新代；cold target 字面为空，单 writer。现有 update cold 不含本次 uninstall helper/显式测试合同，不能直接充当卸载正式冷构建。
- 正式 Linux UID/GID65534、2CPU/4GiB、pids256、禁网、只读源码/rootfs、cap-drop ALL、no-new-privileges。运行目录/锁/socket 保持现有 tmpfs 与真实 POSIX 存储门。
- guest2vCPU/2048MiB、watchdog300秒、pointer-ready5秒、每步45秒、关机余量120秒、日志8MiB不变。七步最低 `300+7*(45+5)+120=770` 秒，一步最低470秒；保留 boot900秒、监督945秒、pair1920秒、container1950秒上限，不因失败扩大预算。
- 每代 pair 只执行一次；boot1 首失败禁止 boot2，盘/日志/RED/全部原始收据封存。修复后使用新冻结和新 runtime/新盘，不续跑旧失败代。
- 物理鼠标暂停；无物理 GPU/display、guest crash、断电、损坏介质或完整系统验收声明；始终 `finalSystemAccepted=false`。

## Task 1：专用几何、输入与独立 raw checker

**Files:** `crates/ming-app-host/src/desktop_native.rs`（仅测试）；新 `artifacts/persistent-uninstall-20261107-linux-inputs/` 下的 geometry receipts、`input-boot1.json`、`input-boot2.json`、`verify-persistent-uninstall-evidence.py`、checker tests 和独立执行合同。实际路径由新 freeze 绑定，不依赖不存在的 `persistent-uninstall-20261104-prep`。

- [ ] 实际执行 `desktop_native::input_tests::persistent_uninstall_two_app_input_geometry`，要求唯一 `1/0/0/0`、退出0、唯一 `MING_PERSISTENT_UNINSTALL_GEOMETRY`，绑定实际测试 ELF/hash、构建 command01、freeze、完整 stdout 与 desktop_model/desktop_shell/desktop_native 源 hash。本次仅添加测试，未运行 Rust/Docker，不预记通过。
- [ ] geometry 应由生产函数派生并断言1280×800七点：INSTALL(1148,12)、INSTALL确认(880,478)、app2 Dock(676,766)、app2 Close(96,104)、app2 U(656,748)、UNINSTALL确认(880,478)、POWER(1238,12)。boot2 仅 POWER(1238,12)。窗口 Close 后 app2 仍在 catalog；U 仍为 index1/count2，不能提前换成单应用 Dock 布局。
- [ ] 两份输入冻结为真实 leading ABS + left down/up。Close 等当前 app2 Ready/呈现，U 等该 generation/PID 的成功 Result/native exit/reap 及非空私有 baseline；确认等 mode10 当前提示呈现。boot2 不启动 app2。核对 ABS/32768 连续 extent 与 native round 的真实坐标，不放宽精确 checker 坐标门。
- [ ] 新 checker 提供 `verify_boot(...)`、`pair(path)` 和 CLI；从完整 guest/raw stderr、QMP、Wayland 重建 parser，核对请求/双沿/当前帧/press-release/确认cause/commit、generation/PID、原始 offset，允许 commit 先于完整 up 输出。不跨 host/guest 时钟比较。
- [ ] 严格检查四阶段：boot1.pre仅app1、无removed/私有文件，初始化 actions各1；boot1.post与boot2.pre/post仅app1、精确app2 removed、完整签名历史包、唯一note.txt=alpha、actions各0。跨轮比较业务content/private，不要求不同 envelope 整体相同。boot2完整state/records/trace/进程表均无 app2 worker。
- [ ] checker 正反例覆盖原计划Task6：完整合法pair/早commit、缺双沿/错点/错帧/错PID/截断/offset断裂、私有空错多文件、隐藏项/额外目录、缺锁/历史签名失败收据、初始化计数篡改、boot2补包、同盘/bundle/marker错误、forced stop/缺sync/unmount/PowerDown。合成正例不替代真实磁盘反例或VM。

## Task 2：Linux Kotlin 来源与冷构建合同

**Files:** `scripts/boot/build-linux-host-kotlin.mjs` 及其测试；`linux_host_qualification.py`、`linux_host_build.py`、`linux_host_freeze.py`、对应测试及来源/冷构建公共入口。profile选择只接受固定 update/uninstall 合同，update既有命令/集合仍精确不变。

- [ ] 为 `persistent-uninstall-demo` 增加明确 sample 白名单与正式来源包：实际 offline target task、依赖清单、source前后稳定、raw/packaged双hash、adapterVersion/rewrites/build-info，签名和测试与最终bundle均使用同一packaged字节。
- [ ] 新包合同包含Rust all-targets依赖的read-file/image-viewer、bootstrap text-entry-demo与uninstall fixture；路径/标签使用专用显式映射，不以update new/old fixture冒充。现有只读依赖缓存可复用，缺件先报实际错误，不联网绕过正式offline门。
- [ ] 新冷构建运行原Task7 release all-targets、all-features/all-targets check、Host与 `prepare_persistent_uninstall_fixture` build；若沿用现有no-default/all-targets check亦纳入精确命令清单。不要套用update固定十命令和18/0/1/0矩阵。
- [ ] 显式运行两个ignored真实测试：`ming_desktop::input_backpressure_tests::persistent_uninstall_fixture_writes_private_data_and_closes` 和 `desktop_native::input_tests::persistent_uninstall_native_real_fixture_ready_close_reap_and_uninstall`；环境仅允许本代 `MING_UNINSTALL_FIXTURE_WASM` 和 release Host。各唯一1/0/0/0，实际Ready后Close/reap，禁止零匹配或ignored计通过。
- [ ] 核对当前真实事务故障矩阵、removed磁盘publisherSignature-only篡改、两个锁分别缺失仍无创建、真实目标/兄弟私有/公共sentinel及授权保留、隐藏项/目录/entry上限、提交后输出失败。检查all-targets实际测试名和计数，不凭旧记录重复声明。
- [ ] 两个固定Host/helper产物保持受限hardlink脱离、两时点seal和实际ELF闭包；冻结source/lock/packages、实际日志/退出/containment/inputs-after，独立核验后才能创建runtime。

## Task 3：runtime、fixture 与无 guest admission

**Files:** `linux_host_runtime.py`、snapshot/public runtime入口及测试、`bootlib.py`/`payload_profiles.py`/`guest.py`的必要专用分支、新输入资产装配与geometry执行工具。禁止修改历史driver来取得新profile准入。

- [ ] 将所有update固定profile/helper/checker/input/geometry字段变为明确两套封闭合同。生产Rust/Kotlin漂移要求新cold，不扩大SOURCE_EXTENSIONS逃逸；允许的boot Python修复逐hash/bytes复核。
- [ ] 使用已存在 `persistent-uninstall` / `persistent-uninstall-pointer-ready-v1`、`persistentUninstallFixture`；固定 `dev.ming.install-demo/1.0.0/SDK6`，校验helper、initial/new Wasm/sourceManifest、安装根、receipt及两输入hash，无旧单hash回落。
- [ ] 验证helper fresh guest数据根的lost+found/marker/no-create边界与boot1初始化，boot1.post/boot2.pre/post仅open-uninstalled。不能假定update helper的fresh guest特例自动覆盖uninstall helper。
- [ ] 正式组装专用payload/签名fixture/几何/七文件source-v2 probe证据，probe收据绑定本次cold freeze。重新观察tools manifest，包含实际Host/uninstall helper/probe的ldd闭包。
- [ ] 无guest admission执行完整checker/driver/guest controls、签名正反例、实际ELF与Python导入、tmpfs locks/fsync/socket/QMP暂停边界及pair拦截；评审实际review后生成不可变bundle。所有阶段失败单次锁存，不自动修补该代。

## Task 4：一次双启动与 portable export 闭合

**Files:** `linux_host_pair.py`、`linux_host_export.py`及专用测试/公共入口；新本代证据。复用已核验的监督与存储机制，不引用旧APFS身份。

- [ ] pair加载本代专用checker/执行合同，强boot1 gate成功后才启动boot2；同盘UUID/serial/marker链、同bundle、不同boot/session，两个正常shutdown、worker自然回收、sync/unmount/kernelPowerDown均实际证明。
- [ ] 新portable profile封闭绑定所有helper/checker/geometry/输入/source/依赖；导出真实raw/QMP/Wayland/logs、盘manifest/归档、pair结果、fixture双hash/build-info/signature及全文件集合/hash。禁止混入socket/symlink或引用旧盘hash。
- [ ] create、verify、独立replay均通过，移离原checkout/卷、任意cwd仍能校验；有界文件/JSON类型、缺新增篡改/未冻结依赖反例保持通过。保存实际归档hash及容器停止/exit/OOM/无VM进程证据。
- [ ] 只有全部实际通过才写 `persistentUninstallNormalRebootVerified=true`、`privateDataPreserved=true`、`finalSystemAccepted=false`；更新continuous-development和原卸载计划，准确记录当前来源代、raw证据、限制、历史RED及未完成scope，提交真实完成阶段。

## 当前状态

本计划及专用geometry测试为新准备工作。没有据此执行新的Kotlin/Rust构建、创建卷/盘或运行guest；没有声明uninstall正常重启、数据保留、export/replay或完整系统通过。历史失败和既有update资格分别保留，不以新文档改变其结果。

2026-11-07 实施阶段进度：独立profile/runtime/checker/export及四包真实离线来源已实现，Linux197项控制、Node18项及公共入口27/24/21项通过。真实cold、geometry ELF执行、admission、guest pair及portable实际导出仍待执行；上述checkbox暂不凭实现或合成控制勾选。详细证据见continuous-development最新阶段记录。
