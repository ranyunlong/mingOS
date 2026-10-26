# Real Compose TextField contract

固定 Kotlin 2.3.20，standalone Gradle include 真实 ming-sdk 与 ming-compose，保留已有 Compose compiler / vendored Runtime / coroutines port。contract 的 source-set 注入仅发生在此 standalone build，不修改 ming-compose/build.gradle.kts。编译输出位于本目录 build/，依赖缓存沿用项目 .cache/gradle。

PowerShell 从项目根目录执行：

~~~powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
node ming-compose/tests/text-contract/verify.mjs another-run
# 发生可恢复执行器问题后，核对同源码已构建产物并继续：
node ming-compose/tests/text-contract/verify.mjs another-run --resume
~~~

stage 目录不可覆盖。build.mjs <stage> <kind> 可单独构建 text、renderer-contract、contract、framed-driver、framed-runtime、framed-app、sample。contract.mjs <stage> 执行新 TextField 合同；regression.mjs <stage> <kind> 执行既有 Kotlin 合同。

host.mjs 从现有 shadow-demo CPU capability double 保留边界实现；新测试增加非等宽且有小数的 Q6 metrics、64 live layouts 检查、实例 FIFO 与 frame-clock/focus ABI。Node 不实现 Kotlin editor、Compose 或 renderer。

artifacts/text-field 保存编译 RED、source-binding 和 caret-hint 两个真实 runtime RED，最终 GREEN、原始/packaged Wasm、源码快照、命令、日志、trace 和源码/证据 SHA-256。仅 synthetic 输入，未验证物理 IME 或 GUI。
