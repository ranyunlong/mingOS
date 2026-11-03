# 持久普通卸载与正常重启设计草案

日期：2026-11-04。状态：只读调查后形成的设计草案；尚未实施、运行测试或执行本阶段 QEMU 资格。

## 目标与最小范围

用户从 Ming Desktop 的 Dock `U` 入口卸载后装的可信应用 app2，确认提示明确保留私有数据。正常关机并从同一数据盘冷启动后，app2 仍处于 removed 状态，不出现在 Dock、不启动 worker，其既有私有文件保持完整。

本阶段只把普通卸载接入现有持久 registry/catalog 一致性保护。保留 bootstrap app1、`autostart=[1]`、应用稳定 UI ID、SDK6、最多4个活动应用、单可信父进程和现有 Strict 资源边界。更新与 purge 在持久模式下继续在写入前拒绝。重新安装后复用数据的既有服务语义可以保留回归，但不扩展本阶段真实双启动资格为重新安装或更新资格。

物理鼠标专项继续暂停；`finalSystemAccepted=false`。本阶段不承担断电耐久性、进程中断后自动修复、安装中崩溃恢复、多用户、跨平台安装服务、生产密钥分发或 Linux 硬件 GPU/display 的验收。

## 已核实的现状

- `docs/continuous-development.md` 最新记录为持久安装正常重启与两轮 Dock 启动已通过。该记录的资格限定 TCG、虚拟显示/输入、pointer-ready 及资格收据，不能扩大为完整系统验收。本次调查未重新执行历史 VM 或重放历史证据。
- `ming_desktop.rs::begin_uninstall` 和 `respond_storage_prompt` 都在持久模式下拒绝卸载。普通模式已有 U 入口、已呈现帧约束、运行时 `CLOSE APP FIRST`、确认及失败重试提示。
- 普通卸载调用 `uninstall_with_data_policy(PreservePrivateData)`，成功后只从内存 catalog、autostart 和待启动队列移除应用。因此不能仅解除上述拒绝，让普通路径直接处理持久根。
- `PackageInstallRegistry::uninstall` 只将目标登记从 active 改为 removed，保留版本、指纹和内容目录；登记写入包含临时文件同步、rename 和根目录同步。`restore_records_snapshot` 能精确恢复磁盘字节和内存记录。
- registry 持有对象生命周期内的 Linux 排他 flock；当前持久事务另依赖单可信父侧、串行桌面 mutation。这个事实不构成任意同 UID 并发修改的防护承诺。
- `persistent_install.rs` 已有有界严格快照、pending 文件、catalog 精确恢复、pending 清理失败的有限补偿和不确定状态报告。`snapshot_while_pending` 的成功规则是“目标仍为活动安装”，不能原样作为卸载成功规则。
- 严格重开要求 bootstrap app1 的 UI ID、包 ID、版本、指纹和 Wasm 保持不变，且 autostart 精确为 `[1]`。它已允许 removed 登记与完整历史内容保留；`tests/persistent_install.rs::removed_records_and_complete_inactive_content_can_remain` 有相应正例。
- 当前 `ContentSnapshot` 包含完整 registry 文件 hash，但只枚举活动应用身份。仅有“app2 不在 apps”不能证明目标精确变为 removed，需要附加登记身份校验。
- 当前 fixture 的 `open-existing`、guest 的 `persistent_content` 和收据 relay 都绑定原安装资格的 app1/app2 与安装顺序。它们不能原样验收卸载后的仅 app1 状态，也不能通过放宽旧 profile 或旧 checker 来适配。
- SDK6 默认私有目录由 `default_private_session_for_package` 提供，不依赖 legacy `private_storage` 标志。不能因现有 fixture 中该标志为 false，就推断应用不能写私有数据；是否已有适合本资格的实际非空写入行为仍需核查。
- 持久安装当前用 `max(活动 catalog ID)+1` 分配 ID，并拒绝大于4的结果；`[1,2,3,4]` 卸载中间 app2 后成为 `[1,3,4]`，再次安装会错误分配5并拒绝，尽管仍有一个活动空槽。卸载最高 ID 时则会复用旧 ID。
- `Session::public_denied`、legacy `app_data.grants` 和待启动队列按 UI ID 保存状态；`app_status` 与 `state_snapshot` 也按裸 ID 查找历史记录。`AppRecord.package_id` 在 worker 成功返回后才填充，不能直接用该可选字段过滤所有 starting/failed 记录。历史记录还被 `Instance.record` 和 `NativeLaunchEvidence.record` 按索引引用，不能为清理 ID 而删除或重排。

## bootstrap 与会话边界

持久普通卸载只接受当前 catalog 中、已验签且活动的非 bootstrap 应用。本阶段正式 fixture 的目标固定为 app2。bootstrap 保护必须由父侧事务依据已验证的 bootstrap 契约再次执行，不能只按 Dock 位置、UI ID 或 UI 是否显示 U 判断。

入口和确认时都检查无目标运行代、待启动目标或尚未回收的目标 worker。仍在运行时沿用现有关闭应用提示；bootstrap 拒绝必须有明确原因，不得误报为关闭应用即可卸载。具体沿用哪个信息提示组件，在实施前检查现有 UI 模式后选择最小改动。

确认沿用当前呈现帧上的匹配按下/释放约束。再次验证同一目标的 package ID、version、fingerprint、当前活动登记、catalog 绑定和发布者签名；不从任意路径重新选择包，不接受被替换后的另一身份。取消不创建 pending、不写 registry/catalog，也不修改私有数据或授权。

持久失败封闭标志也约束卸载入口与确认；已经进入 fatal 状态的会话不得继续安装、卸载或启动任何新 worker。普通开发模式保持原行为。

## ID 空槽与当前绑定的会话状态

新增持久卸载后必须补齐最小的再安装兼容路径，避免卸载留下不能再用的活动槽位。持久安装从 `2..=4` 中选择当前 catalog 未占用的最小 ID，保留 bootstrap ID1 和其他活动应用的原 ID；只有没有空槽或活动应用已达4个时才返回容量错误。不要对现有应用重新编号，也不新增持久 ID 分配数据库。相同 package ID 的私有目录仍由包身份决定，不能按复用的 UI ID 改名或迁移。

成功卸载后才清除该 ID 的 `pending_autostart`、`public_denied` 与 legacy `app_data.grants` 等当前绑定状态；取消、可重试回滚和提交未确认时不提前清除。当前严格持久 catalog 已拒绝 legacy grant 字段，新安装准备也要求 grants 为空，因此这里不能将 legacy grant 风险描述成已复现的持久模式权限泄露；共用清理仍不得让旧 grant 随 ID 复用赋给另一包。清理内存映射不等于删除私有数据或撤销公共授权。

当前绑定的状态查询与永久会话历史分开。用一个小的、仅父侧维护的按 ID 记录起点或最新记录索引，标识本次 catalog 绑定可见的运行记录；成功新安装/重新绑定后，旧包的 closed/failed、worker PID、Ready 时间和 source sequence 不得出现在新包尚未启动的当前 state 中。优先选择只影响当前绑定查询的最小实现，具体字段名在实施时确定，不扩展公开 state schema。

历史 `records`、`trace` 与 native launch 索引保留原序。generation 继续按该 ID 的全部历史取最大值加一，不因卸载或不同包复用 ID 而归零；新 worker 使用新的 WindowId 和隔离身份。checker 必须结合绑定阶段、package 身份、generation/PID 和原始 offset 判断新启动，不能把“复用的数值 ID 必无任何历史”作为新资格条件。原安装资格的首次 app2 无历史规则不放宽。

native 的 U/S/D 按压状态另受呈现帧、位置和当前 catalog 校验；卸载确认完成后不能留下可作用于新绑定的旧按压。新增原生回归覆盖解绑/再绑定之间的旧释放，不在没有证据时重写输入系统。持久资格不传 `--events`，因此本阶段不重新定义普通开发模式中任意未来脚本动作跨卸载的语义。

## 卸载事务与可见提交

两份文件分别原子替换不等于跨文件原子事务。本设计保证正常返回成功前，registry/catalog 均已同步、交叉验证且 pending 已同步清除；失败时恢复旧状态，或明确封闭并报告不确定性。不存在断电自动恢复承诺。

复用同一持久根、同一排他 registry 和 `desktop-install-pending-v1.json` 的存在即阻断规则。卸载 pending 使用单独的严格类型，包含明确的卸载 operation、目标身份、旧 registry/catalog SHA-256 和预期新 catalog SHA-256，保持4KiB上限；不改变原安装 pending 的冻结 schema，不保存任意恢复路径或可执行指令。具体 schema 版本在实施计划冻结前确定。

可信父侧串行执行：

1. 严格快照确认当前根无 pending/update journal、磁盘登记与持锁内存相同、active/catalog 集合一致；确认非 bootstrap、目标已停机并验证精确身份。
2. 保存有界旧 registry 原始字节、catalog 原始字节和完整旧内容快照。在内存生成只移除目标的候选 catalog，保留其他应用 ID、title、绑定与 `autostart=[1]`。
3. 独占创建 pending，完成文件同步、根目录同步及身份/字节回读后，才允许第一处业务状态写入。
4. 调用普通卸载的保留数据原语，只改变目标登记的生命周期状态；再持久化候选 catalog。此事务不调用 purge、数据迁移、私有目录创建或授权变更。
5. 从磁盘回读并交叉验证：完整登记相较旧登记只有目标 active→removed，目标 version/fingerprint 不变；catalog 恰好移除目标；活动包集合精确相等；trust/bootstrap、其他登记、其他应用和全部包历史内容不变。
6. 验证并删除同一 pending，完成根目录同步；随后执行最终无 pending 快照及新 catalog 精确回读。
7. 只有上述步骤全部成功，才发布新内存 catalog、清除目标待启动项并记录 `uninstalled` 与卸载 commit 收据。成功不触发 app2 启动，也不隐式重新编号。

完整登记转换校验应复用 `package_install.rs` 内部解析能力；如需要增加 API，只增加一个有界的父侧转换校验或快照 helper。不要在桌面层复制 registry 格式解析，也不要将整个包管理器改造成通用事务框架。

`ContentSnapshot` 继续保持已有字段。新卸载收据另附已验证的目标 removed 身份、操作与确认因果，绑定当前 registry hash，避免改变旧安装收据解释。最终一致状态允许 app2 的历史 manifest/Wasm 仍在 `files` 中，但不在活动 `apps` 中。

磁盘事务成功返回与收据/状态输出是两个不同边界。第6步全部完成后，磁盘卸载已提交；随后 `uninstalled` 日志、commit 收据、state 文件或原始 stderr 输出失败，属于会话或资格失败，不能回报 `rolled-back`，不能显示 `UNINSTALL NOT SAVED/RETRY` 暗示磁盘仍为旧状态，也不能仅因输出失败创建 pending 或宣称 `persistentBlockConfirmed=true`。先将内存 catalog 与当前绑定状态对齐已提交结果，再执行可能失败的输出；输出失败时封闭当前会话、记录可用的明确失败原因并退出资格。独立进程在没有真实 pending 且磁盘一致时应能重开已卸载状态；该事实不把缺少收据的原运行变成资格通过。

## 回滚与不确定状态

业务写入前的可处理拒绝不改变状态；如果 pending 创建过程已产生未确认标记，则按现有严格失败规则处理，不能谎称是干净的可重试拒绝。

runtime 调查指出 pending 创建故障覆盖仍需补齐；这里记录待验收要求，不把它写为已运行通过。确定性测试逐一覆盖 pending 的 create、identity、write、file-sync、directory-sync 和 readback：创建前失败且能证实标记不存在时可以干净拒绝；一旦已创建对象但同步或回读未确认，不能仅凭文件存在就报告持久阻断，也不能继续 registry/catalog mutation。每个分支都检查业务状态、当前会话是否封闭、有限补偿次数及准确的 `persistentBlockConfirmed`。重复文件、部分/空标记、原文件被替换、创建成功后下一阶段失败都需要单独反例，不能仅在业务写入之后注入错误来代替。

registry/catalog 业务写入或提交回读失败时，即使原语返回错误，也尝试恢复两份精确旧字节。因为 rename 后的目录同步失败可能已改变磁盘命名空间，不能仅相信内存记录已回退或写入返回 Err。两份恢复都要尝试，不能因第一次恢复失败而跳过第二次。

只有两份恢复、同步、完整旧快照回读以及 pending 的同步删除全部确认成功，才能返回可重试的 `rolled-back`，保留当前旧内存 catalog，显示现有 `UNINSTALL NOT SAVED`/`RETRY`。普通卸载不写包内容，因此本阶段回滚应要求完整旧内容快照精确一致，不以“活动应用看起来一致”代替。

恢复未确认、pending 清理失败或清理后最终回读失败时，当前会话立即封闭且不给出卸载成功。pending unlink 失败要检查原文件实际存在性、inode/字节身份；unlink 已成功而根目录同步失败，不能声称标记仍在。复用已有一次有界补偿：独占重建同一标记，文件同步、目录同步并回读。

- 原已持久标记仍能按身份确认，或补偿创建及全部同步/回读成功：报告 `persistent-blocked`、`persistentBlockConfirmed=true`，独立进程应因 pending 拒绝重开。
- 补偿任何阶段未确认：报告 `commit-uncertain`、`persistentBlockConfirmed=false`，记录具体失败阶段与单次尝试；不能保证下一进程一定拒绝，也不能将这条分支记为跨进程阻断通过。

pending 已开始清理之后发生的持久化/最终回读不确定性沿用现有封闭处理，不另起一套自动恢复循环；它与磁盘已确认提交后的单纯输出失败区分。后续进程只要看到 pending 就拒绝，不自动完成、回滚、清除或补包。即使磁盘表面上呈现完整卸载状态，也不替它推断提交成功。

## 私有数据保留证据

安装根内容快照不包含应用私有目录，因此不能拿 registry/catalog 稳定 hash 代替数据保留证明。资格须有明确的非空 sentinel，而非“没有调用 purge”或空目录仍存在的摘要。

优先复用适合的 SDK6 应用/探针：app2 由真实 Dock 启动，在自己的默认私有命名空间写入固定非空文件，完成同步并正常关闭、worker 回收后，再进行卸载。现有样例是否满足该行为尚未核实；实施前检查实际 Kotlin 源码和 SDK 调用，不为此目的盲目改变 legacy `private_storage` 标志。若需要新 fixture，保持为本阶段独立、固定签名的最小测试应用。

在目标停止后的卸载前、卸载提交后、boot1 正常关闭后、boot2 桌面启动前与关闭后，取得同一个受控私有目录的只读文件清单和 hash。检查固定相对路径、大小/数量上限、非 symlink、规范命名空间和完整预期字节；绑定 package ID、boot/session、阶段及原始日志 offset。只读观察器不得使用会创建目录/文件、写授权或重新打开 worker 的便利接口，也不能把 receipt 写回被观察的目录。

资格要求这些私有内容逐阶段精确一致；目标包历史内容同样保留。兄弟应用/公共目录/公共授权的不变性在 Linux 集成测试中用独立非空 sentinel 和授权状态覆盖，避免为真实双启动增加无关交互。正式 fixture 的实际目录布局和所需最小只读观察 API 是待核查项。

## 正常重启资格与原始因果

新增独立封闭 profile，建议名 `persistent-uninstall`，以及独立 qualification 标识、helper 模式/入口和 checker；名称与命令尚未实现。原 `persistent-install` 的两轮 app2 存在性、输入 hash 和 checker 保持原义。可复用底层严格读取与 pointer-ready 原语，不能放宽旧 profile 来接受卸载状态。

同一不可变 bundle、同一正式 review 冻结两份不同输入，新数据盘只运行一次完整双启动：

| 轮次 | 行为 | 必须证明 |
| --- | --- | --- |
| boot1 | 首次初始化 app1；真实 INSTALL/ALLOW 安装 app2；真实 Dock 启动 app2 写数据；关闭并回收；真实 U/UNINSTALL；POWER | app2 曾真实安装/启动且私有数据非空；当前提示呈现后才确认；卸载 commit；最终仅 app1 活动，app2 removed、数据保留；正常关机 |
| boot2 | 严格只读重开卸载结果；只运行 app1 和正常关机所需动作 | 不提供安装源或事件脚本，不初始化/补包/重装；app2 无 Dock 项、无活动登记或 worker；removed 精确身份和私有数据保持；正常关机 |

上述顺序是待冻结的最小资格合同，不是已批准执行的 InputPlan。写实际计划前必须核对 fixture 是否自动关闭、关闭动作及数据同步证据、几何和准确预算；保留现有 watchdog 与 pointer 等待边界，不能靠放宽预算或增加重试判绿。

U 入口和 UNINSTALL 确认均由当前原生匹配 down/up 激活；不同于普通 Dock down 即 Launch 的现有语义。当前 native-input 只用 `launch`/`other` 表达动作，不足以单独证明卸载。需要为新资格加入有界的卸载请求/确认因果：目标身份、呈现帧/提示 revision、实际确认 release 序号及对应 press、commit 前后状态锚点。commit 可能在 `end_native_input` 输出之前产生，不能错误要求日志中先出现完整 up 收据再 commit。

checker 必须用 QMP 派发、同 boot/session/PID 的原生收据、stderr 原始 offset、提示呈现及状态锚点连接真实确认与卸载，不凭坐标、QMP ACK 或随后 Dock 消失判断成功；禁止直接比较 host monotonic 与 guest uptime。第二轮无 app2 worker 的结论要覆盖完整 state/history/原始日志和 worker 进程证据，不能只看最终桌面截图。

跨轮比较 `boot1.post.content == boot2.pre.content == boot2.post.content`；私有数据用独立内容快照比较。envelope 各字段分别绑定同盘 UUID、不同 boot/session、正确 role、marker1→2、previousMarker 链、固定 root/catalog、阶段和下表规定的初始化计数，不任意删除字段以制造相等。两轮都要求正常 terminal、worker 回收、sync/unmount 与 kernel PowerDown。

初始化计数按阶段精确约束，不以“boot1 都允许初始化”代替。fixture/helper 的 `actions.initializedRoots`、`packagesInstalled`、`trustStoresProvisioned`、`catalogsCreated`、`sourcesCreated` 各自满足：

| 快照阶段 | 每项初始化动作计数 |
| --- | --- |
| `boot1.pre`（首次初始化） | 1 |
| `boot1.post`（卸载后只读重开） | 0 |
| `boot2.pre`（只读重开） | 0 |
| `boot2.post`（只读重开） | 0 |

这些计数只描述 helper 初始化行为；boot1 UI 的一次 app2 安装和一次卸载由各自独立 commit 收据及 raw 因果验证，不能混入 `packagesInstalled` 初始化计数。中间私有数据观察和卸载后快照也只读、初始化计数为0。checker 对每项字段、阶段和 raw argv 独立校验，拒绝 boot1.post 重建源包/catalog 或任何 boot2 初始化。

## 文件与职责

| 文件 | 预期职责 |
| --- | --- |
| `crates/ming-app-host/src/persistent_install.rs` | 窄卸载事务、bootstrap 保护、新 pending/结果类型、removed 转换与回滚校验、持久安装空槽分配；复用同步/补偿原语 |
| `crates/ming-app-host/src/package_install.rs` | 必要时新增精确登记转换校验 helper；复用现有 uninstall、验签、锁和原始字节恢复 |
| `crates/ming-app-host/src/ming_desktop.rs` | 持久 U 入口/确认、运行代复核、当前 ID 绑定状态清理与历史隔离、成功发布、失败重试与 fatal 封闭、卸载收据及提交后输出失败分类 |
| `crates/ming-app-host/src/desktop_native.rs` | 真实 U/确认 down/up 的因果绑定和定向测试 |
| `crates/ming-app-host/src/desktop_catalog.rs`、`graphics/desktop_shell.rs` | 仅在新 qualification 参数或 bootstrap 信息提示确有需要时做窄改动；不预先重写 catalog/UI |
| `crates/ming-app-host/tests/persistent_install.rs`、`tests/package_install.rs` 与现有 lib/native 测试 | 新进程重开、精确转换、数据保留和故障边界；保持普通模式回归 |
| 新卸载资格 helper/fixture，必要时复用现有 example 的安全读取原语 | 独立首次/重开合同、removed 精确身份、只读私有数据快照；不得改变旧 helper 模式的期望 |
| `scripts/boot/{bootlib,payload_profiles,bundle,guest,vm,cold-boot}.py` 及定向测试 | 独立 profile、两输入/角色合同、bounded raw relay 和阶段快照 |
| 新阶段准备目录中的 driver、checker 和合成反例 | 冻结新 payload/预算/输入、原始因果及双启动审计；不编辑历史 checker/evidence |
| `docs/continuous-development.md` | 实际验收后记录结果及限制 |

私有观察 API、fixture Kotlin 文件及确切 qualification 参数需在下一步读取使用方后确定；本草案不假定已有接口提供无写入的目录快照。

## 分阶段实施与验收门槛

1. **状态与事务单测。** 先实现并验证成功转换、bootstrap/运行代拒绝、无 pending 成功返回及独立进程只读重开。精确断言目标 removed 身份、其他完整登记、catalog、trust/bootstrap、历史包文件和非空私有 sentinel。新增满槽后卸载中间 ID 再安装 `[1,3,4]→[1,2,3,4]`、卸载最高 ID 后复用、同包重新安装及跨进程重开的正例；其他 ID/身份和 autostart 不变，容量满时写入前拒绝。此阶段不启动 VM。
2. **故障与原生 UI。** 确定性注入 pending create/identity/write/file-sync/directory-sync/readback、registry 持久化、catalog 创建/写入/文件同步/rename/目录同步、commit 回读、两份恢复、回滚回读、pending unlink/目录同步、补偿各阶段及最终回读失败。覆盖“rename 已发生但同步失败”的真实磁盘/缓存分歧，不只在调用前返回 Err。分别断言可重试精确恢复、持久阻断已确认、新进程拒绝、未确认补偿的一次尝试及无虚报。额外注入磁盘已确认提交后的 commit 收据、state 和日志输出失败，断言磁盘仍为已卸载、内存绑定已对齐、会话/资格失败、无假回滚或凭空 pending 阻断；独立进程能严格重开。原生测试覆盖取消、错双沿/错帧、运行/关闭中、身份替换、bootstrap、重复确认与成功后无新 worker；普通 U/D/update 行为保持。复用 ID 回归检查旧 public_denied 不抑制新包提示、旧 legacy grant 不授予新包、旧 failed/PID/Ready 不出现在新绑定当前 state、generation 递增及 records/native 索引不变，失败回滚不得提前清理这些状态。
3. **资格准备。** 核实 fixture 实际数据写入和停机；冻结新 profile/helper、只读快照、两轮输入及准确预算；合成 checker 必须拒绝缺失 removed 记录、仅内存删除、第二轮补包、各阶段初始化计数不符、空数据、数据漂移、无真实 U/确认、提示未呈现、错身份/offset、摘要伪成功和异常关机。合成通过不代表真实 VM 通过。
4. **Linux 当前源码验证。** 在固定禁网 Linux 环境运行受影响的 persistent/package/catalog、session/native 和 boot Python 测试；运行项目要求的全量检查与 Host/helper 构建。实际命令、匹配数与结果进入实施记录，不把本草案列出的预期写为已通过。
5. **真实双启动。** 在执行前核对用户对具体 VM、构建、盘与卷动作的授权及预算，完成一次新盘双启动和独立原始证据重放。保留失败盘和 RED，不改历史证据、不降低 checker 门槛。仅在通过后记录 `persistentUninstallNormalRebootVerified=true`、`privateDataPreserved=true`，同时保留 `finalSystemAccepted=false`。

阶段结束只证明虚拟输入仪表下的正常重启卸载与数据保留。未确认的存储失败不会被包装成已保证持久阻断，暂停的物理输入专项也不会由本阶段自动恢复。
