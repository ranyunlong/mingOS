# 可信系统镜像的 Wasm 预编译模块

日期2026-10-26。状态：设计待静态复核；尚未修改生产实现。目标是保持Kotlin/Wasm与Ming SDK唯一能力边界，消除每次冷启动的重复Module::new开销，不更改fuel/CPU/memory/pids/watchdog或优化级别。

## 直接依据

v17显示Counter Module::new耗时201.380650s，实例化0.455704s；Text在同一调用内超过原300s期限。Counter已自然关闭并完成SDK finally/dispose和strict回收，整体双应用仍失败。原生可行性探针以原两份完整Kotlin Wasm、portable x86_64 GNU Linux目标做Module::serialize与原样deserialize：源1470216/1497701B，产物8577296/8797872B，imports和_start一致。该探针为debug profile，原生编译约54.10/56.11s、恢复约99.6/97.0ms；仅可行性测量，不是release性能、应用执行或kernel通过。

固定Wasmtime50原文：本地crate runtime/module.rs约374–422明确deserialize unsafe，只能载入可信serialize/precompile_module的未修改输出；config.rs约382–410允许显式target以禁用host自动ISA推断；原优化默认Speed。

## 信任方案

预编译只在可信系统镜像构建阶段进行，面向catalog内任意选定Ming包，不按验收应用名称特判。保留原Wasm包。构建工具只建立Module并检查原imports allowlist，再序列化；不创建Store、不执行_start、不调用应用SDK服务，不捕获运行时状态/句柄/权限。

产物作为只读静态数据嵌入该系统镜像的Host二进制。runtime registry只有SDK版本、原始Wasm bytes和trusted serialized bytes；命中要求SDK相同且输入Wasm逐字节相等。没有运行时from_bytes构造器、没有从应用manifest/Init/AppData/资源句柄加载cwasm的接口。应用只能提供原Wasm，不能提交native字节。默认开发Host registry为空，未选入镜像的模块继续走原JIT与校验。

SystemModule字段私有；lookup只返回不可变静态opaque引用。唯一unsafe deserialize接收生成表内可信编译输出，代码内写明安全不变量。生成表是Native Host的可信构建输入，和所有raw/compiled数据被同一compiler-inputs SHA清单绑定，构建前后核验，最终Host又由payload/bundle哈希固定。此处不虚构签名系统；信任与现有可信系统镜像/launcher一致。

## 配置与执行

precompiler和命中时的Engine复用共同配置：consume_fuel、GC、function refs、exceptions、memory64/multi-memory禁用、512KiB stack等保持，Speed不降低。预编译及cache-hit使用显式x86_64-unknown-linux-gnu portable目标；不命中时保持原host JIT配置。Linux/x86_64之外默认不使用这些Linux产物。

加载失败返回明确错误，不静默退回JIT掩盖坏镜像；加载后继续原validate_imports、Store limits、每实例Store/Linker/Broker/UID/cgroup、Ready、Result验证与回收。缓存只含Module，不能共享Store、计数器、输入、AppData或授权。相同code用于不同实例仍由各自Parent配置授权。

## 最小文件/API边界

- 新private system_modules模块与默认空system_module_table：private数据字段、exact-source+SDK lookup、opaque可信load；无公开native字节入口。
- engine.rs：抽共同Config、lookup选择JIT/可信静态Module；添加Native开发工具用precompile_system_module(bytes,sdk)输出可信serialized Module，普通Wasm linker没有该函数。
- lib.rs：engine feature下声明private模块。
- examples/precompile_system.rs：读取既有trusted catalog/package，向新output目录写固定数字文件名的数据和generated table，拒绝覆盖；使用现有依赖，不运行Wasm，不复制Cargo cache。
- 必要的执行来源metadata用于证明system-image命中；不在应用界面暴露实现细节。不更改Init/WorkerMessage/SDK ABI。

## 验证

先测默认空表、source变一字节/SDK不匹配不命中；可信tiny module JIT/恢复的返回值、独立Store状态与fuel；正规序列化但配置不匹配时安全失败；预编译拒绝unknown/WASI imports和错误SDK签名。单元构造的serialized数据也必须来自真实Module::serialize，不能把任意坏blob传进unsafe。

构建工具为整个可信catalog生成条目，生成源码只引用固定数字相对文件名与受限SDK整数，避免路径/应用名代码注入；记录original inputs、compiler/config、raw/native大小与SHA（由外层构建清单计算）。先构建普通空registry工具，再原生release预编译，再将生成表/data复制进独立frozen source snapshot编译cache-enabled Host；不覆盖旧冻结。

完整Windows/Linux回归与原两个Kotlin native/parity之后，必须重做原mingos文本脚本、实际两个worker并行、严格限额、counter/readback/cleanup及kernelPowerDown，再同一bundle同盘两个fresh QEMU。POWER-only诊断不代替这道门槛。通过后继续原kernel窗口/Dock恢复和整体验收。

## 明确边界

这是可信系统镜像构建缓存，不是一般应用上传native artifact的服务，也不实现动态包安装/更新缓存信任。未来动态缓存需单独设计。不得增加任意不可信serialized输入通往unsafe的路径，不得去掉imports/fuel/OS隔离来换启动速度。若效果不符合实际数据，保留RED，不用小应用或更长watchdog替代。
