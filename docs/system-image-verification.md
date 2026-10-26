# 可信系统镜像、Result阶段修复与实际冷启动

更新2026-10-26。整体MingOS目标仍active；本记录确认当前可启动开发系统阶段，不替代后续kernel窗口/launcher故障恢复、物理输入/GPU或完整日常系统资格。

## 结果

当前Linux系统Host SHA-256：`ca6f970aedd07483b24fec648fbaf36cb0c04601e88bfcdf066a62c906e918c4`；实际bundle SHA-256：`ec6bec3f5676712742e9d57492e26e68133ad5a36b48d8afe953797e051ea514`。两完整原Kotlin/Wasm包逐字节保留，数据Counter与Text未重新编译、未换成小探针。系统镜像在可信构建阶段预编译catalog全部选中Wasm，原优化和fuel/CPU/memory/pids/watchdog不变。

| 验证 | 实际结果 | 证据 |
| --- | --- | --- |
| 默认空表Windows全功能含硬件GPU | 408/0/0，原JIT | ../artifacts/system-image-precompile-20261026/windows-empty-registry-tests.log |
| 默认空表Linux CPU/边界 | 238/0/0 | ../artifacts/linux-current-20261025/build-v16-system-image-empty/result.json |
| 构建工具纯生成/文件保护/去重 | 5/0/0 | build-v16的builder-tests.stdout.log |
| 非空系统镜像Linux CPU/边界 | 238/0/0 | ../artifacts/linux-current-20261025/build-v17-system-image-enabled/result.json |
| 实际Native Linux Kotlin/Wayland | 3实例均system-image、error=null、Close1/Cancel0、全reap；Counter0→1→2、精确文字 | ../artifacts/system-image-precompile-20261026/native-wayland-enabled/result.json |
| v18真实kernel原全量脚本 | 独立23原检查+5系统镜像检查通过；Counter8→9；无残留、strict压力、数据卸载、PowerDown | ../artifacts/system-image-precompile-20261026/main-v18/independent-audit.json |
| v19同bundle同原盘两次fresh QEMU | 每boot28项及6项pair检查通过；Counter8→9→10 | ../artifacts/system-image-precompile-20261026/main-pair-v19/independent-pair-audit.json |

v19第一次两个应用ready为2297/2752ms，第二次2254/2924ms，均从MingDesktop会话开始计，不含内核启动，也不是帧率或物理输入延迟指标。每次独立kernel boot；宿主page cache没有清空。actual bootId为7ce3350b-aafa-4503-8902-21e6a600e77e、e6775ac3-8045-4cc7-a039-c69bb4b888b6，sessionId不同。

## 原始问题与修复

旧2s关闭宽限仍可能把正确应用报为APP_CANCELLED。v11父侧诊断保留了Counter全部finally/dispose/final logs、合法Result和无执行错误，之后仍触发deadline取消。现在ViewBridge以父侧专用共享Mutex序列化Result接受与期限取消；仅在原全部Result验证通过后置接受标记。它只抑制尚未发生的Close deadline取消，不清除已有/显式cancel，不生成成功Outcome。

Strict2s、Off200ms仍约束未接受Result的执行阶段；native退出仍必须成功、reap和cgroup cleanup，沿用原300000ms进度看门狗。原native非零退出、Result后额外消息、挂住三个真实监督器探针均报错并reap；其1s是探针配置，不是生产值。TDD先4pass/2 assertion fail，后六个期限/取消用例通过。旧RED与快照不改写。

启动阶段另外观察到Text在原300s内不能完成Module::new。v17阶段诊断定位Counter该调用201.380650s、实例化0.455704s，Text同调用未返回而被watchdog终止。首轮Strict持续会话采用有界CPU初始化队列，只有Parent已applied并降至0.25核才释放槽，不等首帧；Off/限时会话保持旧并发。此策略改善了Counter，但没有掩盖Text失败；随后以可信构建预编译消除重复模块编译。

## 可信静态模块边界

默认Host表为空。系统构建Native工具复用catalog/package loader，按SDK+完整raw bytes去重，同一读取Vec经Module::new、原imports校验、Module::serialize，写固定数字文件和生成表，不创建Store/不执行Wasm。主线程独立重建raw映射及固定Rust模板，核验每份原Wasm与所有SHA；enabled113个编译输入完整冻结。

生成的native bytes只作为Host可信只读静态程序数据。SystemModule字段私有，没有运行时from_bytes、Init/manifest/AppData/native上传入口；SDK与原Wasm逐字节相等才命中。唯一unsafe deserialize的前提是这些数据确为可信编译工具的未修改输出。该保证来自可信构建源与冻结流程，不把checksum或非密码学指纹叫做签名认证。

编译工具与cache-hit共用原Config，Speed、fuel、GC、func refs、exceptions、stack等保持；明确target为x86_64-unknown-linux-gnu，runtime亦检查完整目标资格。miss保留原JIT；hit加载/配置/版本错误失败而不悄悄fallback。加载仍在原child policy应用后，随后原imports校验、独立Store/Linker/Broker/grant、Ready/Result/native退出检查保持。代码缓存不保存实例状态/输入/权限。

实际kernel记录startup_budget observedCpus2/limit1；app2首次出现时app1已由Parent确认25%配额。raw process-table存在两个实际UID20001/20002 worker同时存活且NoNewPrivs1、seccomp2、CapEff0的样本；没有只靠两个历史记录或maxWorkers字段推断并发。

## 运行与交付

QEMU10.0.13/q35/TCG，guest2vCPU/2048MiB，外层容器4CPU/4GiB且UID65534/capdrop/断网/只读Root；worker启动1核、稳态0.25核、512MiB、pids64。原坐标pointer receipt握手、focus→mingos→POWER，精确SDK640/641，Counter readback和finally/dispose；每轮app回收后再跑三资源压力case，sync/unmount后kernelPowerDown。QMP为guest-shutdown，无quit/reset/powerdown强制命令。

原盘UUID1bb342cc-5448-4ae6-8bd6-108a15b2cafc，先只读取得基线8，保留失败写入，不格式化。两轮后10。导出包：[final-bundle-v19](../artifacts/boot-system-image-20261026/final-bundle-v19/README.md)，65文件约197MB，包含精确vmlinuz/initramfs、冻结Boot脚本、review、审计与128MiB Counter10完整快照。replay默认核SHA，-Run只复制私有盘，原盘与导出快照只读，语义失败正确返回非零并保留结束盘。

导出包单独复现已通过同一28项独立审计：私有基线10→11、原盘不挂载。原盘全SHA与Counter10在复现后只读核对相同，65导出文件哈希未改。证据在../artifacts/system-image-precompile-20261026/main-export-replay-v19/independent-audit.json及checkpoint-completed-boot-milestone.json。Windows与Linux默认表、非空Linux镜像、Native/Wayland、kernel/QMP及物理硬件范围分别记录，不互相代替。物理鼠标/Computer Use保持暂停。

下一步按既有设计继续真实kernel中Text关闭→Dock新PID/generation重启、兄弟存活与后续异常恢复；此处不结束整体goal。

## 最新恢复场景（v20）

七步真实QMP恢复流程成功关闭Text1/PID216并启动Text2/PID219/window3/generation2，Counter维持PID213/window1/generation1。但最后Counter返回APP_CANCELLED，30项门槛有3项因其缺自然Outcome失败；保持RED，不以状态流程成功代替整体恢复通过。私有结束盘Counter11已保存，原盘未挂载。正在以原预算/Result保护采集关闭边界，原v18/v19/导出复现的成功记录仍仅限各自场景。
