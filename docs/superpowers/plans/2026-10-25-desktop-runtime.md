# 第7C：原生Ming桌面与真实应用会话

沿用已确认的最小桌面架构，2026-10-25继续。用户已授权连续推进；物理鼠标/ComputerUse专项保持停止。无Git仓库，不初始化Git。

## 实现顺序与边界

1. 可信catalog与desktop CLI：最多4个id/title/manifest，autostart显式；普通guest不能传路径或启动其他应用。保留现有run/view入口。限定开发会话，活动时间<=60s、总wall watchdog<=300s，不删除fuel、memory或request预算。
2. 会话controller：每个实例独立Options/Broker/worker/ViewBridge和forked GPU context，记录真实worker PID、generation、首次frame、关闭/失败结果。窗口操作先由DesktopModel决定，再将Resize/ResetInput/Close效果交给对应bridge；只保留每窗latest。关闭立即移除window，200ms宽限后可取消真实worker；退出必须回收所有workers，不仅隐藏窗口。崩溃不能结束兄弟应用，Dock可重新启动为新WindowId/generation。
3. 可信系统chrome：读取content-only根帧，加正确顶层遮挡的titlebar/窗口按钮、topbar、Dock与加载状态。CPU独立参照，GPU只读root buffer并上传布局/字形参数。输出sequence保持scene root身份，所有source/device/backend校验先于GPU副作用。
4. winit原生循环：一个MingDesktop窗口，Linux借用Wayland底座；CPU softbuffer或GPU Surface。合成+Shell输出真实呈现API接受后才ack对应scene root，resize期间gate关闭。标题拖动/边缘缩放、红黄绿、Dock启动/恢复、焦点键盘/指针捕获使用同一模型。
5. 有界offscreen与可信事件脚本复用controller，作为自动验收入口。offscreen输出只能记为buffer receipt，不能声称Surface/physical scanout。脚本时钟在所有当前应用匹配frame后推进，JIT启动不耗尽操作窗口；仍由总wall watchdog限制。验收覆盖并行真实Kotlin进程、focus/minimize/restore/resize/close/forced worker failure/relaunch及兄弟存活。

## 证据与完成条件

先写catalog/controller合同并观察RED；再实现。真实Kotlin至少两个不同包及两个同时存活的PID，每个有独立source sequence与matching presentation；CPU/GPU终态像素对照、原生GPU无snapshot零读回及显式snapshot、关闭后进程退出、崩溃后兄弟继续、重启新PID/generation。Linux CPU/Wayland与Windows原生分开记录；不把headless或截图替代实际多应用执行。全features/minimal相关编译、Clippy和既有回归；独立复核生命周期和GPU UI。源码/包/host/输入/日志冻结。

## 文件分工

主线程：desktop_catalog.rs、ming_desktop.rs、desktop_native.rs、main.rs/lib.rs注册、supervisor最小PID观测与scene统计；应用catalog/验证输入和实跑脚本。
独立worker：graphics/desktop_shell*.rs/.wgsl及scoped测试；主线程注册模块与统一Cargo/GPU验收。

7D长期会话预算/完整输入与中文IME仍在随后，之后继续平台隔离和可启动镜像。此阶段不改变guest SDK或让manifest id自动获得Shell权限。
