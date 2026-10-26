# SDK4：圆角抗锯齿与UI基础（实验）

日期：2026-10-24。第6A项圆角覆盖率已验收，第6B项有界阴影已完成Windows真实应用与Linux九路径验收，后续动画和组件输入继续推进；不代表完整桌面。应用保持Kotlin/Wasm与Ming-only导入，SDK3字体与旧协议不变。

当前MingFrame4是底层绘制接口。用户已确认的应用组件方向是真正Compose Compiler/Runtime加MingOS组件；用户在完成JVM对照后明确继续Wasm，这部分正在做官方源码WASI平台适配，尚未声称当前SDK4已具备Compose支持。

## 使用

manifest.sdkVersion=4；宿主当前需要text feature。新增ming_v4.frame_submit(ptr,len)->i32，两个参数和结果均i32。SDK4可继续使用ming_v1/v2及ming_v3的字体/测量/关闭API。Kotlin使用独立MingFrame4，避免给Frame3或旧应用引入v4导入。

~~~kotlin
val result = MingText.create("界面设置", 18f)
val label = result.layout
if (result.code == 0 && label != null) {
    try {
        val frame = MingFrame4(1100, 760)
        frame.rect(0, 0, 1100, 760, 0xfff5f4ef.toInt())
        frame.rectAA(24, 24, 400, 160, 0xffffffff.toInt(), 24)
        frame.text(label, 48f, 70f, 0xff203b37.toInt())
        val status = frame.submit()
    } finally {
        label.close()
    }
}
~~~

rectAA(x,y,w,h,color,radius)使用整数frame像素，x/y范围±1000000，正尺寸<=1000000，radius=0..64；宿主将有效半径钳至min(radius,w/2,h/2)。MingFrame4还提供既有rect/image/clip/text/textQ6。rect保持旧op1硬边语义；clip替换半开矩形而非压入栈，圆角填充本身不意味着圆角子内容裁剪。

本地非法参数使builder持续返回-2；第17条AA使其持续返回-4，均不发RPC。宿主拒绝不修改builder内容，仍需由应用处理错误与资源生命周期。布局在提交返回前保持打开，跨帧复用后显式关闭。

## 协议与coverage

MGF2/layout1沿用24字节头和32字节命令。新op6为[6,x,y,w,h,ARGB,radius,0]，reserved必须为0。SDK<=3拒绝op6/v4导入；SDK4清单通过旧ming_v2/v3.frame_submit也不能提交op6，只有新v4入口走内部frame_submit_v4 RPC与父进程SDK4检查。

每像素在{1,3,5,7}/8px的位置取4×4固定采样，圆边界包含。中心/直边全覆盖，只有角区进行圆内测试。命中n个样本时coverage=(255*n+8)/16；effectiveAlpha=(coverage*colorAlpha+127)/255，再按既有整数RGB规则依次混色。所有除法向下取整，和文字/图片/矩形保持painter order。

每像素16个采样点产生包含0/255在内的17种coverage值，尚无隐式DPI、分数形状几何、路径/描边或Retina质量保证。CPU/GPU使用独立实现，测试以精确像素相等为准，没有放宽成近似截图。

## 资源与错误

- 每帧最多16条AA，包括透明、完全裁剪、radius0的命令；4r²之和<=262144。
- 基础面积、文字work、阴影裁剪包围盒及冷生成加上64*sum(r²)后<=32000000；该项等价每个四角像素保守计16次采样，最多4194304额外采样。
- 保留64KiB/256命令、单边4096/总4194304像素；GPU原始命令候选P*max(N,1)<=64000000。AA不增加持久GPU资源，不占字体/图片atlas。
- 普通非法/失效句柄返回-2，额度返回-4；GPU终态错误仍使会话失败，不能冒充额度拒绝。普通拒绝保留最后成功帧。

应用必须遵循宿主已归一化的resize尺寸；擅自把frame截成另一个固定画布会让输入尺寸校验不能完成。大画布应调整布局和命令数量，不能删除候选工作量上限。

## 有界阴影

新增 `ming_v4.shadow_style_create(radius,blur)->i32` 和 `ming_v4.shadow_style_close(handle)->i32`，参数与返回值均为i32。radius=0..64，blur=0..16，最多8个活动的不可变样式，句柄位于0x40000001..0x4fffffff、会话私有且不复用。关闭后不能再次提交，已完成的输出帧仍有效。Kotlin通过MingShadows.create、ShadowStyle.close管理；宿主是生命周期的最终判定者。

~~~kotlin
val result = MingShadows.create(24, 12)
val style = result.style
if (result.code == 0 && style != null) {
    try {
        val frame = MingFrame4(640, 480)
        frame.rect(0, 0, 640, 480, 0xffeef1ee.toInt())
        frame.shadow(style, 48, 56, 544, 360, 0x58304440)
        frame.rectAA(48, 48, 544, 360, 0xfffcfdfb.toInt(), 24)
        val status = frame.submit()
    } finally {
        style.close()
    }
}
~~~

op7=[7,styleHandle,x,y,w,h,ARGB,0]，仅SDK4专用提交入口接受，旧v2/v3入口仍拒绝。最多8条阴影，透明或完全裁剪也计条数；后者仍验证句柄，但不生成不可见cache。它绘制完整模糊形状，再按painter order混合，调用者应先画阴影再画面板，不提供CSS knockout或桌面背景模糊。

阴影以4x4 AA生成A8，经水平、垂直tent卷积，每pass独立整数舍入。九宫格最大canonical patch为193x193；位置、颜色和可伸展中段不进入key。CPU/GPU各有8个固定cache槽，GPU atlas2MiB与scratch512KiB，CPU A8 cache不超过1MiB。每帧先计算基础、文字、阴影面积和冷生成，总work<=32M；超额返回-4并保留最后成功帧和cache提交状态。

GPU执行mask/horizontal/vertical三个pass，每个miss仅上传独立32B参数，不上传CPU阴影像素。gpuStats中的shadowCacheHits/Misses/Evictions、shadowGenerationWork、shadowParameterUploadBytes、shadowEntries与shadowResidentBytes用于核对复用；这些计数不等于端到端UI帧率。

## 验证入口

样例为samples/ui-demo，使用真实Kotlin/Wasm，不需要文件grant。scripts/ui-verify.ps1执行CPU/GPU场景、叠放切换/恢复、窗口尺寸与原生零读回；输出目录必须是全新目录。当前Linux验收包含原七路径与shadow-demo两路径，共九路径。阴影样例为samples/shadow-demo，通过scripts/build-kotlin.mjs shadow-demo统一构建，scripts/shadow-verify.ps1验收。实际结果见ui-aa-verification.md和shadow-verification.md。
