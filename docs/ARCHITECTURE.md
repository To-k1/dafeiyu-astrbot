# Fish Home · 双 Windows 机器与薄扩展架构 v0.7

> 设计合同；待 M0 锁定源码与版本。不要把文中模块名/数据格式误作已存在的上游 API。

## 1. 角色拓扑

```text
                        原作者 upstream/main
                              │ fetch / review
                 用户公开 Fork origin
                              │
台式机 Windows               fish-home / feature/*
FISH_HOME/repo ───────────────┘      ├─ Codex、GitHub、PRD、版本清单、部署工具
  ├─ 本地开发/dev 鱼（测试时）       ├─ 原版小房间 / 记忆星海，本机 localhost 页面
  ├─ backups（校验过的正式档案）    └─ exports（可读 JSONL / Markdown）
  └─ 通过 SSH fishbook 管理/读取   ⇅ LAN SSH / SFTP / 本机端口转发
                         ThinkBook Windows 11 (16GB)
                           ├─ 受控管理员：安装/更新/备份
                           ├─ 鱼的独立标准用户：AstrBot / NapCat / LivingMemory
                           ├─ 上游生活系统 Goals / LifePlanner / Workshop
                           ├─ DSH + Bridge + Fish Extensions（薄扩展）
                           ├─ Windows 安卓模拟器 + PhoneMCP / ADB
                           └─ prod live DB / 项目文件 / 研究成果 / 任务账本
```

VMware Ubuntu 24.04 是现有的**可选** Linux 实验环境，不参与日常生产和强制迁移；WSL2 不必安装。

## 2. 上游拥有生活状态，Fish Extensions 仅补接缝

- **原版 LifePlanner/Goals/Workshop** 负责兴趣、目标、活动选择、跨日项目、作品/来源，不创建第二个 Planner、第二套记忆、第二套生活队列。若现成事件/表可保存 DSH 的续篇状态就复用。
- **LivingMemory** 是长期记忆源；原版记忆星海和小房间在**台式机本地运行**，通过只读一致性数据适配器观测，而不重新制作 UI。
- **Fish Extensions** 可拆为 AstrBot 内插件/adapter 和一个按需要的后台执行小 Worker：负责将原版任务提交到 DSH、限制可用的工具和文件、保存 DSH 外部 job/session/attempt/checkpoint 与 source/result 文件、请求前成本策略（若有 hook）和 usage 汇总。
- **DSH** 实际执行受控 Shell/文件/浏览器操作，默认 Flash；Bridge 与官方 headless/SDK 使用哪个需要固定版本实测，不能同时开发两个平行 DSH 系统。
- **安卓** 是原版 PhoneMCP/ADB 的接口消费者；模拟器部署在台式机 dev 以及 ThinkBook prod。先人工在场核验登录会话、视频推荐、声音、截图、ADB 和网络恢复，验证写入限制后开放登录态自主浏览。

## 3. 任务与费用

逻辑任务键 `job_id / goal_ref / origin(dev|prod, autonomous|interactive) / state / attempt_id / checkpoint_ref / cost_estimate / cost_actual / next_eligible_time` 是**拟议字段**，仅当上游不存在时才增加。

- `ready → running → done`，异常 `deferred / paused / failed / needs_review`；执行中断后检查外部副作用，不盲重放。
- 自主和交互账本各默认 **CNY ¥5/预算日**，北京时间 04:00 切换，管理员分账调额。DeepSeek Flash 输入缓存命中/未命中、输出 tokens 与独立工具费用分项统计，价目可更新。
- 峰谷规则限制**资源**，不规定兴趣和活动类别；昂贵且不紧急的步骤即使预算有余，也倾向低谷，低谷不强制开工。
- 若无法在真实 DSH/AstrBot 请求发出前做强制阻断，则标记**软限制**，不启用无人值守有费用活动；模型不自动切 Pro。

## 4. 权限、网络与公网写入

- 开发 Codex/SSH 部署管理员与鱼的 Windows 标准账户分离。鱼有自己 Shell、浏览器、App 和文件目录，但不得成为 Windows 管理员，不能访问 Codex GH/SSH 私钥、用户私人文件或管理网络服务。必须用 ACL/进程权限真实测试，不能只靠人格 Prompt。
- `public_write_enabled: false` 是**拟议**总开关，默认关闭。开启后只允许平台自己的审批、限额、目标范围允许的写入；原版 B站写入器优先复用。私聊 QQ 和 GitHub push 独立授权。
- 任意 Shell + 互联网可能构造 HTTP POST，意味着工具列表开关不等于强网络隔离。需要高保证时评估凭据隔离、平台 token、网络策略/容器，不实现前应以“软件策略/授权边界”标识并限制无人值守公共发布。
- DSH Bridge 可能使用无鉴权 loopback RPC；不得将其暴露 LAN。台式机看管理 UI 走 OpenSSH 本地端口转发，不在网络公开管理口。

## 5. 数据、一致性和运维

- prod live DB 唯一在 ThinkBook；dev 数据与 prod 隔离。Git 只管理源码，台式机存备份/导出、房间只读缓存。
- 迁移选择 `none/selective/full` 与 QQ 身份 `shared/separate` 独立，旧 QQ 实例必须停止后再启新。`none` 不得擦掉目标原有数据。
- ThinkBook Windows 计划任务执行本地一致性备份；台式机通过 SSH 拉回已完成归档，并可从本地房间读取只读快照。失败应写日志并更新状态，不将网络中断当作备份成功。
- 接电、禁系统睡眠、登录 Windows 后自动恢复 GUI/App；用户选择不实现无人登录 GUI 恢复。16GB 的 ThinkBook 需要真实测量模拟器与后台服务占用，不能写死 6GB 预算。

## 6. 与原版已有能力的审计决策

先看 `docs/UPSTREAM_REUSE.md` 和 `docs/ROOM_AND_REMOTE_VIEW.md`。同一功能先做现有源码证据 → 原配置 → 薄接口适配 → 最小补丁 → 最后才做独立新模块。**没有源码证据时结论只能是“待验证”**。
