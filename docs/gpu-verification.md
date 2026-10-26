# GPU 场景后端验收记录

日期：2026-10-24。范围：Windows DX12 硬件 GPU 场景计算、CPU 像素对照和原生窗口呈现。Linux 验收独立记录在 [Linux 执行说明](linux-runtime.md)。

## 已完成行为

固定 wgpu 27.0.1、pollster 0.4.0、bytemuck 1.25.2。CPU 为默认；显式 --renderer gpu 才创建硬件设备。SDK v1/v2、两个 Kotlin/Wasm 应用、能力授权和 guest 导入均未修改。GPU 直接处理矩形、圆角、图像、点阵文字与裁剪，未上传 CPU 预渲染帧。

当前链路：Kotlin/Wasm → parent broker → 校验后的命令 → GPU compute → 有界读回 → softbuffer。它尚不是 GPU 直接呈现或系统合成器。

## 硬件与结果

- NVIDIA GeForce RTX 4070 Laptop GPU；适配器 DeviceType=DiscreteGpu；API=Dx12；驱动32.0.15.6636。
- 硬件测试显式执行，未将普通测试中的 ignored 算作 GPU 通过。
- 12个命令前缀、64组固定种子的场景、4096宽图像与百万坐标缩放、透明度、负坐标、圆角、文字 fallback、裁剪、资源删除及失败帧保留均与 CPU 完全一致。
- 新增注入测试覆盖 OutOfMemory/Internal 错误处理与首错误锁存，以及成功帧之后设备失败不能返回旧帧成功；实际硬件还以超限 buffer descriptor 注入分配验证失败，确认错误先被检查、无效缓冲不会进入映射；普通非法帧保持可恢复。
- 真实 Kotlin 查看器：相同脚本分别运行 CPU/GPU，5组输出 PNG SHA256 全部相同：d8ff3fa205120623de039cb6ceaa26a13afc9ceb7f2f6eaac873dbc24358b7fd。
- 12秒原生 GPU 窗口冒烟：windowSystem=win32，framesSubmitted=14，framesPresented=12，exitCode=0。会话记录收到12个位移事件和1次点击；最后一帧renderMicros=17757。这里只把含应用帧且 surface.present 成功的次数计为呈现，输出帧 PNG 不冒充操作系统截图。

## 跨平台画面对照

同一份查看器 Wasm 和图像，使用默认初始适应窗口状态，Windows CPU、Windows DX12 GPU、Linux无窗口CPU与Linux Wayland CPU 四条路径的导出PNG完全一致：SHA256=2520ef2f8404195f29158117128f5f6245d0d8641ec25ed7a6440a0f76e2a70a。后两条路径的具体运行与软件Weston边界见 Linux 记录。

Windows对应结果保留在 artifacts/gpu/windows-reference-fit.json、windows-gpu-fit.json 及同名PNG；它们与下面包含缩放/平移输入的5组样本是不同场景，不能混合计算耗时。

## 最终验证命令

| 命令 | 结果 |
| --- | --- |
| scripts/rust.ps1 test --locked --no-fail-fast | 41通过，0失败 |
| scripts/rust.ps1 test --locked --no-default-features --no-fail-fast | 24通过，0失败 |
| scripts/rust.ps1 test --locked --all-features --no-fail-fast | 41通过，0失败；另2项硬件测试默认 ignored |
| scripts/rust.ps1 -CargoArgs @('test','--locked','--features','gpu','--test','gpu','--','--ignored','--nocapture') | 场景硬件专项1通过，0跳过 |
| scripts/rust.ps1 -CargoArgs @('clippy','--locked','--all-features','--all-targets','--','-D','warnings') | 通过 |
| scripts/rust.ps1 -CargoArgs @('test','--locked','--all-features','--lib','--','--ignored','--nocapture') | 硬件分配失败专项1通过，0跳过 |
| 默认 features 的 Clippy all-targets -D warnings | 通过 |
| cargo fmt --all -- --check | 通过 |
| node --test tests/*.test.mjs tests/kotlin.integration.mjs | 24通过，0失败，3项Windows平台条件跳过 |
| node scripts/check.mjs | 通过 |
| node scripts/native-demo.mjs | SDK1原生/参考输出一致，79 requests，1587 fuel |
| scripts/rust.ps1 build --locked --features gpu | 通过，最终开发可执行文件保留GPU feature |
| scripts/gpu-verify.ps1 -Samples 5 | 5组完全一致，全部实际使用硬件GPU |

## 耗时方法与限制

Cargo dev 配置中仅 ming-app-host 的 opt-level=1；1100×760，15条命令，取同一次应用执行的第3帧（输入包含尺寸、缩放、平移与关闭）；每种后端5个独立进程样本。各次 GPU 进程设备初始化耗时另记。最终采样与Windows原生窗口冒烟依次执行；后台Linux容器构建及主机状态未严格控制，因此样本不是受控实验室基准。

| 后端 | 最小 | 中位数 | 最大 |
| --- | ---: | ---: | ---: |
| CPU参考 | 46.239ms | 49.361ms | 57.579ms |
| GPU计算与读回 | 6.536ms | 7.166ms | 7.762ms |

renderMicros 是宿主单调时钟测量，GPU 包含场景/图像打包、缓冲分配与上传、命令编码、执行等待、结果读回和复制；不含设备初始化、Wasm启动、PNG文件写入、窗口blit或垂直同步，不是 GPU timestamp。gpuTiming 分别给出 packMicros 和 executeReadbackMicros。

首次实现每字节序列化并逐像素转换，样本 GPU 中位46.529ms。分段定位后采用安全切片转换，保留明确字节序与对齐检查；优化后的所有像素回归继续通过。早期采样保留为 comparison-before-copy-optimization.json，仅解释本次具体优化，不与跨配置结果混作最终统计。

这5组样本不足以推出 P99、输入延迟、持续60/120Hz或整体桌面性能。下一步应减少图像重复上传和GPU读回，接直接surface呈现，再建立持续帧率/输入延迟测量。

## 证据与复现

- [最终比较数据](../artifacts/gpu/comparison.json)：所有样本、适配器、打包/执行耗时、应用/宿主/shader哈希。
- [GPU样本图](../artifacts/gpu/gpu-5.png) 与 [CPU样本图](../artifacts/gpu/cpu-5.png)。
- [原生GPU运行结果](../artifacts/gpu/native-gpu-run.json) 与 [原生导出帧](../artifacts/gpu/native-gpu-frame.png)。
- [设计](superpowers/specs/2026-10-24-linux-gpu-design.md)、[实施计划](superpowers/plans/2026-10-24-linux-gpu.md)、[ADR0004](adr/0004-gpu-command-backend.md)。

宿主 SHA256：5c31864421ae446b39836b9344f84f997891a5322161589db861c2ad811c2aac。

查看器 Wasm SHA256：2eb888508c0fb4ac0a27df4bdbe8dfff1b402c46c1318dff56b6297c5988de21，与上一阶段相同。SDK1 Wasm SHA256仍为83c17b2df1bf7872f3fd8fa5bf64450eaaf33425df08678219d1ebdaf6306c49。

## Linux 联合验收结果

最终记录位于 artifacts/linux-20261024-task3-reviewed-v2/summary.json 和 run/evidence.json。Linux原生Rust默认41项测试通过，all-features/all-targets编译检查通过；验收工具52项在Linux全部通过，Windows51通过、1项Unix socket条件跳过。SDK1/SDK2无窗口运行通过；Wayland结果为2提交、2成功呈现，windowSystem=wayland。

父流程独立重验当前源码/配置/产物指纹，status=passed、errors=[]。四条跨平台路径的初始帧哈希一致。普通结束和真实attach超时探测的容器均停止并核实最终状态。GPU和Linux验收工具评审发现的具体错误处理、容器清理及Cargo配置漂移问题已修复并复核。

## 明确未覆盖

Linux/Vulkan硬件GPU、物理Linux桌面驱动、多窗口GPU合成、零读回、字体整形与无障碍、编辑控件IME、模糊阴影和抗锯齿；Linux命名空间/seccomp/cgroup、宿主RSS/GC/VRAM总量硬隔离及操作系统启动镜像。GPU额外64M候选检查预算可能拒绝CPU允许的帧，当前是显式实验后端限制。
