# P01 S3 源码、缓存与应用构建进度

更新：2026-11-08。S1严格导入、S2独立WSL环境已实际验收；本页记录S3逐任务结果，不能据此关闭S3或P01。完整合同见[设计](superpowers/specs/2026-11-08-p01-source-build-design.md)和[七任务计划](superpowers/plans/2026-11-08-p01-source-build.md)。

## 最新 Task3 检查点：源码与机械控制验收完成

Task2验收及文档已发布至远程`31edb062bf582e28053a5448c2129bc59cb2e195`。Task3新增封存/只读验证和显式endpoint可执行包装器，实现提交为`ff6425e8a2d6994f3ffa98695fc1a76d927ce763`。新独立审查Spec PASS、Quality Approved，无Critical/Important/Minor问题；主执行者核对完整五项要求、双平台证据和真实挂载矩阵后接受本任务。`guest.run`的实际恢复/编译阶段继续由Tasks4/5实现，公开编排由Task6实现，本任务没有成功占位入口。

- 最终冻结Linux `artifacts/p01-s3-task3-controls-20261108-a3365008/`：151运行、149通过、2项明确Windows专属跳过，零失败/错误，4.999秒、exit0。覆盖20项Task3、27项cache、16项Kotlin、9项transport、39项S2 host、40项S2 guest控制。
- 同冻结Windows `.superpowers/sdd/2026-11-08-p01-source-build/task-3-final-windows-52b47537/`：151运行、117通过、34项平台跳过，零失败/错误，2.950秒、exit0；实际Windows snapshot/junction执行，无同项双平台跳过。145项冻结pin和144项当前源pin重核一致。
- 独立固定canonical seal、不使用候选代码准备夹具；真实all-RO重开通过。另五个新代分别将payload、seal、verifier目录、payload内嵌目录和verifier单文件设为可写，均在字节未变时拒绝；每项1/1、零跳过。对应控制代为`cadd9ab5`、`597dfa48`、`eec50fc2`、`85ae7656`、`c7e5b13a`，完整证据均保留。
- 当前Kotlin/cache真实API实际接收绝对包装器路径；外部Docker夹具故意以23退出，验证的是可执行边界、原始二进制通道、退出码及失败收据，不是缓存恢复或编译验收。阶段准入测试有明确的外部S2观察夹具；实际S3仍必须重新完成原证明链及live admission。
- 初始缺失实现RED、夹具初始化失败、tmpfs默认noexec导致的包装器EACCES、同字节文件身份替换的真实RED均保留。独立mountinfo/执行对照确认noexec原因，只修正测试私有tmpfs的exec属性；身份缺口以跨扫描root/dir/file身份比较修复。原失败代不改判。

独立报告为`.superpowers/sdd/2026-11-08-p01-source-build/task-3-independent-review.md`，逐项重核patch与全部证据pin。审查中的三个CannotVerify项分别属于后续真实S2阶段准入、实际cache/Kotlin完成及完整S3编排；主执行者对照Tasks4–7确认这些依赖保持开放，不将机械控制当作真实集成。控制结果中的`fullTask3Accepted=false`保留生成时语义。接续Task4源码/缓存恢复；实际S3恢复/六次编译/封存导出、S4–S6、P01及全部11项整项交付仍未完成，正式资格标志保持false。

## Task 1：本地证明链准入与执行代码冻结

实现提交为`6403f8b`及审查修复`e02bfb4472247865321bf70b1e0a837afae0f763`。独立审查修复后Spec PASS、Quality Approved。公开模块`scripts/boot/source_build.py`目前只实现以下本地API：

- `qualify_source_build(spec, spec_sha256)`：以外部固定的原始spec摘要为入口，严格检查原S2 packet-v2、源码/独立verdict交叉引用、21个固定验收门、全部365项不可变证据、明确的外部引用和原3.54GB归档；返回本地准入记录。
- `freeze_harness(spec, qualification, output_directory=...)`：重新执行同一准入，不信任调用者修改后的dict；独占复制显式manifest闭包、核对摘要及目录集合、同步文件并保留失败输出。宿主mode与Linux目标0444/0555分别记录。

冻结输出在分配前保护所有已核验的证明文件、不可变清单成员集合、固定helper/harness闭包及checkout脏文件/删除记录。原清单的两个排除子树保持原语义；不会以禁止整个checkout目录的方式妨碍无关产物。Linux控制记录的tomllib路径仅作规范绝对POSIX语法检查，不在Windows访问该guest路径。

本任务不调用WSL/Docker、不分配运行环境、不编译应用。`run`/`reopen`编排和CLI仍由Task 6实现；`--help`不代表已有构建入口。冻结所需的完整最终S3执行闭包也要在后续模块完成后准备，不能把双文件控制夹具当作它。

## 已执行验证

| 验证 | 实际结果与证据位置 |
| --- | --- |
| 修复后Windows完整控制 | 29运行、28通过、1项Linux nofollow专属跳过，exit0；`.superpowers/sdd/2026-11-08-p01-source-build/task-1-fix1-host-green.log` |
| 主执行者冻结Linux控制 | 29/29、零跳过/失败/错误、exit0；`artifacts/p01-s3-task1-controls-20261108-9fe14f36/result.json` |
| 原真实证明链与归档演练 | 原packet-v2、365项清单及外部引用、3,544,954,880字节handoff经公开本地API校验，随后重新准入并冻结双文件夹具，exit0；`.superpowers/sdd/2026-11-08-p01-source-build/task-1-actual-local-proof-d803cd5b/result.json` |
| 独立修复复审 | Spec PASS、Quality Approved；`.superpowers/sdd/2026-11-08-p01-source-build/task-1-fix1-independent-review.md` |

Linux容器使用固定Tools镜像、只读根/代码、禁网、UID/GID65534、2CPU/4GiB/256pids、全部cap删除和NNP，停止后非OOM，容器及日志保留。冻结代码pin：`source_build.py`39120字节/SHA256`6f2bdbcdcfd5fcffc2ce804f83b6d236c91dc4d19b1cd25fe0b95ff991d8a4ae`；测试35107字节/SHA256`ff86220252e24c38b320d63f4bef1ef1f4eb4bac16f96235d10528802bc4fa95`。

真实原件演练仅证明本地准入和双文件夹具冻结；没有重新执行S2运行时或S3源码构建。原始24项控制、首轮审查结果及全部RED/GREEN证据保留，未覆盖或改判。Windows标准库没有可移植的目录fsync；POSIX执行目录fsync。首次收据创建前若文件系统失败，只能保留已分配目录，不能保证失败收据落盘。

## 后续任务与边界

Task 2设计已明确固定spec进入传输/完整harness接口、输入阶段双份容量预算、独立观察证据例外和失败代锁定，并通过独立接口复核；传输实现尚未验收。观察bootstrap/adapter独立冻结，不能依赖尚未传输的harness；S2 helper保持原字节。

adapter字节权限链已补充独立设计复核PASS：外部固定spec绑定原HOST harness manifest中的`wsl_environment_transfer.py`固定角色，safe-open本机模块并核对大小/摘要，冻结内嵌loader和实际argv，再在guest准入证据目录中核验安装字节后导入。它不依赖未完成的guest harness，也不增加独立pin文件或spec字段。

开发检查点`artifacts/p01-s3-task2-controls-20261108-042dfe82/result.json`保留冻结源码、精确容器及状态：真实Linux管道/进程控制10/10、零跳过/失败/错误，exit0，1.086秒。该容器仅为root-owned Unix socket控制夹具，使用capdropALL/NNP/只读根及代码/禁网/2CPU/4GiB/256pids；不作为实际S3受限worker验收。同一冻结副本在Windows执行主机二进制管道、超时和adapter角色拒绝控制3/3，exit0，0.329秒，记录为`.superpowers/sdd/2026-11-08-p01-source-build/task-2-host-core-b1640e17/`。当时公开入口和bootstrap尚未完成，已知监督与持久登记工作继续；这不是Task2完整GREEN、源码审查或实际传输验收。

后续真实Linux路径/监督负控`artifacts/p01-s3-task2-controls-20261108-868ce521/result.json`为4运行、3通过、1失败、零错误/跳过，exit1。唯一失败复现父目录符号链接绕过暂存文件末级`O_NOFOLLOW`，随后改为逐级dirfd/nofollow。该失败代、冻结代码、完整日志及停止容器保留，不能由前面的10项核心通过推导路径安全或Task2完成。

新增完整三套冻结控制`artifacts/p01-s3-task2-controls-20261108-28b448d6/result.json`运行110项（Task2 31项、原S2宿主39项、guest40项），107通过、1失败、1错误、1项Windows junction专属跳过，exit1，6.370秒。父目录链接修复、受监督持久登记、partial-write、管理顺序、4项真实closure/bavail/sibling以及2项公开入口模拟WSL控制通过。两项非通过均来自当时通用`EXPORT_ARCHIVE`原始字节pump尚未实现：策略被提前拒绝，因而无取消结果记录。这是通用传输机械路径的RED，不包含Task6 sender/seal/归档语义，原失败代继续保留。

修复后的新冻结控制`artifacts/p01-s3-task2-controls-20261108-8ca246d2/result.json`为114运行、113通过、1项Windows junction专属跳过、零失败/错误，exit0，8.071秒（Task2 35项、原宿主39项、guest40项）。通用raw-fd1、blocked sink/disconnect及新增身份/内存控制通过；主执行者重核全部冻结/当前源码摘要一致。root socket夹具的隔离范围同前。源码已冻结，完整Windows终态、17项报告及独立源码审查继续；Task2、真实WSL传输和实际S3均尚未验收。

Task2本地实现检查点`6d63345`的独立源码审查现为Spec FAIL、Quality ChangesRequested，尚未推送。八项重要问题涉及收尾路径替换、多层worker监督、统一取消期限、宿主失败后的guest继续资格、实际目标容量、宿主阻塞I/O、存活进程证据传递及缺少真实负控；另有一项记录不变量/UTF-8上限问题。此前114项控制通过不豁免这些问题。第一轮修复交回原实现子Agent，修复后仍须新冻结Linux控制和独立复审；Task2未验收。

首轮修复已有新的Linux负控与检查点：`602e0ae7`实际复现根目录替换导致外部目录被chmod；`c6d2e90b`为8运行/6通过/1失败/1错误，复现实际目标设备误查和存活进程事实丢失。`abfd0ff1`复现完整监督层级超时；新嵌套清理负控配对原冻结adapter的`a4b70c5a`仍超时7秒。当前修复字节的`cebce545`五项控制全部通过，覆盖完整监督层级、统一取消期限、执行中的根/祖先/末级替换、合并失败事实及实际目标设备观察。各代独立冻结、停止容器/日志/来源pin保留；这些是开发控制，尚无完整修复验收。

完成协议采用经原独立审查者澄清的保守边界：guest完成标记仅为暂定，后续操作必须重新核验原始宿主结果/实际收到的确认/guest闭包并重新准入，还需同一存活宿主进程已观察到前次所有受监督持久化成功。该内存状态只增加拒绝条件，不替代固定spec资格或产生新权限。进程退出/重启/fork、最后worker响应丢失及公开调用异常均拒绝继续；Task6须在一个进程串联两次输入操作。宿主阻塞I/O与此协议实现仍在继续。

首轮完整修复检查点`e6857294`仍为RED：Linux三套冻结控制共141项，136通过、1失败、2错误、2项明确Windows专属跳过，exit1；完整源码pin稳定、精确容器停止且非OOM。三处问题分别是既有spool冲突丢失`FileExistsError`兼容性、低容量夹具缺少必需duration而未真正到达容量检查，以及公开完成失败后仍留下成功`result.json`。同字节Windows传输62项为24通过、1失败、37项Linux专属跳过，exit1；原环境宿主/guest套件均exit0。Windows失败与Linux第三项相同。全部失败日志、原始结果和停止资源保留，原实现子Agent已解除该字节HOLD并继续修复；下一次验收必须使用新冻结代。

收据修复需区分不可变prepared记录与最终结果，并覆盖最终结果/索引持久化之后的响应丢失：公开异常仍须保留失败或歧义证据、撤销live witness并拒绝后续sibling，不得依赖覆盖已有成功收据、删除旧记录或将异常重定义为成功。该要求延续上述R4契约，尚无修复通过或独立复审结论。

后续新冻结`ea80d653`的完整Linux控制已通过：146运行、144通过、零失败/错误、2项明确Windows专属跳过，31.385秒、exit0；137项冻结/当前源码pin逐项重核一致，精确容器停止且非OOM。原spool、低容量、真实fork及新增晚期失败控制均执行。同一冻结副本完整Windows控制也通过：146运行、104通过、42项平台跳过、零失败/错误，171.470秒、exit0；native Job/junction实际执行，无双平台同项跳过，136项当前源码pin再核一致，完整机器结果和日志位于`.superpowers/sdd/2026-11-08-p01-source-build/task-2-final-windows-dc0262a3/`。

双平台终态核对后仅提交两份Task2源码形成不可变本地修复检查点`e4256dfc43c6d9138f4bca97c9f9be6f1eedefe1`，尚未推送。独立审查包记录原始CRLF运行字节与Git LF blob的不同身份，只允许普通checkout换行转换。该结果仅为控制测试，不关闭Task2或实际S3。

首轮修复独立复审已返回Spec FAIL/Quality ChangesRequested：原R1/R2/R3/R5/R6/R7/R8/R9已解决；原R4失败窗口修正成立，但完整正常native sibling要求仍未通过。新增F1：owner从不退休已结束worker登记，64条生命周期上限会在正常单文件receive第二次容量检查中耗尽（源码路径需69条）。新增F2：native canonical JSON对members排序，request却将manifest.json置于最后，实际guest序号检查与宿主发送顺序不一致，正常harness失败。此前成功控制替换了这些组合边界，双平台GREEN不覆盖该缺口。

原实现子Agent进入第2轮修复，完整未关闭集合为R4/F1/F2。要求真实owner连续超过64次完成及receive形状路径负控、实际native JSON roundtrip到实际guest ledger和正常handoff→harness机械路径控制，再修复、重新冻结双平台完整验证及原审查者复审。保留有界active ownership、全部潜在存活者/历史登记证据、统一取消期限、canonical authority和严格ordinal拒绝；不以放大累计上限或削弱断言替代。原候选冻结与全部资源保留，Task3尚未派发。

第2轮真实基线负控`2e291768`已使用原`ea80d653`适配器字节及新测试复现两项回归：两个owner子场景（80次连续登记及receive形状序列）均因登记上限失败；两种metadata排序经实际native JSON及实际guest record均抛出成员序号错误。共2个测试方法、2个失败子场景和2个错误子场景、零跳过，1.062秒、exit1；源码来源pin稳定，停止容器非OOM并保留。原runner将子场景数从方法数相减，旧结果中的passed=-2是统计缺陷；原件不改写，后续runner改为按实际成功方法回调计数。原实现子Agent已继续修复，尚无第2轮GREEN或复审结论。

第2轮候选先在新代`a92eb8bf`通过10项定向控制、零跳过；覆盖80次和receive形状真实worker、安全退休负控、native JSON到实际guest record及正常同进程handoff→harness。随后完整新代`c548acfc`153项/149成功方法/2失败子场景/1错误/2明确跳过、exit1：机械夹具加载时改写共享`_read_ready`及observer/witness常量，污染后续原S2测试。因果对照证实后，仅补齐夹具退出恢复和精确身份断言，生产适配器及S2源码不变；失败代不改判。

复审前检查点：最终同冻结`d8b8635a`完整Linux153运行/151通过/2明确Windows专属跳过，39.580秒；原生Windows完整153运行/105通过/48平台跳过，146.216秒，均零失败/错误、exit0。三模块为74传输+39宿主+40guest，native Job/junction实际执行，无同项双平台跳过。主执行者独立重核137项Linux冻结/当前pin及136项Windows后当前pin，全部一致；Windows证据为`.superpowers/sdd/2026-11-08-p01-source-build/task-2-final-windows-305236f7/`。仅两份Task2源码形成本地不可变检查点`5e1fdbe13921a24ce1f6cd6b803105ba085df903`，尚未推送。原审查者正在对R4/F1/F2及修复新增问题复审，未接受Task2。实际WSL准入边界仍由机械夹具替代，不据此宣称实际S3成功。

Tasks 3–7仍须完成seal及真实Docker wrapper、原源码/缓存恢复、两profile六次真实Kotlin编译、工具字节核验、独立只读回放、封存导出、完整编排和实际S3验收。随后还有S4冷构建、S5装包、S6端到端安装及保存重开。11项目总交付仍0/11。

`formalQualification=false`、`finalSystemAccepted=false`、`coldBuildExecuted=false`、`guestStarted=false`。完整项目状态见[未交付总表](undelivered-projects.md)，变更见[changelog](changelog.md)。

## Task2 源码与机械控制验收接受

原审查者对固定`e4256df..5e1fdbe`复审为Spec PASS、Quality Approved，R4/F1/F2均ADDRESSED且无新增问题。主执行者完整阅读复审报告，核对同冻结双平台终态、独立pin复核及报告18项要求映射，接受Task2；此前首轮八项已解决结论继续成立。详细独立报告保留在`.superpowers/sdd/2026-11-08-p01-source-build/task-2-fix2-independent-review.md`。

本次接受仅关闭七任务计划中的Task2实现/控制门。正常native串联夹具保留真实序列化、宿主监督及持久化、guest接收/ledger/finalizer，但替代外部WSL/namespace/admission与complete ack；真实owner序列/退休安全由独立真实进程夹具验证。未在本阶段执行真实S3完整准入/恢复/编译，不将这些边界推导为实际S3通过。Task3 seal/endpoint wrapper接续；Task6须在同一存活宿主进程串联两次INPUT。全部11项目仍0/11。
