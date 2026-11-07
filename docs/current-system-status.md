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

1. 独立 Linux 持久卸载资格：真实专用 Kotlin fixture、冷构建、专用 raw checker、七步/一步同盘双启动及独立导出回放。当前进入实现，尚无通过声明。
2. 按实际系统需求继续核对剩余日常工作流、系统集成与发布标准。物理鼠标专项仍暂停；TCG、虚拟输入和软件呈现不能作为物理硬件资格。
3. 历史APFS资格不能由新Linux证据替代；280个deb的观察记录也不构成完整签名离线APT重建闭包。

完整阶段记录见 [连续开发记录](continuous-development.md)、[当前环境记录](development-environment-20261107.md)、[Linux更新资格计划](superpowers/plans/2026-11-07-linux-host-qualification.md) 与 [Linux卸载资格计划](superpowers/plans/2026-11-07-linux-host-uninstall-qualification.md)。大产物按仓库约定保留本机，不提交Git。

失败代及RED包：`artifacts/environment-rebuild-20261107/failure-evidence-archive-20261107-98ab2bdd71/failed-runtime-and-red-evidence.tar.gz`，1716306393字节，SHA256 `2e7527696320fe2a92f5bddc751086d5b5cbd624c18faf2a8ba106b0faa49764`。覆盖5个失败runtime及相关控制，共9293个常规文件/符号链接，含三张原失败盘；独立成员hash/链接target与源前后清单一致。只保留证据，不赋予历史恢复或系统通过含义。
