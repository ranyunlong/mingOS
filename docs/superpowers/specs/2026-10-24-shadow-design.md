# 第6B项：有界九宫格阴影

日期：2026-10-24。用户最终继续Kotlin/Wasm，真实Compose官方源码WASI适配独立推进。本项是可供未来Ming组件调用的底层图形能力，不是Compose替身或整个UI阶段的结束。

## 已验证的数学基础

probes/shadow-nine-slice的独立full convolution与canonical+mapping完成3104组/7432667像素精确比较，零差异，包含r0..64、blur0..16、奇偶/窄形/单轴及大矩形。负对照检出每pass舍入、core+1和margin缺失。详见shadow-nine-slice.md及对应JSON；这是CPU数学资格，不是GPU或性能验收。

有效r=min(styleRadius,w/2,h/2)，blur为b。coreW=min(w,2*(r+b)+1)，coreH同；含halo的patchW=coreW+2b，patchH同，最大193x193。像素先以SDK4 AA定义生成A8，再做水平/垂直tent卷积，权重b+1-|tap|，零边界，每pass执行(sum+norm/2)/norm，norm=(b+1)^2。

body局部坐标u含halo[-b,w+b)。若w>coreW，u<r+b保持；u>=w-(r+b)则减去w-coreW；中段映到r+b，再加halo索引。另一轴独立。缓存排除位置、颜色和可伸缩中段尺寸；大窗口resize通常复用同一patch，不生成整幅窗口阴影。

## SDK4与资源

新增ming_v4.shadow_style_create(radius:i32,blur:i32)->handle/negative、shadow_style_close(handle:i32)->status。radius0..64，blur0..16，最多8个活动不可变style；句柄单调不复用，类型区间0x40000001..0x4fffffff，会话私有。close后不能新提交，已完成输出帧仍有效。

Frame4新增shadow(style,x,y,w,h,ARGB)，op7=[7,styleHandle,x,y,w,h,ARGB,0]，位置/尺寸沿用op6边界，blur halo由宿主处理；最多8个shadow draw。SDK<=3和SDK4旧submit入口拒绝op7，只有v4专用提交可接受。frame提交仍按painter order，调用者先画阴影再画面板；这是完整模糊形状的source-over，不承诺CSS外阴影knockout或桌面背景模糊。

样式仓库放在Graphics，与图像资源同属父进程。create/close没有文件或原生GPU对象输入。CPU/GPU缓存各自提交元数据，避免CPU参考预热误当成GPU已有数据；cache key=(coreW,coreH,r,b)。cache固定8槽，无动态HashMap/碎片/compaction；当前帧需要的key钉住，仅淘汰未使用LRU。

GPU每槽65536个u32，8槽共2MiB；CPU A8缓存<=1MiB，常用最大patch8份共297992字节。GPU两块256x256 u32 scratch共512KiB，CPU冷生成使用有界A8中间数组。固定cache元数据和style记录<=64KiB。普通拒绝不能提交cache next state或替换最后成功帧。

## 准入

原64KiB/256命令、4194304像素、GPU64M候选、AA16条、文字和图像预算不变。先解析所有style/几何/clip并限制shadow draw<=8，关闭/跨会话/类型错句柄=-2。完全裁剪的draw仍计条数并验证style，但不需要生成不可见patch；透明可见draw仍计面积与cache生成。

W基础+W文字+W阴影裁剪包围盒+W冷生成<=32000000。每个unique cache miss的冷生成保守计patchPixels*(1+2*(2b+1))+64*r*r；相同key只生成一次。cache plan在当前GPU锁内计算，包含cold work，先于GPU操作；CPU也先plan并准入，再生成mask和分配输出。cold额度=-4且保留会话；设备错误仍终态。

## GPU路径

场景新增第5个storage binding（shadow A8-as-u32），仍低于既有默认设备storage buffer上限。新增shader三个entry：mask、horizontal、vertical；每个key使用独立32字节不可变uniform（patch/core/r/b/slot），避免同一buffer多次queue.write_buffer让所有dispatch看到最后参数。cold结果写入持久slot，场景只采样一个九宫格映射像素，再执行既有整数alpha/RGBblend。

使用顺序compute pass表达读写依赖，不把workgroup barrier当作全局同步。WebGPU把每个dispatch作为usage scope并自动处理资源转换；固定wgpu27实现另核对本地依赖源码。普通native无像素读回、无CPU shadow像素上传；headless/显式snapshot才读回最终帧。计数shadow生成、cache hit/eviction、resident与参数字节，不能把参数计成像素帧上传。

## 验收与下一步

CPU canonical mask与独立full oracle/goldens；固定槽LRU/pinning/atomic rejection；style生命周期/版本/guest memory；GPU全像素对照、冷/暖/移动/变色/大尺寸resize reuse、clip/文字/图像/AA顺序；cold work与缓存满恢复；真实Kotlin示例、native零读回与Linux执行。阶段6B结束后继续真实Compose和输入/动画。

参考：WebGPU specification https://gpuweb.github.io/gpuweb/ （usage scopes/资源转换），以及本项目固定wgpu27源码和已执行数学证据。本项现已实现并验收，实际证据、限制和历史快照见docs/shadow-verification.md。
