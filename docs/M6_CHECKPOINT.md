# 台式机试运行检查点 · 2026-11-09

台式机 `D:\Share\code\FishHome\dev\Start-FishHome.cmd` 是一键入口。托盘控制台已经运行，9 个组件在线，重复启动保留原服务进程。AstrBot 4.29.4、NapCat 4.18.38、DSH 0.2.2-rc.1 和 Ollama 0.40.15 继续使用既有安装。ThinkBook 恢复后再做部署和数据迁移验收。

## 本机验收

- 控制台 14 项自检通过。QQ OneBot 已重新连接，OneBot 入口补丁保留，AstrBot 关闭管理员私聊白名单豁免。现有白名单只含主人私聊。
- DSH Bridge 的认证 RPC 可以读取会话列表。PhoneMCP 通过 SSE 列出雷电 `emulator-5554`。`bge-m3` 在 Ollama 生成 1024 维向量，模型驻留 GPU 显存。房间与记忆星海连接真实本地数据源。
- 活动插件更新到仓库版本。B 站伴侣已纳入一键启动，本机 MCP 握手通过，以只读模式运行。自主活动、计划触发和公共写入保持关闭。本机计费两本账各剩 ¥5，预留为 ¥0。这轮探针使用本地接口，收费回复留给日常试跑。
- 试跑开始后，管理员确认已测试 QQ 新会话和额度查询。同一预算日的本机账本随后核对到 1 笔已结算的交互请求；自主账本当时没有已收费请求。
- 真实浏览器验证了服务状态、自检、余额、设置入口、重复一键启动和窄屏布局。另在台式机临时目录实跑在线归档、校验、隔离恢复、只读导出和房间缓存。

## 记忆归档与回滚

- `D:\Share\code\FishHome\backups\desktop-test-memory-20261109T061711Z.zip` 是本次清理前的原生记忆归档。隔离还原验证有 1 条测试记忆、4 个记忆原子。新建的长期库中，文档、原子和关系均为 0。
- 清理范围为 LivingMemory 长期记忆、图谱库、索引和衰减状态。AstrBot 历史对话及 LivingMemory 对话缓存保留。要以空白聊天上下文开始试用，可在管理员 QQ 私聊先发 `/new`。
- 使用 `fish.ps1 verify` 检查归档，用 `fish.ps1 restore -Apply` 还原到新目录。活动插件原源码保存在 `%LOCALAPPDATA%\FishHome\manager\activity-before-trial-20261109T063035272472Z`。AstrBot 原 `cmd_config.json` 以 DPAPI 保存于 `%LOCALAPPDATA%\FishHome\manager\desktop-trial-astrbot-before.bin`。回滚源码或配置前先停止 AstrBot。

## 试跑后续

试跑期间使用管理员私聊，观察日常回复、`/额度` 和房间刷新。M3 已验证真实受限 DSH 文件任务。M4 已验证单次受控投递、原版目标和摘要队列写回，实际进长期库和连续自主运行待验。M5 的交互及自主零额度拦截已实测，其他供应商和非文本付费通路仍需逐条核对。

ThinkBook 上线后核验 Windows OpenSSH 指纹、登录后启动、原生快照定时生成、台式机拉取与隔离恢复。房间要接一次正式机的只读快照，迁移默认 `none`，`selective` / `full` 仍待现场演练。最后做多日运行、断线恢复和运维验收。
