# 当前系统验收状态

更新：2026-11-08。当前 Windows + Docker/Linux 开发环境已可完成真实 Kotlin 来源构建、Rust 冷构建及 kernel VM 资格。原 macOS/APFS 环境与完整备份目前不可访问；历史冻结、失败代和驱动保持原样。

交付记录及发布补记已推送至 `codex/persistent-update`，基线提交为 `ecfe2763454638a4bf2e6808c830c20ac01dd769`；原远程推送授权阻断已解除。本机封存交付目录不变。当前按[未交付项目总表](undelivered-projects.md)推进11项工作：P01的S1公共导入已完成真实封存大包导入和独立只读重开，完整1561文件/406目录源码核对通过；S2新WSL daemon已通过三镜像载入及受限容器实验，公共环境入口继续。S3–S6及P01总体验收尚未完成，仍无新增整项项目交付。阶段证据见[S1验收](p01-strict-import.md)和[环境记录](p01-environment-audit.md)。

当前范围：`persistentUpdateNormalRebootVerified=true`；`persistentUpdateExplicitRecoveryVerified=true` 仅覆盖下述 Linux 受控进程中断与显式恢复。`formalQualification=false`、`finalSystemAccepted=false`。

S2公共入口create/observe/exec已实现并通过Task4代码复核，提交`f00331c`。首次正式新代`554bde65`的create退出0，但独立实际审计发现插件命名空间实际为`plugins.moby`，与本代专属值不符，因此S2未通过；原代、失败观察和全部资源保留。首轮修复补齐实际命名空间准入/回放检查并通过冻结Linux回归139项运行、138通过、1项Windows junction专属跳过及独立代码复核；但新代`8039baf3`在launch阶段因固定Docker拒绝单数JSON键而失败。已保留停止发行版的离线导出和原始错误；固定二进制`--help/--validate`证实应省略两种plugin JSON键并使用复数CLI选项，受影响修复和另一个全新代实际验收继续，不将配置校验当作实际namespace通过，见[环境记录](p01-environment-audit.md)。

2026-11-07 开发交付已生成：`artifacts/mingos-vm-dev-20261107-0c9f60f/README.md`。3544954880字节归档（SHA256 `cb8ef776f35584c915ad4b72d322e1973a06ca9b856c8790625e6b23a09dc744`）已在不挂载原checkout的情况下严格导入、恢复完整源码及独立只读重开；原始三阶段 Notes SDK 回放一致。导入 runtime 投影后，以恢复的1561文件源码实际启动 Notes/Motion，新盘空便签，正常桌面POWER及精确exit0通过。该本机交付不包含全新daemon加载或可迁携用户数据恢复结论。具体pins、失败保留和源码快照/最终记录区别见 [本次交付记录](vm-delivery-release-20261107.md)。

## 已验证范围

- cold `89161e5b1ad44f96` 完成全部十条固定 release 命令：all-targets 775 passed、0 failed、24 ignored；三个真实 controller 和 public integration 各1/0/0/0，recovery 父矩阵18/0/1/0。固定镜像、禁网、单 writer、空 target、UID65534、2CPU/4GiB及1800/900秒上限保持。
- runtime `b30e37e37a5a46c3` 在同 bundle、同128MiB ext4盘上完成两次 fresh QEMU 启动。boot1 完成0.2.0更新试运行、Ready/Close/Result/nativeExit/reap、持久提交和正常同步关机；boot2 无重建或恢复，重开相同版本、catalog和私有数据。原 checker 强 raw、精确 native 因果门均通过。
- 公共入口 export、verify、replay 实际通过；密封导出3502文件，manifest SHA256 `8de918c038441e39630b7e88772031708e2c9f3cb4dbcae0cc3fd47b13540740`。独立回放报告 `linux-portable-raw-normal-reboot-verified`。
- 可迁移压缩包 `artifacts/environment-rebuild-20261107/portable-archive-a5b41592e057/persistent-update-portable.tar.gz` 为191417046字节，SHA256 `ab0a551aa08f8abb6dad22fc2ab8cb15fa5e1d895962ccc9dadc90d22e4f56b5`；3503个常规文件（含manifest）逐项hash及文件mode匹配，898个目录名称/类型及观察到的mode匹配。
- 显式恢复独立审阅匹配615个案例、770个kill/reap身份、18个父测试及9条真实Host观察。范围是受控进程中断、SIGKILL/reap和显式恢复，不包含 guest 崩溃、断电或介质损坏。
- qualification 资产恢复工具支持固定digest镜像加载及严格USTAR匿名卷恢复。实际synthetic并发恢复、独立RO reopen、sentinel保持和Linux worker控制通过；历史资产尚未恢复。

## 继续交付的门槛

开发工作流已有独立 pack、磁盘生命周期和公开外层。实际 bundle 完成独立 initramfs 核对，运行包232文件、22目录完成封装、新消费者复制及独立只读重开，manifest SHA256 `273b38ceb8665d92743d8c4820939fb2c2a858deee4bb330e4af886b3fb7d1e6`。公开外层真实三阶段验收通过：首轮保存 A 后追加未保存编辑并正常关机；第二个容器初载精确 A、保存 B、正常关机；离线备份恢复到新卷后，第三个容器初载精确 B、零 save、正常关机。完整日志/SDK/磁盘回放 SHA256 `04e7156313fa64ad7b0b0a81668076311ea06b5ef191add522c7a3b0547b6698`，`threeStagePersistenceVerified=true`。备份仍依赖同daemon保留的卷、prior容器和lineage。

真实 Notes 首轮的 End no-op 超时和下一代 POWER 完成时序失败完整保留，不续用、不改判。已分别修正单键派发证据与已完成计划的布局检查；Linux公共生命周期控制113/113、零跳过，真实三阶段验收使用全新代 `5de8a419` / `6bf321a8`。诊断收集器另有命令上下文及退出后tmpfs结果导出失败，均保留原失败记录；成功导出与SDK runner经新控制后在新证据目录实际通过。

最新阶段补充：卸载新代 `28aef8a17bbb44d2` 已使用完整输入通过同盘双启动和公共 export / verify / replay，manifest SHA256 `b7e35a77f3ebbd49f7b3b4442b2c57128940c3482d3b27ce9acb608c33543df6`。便携归档已完成，187107454 字节，SHA256 `c1b9e6aaaf433d234102b42ada5130b30ca9a490f323f955b1257f5fa57fbd1b`，3479 个文件及 887 个目录逐项匹配。下面 f500a8a3 的缺失输入失败仍为历史失败，不续用或修改。

恢复后的源码/Gradle 已实际重新编译 Text Entry 和 Motion，宿主 46 文件及编译/依赖/编排链独立回放通过。恢复源码/Cargo 的新 Rust 冷构建 `f7e24f3a64814d49` 已完成七条命令：776 通过、0 失败、24 忽略；Host 和 helper 与原冷构建 9436 字节一致。主执行者另在只读容器中复核全部源码、工具、命令、产物和原容器最终配置。完整开发预览包已独立核验，公开外层 `new → pair` 实际双启动及原始日志回放通过；Notes 的独立离线编译也已通过，内容持久化及跨容器恢复三阶段均已通过，见 [Notes 开发会话](vm-workflow.md)。

1. 独立 Linux 持久卸载：新四包来源重编/回放及七命令 cold 9436c4cf 全部通过（776通过、0失败、24忽略），完整输入新代 28aef8a1 已完成双启动及公共导出回放。历史 runtime f500a8a3 虽完成同盘双启动，独立 verify 因漏带 rust-image.json、node、toolchain-observation.json 失败；该代不续用、不改判，全部盘/卷/容器/日志保留。更早 5d98a41e 的 PID0 启动锚点失败与针对同次唯一 native launch 的修复记录仍保留。
2. 用户确认优先交付可复建的x86_64 VM开发系统，含应用、持久数据、更新/卸载和恢复说明。源码/缓存迁移、恢复输入的实际冷构建与应用重编、真实Notes三阶段均已完成；最终整包装配、移离原checkout的严格导入和只读复核以独立交接收据记录，见[交付操作](vm-delivery.md)。物理鼠标专项仍暂停；TCG、虚拟输入和软件呈现不能作为物理硬件资格。
3. 历史APFS资格不能由新Linux证据替代；280个deb的观察记录也不构成完整签名离线APT重建闭包。

新开发入口阶段：完整开发包只读验证器已独立Linux40/40；卸载checker、开发profile、固定镜像迁移和共享boot控制在真实Linux合计65/65、零跳过。三个开发工具链归档实际只读验证通过，但未实际加载到全新daemon。Kotlin公开包装器已从封印源码kd-ff1e822e完成全新卸载四包和开发双包实际离线编译；两代均通过独立只读容器回放，主执行者另重放宿主导出包及编排收据链，逐文件检查封印前后摘要。原Gradle缓存及1487个封印源文件保持不变。源码和Cargo/Gradle缓存已实际归档并恢复到新消费者卷，独立只读复核通过（1487源文件/392目录、12495 Cargo文件、2496 Gradle文件）；长/短宿主源码投影另逐文件核验。恢复输入的实际Kotlin构建及Rust冷构建均已通过；Notes开发VM真实三阶段已通过，完整交接的外部导入结果由独立收据记录。各项范围、公开命令与待完成门槛见[开发系统交付说明](vm-developer-system.md)。

完整阶段记录见 [连续开发记录](continuous-development.md)、[当前环境记录](development-environment-20261107.md)、[Linux更新资格计划](superpowers/plans/2026-11-07-linux-host-qualification.md) 与 [Linux卸载资格计划](superpowers/plans/2026-11-07-linux-host-uninstall-qualification.md)。大产物按仓库约定保留本机，不提交Git。

失败代及RED包：`artifacts/environment-rebuild-20261107/failure-evidence-archive-20261107-98ab2bdd71/failed-runtime-and-red-evidence.tar.gz`，1716306393字节，SHA256 `2e7527696320fe2a92f5bddc751086d5b5cbd624c18faf2a8ba106b0faa49764`。覆盖5个失败runtime及相关控制，共9293个常规文件/符号链接，含三张原失败盘；独立成员hash/链接target与源前后清单一致。只保留证据，不赋予历史恢复或系统通过含义。

完整boot Python测试发现546项，实际543通过、1失败、0错误、2项非Linux边界跳过。唯一失败是原legacy frozen隔离探针文件缺失；现存source-v2字节不能替代该历史固定hash。该缺口继续保留，当前不声明全部测试通过。
