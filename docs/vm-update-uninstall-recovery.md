# VM 更新、卸载与恢复记录

当前交付目标是 x86_64 开发 VM。更新、卸载和开发预览分别使用独立的输入合同；不能在开发预览包内替换应用文件后沿用原来的摘要或验收结论。

## 已完成的真实 VM 验证

更新代 `b30e37e37a5a46c3` 在同一 128 MiB ext4 盘上完成 0.2.0 更新及两次正常启动，重开相同 catalog、版本和私有数据。公共 export / verify / replay 通过。可迁移归档位于本机 `artifacts/environment-rebuild-20261107/portable-archive-a5b41592e057/persistent-update-portable.tar.gz`：191417046 字节，SHA256 `ab0a551aa08f8abb6dad22fc2ab8cb15fa5e1d895962ccc9dadc90d22e4f56b5`。

卸载代 `28aef8a17bbb44d2` 使用完整运行输入完成两次正常启动。原始证据确认安装、可信 UI 确认卸载、事务提交、移除 catalog 项与能力撤销，私有 `note.txt` 内容 `alpha` 保留；第二次启动没有重新安装或重置数据。公共 export / verify / replay 均通过。归档位于 `artifacts/environment-rebuild-20261107/uninstall-portable-archive-d6d3597139be/persistent-uninstall-portable.tar.gz`：187107454 字节，SHA256 `c1b9e6aaaf433d234102b42ada5130b30ca9a490f323f955b1257f5fa57fbd1b`。3479 个常规文件和 887 个目录逐项匹配。

上述数据来自独立资格场景中的应用；`alpha` 不是用户在 Notes 中输入保存的内容，不能替代后续笔记工作流验收。卸载的含义是移除应用和能力，当前不会擦除私有内容；需要清除数据时必须另行定义显式操作。

## 迁移后核对原始证据

从包外独立取得归档摘要，先验证压缩包，再仅将经过检查的成员解包到全新目录。保持 Linux 文件模式，保留原件；不要把含失败运行的目录当作可续跑实例。将导出目录只读挂载为 `/sealed`，可信公共源码只读挂载为 `/trusted`，结果挂载为独立新目录 `/results`。固定 Tools 镜像中执行：

```sh
python3 -I -B /trusted/scripts/boot/linux_host_uninstall_export.py verify \
  --export /sealed --output /results/uninstall-verify.json
python3 -I -B /trusted/scripts/boot/linux_host_uninstall_export.py replay \
  --export /sealed --output /results/uninstall-replay.json
```

更新归档使用相同形式的 `linux_host_export.py`。结果文件必须不存在，输出不能位于封印目录内。运行前独立核对 `linux-export-manifest.json`：更新为 `8de918c038441e39630b7e88772031708e2c9f3cb4dbcae0cc3fd47b13540740`，卸载为 `b7e35a77f3ebbd49f7b3b4442b2c57128940c3482d3b27ce9acb608c33543df6`。入口先核对全部字节，再由导出自身的封印 driver 重放；replay 不启动 guest，不代表重新执行了一次更新或卸载。

## 失败与恢复边界

任何启动、输入、同步、监督或证据检查失败后，保留该代容器、卷、盘和日志。当前入口拒绝原地重试、重格式化或把不完整结果改判通过。需要重新验证时，先诊断原因，再使用全新输出代；不要对旧卷执行 prune、清空或修复命令。

开发源码和缓存丢失时，按 [复建输入归档](vm-rebuild-inputs.md) 从独立固定的 descriptor、源码归档、依赖归档恢复到新卷，再实际重新编译。工具链镜像归档须经 `development_image_migration.py` 核验；全新 daemon 加载仍没有实际验收记录。原 macOS/APFS 资格依赖当前不可取得，不以 Linux 新证据替代。

已完成的显式恢复演练仅覆盖 Linux 受控进程中断、SIGKILL/reap 和显式恢复：615 个案例、770 次 kill/reap 身份、18 个父测试及 9 条实际 Host 观察。它不覆盖 guest 崩溃、断电、文件系统损坏或介质恢复。正常关机前同步成功也不证明这些耐久性。

开发 VM 的正常跨容器重开、离线数据盘备份及在新卷恢复仍须由后续独立工作流实现和实测；当前 `developer-vm-outer.ps1` 仅提供同容器内的预览双启动。`formalQualification=false`、`finalSystemAccepted=false` 保持。
