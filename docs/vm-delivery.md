# 开发 VM 交付操作

交付范围是 Windows、PowerShell 7.3+、Python 和 Docker Desktop Linux/amd64 上运行的 x86_64 QEMU 开发系统：Notes、Motion、独立持久盘，以及更新、卸载和恢复的公开开发流程。QEMU 直接加载 kernel/initramfs；当前使用软件呈现与虚拟输入。正式资格和最终系统验收标志保持 false。

## 交付内容与校验

完整交接有八个组件：`runtime-pack`、`current-source`、`rebuild-inputs/cache`、`rebuild-inputs/images`、`rebuild-inputs/evidence/update`、`rebuild-inputs/evidence/uninstall`、`licenses`、`receipts`。最终归档、handoff manifest、源码 descriptor 以及独立校验工具的 SHA256 须从包外记录取得。交接记录保存实际源码快照 commit、真实运行收据和导入/只读复核结果。

`handoff_manifest.py verify` 在 Linux 检查完整目录集合、每个文件的字节与模式、运行包来源闭包、源码归档和许可。应使用已独立核验的工具副本，全部输入只读挂载，禁网运行；不执行包内 launcher。源码与运行包中的历史解释代码分别记录身份，实际控制使用独立的 `--source`。源码恢复和校验步骤见 [复建输入](vm-rebuild-inputs.md)。

## 运行 Notes 与持久数据

将已核验的完整 runtime-pack 放入新 Docker 卷根，保留其 Linux 模式。当前 `workflow-manifest.json` SHA256 为 `273b38ceb8665d92743d8c4820939fb2c2a858deee4bb330e4af886b3fb7d1e6`。准备已独立恢复并校验的完整源码树。以下变量须先设为实际路径/名称，证据目录须不存在且与源码分开：

```powershell
$Outer = "$Source/scripts/boot/workflow_outer.py"
& $Python -I -B $Outer new --evidence $Evidence --source $Source `
  --pack-volume $PackVolume `
  --pack-manifest-sha256 273b38ceb8665d92743d8c4820939fb2c2a858deee4bb330e4af886b3fb7d1e6
& $Python -I -B $Outer start --evidence $Evidence
& $Python -I -B $Outer status --evidence $Evidence
```

等 `ready`、`active=false` 后，按 [Notes 操作说明](vm-workflow.md) 截图并通过正常 UI 保存、POWER、`reopen`。真实三阶段记录已验证：保存 A 后放弃未保存编辑；第二个容器读 A 并保存 B；离线备份恢复到新卷后第三个容器初载 B，未再次保存。最后原始回放 SHA256 为 `04e7156313fa64ad7b0b0a81668076311ea06b5ef191add522c7a3b0547b6698`。

Windows 必须使用短源码路径和短证据路径。外层将完整源码复制到 `$Evidence/trusted-source`；先计算源树每个相对文件名在该目标下的完整长度，确保低于260字符，并为后续生成文件留余量。本次短路径 `artifacts/sr-0c9f60f` 与 `artifacts/w` 的冻结文件最长258字符，实际可用；两次更长路径的 `new` 在冻结源码阶段触发 WinError206，失败目录保留，尚未创建 Docker 资源。不要通过修改旧失败目录或放宽驱动继续操作。受沙箱限制的 Docker 查询同样会留下失败记录；需要输入的后续操作将拒绝该代。

`offlinebackup` 仅接受正常停止实例，创建新备份卷；`restore` 以精确 `backup.json` SHA256 入场，创建新工作/结果卷，首次启动用 `reopen`。备份还依赖保留的 Docker 卷、原 prior 容器和完整 lineage，单独复制收据无法迁机。失败代保留全部资源，不清除锁存或原地重试。

## 复建应用与系统

工具链由固定 Rust、Tools、Kotlin 二进制镜像提供；交付保留三个镜像归档、Cargo/Gradle 缓存及其原始收据。镜像默认只读核验；显式加载命令已有控制，但全新 daemon 加载尚未实测。源码、缓存恢复到新副本后，公开 Notes/Motion 强制离线编译入口是：

```powershell
& $Python -I -B "$Source/scripts/boot/kotlin_delivery.py" `
  --repo $Source --output $NewKotlinEvidence `
  --baseline $GradleBaseline --original-cache $GradleVolume `
  --profile developer-workflow
```

`developer-workflow` 编译 Notes/Motion；`update`、`uninstall` 分别编译六个、四个资格包。输出位于源码之外，必须全新；之后独立封印、回放并投影为对应 `packages/` 卷。缓存、源码完整恢复和实际新副本重编已验证；恢复输入的 Rust 七命令冷构建为776通过、0失败、24忽略。这里不声称工具链全部从源重建或逐字节可复现。

系统冷构建公开入口如下，只提供两套已有资格 profile；`$ColdProfile` 为 `persistent-update-linux-host` 或 `persistent-uninstall-linux-host`：

```powershell
& "$Source/scripts/boot/new-linux-host-build.ps1" `
  -Repo $Source -EvidenceRoot $ColdEvidence -RuntimeInputs $RuntimeInputs `
  -PackagesVolume $PackagesVolume -CargoVolume $CargoVolume `
  -BackingVhd $ActualDockerVhdx -ColdProfile $ColdProfile -StartBuild
$Build = Get-Content -Raw "$ColdEvidence/build-started.json" | ConvertFrom-Json
docker wait $Build.cid
docker inspect $Build.cid
```

入口要求完整 `rust-image.json`、固定 Linux node、`toolchain-observation.json`、对应包卷、`cargo/` 缓存卷和实际 Docker VHDX 路径；宿主与 Linux 各需额外可用32.5GiB。`ColdEvidence` 位于源码 `artifacts/` 下且不存在。异步启动及 `docker wait` 命令自身退出0不代表构建成功，须核对内层退出码、最终精确容器及全部结果。工具不会补造缺少的外部输入。

## 更新与卸载

已完成的独立真实场景包括0.2.0更新后同盘正常重开，以及可信 UI 确认卸载、catalog移除、能力撤销、私有 `note.txt` 保留和第二次正常启动。交付中的原始便携证据可以只读 verify/replay，详见 [更新、卸载与恢复](vm-update-uninstall-recovery.md)。这些场景使用各自资格应用和输入，当前 Notes 会话没有任意应用的一键升级/卸载界面。

重新执行场景时，更新用 `new-linux-host-runtime.ps1`，卸载用 `new-linux-host-uninstall-runtime.ps1`，均需要对应成功 cold、完整 Tools manifest、geometry/source-v2 probe资产及包卷。以新 StatePath/EvidenceRoot 执行：

```powershell
& $RuntimeScript -RuntimeStage new -StatePath $StatePath `
  -Repo $Source -EvidenceRoot $RuntimeEvidence -RuntimeInputs $RuntimeInputs `
  -ColdStatePath "$ColdEvidence/generation.json" -AssetsVolume $AssetsVolume `
  -PackagesVolume $PackagesVolume -ToolsManifestName $ToolsManifestLeaf
foreach ($StageName in 'storage-probe','storage-reopen','prepare','fixture') {
  & $RuntimeScript -RuntimeStage $StageName -StatePath $StatePath
}
# 审阅新导出的 review-base.json 后，提供已审定的 ReviewPath。
& $RuntimeScript -RuntimeStage bundle -StatePath $StatePath -ReviewPath $ReviewedReviewFile
foreach ($StageName in 'admission','pair','export','verify','replay') {
  & $RuntimeScript -RuntimeStage $StageName -StatePath $StatePath
}
```

各阶段单次尝试，失败保留。运行入口不自动构造 geometry/probe 或批准 review。`pair` 执行真实双启动，`verify/replay` 校验既有证据。卸载保留私有数据；擦除数据需要另外定义显式操作。

## 当前限制

正常关机与受控进程恢复不证明 guest 崩溃、断电、文件系统损坏或介质恢复。没有裸机安装、物理输入或硬件资格结论；开发密钥不用于生产发布。历史 APFS 输入无法访问；完整离线签名 APT 闭包未完成。完整 boot Python 历史检查为543通过、1失败、2项边界跳过，唯一失败仍是旧 frozen probe 缺失，未用 source-v2 替代。许可随对应组件及源码保留，二进制工具链保留其既有来源和许可范围。
