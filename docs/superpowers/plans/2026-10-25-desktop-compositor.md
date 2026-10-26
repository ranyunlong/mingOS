# 第7B阶段：多窗口帧合成器

沿用已确认的最小桌面设计，2026-10-25继续。此目录不是Git仓库，不初始化Git、不改动物理鼠标暂停状态。

## 契约

可信host按从底到顶提供最多5层（4个client与1个shell）。每层引用不可变RenderedFrame、目标rect、屏幕clip与0..255 opacity；源图按nearest-neighbor映射到目标，clip使用半开区间。输出为不透明ARGB，沿用SDK整数source-over与四舍五入规则。屏幕及source各边1..4096、各<=4,194,304像素；rect与clip位置限制±1,000,000、尺寸0..4096（目标必须非零）。屏幕之外允许裁剪，0面积clip与0opacity合法。拒绝超过5层、帧尺寸/存储不一致和跨device。失败不推进输出sequence，不污染健康设备。

CPU合成器只接受CPU帧，独立标量实现作为像素参照。GPU合成器从现有session fork独立context，直接绑定同device各应用buffer；只上传布局参数，不读回、不上传CPU像素。一次compute输出仍为GpuFrame，可由现有presenter接受。每次等待提交完成（既有2秒上限），限制GPU在途资源；旧输出保留不可变buffer和session，应用关闭后仍可显式导出。Compositor sequence仅成功后递增，保留每层frame自身sequence，不按窗口下标推断身份。

## 执行与验收

- [x] 先写公共API CPU测试，确认缺失API导致RED；覆盖z-order/alpha/scale/clip/负坐标/5层/边界/失败原子性。
- [x] 最简CPU实现及公共输入验证，无GPU依赖。
- [x] 写真实GPU对照与故障边界测试，确认RED；实现WGSL和同device只读buffer绑定，复用native panic/error-scope边界。
- [x] 当前源码运行CPU与硬件测试：逐像素精确对照、帧保留、统计隔离、zero implicit readback、跨device拒绝后仍可继续、最大合法frame与5层预算。
- [x] default/minimal/gpu-only/allfeatures相关编译和Clippy、独立复核，保存source/hash/实际命令与结果。
- [ ] 继续连接窗口模型、实际workers、Shell/Dock；阶段输出不冒称完整桌面。

## 文件范围

新graphics/compositor.rs、compositor_gpu.rs、compositor.wgsl、tests/compositor.rs；graphics/mod.rs注册。如需复用有界GPU等待，仅将Gpu::wait可见性改为pub(super)。不修改SDK/guest协议或既有场景算法，不引入依赖。

窗口内容scene与明确呈现收据已接通宿主库，含15项scene CPU合同和实际GPU集成；原生event loop与真实workers属于下一7C。最终证据见artifacts/desktop-compositor/checkpoint.json（88项冻结输入）。
