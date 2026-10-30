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

## 恢复诊断续记（v21—v23）

v21已结束，30项中26项通过、4项失败；除Counter自然Outcome/readback之外，Text第二代640日志hash也与again不符，匹配候选agaain。原QMP仅五组按下/释放；Wayland第二个a释放处理相隔425.811ms，超过400ms重复阈值，这支持重复键时序线索，不能把hash匹配或该间隔当作私有编辑缓冲区与完整根因证明。原始guest连续offset重建全部9份日志，并核对诊断stderr完整性收据。Boot当前回归44/44通过。

v22新增Counter单独关闭步骤，但将instrumentedQualification设为false激活了普通诊断的短采集窗口；流程未到对照点，不能用于归因。失败bundle/原始日志/私有盘均保留。v23恢复原v21采集配置后完成八步：Counter单独关闭的Result、native成功退出、finally/dispose和10→11 readback通过；Text1单独关闭与Text2随后关闭仍各自APP_CANCELLED，后者frameClock.closes=0。因此不能仅凭Counter成功就把关闭改为排队，也不能用这次修改过的八步profile宣告原七步资格通过。

本轮未改生产Host/SDK、Wasm或任何worker限额。v23无QMP强制退出，三worker均reap/cgroup pids0，guest sync/unmount/kernelPowerdown；自然Outcome要求仍因Text失败而未通过。v19导出原快照SHA再次核对不变，原盘未挂载。诊断证据见[独立核查](../artifacts/kernel-recovery-20261026/independent-review-v23.json)。下一份诊断需要同一父侧Instant记录Close请求与SDK回复写出时间，并采集有界worker CPU/cgroup样本，避免跨时钟原点相减或靠延长限额掩盖问题。


## 第一轮本地诊断准备（2026-10-26，本机未重放 kernel）

为下一轮 v23 后续诊断补充 guest worker CPU/cgroup 收据，沿用现有每秒一次的诊断进程表，
记录累计 CPU tick、限流计数、当前配额和 guest monotonic 采样窗口。读取大小受限，校验
PID/starttime/cgroup 身份变化，读取失败显式保留；不会因采样失败把 worker 从残留检查中删除。
新模块已加入 bundle 打包与源码 SHA 清单；未改变 Host、SDK、Wasm 或任何资源/关闭期限。
字段与边界见 [Boot 诊断说明](../scripts/boot/README.md)，执行项见
[关闭边界诊断计划](superpowers/plans/2026-10-26-kernel-close-diagnostics.md)。

该轮工作区没有历史 `artifacts/` 冻结 Host/Kotlin/bundle/数据盘证据，也没有 Rust 工具链和
MingOS Linux/QEMU 工具镜像。沙箱外访问固定 Rust 1.99.1 官方发行站仍连接超时。
本轮仅生成新的本地测试证据，不表示历史证据已恢复。父侧共用 Instant 的 Close 请求/SDK 回复/Result
时间线与真实 kernel 复验尚未执行，v23 的 APP_CANCELLED 与输入重复问题仍未解决。


## 第二轮本机推进：正常 Close 在服务预算等待中被误取消

新回归确认 supervisor 在非 cooperative SDK 服务延迟中将 `bridge.closed()` 当作
`APP_CANCELLED`；正常 Close 也满足该条件，可能中断尚未写出的 SDK 回复。现在该分支只由
真正的 `cancel` 标志中断，原延迟、Strict2s/Off200ms、fuel 和 Result 接受保护均保持。
此缺陷已单独 RED/GREEN 验证，但不能据此将历史 v23 全场景改为通过。

本机固定 Rust 镜像已恢复。Linux 默认 features 库测试 196/196、帧时钟宿主 7/7 通过；
持续会话集成 10/11，固定 1800ms shutdown 的故障隔离用例失败在强制重编译的原 HEAD 对照中同样复现，
保留失败，不报整组通过。AppData 两项测试仅补齐夹具 Unix0700 前提，生产目录权限边界未改。

真实 Text 按当前 Kotlin 源码重新编译并绑定输入，非旧 v19 产物。CPU offscreen 快速输入与自然关闭通过；
三实例关闭/启动新代诊断 23/23 通过：兄弟 PID9/generation1 保持，Text PID10/generation1 关闭后
启动 PID14/generation2，两代文字 `mingos`/`again` 精确；三实例 error=null、Close1/Cancel0、全部回收。
证据见 [本机诊断计划与结果](superpowers/plans/2026-10-26-kernel-close-diagnostics.md) 和
[三实例审计](../artifacts/close-service-budget-20261026/native-relaunch/independent-audit.json)。

本轮使用 Linux CPU offscreen、合成 Host actions，不是严格 guest/QMP/Dock 输入、Counter 数据盘、
Wayland 按键重复或真实硬件验收。QEMU 工具镜像恢复与严格 kernel 重放仍需完成，原 v23 恢复资格仍为 RED。


第二轮补充：Counter 的新建私有 Linux 卷已完成两个独立进程的 0→1→2 写入/readback，
19/19 独立检查及最终4B文件只读核对通过，finally/dispose、Close1/Cancel0 和 worker 回收均确认。
这不替代原数据盘与 kernel 冷启动资格。QEMU 工具镜像在已校验内核包后仍遇到 QEMU/Weston 等下载失败；
构建器要求的冻结 `mingos-isolation-probe`、`vm-prepare.sh` 也不在仓库/Git历史中，详见
[缺失产物清单](../artifacts/close-service-budget-20261026/missing-frozen-probes.json)。原哈希校验条件保留，严格复验尚未执行。
