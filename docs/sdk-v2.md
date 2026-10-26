# Ming SDK v2

日期：2026-10-24。实验接口，清单 sdkVersion=2；v1 ABI 与原有示例保持可用。v2 普通应用仍无宿主路径、网络、设备或完整 WASI。

## 函数导入

所有参数和结果都是 i32。ming_v1 的五个函数继续可用，新增 ming_v2：

| 函数 | 参数 | 含义 |
| --- | --- | --- |
| file_read | handle,offset,pointer,length | 最多 65536 字节；返回实际字节数，EOF 为 0 |
| image_open | fileHandle | 解码授权文件中的 PNG，返回正图像句柄 |
| image_width / image_height | imageHandle | 图像尺寸 |
| image_close | imageHandle | 释放图像句柄，已渲染帧保持有效 |
| frame_submit | pointer,length | 验证并提交完整绘制批次 |
| event_next | pointer,capacity | 写入 16 字节输入记录，capacity 至少 16 |
| random_fill | pointer,length | 运行时熵能力：成功 0，最多 4096 字节/次、1 MiB/运行 |

通用负值：-1 未授权，-2 无效参数/句柄/帧，-3 IO/解码/熵源错误，-4 预算限制。guest memory 必须由模块导出为 memory；worker 在复制前验证地址和长度，使用导出内存对象读写，父进程从不接受 guest 指针。

file_read 的父进程内部消息只携带句柄、偏移和长度；返回有界 base64 数据，worker 核对长度再写入。frame_submit 一次复制有界命令包，图像像素不逐帧传输。SDK1 不可导入 ming_v2。

## 标准库运行时适配

Kotlin/Wasm 标准库的对象身份哈希可达代码引入 wasi_snapshot_preview1.random_get。当前打包器仅把这一对函数导入显式改名为 ming_v2.random_fill，记录原始/打包 SHA-256 与改写列表到 build-info.json。它不链接完整 WASI；其余 WASI 导入在打包时拒绝，直接运行原始 WASI 模块也被宿主拒绝。

此适配器不是安全边界。宿主仍按 SDK 版本、精确类型和内存范围校验；熵实现调用平台随机源且单次/总量有界。Kotlin Random 的上层 PRNG 不因种子来自系统而自动成为密码学随机接口。

## 数据与绘制预算

SDK1 文件上限仍为 256 KiB；SDK2 的显式授权普通文件上限 8 MiB。每批读取最多 64 KiB。PNG 最多 4096 单边、4,194,304 像素，RGBA 最多 16 MiB/图，总图像 32 MiB、最多 8 句柄；解码器另设 32 MiB 分配限制。

帧最多 64 KiB、256 条命令、4096 字节文本池；宽高 1..4096、总像素不超过 4,194,304；估算光栅工作不超过 32,000,000 像素操作。完整解析和资源验证通过后才替换上一帧。

## 帧字节布局

全部小端 i32，头部六项：magic=0x4d474632、formatVersion=1、width、height、commandCount、textPoolBytes。随后是 commandCount 个 8*i32 记录，再跟文本池。总长度必须精确匹配。

| op | 七个参数 |
| --- | --- |
| 1 矩形 | x,y,w,h,ARGB,radius,reserved=0 |
| 2 图像 | imageId,x,y,w,h,0,0 |
| 3 文字 | x,y,scale,ARGB,poolOffset,byteLength,0 |
| 4 裁剪 | x,y,w,h,0,0,0 |

参考文字覆盖 ASCII 字母、数字和常用符号，其余字符以占位字形显示。scale=1..4；每段文字最多 256 UTF-8 字节；radius=0..64。图像采用最近邻参考采样，不代表最终图像质量/色彩管理。

## 输入

每条记录四个 i32：type,a,b,c。0 关闭、1 尺寸、2 缩放、3 位移、4 点击、6 原始比例、7 适应窗口、8 空闲。关闭为独立优先标志；尺寸保留最新值；位移与滚轮累积合并；普通队列最多 128。空闲等待最多 20ms，Kotlin 不为空闲事件重绘。

原生窗口采用 winit 事件驱动分发与 softbuffer 安全窗口缓冲。帧和输入按有界逻辑视口投影，物理窗口表面最多 16,777,216 像素；完整字体、无障碍、编辑控件与系统 IME 协议留后续。
