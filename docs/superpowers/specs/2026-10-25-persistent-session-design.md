# 持续桌面会话与受控预算

2026-10-25。7C的有界开发会话已完成真实Windows/Linux验收；可启动系统需要持续桌面，不能在300秒后终止正常闲置应用。保持旧run/view以及正duration开发会话契约，新增显式desktop --duration-ms 0持续模式。

## 受控补给

初始fuel维持现有300M上限；持续模式只在真实SDK cooperative wait（event_next/frame_clock_wait）返回边界补给，未调用SDK的失控计算仍因fuel耗尽退出。worker在私有RPC中报告当前remaining fuel，parent按monotonic token bucket给实际缺口授信，初始burst300M、持续100M fuel/s；Reply携带grant，worker检查溢出/上限并累计total granted用于Result。guest ABI和SDK1..5导入不变，ordinary run/view不获得授信。

请求限制改为持续会话滚动预算（32768/min），内存仍64MiB，所有原图像/布局/scene/handle配额保持。耗尽预算停止对应应用，不结束兄弟。每个普通RPC的实际host service elapsed另以1s burst/500ms每秒限制；阻塞SDK wait时间不计作host service工作。以等待/背压限制服务使用，不把该wall-time计时冒称精确CPU计量；Linux内核CPU/内存/pids隔离继续落实在8阶段。

## 看门狗与闲置

持续模式取消的是会话总时长，不取消执行看门狗：首次初始化仍有有界startup watchdog；消息到达刷新progress，在parent受控SDK wait/credit wait时标为parked，合法闲置可以持续；未parked且长期无进展则终止child。关闭、取消和实际worker reap保持现有路径。FrameClockService持续模式无总lifetime deadline，但每次请求格式/帧机会/关闭优先级不变。

## CLI及验收

正duration维持100..60000ms和既有300s wall上限；0为显式持续会话，使用可信host action shutdown或原生窗口关闭收尾。shutdown仅可信host控制面，不是guest权限。持续脚本afterMs仍有界（最多1h、128项），不得用无限脚本掩盖未清理进程。

验证旧有界fixture不变、token refill速率/突发/负数/overflow、fuel耗尽与授信累计、忙循环不能自补给、闲置跨短watchdog仍存活、RPC超额只杀对应进程、受控关闭全reap。真实Kotlin持续场景与后续VM boot另记；不把单元假时钟当真实长时间运行。
