# SDK3字体与文本布局设计

日期：2026-10-24。GPU直接呈现/缓存已验收，按用户连续开发授权进入字体阶段。

## 范围和选择

提供单行中文/拉丁标签的真实字体、测量、布局与GPU灰度抗锯齿。旧SDK1/2与op3点阵字体保持语义，新增SDK3与op5，不增加编辑器/IME/段落排版。详情和主来源见docs/typography-research.md。

固定字体notofonts/noto-cjk Sans2.004提交523d033d6cb47f4a80c58a35753646f5c3608a78中的NotoSansSC-Regular.otf；8331336字节，下载后SHA256=faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9。保留上游OFL和嵌入版权，不自行子集化。

采用cosmic-text=0.19.0、关闭默认features、仅std/swash。数据库只加载内置字节，通过new_with_locale_and_db构造；禁止FontSystem::new/new_with_fonts系统扫描。只接受BuiltinFont1/Regular400，应用不能提交字体文件、家族名、原生句柄或路径。真实解析、cmap覆盖、灰度结果和内存必须验证后启用。

## SDK和语义

manifest sdkVersion=3允许原有ming_v1/v2函数及以下ming_v3精确签名，仍禁止WASI/未知导入/非i32签名：

- text_layout_create(ptr,len,font_id,em_size_q6,flags)->handle/negative。
- text_layout_metrics(handle,out_ptr,capacity)->48/negative；12个LE i32：advance,ascent,descent,gap,line_height,ink_left,ink_top,ink_right,ink_bottom（前9为Q26.6），glyph_count,status_flags,font_pack_revision。
- text_layout_close(handle)->0/negative。
- frame_submit(ptr,len)->status；线格式仍MGF2/layout1，op1..4与旧版一致，op5为[layout_handle,x_q6,baseline_y_q6,ARGB,0,0,0]。

文本布局句柄只属于一次broker会话，正值带类型区间、不复用；关闭后不可再提交，已接受帧保留其资源直到完成。SDK2绝不接受op5。新增独立Kotlin MingText/MingFrame3 API，避免把v3导入混入旧MingFrame.submit。旧样例保留并回归。

允许512 UTF8字节、512 shaped glyph、64个活动布局；em为8..96px、Q6每1/4px步进。只接受水平LTR中文/拉丁标签，无换行/tab/C0/C1/bidi格式控制或RTL run；空串合法。默认缺字拒绝，flags bit0允许.notdef并返回MISSING_GLYPH状态。坐标是frame像素空间、x向右y向下、左pen与baseline原点，大小范围±1,000,000px；没有隐式DPI转换。

测量与绘制共用immutable shaped records；advance包括空格/kerning，不等于bitmap宽度。使用face metrics计算非负ascent/descent/gap，lineheight为其和；墨迹边界覆盖支持的subpixel相位，half-open，允许negative bearings。真实引擎布局坐标、phase和覆盖必须用测试确认。错误码沿用-2非法/句柄，-4预算，-5缺字，-6不支持文本；设备故障仍是会话终态错误。

## CPU与GPU

TextService在broker持有字体/布局和受限mask缓存。各frame引用唯一输入<=4096字节，实例<=2048；保留256条命令/64KiB/4M像素限制。CPU masks<=4MiB/4096项，保留布局和cache metadata<=2MiB；每次mask插入和帧栅格化必须检查布局占用后剩余的元数据额度，失败不能留下超额缓存；已被帧引用的coverage不能通过淘汰逃避计费。引擎私有分配另设64MiB资格目标，不当成已证明的RSS硬限制。

GPU单独4MiB packed-u8 coverage atlas，不占图像handle/32MiB image atlas；其持久驻留表元数据独立限制为1MiB。CPU/GPU哈希表按usable capacity的两倍槽位、control和对齐余量保守计费，不能把capacity当作实际bucket数量。每mask<=256x256，frame新增上传<=1MiB；缓存key包含字体revision/glyph/em/phase，排除颜色与整数平移。当前frame/inflight项钉住，完成后可LRU回收。

每op5在16x16tile建立按原glyph顺序的候选索引，最多65536引用、每tile<=64候选，frame text staging<=2MiB；由此额外检查<=16,777,216。GPU在既有命令遍历中按tile采样，仅对候选glyph混色；不按每像素扫描整段文字，不生成整段图片重传。有效alpha=(coverage*colorAlpha+127)/255，沿用整数RGBblend，与CPU使用同mask。继续直接GPU buffer/Surface路径。

所有普通预算/非法输入失败必须先于有效帧提交，保留最后成功帧和可用会话；GPU设备故障仍终止会话。计数glyph misses/uploads/bytes/residency，分别记录cold与warm时间。高DPI/完整Unicode/编辑IME另行验收，不用frame内字体样本宣称macOS级桌面质量。

## 验收

校验字体license/sha/name/cmap；真实字体绘制MingOS 中文，标点。AV fi é Á，12/16/24/48px；基线、空格、combining、negative bearing、clip、缺字与测量一致。SDK越权/签名/内存范围/句柄/预算/关闭/inflight/缓存回收和shader候选上限回归；同文字重复绘制不上传mask，颜色/整数移动不新增mask。真实KotlinSDK3样例、CPU/GPU像素对照、原生零读回、LinuxCPU/Wayland/allfeatures全部执行并保留证据。
