# Changelog

## 2026-11-07 — VM 复建输入

- 增加公开 Cargo/Gradle 缓存导出与显式恢复工具，外部固定 descriptor、严格 USTAR、全新匿名卷和消费者卷、独立只读复核，原资源保持。
- 增加源码别名归档数据层，绑定冷构建完整源码清单，支持长路径及空目录，恢复保留 Linux 模式并规范化 owner。
- 实际完成 1487 源文件/392 目录和 Cargo 12495/Gradle 2496 文件的归档恢复；宿主导出副本另行封印。
- 恢复后的源码和 Gradle 消费者已实际重新编译 Text Entry / Motion；独立容器及宿主回放通过，46 个导出文件全部匹配。
- 增加公开开发 VM 外层入口及固定 driver，PowerShell 控制 34/34、Linux driver 控制 8/8；缓存/源码独立 Linux 控制 51 通过、1 项 Windows 专属跳过。
- 七命令冷构建 9436c4cf 已通过（776/0/24）；卸载 f500a8a3 的输入缺失失败代封存，新代 28aef8a1 已完成同盘双启动及公共 export / verify / replay，私有文件保留检查通过。
- 实际开发 bundle 已组装并独立核对内核和 54 项必需内容；完整 pack、真实开发 guest 和笔记内容保存流程继续。
- 完整开发交付尚未验收；历史 legacy frozen probe 缺失、完整 boot suite 未全绿的状态保持。
