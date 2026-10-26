# ADR0006：父进程拥有字体和SDK3文本布局

日期：2026-10-24。状态：已接受并完成本阶段验收；SDK3仍为实验接口。

MingOS应用需要可读的中英文和明确测量语义。旧SDK2 op3使用5x7点阵、按Unicode scalar固定advance；替换其行为会破坏既有应用。应用也不应获得系统字体目录、动态库和任意原生绘图API。

决定新增SDK3与op5。应用用Kotlin创建不可变布局，父进程用固定cosmic-text0.19.0排版并管理类型化、单调不复用的会话句柄；worker仅通过精确i32 ABI传输受限UTF8/样式和读取48字节metrics。布局大小、字体来源、mask/cache、每帧工作与上传均有明确上限。

内置未经修改的NotoSansSC-Regular.otf，来自notofonts/noto-cjk Sans2.004固定提交，保留OFL1.1和版权。FontSystem只加载包内字节，不扫描宿主字体。字体资格、字节哈希和覆盖检查是启用前提，当前只承诺单行LTR中文/拉丁标签。

CPU/GPU共享灰度coverage。GPU使用独立4MiB atlas与有界16x16tile候选，在原有绘制顺序中混色；整数平移和颜色不进入coverage key。原生窗口继续消费GPU输出buffer；只有显式截图读回像素。图像atlas、旧文字与SDK1/2保持既有行为。

代价是字体增加约8MiB包体，排版/缓存增加父进程工作。元数据额度采用保守预留计费，可能早于真实分配耗尽而拒绝；这是受控资源模型的明确选择。当前没有段落、编辑、IME、完整Unicode/emoji或高DPI桌面质量保证。将来扩展必须版本化，并保留旧ABI与像素回归。

接口见sdk-v3.md，具体资源边界与验收见字体设计和阶段验证文档。工程级额度不替代后续Linux进程、系统调用和资源隔离。
