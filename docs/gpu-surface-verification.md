# GPU直接呈现与缓存验收

日期：2026-10-24。阶段已完成，随后按连续开发路线推进字体。上一阶段计算/读回记录保留在gpu-verification.md，本记录对应新的GPU Surface链路。

## 实现

Kotlin/Wasm与SDK v1/v2保持不变。CPU默认、headless GPU读回、显式PNG导出继续支持。原生GPU路径为验证后的命令→compute输出buffer→fullscreen fragment pass→wgpu Surface；不再调用softbuffer或每帧把像素传回CPU。

每应用GpuSession串行管理同设备操作和错误作用域。GPU帧持有自己的输出buffer与会话，图片关闭不影响已计算的帧。compute仍有2秒上限的完成等待，以限制在途资源，尚未改成异步帧调度器。

图片使用独立32MiB atlas，按句柄缓存、释放和必要时紧凑重排；命令和字形仍逐帧上传。缓存布局11项纯Rust回归覆盖first-fit、合并、紧凑、原子失败等。实际硬件回归验证重复绘制只上传一次、关闭后释放及后续重用、显式像素读取与CPU一致。

## 真实原生窗口

NVIDIA GeForce RTX4070 Laptop GPU，DX12，驱动32.0.15.6636；格式Bgra8Unorm。最终固定开发二进制分别执行8秒有界会话；两次都正常退出并成功呈现2帧。

| 指标 | 普通GPU会话 | 显式PNG会话 |
| --- | ---: | ---: |
| compute提交 | 2 | 2 |
| 成功Surface呈现 | 2 | 2 |
| 图像上传次数 | 1 | 1 |
| 图像上传字节 | 2621440 | 2621440 |
| 场景上传字节 | 5304 | 5304 |
| 呈现参数上传字节 | 64 | 64 |
| 像素读回字节 | **0** | **3344000** |

1100×760×4=3,344,000字节，显式PNG只读回一帧。统计在导出后刷新。应用退出主动关闭图像，因此cachedImages=0；这不代表测量了驱动总VRAM占用。

导出PNG SHA256为2520ef2f8404195f29158117128f5f6245d0d8641ec25ed7a6440a0f76e2a70a，与CPU参考和Linux同画面一致。它是应用导出帧，不冒充操作系统截图。

## 颜色、故障与生命周期

硬件离屏测试执行真实呈现shader，覆盖RGBA/BGRA UNORM与sRGB四种目标、两组不等比缩放；UNORM与CPU颜色精确一致，sRGB允许每通道1级量化差，alpha精确一致。

故障回归先观察失败再修复：Surface跳过后保留待重绘状态，即便序号和逻辑尺寸相同也会重试；零物理尺寸和关闭状态不重试。Lost/Outdated仅有限重配，Timeout不计成功或打开输入门闩。

wgpu的已识别native fatal poll/present/discard通过窄unwind边界转成终态错误；资源保护对象保证已有错误时仍执行清理，第二次清理失败不覆盖首错误。GPU要求panic=unwind。独立device-lost回调不获取会话Mutex；分配、配置和最终统计驱动poll投递。真实硬件Device.destroy回归验证空闲丢失会阻止最终成功、之后render与snapshot均拒绝。panic回归是注入测试，不声称复现了真实驱动崩溃或物理热插拔。

## 检查结果

| 检查 | 结果 |
| --- | --- |
| Windows默认Rust测试 | 42通过，0失败 |
| 无默认feature Rust测试 | 24通过，0失败 |
| all-features Rust测试 | 56通过，0失败；5项硬件测试默认忽略 |
| 显式硬件专项 | lib3项与integration2项全部通过，0跳过 |
| 默认/all-features/headless engine,gpu 的Clippy all-targets -D warnings | 通过 |
| Rust格式检查 | 通过 |
| Node测试 | 24通过，3项Windows条件跳过，0失败 |
| JavaScript语法检查 | 通过 |
| SDK1原生/参考宿主 | 输出一致，79请求，1587fuel |
| Linux默认Rust | 42通过，0失败 |
| Linux SDK1/SDK2 headless与Wayland | 通过；Wayland2提交、2成功呈现 |
| Linux all-features/all-targets | 编译检查通过 |
| Linux验收脚本 | Linux60通过；Windows59通过、1项AF_UNIX条件跳过 |
| 父流程源码/配置/产物重验 | passed，errors=[] |

Linux证据在artifacts/linux-gpu-surface-20261024-final。Linux实际渲染是Weston/Pixman CPU路径；未执行Linux硬件GPU或GPU Surface，不用编译检查代替运行。

## 计时与证据

最终5组headless CPU/GPU PNG完全一致。CPU最小/中位/最大41.826/45.077/49.931ms；缓存后的headless GPU读回3.717/5.862/7.955ms。原生会话逐帧compute时间另见JSON。两种模式计时范围不同，不混算提升倍数；背景Linux/原生会话未严格控制，这些样本不支持P99或持续60/120Hz、输入延迟结论。

GpuTiming保留executeReadbackMicros字段名：headless包含读回，native仅包含执行等待；以backend和readbackBytes解释。渲染时间不含最终Surface呈现、垂直同步、PNG编码或应用冷启动。

[汇总与源码哈希](../artifacts/gpu-surface/acceptance.json)、[普通窗口](../artifacts/gpu-surface/native-no-snapshot.json)、[显式截图窗口](../artifacts/gpu-surface/native-snapshot.json)、[导出PNG](../artifacts/gpu-surface/native-snapshot.png)、[5组比较](../artifacts/gpu-surface/comparison/comparison.json)、[Linux汇总](../artifacts/linux-gpu-surface-20261024-final/summary.json)。

开发二进制SHA256：9365a829df3322ac0610c766ed5d45dc27249595ead20df7a87aaba76aafe66c。当前两个Kotlin/Wasm产物与上一阶段一致。未来阶段改代码后本记录保留为历史，不假设旧哈希持续匹配工作区。

## 复现

~~~powershell
.\scripts\rust.ps1 build --locked --features gpu
.\.cache\rust-target\debug\ming-app-host.exe view artifacts/image-viewer/app.json --grant 1=samples/image-viewer/data/coastal.png --renderer gpu --auto-close-ms 8000
.\.cache\rust-target\debug\ming-app-host.exe view artifacts/image-viewer/app.json --grant 1=samples/image-viewer/data/coastal.png --renderer gpu --auto-close-ms 8000 --snapshot artifacts/gpu-surface/native-snapshot.png
.\scripts\gpu-verify.ps1 -Samples 5 -OutputDir artifacts/gpu-surface/comparison
~~~

完整架构与限制见ADR0005和本阶段设计；下一阶段为可分发字体和明确的文本布局/测量API，完整IME、系统合成器和启动镜像仍分别验收。
