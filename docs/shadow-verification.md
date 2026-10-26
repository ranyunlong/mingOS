# 第6B项：有界阴影验收

2026-10-24完成。正式路线保持Kotlin/Wasm/WASI + Wasmtime，SDK4新增不可变阴影样式与op7；整个操作系统仍在开发。本项不等于Compose组件、桌面合成器或可启动系统完成。

## 实现与边界

官方Kotlin应用通过MingShadows.create/ShadowStyle.close和MingFrame4.shadow调用唯一Ming能力入口。样式8个、单帧阴影8条、radius<=64、blur<=16，typed单调句柄会话私有。位置/颜色/可伸缩中段不进入key，最大canonical patch193x193。GPU三个有序pass执行AA mask和tent卷积，8个固定atlas槽2MiB、scratch512KiB，CPU A8 cache<=1MiB。

帧准入合计基础、文字、阴影包围盒和冷生成<=32M。当前帧需要的key全部pin；超额不提交cache metadata或替换最后成功帧。旧SDK和SDK4旧frame_submit入口拒绝op7。SDK细节见sdk-v4.md，数学资格见shadow-nine-slice.md。

## Windows实际验证

- 整仓all-features：212通过，默认忽略12项硬件测试；随后显式运行全部12项硬件测试，12/12通过。
- no-default：70通过；最终40项shadow相关测试再次通过。
- Clippy all-features/all-targets、gpu-only、text+gpu无desktop，均-D warnings通过。
- 真实Wasm API/帧/关闭/内存/额度测试3/3；统一Kotlin构建及SDK/ABI/生命周期合同10/10。
- GPU独立canonical379组、3332593像素；完整场景261帧、10850620像素；中文/图像/AA/阴影混排3帧、122880像素，均CPU/GPU精确相等。设备为RTX4070 Laptop、DX12、driver32.0.15.6636。

原始日志见../artifacts/shadow-service、../artifacts/shadow-gpu；作者独立harness与父线程整仓执行分别记录，不混为同一检查。

## 最终真实应用与原生窗口

最终强绑定结果是../artifacts/ui-shadow-reviewed/acceptance.json。8组真实Kotlin CPU/GPU PNG精确相同，包含初始、移动、变色、恢复、四次大尺寸连续resize、窄窗口、1x1和320x240。移动/变色改变像素，恢复完全回到初始PNG。常规点击及大尺寸调整始终只生成2个canonical key；暖帧不再上传阴影生成参数。shadowParameterUploadBytes=32*shadowCacheMisses，计的是参数而不是像素帧。

85份Rust/WGSL/Kotlin/构建/fixture来源在首case前冻结，并同时绑定宿主、原始Compiler Wasm、package、build-info、字体与harness。每次调用前后及报告前复查。独立复核发现metadata缺失会跳过raw绑定的P2，现已强制检查；13组真实生产AST负例/对照通过，父线程也独立重跑。此前ui-shadow-20261024和ui-shadow-final只保留当时画面结果，不替代修复后的最终强绑定结果。

native非截图：1次Surface呈现，readbackBytes=0，呈现参数32B。显式截图：1次呈现，readback3344000B，参数32B。两次窗口均自动关闭；这不是本阶段物理鼠标点击或最大化操作的声明。脚本输入中的点击与resize覆盖事件路径；既有真实最大化鼠标测试属于6A历史证据。

## Linux与跨平台

../artifacts/linux-shadow-20261024/summary.json记录9条路径通过：headless SDK1/2/3/4及shadow，Wayland SDK2/3/4及shadow。原有应用没有被shadow覆盖。Linux Rust154通过，验收夹具131/131且无skip，字体夹具42/42。all-features/all-targets编译与Windows二次验证通过；容器exit0、非OOM、已停止。

Linux窗口环境为Weston headless/Pixman，不代表Linux物理GPU或显示器验收。真实Kotlin包在Linux由原生Rust/Wasmtime执行，无Node/JDK运行依赖。

Windows CPU、Windows GPU、Linux headless、Linux Wayland初始PNG SHA256均为：

~~~text
6f82fed15eec866f54d14c82938a71c6c2cc13441f7971cbe30fe3fc0cfbd979
~~~

父线程独立比对及源码未变记录见../artifacts/shadow-service/cross-platform.json。源码、原始/最终Wasm与metadata快照见../artifacts/shadow-source-snapshot/manifest.json；后续阶段会正常演进当前源码，不能拿新树替代旧阶段快照重验。

## 复现与后续

使用scripts/build-kotlin.mjs shadow-demo构建，samples/shadow-demo/test.mjs执行合同；scripts/shadow-verify.ps1 -Native -OutputDir <全新目录>执行真实画面/窗口验收。Linux入口保持scripts/linux-container.py；本次run-frozen-linux.py另外核验了本任务旧image/cache归属并复用，未清理其他容器或卷。

下一项见superpowers/plans/2026-10-24-compose-ming-bridge.md：真实官方Runtime驱动Ming组件、自动Snapshot重组和实际输入。阴影不提供CSS knockout、任意路径或背景blur；当前局部渲染耗时不是端到端FPS或输入延迟。之后继续帧时钟/异步调度/动画、桌面、隔离与启动镜像。
