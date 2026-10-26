# 真正Compose的MingOS示例

这是Kotlin/Wasm应用：remember持有计数/详情状态，MingApplication自动接收输入与Snapshot变化，MingColumn/Text/Button由真实ComposeNode构建。应用使用官方Compiler/Runtime源码port，经Ming SDK4由Rust/Wasmtime运行。

从项目根构建：

~~~powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
node scripts/build-kotlin.mjs compose-demo
node --test samples/compose-demo/tests/app.test.mjs
.\scripts\rust.ps1 -CargoArgs @('build','--locked','--release','--all-features')
.\scripts\compose-verify.ps1 -Native -OutputDir artifacts/ui-compose-new-run
~~~

包入口artifacts/compose-demo/app.json。event-contract.json记录初始1100x760及计数、详情切换、恢复和大尺寸事件序列。增加计数点击(40,120)，切换详情点击(40,175)；这是当前固定BuiltinFont布局的脚本坐标，实际窗口交互应按当前画面定位。

10组应用合同执行真实编译的Kotlin/Compose Wasm，JS只double SDK边界，验证正常退出、状态和条件节点、重复/未知事件、viewport与失败清理。真实CPU/GPU/native结果见../../docs/compose-verification.md。呈现身份presentedFrame只描述Surface/softbuffer API接受的source frame，不是物理scanout时间。

当前是组件/输入闭环示例，尚无帧时钟、异步effects或动画。物理鼠标专项因用户Escape停止Computer Use保持未执行；窗口可自动呈现和关闭，尚不能将自动呈现结果称为真实鼠标操作通过。
