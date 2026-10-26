# SDK3字体阶段验证记录

日期：2026-10-24。状态：本阶段已验收，进入视觉效果/UI基础。这里只关闭SDK3字体阶段，整个操作系统仍在开发。

## 已实现的行为

固定内置Noto Sans SC Regular字节与OFL1.1，cosmic-text0.19.0只加载该字体。父进程拥有字体、不可变布局和coverage，Kotlin/Wasm只通过ming_v3创建/测量/关闭/提交。SDK3使用op5，保留SDK1/2/op3语义。

CPU/GPU共享灰度mask、Q6 baseline、半开ink边界与整数混色。GPU独立4MiB coverage atlas与1MiB驻留元数据上限，图像atlas仍32MiB；tile候选有界。布局+CPU cache元数据2MiB，每次帧栅格化/插入重新准入，保守计入哈希表spare buckets/control/alignment。关闭但仍被引用的布局与mask继续计费。

## 已执行

- Windows全功能Rust回归130通过、0失败；另6个硬件测试在RTX4070 Laptop/DX12上明确执行并通过，没有把ignored当通过。见artifacts/typography/windows-all-features-tests.txt、windows-gpu-library-tests.txt、windows-gpu-tests.txt。
- no-default-features测试24通过，验证text/GPU均可选择关闭。独立gpu与text,gpu的无desktop组合及全功能Clippy -D warnings均通过；fmt检查通过。Windows默认配置93通过。
- KotlinSDK3真实Wasm的15项ABI/生命周期测试通过。旧两应用重新编译的规范化哈希与原包一致，旧artifacts包未改写。
- 字体资产42项测试：Windows41通过、1符号链接能力跳过。stdlib-only验证font/OFL SHA256、OTTO/CFF、name/cmap与指定18scalar corpus均通过；不能据此承诺完整Unicode或emoji。
- Linux验收工具85项Windows回归：84通过、1AF_UNIX能力跳过；新增SDK3的headless/Wayland路径和字体指纹。真实Linux本阶段已通过，具体记录如下。
- 真实Kotlin样例首次/重复绘制的CPU/GPU PNG精确一致；首次217个coverage上传共36944字节，重复3帧仍为36944字节。最终验收切换700x500窗口并实际平移/裁剪；与同尺寸仅resize结果的像素哈希不同，CPU/GPU仍精确一致。

## 字体资格与内存

当前text_probe与profile-text.ps1结果保存在artifacts/text-engine/qualification.json。初始化7.330ms；指定12/16/24/48/96px corpus每次约5–8ms；510UTF8字节/170个不同汉字/96px压力样本95.873ms。进程private-byte每2ms采样，峰值相对基线增加20447232字节，OS峰值working set36564992字节，低于64MiB资格目标。mask驻留4192251字节/864项。

该记录包含字体、coverage和显式PNG预览工作。采样峰值与引擎资格目标不等于hard RSS保证；还没有Linux cgroups/seccomp资源隔离。图形renderMicros包括相应主机准备/执行，不能换算为桌面帧率或输入延迟。

## 最终真实应用证据

Windows完整证据在artifacts/typography-reviewed/acceptance.json：initial、repeat、resize、move四组各有真实Kotlin CPU/GPU执行、JSON日志与PNG；四组分别精确匹配。resize为700x500、2帧、4744000字节累计headless读回，move同尺寸、4帧、7544000字节；两组最终PNG不同，证明实际滚动/拖拽改变了画面。

原生RTX4070 Laptop/DX12窗口：无截图2次呈现、readbackBytes=0；显式截图2次呈现、只读回最终1100x760x4=3344000字节。每次呈现32字节尺寸/格式参数，共64字节，计入presentationUploadBytes；这是参数上传。217个字形coverage首次上传36944字节，重复绘制无额外coverage上传。两会话均Bgra8Unorm。

Linux完整证据在artifacts/linux-typography-20261024-reviewed/{summary.json,run/evidence.json}：原生x86_64 Linux默认Rust测试93通过、0失败；85项Linux验收工具fixture全部通过、42项字体fixture全部通过且无跳过；SDK1/2/3 headless和SDK2/3强制Wayland全部通过。两个Wayland应用各提交并呈现2帧，实际windowSystem=wayland、presentation=softbuffer。all-features/all-targets编译通过。

Linuxheadless、LinuxWayland、WindowsCPU/GPU及Windowsnative最终初始文本PNG均为1100x760，SHA256=c74752c7a97fb63de3bc2d0ba210cad6513cd1733a406896823b34d6229d3b9f。原生窗口PNG及LinuxWaylandPNG已目视检查。Linux使用Weston headless/Pixman；没有Linux物理GPU或显示设备验证。

Windows重检Linux证据通过：源码/manifest/Wasm/font资产指纹保持与冻结时相同，原始命令输出/退出码/快照/编译器及配置隔离记录一致。容器身份/归属核验后确认已停止，非OOM退出。Docker引擎保持运行。

## 复核与保留的失败记录

资源预算、HashMap backing容量、SDK3错误码和两项验收假通过缺口均已修复并复核；见artifacts/typography/review.md。SDK3配额返回-4，非法/失效句柄-2；底层分配错误带quota context时保留-4，终态renderer/GPU错误优先传播。预算拒绝保留最后成功帧。

artifacts/linux-typography-20261024-final保留主动停止的首轮（exit143），在源码修复前核验归属并停止；不当作通过。artifacts/typography-final保留把32字节参数上传误当像素上传而失败的Windows验收；正确的宿主零像素读回已存在于原始结果，修正脚本后在全新reviewed目录完整重跑。artifacts/typography下早期move未产生平移的记录只作为集成过程证据。

## 可复现入口

```powershell
.\scripts\rust.ps1 test --locked --all-features --no-fail-fast
.\scripts\rust.ps1 build --locked --all-features
.\scripts\text-verify.ps1 -Native -OutputDir artifacts/typography-local
.\scripts\rust.ps1 build --locked --example text_probe --no-default-features --features text
.\scripts\profile-text.ps1
& 'D:\python\python.exe' -B -S -m unittest discover -s tests -p 'test_*.py'
```

text-verify输出目录必须不存在，重跑需另选全新目录。GPU计时是单次样本，包含packing/等待/headless读回，不能当成桌面帧率。

整个MingOS仍没有自有多窗口合成器、完整输入法、系统服务沙箱和可启动镜像。当前阶段结束后按连续路线进入视觉效果/UI基础。
