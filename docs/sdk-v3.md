# SDK3：内置字体与不可变文本布局

日期：2026-10-24。实验接口，manifest 的 sdkVersion 必须是 3；宿主构建必须启用 text。SDK1/2 的点阵 op3 保留原语义。SDK3仍使用既有Kotlin/Wasm worker与父进程RPC，不开放WASI、字体文件路径或原生系统接口。

## Kotlin使用

```kotlin
import ming.sdk.*

val result = MingText.create("MingOS 中文", 24f)
val layout = result.layout
if (result.code == 0 && layout != null) {
    try {
        val measured = layout.metrics()
        if (measured.code == 0 && measured.metrics != null) {
            val frame = MingFrame3(1100, 760)
            frame.rect(0, 0, 1100, 760, 0xff11171e.toInt())
            frame.text(layout, 24f, 60f, 0xffe6edf5.toInt())
            val status = frame.submit()
            // status < 0 时由应用决定恢复或退出；旧帧继续有效。
        }
    } finally {
        layout.close()
    }
}
```

布局创建一次后可跨帧复用；颜色与整数平移不改变coverage缓存key。关闭后metrics/submit/再次close失败，句柄不会复用。提交返回前保持布局打开；父进程在已接受帧期间持有相关引用。布局和字体资源仅属于本次应用会话。

当前BuiltinFont.BUILTIN_FONT1是固定Noto Sans SC Regular400；不接受家族字符串、粗体/斜体或任意字体数据。字号8..96px，1/4px步进。createQ6和textQ6接受精确Q26.6；Float便利接口先验证有限值及范围。坐标属于frame像素，x向右、y向下，原点是左pen与baseline；没有隐式DPI缩放。

## ABI

所有参数/结果均i32，内存为worker的Wasm线性内存。ming_v3的导入如下：

| 函数 | 参数 | 返回 |
| --- | --- | --- |
| text_layout_create | ptr, UTF8len, font1, emQ6, flags | 正句柄或负错误 |
| text_layout_metrics | handle, ptr, capacity | 精确48或负错误 |
| text_layout_close | handle | 0或负错误 |
| frame_submit | ptr, length | 0或负错误 |

metrics写入12个LE i32：advance、ascent、descent、gap、lineHeight、inkLeft、inkTop、inkRight、inkBottom、glyphCount、statusFlags、fontRevision。前9个为Q6。advance包含空格和字距，ink为相对baseline的半开墨迹边界；它覆盖引擎支持的Q6相位，不能用字符数或位图宽度代替advance。lineHeight=ascent+descent+gap。空串合法，advance/ink/glyphCount为0。

文本只支持单行水平LTR中文/拉丁标签；拒绝换行、tab、控制字符、bidi格式控制与RTL运行。512 UTF8字节和512shaped glyph上限独立计算。flags只允许bit0：为0时缺字返回-5，为1时绘制.notdef且statusFlags bit0置位。fontRevision当前为1；当前字体没有完整Unicode/emoji覆盖保证。

MGF2/layout1头部不变。op5的8字为[5, layoutHandle, xQ6, baselineYQ6, ARGB, 0, 0, 0]。和矩形、图像及旧文字按提交顺序混色。op4替换裁剪矩形，不隐式压入裁剪栈。每帧仍限制256命令、64KiB、4096x4096以内且总像素<=4194304。

SDK3宿主返回-2表示非法协议/参数/失效句柄，-4表示资源额度，-5缺字，-6不支持的文本。Kotlin builder的本地参数检查失败返回-2且不发起RPC；这些本地检查不替代宿主检查。设备丢失等GPU终态错误会使整个宿主会话失败，不冒充普通额度不足。

## 资源额度

| 资源 | 上限 |
| --- | --- |
| 活动布局 | 64 |
| 保留的原始UTF8 | 32KiB，关闭但仍被引用的布局继续计费 |
| 布局和CPU cache元数据 | 2MiB，保守计入预留哈希表容量 |
| CPU coverage | 4MiB / 4096条 |
| GPU coverage atlas | 独立4MiB，不占32MiB图像atlas |
| GPU持久驻留表元数据 | 1MiB |
| 单个mask | 256x256以内 |
| 每帧glyph实例 / 唯一布局输入 | 2048 / 4096字节 |
| 16x16tile引用 / 每tile候选 | 65536 / 64 |
| 帧文字staging / 新增padded上传 | 2MiB / 1MiB |

每次栅格化和mask插入都检查布局占用后剩余额度；已被帧引用的coverage不能被淘汰来逃避计费。超额拒绝不能替换最后成功帧。CPU/GPU复用相同灰度mask和整数混色规则：有效alpha=(coverage*colorAlpha+127)/255。

内置字体解析与排版引擎的私有分配另外通过64MiB资格目标检查；该目标不是Linux RSS/cgroups硬限制，也不是对恶意程序的完整OS沙箱。真实进程/系统调用隔离仍属于后续阶段。

## 运行样例

先构建samples/text-demo，再用scripts/text-verify.ps1执行真实Kotlin/Wasm的CPU/GPU像素对照；加-Native时同时检查Windows原生GPU Surface与截图读回。Linux验收由scripts/linux-container.py与linux-verify.py记录。各平台实际状态以当前阶段的验证文档与JSON证据为准。
