# P01 全新环境部署设计

日期：2026-11-08。项目状态：调查及分任务实施，尚未交付。项目总表见 [未交付项目](../../undelivered-projects.md)。用户已授权顺序实施、子 Agent 协作、测试、文档和向现有远程开发分支提交，无需重复常规审批。

## 项目完成条件

从可信外部摘要指定的交接包出发，在受支持宿主上的全新独立 Linux Docker daemon 中，加载固定工具链、恢复源码和缓存，以空 Rust target 实际编译宿主、离线强制重编 Kotlin 应用，重新构造绑定这次构建的 probe、bundle 和 runtime pack。随后在新数据盘实际运行 Notes/Motion，保存、正常关机、跨容器重开并验证保存内容。公开单入口须完成前置检查、按阶段留存证据及失败停止；独立审查须能逐条确认构建产物实际进入新运行包。

已有开发交付不等同于 P01：复用旧 runtime pack 启动、重放旧构建证据、加载镜像到已有 daemon 均不能关闭本项目。全新 daemon 验收仅证明独立空存储环境；没有另一台物理电脑时不声明跨物理宿主验收。

## 已有输入及约束

- 现有交接包：`artifacts/mingos-vm-dev-20261107-0c9f60f/handoff.tar`，3544954880 bytes。
- archive SHA256：`cb8ef776f35584c915ad4b72d322e1973a06ca9b856c8790625e6b23a09dc744`。
- handoff manifest SHA256：`7a4ad1336705de97fd51eb1602fed4d852d54935f3ee3f6764ad96d2ce2d1a68`。
- source descriptor SHA256：`26b90b37e3abaad293d0fafd3aae626909ed549607e42aee19557077f53e3ef0`。
- Tools image：`sha256:a085efa9e8b4958b1943ee87857313f3d2666f696f730f242daef643347b0091`。
- 原交付目录、历史失败代、容器、卷、磁盘和日志保持原样。新增输出只能使用新代，不清理、不重置、不原地重试失败代。
- 不放宽历史资格 driver，不替换旧冻结 probe 的固定摘要；新开发 probe 使用自己的来源合同。
- 固定二进制编译工具链仍是构建依赖，不声明工具链源码自举或逐位可复现。
- 原 macOS/APFS 资产当前不可访问，P01 不将新的 Linux 证据作为这些历史资产的替代。
- `formalQualification=false`、`finalSystemAccepted=false` 在本项目保持。

## 环境方案比较及选择

1. 独立真实 Linux VM：有独立内核及存储，适合完整验收，但当前宿主未发现现成的独立虚拟化运行环境。
2. 新 WSL distro：可以有独立 VHD/daemon，但共用 WSL 基础设施；现有 Windows 冷构建入口的路径合同也需要单独适配。
3. 固定 Tools 镜像中的非特权 QEMU TCG：创建独立 guest 内核、磁盘、Docker/containerd store，不挂宿主 Docker socket，可保留现有 Desktop。以此作为当前可行性实验方向；只有实际空 store、容器运行和固定 OCI index 载入通过后，才进入正式环境实现。

不采用 privileged DinD、Docker Desktop reset、修改现有 data-root 或清理镜像来证明空环境。外层 Tools 容器只是新 guest 的运行依赖；外层已有镜像不计入 guest 的空 store 证明。部署文档必须说明宿主 PowerShell/Python/Docker Desktop 前提及首次工具链导入步骤。

## 阶段边界

| 阶段 | 交付物 | 必须证明的行为 |
| --- | --- | --- |
| S1 | 公共严格 handoff 导入模块 | 三个外部 pin、完整 sibling allowlist、独占源码恢复、完整只读重开，失败留存 |
| S2 | 新环境及 Docker transport | 明确 executable/endpoint、独立 guest/store、首次空清单、固定三镜像 index 身份；现有 daemon 前后保持 |
| S3 | 源码/缓存消费者及应用构建 | 新消费者、只读复核、实际离线强制 Kotlin 编译、Node 和工具链观察的实际字节核验 |
| S4 | 开发冷构建及 probe | 新开发合同、实际空 target Rust 编译、新 probe 绑定本次源码和构建，历史 driver 不变 |
| S5 | 新运行包装配 | 新 host/apps/probe/kernel/tools 完整闭包、来源链、initramfs 成员核验、独立只读重开 |
| S6 | 单入口及真实端到端验收 | 新 guest 中 S1–S5 实际串联，新数据盘保存/关机/重开，完整失败控制和操作文档 |

各阶段使用自己的可审查计划。S1 的完成只关闭 S1，不改变 P01 未交付状态；后续阶段按已验证接口制定计划，不用猜测的接口预先约束实现。

## S1 公共导入合同

新增 `scripts/boot/import_handoff.py`，复用 `current_source.py`、`handoff_manifest.py`、`workflow_pack.py` 和严格归档底层，不再依赖封存目录中的固定 helper 路径或 `/public` 隐式 import。

接口：

```python
restore(archive_path, archive_pin, handoff_pin, destination) -> dict
import_restore(archive_path, archive_pin, handoff_pin,
               source_descriptor_pin, output_directory) -> dict
reopen(archive_path, archive_pin, handoff_pin,
       source_descriptor_pin, imported_directory) -> dict
```

`restore` 只恢复完整 delivery；后两个接口提供组合恢复和重开。路径必须绝对、无链接；输出目录必须原先不存在。组合输出只含 `delivery/`、`source-repo/` 和 `import-restore-result.json`。外层 Docker wrapper 将其放在新卷里的新子目录，不要求覆盖挂载根。

archive 和 handoff pin 均为独立外部输入；组合接口另须独立 source descriptor pin，不能自动信任包内自报摘要。调用之前不改变当前工作目录、不修改全局 Docker context。S1 不创建 Docker 资源；后续 transport 参数化资源名和 endpoint。

导入只接受精确 `delivery` 根和 manifest 对应的全部文件/目录，拒绝缺失、多余、重复、链接、特殊文件、sparse、未知 PAX 元数据、非可移植路径、类型/大小/mode/hash 不匹配。兼容实际封存 PAX 归档中的 `path/mtime/atime/ctime`，不一概禁用 PAX 导致已有包不可用。最多4096个逻辑成员、每文件16GiB、manifest 8MiB、提取总量32GiB；超过上限立即失败。持有同一只读归档句柄并核对前后身份与摘要，不使用 `extractall`。

源码恢复前后独立核对 descriptor 和 standalone inventory；恢复后的完整文件集合、目录集合、字节和 POSIX modes 均须匹配。输出文件 fsync；失败保留部分输出，不写成功收据，不删除资源。已经成功或失败的输出目录都不能复用。

`reopen` 不写入 imported directory，只返回复核结果供调用者写入独立证据目录。要求 archive、imported directory 和当前 verifier 模块目录均处于只读文件系统；复核完整 delivery、源码、组合结果收据与根目录集合，拒绝任何新增路径或声明扩大。成功结果只能说明完整性和恢复，不得声称编译、guest 启动、发布者认证或正式资格。

CLI 子命令为 `import-restore`、`reopen`，均显式接受 `--archive`、`--archive-sha256`、`--handoff-sha256`、`--source-descriptor-sha256`，分别接受 `--output-directory` 或 `--imported-directory`。JSON 结果写 stdout，错误退出非零。Linux descriptor 语义为强制条件，Windows 不能以跳过恢复测试代替 Linux 验收。

## 验收证据

S1 需要 TDD 的真实归档和文件系统控制、不同错误 pin、恶意成员、已有目录/链接父路径、中途失败后拒绝重试，以及只读重开对变更内容/mode/多余路径的拒绝。另将实际3.54GB交接包导入新卷，并在不同的只读容器中重开，记录容器和卷身份、源码数量及全部摘要。

P01 总体验收另外需要全新 daemon 前后清单、实际镜像载入、全部编译命令/日志、空 target 观察、新产物到 pack 的逐项绑定和真实保存重开。任一阶段失败保留全部资源；不得通过只跑 S1 测试宣称 P01 完成。完整报告列出全部11项状态、内容和操作文档、changelog、精确本地及远程提交。
