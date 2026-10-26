# 第7C：原生Ming桌面与真实多应用验收

日期2026-10-25。已完成有界开发会话的原生窗口循环、可信catalog、独立Kotlin workers、系统titlebar/按钮/Dock、关闭/故障/重启和输入门控。检查点：../artifacts/desktop-runtime/checkpoint.json；不可变源码587项保存在source-snapshot，两个Kotlin包共同544项编译输入。后续7D源码变化不改写本阶段记录。

## 实际会话

| 路径 | 真应用/代际 | 实际呈现 | 结果 |
| --- | --- | --- | --- |
| Windows CPU offscreen | Compose+Motion，共4个不同PID | 73个buffer收据，Surface0 | passed |
| Windows GPU offscreen | 同上 | 74个buffer收据，Surface0 | passed |
| Windows GPU native snapshot | 同上 | 80次Surface API接受 | passed |
| Windows GPU native direct | 同上 | 78次Surface API接受 | passed，scene/Shell/Surface读回均0 |
| Linux CPU offscreen | 同上 | buffer收据，Surface0 | passed |
| Linux CPU Wayland native | 同上 | 67次softbuffer/Wayland API接受 | passed |

每轮15步可信host脚本覆盖点击、focus、最小化/恢复、最大化/恢复、geometry resize、强制终止Motion worker、重新启动Motion、正常关闭/重新启动Compose。每轮都有2个并行真实进程、4个不同PID和两个generation；成功实例核对真实package ID、原始source sequence及匹配viewport，Compose第一代确实消费2个pointer事件。错误退出没有完整Outcome的统计为null，lastSourceSequence单独记录，不用0冒充未知值。

Windows四轮16个PID经Get-Process检查全部不存在，Linux两轮8个PID在同容器/proc检查全部不存在。监督器先wait/reap后上报完成，关闭一个app不关闭兄弟，重新启动获得新WindowId/generation/PID。GPU共享device但每app、scene、Shell各自fork context，普通应用没有Wayland socket、GPU对象或启动权限。

五张1280x800终态PNG（Windows CPU/GPU offscreen、Windows GPU native、Linux CPU offscreen/Wayland）字节一致：15b388b34436e865d3247cd1f5d854dd299686cafa06f56730b8bf8a1bd591ec。当前预览../artifacts/desktop-runtime/desktop-preview.png，已视觉检查双窗口内容、前后遮挡、中文、titlebar与Dock。GPU snapshot显式读回4,096,000B，仅计Shell output；无snapshot会话三处读回均0。

## 自动检查与复核

Windows Rust全features显式include ignored：333 passed/0 failed/0 ignored；minimal：98 passed/0 failed；allfeatures/alltargets与gpu-only/alltargets Clippy -D warnings通过。Node默认21 pass/3平台skip；Linux验证器Python177中176 pass/1 Socket平台skip。Linux实际release allfeatures构建及两条真实应用路径通过；不是Linux硬件GPU验证。

独立runtime复核发现两项P2：未接受root前的chrome命中，以及单应用输入队列满升级全局失败。均有真实RED并修复，最终复核已关闭。相应回归在library-green.log；完整重跑在final-allfeatures-alltests.log。Shell CPU/GPU包含14项标量行为、hardware逐像素对照和实际presenter兼容；14px双倍字形保持3508B布局上传。完整证据在stage目录，Linux在../artifacts/linux-desktop-runtime-20261025/。

## 使用与边界

见../samples/desktop/README.md。Node仅用于构建/验收，运行时为Rust/Wasmtime与Kotlin/Wasm。此阶段默认60秒ready活动时间、300秒总watchdog，保留每worker300M fuel/64MiB memory/request及graphics配额；长期会话不能简单移除限制，正在7D实现。中文显示已验，完整键盘/IME、平台文件/授权服务、Linux隔离与可启动镜像继续推进。物理鼠标专项保持暂停。API呈现不等于物理scanout或性能指标，未声明FPS/输入延迟/生产沙箱完成。
