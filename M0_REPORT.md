# Fish Home M0 报告

2026-11-07 · Windows 台式机 · 源码审计与无凭据预检

## 基线与交付

- 上游 `whyever77/dafeiyu-astrbot` 的 `main` 固定在 [`b56a03c`](https://github.com/whyever77/dafeiyu-astrbot/tree/b56a03c054a51905650f3f64448690565c2e0a2a)。本地 `main` 与上游及 Fork 的 `main` 对齐。`origin` 为 [To-k1/dafeiyu-astrbot](https://github.com/To-k1/dafeiyu-astrbot)，本地交付分支为 `fish-home`。自动审批拦下了将项目文档推送至公开 Fork 的操作，待用户确认这批文档的公开范围。后续具体功能才建立 `feature/*`。
- 使用 `gh api` 确认当前账号与 Fork 来源，`gh repo fork --clone=false` 创建公开 Fork。完整历史下载停滞后，使用 `git fetch --depth=1 --no-tags upstream main` 和对应的 `origin` 拉取命令，保留当前可复现基线。若后续同步需要较老的共同祖先，再加深历史。
- v0.7 ZIP 保存在工作区本地。核对路径无冲突后引入其 `repo/` 下 22 个文档及模板，并合并本轮确认的调额、安卓和数据权限。源码复用判定及可定位符号见 [UPSTREAM_REUSE.md](docs/UPSTREAM_REUSE.md)。

## Windows 路线与组件版本

| 组件 | M0 证据与后续候选 |
|---|---|
| AstrBot | 官方 [4.29.4 发行版](https://github.com/AstrBotDevs/AstrBot/releases/tag/v4.29.4)，所审源码 [`86c24e8`](https://github.com/AstrBotDevs/AstrBot/tree/86c24e846c0de78830f6a0bc402049edb0f0c671) 的 `pyproject.toml` 标记 4.29.4。上游的 WSL pilot 实跑版本是 4.29.0。M1 走原版 Windows Launcher，现场记录 Launcher 和实际加载的 AstrBot build。 |
| NapCat / QQ | 候选为官方 [NapCat v4.18.38](https://github.com/NapNeko/NapCatQQ/releases/tag/v4.18.38) 的 `NapCat.Shell.Windows.Node.zip`，从根目录 `napcat.bat` 启动自包含模式。上游 `docs/部署指南.md` 中的 QQ `9.9.35-52892` 是注入模式成功记录。`deploy-kit/bootstrap.ps1` 只准备部署材料和依赖，AstrBot/NapCat 仍需要单独安装。 |
| LivingMemory | [`1c6a471`](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/tree/1c6a471c9af9ae45a4ccb3a8cec5695c917de50b) 的 `metadata.yaml` 标记 2.9.1。插件由 `StarTools.get_data_dir` 定位状态。原版房间过去核验的是更早的 LivingMemory 版本，M2 先用隔离库检查 2.9.1 的读者兼容性。 |
| 生活与社交 | 上游 B 站与抖音活动插件 `v3.0.3`。现有 Planner、Goals、Workshop、Projects 和联系账本继续作为业务权威。 |
| PhoneMCP | [phone-mcp 0.1.0 源码](https://github.com/kengerlwl/phoneMcp/tree/2f41018f1568001c2117704be246a02b6716bb4a)，与上游雷电 14 / ADB 方案一起作为 M2 候选。`start_phone_mcp.ps1` 默认回环，同机联调直接走本机入口。 |
| DSH / Bridge | [官方 npm 0.2.2-rc.1](https://www.npmjs.com/package/@deepseek-ai/dsh/v/0.2.2-rc.1) 与 [官方发行标签](https://github.com/deepseek-ai/deepseek-harness/tree/dsh-v0.2.2-rc.1) 对应审计点 `66491b0`。Bridge [1.0.0 / `bf49f1f`](https://github.com/Fantasality/astrbot_plugin_dsh_bridge/tree/bf49f1ffdca7be248d903ab03f2339c4924653cf)。两者的组合实测安排在 M3。 |

## 影响后续实施的发现

1. **DSH Bridge 与当前 Web 发行版存在鉴权差异。** Bridge `dsh_client.py` 的 `DshClient.call` 发送无认证的 `/api/<method>`，使用 `session.create/prompt/history/cancel` 并从事件提取文本。固定版 DSH 的 `packages/client/connection/src/rpc-host.ts` 校验 Host/Origin，并要求浏览器会话或 API 身份。Web 默认 `127.0.0.1:3080`，Bridge 自动发现优先 `59934`。改端口只能解决寻址。M3 先验证现有 Bridge 和合规认证路径，同机测试，保留鉴权。 [官方 Connection 实现](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.2-rc.1/packages/client/connection/src/rpc-host.ts)
2. **Headless 可优先验证受限文件任务。** 发行版 `packages/bundle/headless/cordis.patch.yml` 挂载任务、会话标识和 JSON 结果配置，没有浏览器运行时；基础 bundle 的 Search/Fetch 和交互浏览器是不同能力。`token-meter/src/turn-usage.ts` 从会话事件聚合输入、缓存、输出和重试用量。Bridge 的文本结果通路没有传出这些结构化字段，也没有证据表明它能阻断 DSH 的逐次收费。M3 先验证文件产物、会话续跑、超时/取消、用量和会话日志上传设置。 [官方 headless 配置](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.2-rc.1/packages/bundle/headless/cordis.patch.yml) · [官方 CLI](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.2-rc.1/apps/cli/reference/README.md)
3. **双账本在调用边界扩展。** 上游活动 `BudgetGuard.before_call` 已包裹生活代理的流式和非流式 `text_chat`，配额单位是调用次数、时长和分享。AstrBot `ProviderOpenAIOfficial` 走 SDK 的 `chat.completions.create`，并从响应提取 usage。M5 先验证真实请求前预留、结算、并发、重试与缓存字段，分别覆盖 AstrBot、DSH 和收费工具。交互与自主各 ¥5，04:00 切换，任务续跑继承来源。保留上游的任务内配额。硬上限和无人值守开放以实际调用链覆盖为门槛。 [AstrBot 供应商实现](https://github.com/AstrBotDevs/AstrBot/blob/86c24e846c0de78830f6a0bc402049edb0f0c671/astrbot/core/provider/sources/openai_source.py)
4. **房间复用前端，改远端数据入口。** `SSHMemorySource` 与 `SSHActivitySource` 通过远端 `python3 -` 执行读取器，默认使用 `/opt/dafeiyu/...`。两者的 SQLite 查询是只读，但记忆读者仍可投影记录 `text` 字段。M2 先在台式机使用隔离库跑 `/room` 和 `/`。M6 在 ThinkBook 生成脱敏状态快照，走受限 SSH 入口供台式机读取，沿用现有 UI 与同步状态。管理员的原生备份、原文拉取与房间使用独立访问通道。
5. **平台写入器与安卓原始点击要分别约束。** `BiliActivity.execute_interaction` 及 `interactions.py` 负责平台审批和限额，`PhoneAgent` 的包名与高风险文本检查后仍可发出坐标点击。公开写总开关应覆盖受信执行入口。B 站登录推荐流先人工在场联调，执行层验证点赞、关注、评论等不可绕行后才向自治开放。标准 Windows 用户与受限 ACL 用于隔离管理凭据。DSH RPC 保持回环。

### 官方人民币计费记录

2026-11-07 查阅官方人民币价表。`deepseek-flash` 对应 `DeepSeek-V4.1-Flash-1017`。按每百万 tokens，低谷/高峰分别为缓存命中输入 ¥0.02/¥0.04、未命中输入 ¥1/¥2、输出 ¥4/¥8。Search 和 Fetch 分别按每千次成功请求 ¥5 计。高峰为北京时间工作日 09:00–12:00 和 14:00–18:00，法定节假日适用官方低谷规则。04:00 是本项目账本边界。 [DeepSeek 人民币价表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

## 状态路径与三条读出路径

| 权威数据 | 代码定位与处理 |
|---|---|
| AstrBot 与 NapCat | AstrBot 的 `conversation_mgr.py` 与 `core/db/sqlite.py` 管理会话及状态。Windows Launcher 实例的实际数据根与库文件在 M1 核验。NapCat 实例配置位于 `config/onebot11_<QQ号>.json`，登录态及敏感配置排除于普通归档。 |
| LivingMemory | `<AstrBot 数据根>/plugin_data/astrbot_plugin_livingmemory/`。`BackupManager` 枚举 `livingmemory.db`、`livingmemory.index`、`livingmemory_graph_documents.db`、`livingmemory_graph.index`、`conversations.db`、`decay_state.json` 及 WAL/SHM。它按版本变更逐文件 `copy2`，可作升级前保护，定时一致性归档需要协调停写或采用 SQLite 快照。涉及索引及多个数据库时以同一逻辑恢复点为准。 |
| 生活与项目 | `<AstrBot 数据根>/plugin_data/astrbot_plugin_bili_activity/activity.sqlite3`。`Store` 与 `LifeState` 持有目标、事件与检查点，`Workshop`、`Projects`、`ContactLedger` 在同一库内扩展。DSH 只保存外部执行所缺字段和独立成果文件。 |
| DSH | 使用独立 Windows 执行工作区。会话目录与状态从实际 `DSH_HOME` 和固定 profile 解析，M3 记录实际路径。备份关注检查点、成果和必要会话引用，凭据与 `.env` 单独管理。 |

1. **恢复归档**：ThinkBook 保持唯一正式活跃状态，生成一致性原生快照后由台式机管理员拉取。若一个原生库包含私聊原文，先识别范围与访问权限，将含原文的副本归入管理员按需或单独启用的同步路径，报告默认归档覆盖范围。
2. **可读导出**：从实际记录、时间、归属、来源 ID 和项目关系生成 JSONL/Markdown。私聊原文走管理员专用目录。
3. **房间快照**：只传脱敏摘要、来源标识及观测所需的项目/活动。生产中断时保留来源与同步时间。台式机开发库、正式备份和房间缓存分别存放。

## 实测与下一阶段

- 台式机 `FISH_HOME` 已就绪。Windows build `26200.9711`、Python 3.12.7、Node 24.21.0；Git、Windows OpenSSH 和 GitHub CLI 可用。`fishbook` 尚未配置，ThinkBook 原生安装路径及主机指纹留到实机配置时记录。
- `python -B deploy-kit/preflight.py --with-bili --json` 返回 `ok: true`，6199、6185、6099 可用。宿主 Python 的 `mcp`、`dashscope`、`imageio-ffmpeg` 与 `ffmpeg` 缺失，对应功能联调时在隔离运行环境补齐。
- `python -B -m unittest discover -s tests -p <文件名>`：房间 27 项、活动预算 18 项、代理预算 9 项通过，合计 54 项。首次受限环境阻止回环和临时目录，复跑使用授权的本机权限。`test_phone_agent.py` 因缺 `mcp` 未加载，记为 M2 待测。以上均为无凭据证据。
- **M1 最小范围**：固定 Windows Launcher、AstrBot、NapCat、LivingMemory 的实际 build 与数据路径，隔离 dev 启动；优先复用 `deploy-kit/preflight.py`，按需加薄 `doctor/status`；以模拟 SQLite 做一次一致性快照、校验及空目录恢复。真实 QQ、安卓、房间数据、DSH 和预算联调按后续阶段推进。
- **回退**：上游基线在 `main`，本次仅在 `fish-home` 增加文档和模板。可切回 `main` 查看原版；若需撤回已推送文档，使用常规 `git revert`，保留历史。原 ZIP 保存在工作区。M0 未部署运行组件或接触生产数据。
