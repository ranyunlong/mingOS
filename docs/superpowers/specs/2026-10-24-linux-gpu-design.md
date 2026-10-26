# Linux 执行链路与 GPU 场景光栅化设计

日期：2026-10-24。承接已批准的 Linux 底座、Kotlin/Wasm 应用与 SDK 边界，作为下一阶段的具体实现范围。

## 环境与范围

实测 Windows 主机包含 NVIDIA GeForce RTX 4070 Laptop GPU，驱动版本 32.0.15.6636。WSL 只有停止的 docker-desktop 分发版，Docker Linux 引擎未启动；尚无 Linux/Wayland 验收环境。用户随后明确授权启动 Docker；已启动 Docker Desktop 29.8.0，增加独立 Linux 容器的实际构建及 Weston headless Wayland 验收。不会改动现有业务容器。

交付两个可独立验收的部分：可选 wgpu GPU 场景后端，以及 Linux 原生构建/验收入口。Linux 结果只有在实际运行后才能标为通过。默认 CPU 后端继续工作。

## 架构

Kotlin → Wasm worker → parent broker → validated Frame → CPU 或 GPU 光栅化 → RenderedFrame。GPU 后端在宿主进程中运行；应用无设备、驱动、shader、路径或原生句柄访问权。SDK v1/v2 和两个已构建的 Kotlin 应用均不修改。

GPU 使用显式选择的 wgpu 27.0.1（DX12/Vulkan）、pollster 0.4.0 和 bytemuck 1.25.2。每个像素由 compute shader 执行按顺序的矩形、圆角、图像、文字与裁剪命令，使用整数混色、最近邻图像采样和相同点阵字体。CPU 仅打包有界命令/资源与读取结果，不预先生成整帧。保留 CPU 的 32,000,000 像素工作预算，并为实验性 GPU 后端额外限制像素×命令候选检查为 64,000,000；超额明确拒绝，避免高复杂度 shader 调度。记录该实验限制与 CPU 允许集合的区别。

GPU 错误作用域覆盖 Validation/OutOfMemory/Internal；未捕获错误记录到会话状态，终态错误必须使监督进程返回失败，普通非法帧可恢复。GPU 设备会话复用，资源快照只存在于调用期间；图像删除、错误帧、未知句柄和工作量检查都先于提交。只选择非 CPU 类型的图形适配器；没有硬件适配器或功能未编译时明确失败，不自动换成 CPU 后宣称 GPU 成功。报告真实适配器名称、驱动、设备类型、图形 API、渲染模式和耗时定义。

本阶段 RenderedFrame 仍读回 CPU；原生窗口继续由 softbuffer 呈现。它验证 GPU 场景计算而非最终的零读回合成器。渲染耗时包含打包、上传、GPU 执行、同步和读回，不是 GPU timestamp，不含设备初始化、窗口呈现和输入延迟。初始化耗时单独报告。

## CLI 和 Linux

CLI 增加 --renderer cpu|gpu，默认 cpu；GPU 是可选 Cargo feature，未编译时显式报错。CPU 的旧行为、资源预算和确定性像素保持不变。

Linux 脚本要求原生 Linux、固定 Rust 工具链、C 编译器与 Wayland/X11 构建库。headless 验收运行两个现有 Wasm 应用；Wayland 模式要求有效 XDG_RUNTIME_DIR/WAYLAND_DISPLAY socket，并禁用 X11 环境变量，无法连接时失败。脚本保存系统、会话、应用产物 hash、运行结果；每条路径有独立结果，不把 headless 或跳过计为 Wayland 通过。

## 验收

先写失败测试：渲染器参数、共享验证、GPU/CPU 完全一致（透明度、圆角、非 ASCII fallback、裁剪、负坐标、大幅图像缩放、资源关闭和错误帧保留）。GPU 测试必须显式运行、无适配器即失败；一般测试不能假装 GPU 已验证。运行真实 Kotlin 查看器，保存 CPU/GPU 相同输入的 PNG 和统计。Linux 脚本在缺少环境时只能交付为待验收，不能宣称 Linux/Wayland 已通过。

## 后续

GPU surface 直接呈现、图像纹理缓存、增量更新、抗锯齿、字体、Wayland 合成器及 Linux 强隔离为后续独立阶段。
