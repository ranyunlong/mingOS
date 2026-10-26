# GPU 直接呈现与图像缓存设计

日期：2026-10-24。承接用户“继续”对上一阶段建议的授权，范围为既有渲染链路的架构升级。

## 目标与边界

Kotlin应用、SDK v1/v2导入、帧格式及原有资源预算不变。保留默认CPU参考后端、无窗口GPU像素回归和显式PNG导出。native --renderer gpu 改为 GPU compute → GPU buffer → fragment pass → wgpu Surface；不再经过softbuffer或每帧CPU读回。普通CPU窗口仍使用softbuffer。

## 设备与生命周期

窗口线程创建与Surface兼容的硬件适配器和共享GpuSession；宿主监督线程复用同一设备执行compute。设备操作、错误作用域、资源更新和呈现由每会话Mutex串行化，避免跨线程错误作用域交错。所有Validation/OutOfMemory/Internal和未捕获错误继续记录首个终态错误，向监督进程传播。每次compute提交仍等待有界GPU完成（2秒），限制在途资源，明确这不是完全异步调度器。

帧内容显式区分CPU像素与GPU buffer。GPU帧持有会话和资源；替换旧帧后，wgpu管理提交资源的生命周期。Surface在窗口之前释放。共享设备只属于一次应用运行，不跨应用共享图像或句柄。

Surface优先非sRGB格式；sRGB目标显式转换颜色，防止重复编码。缩放采用与参考窗口一致的最近邻逻辑。零尺寸窗口暂停呈现；Timeout跳过且不打开输入门闩，Lost/Outdated重新配置后有限重试；OOM及其他终态故障使会话失败，不以旧帧报告成功。窗口/物理像素预算沿用上一阶段。

## 图像缓存

每个GPU会话使用最多32MiB的图像atlas storage buffer，shader单独读取图片像素和每帧命令/字形。按有效图像句柄分配连续像素区间；相同图像再次绘制不上传。关闭句柄释放区间。碎片阻止连续分配但总量允许时紧凑重排并重传，明确统计这类上传。无效帧先验证，不修改缓存或最后成功帧。

独立纯Rust布局分配器CacheLayout::new(capacity_pixels)、sync(&BTreeMap<i32,usize>)->Result<CacheUpdate>、release(id)->bool。CacheUpdate有offsets:BTreeMap<i32,u32>、uploads:Vec<i32>、compacted:bool。sync验证全部输入、删除失效项、为新增项first-fit，必要时紧凑重排；失败原子保留旧布局。capacity_pixels不超过8,388,608，最多8个正句柄，长度必须为正且总量不超容量。

## 可观测与验收

Outcome报告presentationBackend、gpuStats，包括imageUploads/imageUploadBytes/sceneUploadBytes/readbackBytes/renderSubmissions/surfacePresentations。原生不请求snapshot时readbackBytes必须为0；显式snapshot允许一次有界读回，并在最终序列化前刷新统计。PNG读回不计入compute渲染耗时，作为单独统计；不得用新旧不同范围的计时直接宣称倍数。

headless gpu保留逐像素CPU对照。硬件测试验证缓存复用、关闭后重用、碎片紧凑、错误帧原子性，以及GPU呈现shader的缩放与颜色结果。原生窗口执行有界会话并验证实际wgpu-surface成功呈现、无隐式读回。Linux CPU/Wayland和全功能编译重新验收；Linux没有硬件GPU则不声称其GPU surface通过。

GPU直接呈现不等于已达成持续60/120Hz或系统合成器。本阶段不增加字体、阴影、模糊、内核隔离或启动镜像。
