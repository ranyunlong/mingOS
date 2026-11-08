# P01 环境可行性记录

日期：2026-11-08。本记录是部署环境调查证据，不是 P01 完成交付记录。完整门槛见 [P01 设计](superpowers/specs/2026-11-08-p01-clean-bootstrap-design.md)。

## 已实际运行的独立 guest

固定 Tools 镜像 `sha256:a085efa9e8b4958b1943ee87857313f3d2666f696f730f242daef643347b0091` 包含 Linux 6.12.113+deb13-amd64、QEMU、静态 BusyBox 和所需模块，但未包含 Docker 引擎。新增开发环境输入为 Docker29.8.1 static archive，86055881 bytes，SHA256 `d8db66739d2e28d4933786d73e918d9be643a67fbd835db1bf740d650a259e70`；已记录 HTTPS 来源和成员摘要，不宣称生产发布者签名认证。

实验使用非特权、无宿主 socket、只读根、UID65534、drop ALL capabilities、no-new-privileges、network none 的外层容器，QEMU TCG 启动独立 Linux guest。每代新建独立2GiB ext4磁盘；旧盘从未重跑。

| 实验 | 实际结果 | 范围 |
| --- | --- | --- |
| guest-a | daemon启动且store为空，容器start因 `pivot_root: invalid argument`失败，PID1退出导致panic | 失败。即使QEMU/外层exit0，也不能改判成功 |
| guest-b | `DOCKER_RAMDISK=true`单变量诊断后，BusyBox容器实际运行/exit0，daemon正常停止并关机 | 证明initramfs根限制的原因；overlay2模式不满足最终OCI index合同 |
| guest-c | switch_root到新tmpfs，不用DOCKER_RAMDISK；containerd snapshotter启用，空store，真实容器运行/exit0，daemon停止、ext4卸载和ACPI关机 | 独立内核及新daemon的最小容器可行性通过；尚无固定三镜像加载或冷构建结论 |

guest-c daemon ID：`81326b10-1096-4e41-a8a0-0303a61dd95e`；driver `overlayfs`，driver-type `io.containerd.snapshotter.v1`，cgroup2/cgroupfs。磁盘 UUID：`f29087cd-b382-4214-8147-162b2cb01f23`。QEMU耗时55.00秒，无timeout或OOM。

保留证据在本机 `artifacts/p01-guest-audit-20261108/`，含 `REPORT.md`、每代脚本/模块输入、命令、串口、结果、容器inspect与独立磁盘。b/c另保留精确initramfs；a的tmpfs initramfs未成功导出，只保留构造脚本及输入，不宣称其原始字节保留。

## 完整构建路径的下一步

单次新容器的 `/dev/kvm` 探测在启动前失败，设备不存在；保留Created容器和日志，未修改Desktop/WSL。TCG实验未做Rust/Kotlin编译基准，不能从最小容器的55秒推断冷构建性能。

为执行完整实际冷构建，继续评估独立WSL distro及其新VHD、新daemon和独立containerd store。当前只读升级查询已确认WSL服务可用，原来只有 `docker-desktop` WSL2发行版；普通沙箱下的 `E_ACCESSDENIED` 是访问限制，不是宿主WSL损坏证据。WSL版本2.7.14.0，kernel6.18.33.2-2，Windows10.0.26200.9711。

新distro实验不得修改默认distro、全局WSL配置、现有Desktop、现有socket或存储，不执行全局shutdown或unregister。禁用新distro的Windows盘自动挂载和interop；先实测cgroup/共享挂载边界，再决定是否启动只使用新配置及Unix socket的daemon。WSL共用基础设施，不声明独立内核或物理宿主隔离。最终部署方案以实际工具链加载、资源限制和冷构建证据选择。

## 状态

P01 S1公共严格导入模块正在实施，设计和计划已保存；本环境实验没有完成S2–S6。全部11项新项目仍为0项完成并发布；原开发VM交付与正式验收标志保持原范围。
