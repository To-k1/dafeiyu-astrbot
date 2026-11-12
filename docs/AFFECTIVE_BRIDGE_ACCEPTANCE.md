# FishAffectBridge 接入与隔离验收

2026-11-10。接入分支为 `feature/fish-affect-bridge`。以台式机 v2.3.1 试跑分支合并本地 `fish-home`，保留台式机的控制台、原样人格与北京时间 00:00 双账本。原作者 Affective Core v0.1.0 与合并版完全一致。

这是首次接入记录。验收后的产品决议、消息路径分类与新增验证见 [v1.1 增量验收](AFFECTIVE_BRIDGE_V11.md)。

## 已接入

- `plugin/astrbot_plugin_dafeiyu_affect_bridge` 是独立 AstrBot Star，动态使用 `affective_bridge_v1`。主人真实 OneBot 好友消息才可附上一段可选背景，合成后台、通知、访客不经过此注入。停 Bridge 或接口版本不符时，原动力学与原消费者继续可用。
- `df_interest` 已接通明确正负评价 → 现有活动证据 → 原版事件 → 原库真实状态。省略评价仍走既有兴趣记录，但无额外心境脉冲。重复来源用活动库现有 `settings` 认领；不新增心境表或记忆库。投递不确定时保留认领，避免自动重复写入。已接的提议、前台抢占事件仍由原生产者处理。
- ContactLedger 在预留和派送均检查暂停，并保留原有留白、冷却、每日和未回复限制。主人好友私聊的「今天别找我」在上海零点到期，「暂时别主动找我」需「恢复主动联系」或「可以主动找我了」。普通来信不解禁。新集成配置要求统一协调时，缺少票据或协调器会阻止发送；已尝试且结果不明时不切换通道重发。B 站后台登录与风控通知使用同一发送入口。
- 小房间沿用合并版 SQLite 只读投影并标明「最后保存的心境」。LifePlanner、联系与可用表达通道继续读原 profile。TTS 和表情实机依赖单独核对。

## 验证与证据

| 验证 | 结果与边界 |
| --- | --- |
| 原版心境基线 | `python -B -m unittest test_affective_core test_affective_integration test_affective_appraisal_merge -q`，49 项通过。 |
| 定向联调 | `python -B -m unittest test_memory_room test_fish_affect_bridge test_bili_contact test_bili_contact_bridge test_affective_integration -q`，87 项通过。覆盖原版 SQLite 的实际增量、重开库后不重复写、投递不确定不重放、暂停和分段、房间无已保存心境时不显示默认值。 |
| 仓库全量 | 设置 `PYTHONUTF8=1`、`PYTHONPATH=tests`，Git Bash 在 PATH。 `python -B -m unittest discover -s tests -p test_*.py -q`。共 914 项，894 项通过、20 项跳过，退出码 0。最后加的房间空状态识别已纳入上面的定向复跑。 |
| 原生 AstrBot 4.29.4 | 临时根、真实三个插件、独立原生 SQLite、假 OneBot、回环替身模型。在真实请求捕获中 v2.3.1 与 Bridge 背景各 1 段、原版 memo 0 段；`/new`、重启仍成立，陌生与群事件未新增请求。原生场景预置临时库中的活动脉冲；实际 `df_interest` 写入由上面的定向联调验证。 |
| 运行边界 | 未启动真实 QQ、未用付费模型、未写实际 LivingMemory，未修改台式机服务目录和 ThinkBook。付费自主调度、原生 Windows TTS/表情以及真实 QQ 主动回执仍待目标机验证。 |

另在临时目录用上一版社区主动联系补丁验证了升级、原文件备份和重复安装。Python 源码及房间 JavaScript 语法检查通过。

## 同批启用与回滚

台式机有授权切换时，先停 AstrBot，在仓库运行 `powershell -File deploy/desktop_manager/install.ps1 -InstallActivity -InstallAffectiveBridge`。该命令安装合并版活动与两个心境插件，备份旧插件源码。原有运行数据目录不参与覆盖。若存在旧 EmotionAI，先由管理员核对停用及其数据归属。ThinkBook 使用同一批源码及插件配置，在实际部署根复核后安装。社区主动聊天需已有匹配版本，按 [联系补丁安装说明](../deploy/patches/proactive_chat/README.md) 做只读检查、停机安装与版本验收。

正式启用在同一轮配置以下几项。配置路径均相对于 AstrBot 运行根，QQ 号与凭据只填运行端。

| 路径 | 配置 |
| --- | --- |
| `data/config/astrbot_plugin_affective_core_config.json` | 显式设置同一 `owner_scope` 与 `owner_id`，`enabled=true`、`inject_prompt_memo=false`。首次完整数据链路可用 `appraisal_mode=math_only`，保留原动力学。 |
| `data/config/astrbot_plugin_dafeiyu_affect_bridge_config.json` | 显式设置同一主人，`chat_background_enabled=true`、`life_events_enabled=true`。 |
| `data/plugin_data/astrbot_plugin_bili_activity/control.json` | 启用统一主动联系时同时设置 `contact_coordination_enabled=true`、`contact_coordination_required=true`、`dnd_ranges=[]`。活动、周期和生活的授权按既有启动闸门配置，先验收实机自主 provider 确实走本机自主人民币入口。公开写入保持关闭。 |
| 现有 QQ 与观察组件 | 保持唯一主人好友私聊，核对原版房间按指定 scope 只读快照。表情和语音只开启目标机已支持的消费者，缺失时不阻塞其余路径。 |

回滚先关闭主动发送与自主调度。停用 Bridge 后可单独恢复原 memo，恢复安装备份中的插件代码与发布前配置。保留 `affective.db`、活动库及暂停记录。心境核心的 `enabled=false` 单独使用时不等于停 Bridge 或隐藏历史快照。

## 仍待决定或验证

- **方案 B 部分实现。** 原版仍只接固定正负脉冲，混合评价、强度和自由文字留在业务记录。跨库没有原子事件 ID 去重。认领后异常退出可能少一次脉冲，已知不确定记录不自动重放。来源判断以已核实的观察记录为粒度；同一视频在真正的新观察中可形成新事件。
- **牵挂计数。** 原版可能记录已提示，即使 memo 关闭。Bridge 没有用该字段确认实际发送，也没有将牵挂另行注入或发信。若需要准确区分挑选、注入和送达，需要原版回执接口。
- **暂停例外。** 暂无提醒或紧急回报的通用例外，明确受托的提醒如何在暂停时处理仍待定。
- **上游后备分支。** 原版缺消息 ID 的后备代码引用了未导入的 `hashlib` 和 `uuid`，真实适配器缺 ID 行为待验证。本次原生 OneBot 验收使用真实消息结构和消息 ID，截图配文已明确标为后台合成事件。
