# x86_64 VM 开发系统交付

2026-11-07 用户确认首个交付范围：可复建的 x86_64 VM 开发系统，含应用、持久数据、更新/卸载和恢复说明。

这里的重建允许使用已核验的固定 Rust、Tools、Kotlin 二进制工具链镜像，要求在新的源码/缓存副本和空 target 中实际构建 MingOS、应用和 VM，并检查实际产物。工具链或 Debian 全部从源重建、完整签名离线 APT 闭包、逐字节可复现不属于已确认范围；现有来源、离线、签名和冻结检查仍须满足。

当前启动形态是 QEMU 直接加载 kernel/initramfs，并挂载独立 ext4 数据盘。当前公开运行协调器支持 Windows、PowerShell 7.3+ 和 Docker Desktop Linux 容器，并要求准确的 Docker VHDX 路径。它不是 BIOS/UEFI ISO 或裸机安装器。已有 VM 使用软件呈现与虚拟输入，物理鼠标工作仍暂停。

## 已提供的完整性检查

`scripts/boot/developer_pack.py` 是独立、只读的 Linux 开发包验证器：

```sh
python3 -I -B /repo/scripts/boot/developer_pack.py verify \
  --pack /pack --manifest-sha256 <独立取得的精确小写SHA256>
```

调用者应保持输入不可变；正式使用时将 `/pack` 只读挂载。验证器不运行包内代码，不调用 Docker 或创建磁盘。manifest 要求闭合的 `developer-vm-v1` 开发预览格式，逐项校验全部文件、目录、名称、类型、单链接、大小、模式和 SHA256，拒绝额外成员、符号链接及特殊文件。上限是单文件16GiB、总量32GiB、4096成员；缓存应封装为归档文件。返回前完整重开每个成员并核对身份和目录成员集合；这不是针对恶意并发宿主的原子快照。

`developer-pack-bytes-verified` 只表示外部固定摘要下的完整字节检查。来源收据被绑定，但该验证器不重放构建、不认证发布者、不启动 VM。开发样例密钥 `mingos-dev-2026` 不是生产发布密钥。`guestBooted`、`sourceBuildReplayed`、`publisherAuthenticated`、`formalQualification` 和 `finalSystemAccepted` 均保持 false。

## 固定工具链镜像迁移

`scripts/boot/development_image_migration.py` 使用独立的开发镜像合同；默认只读验证三个固定归档：

```sh
python3 -I -B /repo/scripts/boot/development_image_migration.py \
  --archive-root /archives --result /evidence/new-verification.json
```

目录必须包含 `rust-88624fbbfcb6.tar`、`tools-a085efa9e8b4.tar` 和 `kotlin-5fba561826e8.tar`。工具固定其完整大小、SHA256 和 OCI index ID，逐层核对 descriptor、blob、解压后的 diffID、唯一 linux/amd64 运行目标和无标签 Docker manifest。unknown 平台的 attestation 必须精确绑定运行目标；这里只检查其字节和 subject，不认证其声明。历史 qualification 恢复入口和原固定 digest 合同保持独立。

显式添加 `--load` 才调用 Docker 加载；工具先验证全部三个归档，再在每次加载前重验对应打开文件的身份、大小和摘要，不改标签、不删除资源、不下载。调用者必须始终保持归档不可变；结果路径须为父目录已存在的全新文件，加载前写入并同步初始收据，普通失败保留已尝试和已完成的操作。收据原地更新，不是断电原子 journal。当前三个真实归档的只读验证和失败路径控制已通过；尚未在全新 daemon 实测加载及 index ID 保持，不能称镜像迁移已验收。

## Kotlin 应用源码构建入口

`scripts/boot/kotlin_delivery.py` 要求已有固定 Kotlin 镜像、完整封印的 Gradle 缓存文件清单，以及独立源码目录。输出必须全新且位于源码目录之外，父目录须已存在：

```sh
python3 -I -B /source/scripts/boot/kotlin_delivery.py \
  --repo /source --output /evidence/new-kotlin-generation \
  --baseline /inputs/dependency-baseline.json \
  --original-cache mingos-original-gradle --profile developer
```

`developer` 真实编译 Text Entry（SDK6）和 Motion（SDK5）；`uninstall` 编译四个卸载资格包，`update` 编译六个更新资格包。使用 `--synthetic-controls` 只执行输入复制、只读拒绝和失败收尾控制，不编译。可用 `--docker` 指定 Docker 可执行文件。

每代创建独立源码、缓存、工作和输出卷；原始源码和缓存只读挂载，复制后比对完整清单。编译固定禁网、非 root、只读根、2 CPU/4GiB，强制离线重执行编译任务并使用新工作目录。每次创建容器前重验卷身份，编译后另起容器只读回放每个包的源码、编译收据和依赖链。失败代不能原地续跑；卷、容器及日志全部保留。宿主 `packages-and-evidence` 导出仍须由外层逐文件封印和重验，容器内回放不单独证明宿主副本完整。

## 开发预览会话边界

源码与 Cargo/Gradle 归档已有公开入口及实际新卷恢复证据，命令、输入布局和范围见 [VM 复建输入归档](vm-rebuild-inputs.md)。完整开发交付仍需后续实际构建、开发会话和交接验收。

恢复后的源码与 Gradle 消费者已实际重新编译开发双包，独立容器和宿主回放通过。实际开发 bundle `developer-public-bundle-e512a7d9` 已生成并另起只读检查：完整 newc 流及 54 项必需文件、内核和配置均匹配。完整 pack 与真实开发 guest 仍待验收；以下预览不能保存 Text Entry 内容，因为当前该样例只有内存文本状态。

公开 `scripts/boot/developer-vm-outer.ps1` 配置固定 Docker 外层、可信源码快照、全新工作/结果卷及完整 pack 只读挂载。它只接受新的证据目录和包外 SHA256，不接受包内 launcher 作为可信执行代码：

```powershell
./scripts/boot/developer-vm-outer.ps1 -Action new `
  -Evidence C:/path/to/repo/artifacts/new-developer-session `
  -PackVolume <已独立验证的完整包卷，包位于卷根> `
  -PackManifestSha256 <包外精确小写SHA256>
./scripts/boot/developer-vm-outer.ps1 -Action pair `
  -Evidence C:/path/to/repo/artifacts/new-developer-session
```

`new` 固定 14 项 Linux 可信源码、宿主 helper、Tools image inspection 和卷身份；独立只读验证完整 pack。`pair` 在同一受控容器内初始化新盘并执行两次 start/inspect。每步仅可尝试一次，失败现场保留。PowerShell 34 项控制及独立 Linux driver 8 项控制通过；当前没有该公开外层真实 guest 成功记录。实际 pack 摘要和可运行代须从后续独立交接记录取得，示例占位符不代表已有发布包。

独立 `developer-vm-v1` bundle profile 固定 Text Entry 与 Motion、15秒严格隔离会话及 `/data/mingos`，禁止输入脚本、测试事件、资格角色和安装/更新 fixture。所有 payload 文件、Host 来源收据、两包编译收据和 source-v2 probe 构建收据须精确绑定。该入口绑定已审来源，不在验证阶段重新运行编译器；真实冷构建、应用编译和独立回放仍须另外执行。当前 profile 控制已通过，真实开发 bundle 和 guest 工作流仍待执行。

`scripts/boot/developer_vm.py` 提供 `verify`、`init`、`start`、`inspect` 生命周期入口。`init` 和 `start` 要求 Linux UID/GID65534、完整包只读挂载，以及固定私有目录 `/work/mingos-boot-infra-20261025`；包内 bundle 位于 `/pack/bundle`。协调器只执行独立安装的可信 `scripts/boot/vm.py`，不导入包内代码。新建数据盘固定128MiB，已有实例或部分初始化不能覆盖、重格式化或续建。

在已配置的同一受控 Tools 容器内，命令形状如下；`<pin>` 必须从包外独立取得。外层容器封装、真实包及guest验收尚待完成，这些命令不单独配置Docker隔离或证明应用数据已保存：

```sh
python3 -I -B /trusted/scripts/boot/developer_vm.py init \
  --pack /pack --manifest-sha256 <pin> \
  --work-root /work/mingos-boot-infra-20261025
python3 -I -B /trusted/scripts/boot/developer_vm.py start \
  --pack /pack --manifest-sha256 <pin> \
  --instance /work/mingos-boot-infra-20261025/developer-instance.json \
  --output /results/boot-0001 --runtime-root /run/mingos-runtime
python3 -I -B /trusted/scripts/boot/developer_vm.py inspect \
  --instance /work/mingos-boot-infra-20261025/developer-instance.json
```

第二次 `start` 仅将输出改为全新 `/results/boot-0002`，继续使用同一个实例、数据盘和包摘要。每次尝试独占实例锁，写入不可覆盖的尝试/结果/监督日志，并检查正常关机、kernel powerdown、disk UUID、marker递增及从未复用的boot/session身份。失败或中断锁存该代；不清除锁存，不原地重试。监督器超时或等待异常只清理其创建的进程组，保留中断原因及清理错误。

`inspect` 只读打开已有实例锁及同盘runtime lease，检查磁盘superblock和全部历史，不创建锁、不修复状态、不运行子进程。第一次 `start` 建立lease前，或切换到全新runtime tmpfs后，`inspect` 会拒绝缺失lease。初始验证应在同一容器/runtime内按 `init → start → inspect → start → inspect` 执行。两个正常marker只证明正常同盘重开，不证明应用内容持久化、异常断电恢复或最终交付。集成生命周期在独立Linux控制中40/40通过；真实guest仍待执行。

## 交付前必须完成

1. 公开的源码、Cargo/Gradle缓存和固定镜像迁移接口，使用全新目标实际完成 MingOS、应用及 VM 的重建，并独立核验导出副本。
2. 独立开发会话、全新数据盘创建与正常重开入口，以及应用内容保存、重开、更新和卸载的实际验证。
3. 与交付来源一致的运行包、完整清单、命令和恢复说明；在移离原 checkout 后执行交接流程。
4. 如实列出工具链依赖、许可材料、受支持宿主、开发密钥与恢复演练范围。正常关机和受控进程恢复不代表断电或介质损坏恢复。

15秒预览、截图或盘 marker 连续性是中间验证，不能替代所选开发系统的交付。当前尚无完成上述全部门槛的发布包。实际阶段状态见 [系统状态](current-system-status.md) 和 [连续开发记录](continuous-development.md)。
