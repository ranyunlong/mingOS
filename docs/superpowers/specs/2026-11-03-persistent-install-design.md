# 正常重启后的持久安装设计

日期：2026-11-03。状态：供复核的设计；尚未实施或验收。

## 目标与已有证据

用户在 Ming Desktop 安装一个可信签名应用，经正常关机和同盘冷启动后，应用仍出现在 Dock，并能由原生 Dock 输入启动、呈现和正常回收。第二轮不能重新安装、重新创建 catalog 或依赖安装源恢复应用。

`artifacts/install-entry-pointer-ready-20261103-qual1/` 已证明两个独立 QEMU 进程中的安装确认呈现、pointer-ready 输入、catalog 提交、新应用 ready、worker 回收和正常关机。该轮两次均从 `/opt/mingos/catalog.json` 和 `/opt/mingos/install-root` 运行；initramfs 每轮重建，不能证明安装持久化。app2 实际由累计 ready 30000ms 的脚本启动，第三步 QMP 只有移动指针，不能证明 Dock 启动。

现有 `PackageInstallRegistry` 已有 verified registry、内容指纹、版本化包内容、排他锁和同步写入；`desktop_catalog::persist_installed_catalog` 已有 schema v3、文件同步、rename 和父目录同步；guest 已能验证 UUID/serial/ext4、挂载 `/data/mingos` 并同步卸载。本阶段复用这些原语，不重做包管理器。

## 方案选择

采用独立的 `persistent-install` boot profile：持久 registry、内容、trust store 和 catalog 共处数据盘上的一个私有根；首次初始化整体发布，后续严格重开；安装操作增加一个有界的未完成标记，并只在两份状态均同步且交叉验证成功后宣告提交。

只移动 registry 会丢失 Dock 的 catalog，因此不采用。使用一个新数据库重写全部安装和更新事务成本过高，也不采用。已有 `install-lifecycle` 和 `data-text-update` profile 的冻结契约与历史证据保持原义；新资格使用独立名称和 checker。

## 固定路径与父侧契约

| 内容 | 固定路径 |
| --- | --- |
| 持久安装根及 registry | `/data/mingos/install-root` |
| 持久 catalog | `/data/mingos/install-root/catalog.json` |
| 初始化契约 | `/data/mingos/install-root/desktop-install-state-v1.json` |
| 未完成安装标记 | `/data/mingos/install-root/desktop-install-pending-v1.json` |
| 已有 trust store / registry / packages | 沿用安装根下现有名称和格式 |
| 只读首次 catalog 模板 | `/opt/mingos/catalog.json` |
| 只读候选安装源 | `/opt/mingos/install-source/app.json` |

父侧新增 `--persistent-install-state <absolute-root>`，仅 Linux 支持。它选择上述严格持久状态模式，并据此打开 verified registry；传入的 catalog 必须恰为该根的 `catalog.json`。拒绝重复、相对路径、与 `--install-root` 混用及其他路径冲突。普通 `--install-root` 行为保持兼容，不能据此宣称满足新模式的一致性契约。

boot profile 只接受表中固定路径、现有 strict/data/state 参数、SDK6 和预审二进制/输入 hash。guest 必须在确认数据盘挂载身份后才调用初始化器或启动桌面，不能回落到 RAM 根。根目录必须当前可信父进程拥有、0700、非 symlink；状态文件必须普通文件、0600、无额外 hard link，并采用有界读取和重复 key 拒绝。元数据检查失败时不自动 chmod 修复已有对象。

## 首次初始化与严格重开

首次初始化必须由 runner 的固定第一轮角色及已校验的全新盘 marker1 共同授权，且安装根完全不存在。目录已存在但为空，也属于已有状态，不能按首次处理。

初始化器在数据盘同一父目录创建私有、唯一的 staging 根，在其中：

1. 写入固定开发 fixture 的 trust store；签名开发密钥仍只属于测试 fixture，不能作为生产发布者根。
2. 只安装初始 `dev.ming.text-entry-demo@0.1.0`，不安装 app2。
3. 写入 schema v3 catalog：app1 的稳定 UI ID 为1，autostart 仅含1。
4. 写入初始化契约，绑定 schema/profile、初始包身份和初始 fixture/trust 输入 hash；这里不保存会随安装变化的当前 catalog hash。
5. 用相同严格重开校验验证 staging 全部内容，同步文件、内部目录和 staging 根。

随后以同文件系统、禁止覆盖目标的 rename 发布整个根，并同步父目录。发布前失败不出现正式根；遗留 staging 只记录并拒绝继续初始化，不自动清理、覆盖或重试。发布目录不是把 registry/catalog 的每次后续更新变成单次 rename。

后续启动只执行 `open-existing`：不得调用 provision、install、copy-template 或补包逻辑。根、初始化契约、trust store、登记、catalog 或任何活动内容缺失、损坏、权限错误、签名错误、存在 pending 标记时，均失败且不启动 worker。不能照搬现有 fixture 中 `active_manifest_path(...).is_err()` 或 `.ok()` 后重新播种的做法。

严格重开时，在 registry 锁下验证 catalog 的每个 ID 唯一，active registry 的包 ID 集合与 catalog 包 ID 集合完全相等，每个 catalog 项均解析到该根下已验签的活动 manifest/Wasm，版本和指纹精确匹配登记。资格 catalog 的 `autostart` 必须精确为 `[1]`，安装 app2 不得改变它。removed 记录和未活动的历史内容可保留，不属于 active 集合；本限定 profile 不支持在同一根存放未展示于 catalog 的活动包。catalog 缺项不能由 registry 自动补齐，registry 缺项也不能由模板补建。

## registry/catalog 一致性与失败处理

两个文件分别原子替换，不等于跨文件原子提交。本阶段只在提交完成后对外发布成功；失败时验证精确回滚，否则当前进程封闭拒绝新 worker。跨进程阻断仅在 pending 已确认持久化时成立；存储无法完成补偿写入时必须报告提交结果未确定，不宣称断电事务自动恢复。

可信父侧在持有 registry 排他锁、确认无既有 pending/update journal 后，执行一次新包安装：

1. 完整加载、验签并核对用户确认时的同一包对象；保存现有精确 registry rollback 信息及 catalog 原始字节快照。catalog 快照使用既有16KiB上限。
2. 在内存准备新 catalog 和活动包集合，检查4应用上限、稳定 UI ID、包 ID 唯一性和目标指纹。
3. 先独占创建并同步 pending 标记及父目录。标记有 schema、包 ID、版本、指纹、原 registry/catalog SHA256 和预期新 catalog SHA256；最大4KiB，不包含任意路径或可执行恢复命令。
4. 用已加载包写入并同步包内容和活动 registry，再原子持久化新 catalog。
5. 从磁盘回读并按严格规则交叉验证 registry/catalog/包内容，确认所有要求的同步成功。
6. 删除 pending 并同步父目录；两步均成功后才发布新内存 catalog、记录 `installed` 和持久 commit 收据，允许 Dock 启动新包。unlink 成功但父目录同步失败不能视为提交成功，也不能声称 pending 仍存在。

发生可处理错误时，恢复精确旧 registry 记录及旧 catalog 字节，再同步并回读验证。只有旧状态恢复已确认、pending 已同步删除，才能显示现有失败/重试提示。不能忽略 `rollback_install`、catalog 恢复或目录同步的错误。无法证实一致、pending 清理失败或提交结果不确定时，当前进程立即拒绝启动任何新 worker，以明确失败终态退出；不得记录 `installed` 或正常成功。

对 pending 清理错误区分 namespace 与持久状态：unlink 失败后检查原标记是否仍存在并验证其内容，不能只根据错误假设标记尚在。unlink 已成功而父目录同步失败，或无法确认标记存在时，有限尝试以 `O_EXCL|O_NOFOLLOW` 重建同一有界 pending，完成文件同步、父目录同步与回读；只有原标记已确认持久存在，或补偿重建与同步全部成功，才报告 `persistentBlockConfirmed=true`，并要求独立进程重开因该标记拒绝。补偿尝试与确认结果必须分别记录，不进行无界重试。

若补偿创建、文件同步、父目录同步或回读任一步失败，报告 `commit-uncertain`、`persistentBlockConfirmed=false` 及具体失败阶段，保留外部 evidence 并失败退出。此时不能声称“标记已保留”、不能保证下一进程必然拒绝，也不能声称该故障的持久阻断或恢复已覆盖；资格不得接受这次运行。同一存储介质拒绝所有持久写入时，仅靠该介质上的标记无法提供跨进程保证。

进程中断后若发现 pending，即使它对应两个看似完整的新文件，也不得自动清除或视作成功；严格重开拒绝并保留现场，后续恢复工具另行设计。写入临时文件、完成内容复制或 QMP 成功回复均不构成 commit。

这套保护只覆盖本阶段的新包安装。更新迁移继续使用已有独立 journal；本 profile 不提供更新、卸载或 purge 的资格，新持久模式对这些未接入一致性保护的 mutation 在写入前明确拒绝，不能悄悄走旧路径。普通开发模式的 U/D/S 行为及几何保持原样。

## 两轮启动与原生 Dock 输入

同一不可变 bundle 的正式 review 一次冻结两份输入及角色：`inputScripts.boot1Sha256`、`inputScripts.boot2Sha256`。新 profile 不接受单一 `inputScriptSha256` 的回落。`cold-boot.py` 只运行固定编号1、2；`vm.py` 接收并记录编号，只验证该编号对应的 hash；guest 的首次/重开角色由 runner 固定 kernel 参数传入并验证 marker1/2。角色不是通用脚本动作，也不允许应用提供。旧 profile 保留单输入行为。

| 轮次 | 启动前状态 | 输入 | 期望 |
| --- | --- | --- | --- |
| boot1 | 初始化后仅有 app1 | INSTALL→ALLOW→app2 Dock→POWER | 当前 mode16 提示确已呈现；app2 安装 commit；实际 Dock 启动并 ready；正常关机 |
| boot2 | 只读校验 boot1 持久结果 | app2 Dock→POWER | 无初始化、无安装或确认输入；app2 从持久内容 ready；正常关机 |

第二轮不运行写入 fixture 的 helper、不传 `--install-source`、不传 `--events`；第一轮也没有脚本 launch 动作。两轮 app2 都由 QMP pointer+left down+up 命中原生 Dock 普通启动区域。1280×800、两应用时可使用 `(680,770)`；执行前由现有几何函数核对，避开 S/U/D 区，不能只复制旧坐标而不核对数量和布局。

沿用 pointer-ready qualification：每次 leading ABS 后必须取得当前 boot/session/desktop PID、offset 之后、同 root surface、误差≤1px 的 enter/motion frame，5秒从 ABS 发出起算，然后发送按钮。INSTALL/ALLOW 保留现有呈现帧按下/释放约束。普通 Dock 当前在原生 `down()` 命中后调用 `DesktopAction::Launch`；本设计不虚构 Dock 的释放激活语义，也不顺带修改它，仍发送配对释放并在证据中明确此行为。

两轮 Dock 步骤各自的派发前基准 snapshot 必须证明 app2 从未出现 launch/generation/worker，且没有运行或已回收的 app2 实例；raw 事件中 app2 首次 launch、gen1、worker PID 和 ready 均须出现在该步实际 down 派发之后。仅有合法坐标、按钮双沿和随后 running 不足以证明启动：已有实例上的 Dock 行为可能只是 restore/focus，产品原有语义保持不变。checker 必须拒绝 app2 先 autostart、随后点击只 focus 的证据。

host monotonic 与 guest uptime 属于不同钟域，不能直接比较时间戳数值来判定上述先后。checker 必须通过原始 QMP 派发记录、同 boot/session 的原生输入收据、guest raw stream offset 及对应前后 snapshot 锚点形成可追溯因果链，绑定该 Dock down 与首次 launch/gen1/PID/ready；只有时间戳看似递增但缺少因果绑定或锚点的证据必须拒绝。同一钟域内的5秒预算仍按现有 host ABS 发出时间计算，不能用跨钟域数值相减替代。

## 可审计收据

初始化/重开结果使用固定 schema 的结构化收据，严格分为 `envelope` 与 `content`；拒绝未知字段、缺失字段及重复 key，不允许 checker 为通过比较而任意删除字段。内容记录限制为当前最多4应用，不收集任意路径。

`content` 只包含跨轮稳定内容：catalog/registry/trust/bootstrap 文件 hash，按 package ID 排序的应用 UI ID、package ID、version、fingerprint、manifest/Wasm hash 和受控相对路径，以及完整受控相对文件枚举与文件 hash。schema 明确排除锁文件和独立 evidence 文件；pending 不作为普通内容忽略，而是要求快照时不存在。

`envelope` 包含收据 schema、boot/session 身份、固定轮次、角色、marker/previousMarker、disk UUID、固定绝对 root/catalog 路径、阶段、动作计数及状态。checker 分别将这些字段与对应轮次的 raw 证据绑定：boot/session 必须不同，boot1/boot2 角色和 marker 必须各自正确；disk UUID 和固定绝对路径必须相同，boot2 包/trust/catalog 初始化写入计数必须为0。各轮 envelope 不参与内容相等比较，但每个字段均须按固定规则独立校验。

boot1 安装成功后、正常关闭桌面前产生完成快照；worker 全部回收后再确认这些持久文件未变，形成最终快照。boot2 启动桌面前产生 `opened-existing` 快照，要求 `boot1.final.content == boot2.preDesktop.content`，不能要求含不同 boot/角色/marker 的整份收据相等。不能仅相信一个 `noWrite=true` 摘要，必须用严格只读控制流测试、前后 hash、内容枚举和原始命令/收据共同证明。

桌面收据绑定实际 catalog path、registry root、活动 manifest path、包 version/fingerprint 与 UI app ID。`installed` 与持久 commit 绑定同包；app2 launch/ready/worker-reaped 按 generation/PID 绑定。boot2 在任何 app2 worker 启动前，已有 `opened-existing` 收据；没有 `installed`、`updated` 或 fixture 创建记录。

独立 checker 复用已审阅 raw audit 的严格 JSON、offset/base64、完整 stderr hash、Wayland receipt 重放、QMP唯一成功回复、输入计划匹配及正常关机规则；为新 profile 分别验收 boot1 四步和 boot2 两步，不能沿用要求两轮都 installed/四步的旧 pair 规则。checker 还要求同 bundle/盘、不同 boot/session、marker1→2、第二轮 previousMarker 精确等于第一轮 marker、两轮自然回收与 sync/unmount/PowerDown。

## 测试与退出条件

先验证初始化和一致性原语，再验证封闭 profile 与输入选择，最后构建新 Host/helper/bundle 并运行新盘两轮冷启动。损坏反例必须证明未启动 worker、未重写原状态；成功正例必须在第二进程和第二 guest 中实际重开，不能只在同一内存对象读回。

至少覆盖：全新根一次发布；已有空根/缺文件/坏JSON/重复key/错误权限/symlink/hardlink；错误签名或包内容；active/catalog集合任一方向不一致；pending存在；初始化和安装每个同步/发布失败；catalog失败后的精确回滚、回滚失败后当前进程封闭；pending unlink成功而父目录同步失败后的补偿成功及各补偿阶段失败；content相同而合法不同的envelope、envelope互换/boot身份复用/错误计数、未知或缺失收据字段；错误轮次/marker/hash、第二轮含INSTALL或script launch；摘要伪成功、raw截断或缺少真实Dock按钮、app2先autostart后合法Dock点击只focus、boot2 helper补包和跨轮内容漂移。

退出条件是独立 checker 接受一次新、无重试的完整双启动证据，明确标记 `persistentInstallNormalRebootVerified=true`、`dockLaunchVerified=true`、`finalSystemAccepted=false`。历史 RED、RAM 安装资格和物理鼠标暂停状态保留。

## 明确限制

仅验证单可信父进程、固定开发发布者、x86_64 Linux、QEMU TCG、虚拟图形/输入、现有 strict 预算和正常关机重启。WAYLAND_DEBUG、bounded evdev observer 和 pointer-ready 会影响时序；不代表无仪表或物理鼠标可靠性。物理鼠标专项继续暂停。

同盘持久写不可用且 pending 无法确认重建时，只能确认当前进程拒绝和提交未确定，不能保证跨进程阻断。此边界必须由针对性故障测试证明没有虚报保证，不能记作持久阻断已通过。

不覆盖断电耐久性、自动修复损坏状态、安装中崩溃恢复、更新/卸载/purge持久事务、多用户、远程仓库、生产密钥分发、跨平台服务、Linux 硬件 GPU/display 或整体日常系统验收。遇到真实外部权限/环境阻碍时保留现场并报告，不重格式化、增加输入、放宽预算或降低 checker 门槛。
