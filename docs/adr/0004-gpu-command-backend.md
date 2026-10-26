# ADR 0004：先验证 GPU 场景计算，再接直接呈现

日期：2026-10-24。状态：采用。

## 背景

SDK v2 已有 Kotlin 查看器、按命令提交的帧与 CPU 参考光栅化。上一阶段实际渲染约45–78ms，尚不满足流畅桌面的目标。GPU 需要处理绘制工作，单纯上传已经由 CPU 画好的整帧不能验证这条路线。

## 决策

增加可选 Cargo feature gpu 和 CLI --renderer cpu|gpu，默认仍为 CPU。固定 wgpu 27.0.1、pollster 0.4.0 和 bytemuck 1.25.2；版本通过 Cargo.lock 重现，此选择不表示它们是最新版本。Windows 使用 DX12，Linux 使用 Vulkan。只接受适配器报告为 DiscreteGpu 或 IntegratedGpu 的设备；初始化失败明确返回，不自动转成软件设备。

GPU compute shader 每个线程负责一个像素，按顺序执行 SDK 命令，使用整数混色、最近邻采样和既有字体。所有指令先通过共同的帧、资源和32M像素工作验证；GPU 再施加64M像素×命令候选检查预算。它是本实验后端的额外限制，并未修改 SDK 线格式。裁剪在宿主解析为逐条命令的状态；图像和字形按有界数据打包，无自定义 guest shader。

CPU 和 GPU 共用宿主资源授权。图像句柄不能携带文件系统路径、设备指针或驱动调用。SDK 与应用不因后端选择发生变化。最大场景存储36MiB，每帧输出16MiB以内；这不是宿主总 RSS 或 VRAM 的硬隔离承诺。

所有三类 wgpu 错误作用域都会捕获并排空，并配置未捕获错误处理器。首个终态设备错误被共享状态记录，之后拒绝提交；监督进程在提交后和正常结束前检查它，不能以旧帧返回成功。普通非法帧和实验工作量超限仍可恢复。

## 替代方案

- 直接做完整 GPU 图形管线和合成器：更接近最终性能结构，但同时改变混色、字体、窗口和资源生命周期，难以定位行为差异。本阶段先保持整数像素完全一致。
- 只将 CPU 帧上传纹理：可以试验 surface，但没有迁移光栅化工作，不符合本阶段目标。
- 仅构建 Linux 二进制：不能说明两个实际应用及图形会话行为一致，必须另行执行。

## 代价与下一步

当前每帧重新打包图像并读回 GPU 输出，由 softbuffer 呈现。compute 中每像素遍历命令的复杂度不会随裁剪自动降低，因此必须限制候选检查。没有新增抗锯齿、模糊、阴影、字体整形或最终系统合成器。

bytemuck 只在宿主中安全转换有界 u32 数组和字节切片。打包显式转换为 little endian；读回检查对齐并恢复主机字节序。GPU 渲染计时包含打包、上传、执行、等待和读回，不是纯 GPU timestamp；设备初始化单独记录。未来应优先实现 GPU surface 直接呈现、图像缓存和增量更新，再引入高质量文字和视觉效果。

## 参考

- wgpu 官方 API 文档：https://docs.rs/wgpu/27.0.1/wgpu/
- 固定依赖的 Device::on_uncaptured_error、ErrorFilter、PollType 和 ComputePipelineDescriptor 源码在项目 .cache/cargo/registry 中可复查。
- 验收记录：docs/gpu-verification.md；Linux：docs/linux-runtime.md。
