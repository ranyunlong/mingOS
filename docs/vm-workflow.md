# Notes 开发会话

`developer-workflow-v1` 是独立的开发会话合同：Notes（SDK6）和 Motion（SDK5），严格隔离，持久目录 `/data/mingos`。它使用正常桌面的虚拟键盘、鼠标和 POWER 按钮。会话最长 900 秒，driver 监督上限 945 秒，外层上限 1950 秒；超时失败并保留现场。

Notes 的私有目录由可信宿主按已选择的包身份提供，无需共享存储授权对话框。输入文字后点击 **保存** 才写入，关闭不会自动保存。启动会读取已有便签；**重新读取（替换编辑内容）** 会丢弃尚未保存的编辑。读取或写后核对失败会锁定保存。状态区分目录同步已确认、同步未确认及写入失败。当前演示会记录有效便签明文字节，请只使用非敏感测试内容。

## 交互接口

受控 Linux runtime 内的可信 `scripts/boot/workflow_cli.py` 提供 `status`、`capture`、`click x y`、`key qcode`、`type text` 和 `power x y`。外层入口负责创建和监督容器；这些内部命令本身不会配置 Docker 隔离或证明数据已持久化。控制目录必须来自本次正常启动的独立 tmpfs，不能从旧实例复制。

每条命令绑定私有 Unix socket 凭据、本次实例、单调序号、当前 session、窗口布局和输入 epoch。Motion 帧可持续推进 revision；窗口、包实例、存储提示或其他输入变更会使先前观察失效。仅在未发送输入的布局拒绝后，CLI 最多重新获取三次状态；已接受或超时的命令不会自动重发。唯一 QMP owner 使用既有 InputPlan 和原生 pointer-ready 回执，不开放原始 QMP 或 TCP 服务。

`type` 接口支持最多 512 个拉丁字母、数字、空格及换行；Notes 本身只接受最多 512 UTF-8 字节的单行内容。中文及其他 Unicode 文本的编辑模型已有测试，但当前 CLI 没有相应的输入方法。截图、虚拟输入和 TCG 运行均不能证明物理输入或硬件支持。

## 公开外层快速操作

公开 `scripts/boot/workflow_outer.py` 提供 `new/start/status/capture/click/key/type/power/reopen/offlinebackup/restore/inspect`。内部生命周期的 `backup` 对应外层 `offlinebackup`；外层没有 `verify` 子命令。下面的 PowerShell 命令使用此公开外层配置和监督 Docker 隔离。

截至 2026-11-07，真实 `developer-workflow-v1` 已完成三阶段持久化验收：首次保存 `MingOS note A` 后追加未保存编辑并正常关机；第二个容器读出精确 A，保存 `MingOS note B` 并正常关机；离线备份恢复到新工作/结果卷后，第三个容器初始读出精确 B，未再次保存并正常关机。完整原始日志、SDK 操作、离线文件及历史链回放通过。运行 pack manifest 为 `273b38ceb8665d92743d8c4820939fb2c2a858deee4bb330e4af886b3fb7d1e6`；最终源码与整包摘要由包外交接收据固定。以下 `<...>` 是使用者路径或名称参数，不能直接照抄执行。

### 输入与信任

需要已安装 Python、可用的 Docker Linux daemon、外层固定 Tools 镜像和已有完整 `developer-workflow-v1` pack 卷。旧 `developer-vm-v1` 预览 pack 不适用。包摘要必须从包外独立可信渠道取得，固定的是卷根 `workflow-manifest.json` 的精确小写 SHA256。

`--source` 指向完整、独立安装且已审定的可信源码树，不能用 pack 内 launcher。先按交接清单独立核验外层脚本和整个源码树；CLI 没有 `--source-sha256` 参数，也不会认证发布者。外层会冻结完整源码，并在以后操作时复验清单。源码须包含实际依赖、`scripts/boot` 和 `probes/linux-isolation-source-v2/profile.json`，不能含根级 `source-manifest.json`。

源码与证据目录必须分开；尤其不能将 evidence 放在 `--source` 树内。路径使用规范绝对路径，避免 `..`、逗号、换行、符号链接和 reparse 路径。每代 evidence 必须不存在，其父目录必须已存在；失败后换新代。

```powershell
$Python = 'C:/<Python安装>/python.exe'
$Source = 'C:/<可信安装>/mingos-source'
$Outer = "$Source/scripts/boot/workflow_outer.py"
$Evidence = 'C:/<证据父目录>/notes-generation-001'
$Restored = 'C:/<证据父目录>/notes-restored-001'
$PackVolume = '<已有完整workflow包卷名>'
$PackPin = '<包外workflow-manifest.json的64位小写SHA256>'
# 独立核验源码清单后再执行；不要把刚计算出的摘要当作可信来源证明。
& $Python -I -B $Outer new --evidence $Evidence --source $Source `
  --pack-volume $PackVolume --pack-manifest-sha256 $PackPin
& $Python -I -B $Outer start --evidence $Evidence
& $Python -I -B $Outer status --evidence $Evidence
```

`new` 返回 `workflow-created`；`start` 的 `workflow-container-started` 只表示容器已启动。继续观察 `status`，直至 `status=ready`、`active=false` 且有呈现状态。`workflow-not-started`、`workflow-not-ready` 和退出后的 `workflow-not-running` 是正常观察，不表示输入已发送。尚未 ready 时不能把一次输入当成已经完成；先读结果再决定下一步。运行期间用 `status`，不要用 `inspect` 探测就绪。

### 截图与正常输入

```powershell
$View = (& $Python -I -B $Outer capture --evidence $Evidence | ConvertFrom-Json)
$View
```

`capture` 没有 `--output`。返回的 `capture` 是 PPM 文件名，实际位于该启动容器的 `/results/boot-NNNN/`。从 evidence 中选择当前 `boot-NNNN.json`，读取其完整 `cid` 与 `index`，用只读导出命令取到全新的宿主目标：

```powershell
$Boot = Get-Content -Raw "$Evidence/boot-<四位启动序号>.json" | ConvertFrom-Json
$GuestCapture = '/results/boot-{0:D4}/{1}' -f [int]$Boot.index, $View.capture
$Screenshot = 'C:/<已有导出父目录>/<新截图名>.ppm'
docker cp "$($Boot.cid):$GuestCapture" $Screenshot
```

按实际截图和 `status` 布局选择 Notes、输入框和保存按钮坐标；不要沿用其他启动的坐标。下列 `$X`、`$Y` 等变量必须先设为该次画面中的整数：

```powershell
& $Python -I -B $Outer click --evidence $Evidence $X $Y
& $Python -I -B $Outer key --evidence $Evidence a --modifiers ctrl
& $Python -I -B $Outer type --evidence $Evidence 'RememberA'
& $Python -I -B $Outer click --evidence $Evidence $SaveX $SaveY
& $Python -I -B $Outer capture --evidence $Evidence
```

Notes 必须点击 **保存**。编辑、关应用和重启不会自动保存。私有目录由可信宿主按 Notes 包身份提供，无需共享存储授权对话框。当前演示会记录有效便签明文字节，**只输入非敏感测试内容**。

`type` 仅接受 1–512 个拉丁字母、数字、空格和换行；不接受中文、标点或其他 Unicode。Notes 本身只接受最多 512 UTF-8 字节的单行便签，因此这里不要输入换行。`key` 支持 a–z、0–9、esc、ret、spc、tab、backspace、delete、方向键、home、end 和修饰键；`--modifiers` 最多两个不同的 ctrl/shift/alt。坐标协议范围 0–4095，实际屏幕范围以截图为准。

输入绑定当前 session、布局和 epoch。只有未发送输入的过期布局拒绝允许内部有限刷新；外层不会自动重发已接受或超时的输入。收到执行失败时保留现场，不重复输入来猜测结果。

单独 `key` 允许合法的无画面变化操作，例如光标已在末尾时按 End。其完成回执的 `completionScope=qmp-dispatched-desktop-state-observed` 与 `applicationEffectVerified=false` 仅表示 QMP 按键事务派发成功、按键已释放，并观察到同 boot/session 的更新 ready 状态；该状态可能在派发前生成，不是应用处理确认。保存内容仍需应用回读及停止后文件核对，正常 POWER 仍需完整退出证据。

### 正常关机、重开和离线备份

保存后，按本次截图识别桌面 POWER 按钮坐标。`power` 是正常 UI 点击，不是直接 kill 或 ACPI 捷径：

```powershell
& $Python -I -B $Outer power --evidence $Evidence $PowerX $PowerY
& $Python -I -B $Outer status --evidence $Evidence
& $Python -I -B $Outer inspect --evidence $Evidence
& $Python -I -B $Outer reopen --evidence $Evidence
& $Python -I -B $Outer status --evidence $Evidence
```

`power` 须完成正常内层 POWER 流程，并确认该精确容器实际退出码为 0。`inspect` 用于已停止实例的只读审计；运行时调用可能产生失败记录并阻断后续变更。正常后续启动都用 `reopen`，不要再次 `start`。重开后等 ready，再检查 Notes 启动读取的内容；截图或盘 marker 连续性本身不能证明便签内容持久化。

再次正常 POWER 并确认退出后执行备份：

```powershell
& $Python -I -B $Outer power --evidence $Evidence $PowerX $PowerY
& $Python -I -B $Outer offlinebackup --evidence $Evidence
```

要求结果 `offline-backup-complete`。宿主收据在 `$Evidence/offlinebackup-NNNN/backup.json`，相邻是 `backup-manifest.json`。这里 NNNN 是上一启动 index 加一，例如 boot-0002 后对应 offlinebackup-0003。备份创建全新 Docker backup 卷，停止后的原工作和结果卷只读挂载。

### 恢复为新代

从独立交接记录取得精确宿主 `backup.json` 的 SHA256；不要把 `backupManifestSha256` 当作该收据的摘要。

```powershell
$Receipt = 'C:/<原证据>/offlinebackup-<四位序号>/backup.json'
$ReceiptPin = '<独立固定的该backup.json的64位小写SHA256>'
& $Python -I -B $Outer restore --evidence $Restored --source $Source `
  --pack-volume $PackVolume --pack-manifest-sha256 $PackPin `
  --backup-receipt $Receipt --backup-receipt-sha256 $ReceiptPin
& $Python -I -B $Outer reopen --evidence $Restored
& $Python -I -B $Outer status --evidence $Restored
```

恢复分配新 work/results 卷和新 generation，保留备份与原数据；第一次恢复后启动也用 `reopen`。全局启动次数与 marker 延续。等 ready 后核对 Notes 内容，再正常 POWER，停止后 `inspect`。实际恢复验收须保留内容、来源、正常关机和新卷证据。

此备份不是独立可携带的 tar：只复制宿主 `backup.json` 不够。须保留原 Docker backup 卷、精确 prior 容器、来源卷、身份收据与完整 lineage；恢复会验证它们仍存在、先前容器正常退出，并检查相关卷没有活动写者。完整迁机导出与重建另需专门交接流程。

### 失败处理

有效操作执行失败会在该操作目录留下 `failed.json` 并锁存该代；保留 evidence、容器、卷和日志。不要删除锁存、重命名或删除资源，也不要原地重试、重格式化或“修复”。按已审输入创建新代，必要时从已完成的可信离线备份恢复。`status`/停止后的 `inspect` 可用于调查，但不会清除失败。此 CLI 不提供 stop/kill/reset/repair/retry；900 秒 guest 限时和监督超时失败也须保留现场。

## 当前验证范围

公开 `workflow_pack.py` 使用独立的 `workflow-manifest.json`、profile 和 purpose；旧预览格式与新工作流互相拒绝。`workflow_vm.py` 提供 verify/init/start/inspect/reopen/backup/restore。运行、重开和结果封印持有同一持久 EX 锁；每次容器使用新 tmpfs。正常重开还要求上一容器已正常退出的外层观察、完整历史和磁盘 SHA256。备份只读复制停止后的完整实例；恢复写入全新工作卷、创建新实例身份并保留原备份及历史。

跨进程握手先同步完整数据，再独占创建空的 `.ready` 文件；消费者只在该完成标记出现后读取。独立测试曾真实复现空文件竞争，修复后公共生命周期、pack 和交互组合控制在 Linux 中 73/73 通过，外层另有 30/30 控制通过，合计 103/103 独立 Linux 控制。这些控制使用明确的 guest/formatter/pack 或 Docker 生命周期替身（double），控制本身没有启动真实 guest，不证明 Notes 保存、重开或恢复通过。文件发布、锁和部分隔离检查实际运行于 Linux；生命周期 fixture 删除前要求显式环境开关、uid/gid65534 和四个独立 tmpfs 挂载。

Notes/Motion 的实际离线强制重编、包适配和独立回放已通过；Notes 另通过 14 个真实 Kotlin/Wasm 存储合同和 4 个 Compose 输入/保存保护场景，存储能力使用替身。新控制接口及共享 boot/profile/input/pointer 控制在 Linux 中 143/143 通过，包含真实 Unix socket 和实际 main 参数入场，QMP 为明确替身。

真实 Notes 三阶段记录位于本机证据根的 `workflow-notes-actual-5de8a419` 与 `workflow-notes-restored-6bf321a8`。最后原始回放为 `workflow-notes-sdk-restored3-73c9ed21/review-original.json`，SHA256 `04e7156313fa64ad7b0b0a81668076311ea06b5ef191add522c7a3b0547b6698`，`normalBoots=3`、`threeStagePersistenceVerified=true`。A/B 均为13字节；导出逐字节核对，fsck 只读检查通过。三次均确认应用退出、资源回收、数据同步、卸载文件系统、kernel powerdown 与精确容器退出码0。单键 no-op 和 POWER 完成时序的原失败代完整保留；修正后的公共生命周期控制为 Linux 113/113、零跳过。

旧 `developer-vm-v1` 仍是固定15秒 Text Entry/Motion 预览；历史 `6d8f2b79` 只保留其原始范围。Notes 上述验收覆盖正常保存、正常关机、跨容器重开及同 daemon 下新卷恢复，不证明异常断电、损坏介质、跨主机用户数据备份或物理输入。

参见 [开发系统交付范围](vm-developer-system.md)、[复建输入](vm-rebuild-inputs.md) 与 [更新、卸载和恢复](vm-update-uninstall-recovery.md)。当前 `formalQualification=false`、`finalSystemAccepted=false`。
