# M4 接入与验收 · 2026-11-09

M3 已收到管理员 QQ `/dsh ping` 的真实回执，见 [M3 验收记录](M3_ACCEPTANCE.md)。M4 已通过台式机受控验收。原版 LifePlanner 选中样例目标并投递给真实 DSH，产物经核对后回写原目标和记忆队列。

## 接线和数据位置

- 原版 LifePlanner 选中 `research` 目标并领取运行权后，才匹配本地 `control.json` 的 `dsh_life_task`。该配置默认为 `null`，包含 `goal_id`、DSH 工作目录 `cwd`、相对输入路径 `source` 和短指令 `instruction`。新任务只允许管理员安排的 `once_at` 单次活动启动，周期活动只可查已投递的会话。
- `activity.sqlite3` 的既有 `settings` 保存任务、请求、会话、预期产物和自主账本归属日。归属日按北京时间 04:00 切换，恢复时沿用首次记录。请求发送前先保存标识，状态查不清则进入 `needs_review`，后续只查询原会话。原版调度冷却仍适用。
- DSH 在 `FishHomeRunner` 私有工作区内写 Markdown 和 JSON。适配层确认终态与请求对应、读取路径与出处、取得 Token 用量后，写入既有 `episodes`、合并 `goals` 的检查点，并向原 `memories` 队列添加一条摘要。目标状态仍由原版管理。本轮验证到队列入库。

## 验证记录

- 本地集成测试使用原版 `run_activity`、临时活动库和模拟 DSH。覆盖周期活动拦截新投递、投递后断线、重开数据库后跨 04:00、服务离线时 `needs_review`、部分写回中断，以及最终只投递一次、只记录一份产物经历和一条记忆摘要。
- 在 Windows 的 `fish-home-dev` 环境启用 `PYTHONUTF8=1`，将 Git Bash 加入 `PATH`，运行 `python -B -m unittest discover -s tests -p 'test_bili_*.py'`。共 658 项，643 项通过，15 项跳过。
- 本机 DSH `0.2.2-rc.1` 使用原 M3 的 `FishHomeRunner` 标准账户、文件工具配置与 `127.0.0.1:59934`。实际读取原请求终态、两份产物、输入来源和 Token 用量，并由 M4 经原版生活入口写入临时目标与记忆队列。本次只开放查询调用，模型费用为零。
- 恢复本机 DSH 后，原 QQ Bridge 的签名凭据返回 HTTP 401。更新开发实例内的凭据并通过本机 Bridge 探针复验。既有 QQ 回执见 M3 记录。

## 单次真实投递

使用 `fish-home-dev` 的 Python 3.12 调用原版 `run_activity("once")`。生活控制与数据库放在 `%LOCALAPPDATA%\Temp\FishHome-M4-Flash-20261109`，DSH 沿用 `FishHomeRunner` 私有工作区和文件工具配置。试验只允许一个 `session.prompt`，本地等待上限设为两分钟。活动完成用时约 10.5 秒。

| 项目 | 实测结果 |
| --- | --- |
| 投递 | 选择官方 `deepseek-official / deepseek-flash`，原请求在 DSH 历史中出现一次，终态 `completed`。 |
| 产物 | Markdown 报告与 JSON 来源均记录 `sample.csv` 共 3 行、合计 24 分钟。执行前后读取的原 CSV 内容一致。 |
| 用量 | 未缓存输入 1,200、缓存命中输入 29,184、输出 1,123、缓存写入 0。归属为 2026-11-09 自主账本，记录的是 Token 用量。 |
| 写回 | 原目标检查点字段保留，并加入报告、来源与经历引用。`episodes` 一条，`memories` 一条，记忆状态为 `pending`。原目标按既有流程保留为 `paused`。 |

临时验收目录中的 `acceptance.json`、`report.md`、`sources.json` 和 `activity.sqlite3` 可用于复查。原产物留在 `FishHomeRunner` 的 `%LOCALAPPDATA%\FishHome\m3-file-check\output`，具体路径保存在目标检查点。

## 接续和回滚

2026-11-13，ThinkBook 已启用自主文件研究。生活活动可以用 `df_file_research` 为当前目标从 Bridge 白名单中选择输入并排队。LifePlanner 后续选中该目标时使用文件专用预设，核验输入前后一致，继续写回原目标、经历和记忆候选。正式白名单目前只有 Runner 工作区里的 `sample.csv`。隔离活动库实测一次投递，四笔自主账单全部结算，合计 ¥0.00428852。正式调度保持运行，首次自然选题及跨日续跑待观察。关闭活动配置 `dsh_agent_tasks_enabled` 即可停用此入口。部署前文件保存在 ThinkBook 的 `%LOCALAPPDATA%\FishHome\rollback\agent-dsh-20261113-224740`。

同日晚补上 `df_file_research create`。肥鱼可以为已选目标直接提交任务说明，无需现成文件。该路径选用仅开放文件写入的 `fishhome-draft`，报告归档同时核验来源清单明确记为 `null`。ThinkBook 隔离实测从目标、排队、周期调度到 Markdown、候选和自主账本均通过，投递一次，三笔请求共 ¥0.00966464。正式入口已启用，QQ 已重连。更新前的回退副本位于 `%LOCALAPPDATA%\FishHome\rollback\agent-dsh-20261113-225328`。

- 现有 AstrBot 开发实例的活动与生活选择仍关闭，保持其既有 B 站单次验收前置条件。M5 负责人民币费用门控、峰谷调度和公共写入总开关。无人值守的付费投递继续关闭。
- 运行端可把 `dsh_life_task` 置为 `null` 停止接线。保留现有活动库与鱼账户下的 DSH 数据可供复查。

## 自主文件研究结案

原版目标完成入口现在也认可已经完成写回的自主 DSH 报告。它从已保存的目标检查点找到同一作用域、同一目标的报告经历，核对任务、会话、产物路径及用量。回归测试覆盖写回中断、检查点中的报告不一致和合法结案。活动结束时继续按原版流程暂停目标，之后可由目标完成入口结案。已纳入新版安装包，2026-11-13 23:59 已在 ThinkBook 上线，AstrBot 重启后 OneBot 重连。回退文件在 ThinkBook 的 `%LOCALAPPDATA%\FishHome\rollback\dsh-goal-20261113-235925`。上线前活动库尚无正式 DSH 报告经历，首次自然任务结案待观察。
