# SDK4圆角抗锯齿验证

日期：2026-10-24。状态：第6A项已验收。第6阶段的阴影、动画、组件输入及Compose运行时适配仍在继续，整个操作系统目标尚未完成。

## 已执行

WindowsRust全功能147通过、0失败、7项硬件测试默认ignored；随后显式执行3项GPU库测试与4项GPU图形测试，7项全部通过。no-default34项通过。三组Clippy（全功能、gpu无text/desktop、text+gpu无desktop）-Dwarnings通过。原始日志在artifacts/ui-aa-checks/。

AA覆盖率CPU核10项测试通过；先观察到RED，再实现GREEN。显式corner goldens、独立f64圆截面oracle和i128有理几何交叉检查，涵盖全部半径0..64、奇偶小尺寸、反射、半开边界和大坐标。原始standalone记录在artifacts/aa-kernel/。

3项真实Wasm host测试与4项图形测试通过：SDK<=3拒绝v4导入，SDK4旧submit入口拒绝op6，新入口接受；签名与guest内存检查；非法复制不发RPC；超16条AA返回-4且可恢复；测量/关闭/失效字体句柄保持SDK3语义；预算拒绝前不分配新帧，保留最后成功帧。

RTX4070 Laptop/DX12显式硬件差分覆盖radius0..64、透明颜色/替换式clip、百万级矩形、图片、点阵文字与中文混绘，CPU/GPU逐像素一致；直接GPU帧在显式read_pixels之前没有读回，AA颜色变化与文字整数移动不重传缓存。

Rust/WGSL实现已独立只读审查，未发现具体缺陷；该审查不替代实际测试；随后Kotlin响应式修正与验收工具也完成独立复核。

Linux验收工具新增SDK4后，Windowsfixture测试110项：109通过、1项AF_UNIX环境跳过。该Windows结果验证验收工具逻辑；真实Linux执行结果如下。

## 响应式与实际应用

初版ui-demo把合法1920x1080、320x240窗口尺寸改成自选画布尺寸，与宿主ViewportGate不一致，可能阻断原生点击。现已修复：所有合法宿主尺寸原样提交；空间和预算充足时保留26命令/5AA的双栏，其余改为12～14命令/4AA的单列。宿主输入保护未放宽。17个真实Kotlin/Wasm测试涵盖87组合法边界尺寸、候选预算切换、快速resize、点击和销毁；加旧SDK/adapter共32项Node测试由父线程复验通过。

最终Windows证据在artifacts/ui-aa-final/acceptance.json。九个场景initial/toggle/repeat/small/maximum/wide/minimum/square/rapid各有独立CPU与RTX4070/DX12执行，所有PNG逐字节一致；一次切换改变图片，再切回恢复初始图片。新文字首次显示可上传新coverage，warm恢复不重复上传。

窗口正常显示：1次呈现、readbackBytes=0；显式截图：1次呈现、只读回最终1100x760x4=3344000字节。两者均上传32字节尺寸/格式参数，未上传CPU像素帧；最终frame backend为wgpu-compute-surface。

## 原生最大化点击

使用Computer Use仅操作MingOS测试窗口。真实最大化后frame为1706x889，点击右上按钮后，目视标签从“琥珀色在上”变为“青绿色在上”。输入计数含2次resize、1次click，3次提交、4次呈现，exit0。额外呈现来自原生窗口重绘；不能把呈现数当应用提交数。

证据在artifacts/ui-aa-native-maximized/interaction.json及点击前后JPEG、outcome.json、final.png。按该实际尺寸和点击序列另跑CPU，PNG SHA256均为a61bac96122fe325f82631b6aacf74ec6b0a7296645054b676e61270bb23a08a，证明原生输入产生了预期场景。JPEG仅用于目视交互证据，不参与精确像素oracle。该窗口最终由120秒自动关闭；较晚的Escape动作遇到已关闭窗口，没有作为成功关闭证据。

## Linux冻结验收

证据在artifacts/linux-ui-aa-20261024-reviewed/{summary.json,run/evidence.json}。真实x86_64 Linux默认Rust测试110通过，验收工具fixture110通过、字体fixture42通过且无跳过；SDK1/2/3/4 headless和SDK2/3/4 Wayland七路径全部通过。SDK2/3 Wayland各2次呈现，SDK4为1次呈现；SDK4相同resize不重复绘制是预期行为。all-features/all-targets编译通过。

LinuxSDK4 headless、Wayland及WindowsCPU/GPU initial图片均为1100x760，SHA256=0ef44f7264188be7e640d9a0bb4272ba6c09d641e9a0c3092c327691031beb62。Linux使用Weston headless/Pixman，未验证Linux物理GPU/显示器。Windows重检源码/包/字体/编译器/Cargo配置与原始输出/图片记录通过；容器身份确认、已停止、exit0且非OOM。

## 证据绑定与限制

独立审查发现首版Windows工具仅在结束时读取appSHA，不能绑定全部实际执行版本。已在首次调用前冻结manifest/Wasm/build-info/font/host/harness指纹，每次调用前后及报告写入前重查，验证manifest.entry和packagedSha256；summary使用冻结值。实际生产guard的4个文件漂移/元数据出现负例均拒绝，见artifacts/ui-aa-checks/input-guard-test.json。旧artifacts/ui-aa-reviewed保留图形结果，但最终绑定结果以ui-aa-final为准。

Linux启动器曾在启动容器前因字符串生成错误报SyntaxError，修复并通过Python语法预检后再启动；没有将该失败记为Linux通过。旧字体与更早阶段记录继续保留其历史源码指纹，不能与新源码强行匹配。

当前4x4 AA、整数frame几何与有界候选仍是原型约束，renderMicros不含整个桌面合成/输入延迟。完整阴影、圆角子内容裁剪、全局背景模糊、动画调度、组件及高DPI仍须各自实现/验收。用户已选择真正Compose加MingOS组件，并在JVM实测对照后明确继续Wasm。官方Runtime的WASI源码适配继续进行，未因低层AA完成而宣称Compose已接入。
