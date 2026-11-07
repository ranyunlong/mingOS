# 2026-11-07 VM 开发交付记录

本次交付按用户确认的范围：可复建的 x86_64 VM 开发系统，含 Notes/Motion、持久盘、更新/卸载验证和恢复说明。交付目录为仓库下 `artifacts/mingos-vm-dev-20261107-0c9f60f`，从其中 `README.md` 开始。大产物保留本机，不进入 Git。

| 项目 | 实际值 |
| --- | --- |
| handoff.tar 字节数 | 3544954880 |
| archive SHA256 | `cb8ef776f35584c915ad4b72d322e1973a06ca9b856c8790625e6b23a09dc744` |
| handoff manifest SHA256 | `7a4ad1336705de97fd51eb1602fed4d852d54935f3ee3f6764ad96d2ce2d1a68` |
| 源码快照 commit | `0c9f60f10e10ec501ee1a58270dffa918ad1090b` |
| 源码 descriptor SHA256 | `26b90b37e3abaad293d0fafd3aae626909ed549607e42aee19557077f53e3ef0` |
| 独立公开校验工具 code-pins SHA256 | `1a636da9e62cede4cde71d6f2ca0fb88d3e5bb8b317b491570b175e3c7dab44a` |
| workflow manifest SHA256 | `273b38ceb8665d92743d8c4820939fb2c2a858deee4bb330e4af886b3fb7d1e6` |

归档包含 runtime pack、当前源码、Cargo/Gradle 缓存、三个固定工具链镜像归档、更新/卸载便携证据、许可和完整收据。实际导入时未挂载原 checkout；只读 tar 经完整摘要校验后严格恢复到新卷，独立只读容器再核对完整闭包。源码1561文件、406目录在 Linux 恢复及只读重开后，另在 Windows 逐文件重哈希匹配。NTFS 副本不声称保留 POSIX 模式。

导入后的原始 Notes SDK 证据回放得到与原件完全相同的 review 字节，SHA256 `04e7156313fa64ad7b0b0a81668076311ea06b5ef191add522c7a3b0547b6698`，`threeStagePersistenceVerified=true`。对应真实流程是保存 A/丢弃未保存后缀、第二容器读 A/保存 B、新卷恢复后第三容器初载 B/零保存，三次正常关机。该回放没有重新启动 guest。

随后从导入包投影 runtime 到新卷根，完整文件模式/空目录及8次全局 writer 扫描通过。使用完整恢复源码 `artifacts/sr-0c9f60f` 和该投影 pack，在全新代 `artifacts/w` 实际启动 Notes/Motion，严格隔离均生效。新盘便签为空；未编辑或保存，通过当前桌面 POWER 正常关闭，两应用资源回收，精确容器退出0且非 OOM。此补充场景只证明导入后可运行，不新增持久化结论。

- 实际运行 CID：`f5eedeeb3027c0db9474dd9d8b28101ea2d0063dbb7578ad20e301d3ee3b4465`。
- boot：`47e30b75-c9ed-4aba-9c96-b9e680378186`；session：`ming-209-1794066078075556721`。
- 数据盘 UUID：`4c71363f-7d44-4fa3-8160-f6b9d59351fe`；停止后 SHA256：`51d52fffc6c45d88b1a156600614b1e966971256dae90adb63b21ee9d37f3885`。
- driver：`workflow-session-normal`，`normalBoots=1`；history tip：`846b1fcd0de8b692d52bbb56878947208d9214f697754206400ac73739a26731`。

Windows 两次长路径 `new` 在源码冻结阶段触发 WinError206，尚无 Docker 资源；`artifacts/v` 另因受沙箱限制的 Docker 查询留下失败记录，后续输入被驱动拒绝。全部失败代、日志、盘、卷及容器保留；`w` 是新代，未清除旧锁存或放宽驱动。详见短路径预检和 [工作流操作](vm-workflow.md)。

交付外层文档及本记录的后续提交不属于 tar 内源码快照；不重新生成归档来追逐文档提交。runtime 的历史构建来源也与当前源码快照分别绑定，不声称当前快照重新编译了该 runtime 的全部内容。

相关验证：独立冻结的完整源码/交付 Linux 控制54/54、零跳过；恢复输入的 Rust 七命令冷构建776通过、0失败、24忽略；真实 Kotlin 离线应用编译及更新/卸载同盘双启动通过。历史完整 boot Python 检查仍有旧 frozen probe 缺失的一项失败（543通过、1失败、2跳过）。本次仅增加交付与文档记录，不把局部控制扩大为全套通过。

固定二进制工具链是复建输入；全新 daemon 镜像加载未实测。用户数据恢复依赖同 daemon 的保留卷、原容器与 lineage，不能单凭归档迁机；没有断电/损坏恢复、裸机安装或物理硬件资格结论。`formalQualification=false`、`finalSystemAccepted=false` 保持。远程推送此前被自动审批以目标及内容授权未明确为由拒绝，未重复尝试；本地提交与交付产物保留。

## 2026-11-08 发布补记

用户明确批准推送目标及内容后，远程 `git@github.com:ranyunlong/mingOS.git` 的 `codex/persistent-update` 已从5dc34ea快进到交付记录提交 `0bb11c744e00862454cb137d54fe1d356eb2d759`，独立远程查询精确匹配。本补记随后也发布到同一分支。原推送授权阻断已解除，交付目录内的封印时状态记录保持其历史含义；本补记不修改 tar、源码快照或外层目录清单，也不扩大正式系统验收范围。
