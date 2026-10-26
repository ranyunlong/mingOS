# Ming Desktop开发会话

先构建Kotlin desktop样例与启用gpu的Rust宿主，再运行：

~~~powershell
node scripts/build-desktop-samples.mjs
.\scripts\rust.ps1 -CargoArgs @('build','--locked','--release','--all-features')
.\.cache\rust-target\release\ming-app-host.exe desktop samples/desktop/catalog.json --renderer gpu
~~~

这是有界开发会话：默认在应用就绪后的60秒活动时间结束，总wall watchdog为300秒，保留每worker的fuel/memory/request限制。Linux原生模式采用单一全屏Wayland/X11 client；Windows使用开发窗口。不是可启动系统或长期日常会话。

窗口标题可拖动，右下角12px区域可调整尺寸，红/黄/绿按钮用于关闭/最小化/最大化或恢复；Dock启动或恢复应用。当前键盘仅桥接既有F/R/1快捷键，Esc退出整个开发会话；完整键盘/IME仍在7D。

自动验收输入是可信host事件文件，普通guest无权读取或发起这些管理操作。使用--backend offscreen时仅记录buffer收据，不计原生Surface呈现。物理鼠标专项仍暂停。

当前7C不可变基线与跨平台5张一致图片见../../artifacts/desktop-runtime/checkpoint.json；后续7D对持续模式的修改有独立记录。
