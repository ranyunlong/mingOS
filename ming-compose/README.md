# Ming Compose（实验）

Kotlin/Wasm 的真实 Compose 编程模型，使用官方 Compose Compiler2.3.20、Runtime1.10.0源码WASI适配及Ming SDK4。当前通过根Gradle includedBuild从源码构建。

~~~kotlin
import androidx.compose.runtime.*
import ming.compose.*
import ming.sdk.MingLog

fun main() {
    val result = MingApplication.run {
        var count by remember { mutableStateOf(0) }
        MingColumn(padding = 24, gap = 12) {
            MingText("计数：" + count)
            MingButton("增加") { count++ }
        }
    }
    MingLog.event(if (result.code == 0) 40 else 60,
        if (result.code == 0) result.frames else result.code)
}
~~~

应用只修改state；框架订阅Snapshot，应用循环负责同步pump并通过SDK4提交frame。MingColumn是整数垂直布局，padding/gap=0..64；MingText是单行BuiltinFont1，emPx=8..64，UTF-8<=512B；MingButton使用固定首版样式和半开命中矩形。合法宿主viewport原样保留，极小窗口通过裁剪显示。

会话最多32个已提交节点（含root），一次更新最多64个暂态节点；最多16个已提交文字layout，加16个原子替换暂存，总活动不超过32。SDK4的8shadow/16AA、32M work、4M像素、64M GPU候选等额度不变，首版Button最多8个。布局、回调和SDK资源是框架私有的；所有外部能力通过Ming SDK。

只有成功提交的frame才能替换资源和hit targets。资源分配、测量或提交失败返回负状态并清理暂存；dispose仍关闭其他资源，保留最早关闭错误。MingApplication收到close退出；4096次事件预算耗尽返回-4。frames统计成功提交，原生呈现可能合并快速到达的frame，不能把提交次数当作呈现次数。

当前为同步模型。LaunchedEffect/delay/动画、完整Compose UI/Material、键盘focus、IME、通用布局和长期内存资格未完成。官方port仍有single-thread、strong weakref fallback、固定Kotlin内部identity与explicit frame clock限制。详见../docs/compose-wasi-port.md及../docs/compose-verification.md。

构建、合同和真实应用：

~~~powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
node scripts/build-kotlin.mjs compose-demo
node --test samples/compose-demo/tests/app.test.mjs
node scripts/build-compose-contract.mjs renderer
node --test ming-compose/tests/renderer-contract/host.test.mjs
~~~

正式应用包位于artifacts/compose-demo。Node用于构建和SDK边界double；真实UI验收在Rust/Wasmtime宿主，入口scripts/compose-verify.ps1。Computer Use物理输入验证按用户停止状态暂挂；scripted输入的通过不冒称物理鼠标测试。
