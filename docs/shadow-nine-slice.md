# Shadow 九宫格 CPU 数学资格验证

日期：2026-10-24。结论：给定 canonical core 与映射公式成立；本次所有逐像素比较 exact，未发现需要更正的反例。这里只完成 stdlib Python 的 CPU 数学与证据验证，不代表 Rust 缓存或 GPU shadow 已实现。

## 数学约定与确定的公式

输入为正整数 `w,h`、`requestedR=0..64`、`R=0..16`。先计算 `r=min(requestedR,w//2,h//2)`。RRect alpha 使用 4×4 AA，像素内偏移 `{1,3,5,7}/8`；圆内及边界命中，`alpha=(255*hits+8)//16`。

输出在 body-local 半开域 `[-R,w+R) × [-R,h+R)`，其余为零。每个 pass 在数组之外取零，tent 权重 `R+1-|tap|`，`norm=sum(weights)=(R+1)^2`，每个 pass 均计算 `(sum+norm//2)//norm`。本探针固定先水平、后垂直，保留中间整数舍入；不合并为一次 2D 舍入。

令 `m=r+R`，`cw=min(w,2*m+1)`，`ch=min(h,2*m+1)`。先独立光栅化 `cw×ch` RRect，四周补 `R` 个零像素，再使用同样的两个整数 blur pass。缓存 patch 为 `(cw+2R) × (ch+2R)`。

横轴采样规则（纵轴独立同理）：

```python
if w == cw:
    q = u
elif u < m:
    q = u
elif u >= w - m:
    q = u - (w - cw)
else:
    q = m
patch_x = q + R
```

仅在输出半开域内使用 mapper。外部像素直接为零；不能把外部坐标继续交给数组索引。读取离散整数像素，不插值。

## 为什么每个 pass 的舍入不会破坏等价性

tent 的有限支撑意味着一个输出只依赖相对偏移 `[-R,R]²` 内的输入 alpha。对于可伸缩轴，core 为 `2(r+R)+1`。

- 左段 `u<m` 保持坐标，其采样窗口不会触及 canonical 的右侧圆角；右段对称地平移，保留距右边缘的距离。
- 中段 `m<=u<w-m` 的窗口整数坐标全部落在 `[r,w-r-1]` 内。canonical 的中心列 `m` 的窗口为 `[r,r+2R]`，也全部处于其直边区。这里 RRect alpha 与该轴位置无关。
- 对不伸缩的轴保持恒等映射。两轴分别应用以上性质，完整局部输入邻域逐点相同；不是只保证卷积结果近似。

因此对应位置的每一行加权和完全相同，水平舍入后的中间值也相同；垂直加权和与舍入继续相同。这个论证不依赖卷积的线性舍入假设。四周 halo 足以容纳有限支撑；省略域外的零不会改变任何和。`+1` 为伸缩中段保留一列/行完整支撑的代表像素，不能删除。

输入钳制必须在确定 core 和 margin 之前进行。canonical 的缩小尺寸不会再次降低有效 `r`：缩小轴的 core 至少 `2r+1`；未缩小轴保持原尺寸。

## 独立实现与 RED/GREEN

`probes/shadow-nine-slice/reference.py` 使用全局采样点到内部矩形最近点的有理数距离定义 AA；在完整 `(w+2R)×(h+2R)` alpha 上逐 tap 水平/垂直卷积，不调用 candidate、mapper 或 patch。`candidate.py` 使用角部反射 AA，并以加权前缀和实现 tent，每个 pass 一次舍入。

`test_shadow.py` 先针对返回零的 candidate 记录 RED：8 项测试中 7 项按预期失败，独立 AA goldens 通过；实现后 8/8 GREEN。测试包含 AA goldens、tent impulse、每 pass 舍入、零边界、blur=0、恒等映射、接缝 goldens、193² patch 和细线/半径钳制。大数组失败仅输出差异标志，避免 unittest 构建无关的巨型文本 diff。

## 实际逐像素资格结果

每组重建并比较**整个输出域**，包括全部 halo、四边、内部及接缝；没有仅抽取 corner。比较为整数完全相等，无阈值与容差。

| 批次 | 逻辑用例 | 比较像素 | Python elapsed 秒 | 差异像素 |
| --- | ---: | ---: | ---: | ---: |
| small | 2,401 | 705,208 | 2.042446 | 0 |
| seams | 134 | 969,561 | 10.657025 | 0 |
| radii | 325 | 3,381,820 | 31.630366 | 0 |
| blurs | 238 | 1,353,931 | 12.527394 | 0 |
| large | 6 | 1,022,147 | 7.489675 | 0 |
| 合计 | 3,104 | 7,432,667 | 64.346906 | 0 |

- small 是 `w,h=1..7 × requestedR={0,1,2,3,4,8,64} × R={0,1,2,3,4,8,16}` 的完整笛卡尔积。只复用 full oracle 的相同有效几何结果；每个请求仍重新构建 candidate。总计实际执行 1,438 次独立 full convolution。
- seams 遍历 `2m-1,2m,2m+1,2m+2,2m+3` 周围尺寸的两轴组合（去重且尺寸至少 1），包含最大参数、奇偶尺寸、刚开始伸缩和不伸缩情形。
- radii 遍历所有 `r=0..64`，分别取 `R=0,1,3,8,16`，两轴均伸缩。
- blurs 遍历所有 `R=0..16`，分别取请求半径 `0,1,3,8,16,32,64`，覆盖 x-only 与 y-only 伸缩和请求半径被钳制。
- large 包含 `(769,513,64,16)`、`(512,384,64,16)`、`(1024,1,64,16)`、`(1,1023,64,16)`、`(513,257,64,0)`、`(257,514,0,16)`。
- 整体分类：两轴伸缩 433 组，x-only 348 组，y-only 348 组，不伸缩 1,975 组。

## 负对照确认测试灵敏度

`negative_controls.py` 刻意破坏三个条件，独立 oracle 均立即检出差异。以下是小尺寸搜索按 `(area,w,h,requestedR,R)` 排序得到的首个 witness，**不是原公式的反例**：

| 故意错误 | w×h / requestedR / R | body-local 位置 | 错误值 / 正确值 |
| --- | --- | --- | --- |
| 两 pass 之后才舍入 | 1×2 / 0 / 2 | (-1,0) | 31 / 32 |
| core 去掉 `+1` | 1×3 / 0 / 1 | (-1,1) | 48 / 64 |
| margin 漏掉 blur | 1×4 / 0 / 1 | (-1,1) | 48 / 64 |

搜索范围和完整 witness 在 `artifacts/shadow-nine-slice/negative-controls.json`。

## 字节与时间证据的解释

A8 单通道有效负载：`patchbytes=(cw+2R)*(ch+2R)`，`fullbytes=(w+2R)*(h+2R)`。由 `r<=64,R<=16` 可得 `cw,ch<=161`，含 halo 的 patch 单边最多 `193`，单个 patch 至多 **37,249 bytes**。若后续选择 32 位单通道存储，单个 patch 的有效负载为该值的 4 倍，即最多 **148,996 bytes**；这不包含对齐、缓存元数据、临时 pass、驱动资源或 Python 对象开销。

| 实际矩形 / r / R | core | patch extent | A8 patchbytes | A8 fullbytes | full / patch |
| --- | --- | --- | ---: | ---: | ---: |
| 769×513 / 64 / 16 | 161×161 | 193×193 | 37,249 | 436,545 | 11.720× |
| 512×384 / 64 / 16 | 161×161 | 193×193 | 37,249 | 226,304 | 6.075× |
| 1024×1 / 0 / 16 | 33×1 | 65×33 | 2,145 | 34,848 | 16.246× |

例如 `769×513` 的 full 为 `(769+32)*(513+32)=801*545=436545`，patch 为 `(161+32)^2=37249`。该例本机 Python full oracle 实测 **3.489375 s**、patch 构建 **0.073484 s**、整幅重建与比较 **0.022938 s**。`512×384` 分别为 **1.916601 s / 0.079790 s / 0.012597 s**。两者 patch SHA256 相同，而各自 full 与重建 SHA256 分别一致。

这些时间来自 Windows 上 `D:\python\python.exe`、Python 3.13.12，使用 `perf_counter()`，是单次 CPU Python 数学探针时间。两条路径的算法也不同（逐 tap / 前缀和），不能将时间比解释为实际 GPU、Rust cache、持续帧率或端到端收益。JSON 保留每个阶段的原始时间及来源指纹。

## 复现与证据位置

从仓库根目录运行；`-B` 避免生成 `__pycache__`。无需 pip、Cargo、Wasm、JVM 或图形运行时。

```powershell
& D:\python\python.exe -B probes/shadow-nine-slice/test_shadow.py
& D:\python\python.exe -B -u probes/shadow-nine-slice/qualify.py small
& D:\python\python.exe -B -u probes/shadow-nine-slice/qualify.py seams
& D:\python\python.exe -B -u probes/shadow-nine-slice/qualify.py radii
& D:\python\python.exe -B -u probes/shadow-nine-slice/qualify.py blurs
& D:\python\python.exe -B -u probes/shadow-nine-slice/qualify.py large
& D:\python\python.exe -B probes/shadow-nine-slice/negative_controls.py
```

各批次写 `artifacts/shadow-nine-slice/{suite}.json`；原始执行日志、RED/GREEN 日志、负对照和汇总也在该目录。JSON 包含精确用例生成器的源码 SHA256、Python 环境与各大矩形输出指纹。完整参数域的可扩展性由上述有限支撑论证支撑；实测用例不是百万级尺寸及全部参数组合的穷举。

本次仅写新探针、证据和本文件。尚未实现 GL/shader、Rust 缓存、资源额度/淘汰或 handle API，也未改变 root Rust、SDK、AA、Compose 路径。

