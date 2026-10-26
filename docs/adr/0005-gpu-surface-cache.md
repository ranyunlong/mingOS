# ADR 0005：共享GPU会话、直接Surface呈现与有界图像缓存

日期：2026-10-24。状态：采用。

上一阶段已把SDK场景光栅化移到GPU，但每帧读回CPU并用softbuffer呈现，还重复上传图像。本阶段保留CPU参考和headless GPU读回，原生GPU窗口改为读取GPU输出buffer的fragment pass并提交wgpu Surface。

每应用拥有一个GpuSession，窗口与监督线程通过Arc/Mutex共用设备。设备操作和错误作用域串行，避免跨线程作用域栈交错。compute仍进行2秒上限的完成等待，以约束在途帧和资源；本决策没有承诺完全异步调度或零CPU工作。

帧内容显式区分CPU像素与GPU资源。GPU帧保留会话和输出buffer，替换/关闭图像不会改变已渲染帧；只有显式读取或PNG导出才创建像素读回。输出序列化前刷新统计，并poll待投递的设备丢失回调，避免空闲期间设备丢失被当成成功。

图像存储与命令/字形分离。32MiB图像atlas使用纯Rust布局分配器：句柄保留、释放合并、first-fit、必要时紧凑；输入检查和失败提交保持原子性。碎片紧凑会重传当前缓存图像，并明确计数。缓存不跨应用共享。

Surface只使用已测试的RGBA/BGRA 8-bit UNORM/sRGB格式，优先非sRGB；sRGB目标显式执行相应颜色转换。物理缩放与参考后端同为最近邻。Timeout跳过，Lost/Outdated有限重配；待重绘状态直到成功才清除，不依赖新的应用帧序号。

wgpu27的部分poll/present/discard错误会触发Rust panic。窄作用域unwind边界将其记录为终态失败；取得的Surface帧由保护对象释放，清理在已有错误时仍执行，二次清理错误保留首错误。GPU构建要求panic=unwind。Validation/OutOfMemory/Internal与独立device-lost回调均接入共享状态。应用异常走已有监督进程回收路径。

统计区分图像、场景、呈现参数上传与像素读回，并报告真实Surface格式和成功呈现次数。native无snapshot必须零读回；显式snapshot允许一次读回。headless读回继续作为确定性oracle，不把它与native compute时间混算。

后续优先字体与文本；更复杂场景可能需要tile/binning、图形管线或异步调度，当前每像素遍历命令的64M预算依然存在。完整字体、视觉效果、系统合成器和Linux启动镜像仍是独立阶段。
