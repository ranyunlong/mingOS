# 视觉效果与UI基础设计

日期：2026-10-24。用户已授权阶段验收后自动推进；本设计在该授权内执行。第6阶段包括圆角抗锯齿、阴影、动画与组件输入，完成其中一项不代表整个阶段结束。字体阶段的冻结证据保留，不用旧指纹验证新源码。

## 实现顺序与取舍

先提供SDK4圆角抗锯齿，继续既有整数compute/CPU参考与GPUbuffer→Surface；随后实现有界阴影缓存，再提供可取消的按钮输入和按需动画。用户已于2026-10-24明确选择真正Compose编程模型配MingOS组件：先进行真实Compiler/Runtime的wasmWasi编译、重组、状态和销毁资格验证，通过后采用自定义Applier接入Ming组件树、布局及SDK绘制。固定Runtime1.10.0的WASI发布物缺失已实测，用户随后要求比较JVM/ART与Wasm；现增加同一真实Compose counter的JVM编译/运行对照，保留WASI适配checkpoint，用户在实测对照后已明确放弃JVM、继续Wasm，当前恢复官方源码WASI适配，正式运行时保持Wasmtime与Ming-only导入。不得以自造Composable标记或替代Runtime宣称Compose兼容。新实例化渲染后端需要另行测量与设计，不能为提高截图表现直接删除现有工作额度或放宽像素oracle。研究与权威来源见visual-effects-research.md。

本阶段保留SDK1/2/3、op1硬边圆角和op3点阵语义、op5字体与测量。完整桌面、多窗口合成、IME、全局背景模糊、高DPI和启动镜像仍分别推进；先让可运行应用使用真实绘制与交互基础。

## 第6A项：SDK4圆角抗锯齿

manifest sdkVersion=4，默认仍需text feature，与SDK3字体支持一致。接受原ming_v1/v2和ming_v3字体API；新增ming_v4.frame_submit(ptr,len)->i32，精确i32 ABI，无WASI/路径/原生指针。ming_v4的帧走内部RPC方法frame_submit_v4，父进程同时核对manifest=4。旧frame_submit仍按至多SDK3解码，不能利用SDK4 manifest通过旧导入提交新op6。

MGF2/layout1不变，op6=[6,x,y,w,h,ARGB,radius,0]。几何为整数frame像素，x/y±1000000，w/h=1..1000000，radius=0..64并钳至min(w/2,h/2)。只绘制实心轴对齐圆角矩形；clip继续是替换式半开整数矩形。

圆角为矩形内部连续圆弧，4×4固定子像素采样，偏移为{1,3,5,7}/8px；在圆内或边界上算命中。中心/直边全覆盖，仅角区做16次测试。命中n时coverage=(255*n+8)/16，effectiveAlpha=(coverage*colorAlpha+127)/255，均整数除法向下取整，再使用既有整数RGB混色。radius=0与op1直角矩形一致；op1自身不改变。

CPU核接口为graphics::aa::coverage(x:i32,y:i32,width:i32,height:i32,radius:i32)->u32，输入x/y为矩形局部像素，返回0..255；无分配。GPU独立实现相同定义。先确认像素在范围和角区，再计算缩放后的圆心差；合法输入的|d|<=512，平方和<=524288，禁止对百万级坐标直接平方。

## 准入与故障

保留64KiB/256命令、单边4096/总4194304像素、GPU原始命令候选64M、图像/字体独立额度。AA命令最多16条，A=sum(4*r*r)<=262144；W=原有面积/文字work+16*A<=32000000。预检使用checked arithmetic，先于像素分配/GPU提交；完全裁剪或透明命令也消耗AA数量与保守采样额度。额外AA采样最多4194304次/帧，没有新增持久GPU资源。

SDK4 frame非法/失效句柄=-2，额度=-4；GPU终态错误继续终止会话，普通拒绝保留最后成功帧及可用会话。字体句柄创建/测量/关闭在SDK4仍经过同一TextService，不能扩大其额度或资源所有权。

## 精确集成接口

Graphics新增submit_with_sdk(bytes:&[u8],service:Option<&mut TextService>,sdk:u32)->Result<()>，仅支持3/4。既有submit_with_text固定调用sdk3；无op5时直接渲染已解码帧，不能再次用SDK2解码器拒绝op6。Broker新增submit_frame_v4；旧submit_frame在sdk4清单中仍固定SDK3绘制语义。

Kotlin新增独立MingFrame4，提供rect、rectAA、image、clip、text/textQ6、submit；不把v4导入混入Frame3。仍复用不可变MingText布局。新增ui-demo/sdk4实际Kotlin包，比较硬边/AA及半透明绘制和裁剪；先复用现有查看器输入，不宣称通用按钮系统已完成。

## 第6A验收

独立CPU采样oracle、显式corner goldens、半径/小尺寸/反射/大坐标/透明组合；SDK<=3拒绝op6和v4导入，SDK4旧导入拒绝op6；v4签名/guest memory在RPC前验证；额度拒绝后仍能提交正常帧。真实Kotlin示例CPU/GPU exactPNG对照与原生零读回；AA帧不重新上传图片/字形；截图只读回最终帧。按范围检查feature组合，并保留旧SDK样例字节回归。

AA只是质量起点，必须目视检查角部与缩放后效果，单次renderMicros不能代替持续帧率。第6A完成后立即进入阴影、输入和动画；全部阶段验收后再关闭第6阶段。
