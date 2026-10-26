# SDK6窗口文本/键盘/组合输入

7D实现，2026-10-25。普通应用只通过新ming_input_v1接口接收本实例输入，不能访问OS input method、clipboard、其他appID或宿主路径。旧SDK1..5及MFE1输入1..7保持不变。

## 包格式与通路

poll(ptr,capacity)->i32最大568B，0pending不解码；text_focus(ptr,len)->i32接收24B(v1,active,x,y,w,h)。包56B头：v1/kind u32，sequence/epoch/presented-source-frame u64，a/b/c i32，modifiers u32，textLength u32，reserved0，后接最多512B严格UTF8。Kind为commit/preedit/key/pointer/cancel；preedit选择使用UTF8 byte边界，SDK转UTF16 scalar边界。active caret必须在此实例最新source frame内，host只投影到当前focused/ready窗口，不能越权定位。

ViewBridge每实例独立有界typed FIFO，native pointer/key/text同队列有序，legacy通道只作wake与旧SDK兼容。typed wake使用同尺寸resize通知，不扩展旧MFE1事件类型。resize/native focus change取消gesture/preedit；已排队的本实例输入保持顺序，cancel marker以epoch隔离后续状态。队列满拒绝新事件并报告drop，可靠cancel最多一份pending，不能全局关桌面。Frame receipt仍来自实际presenter接受后，pointer在resize直到matching source前禁止。

## UI及语义

真实Compose新增MingTextField包装immutable TextEditState，支持中文/Latin commit、preedit不提交、选区/退格/Delete/方向/Home/End/Shift/Ctrl+A、surrogate-safe cursor、512B有界值与裁剪caret；不冒称完整UAX grapheme或clipboard功能。renderer只在frame submit成功后发布hit/caret目标，资源失败回滚旧目标。新文本入口的SDK6命名空间不得泄漏到旧sample的DCE输出。

Windows native Ime::Preedit cursor是UTF8 byte index，Commit为完整输入结果；组合阶段由winit处理原始键，避免自制拼音假冒系统IME。host在virtual-window切换时取消旧上下文，按实例/epoch隔离preedit/commit。Linux input-method协议和实际IME引擎另以当前backend证据验证，synthetic中文合同不等于物理拼音已测。物理鼠标/ComputerUse专项仍暂停；VM虚拟输入可独立验收。

参考固定winit0.30.13 API文档，相关片段与链接作为代码引用记录，不随latest迁移。
