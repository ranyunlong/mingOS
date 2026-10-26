# 连续开发路线与阶段验收

更新：2026-10-25。用户授权每个阶段完成后直接进入下一阶段，直到整体任务结束。阶段验收通过只表示该阶段结束，不表示整个操作系统已经完成。

## 当前工程基线

Linux底座；普通应用使用Kotlin/Wasm，通过Ming SDK获取外部能力；父进程持有授权和系统资源，独立worker执行Wasm。保留真实Kotlin样例和旧SDK回归。当前先验证x86_64；Windows用于开发与硬件GPU验证，Linux容器/Wayland用于原生执行验证，两者都不替代可启动系统或真实Linux硬件验收。

## 顺序

1. 应用运行与受控能力：已完成原型及Rust迁移。
2. 图形原型：已完成图片查看器、CPU参考和Linux/Wayland原生运行。
3. GPU场景计算：已完成硬件计算与像素对照。
4. GPU直接呈现与缓存：已验收；原生非截图会话readbackBytes=0，重复图像不重复上传，故障可诊断、资源可回收。证据见gpu-surface-verification.md。
5. 字体与文本：已验收；固定OFL字体、中文和拉丁文字、Q6测量/布局、SDK3、CPU/GPU coverage、真实Kotlin样例及Windows原生/Linux五路径通过。见typography-verification.md。
6. 视觉效果和UI基础：当前实现中；第6A项SDK4圆角抗锯齿已验收，包含真实Kotlin九CPU/GPU场景、原生最大化点击与零像素读回、Linux七路径。第6B项有界阴影已验收，含8组真实Kotlin CPU/GPU精确对照、原生零像素读回与Linux九路径，见shadow-verification.md。后续继续动画、组件与输入状态；组件路线已按用户确认选择真正Compose Compiler/Runtime配MingOS组件，固定官方Runtime1.10.0的wasmWasi发布物缺失已实测，JVM真实对照已执行，用户最终明确放弃JVM、继续Wasm，官方源码WASI适配已跑通无窗口Compose状态/重组/销毁并获父线程独立重放；第6C项Ming组件实现及自动验收已通过（真正Compose、8CPU/GPU场景、原生API零读回、Linux11路径）；物理鼠标专项因用户Escape停止而暂挂，未冒称全交互通过；第6D真实HostFrame/Compose异步动画实现与自动验证已通过（SDK5、8CPU/GPU场景、native25帧/零读回、Linux13路径177fixtures），见animated-verification.md；framed Kotlin最终独立审阅现已补齐，发现的异常链P2经最小修复、真实WASI合同7通过及独立复核关闭；当前动画包另完成complete/earlyClose两后端回归，历史native/Linux记录保持历史范围；第7阶段GPU多应用context与合成核心已推进，物理专项仍暂停；JVM记录仅作历史证据；按测量结果调整渲染架构。
7. 最小桌面：7A同device独立GPU contexts已完成实现及独立复核；7B合成核心与内容scene已接入正常宿主库，当前Windows全features含硬件309通过无skip、Linux定向CPU84通过及全features编译通过，见desktop-compositor-verification.md。7C原生event loop、真实Kotlin多worker、系统chrome/Dock及关闭/故障/重启已验：Windows333 Rust/98 minimal、Linux release真实offscreen/Wayland两路径；24个实际worker跨六轮回收，五张终态图一致，见desktop-runtime-verification.md。7D正在实现受控持续会话、输入/中文IME与基本恢复；启动系统构建前置已并行开始。
8. 平台服务与隔离：文件选择和应用数据、能力授权/撤销、包管理与工具、Linux进程/挂载/网络/系统调用/资源限制。
9. 可启动开发系统：固定Linux基础，构建可启动镜像，在虚拟机启动到Ming桌面并运行Kotlin应用；建立启动日志、关机、恢复和回归。
10. 独立系统能力：针对确定的日常任务补齐浏览器、设置、媒体、更新和恢复，并开展指定真实硬件验收。

后续每阶段先把具体设计和边界落入文档，再开发、复核和执行验收，随后自动推进。工程默认选择必须可追溯、可回退；实际缺少的设备和未验证能力持续标明。

## 验证原则

- 软件编译、软件渲染、GPU计算、GPU Surface、Wayland客户端、桌面合成器和系统启动分别记录。
- 使用真实应用与真实执行结果，不把跳过、旧证据或环境变量当作通过。
- 保留每阶段证据；源码/配置改变后，当前阶段重验，历史结果保持原样。
- 应用与SDK边界、故障隔离和性能统计一起验收。GPU资源计时不替代端到端帧率和输入延迟。
- 可启动开发版是中间里程碑；完整日常系统和生产质量仍须通过相应验收。
