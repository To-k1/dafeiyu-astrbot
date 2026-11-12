# M5 · 人民币预算与公共写入 · 2026-11-09

本文保留当时按北京时间 04:00 换日的验收记录。现行实现和复验按北京时间 00:00 换日，见 [现行决策](DECISIONS.md)。

## 接线

- 本机 `127.0.0.1:6197` 提供 DeepSeek Flash 文本、显式 Search/Fetch 和余额入口。交互与自主各使用一个本机凭据，各默认 ¥5。管理员命令可临时或永久调额，当天已用和预留保持原值。临时额度在北京时间 04:00 失效。跨日返回的请求结算到发出时的预算日。
- 在现有 `activity.sqlite3` 增加 `fish_cost_limits` 和 `fish_cost_calls`。SQLite 原子检查余额并预留，随后才向上游转发。文本按最大上下文与最多 8,192 输出 Token 预留，按完整返回的输入、缓存和输出用量结算。未确认用量保留预留额。Search/Fetch 使用各自的固定单价，明确无结果的请求退回预留。
- DSH 已有任务检查点记录 `deferred` 和 `resume_after`。默认高峰阈值为 24,000 Token，大于阈值且来得及延期的文件任务留至下一低谷，管理员可再安排单次运行。较小任务与低谷前到期的任务可继续。周期活动只能查询已投递会话。
- `public_write_enabled` 默认为 `false`。B 站和抖音写入均须同时通过总开关、平台开关与原审批流程。B 站伴侣在发出 POST 前再次读取控制文件。抖音在提交评论或点击发布前重新检查。模型的手机通用工具仅提供定向滑动、打开应用、返回和等待，写入使用专用入口。

[DeepSeek 人民币价格与峰谷时段](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) · [Search/Fetch 计费规则](https://api-docs.deepseek.com/zh-cn/guides/search/) · [2026 年节假日公告](https://www.gov.cn/gongbao/2025/issue_12406/material/gwygb202532.pdf)

## 管理入口

在仓库根目录使用 `fish-home-dev` Python。`$activityDb` 指向运行端活动库。计费入口进程由管理员持有 `FISH_COST_UPSTREAM_KEY`，另设两个不同的本机凭据 `FISH_COST_INTERACTION_TOKEN` 和 `FISH_COST_AUTONOMOUS_TOKEN`，每个至少 24 字符。价格变更时更新 `deploy/fish_cost_prices.json` 或向 `serve` 提供 `--prices`。

```powershell
python -B -m deploy.fish_cost --db $activityDb serve
python -B -m deploy.fish_cost --db $activityDb balance
python -B -m deploy.fish_cost --db $activityDb adjust interaction 6 --temporary
python -B -m deploy.fish_cost --db $activityDb adjust autonomous 5
```

| 调用方 | 配置 |
| --- | --- |
| AstrBot 交互 | OpenAI Chat Completion，模型 `deepseek-flash`，API Base 为 `http://127.0.0.1:6197/openai/interaction/v1`，Key 用交互凭据。停用指向收费源站的回退提供商。 |
| AstrBot 自主 | 独立提供商使用 `http://127.0.0.1:6197/openai/autonomous/v1` 和自主凭据。把提供商 ID 写入活动 `control.json` 的 `autonomous_provider_id`。自主 runner 使用这一路并移除模型回退。 |
| DSH 自主 | 受控配置的 `DEEPSEEK_BASE_URL` 为 `http://127.0.0.1:6197/anthropic/autonomous`，`DEEPSEEK_API_KEY` 为自主凭据。该配置的 FishHomeRunner 进程只持有本机凭据。 |
| 管理员 QQ | 更新 DSH Bridge 插件后，将交互凭据传给 AstrBot 进程。原管理员私聊可用 `/额度` 查看两本账，交互余额低于默认请求预留时可收到提示。 |
| B 站伴侣 | 用 `service.py --write-control <管理员控制的 control.json>` 指向活动配置。省略时伴侣只提供读操作。总开关由管理员用 `ops.py --data-dir <活动数据目录> switch public_write_enabled on\|off` 管理。 |

## 验证与运行边界

- 本机临时环境用 DSH `0.2.2-rc.1` 和 AstrBot `4.29.4` 的实际 Provider 连接网关，后端使用回环模拟服务和假凭据。DSH 的标题请求和主请求分别过账；自主余额调为 ¥0 后，后续任务报错且模拟上游请求数保持不变。AstrBot 正常收到回复，交互余额调为 ¥0 后收到 HTTP 402，模拟上游同样没有新增请求。
- `test_fish_cost.py` 覆盖两种流式用量、Search/Fetch、并发预留、未知结果和 04:00 结算。B 站套件覆盖两层开关、伴侣拦截和高峰延期后重新安排。用 `python -B -m unittest discover -s tests -p <模式>` 分别运行 `test_bili_*.py`、`test_fish_cost.py`、`test_douyin_activity.py` 和 `test_phone_agent.py`，共 660 项通过、15 项跳过。Windows 运行回归需要 `PYTHONUTF8=1` 与 Git Bash 在 `PATH`。
- 台式机开发实例已切换 AstrBot 交互与 DSH 自主两条 Flash 路径。实测 DSH 的实际模型循环与 AstrBot 原生 Provider 在各自额度为 ¥0 时收到 HTTP 402，两本账的已用额与预留额均无新增。恢复原额度后，QQ OneBot 和 DSH Bridge 已重新连通。管理员 QQ `/额度` 已实收；本次真实实例验收范围为零额度拦截。
- 自主活动和公共写入保持关闭。实际覆盖范围限于流经计费入口的请求，其他供应商、未接入的搜索、记忆钩子及前台多媒体需要单独核对后再开放无人值守。任意 Shell、ADB 或浏览器操作仍须执行权限限制，公共写入总开关只约束已接入的执行路径。
- 回退时停止本机网关并把活动 `autonomous_provider_id` 置空，保留 SQLite 账本与任务检查点。价表保留已核验版本，后续年度更新 `cost_window.py` 的节假日表。
