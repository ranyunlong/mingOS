# Persistent Install Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Only use subagents when separately authorized, and respect file ownership.

**Goal:** 用户通过可信安装入口安装应用，正常关机后从同盘冷启动，能从原生 Dock 启动持久应用；第二轮不重新安装。

**Architecture:** 新增封闭的 `persistent-install` profile，registry/content/trust/catalog 共处数据盘私有根。首次整体发布；已有状态严格重开；新安装以 pending 标记、双文件同步和交叉校验保护，不能证实一致时当前进程拒绝新 worker，跨进程阻断须有已确认持久化的标记。两轮使用同一正式 review 预审的不同输入，均经现有 pointer-ready 机制执行真实 Dock 点击。

**Tech Stack:** Rust 1.99.1、现有 Rust/Wasmtime Host、Python boot tools、Linux ext4、固定禁网 Docker 构建环境、QEMU/Weston/Pixman；不新增外部依赖。

**Spec:** `docs/superpowers/specs/2026-11-03-persistent-install-design.md`。

## Global Constraints

- 持久根 `/data/mingos/install-root`；catalog 为根下 `catalog.json`，不回落到 `/opt`。
- 已有根缺失/损坏内容一律拒绝；只有固定第一轮、marker1、正式根完全不存在才能初始化。
- registry/catalog 各自原子替换不等于跨文件原子提交；pending 遗留或回滚未确认时当前进程拒绝新 worker。unlink 后目录同步失败须有限补偿并分别记录尝试/确认；补偿未确认时报告 `commit-uncertain`、`persistentBlockConfirmed=false`，不能保证跨进程阻断。
- 第二轮包/trust/catalog初始化写入计数为0，不运行写入 fixture、不提供安装源、不执行 `--events`。
- 资格catalog的`autostart`严格为`[1]`；两轮Dock派发前app2无既有实例/worker/launch，派发后才首现gen1和PID，不能以restore/focus冒充首次启动。
- 不改旧冻结 profile、历史 evidence、4应用上限、SDK6、Strict关闭/资源预算或5秒pointer等待。
- 普通 Dock 沿用当前原生 down 触发 Launch 行为；INSTALL/ALLOW沿用现有呈现帧按下/释放约束。
- 物理鼠标专项继续暂停；`finalSystemAccepted=false`。
- 本计划是待执行清单；文档中的命令和预期不代表已经运行或通过。新的真实 VM、卷、构建和盘动作在执行时核对已有授权及具体预算。

## 文件与职责

| 文件 | 责任 |
| --- | --- |
| 新增 `crates/ming-app-host/src/persistent_install.rs`，注册于 `src/lib.rs` | 有界初始化契约、只读严格重开、状态快照、pending 生命周期与一致性校验；不是通用事务框架 |
| `src/package_install.rs` | 只增加窄的活动记录快照/精确恢复能力，复用现有锁、验签和内容格式 |
| `src/desktop_catalog.rs` | 新父侧参数、持久模式加载约束、有界catalog编码与精确字节恢复 |
| `src/ming_desktop.rs` | 新包安装提交/回滚与持久收据、禁止未支持 mutation；保持旧模式 |
| 新增 `examples/prepare_persistent_install_fixture.rs` | 只在明确首次角色初始化；后续只读校验；不复用遇错重装逻辑 |
| 新增 `tests/persistent_install.rs`；现有 `tests/{package_install,desktop_catalog}.rs`、native/session单测 | 文件/进程重开、失败边界、原生Dock路径和旧行为回归 |
| `scripts/boot/{bootlib,payload_profiles,bundle,guest,vm,cold-boot}.py` | 新封闭profile、两输入hash/轮次绑定、持久catalog启动、收据；旧profile不变 |
| `scripts/boot/{test_profiles,test_boot,test_infra}.py` | profile、runner argv、轮次/marker/hash、raw收据与输入边界 |
| 新 artifact 目录中的准备driver、checker及其tests | 构建/预审输入、分轮raw audit与两轮内容相等证明；不改冻结旧checker |
| `docs/continuous-development.md` | 只在验收后记录真实状态和限制 |

以下 Rust 命令在已确认可用的固定 Linux Rust 1.99.1 环境中执行；本机 macOS/Windows 过滤后的通过不能替代 Linux 断言。Python 使用支持 `hashlib.file_digest` 的 bundled Python 3.12+，通过明确解释器路径调用，不依赖系统 Python 版本。

### Task 1: 严格状态模型、首次发布与只读重开

**Files:** 新增 `persistent_install.rs`、`tests/persistent_install.rs`、fixture example；必要修改 `lib.rs`、`package_install.rs`、`desktop_catalog.rs`。

- [ ] 写失败测试：全新根初始化后仅有app1，独立进程重开可解析同签名/指纹；已有空根、任一必要文件缺失、错误权限、符号/硬链接、重复key和坏签名均拒绝且原字节不变。
- [ ] 运行 `cargo test --offline --locked -p ming-app-host --test persistent_install -j1`，确认失败原因是缺失契约，而非 fixture/工具缺失。
- [ ] 实现严格固定布局和 schema、最多4应用的活动记录快照、active/catalog集合双向一致检查；资格catalog的autostart精确为`[1]`且安装app2不改变它。不能把 load/verify 错误转换为 NotFound；所有已有模式重开禁止写内容。
- [ ] 实现首次 staging：写trust、只安装初始包、写catalog和初始化契约；完整同步/回读后 no-clobber rename 并同步父目录。已有正式根或遗留staging拒绝初始化，不自动清理。
- [ ] fixture example 显式接收 `initialize-first` 或 `open-existing` 角色并验证参数；不能隐式“缺包即安装”。输出身份/hash/动作计数收据。通用严格校验原语可重开初始化后的仅app1状态；资格的boot2角色则必须找到已提交app2，否则拒绝。
- [ ] 增加原子发布失败和已有状态漂移测试，重跑上述目标及 `--test package_install`、`--test desktop_catalog`；检查首次与重开模式均不会写app2。

**完成证据：** 初始化正例、独立进程重开、每个损坏反例的前后内容清单及无worker证据。该任务不启动QEMU。

### Task 2: 新包 registry/catalog 提交边界

**Files:** `persistent_install.rs`、`package_install.rs`、`desktop_catalog.rs`、`ming_desktop.rs`；对应Linux测试。

- [ ] 写失败测试：registry成功/catalog失败须恢复精确旧状态；catalog rename后父目录同步失败、registry回滚失败、catalog恢复失败、pending删除同步失败均不能宣告成功或启动新worker。
- [ ] 运行聚焦测试，确认当前忽略rollback错误/缺少pending保护的行为被反例捕获。用确定性测试注入，不靠改变共享目录权限或概率时序。
- [ ] 实现新父侧 `--persistent-install-state` 参数；拒绝相对/重复/冲突root及非根下catalog。普通`--install-root`保持原行为。
- [ ] 安装前保存有界精确快照、准备候选catalog、持久化最大4KiB pending；按规范执行registry→catalog→回读校验→删除pending并sync→发布内存catalog和commit收据。
- [ ] 失败时按快照恢复并验证，任何不确定结果产生明确失败并封闭当前进程的新worker启动；不要吞错误。unlink失败后检查原pending存在性与内容；unlink成功但父目录同步失败或标记存在性未确认时，有限尝试以`O_EXCL|O_NOFOLLOW`重建同一pending并完成文件sync、父目录sync及回读，分别记录补偿尝试和持久化确认。独立进程遇pending必须拒绝，不自动回滚或补建。
- [ ] 加入确定性故障测试：pending unlink已成功、首次父目录sync失败、重建与全部sync成功，须当前进程失败、无installed/无新worker、`persistentBlockConfirmed=true`，并由独立进程重开证明因补偿marker拒绝；另覆盖unlink失败且原marker仍在与namespace已无marker两个分支。
- [ ] 分别注入补偿create、文件sync、父目录sync和回读失败，证明当前进程失败、`commit-uncertain`与`persistentBlockConfirmed=false`且无installed/无新worker；不得断言新进程一定拒绝，不得把持久写不可用归为已保证持久阻断。测试须检查无虚报“标记已保留”或成功，也无自动重试循环。
- [ ] 新持久模式在写入前拒绝未接入此保护的update/uninstall/purge；测试普通模式原行为不变，S公共管理不扩大本阶段资格。
- [ ] 复跑 `cargo test --offline --locked -p ming-app-host --test persistent_install -j1`、`--test package_install`、`--test desktop_catalog`，以及 lib 的 `install_catalog`、`input_backpressure_tests` 和 `desktop_native::input_tests`，每个过滤器单独命令并检查实际匹配数。

**完成证据：** 成功仅在两份状态同步且pending已同步删除后出现；可恢复错误恢复旧快照；不确定结果无新worker。持久阻断已确认的分支须独立进程拒绝；补偿不能确认的分支须准确报告未知，不声称pending仍在或跨进程阻断已覆盖。不得将此项写成断电自动恢复。

### Task 3: 封闭boot profile与两轮预审输入

**Files:** `scripts/boot/{bootlib,payload_profiles,bundle,guest,vm,cold-boot,test_profiles,test_boot,test_infra}.py`。

- [ ] 写RED测试：新profile只接受固定持久根/catalog、新父侧参数、strict/data/state与冻结helper；两份输入hash必填，单hash回落、未知轮次、重复/错序角色、输入互换和漂移均拒绝。
- [ ] 实现`persistent-install`分支并使用独立qualification标识 `persistent-install-pointer-ready-v1`；保留旧profile分支及全部policy/instrumentation边界。
- [ ] 正式review同时包含`inputScripts.boot1Sha256`和`boot2Sha256`。cold runner固定选择两份输入，vm验证相应hash并写入`bootNumber`、`inputScriptSha256`；不能在第二轮修改review/bundle。
- [ ] 为新profile固定传入guest轮次kernel参数，guest核对磁盘marker：boot1/marker1才能初始化；boot2/marker2必须已有根。错误在desktop启动前拒绝，旧profile命令行不变。
- [ ] guest从持久catalog和`--persistent-install-state`启动；boot1提供可信安装源，boot2不提供；两轮都不传`--events`。第二轮只能调用严格只读validator，不能调用写入fixture模式。
- [ ] 在guest的初始化/重开前后、桌面关闭后写出有界快照收据；固定schema区分稳定`content`与按轮变化的`envelope`，拒绝未知/缺失字段与重复key。content包含catalog/registry/trust/bootstrap hash、按package ID排序的应用身份/hash/受控相对路径和完整受控文件枚举；明确锁/evidence排除规则，pending必须不存在。envelope绑定各轮boot/session、role、marker/previousMarker、disk UUID、绝对root/catalog、阶段、动作计数与状态。封闭profile校验路径、readback及同步，记录到原始guest stream，不只写派生摘要。
- [ ] 用 `PYTHONPATH=scripts/boot <bundled-python> -B -m unittest test_profiles test_boot test_infra -q` 运行相关测试，再运行现有pointer/diagnostic lifecycle目标；检查旧安装及update profile仍按旧hash契约拒绝漂移。

**完成证据：** 不启动CPU的argv/输入选择测试、初次与重开分支反例、旧profile回归。不增加通用InputPlan分支语言。

### Task 4: 原生Dock启动与独立raw checker

**Files:** 原生/session现有测试；新 `artifacts/persistent-install-20261103-prep/` 下输入、review草稿、stage driver、`verify-persistent-install-evidence.py`、`test_persistent_install_evidence.py`。

- [ ] 在原生测试中命中两应用Dock的普通启动区，证明down通过现有`DesktopAction::Launch`启动app2；配对up不触发安装/管理动作。核对 `(680,770)` 与当前geometry，避免S/U/D区域；不改Dock释放语义。
- [ ] 准备boot1四步：INSTALL/ALLOW/真实Dock app2/POWER；boot2两步：真实Dock app2/POWER。Dock步骤均包含pointer+left down/up，expect对应app2 gen1 running，关机前requireApps `[1,2]`。
- [ ] 输入中不含script launch、30s计时动作或action安装；审核freeze后在同一review写入两份hash和准确预算，review草稿保持未批准状态直至主代理实际复核。
- [ ] 新checker复用旧raw审计基础但采用分轮规则，不改旧冻结checker。要求boot1 mode16呈现/当前提示确认、持久commit及真实Dock→launch；boot2 opened-existing、无installed/updated/seed/INSTALL输入、真实Dock→launch。
- [ ] 两轮Dock派发前基准snapshot均要求app2无既有实例、worker或任何launch/generation历史；raw中首次app2 launch/gen1/worker PID/ready须在该步实际down派发之后。校验catalog.autostart精确为`[1]`，合法按钮后仅restore/focus不构成Dock启动资格；保持产品原有restore行为。
- [ ] 顺序判定必须用raw中因果绑定的QMP派发、同boot/session原生输入收据、guest stream offset与前后snapshot锚点串联Dock down和首次launch/gen1/PID/ready。host monotonic与guest uptime为不同钟域，禁止直接比较或相减；加入“时间戳数值看似有序但缺少因果绑定/锚点”的拒绝反例。5秒预算只沿用同一host钟域的ABS发出时间计算。
- [ ] checker绑定receipt的boot/session/PID/offset/surface、5秒ABS发出起算、QMP精确输入及成功回复；解析完整stderr重放pointer、核对按钮双沿；所有进度snapshot必须存在于raw guest states。
- [ ] checker只精确比较`boot1.final.content == boot2.preDesktop.content`，包括registry/catalog/trust/bootstrap/manifest/Wasm hash、UI ID/package ID/version/fingerprint、相对路径和完整内容枚举。独立校验各轮envelope与raw绑定、不同boot/session、正确role/marker链、相同disk UUID/固定绝对路径、正确阶段及boot2零初始化写入；绑定worker generation/PID/ready/reap及自然关机，不能任意忽略字段。
- [ ] 增加快照专用控制：content相同且boot/session/role/marker合法不同的两份收据必须接受；互换envelope、复用boot身份、错误role/marker、非零boot2计数、任一稳定hash/path/ID/version/fingerprint/枚举漂移，以及未知/缺失字段均拒绝。
- [ ] 写合成控制和破坏反例：换输入/角色、伪no-write摘要、补包/重装、hash漂移、缺pending处理、错误Dock坐标或无按钮、app2先autostart再合法Dock点击只focus、Dock前已有app2 launch/worker历史、缺commit、错PID、raw截断/offset、QMP按钮过早、慢ABS ACK、boot2新增installed trace均拒绝。明确合成正例不代表真实执行。
- [ ] 运行新checker unittest和既有raw-audit tests，独立复核冻结代码；在任何VM前形成可审阅的准确payload、预算和命令。

**完成证据：** 可复现的checker反例、原生真实事件路由测试、冻结且可审阅的两轮输入。尚不宣称持久启动通过。

### Task 5: 新构建、两轮真实cold boot与独立验收

**Files:** 新 `artifacts/persistent-install-20261103-qual1/`；验收后更新 `docs/continuous-development.md`。

- [ ] 确认当前固定Docker镜像、Rust版本、source-v2 probe、离线依赖和准确用户授权/预算；创建独立新目录/任务卷/数据盘，保留已有qual1和失败现场。
- [ ] 冻结源码输入并记录before hash；禁网以非root构建Host及新fixture helper，运行Linux release定向测试、all-targets及all-features/all-targets check；记录所有ignored及平台范围。
- [ ] 独立验证签名fixture、篡改负例和输入/二进制hash；构建新bundle，记录kernel/initramfs/manifest/review identity，不复用旧资格冒称新Host已验。
- [ ] 固定运行两次顺序QEMU，每次900秒、2vCPU/2048MiB/TCG、禁网，stage上限2000秒；保留strict worker预算和现有8MiB诊断stderr界限。失败立即保留原盘/日志/容器，不自动重试、格式化或补发输入。
- [ ] boot1证明只初始化app1、安装确认及持久commit、从Dock启动app2、两worker自然关闭/回收、持久快照、sync/unmount及kernel PowerDown。
- [ ] boot2证明不写安装内容、不安装、不重建catalog、不依赖候选源，严格content快照等于boot1最终状态且各轮envelope分别正确；实际Dock启动同包版本/指纹app2、ready和reap，正常关机。
- [ ] 独立重放新raw checker；核对两轮同bundle/同disk、不同boot/session、marker1→2、previousMarker、实际data盘内容路径、所有原始hash和source前后不漂移。保存checker完整输出及原始argv/exitCode。
- [ ] 只在以上通过后记录 `persistentInstallNormalRebootVerified=true`、`dockLaunchVerified=true` 和 `finalSystemAccepted=false`；更新阶段记录，明确仪表/TCG/虚拟输入、开发trust、无断电恢复、无物理鼠标资格等限制。
- [ ] 最后运行 `git diff --check`，核对实际改动范围和证据索引；报告真实执行的验证、未通过项和外部阻碍。是否提交由主代理既有授权决定，本计划不自行授予提交、部署或发布权限。

## 实施前复核点

1. 初始化根是一次完整目录发布，现存空目录也不能播种；没有“错误即首次”的分支。
2. 新模式对未保护的mutation明确拒绝，不能让update/卸载绕过pending契约。
3. 两份输入、角色、guest marker、review和bundle形成同一不可变链；第二轮不能靠helper修复而通过。
4. Dock真实输入替代timer launch；autostart严格为`[1]`，两轮app2首个gen1/worker/launch均出现在Dock派发后；新checker不要求boot2再次安装，也不接受只有成功summary或restore/focus。
5. pending unlink成功但父目录同步失败须补偿；仅持久化确认后声明跨进程阻断，补偿失败明确报告未确定且不得计为该故障持久阻断已覆盖。
6. 仅稳定content跨轮相等，boot/role/marker等envelope各自严格校验；不得用整份收据相等或任意删字段替代。
7. 任一错误不能把未确认状态写成installed/normal；本阶段退出条件始终只限正常重启。
