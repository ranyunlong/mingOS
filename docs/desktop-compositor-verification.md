# 第7B阶段：多窗口GPU合成与窗口scene验证

日期：2026-10-25。当前检查点：artifacts/desktop-compositor/checkpoint.json。范围为可信Rust宿主库中的合成核心和内容scene；尚未交付原生多应用桌面、Shell/Dock或启动镜像。

## 已接通的路径

Compositor::cpu提供独立标量像素参照；Compositor::gpu在现有device上fork独立context。输入层按底到顶排列，最多5层；nearest-neighbor缩放、半开clip与层opacity均由host指定。先将源alpha与opacity按整数四舍五入相乘，再source-over到不透明背景。根与每个source各边1..4096，总像素各<=4,194,304。

GPU直接绑定各应用不可变输出buffer，仅上传16+48*layerCount字节布局参数。输出仍是GPU-resident RenderedFrame，已有presenter可直接消费。合成不上传CPU像素、不隐式读回来源或输出，不改变应用cache/统计；提交使用已有2秒有界等待。非法输入在GPU错误边界前拒绝，成功才推进root sequence。

DesktopModel与DesktopScene已注册到正常宿主库。scene每窗保留一份latest，最多4窗，按模型z顺序合成内容；pending仅保留一次root的元数据。compose不会开放输入，只有宿主确认对应root已被presenter接受后，presented才按快照里的原始app sequence与尺寸更新ViewportGate。新latest不能替代旧root收据的身份，陈旧/重复/未来/变更后失效收据均拒绝。CPU/GPU后端或device不匹配的内容在替换latest前拒绝。

最小化保留最后画面及合法更新，隐藏期间不合成/ack；恢复无需新submit即可重画。关闭后sync回收内容。任何model_mut访问保守失效pending，invalid screen resize通过scene专用方法保持原子性。当前scene只合成内容，尚无title chrome或Shell。

## 当前源码的实际验证

| 检查 | 结果与范围 |
| --- | --- |
| Windows allfeatures，显式包含所有ignored硬件用例 | 309 passed / 0 failed / 0 ignored，24组摘要 |
| Windows minimal，无默认features | 84 passed / 0 failed / 0 ignored |
| Windows default / alltargets check | passed |
| Clippy allfeatures / alltargets | passed，-D warnings |
| Clippy gpu-only / alltargets | passed，-D warnings |
| Linux release minimal定向CPU测试 | 84 passed / 0 failed / 0 ignored |
| Linux release allfeatures / alltargets check | passed；编译不代表GPU执行 |
| Node默认测试 | 21 passed / 3平台条件skip / 0 failed；未宣称177项Linux验证夹具重跑 |
| scoped新文件rustfmt check | passed |

源码、tests/fixtures、字体资源及构建输入共88项已冻结，并在最终执行后重新计算SHA256全部一致。最终源码针对窗口模型首次正常注册后暴露的三项Clippy告警做了两处等价let-chain和一处tuple类型别名收口，随后重跑Windows完整测试与Linux定向验证。初轮日志保留为历史，不冒充最终hash结果。

## 关键行为与真实GPU证据

实测适配器NVIDIA GeForce RTX4070 Laptop GPU / DX12。合成器覆盖64组五context布局、4M像素×5层、source alpha×opacity的49组合及写死像素oracle、同应用A1/A2与B1交错、旧帧在应用/合成器释放后可读、第五层不可见foreign拒绝后完整统计不变及sequence连续。

实际GPU scene外部测试用两个独立Graphics context生成的内容帧（sequence7/11）及root sequence1验证身份隔离；它尚未启动真实Kotlin多worker。CPU/GPU在normal/minimize/restore三轮逐像素相等；不需要新的应用提交即可恢复。另有错误backend/foreign在替换前拒绝的RED/通过记录，确保不污染原内容和sequence。

新增presenter测试实际调用现有draw，将合成器fork的输出绘制到RGBA/BGRA离屏render target并检查像素；来源和合成器readbackBytes均保持0。library显式硬件测试同时覆盖原有fork、poison、device loss、error scope和阴影回归。测试专用snapshot明确读回，不计作直接合成路径。

## 独立复核

7A已冻结GPU上下文代码与证据经81项SHA256核对通过，范围内无阻断；见artifacts/gpu-render-context-review-20261025/。本轮compositor与scene均有独立只读复核，当前范围未发现剩余代码缺陷；见artifacts/desktop-compositor/review.md和artifacts/desktop-scene/review.md。最后的三项model Clippy等价收口由主线程核对和当前测试覆盖，没有冒称再次独立全模型审查。

## 继续与限制

下一步为第7C：原生桌面event loop、真实独立Kotlin workers、系统chrome、launcher/Dock和关闭/崩溃/重启生命周期。当前scene receipt测试是调用契约，不自行证明Surface或物理scanout；presenter离屏测试也不等于原生多窗口会话。Linux本轮仅CPU测试与编译，无真实Linux硬件GPU/显示器验收。物理鼠标专项继续保持用户此前暂停状态。长期会话、完整IME、隔离与启动镜像仍在后续路线中。
