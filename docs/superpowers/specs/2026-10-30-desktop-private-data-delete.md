# 桌面私有数据删除确认

日期：2026-10-30。

## 目标

- 在 Linux 桌面为已登记应用提供独立的 `D` 管理入口，明确表示卸载并永久删除该应用私有目录。
- 保留现有 `U` 普通卸载入口；`U` 只撤销安装登记并保留私有数据。
- 删除操作必须经过当前已呈现帧对应的按下/释放和系统确认提示；脚本、离屏会话、未呈现帧、位置不匹配不能触发删除。
- 删除成功后撤销安装登记、移除当前 desktop catalog 和待启动队列，并删除目标应用私有目录。
- 公共目录、公共授权、兄弟应用私有目录和安装包历史内容不受影响。

## 状态与交互

- Dock 每个应用的管理入口为 `D`（卸载并删除私有数据）、`U`（卸载并保留数据）和 `S`（公共目录授权管理）。
- 初始提示显示 `PRIVATE DATA WILL BE DELETED`，按钮为 `CANCEL` / `DELETE`。
- 应用仍有运行代时只显示 `CLOSE APP FIRST`，不改变登记和数据。
- 登记已撤销但私有数据清除失败时，应用从 catalog 移除，提示显示 `PRIVATE DATA NOT DELETED`，按钮为 `CANCEL` / `RETRY`；重试只执行清除，不重新激活登记。
- 无安装登记根、无数据根或无法打开私有 authority 时，D 入口不产生可执行删除动作。

## 父侧组合

- 使用已有 `PackageInstallRegistry::uninstall_with_data_policy` 和 `UninstallDataPolicy::PurgePrivateData`。
- 运行中实例先阻止；旧式私有 grant 或仍存活的私有 session 导致清除失败时保持“已卸载、数据保留、可重试”。
- 清除仍由 `PackageStorageAuthority::purge_private_data` 执行，沿用 pinned descriptor、`O_NOFOLLOW` 和目录同步边界。

## 验收

- CPU/GPU shell 正确绘制 D/U/S 三个入口和删除确认提示。
- 原生 controller 要求匹配的呈现帧和按下/释放；取消不改变登记或数据。
- 成功删除后目标私有数据不可恢复，兄弟私有数据和公共文件仍可读。
- 清除失败显示可重试状态；重试成功后提示消失且不会重新安装应用。

## 非目标

- 不实现安装包选择器、更新迁移、多用户身份、Windows 安装服务或物理输入/GPU 最终验收。
