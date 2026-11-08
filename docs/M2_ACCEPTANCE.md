# M2 台式机开发验收

验收日期为 2026-11-08，以下时间均为北京时间。范围是台式机 `dev` 实例，由管理员在场触发 QQ 和手机测试。

| 项目 | 验收级别 | 结果 |
|---|---|---|
| QQ 入口 | 模拟事件、真实 QQ | 13 个模拟用例覆盖唯一 QQ 管理员的好友私聊、其他账号、群聊、请求、通知和管理员配置歧义。真实管理员私聊收到了回复。 |
| 长期记忆 | 真实 QQ、本机 SQLite | `/lmem summarize` 写入一份总结及四条记忆。测试记忆于 00:23:59 写入，AstrBot 于 00:27:52 重启，`/new` 后正确答出测试代号，记忆访问时间更新至 00:28:45。 |
| 原版小房间与星海 | 本机 HTTP、既有测试 | 星海从 LivingMemory 2.9.1 读取并刷新测试记忆。小房间读取活动账本，按开关显示活动关闭。两个来源均使用真实开发数据。相关 Python 测试 73 项通过。 |
| 安卓与 PhoneMCP | Windows 安卓实机、本机 MCP | 明确选定 `emulator-5554`，通过 PhoneMCP 查看已登录推荐首页、点开公开视频、核对画面随时间推进并返回。PhoneAgent 离线测试 7 项通过。 |
| QQ 截图往返 | 真实 QQ、本机 SQLite | 管理员私聊发出 `截图看看`。活动账本在 12:42:16 记录截图，在 12:42:26 记录 QQ 送达回执。唯一截图送达记录为 `sent`，管理员确认收到图片。 |

## 版本、启动和数据位置

- 上游基线为 `b56a03c`，Fork 为 `To-k1/dafeiyu-astrbot`。M2 使用本地 `feature/m2-admin-ingress` 分支，运行代码与启动说明的检查点为 `7154526`。DSH 与 Bridge 的运行验收排在 M3。
- AstrBot 4.29.4、NapCat 4.18.38、LivingMemory 2.9.1、Ollama 0.40.15 与 `bge-m3`、活动插件 v3.0.3、雷电 14.0.34.1、B 站 9.18.0、PhoneMCP v0.5.0 与 fastmcp 4.1.1。AstrBot 使用 Pillow 12.3.0 和 mcp 1.30.0，依赖检查通过。QQ 聊天与 LivingMemory 总结绑定官方 `deepseek-flash`。
- `D:\Share\code\FishHome\dev\Start-FishHome.cmd` 从各自工作目录打开 AstrBot 与 NapCat。快登参数保存在本机脚本。NapCat 进程已确认带快登参数，OneBot 在本机回环重新连接。
- PhoneMCP 源码和独立环境位于 `dev\PhoneMCP`，监听 `127.0.0.1:8009`。活动插件显式选择雷电的 `emulator-5554` 并使用 `1440×2560`。星海和房间在 `127.0.0.1:8767` 提供原版界面。见 [PhoneMCP 启动说明](../deploy/phone_mcp/README.md) 和 [观察台启动说明](ROOM_AND_REMOTE_VIEW.md)。
- 开发记忆与活动账本在 AstrBot 的 `data\plugin_data`，分别位于 `astrbot_plugin_livingmemory` 和 `astrbot_plugin_bili_activity`。后者包含 `activity.sqlite3`、`control.json` 与截图目录。机器人登录态与 API Key 留在开发运行目录。

## 变更与回退

- 在 OneBot 事件转换和日志之前应用了 [管理员私聊入口补丁](../deploy/patches/astrbot_4.29.4_qq_admin_ingress.patch)。重建 venv 时可按 [补丁说明](../scripts/README.md#m2-管理员私聊入口) 重新应用。回退后停止用真实 QQ 做验收，直到入口限制重新启用。
- 小房间和星海沿用原版 UI 和只读投影。星海新增 `--astrbot-root` 读取本地数据库，房间复用 `--life-data-dir`。观测服务可独立停止，数据库由原插件管理。
- 活动插件沿用上游存储和控制代码。卸载时先停止 AstrBot，再把 `data\plugins\astrbot_plugin_bili_activity` 移出插件目录，保留 `data\plugin_data\astrbot_plugin_bili_activity` 供后续恢复。

## 后续验收

- TODO 在真实链路验证其他 QQ 账号和群聊的拒绝、模拟器断连后的恢复，以及更多推荐页交互。当前入口拒绝证据来自模拟事件。
- TODO M4 验证 LifePlanner、Goals、Workshop 与项目的自动运行和跨日恢复。开发实例的自主活动、定时活动和公开互动已确认关闭。
- TODO M5 实测交互与自主两本人民币账及公开写入总开关。M6 验证 ThinkBook 的生产隔离、快照和恢复。
