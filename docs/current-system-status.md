# 当前系统验收状态

更新：2026-11-07。当前 Windows + Docker/Linux 开发环境已可完成真实 Kotlin 来源构建、Rust 冷构建及 kernel VM 资格。原 macOS/APFS 环境与完整备份目前不可访问；历史冻结、失败代和驱动保持原样。

当前范围：`persistentUpdateNormalRebootVerified=true`；`persistentUpdateExplicitRecoveryVerified=true` 仅覆盖下述 Linux 受控进程中断与显式恢复。`formalQualification=false`、`finalSystemAccepted=false`。

## 已验证范围

- cold `89161e5b1ad44f96` 完成全部十条固定 release 命令：all-targets 775 passed、0 failed、24 ignored；三个真实 controller 和 public integration 各1/0/0/0，recovery 父矩阵18/0/1/0。固定镜像、禁网、单 writer、空 target、UID65534、2CPU/4GiB及1800/900秒上限保持。
- runtime `b30e37e37a5a46c3` 在同 bundle、同128MiB ext4盘上完成两次 fresh QEMU 启动。boot1 完成0.2.0更新试运行、Ready/Close/Result/nativeExit/reap、持久提交和正常同步关机；boot2 无重建或恢复，重开相同版本、catalog和私有数据。原 checker 强 raw、精确 native 因果门均通过。
- 公共入口 export、verify、replay 实际通过；密封导出3502文件，manifest SHA256 `8de918c038441e39630b7e88772031708e2c9f3cb4dbcae0cc3fd47b13540740`。独立回放报告 `linux-portable-raw-normal-reboot-verified`。
- 可迁移压缩包 `artifacts/environment-rebuild-20261107/portable-archive-a5b41592e057/persistent-update-portable.tar.gz` 为191417046字节，SHA256 `ab0a551aa08f8abb6dad22fc2ab8cb15fa5e1d895962ccc9dadc90d22e4f56b5`；3503个常规文件（含manifest）逐项hash及文件mode匹配，898个目录名称/类型及观察到的mode匹配。
- 显式恢复独立审阅匹配615个案例、770个kill/reap身份、18个父测试及9条真实Host观察。范围是受控进程中断、SIGKILL/reap和显式恢复，不包含 guest 崩溃、断电或介质损坏。
- qualification 资产恢复工具支持固定digest镜像加载及严格USTAR匿名卷恢复。实际synthetic并发恢复、独立RO reopen、sentinel保持和Linux worker控制通过；历史资产尚未恢复。

## 继续交付的门槛

最新阶段补充：卸载新代 `28aef8a17bbb44d2` 已使用完整输入通过同盘双启动和公共 export / verify / replay，manifest SHA256 `b7e35a77f3ebbd49f7b3b4442b2c57128940c3482d3b27ce9acb608c33543df6`。便携归档已完成，187107454 字节，SHA256 `c1b9e6aaaf433d234102b42ada5130b30ca9a490f323f955b1257f5fa57fbd1b`，3479 个文件及 887 个目录逐项匹配。下面 f500a8a3 的缺失输入失败仍为历史失败，不续用或修改。

恢复后的源码/Gradle 已实际重新编译 Text Entry 和 Motion，宿主 46 文件及编译/依赖/编排链独立回放通过。恢复源码/Cargo 的新 Rust 冷构建 `f7e24f3a64814d49` 已完成七条命令：776 通过、0 失败、24 忽略；Host 和 helper 与原冷构建 9436 字节一致。主执行者另在只读容器中复核全部源码、工具、命令、产物和原容器最终配置。完整开发预览包已独立核验，公开外层 `new → pair` 实际双启动及原始日志回放通过；Notes 的独立离线编译也已通过，内容持久化及跨容器恢复继续，见 [Notes 开发会话](vm-workflow.md)。

1. 独立 Linux 持久卸载：新四包来源重编/回放及七命令 cold 9436c4cf 全部通过（776通过、0失败、24忽略），完整输入新代 28aef8a1 已完成双启动及公共导出回放。历史 runtime f500a8a3 虽完成同盘双启动，独立 verify 因漏带 rust-image.json、node、toolchain-observation.json 失败；该代不续用、不改判，全部盘/卷/容器/日志保留。更早 5d98a41e 的 PID0 启动锚点失败与针对同次唯一 native launch 的修复记录仍保留。
2. 用户确认优先交付可复建的x86_64 VM开发系统，含应用、持久数据、更新/卸载和恢复说明。公共只读developer pack验证器已通过主执行者独立Linux40项控制；源码/缓存迁移、实际干净重建与开发会话仍在实施，详见[交付范围与检查入口](vm-developer-system.md)。物理鼠标专项仍暂停；TCG、虚拟输入和软件呈现不能作为物理硬件资格。
3. 历史APFS资格不能由新Linux证据替代；280个deb的观察记录也不构成完整签名离线APT重建闭包。

新开发入口阶段：完整开发包只读验证器已独立Linux40/40；卸载checker、开发profile、固定镜像迁移和共享boot控制在真实Linux合计65/65、零跳过。三个开发工具链归档实际只读验证通过，但未实际加载到全新daemon。Kotlin公开包装器已从封印源码kd-ff1e822e完成全新卸载四包和开发双包实际离线编译；两代均通过独立只读容器回放，主执行者另重放宿主导出包及编排收据链，逐文件检查封印前后摘要。原Gradle缓存及1487个封印源文件保持不变。源码和Cargo/Gradle缓存已实际归档并恢复到新消费者卷，独立只读复核通过（1487源文件/392目录、12495 Cargo文件、2496 Gradle文件）；长/短宿主源码投影另逐文件核验。恢复输入的实际Kotlin构建及Rust冷构建均已通过；开发VM真实启动和完整交接仍待执行。各项范围、公开命令与待完成门槛见[开发系统交付说明](vm-developer-system.md)。

完整阶段记录见 [连续开发记录](continuous-development.md)、[当前环境记录](development-environment-20261107.md)、[Linux更新资格计划](superpowers/plans/2026-11-07-linux-host-qualification.md) 与 [Linux卸载资格计划](superpowers/plans/2026-11-07-linux-host-uninstall-qualification.md)。大产物按仓库约定保留本机，不提交Git。

失败代及RED包：`artifacts/environment-rebuild-20261107/failure-evidence-archive-20261107-98ab2bdd71/failed-runtime-and-red-evidence.tar.gz`，1716306393字节，SHA256 `2e7527696320fe2a92f5bddc751086d5b5cbd624c18faf2a8ba106b0faa49764`。覆盖5个失败runtime及相关控制，共9293个常规文件/符号链接，含三张原失败盘；独立成员hash/链接target与源前后清单一致。只保留证据，不赋予历史恢复或系统通过含义。

完整boot Python测试发现546项，实际543通过、1失败、0错误、2项非Linux边界跳过。唯一失败是原legacy frozen隔离探针文件缺失；现存source-v2字节不能替代该历史固定hash。该缺口继续保留，当前不声明全部测试通过。
