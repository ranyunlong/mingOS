# Notes 开发会话

`developer-workflow-v1` 是独立的开发会话合同：Notes（SDK6）和 Motion（SDK5），严格隔离，持久目录 `/data/mingos`。它使用正常桌面的虚拟键盘、鼠标和 POWER 按钮。会话最长 900 秒，driver 监督上限 945 秒，外层上限 1950 秒；超时失败并保留现场。

Notes 的私有目录由可信宿主按已选择的包身份提供，无需共享存储授权对话框。输入文字后点击 **保存** 才写入，关闭不会自动保存。启动会读取已有便签；**重新读取（替换编辑内容）** 会丢弃尚未保存的编辑。读取或写后核对失败会锁定保存。状态区分目录同步已确认、同步未确认及写入失败。当前演示会记录有效便签明文字节，请只使用非敏感测试内容。

## 交互接口

受控 Linux runtime 内的可信 `scripts/boot/workflow_cli.py` 提供 `status`、`capture`、`click x y`、`key qcode`、`type text` 和 `power x y`。外层入口负责创建和监督容器；这些内部命令本身不会配置 Docker 隔离或证明数据已持久化。控制目录必须来自本次正常启动的独立 tmpfs，不能从旧实例复制。

每条命令绑定私有 Unix socket 凭据、本次实例、单调序号、当前 session、窗口布局和输入 epoch。Motion 帧可持续推进 revision；窗口、包实例、存储提示或其他输入变更会使先前观察失效。仅在未发送输入的布局拒绝后，CLI 最多重新获取三次状态；已接受或超时的命令不会自动重发。唯一 QMP owner 使用既有 InputPlan 和原生 pointer-ready 回执，不开放原始 QMP 或 TCP 服务。

`type` 接口支持最多 512 个拉丁字母、数字、空格及换行；Notes 本身只接受最多 512 UTF-8 字节的单行内容。中文及其他 Unicode 文本的编辑模型已有测试，但当前 CLI 没有相应的输入方法。截图、虚拟输入和 TCG 运行均不能证明物理输入或硬件支持。

## 当前验证范围

公开 `workflow_pack.py` 使用独立的 `workflow-manifest.json`、profile 和 purpose；旧预览格式与新工作流互相拒绝。`workflow_vm.py` 提供 verify/init/start/inspect/reopen/backup/restore。运行、重开和结果封印持有同一持久 EX 锁；每次容器使用新 tmpfs。正常重开还要求上一容器已正常退出的外层观察、完整历史和磁盘 SHA256。备份只读复制停止后的完整实例；恢复写入全新工作卷、创建新实例身份并保留原备份及历史。

跨进程握手先同步完整数据，再独占创建空的 `.ready` 文件；消费者只在该完成标记出现后读取。独立测试曾真实复现空文件竞争，修复后公共生命周期、pack 和交互组合控制在 Linux 中 73/73 通过。生命周期测试使用明确的 guest/formatter/pack 入场替身，且删除 fixture 前要求显式环境开关、uid/gid65534 和四个独立 tmpfs 挂载。

Notes/Motion 的实际离线强制重编、包适配和独立回放已通过；Notes 另通过 14 个真实 Kotlin/Wasm 存储合同和 4 个 Compose 输入/保存保护场景，存储能力使用替身。新控制接口及共享 boot/profile/input/pointer 控制在 Linux 中 143/143 通过，包含真实 Unix socket 和实际 main 参数入场，QMP 为明确替身。

旧 `developer-vm-v1` 仍是固定 15 秒 Text Entry/Motion 预览，拒绝输入脚本和资格角色。其公开外层已实际完成同盘双启动、正常同步关机及原始结果回放。这不能替代 Notes 保存内容、正常跨容器重开、离线备份及新卷恢复；这些交付门槛仍在实施。

参见 [开发系统交付范围](vm-developer-system.md)、[复建输入](vm-rebuild-inputs.md) 与 [更新、卸载和恢复](vm-update-uninstall-recovery.md)。当前 `formalQualification=false`、`finalSystemAccepted=false`。
