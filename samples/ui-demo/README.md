# UI demo / SDK4

真实 Kotlin 2.3.20 / Wasm 应用。初始画布为 1100×760；合法宿主 resize（宽 320..4096、高 240..4096、总像素不超过 4194304）原样作为提交帧尺寸。左右对比相同几何的硬边圆角与覆盖抗锯齿圆角；半透明琥珀色区域故意超出矩形裁剪窗口。右上角“切换叠放”可交换绘制顺序。界面没有输出 ABI 或内存信息。

## 构建与测试

在仓库根目录使用现有本机 Node / JDK21：

~~~powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
$env:GRADLE_USER_HOME = "$PWD\.cache\gradle"
$env:JAVA_TOOL_OPTIONS = '-Djdk.net.unixdomain.tmpdir=C:\Users\ranyunlong\projects\MingOS\.cache\java-tmp'
& "$env:JAVA_HOME\bin\java.exe" -classpath gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --no-daemon --offline -PuiDemoContract :ui-demo:stageSdkContract
node scripts/build-kotlin.mjs ui-demo
node --test samples/ui-demo/tests/sdk.test.mjs samples/ui-demo/tests/demo.test.mjs samples/ui-demo/tests/resize.test.mjs
~~~

契约探针在单独的 build/sdk-contract 目录编译，副本为 build/sdk-contract-artifact/sdk-contract.wasm；应用构建不会覆盖该副本。应用包位于 artifacts/ui-demo。build-kotlin.mjs all 包含 read-file、image-viewer、text-demo、ui-demo 四个应用。只打包 ui-demo 可避免重写旧 artifacts；若只需四应用编译兼容性，直接执行对应的四个 Gradle compileProductionExecutableKotlinWasmWasi 任务。

测试实例化真实编译后的 Wasm，并提供 Ming ABI mock host，检查命令字、边界、无 RPC 本地拒绝、句柄生命周期、各个创建/测量失败位置、提交失败、关闭错误、idle 上限、resize 与点击。resize 测试还覆盖最大化、320×240、2048×2048、连续快速 resize、87 个合法边界尺寸、64M 候选预算切换边界及非法 headless 极值。mock host 不绘制像素；真实 CPU/GPU 差分与原生窗口验收由父任务执行；本样例补跑现有二进制的 CPU smoke，见下文。

## SDK 接口（供 host 集成）

新文件 ming-sdk/src/wasmWasiMain/kotlin/ming/sdk/Frame4.kt；包名仍为 ming.sdk。

~~~kotlin
MingFrame4(width: Int, height: Int)
rect(x: Int, y: Int, w: Int, h: Int, color: Int, radius: Int = 0)
rectAA(x: Int, y: Int, w: Int, h: Int, color: Int, radius: Int = 0)
image(handle: Int, x: Int, y: Int, w: Int, h: Int)
clip(x: Int, y: Int, w: Int, h: Int)
text(layout: TextLayout, x: Float, baselineY: Float, color: Int)
textQ6(layout: TextLayout, xQ6: Int, baselineYQ6: Int, color: Int)
submit(): Int
~~~

仅新 builder 导入 ming_v4.frame_submit(ptr: i32, len: i32) -> i32；MGF2/layout1 六字头不变，op6 八字为 [6,x,y,w,h,ARGB,radius,0]。使用现有不可变 SDK3 TextLayout，字体创建/测量/关闭继续导入 ming_v3.text_layout_create / text_layout_metrics / text_layout_close。清单 sdkVersion=4，无 grants；真实 host 需启用既有 text feature，并允许 SDK4 使用这些继承字体导入。

x/y 在 ±1000000，w/h 在 1..1000000，radius 在 0..64；半径不由 client 提前钳制，交给 host 按矩形尺寸处理。clip 为替换式半开矩形，允许空裁剪。text 在有限像素范围内四舍五入到最近 1/64 像素，textQ6 保留精确 Q26.6。非法几何或超过 256 命令返回 -2；第 17 条 AA 返回 -4，透明、radius=0 和完全裁剪的 AA 仍计数。第一个本地错误持续保留，submit 不执行 RPC。host 拒绝会原样返回，合法 builder 可再次 submit；布局失效由 host 判定。总工作量、句柄类型、资源额度与像素语义仍由 host 权威校验。

## 父任务的运行输入与日志

只复用现有 event_next 模型，不实现通用 hover/release/focus。类型 0 退出，1=resize（a=width，b=height），4=click（a=x，b=y），8=idle。其他事件忽略。初始按钮半开命中范围为 x=[916,1076)、y=[30,70)；resize 后 x=[width-184,width-24)。

| MingLog code | value 与含义 |
| --- | --- |
| 40 | 成功提交总帧数，finally 最后记录；正常运行仅这一条日志 |
| 50 | 字体布局创建失败码 |
| 51 | 布局测量失败码 |
| 52 | frame_submit 失败码；随后退出并清理 |
| 53 | 每个布局关闭失败码；继续关闭其余布局 |
| 54 | -4，16384 次事件轮询预算耗尽 |

可直接供父任务 Windows / Linux CLI 使用的事件文件位于 tests/cases：

| case 文件 | 输入 | framesSubmitted / 正常日志 |
| --- | --- | --- |
| initial.json | [[0,0,0,0]] | 1 / [[40,1]] |
| toggle.json | [[8,0,0,0],[4,960,40,0],[0,0,0,0]] | 2 / [[40,2]] |
| repeat.json | [[4,960,40,0],[4,960,40,0],[0,0,0,0]] | 3 / [[40,3]] |
| small.json | [[1,480,520,0],[4,300,40,0],[0,0,0,0]] | 3 / [[40,3]] |
| maximum.json | [[1,1600,1200,0],[0,0,0,0]] | 2 / [[40,2]] |
| maximized.json | [[1,1920,1080,0],[4,1816,50,0],[0,0,0,0]] | 3 / [[40,3]] |
| minimum.json | [[1,320,240,0],[4,216,50,0],[0,0,0,0]] | 3 / [[40,3]] |
| pixel-cap.json | [[1,2048,2048,0],[4,1944,50,0],[0,0,0,0]] | 3 / [[40,3]] |
| fast-resize.json | 连续 resize 及三个对应尺寸下的点击，完整输入见 JSON | 10 / [[40,10]] |

初始状态为“琥珀色在上”；toggle 后为“青绿色在上”；repeat 后回到初始状态，末帧 CPU PNG 应与 initial 完全相同。small 检查裁剪下边与右边、按钮位置和中文标签。idle 不增加帧数；一直 idle 的 mock 用例在 16384 次轮询后退出，预期日志 [[54,-4],[40,1]]。

全部 11 个文字布局只创建/测量一次，任何正常退出、部分初始化失败、提交拒绝和事件预算退出均在 finally 逆序关闭。没有定时动画或 idle 重绘。

## Resize 与渲染候选预算

host 的 ViewportGate 仅在 presented frame 尺寸与 resize 后期望 viewport 完全相同时接受 pointer。示例不能为了减少工作量而把合法 viewport 改成自己的画布大小。布局降级只改变绘制命令，不改变帧尺寸、按钮命中坐标或现有事件模型。

- 画布至少 480×520 且 P×26≤64000000 时，保留原双栏对照：26 条命令、5 条 op6。初始与原有五个输入 case 均保持该布局。
- 其余合法 viewport 使用精简单列：12..14 条命令、4 条 op6，上下两行分别显示硬边与 AA 圆角，并保留半透明叠放、矩形裁剪、切换按钮与状态文字。宽度不足 480 时隐藏标题，高度不足 360 时隐藏页脚。
- 最大合法 P=4194304 时，精简布局最多 14 条，候选上界为 58720256<64M。1920×1080 可继续使用 26 条命令；2048×2048 使用 14 条。
- 非法 headless 尺寸先按宽 320..4096、高 240..4096 夹取，再在超总像素时降低高度至 floor(4194304/width)。例如 Int.MAX_VALUE 两边归一化到 4096×1024，负数两边归一化到 320×240。合法输入无需这些修正。

新增真实 CPU case 的最终几何：

| case | 最终 frame | 命令 / AA 数 | 按钮半开范围 | 成功帧数 |
| --- | --- | --- | --- | --- |
| maximized | 1920×1080 | 26 / 5 | x=[1736,1896), y=[30,70) | 3 |
| minimum | 320×240 | 12 / 4 | x=[136,296), y=[30,70) | 3 |
| pixel-cap | 2048×2048 | 14 / 4 | x=[1864,2024), y=[30,70) | 3 |
| fast-resize | 1920×1080 | 26 / 5 | x=[1736,1896), y=[30,70) | 10 |

fast-resize 的每帧尺寸/命令数依次为：1100×760/26 → 1920×1080/26 → 320×240/12 → 2048×2048/14 → 2048×2048/14（点击）→ 1100×760/26 → 320×240/12 → 320×240/12（点击）→ 1920×1080/26 → 1920×1080/26（点击）。布局切换与 resize 不重建文字布局；点击按当时的按钮位置交换 painter order。


使用现有 host 的 CPU smoke（不构建 Rust）：

~~~powershell
& .\.cache\rust-target\debug\ming-app-host.exe run artifacts/ui-demo/app.json --renderer cpu --events samples/ui-demo/tests/cases/toggle.json --snapshot samples/ui-demo/build/cpu-toggle.png --timeout-ms 30000
~~~

初始、toggle、repeat、small、maximum、maximized、minimum、pixel-cap、fast-resize 九个 case 均用上述现有 host 执行；CPU PNG 与 JSON 在 build/cpu-*.png / build/cpu-*.json。正常退出码为 0，backend=cpu-reference，framesSubmitted 和日志应符合上表。CPU smoke 不代表 GPU 差分、Wayland 或 Windows 原生窗口验收已完成。
