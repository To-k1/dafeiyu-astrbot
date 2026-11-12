# FishAffectBridge v1.1 增量验收

2026-11-10。基线为本地 `feature/fish-affect-bridge` 的 v1.0 验收提交 `4593678`。原作者 Affective Core v0.1.0、v2.3.1 人格及当前安装保持原样。本轮用临时数据和替身通道验证。没有修改台式机或 ThinkBook 的运行实例。

## 现有消息来源与分类

| 来源 | 已有证据与实际发送路径 | 社交暂停、结果与本轮处理 |
| --- | --- | --- |
| 社区主动聊天和问候 | `deploy/patches/proactive_chat/contact_bridge.py` 从实际主人 UMO 取得 ContactLedger 票据，`sender_service.py` 在每次派送前重新核验。没有用户任务接受记录。 | 普通主动联系。暂停在预检、预留、派送有效，仍使用原弹性冷却、额度和未回复限制。社区发送器成功仅记录 `reported`，没有独立 QQ 消息 ID。结果不明不换通道。 |
| B 站后台分享、作品、互动提议、登录失效和风控通知 | `plugin/astrbot_plugin_bili_activity/main.py:1318–1357` 的 `contact_send`、`contact_notice` 使用 `df_run_id` 和已有投递键。合成后台事件、作品 ID、提议 ID 或活动状态不能证明主人委托。 | 普通主动路径仍受暂停。已接入的严格协调模式检查票据与独立限制。OneBot `message_id` 用于确定送达记录，缺回执不记为已发送。正文写「这是受托任务」没有作用。主人当轮发起的回复仍沿现有直达路径，不消耗普通主动轮次。 |
| DSH / LifePlanner 文件任务、结果和异常 | `deploy/dsh_bridge/m3_qq.py:90–116` 的 QQ 命令仅支持 `/dsh ping`。`plugin/astrbot_plugin_bili_activity/dsh_task.py:68–163` 有管理员计划、目标租约、DSH 请求/会话 ID、已核实的终态、产物和用量。 | 现有计划明确归类自主，完成写回目标和记忆队列，异常留在 `needs_review`。没有发现可追溯的主人委托接受记录、约定接收者和后台 QQ 投递入口。完成与回写均不能当作送达。本轮未新增豁免或自动发送。 |
| 原生定时提醒 | AstrBot 4.29.4 `core/tools/cron_tools.py:133–164` 保存创建会话、sender、note 和任务。 `core/cron/manager.py:437–471,503–529,580–610` 依任务唤醒，并在配置了目标时挂载 `send_message_to_user`。项目 `schedule_awareness` 读取任务用于上下文。 | 原生 Cron 没有走 ContactLedger，社交暂停不会拦这条原路径。原生任务状态和发送工具的成功返回不能替代可核对的 QQ 消息 ID。本轮只读查到开发实例提醒工具关闭、已存任务 0 条，没有重新启用或假造既有提醒。目标机实际任务和真实送达待核验。 |

对未来确实需要的后台受托报告，最小接线位置是现有任务接受和完成适配层。只有能追溯到主人委托、已接受承诺和目标 UMO 的必要结果或实质异常才走任务交付；沿用既有任务 ID、撤销检查、发送权限与回执。当前没有可直接复用的完整业务链路，因此本轮没有通过消息正文、普通活动标签或计划 ID 放行。

## 四项决议

1. **混合评价已修正，方案 B 仍是部分实现。** `df_interest` 省略方向时保存原文和观察来源，极性保持空。相同评价重试复用原记录，不同评价可保留各自原文。LifePlanner 和候选归纳跳过明确标为隐式的旧默认正值。没有迁移已写入的历史，也不猜旧版缺少标志的行如何产生。明确正负评价仍通过原桥写入唯一的心境库。
2. **牵挂无需修改外围实现。** 房间投影读取活跃事项和来源，不输出 `prompt_count`；Bridge 不用它生成发送史。原版 `prompt_count` 是内部选择/提示计数，即使原 memo 关闭也可能变化，不能证明已说、已送达或已读。
3. **社交暂停保留原限制。** 新增已确认的「先不要主动聊天」作为无期限社交暂停。普通来信不解禁，带任务字样的后台分享也不能绕过。未修改 ContactLedger、原生 Cron 或 DSH 任务适配层。可直接复用的既有原生提醒保留独立入口，DSH 受托后台送达仍缺接线。任务撤销按其自己的授权状态处理。
4. **消息 ID 增量补丁已准备，运行实例待安装。** 已装版在管理员身份过滤后执行 `str(event.message_id)`，缺字段可报错，None 可被变为非空的 `"None"`。仓库新增的 OneBot 补丁在转换和原始日志前拦截异常输入，合法带符号十进制 ID 原样传给转换器，不伪造 ID。用普通事件对象复现原 Affective Core 无 ID 且有/无时间戳时分别报 `NameError: hashlib` 和 `NameError: uuid`。建议上游分别补上两个导入并新增不使用 MagicMock 隐式属性的测试。本轮未改原作者文件。

## 修改与验证

- B 站 `main.py`、`life.py`、`memory_policy.py` 处理混合评价与暂停命令，保持现有心境和联系账本的写入边界。
- [OneBot ID 增量补丁](../deploy/patches/astrbot_4.29.4_qq_message_id.patch) 与 [部署及回滚命令](../scripts/README.md#m2-管理员私聊入口) 提供独立应用与撤回。需在已应用管理员入口补丁的 AstrBot 4.29.4 上停机应用。旧入口补丁可保留。
- 定向 `python -B -m unittest test_qq_message_id_ingress test_fish_affect_bridge test_bili_contact test_bili_contact_bridge test_bili_memory_policy test_bili_life_integration test_memory_room test_affective_core -q`，158 项通过。覆盖中性原文、旧隐式记录、明确评价真实增量、社交暂停、伪装任务、主人直接回复、只读投影和原版回归。
- 全量 `python -B -m unittest discover -s tests -p test_*.py -q`，918 项中 898 通过、20 跳过。固定 AstrBot 测试环境设 `PYTHONPATH=tests`、`PYTHONUTF8=1`，Git Bash 加入 PATH。
- 另用临时副本中的补丁、原生 aiocqhttp `Event` 和原生文本转换器验证正常及异常 ID。有效 ID 保留，缺 ID、None、空值和布尔值拦截，未连接真实 QQ。目标机还需核对补丁应用、实际 NapCat 消息形态及真实任务回执。

回退时撤回这次 B 站代码及 OneBot 增量补丁即可。无需覆盖心境库、清理现有评价、修改人格或恢复额外记忆写入。既有正负脉冲及暂停记录保留。
