# DS-Bot 源码核查

核查对象为 [DS-Bot 的固定版本](https://github.com/Fei2411/DS-Bot/tree/5e9b7da8882da3b68d8a0624342708c2150a9156)。此次静态审查用于第 3、4 项选型。

| 能力 | DS-Bot 源码与 FishHome 接入判断 |
| --- | --- |
| 身份、人格、持续对话 | Bot 有稳定 ID、人格指令、工作目录和独立 Session。复用其职责划分即可，正式身份和日常聊天继续使用已通过重启实测的 FishHome 名册及现有 Session。DS-Bot 自带 Bot、群与团队记忆，接入时会和既有状态重叠。 [名册](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/roster.js#L192) · [身份与会话映射](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/store.js#L482) · [记忆](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/memory.js#L1077) |
| 具名派活 | 定向消息、request_id 去重、阶段任务与证据都有实现。要接既有工作指南、受控项目目录、产物收据和 Go 预算，还需适配。群目标已经包含与 FishHome 生活项目重复的状态。 [定向消息](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/tools.js#L253) · [群目标](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/goals.js#L671) |
| 组会 | 群内多 Bot 可并行响应，记录按序读，群批次能恢复。可采用独立参加者会话、追加式记录及保存轮次游标。其恢复提示让模型自行检查并重试，执行层没有据此确认外部副作用恰好一次。FishHome 继续把已有执行证据却无终态的工作列为待核对。 [并行响应](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/groups.js#L169) · [群批次](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/groups.js#L558) · [恢复提示](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/resume.js#L24) |
| Windows 与当前 DSH | 桌面版固定 DSH 0.2.2-rc.1，与 ThinkBook 现用版本一致。桌面宿主使用独立 profile 和 DSH_HOME。仓库的插件配置会选择整个 profile 的搜索 provider。现有 Go 登录、网关计费和受控预设能否共用尚无实测证据。可在无正式凭据的独立 profile 验证适配。 [宿主版本](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/app/package.json#L28) · [启动](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/app/src/host.mjs#L1) · [搜索配置](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/cordis.patch.yml#L8) |
| 状态维护 | 写锁和原子替换值得参考。插件有写权限的 `state` 请求会初始化缺失的 Main Bot。启用现成管理入口前，应核对这一生命周期与大肥鱼自行养成名册的流程，以及它是否会同时维护第二套群与目标。 [状态写入](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/store.js#L321) · [初始化入口](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/channel.js#L268) |

## 第 3、4 项的最小方案

1. **具名派活**继续使用 FishHome 的 `agent_id` 和已有队列。入队时绑定小弟、当次选择的指南及工作区项目，执行器创建独立任务 Session，结果带回现有证据账本。人格由身份提供，指南随任务加入。直接复用当前 DSH Session、取消、完成事件和 Go 计费链路。 [名册](../../plugin/astrbot_plugin_bili_activity/dsh_roster.py) · [任务账本](../../plugin/astrbot_plugin_bili_activity/commissioned.py)
2. **组会**在已有队列上增加会议 ID、发言记录和可恢复的轮次游标。实际调用各参与者，可并行获取独立意见。互动模式允许插入大肥鱼或主人发言，后台模式沿用按轮或时间的汇报和结束条件。先用隔离 profile 验证 DS-Bot 的群发送和读回接口能否只承担这一层；适配不能保住单一名册、权限和 Go 账本时，参考其群批次与记录机制实现薄层。 [群记录](https://github.com/Fei2411/DS-Bot/blob/5e9b7da8882da3b68d8a0624342708c2150a9156/ds-bot/src/host/groups.js#L1599)

此次核查未安装 DS-Bot，未迁移正式会话或变更小弟数据。
