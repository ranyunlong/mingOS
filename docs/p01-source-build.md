# P01 S3 源码、缓存与应用构建进度

更新：2026-11-08。S1严格导入、S2独立WSL环境已实际验收；本页记录S3逐任务结果，不能据此关闭S3或P01。完整合同见[设计](superpowers/specs/2026-11-08-p01-source-build-design.md)和[七任务计划](superpowers/plans/2026-11-08-p01-source-build.md)。

## Task 1：本地证明链准入与执行代码冻结

实现提交为`6403f8b`及审查修复`e02bfb4472247865321bf70b1e0a837afae0f763`。独立审查修复后Spec PASS、Quality Approved。公开模块`scripts/boot/source_build.py`目前只实现以下本地API：

- `qualify_source_build(spec, spec_sha256)`：以外部固定的原始spec摘要为入口，严格检查原S2 packet-v2、源码/独立verdict交叉引用、21个固定验收门、全部365项不可变证据、明确的外部引用和原3.54GB归档；返回本地准入记录。
- `freeze_harness(spec, qualification, output_directory=...)`：重新执行同一准入，不信任调用者修改后的dict；独占复制显式manifest闭包、核对摘要及目录集合、同步文件并保留失败输出。宿主mode与Linux目标0444/0555分别记录。

冻结输出在分配前保护所有已核验的证明文件、不可变清单成员集合、固定helper/harness闭包及checkout脏文件/删除记录。原清单的两个排除子树保持原语义；不会以禁止整个checkout目录的方式妨碍无关产物。Linux控制记录的tomllib路径仅作规范绝对POSIX语法检查，不在Windows访问该guest路径。

本任务不调用WSL/Docker、不分配运行环境、不编译应用。`run`/`reopen`编排和CLI仍由Task 6实现；`--help`不代表已有构建入口。冻结所需的完整最终S3执行闭包也要在后续模块完成后准备，不能把双文件控制夹具当作它。

## 已执行验证

| 验证 | 实际结果与证据位置 |
| --- | --- |
| 修复后Windows完整控制 | 29运行、28通过、1项Linux nofollow专属跳过，exit0；`.superpowers/sdd/2026-11-08-p01-source-build/task-1-fix1-host-green.log` |
| 主执行者冻结Linux控制 | 29/29、零跳过/失败/错误、exit0；`artifacts/p01-s3-task1-controls-20261108-9fe14f36/result.json` |
| 原真实证明链与归档演练 | 原packet-v2、365项清单及外部引用、3,544,954,880字节handoff经公开本地API校验，随后重新准入并冻结双文件夹具，exit0；`.superpowers/sdd/2026-11-08-p01-source-build/task-1-actual-local-proof-d803cd5b/result.json` |
| 独立修复复审 | Spec PASS、Quality Approved；`.superpowers/sdd/2026-11-08-p01-source-build/task-1-fix1-independent-review.md` |

Linux容器使用固定Tools镜像、只读根/代码、禁网、UID/GID65534、2CPU/4GiB/256pids、全部cap删除和NNP，停止后非OOM，容器及日志保留。冻结代码pin：`source_build.py`39120字节/SHA256`6f2bdbcdcfd5fcffc2ce804f83b6d236c91dc4d19b1cd25fe0b95ff991d8a4ae`；测试35107字节/SHA256`ff86220252e24c38b320d63f4bef1ef1f4eb4bac16f96235d10528802bc4fa95`。

真实原件演练仅证明本地准入和双文件夹具冻结；没有重新执行S2运行时或S3源码构建。原始24项控制、首轮审查结果及全部RED/GREEN证据保留，未覆盖或改判。Windows标准库没有可移植的目录fsync；POSIX执行目录fsync。首次收据创建前若文件系统失败，只能保留已分配目录，不能保证失败收据落盘。

## 后续任务与边界

Task 2设计已明确固定spec进入传输/完整harness接口、输入阶段双份容量预算、独立观察证据例外和失败代锁定，并通过独立接口复核；传输实现尚未验收。观察bootstrap/adapter独立冻结，不能依赖尚未传输的harness；S2 helper保持原字节。

adapter字节权限链已补充独立设计复核PASS：外部固定spec绑定原HOST harness manifest中的`wsl_environment_transfer.py`固定角色，safe-open本机模块并核对大小/摘要，冻结内嵌loader和实际argv，再在guest准入证据目录中核验安装字节后导入。它不依赖未完成的guest harness，也不增加独立pin文件或spec字段。

开发检查点`artifacts/p01-s3-task2-controls-20261108-042dfe82/result.json`保留冻结源码、精确容器及状态：真实Linux管道/进程控制10/10、零跳过/失败/错误，exit0，1.086秒。该容器仅为root-owned Unix socket控制夹具，使用capdropALL/NNP/只读根及代码/禁网/2CPU/4GiB/256pids；不作为实际S3受限worker验收。同一冻结副本在Windows执行主机二进制管道、超时和adapter角色拒绝控制3/3，exit0，0.329秒，记录为`.superpowers/sdd/2026-11-08-p01-source-build/task-2-host-core-b1640e17/`。当时公开入口和bootstrap尚未完成，已知监督与持久登记工作继续；这不是Task2完整GREEN、源码审查或实际传输验收。

Tasks 2–7仍须完成受限传输、seal及真实Docker wrapper、原源码/缓存恢复、两profile六次真实Kotlin编译、工具字节核验、独立只读回放、封存导出、完整编排和实际S3验收。随后还有S4冷构建、S5装包、S6端到端安装及保存重开。11项目总交付仍0/11。

`formalQualification=false`、`finalSystemAccepted=false`、`coldBuildExecuted=false`、`guestStarted=false`。完整项目状态见[未交付总表](undelivered-projects.md)，变更见[changelog](changelog.md)。
