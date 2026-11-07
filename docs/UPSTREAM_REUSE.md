# 上游能力复用审计

M0 源码基线、第三方版本及 Windows 本机证据见 [M0_REPORT.md](../M0_REPORT.md)。以下结论区分源码、无凭据测试和实际集成。

| 能力 | 固定版本源码证据 | 判定与验收 |
|---|---|---|
| Windows 部署、QQ、人格 | `deploy-kit/bootstrap.ps1` 与 `preflight.py` 负责准备和诊断，`extract_persona_prompt` 读原版人格。AstrBot `aiocqhttp_platform_adapter.py` 配置反向 WS，NapCat 自包含包为 Windows 候选。 | 复用部署材料和 OneBot。M1 固定实际安装版本并验证隔离启动，M2 用单一活动 QQ 实例联调。 |
| 生活选择和持久状态 | `main.py` 用 `life_enabled`、`life_decision_enabled`、`workshop_enabled` 等保护入口；`planner.py::LifePlanner` 决策，`life.py::LifeState.update_goal` 保存 checkpoint，启动恢复将 `running` 目标转回 `paused`。 | 启用原系统，使用既有暂停及项目恢复状态。M4 仅补外部 DSH 执行标识、确认过的副作用与最小去重状态。 |
| 研究、项目、联系 | `workshop.py::Workshop` 管理作品及版本，`projects.py::Projects` 管理项目、阶段与回看，`contact.py::ContactLedger` 记录发送尝试与用户活动；都使用 `core.py::Store` 的 `activity.sqlite3`。 | 复用源数据和联系协调。隔离实例逐项启用，关闭固定日夜作息实验。 |
| LivingMemory | 插件 `main.py` 用 `StarTools.get_data_dir`，`storage/schema.py` 管理表，`core/managers/backup_manager.py::BackupManager` 按版本复制 DB、索引与状态。 | 复用原生库。迁移前保留原版机制，运行态归档另做一致性协调和隔离恢复。 |
| 房间与星海 | `deploy/memory_observatory/server.py::ObservatoryServer` 限定回环；`livingmemory.py::SSHMemorySource` 使用远端 `python3 -`；两个 `*_reader.py` 用只读 SQLite 与 Linux 默认路径。 | 前端直接复用。替换 Windows 远端数据入口，保留原版路由和同步 UI，快照中筛去原文。27 项本机离线测试通过。 |
| Android 推荐流、公共写入 | `deploy/phone_mcp/start_phone_mcp.ps1` 使用回环 PhoneMCP 和模拟器 ADB；`phone_agent.py::PhoneAgent` 仍能坐标点击；`main.py::execute_interaction` 保留受信平台审批与远端账本。 | 复用模拟器、PhoneMCP 和平台写入器，开发期先人工在场。总开关覆盖受信写入口，并验证 ADB、Shell/HTTP 绕行。当前 `mcp` 缺失，PhoneMCP 测试留到 M2。 |
| DSH 文件与浏览 | Bridge `dsh_client.py::DshClient.run_prompt` 创建、提交、轮询、超时取消；官方 Web `rpc-host.ts` 要求身份校验，headless `cordis.patch.yml` 使用独立会话配置。 | 固定版 Web/Bridge 鉴权差异待 M3 联调。优先验证 headless 文件任务，浏览器执行单独验证，优先复用已有执行入口。 |
| 费用与峰谷 | `agent_budget.py::BudgetGuard`、`budget.py::Budget` 预先扣调用/时长配额；AstrBot `openai_source.py` 提取 token usage；DSH `token-meter/src/turn-usage.ts` 聚合事件。 | 原有额度保留。M5 实现交互与自主独立 CNY 账本、04:00 边界及峰谷延期。待实测每条付费通路的调用前预留和结算。原版预算相关 27 项测试通过。 |
| Windows 备份与发布 | `deploy/backup.sh` 依赖 Linux 容器停写并归档，LivingMemory 原生备份面向版本变化。 | Windows 需要调用原生快照、隔离恢复及 PowerShell/SSH 传输的薄封装。M1 先用模拟数据，M6 核验 ThinkBook 三条独立读出路径。 |
