# 持久 catalog 下的应用更新与失败恢复设计

日期：2026-11-04。状态：设计草案，供独立复核；本文件没有实现任何接口，也不代表构建、迁移、恢复或 VM 验收通过。

## 目标与范围

将已经立项的签名应用更新、声明式私有数据迁移和启动确认接入现有持久安装模式。用户确认更新一个已安装的非 bootstrap 应用后，新版本与迁移结果经过一次完整运行确认；正常关机并在同盘冷启动后，应用仍可从原 UI ID 启动。提交决定之前失败时恢复旧版本和旧私有数据；恢复不能确认时封闭会话并保留现场。

本阶段只更新同一 `packageId` 的更高版本，覆盖既有空/非空私有目录，以及尚无私有目录的已安装应用。沿用已定义的 migration 和 publisher 合同，不新增日常应用、远程下载、生产密钥发布、系统镜像更新、任意迁移脚本或多用户服务。不更新 bootstrap ID1，不执行 purge，不改变公共目录或公共授权。进程中断后的恢复采用显式父侧恢复入口；普通严格重开继续只读检查，不自动修复。

## 已确认的实现与证据边界

- [既有更新设计](2026-11-01-update-migration-design.md)与[实施计划](../plans/2026-11-01-update-migration.md)定义 manifest `dataMigration`、签名和指纹覆盖、私有 staging/backup、registry journal 及启动确认。[连续开发记录](../../continuous-development.md)的 2026-11-02 段记载普通更新迁移双冷启动资格；本次仅核对记录与当前源码，没有重新运行该历史资格，也没有把它计作持久 catalog 更新通过。
- `package_install.rs` 的 `update_loaded_package`、`recover_pending_update`、`begin_update_startup`、`confirm_pending_update` 和 `rollback_pending_update` 已存在。`update-journal-v1` 的阶段为 `Planned`、`DataSwapped`、`RecordPublished`、`StartupAttempted`、`StartupConfirmed`、`RollingBack`。普通恢复允许尚未尝试的 `RecordPublished` 更新保留一次试运行机会；这不是本设计中断后的恢复策略。
- `ming_desktop.rs` 的 `UpdateStartupEvidence::confirms` 要求精确 package/version/fingerprint、UI ID/generation/window、有效 PID、inputReady、已呈现帧、正常完成、Result 接受、native exit 成功和 reap。不能把 Ready、首帧或 QMP 回复单独当作更新确认。失败回滚前，当前实现会释放该运行代的私有文件会话。
- 持久 `snapshot_inner` 要求 `update-journal-v1` 不存在；`open_existing` 在普通恢复逻辑之前检查持久根。`persistent_install_rejects_update_and_purge_before_writes` 则验证持久模式从入口和确认两层拒绝更新。因此不能只解除桌面 guard 或调用普通恢复来完成接入。
- 持久 catalog schema v3 只保存 UI ID、title、packageId 和 autostart；版本、内容路径和指纹从 registry 解析。同包更新可以保持 catalog 原始字节完全不变。bootstrap 初始版本、指纹和 Wasm hash 则由初始化合同固定，本阶段继续保护。

已有单测包括 registry 的 `pending_update_reopens_and_preserves_startup_retry_after_record_publish`、`startup_attempted_update_reopens_and_rolls_back_after_process_restart`、`startup_confirmation_commits_migration_and_clears_journal`，集成测试 `update_from_manifest_migrates_private_files_and_keeps_old_content`，以及桌面的 `update_confirmation_requires_exact_identity_and_complete_lifecycle`、`desktop_startup_confirmation_commits_pending_update`。这些是可复用的验证基础，均不能替代本阶段新增合同和执行证据。

## 与当前持久卸载候选的关系

当前 `persistent-uninstall` 的 candidate2 执行冻结绑定 1214 项输入；正式双启动仍未通过，准备记录的 `formalRunAuthorized=false`。本设计不修改冻结源码、输入、bundle、checker、review、失败盘或候选资格状态，也不启动任何 VM。文件路径位于该冻结之外，设计准备不依赖先执行卸载双启动。

后续实现会改动持久状态、registry 和桌面源码，必须使用独立开发基线与新冻结、新 Host/helper/bundle；不能把新代码塞回 candidate2。是否继续执行该候选由主执行任务现有授权和用户决定，本设计不提供新正式尝试授权。更新资格使用独立 profile、review、fixture、数据盘和 checker，不放宽旧安装/卸载资格的期望。

普通卸载与本阶段共享已签名的活动/removed 账本、UI ID 和严格重开约定。设计不要求先卸载目标；removed 记录不能作为更新对象。更新事务存在时不允许安装、卸载、purge、第二次更新或目标的新运行代绕过它。已有安装/卸载 pending 属于原合同，恢复入口不得解释、清除或修复它们。

## 方案选择与保持不变的状态

采用一个小型持久更新协调记录，包裹既有 registry/private-data 更新原语；catalog 在整个更新和回滚期间保持字节一致。这样无需为可保持不变的 catalog 引入备份和第三份可变状态。

单纯启用旧 update 路径不能处理严格重开对 journal 的拒绝，也没有外层提交/恢复身份绑定。把 catalog 同步改写为新标题会增加跨文件提交与回滚窗口，当前目标无需这项变化。重写为通用数据库也超出本阶段所需。

更新前后目标 UI ID、catalog title、packageId、autostart、bootstrap 和 trust 原始字节保持一致；新 manifest 的 title 不在此阶段同步到 Dock。新版本可能变更 manifest、Wasm、fingerprint 和已签名 migration。旧版本内容继续保留，不在提交或回滚时清理历史包。

## 事务入口与状态绑定

更新由用户操作现有 INSTALL 入口选择受控签名源触发，父侧发现同 packageId 已 active 且版本增加后显示更新提示；必须经当前提示已呈现后的匹配按下/释放明确确认，不允许应用、后台扫描或启动 helper 自行触发。目标从 active registry 精确解析到 UI ID2..4，入口和确认均验证非 ID1、版本严格增加、publisher 验签、SDK6、支持的 migration、同一不可变包快照，以及无目标 worker、closing/reaping 运行代、pending launch 或存活 private session。目标 `packageId` 的历史 generation 继续递增，当前显示不能继承上个版本的 Ready/PID/失败状态。

可信父侧以固定顺序取得 registry 和 data-root authority；所有更新、恢复入口必须遵守同一顺序并串行化。现有进程已持有的根锁继续复用，不能再次打开导致自身锁竞争；新进程恢复不得创建缺失的根、锁、trust、registry、catalog 或目标数据来掩盖损坏。具体无创建 authority 接口需要实现时补齐，现有 `open_default_private` 不能未经核对直接当作严格恢复 API。

新增协调记录建议固定为安装根下 `desktop-update-pending-v1.json`，最大 4 KiB，严格拒绝未知/重复/缺失字段，0600、单链接、非 symlink。它只包含：

- schema/operation/transaction 身份、目标 UI ID/packageId、旧/新版本与指纹，以及同一候选包的 manifest/Wasm 和 migration 摘要；
- 原 catalog、bootstrap、trust 和除目标外完整 registry 记录集合的 hash；
- 版本化的保留内容摘要：更新前全部受控历史包文件的相对路径、类型、长度和字节 hash，以及候选版本内容目录更新前是否已存在；
- data-root 的绑定值、目标包命名空间与 `files` 更新前各自存在性、旧完整树摘要、命名空间内 files 以外原有受控内容摘要，以及 `backupExpected`；后者严格等于旧 `files` 是否存在；
- `prepared` 或 `commit-decided` 阶段；后者还绑定父侧完整启动确认与确认后目标私有数据摘要。

记录不包含任意恢复路径、宿主命令或应用提供的操作。data-root 绑定只校验显式可信调用参数，不能从记录取路径后直接打开；backup 名由既有 journal 的规范格式派生，并与同一目标/旧新身份核对。完整快照写入独立 evidence，记录只保存规范化摘要，避免超出 4 KiB。

恢复必须自行重算保留内容摘要，不依赖 evidence 目录才能决定可否继续：若候选目录原先不存在，只允许排除由本次精确旧新身份推导的那一个新增目录，并另行完整校验其签名与 manifest/Wasm；若候选目录原已存在则不排除，原 bytes 必须仍匹配。原目录/文件缺失、额外未知历史包、候选以外的新内容或类型变化均拒绝。仅重新验签当前包不能替代这个原文件集合和字节检查。

正常 `open_existing` 遇到该记录、旧安装/卸载 pending、孤立 update journal 或未知文件仍拒绝。只有事务内部检查和显式恢复入口可以识别这一种新记录与其匹配 journal，且必须验证目录其他文件、锁、签名和身份；不得把两种 journal 一概加入普通快照的忽略列表。旧二进制会因未知文件拒绝活动事务；完成或回滚后没有新记录，仍保持原持久格式。

## 正常更新与提交边界

1. 在无其他事务的严格旧快照上准备候选，记录旧身份、目录存在性和数据摘要。目标私有目录缺失不能因观察而创建；在锁内把“缺失”与“已有空目录”作为两个不同状态。无 migration 且原 `files` 缺失时，更新本身保持缺失，试运行可按普通 SDK6 默认私有会话规则创建数据。有 migration 时从受限空 staging 执行声明步骤，缺少源文件的 rename/copy 等仍按既有规则失败，不制造源文件。
2. 独占创建 `prepared` 记录，完成文件同步、父目录同步及身份回读，之后才能写入候选内容或迁移业务状态。调用已加载包的更新原语，保留旧内容和数据 backup，生成并核对匹配的 inner journal。catalog 原始字节不变。
3. 从磁盘验证新 active registry、已验签新内容、迁移后的目标目录、保留的旧数据 backup，以及兄弟状态。通过受限的事务快照发布内存中的新活动解析；此时状态为“待运行确认”，不是最终更新成功。
4. 只允许目标的一次显式 Dock 试运行；先将 inner journal 写为 `StartupAttempted`，再创建 worker。禁止脚本、autostart 或旧实例恢复冒充首次新版本启动。其他已运行应用可继续运行，但不得在待确认期间执行其他安装状态 mutation。
5. 目标正常关闭并回收、private session 全部释放后，按现有完整生命周期条件验证精确新身份。取确认后的目标数据摘要，将外层记录原子持久化为 `commit-decided`，完成同步和回读后，才允许删除旧数据 backup。这个持久决定是不可再回滚的分界。
6. 将 inner journal 推进为 `StartupConfirmed`，完成 backup 清理和同步、inner journal 清理与同步。这里需要一个只供协调器使用、核对外层决定和 inner 状态的幂等完成入口；不能假定现有 `confirm_pending_update` 可直接处理所有重开阶段。
7. 回读确认新 active 内容、catalog 原字节、目标数据、无 backup/inner journal 和其余不变量；同步删除外层记录。最后通过原有严格重开验证，再发布最终 commit 收据。正常重启仅只读重开该稳定结果，无更新源、无补包或迁移动作。

`commit-decided` 之前，失败可进入旧状态恢复；之后仅能继续完成新状态和清理，不能恢复旧数据或显示“更新未保存，可重新安装”。写出 commit 收据、日志或 state-file 失败属于会话/资格失败，不反向改变已经确认的磁盘决定；内存须先对齐实际稳定磁盘状态，然后执行可能失败的输出。

## 失败与显式恢复

普通启动不执行恢复。受控父侧 helper 的显式恢复模式必须在任何 worker 创建之前，取得上述两根的排他权，严格验证外层记录、可信根、候选和旧包签名、inner journal 身份、目标数据布局及非目标不变量。现有 inner journal 没有 catalog/trust/完整私有树 hash，这些约束必须来自外层绑定并对实际文件重算，不能假定旧 journal 已经覆盖。它不提供任意路径删除或“忽略错误后继续”。恢复失败保留现场，不循环重试。

| 外层与 inner 状态 | 恢复行为与成功条件 |
| --- | --- |
| 没有外层、没有 inner | 原严格只读重开；不触发恢复 |
| 没有外层、只有 inner | 拒绝，不能把普通 update journal 自动收编为本阶段事务 |
| `prepared`，尚无 inner | 仅当旧 active/原 catalog/旧私有存在性与摘要和无备份残留均验证一致时清外层；候选历史内容可保留，但须完整、验签且不活动，否则拒绝 |
| `prepared`，inner 为 Planned/DataSwapped/RecordPublished/StartupAttempted/RollingBack | 固定恢复旧 active 和旧私有数据，不再试运行；复用经核对的幂等 rollback，验证成功后清 inner，再验证旧状态并清外层 |
| `commit-decided`，inner 为 StartupAttempted/StartupConfirmed 或已清理 | 精确匹配持久决定及新 active 后完成新状态；不重新启动 worker。backupExpected=true 时，backup 只可在 StartupConfirmed 或 inner 已清理时缺失；backupExpected=false 时所有阶段均要求 backup 不存在。清理后复核新目标数据存在性与摘要，再清外层 |
| `prepared` 却出现 StartupConfirmed、`commit-decided` 却出现旧 active/回滚状态、身份不符或任一不变量漂移 | 拒绝并封闭，不能猜测或覆盖原文件 |

`commit-decided` 配合 RecordPublished 也必须拒绝：正常启动顺序已先持久化 StartupAttempted，外层提交决定不能绕过它。重启遇到尚未确认的更新，无论 inner 是否为 RecordPublished，都停止在严格拒绝状态；显式恢复统一恢复旧版本，不继承普通模式“重开后再尝试一次新版本”的语义。恢复完成后是否重新更新或运行旧版本均由用户新动作触发，不自动重启 worker。

恢复仅处理认可的进程中断状态，不承诺断电、损坏介质或恶意同 UID 写者下自动恢复。`prepared` 在同一会话内也采用同样的旧状态恢复规则；只有旧 registry、catalog 和私有数据均确认、两个 journal 同步清除、严格重开通过，才显示可重试。新增完整有效的非活动候选包可以留作历史，不能为精确恢复而删除原有历史内容。

原操作失败后立即阻止新 worker 和所有 mutation；若在 commit-decided 之前完成全部可信旧状态恢复、journal 同步清理和严格重开，可返回 `rolled-back` 并解除该临时阻止，等待新的用户确认重试。恢复、决定、清理或最终验证本身失败则保持会话封闭；commit-decided 之后不提供回滚重试。区分 `rolled-back`、待完成提交及 `commit-uncertain`，不可仅凭错误类型或文件名存在推断持久性。外层 unlink 成功但目录同步失败时，允许沿用既有的一次有界补偿思路；只有原记录或重建记录的身份、文件同步、根同步和回读全部确认，才报告持久阻断。补偿失败必须报告 `persistentBlockConfirmed=false`，不能保证下一进程一定拒绝，也不能记作恢复通过。

## 数据与权限保护

目标迁移只操作按 packageId 派生的私有命名空间，保留 no-follow、路径/配额、同目录 rename 和备份同步规则。已有迁移 API 内存在失败后尝试清理、以及 backup 缺失时创建 `files` 的分支；新协调器必须先核对认可状态并新增反例，不能直接将这些分支当作所有损坏情况下的恢复保证。恢复不得在旧数据或 backup 均缺失时制造空目录宣布成功。

当 backupExpected=true，`prepared` 或尚未开始清理的 `commit-decided`+StartupAttempted 需要对 backup 完整树重算更新前摘要，路径相对 backup 根归一化；任何内容、空目录、类型、权限边界或条目不符均拒绝，不能先覆盖现有 files 再发现备份损坏。`prepared` 配合 DataSwapped/RecordPublished/StartupAttempted 时 backup 缺失必须封闭；只有 Planned 尚未交换，或 RollingBack 已恢复且当前 files 与更新前完整摘要精确一致，才允许认可“没有 backup”的旧状态。不能将缺失解释为“原来没有数据”，也不能创建空 files 冒充恢复。

`commit-decided`+StartupConfirmed 可能在递归删除 backup 中途被终止，此时合法残余不再具有完整旧摘要。该分支只按外层已确认的新摘要校验当前 files；验证残余 backup 的派生身份、no-follow/类型/边界后继续清理，不再要求旧树完整，也不把它用于回滚。inner 已清理时 backup 应已同步删除；若发现残余则按状态不符拒绝，不能扩展为任意目录清理。上述分支都不得用旧摘要要求迁移后的新内容不变。

当 backupExpected=false，所有阶段都禁止出现 backup；原缺失状态由已同步外层记录证明，不能只凭当前文件缺失推断。需要新增父侧迁移/回滚前置能力：在不创建旧 files 的情况下准备空 staging，并在回滚时恢复原缺失状态。该能力须绑定同一 transaction、packageId 和 data-root authority，等待目标所有运行代 reap/private session 释放，持有根排他权，先有界验证当前目标 files 与唯一认可 staging/discard，再使用 pinned no-follow 目录句柄清理本事务和试运行创建的目标 files；拒绝符号链接、特殊文件、越界，以及 files/认可临时树以外的新增条目。即使试运行通过普通 SDK 写入任意合法文件名的非空内容，这个 files 子树也由原缺失记录及独占运行边界证明属于本次试运行；恢复目标仍是更新前的“没有 files”，不能保留新树或把它当作旧数据。删除范围仅限这个包的 files 与该事务的临时树，不能触碰包命名空间内其他既有元数据、其他包或公共根。

外层还记录目标包命名空间原本是否存在：若原已存在，回滚仅恢复 files 缺失并保留其余原内容；若原不存在，清理认可 files/临时树后只允许 rmdir 经验证为空的本次新建命名空间，不递归删除未知兄弟条目。删除失败、目录不空或身份改变均封闭，保留可检查的记录。每次文件/目录删除及父目录同步都须可重入：恢复进程再次中断后只在外层身份仍匹配时继续完成；直到原存在性与摘要精确恢复并同步才清 journal。commit-decided 分支保留新版本实际生成的数据及其存在性，不能再运行上述旧缺失回滚。现有 app_data 便利 API 不具备完整合同，这些能力必须先实现并通过跨进程故障测试，之后才能开放持久更新入口。

目标旧 backup 保留到外层 `commit-decided` 已确认持久化；之后只清理该事务的 staging/backup/discard，不遍历删除其他目录。失败前的新版本试运行可能已写入私有文件，回滚恢复更新前快照；提交保留试运行后的新数据。用户不应在此期间开启目标第二运行代。

公共文件和公共授权记录均不迁移、不撤销、不代授权。事务须绑定公共 policy slot 更新前的存在性与原始字节 hash；首次缺失沿用默认空授权，但原有 slot 在事务中消失或改变必须拒绝，不能当作首次缺失。新 fingerprint 必须按原公共授权策略重新匹配，旧版本授权不能因 UI ID 相同自动授给新版本。更新器不能改写兄弟应用目录、版本和完整登记；继续运行的兄弟应用可以正常写自己的私有数据，不能要求其完整私有树 hash 在事务前后相同。前后字节不变的验收使用静止的兄弟/公共 sentinel，验证更新器没有副作用；这些 fixture 文件不成为普通产品更新的前提。公共授权管理在更新待确认期间也不由更新器代执行，其他用户管理动作需按既有控制流串行化。

当前卸载快照是有界小 fixture 的只读观察器，不能未检查其条目、空目录和大小限制就作为所有产品私有目录的通用摘要。更新实现需在现有 8 MiB/1024 项/16 层边界内规范记录目录存在性、空目录、相对路径、类型、文件长度和内容；拒绝 symlink/特殊文件及读取期间变化。旧/新摘要规范必须版本化并有边界测试；观察不得创建文件、目录或授权。

## 验证与退出条件

先实现并检查本地事务与恢复，之后再准备独立资格。至少覆盖：

1. 正常同 ID 更高版本更新，有/无 migration、既有空/非空私有目录、原 files/整个命名空间缺失；分别验证试运行不写文件和实际新建非空文件后的提交/回滚，并在清理中断后跨进程恢复到原缺失。缺 backup 的合法性由 backupExpected 精确区分，不能扩大成忽略损坏。另覆盖 catalog 完全不改写、bootstrap/removed/运行中/重复确认/错签名/降级/路径/配额拒绝，取消不写业务状态。
2. 新 worker 完整正常生命周期才可持久决定；Ready 但无呈现、错误 PID/generation/fingerprint、Result 未接受、native exit 失败、未 reap、挂起和中断均不确认。旧当前状态不能串到新版本，历史 generation 和索引保持。
3. 在 outer create/write/file-sync/root-sync、inner 各阶段、迁移 rename 已发生后的失败、registry 发布后同步失败、commit 决定替换/同步/回读、backup/inner/outer 清理、最终严格回读和单次补偿处注入失败。核对真实磁盘状态与内存分歧，不只在调用前返回 Err。
4. 在每个合法阶段用真实子进程退出或 SIGKILL，再以新进程执行显式恢复；对上述状态表逐项测试。普通 `open_existing` 对活动事务始终拒绝且不写入；恢复成功后独立进程严格重开。缺根/缺锁/错身份/未知字段/记录截断/额外路径/旧安装或卸载 pending/丢失 backup 的反例均不得被修复成成功。
5. 目标非空私有数据和空目录精确比较；兄弟私有文件、公共非空 sentinel、公共授权、bootstrap/trust/catalog 原字节及原有历史包文件不变。新增非活动候选内容单独核对，不能用完整根 hash 相等误判允许的内容增量。
6. 单独注入持久决定之后的日志、state 和 commit 收据输出失败，确认磁盘选择不倒退、内存对齐、资格失败且无虚假回滚；清理故障与单纯输出故障按实际磁盘边界分类。

本阶段的正式正常重启资格应使用独立 `persistent-update` profile：初始根已含固定旧 app2，boot1 以当前提示后的真实确认更新、由 Dock 启动新版本并正常关闭回收后完成持久决定；boot2 不提供更新源、不初始化、不迁移、不补包，以 Dock 从同一持久登记启动新版本。两轮证明同盘/同 bundle、不同 boot/session、marker 链、确切版本与迁移内容、journal/backup 清理、worker 回收、sync/unmount/PowerDown。失败恢复另用独立 fixture 与进程证据；正常双启动不代替中断恢复测试。

checker 绑定真实确认、更新待确认、Dock 因果、运行确认和持久 commit 的原始 offset/同 boot-session-PID 收据，不直接比较 host/guest 时钟。沿用旧原生输入语义、pointer-ready 合同和预算，不恢复物理鼠标专项。准确输入步骤、schema、执行预算与资源动作需在实施计划中根据实际 fixture 冻结，不能从本草案推定已授权执行。

只有新源码 Linux 测试、真实 worker、故障/跨进程恢复及独立正常双启动证据全部通过后，才可记录对应 scope 的 `persistentUpdateNormalRebootVerified=true`；显式恢复的资格另行记录具体范围。当前这些能力均未实现或验收，`finalSystemAccepted=false` 持续保留。

## 后续实现影响面

预期主要涉及 `persistent_install.rs` 的外层协调与严格事务快照、`package_install.rs` 的受限幂等完成接口、`app_data.rs` 的无创建恢复与完整摘要、`ming_desktop.rs` 的持久更新分发与确认输出，以及相应 Rust 测试。新 profile/helper/checker 需独立新增。`desktop_catalog.rs` 只在必要的严格校验与选项接线范围内调整，不为更新改写 schema v3 内容。

实施前需围绕现有函数精确列出每个锁持有区、同步返回值和故障窗口，检查已有清理分支能否满足状态表；不能把本草案的“复用”解释为既有函数已经提供这些保证。本次仅交付此设计文件，不改源码、不改候选、不执行构建或 VM。
