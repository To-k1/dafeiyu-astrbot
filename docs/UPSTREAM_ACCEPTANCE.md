# 上游功能验收与 FishHome 进度 · 2026-11-09

在独立 worktree 验收已合并的 `fish-home` 源码。台式机日常试跑使用 `feature/desktop-trial`。隔离实例使用 AstrBot 4.29.4、上游心境中枢 v0.1.0、临时 SQLite 和回环模拟 OneBot / OpenAI 服务。PhoneMCP 只读检查使用台式机现有的雷电实例。

| 上游功能 | 已验收 | 待验收 |
| --- | --- | --- |
| 心境中枢 | 隔离 AstrBot 收到模拟主人私聊后先评估，再将心境提示注入主请求，出站过滤内部标签。评估返回无效 JSON 时降级，主回复继续。牵挂入库，重启后 `/心情` 仍可查且不请求模型。模拟访客和群聊未触发回复或写入。 | 台式机日常 QQ 启用、真实供应商计费、音色和出站布局实机联动。默认 `sync_pre` 会增加模型调用，启用前需核对计费归账。 |
| 跨日好奇心 | 定向测试覆盖原版卡片→活动→显式解决的入口、重读旧来源不能完成、无新来源后的暂停、活跃名额上限及重开数据库。 | 登录推荐流中的真实多日自主活动。 |
| 持有念头的收尾 | 定向测试覆盖同条消息在工具续轮中保持原选择、无关回复保留念头、准确提到后只清理一次。 | 实际 QQ 日常对话与主动投递联动。 |
| PhoneMCP 适配 | 用上游适配器调用台式机的真实 PhoneMCP，前台应用名成功解析为包名。已核对 Home、Back 对应的服务端工具名，导航映射由定向测试覆盖。 | 真实会话中的 Home、Back 操作。 |
| 房间与星海 | 原版页面用浏览器验收交互、手机布局及同步失败恢复。隔离房间读取了真实 SQLite 心境与牵挂。跨机摘要保留心境状态，排除牵挂明细与上次互动时间。 | ThinkBook 实际只读同步，正式心境库的归档和隔离恢复。 |

在合并版 worktree 使用 AstrBot 的 Python，设置 `PYTHONPATH=tests` 后运行定向测试，共 150 项通过。运行 `node tests/memory_observatory_browser.mjs --spawn-demo` 验收演示数据的房间与星海。隔离 AstrBot 的 OneBot、评估、重启和房间投影另外实测，退出后释放测试监听。

```powershell
python -B -m unittest test_affective_core test_affective_integration test_bili_curiosity_goals test_bili_expression_cadence test_phone_agent test_memory_room test_fish_m6 -q
```

## FishHome 已完成与待做

- **M0–M5 台式机核心验收完成。** Windows 原生运行、管理员 QQ 私聊、跨会话记忆和手机截图已实测。DSH 跑过受限文件任务，LifePlanner 单次真实投递后写回目标与记忆队列。交互和自主预算的零额度拦截已实测，交互账本已有正常结算。
- **M6 台式机部分完成。** 托盘可一键启动 9 个服务，原生归档、隔离恢复、可读导出和房间快照已在本机演练。测试长期记忆已备份并清空。运行实例继续进行多日试跑和断线恢复验证。
- **M6 正式机部分待做。** ThinkBook 可用后核对 Windows OpenSSH 指纹、登录后启动、原生快照定时生成、台式机拉取和只读房间同步。正式记忆默认不迁移，选择性和全量迁移待现场演练。接入心境中枢后需把其独立数据库纳入归档验收。
- **M7 与进一步开放待做。** 验证正式机多日运行、登录后恢复和告警。自主活动及公开写入当前关闭，后续仍需核对其他付费通路、记忆队列入长期库，以及平台写入审批。

台式机运行与回滚记录见 [M6 检查点](M6_CHECKPOINT.md)。设计入口见 [台式机托盘控制台](DESKTOP_MANAGER.md#界面图标和人格源码)。
