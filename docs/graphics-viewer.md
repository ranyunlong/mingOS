# Kotlin 图片查看器原型

本阶段接通 Kotlin → Ming SDK v2 → Rust/Wasmtime → CPU 参考渲染 → 原生窗口。窗口不是网页；Node 只参与构建/适配和旧宿主对照，不参与图片应用执行。

## 构建

Windows 先按 [原生宿主说明](native-host.md) 准备工具链/JDK 21，在项目根目录：

~~~powershell
node scripts/generate-viewer-fixture.mjs
node scripts/build-kotlin.mjs image-viewer
.\scripts\rust.ps1 build --locked
~~~

图片为仓库脚本生成的原创确定性 PNG 夹具，也可通过 --grant 1=... 指定其他 PNG。当前示例标题固定，没有文件选择器或图片编辑功能。

## 原生窗口

~~~powershell
.\.cache\rust-target\debug\ming-app-host.exe view artifacts/image-viewer/app.json --grant 1=samples/image-viewer/data/coastal.png
~~~

滚轮缩放；按住左键拖动平移；FIT/F/R 适应窗口；100%/1 原始比例；Esc 或系统关闭按钮退出。窗口缩放产生尺寸事件，由 Kotlin 重新布局。GUI 使用 winit/softbuffer，CPU 协议与资源服务保持独立，便于后续 GPU 后端接入。

普通 run 默认 5 秒、1000 万 fuel；view 默认 60 秒、10 亿 fuel。可信 CLI 可显式 --timeout-ms 延长 view 至最多 300000ms，普通 run 仍最多 60000ms。--auto-close-ms 用于有限验收会话，不能由应用自行改变。预算到达会终止会话；需要有序自动退出时使用 --auto-close-ms。没有永久后台运行或无限期窗口生命周期承诺。

## 无窗口与输入脚本

~~~powershell
.\.cache\rust-target\debug\ming-app-host.exe run artifacts/image-viewer/app.json --grant 1=samples/image-viewer/data/coastal.png --snapshot artifacts/image-viewer/headless.png
~~~

--events <json> 可以提供最多 128 个 [type,a,b,c] 记录，例如 [[1,1000,700,0],[2,200,0,0],[4,500,400,1],[3,50,-25,0],[0,0,0,0]]。没有脚本时，首帧后返回关闭。

输入脚本和快照路径属于可信启动参数。普通应用只得到资源句柄，没有宿主路径。

## 本阶段边界

CPU 参考后端用于验证权限、协议、交互与可重复像素输出；没有声称达到 GPU、120Hz 或 macOS 级性能。位图字体和最近邻图像采样是参考实现。Linux/Wayland、GPU、完整字体/IME/无障碍、硬件和启动镜像尚待后续实测。

SDK 与预算见 [SDK v2](sdk-v2.md)；实际命令、测试与截图见 [图形阶段验证记录](graphics-verification.md)。
