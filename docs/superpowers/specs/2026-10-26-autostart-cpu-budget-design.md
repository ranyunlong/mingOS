# 受限CPU下的首轮初始化调度

日期2026-10-26。前提：Result phase三文件修复已通过Windows398、Linux222和3个native终态负例，kernel资格尚未完成。v12外层2CPU时两worker初始化TIMEOUT；v15外层4CPU无节流，Counter firstReady290255ms、Text仍初始化TIMEOUT，说明仅增加外层资源不够。两轮均保存原始日志/私有盘，未进入原关闭脚本；原盘不变。

选择有界启动排队，保持Wasmtime优化、单worker启动1核/稳态0.25核、512MiB/pids64、300000ms及fuel不变。Strict模式初始化并发=max(1,min(4,可用CPU数-1))；Off保留现有同时启动。该策略为Shell/监督器保留CPU预算，不按TCG或应用ID特判。

只调度catalog已有初始autostart。Session保存最多4个待启动ID，每次tick在poll后检查：实例只有在Parent已确认applied且cpuMax已降为STEADY_CPU_MAX后释放初始化槽；失败并reap的实例移除后也释放槽。不等待首帧，避免没有首帧的应用阻塞后续启动。待启动队列非空时不能进入全局ready/活动计时。

Shutdown清队列；用户主动Launch移除同ID的排队项，避免稍后重复启动；关闭尚在队列的应用取消该排队项，实际运行实例按原关闭路径。队列为空后运行时不再做启动计数。没有新CLI/配置/应用能力接口。

先RED验证：初始化满槽/只有applied但仍全核/缺quota收据均不启动下一项，降配额无需首帧可继续，失败释放槽，队列阻止提前ready，shutdown/queued-close取消排队，CPU预算策略确定。再GREEN、回归与真实kernel验证。

验收仍要求两个真实Kotlin worker同时存活、严格限额、精确文字/数据和自然完成；不得用只启动一个应用代替。若新证据不支持此策略，保留失败，不加长看门狗掩盖。

复核后收窄：仅Strict且duration_ms=0的持续会话采用此排队预算；有限时长会话与Off维持旧并发启动，避免将排队时间压进既定300s总wall上限。新增bounded回归已实际RED(0pass/1fail)。启动时在既有有界trace内记录一次startup_budget，含观测CPU数与选中的limit；不是CPU affinity或硬预留一核。
