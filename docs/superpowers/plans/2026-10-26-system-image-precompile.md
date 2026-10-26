# 系统镜像预编译实施计划

**Goal:** 以可信构建数据复用优化后的Wasm Module，保持所有SDK与隔离边界并完成实际kernel原全量验收。
**Spec:** ../specs/2026-10-26-system-image-precompile-design.md
**Constraints:** 无.git不初始化；CARGO_INCREMENTAL=0；只复用既有cache；旧源码/RED/盘不覆盖；物理鼠标暂停；未选中源码仍JIT；Native字节不来自运行时应用输入。

## 1. Runtime可信静态边界

- [x] 写default-empty、exact source/SDK miss、trusted real tiny Module恢复/独立Store/fuel、config mismatch、imports allowlist测试，先观察RED。
- [x] private opaque SystemModule和generated table，唯一unsafe点只用可信静态serialize输出；共同Engine配置与cache-hit portable选择，保留旧JIT。
- [x] Runtime观察来源metadata，保持所有原Result/native exit/watchdog/reap/cgroup检查。

## 2. 可信catalog构建工具

- [x] Native example使用既有catalog/package loader和precompile API；写新目录、固定数字文件名和generated table，不执行Wasm，不给SDK新入口。
- [x] 原生release生成实际两Kotlin产物；外层SHA绑定所有源/原Wasm/native数据；复制到独立源码快照编译系统Host。

## 3. 资格和独立复核

- [x] 适当Windows/Linux测试，原始SDK/真实Kotlin语义、两key/实例隔离、无额外燃料；独立安全/影响复核。
- [x] 全原QMP文字/POWER脚本、两实际进程、UID/namespace/cgroup/probes/reap/data/unmount/Powerdown；startup与执行来源可观测。
- [x] 同bundle同原盘两次fresh QEMU，先读真实基线、保持旧数据；Main独立审原始证据。
- [ ] 导出实际启动产物与复现入口，继续kernel生命周期恢复与完整goal；不把局部通过标成系统完成。

2026-10-26检查点：default Windows408/Linux238、builder5，enabled Linux238；Native真实3实例、v18原full kernel28、v19原盘8→9→10 pair与export私有10→11 replay均独立通过。导出完成，最后“继续恢复/完整goal”仍待执行，不标系统完成。
