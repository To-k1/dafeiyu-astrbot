# M4 接入断点 · 2026-11-09

M3 已收到管理员 QQ `/dsh ping` 的真实回执，见 [M3 验收记录](M3_ACCEPTANCE.md)。M4 已接通原版生活选择、DSH 会话恢复、目标检查点与原有记忆队列。真实 DSH 验证复用了 M3 已完成的会话，新增的文件任务仍待一次受控联调。

## 接线和数据位置

- 原版 LifePlanner 选中 `research` 目标并领取运行权后，才匹配本地 `control.json` 的 `dsh_life_task`。该配置默认为 `null`，包含 `goal_id`、DSH 工作目录 `cwd`、相对输入路径 `source` 和短指令 `instruction`。新任务只允许管理员安排的 `once_at` 单次活动启动，周期活动只可查已投递的会话。
- `activity.sqlite3` 的既有 `settings` 保存任务、请求、会话、预期产物和自主账本归属日。归属日按北京时间 04:00 切换，恢复时沿用首次记录。请求发送前先保存标识，状态查不清则进入 `needs_review`，后续只查询原会话。原版调度冷却仍适用。
- DSH 在 `FishHomeRunner` 私有工作区内写 Markdown 和 JSON。适配层确认终态与请求对应、读取路径与出处、取得 Token 用量后，写入既有 `episodes`、合并 `goals` 的检查点，并向原 `memories` 队列添加一条摘要。目标状态仍由原版管理。本轮验证到队列入库。

## 验证记录

- 本地集成测试使用原版 `run_activity`、临时活动库和模拟 DSH。覆盖周期活动拦截新投递、投递后断线、重开数据库后跨 04:00、服务离线时 `needs_review`、部分写回中断，以及最终只投递一次、只记录一份产物经历和一条记忆摘要。
- 在 Windows 的 `fish-home-dev` 环境启用 `PYTHONUTF8=1`，将 Git Bash 加入 `PATH`，运行 `python -B -m unittest discover -s tests -p 'test_bili_*.py'`。共 658 项，643 项通过，15 项跳过。
- 本机 DSH `0.2.2-rc.1` 使用原 M3 的 `FishHomeRunner` 标准账户、文件工具配置与 `127.0.0.1:59934`。实际读取原请求终态、两份产物、输入来源和 Token 用量，并由 M4 经原版生活入口写入临时目标与记忆队列。本次只开放查询调用，模型费用为零。
- 恢复本机 DSH 后，原 QQ Bridge 的签名凭据返回 HTTP 401。更新开发实例内的凭据并通过本机 Bridge 探针复验。既有 QQ 回执见 M3 记录。

## 接续和回滚

- 下一次在临时活动库创建样例研究目标，用既有只读 `sample.csv`、文件工具限定的 DSH 和管理员安排的一次运行验证真实 Flash 投递、产物和写回。现有 AstrBot 开发实例的活动与生活选择仍关闭，保持其既有 B 站单次验收前置条件。
- 本轮只提交仓库代码。运行端可把 `dsh_life_task` 置为 `null` 停止接线。保留现有活动库与鱼账户下的 DSH 数据可供复查。M5 负责收费请求的硬门控，当前仍禁止无人值守的付费投递。
