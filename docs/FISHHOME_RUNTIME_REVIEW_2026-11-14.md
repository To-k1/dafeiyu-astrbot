# 大肥鱼独特能力运行核查

2026-11-14，北京时间。依据当日交接稿 A1–F3、PRD v0.9，以及正式机只读取证。台式机已打开既有 ThinkBook 远程托盘，页面和 SSH 转发后的已鉴权探针均返回 HTTP 200。本轮生产侧只读。

## 现状结论

ThinkBook 是唯一正式实例。检查时 AstrBot 4.29.4、OneBot、两路 DSH、媒体伴侣及房间端口均在监听，OneBot 已连接。正式安装根为 `D:\FishHome\FishHome-ThinkBook-OneClick-Repair-v2-2026-11-12`，源码在 `repo`，AstrBot 插件与库在 `dev\astrbot-4.29.4\data`。AstrBot 在 FishHomeOps 下，受控 DSH 以 FishHomeRunner 运行，NapCat 4.18.38 和 DSH 0.2.2-rc.1 版本沿用安装基线。台式机当时只有远程控制页和转发端口，未监听本机 AstrBot、NapCat 或房间端口。

台式机审查工作区为 `feature/fish-affect-bridge` 的 `74812ee`，有未提交差异。比较使用现有 `upstream/main` 的 `6427435`，公开分支本地引用 `origin/fish-home` 对应 `70e7e18`。正式机由安装包及后续增量维护，不能用以上任一提交标记所有已安装文件。

原作者提供了 LifePlanner、目标、项目、工坊、ContactLedger、Affective Core、房间与星海。我们的运行差异主要是 v2.3.1 主人会话、双人民币账本、Windows 双机运维、原心境的独立 Bridge、受托提醒与文件交付、自主 DSH 和可审计的候选及结果写回。正式记录已确认自主 B 站能分析视频画面，受托链能在社交暂停期间按约履约。优先缺口是专用电脑能力的可执行权限边界、跨日项目和工坊在正式机的接通、自主 DSH 的首次自然触发及通用峰谷成本偏好。

## 功能核验矩阵

层级按源码支持、已装、已开、可达、受控已测、运行观察区分；最右栏给出缺口。运行计数为检查时的脱敏读数。

| 编号 | 功能与来源 | 层级、证据与结论 |
| --- | --- | --- |
| A1 | 人格替换 · FORK_CHANGE / CONFIG_ONLY | **已生效。** 主人会话配置引用 v2.3.1，库中系统正文和本地 v2.3.1 文件一致，条目无开场示例。Bridge 注入与原 memo 开关已读回。本次未采集完整真实请求，LivingMemory 自动上下文与其他提示在每一轮的最终拼装仍需验证。请求接线见 [Bridge 请求钩子](../plugin/astrbot_plugin_dafeiyu_affect_bridge/main.py#L54)。 |
| A2 | 表达自由 · 原人格与 FORK_CHANGE | **非技术观察项。** 本轮关注的调度和 Bridge 路径未发现人格评分器、固定亲昵回合或产出 KPI。存在明确受托指令及能力提示。运行模型语气留给用户判断。 |
| A3 | 主人权限 · FORK_CHANGE | **已装、已开，主人正向链实测。** 单一 OneBot 适配器，白名单一项并与生活活动主人会话一致。好友私聊及有效消息 ID 在转换前拦截。群 @ 和表情反应未实机验收。见 [QQ 入口补丁](../deploy/patches/astrbot_4.29.4_qq_admin_ingress.patch)、[消息 ID 补丁](../deploy/patches/astrbot_4.29.4_qq_message_id.patch)。 |
| B1 | 定时与生活选择 · UPSTREAM / CONFIG_ONLY | **运行中已观察。** 生活决策、周期活动启用，固定节律关闭、免打扰为空。正式库 5 次完成，最新开始 11:08。可休息或继续。见 [调度入口](../plugin/astrbot_plugin_bili_activity/main.py#L1633)、[选择器](../plugin/astrbot_plugin_bili_activity/planner.py#L331)。 |
| B2 | 个人活动终点 · UPSTREAM / FORK_CHANGE | **生效。** 活动完成与 QQ 投递分别记账，库中 5 次完成、3 条生活侧送达；计数不用于推断哪一轮一定分享。可停留在已有事实、感想或下一步。见 [生活执行](../plugin/astrbot_plugin_bili_activity/main.py#L1834)、[联系出口](../plugin/astrbot_plugin_bili_activity/main.py#L1409)。 |
| B3 | Android、媒体与 DSH · UPSTREAM / FISH_EXTENSION | **部分接通。** 截图、应用操作、真实视频及音轨有验收；正式库 3 条带自主 run ID 的抽帧分析，时刻为 03:06、05:57、11:09。B 站扫码刚由用户确认，关注列表返回尚未另测。两种自主 DSH 文件路径有受控真实记录，正式库无计划或任务；未开放通用 Windows 或任意网页控制。见 [媒体入口](../plugin/astrbot_plugin_bili_activity/main.py#L2712)、[DSH 检查点](M4_CHECKPOINT.md)。 |
| B4 | 跨日目标、项目、工坊 · UPSTREAM / FISH_EXTENSION | **部分接通。** 目标与检查点持久化，正式安装有 DSH 结案修复。项目、工坊正式开关均关闭；Windows 工坊导出只在本地源码和安装包，未部署。重开临时账本的项目续跑实验通过，待观察正式自然跨日。见 [项目实现](../plugin/astrbot_plugin_bili_activity/projects.py)、[创作工坊](../plugin/astrbot_plugin_bili_activity/workshop.py)。 |
| B5 | 费用与公开写入 · FISH_EXTENSION / FORK_CHANGE | **部分接通。** DeepSeek Flash 走 6197 网关，今日交互和自主上限各 ¥5，无覆盖，已结算约 ¥0.5221、¥0.3384，查时无预留；00:00 换日。百炼语音另计。公共写入及抖音关闭。DSH 有按预估 24,000 Token 阈值择时，任务未给估算时使用 32,000；全体自主步骤的成本估算和低谷复评未实现。见 [计费网关](../deploy/fish_cost.py#L137)、[峰谷计算](../plugin/astrbot_plugin_bili_activity/cost_window.py#L20)、[DSH 延期](../plugin/astrbot_plugin_bili_activity/dsh_task.py#L150)。 |
| C1 | 唯一心境 · UPSTREAM / FISH_EXTENSION | **已装、已开，有正式写入。** Affective Core 0.1.0 配置为 `math_only`，Bridge 0.1.0 取版本化接口，发现一份活动心境库、一条状态、35 条事件。生活事件跨库不具原子性，不确定时不盲目重放。见 [Bridge 接口](../plugin/astrbot_plugin_dafeiyu_affect_bridge/main.py#L34)。 |
| C2 | 单一可选背景 · FISH_EXTENSION | **已开、入口可达、隔离已测。** 原 memo 关闭，Bridge 主人私聊背景开启，后台与其他会话过滤，核心不可用时略过。尚无本轮完整真实提示链证据。见 [背景注入](../plugin/astrbot_plugin_dafeiyu_affect_bridge/main.py#L54)。 |
| C3 | 事实与评价进入心境 · FORK_CHANGE / FISH_EXTENSION | **部分接通。** 来源需有同范围观察经历，明确正负才经 Bridge 送原核心，混合或未定保留文字，活动库有 5 条兴趣经历。现有正负及混合的边界经隔离测试，尚无本次生产逐条对账。见 [兴趣工具](../plugin/astrbot_plugin_bili_activity/main.py#L3694)、[增量验收](AFFECTIVE_BRIDGE_V11.md)。 |
| C4 | 心境消费者 · UPSTREAM / FORK_CHANGE | **部分接通。** LifePlanner 和联系闸门可读心境；表情读取按会话的画像，限制数量并可压制欢快标签，隔离已测。房间展示保存值。正式 Windows 百炼语音入口未接画像；当前效果不能由普通语音验收推定。见 [原版画像](../plugin/astrbot_plugin_affective_core/main.py#L155)、[联系闸门](../plugin/astrbot_plugin_bili_activity/contact.py#L89)、[表情](../plugin/astrbot_plugin_face_convert/main.py#L1095)。 |
| C5 | 牵挂及回执 · UPSTREAM / FORK_CHANGE | **已接通。** 房间读保存值，台式机摘要省略牵挂明细；联系送达用独立账本及 OneBot ID。牵挂内部提示计数未用作 QQ 发送证据。见 [心境投影](../deploy/memory_observatory/activity_reader.py#L144)、[跨机摘要](../scripts/m6_data.py#L30)。 |
| D1 | LivingMemory · UPSTREAM / FISH_EXTENSION | **已装，已实测。** 正式机 2.9.1，当前库 3 条文档、14 条原子；历史候选保存、纠正及 `/new` 召回有 QQ 与数据库证据。未查询私聊或记忆正文。见 [正式机运行记录](DESKTOP_MANAGER.md#L48)。 |
| D2 | 来源分层 · UPSTREAM / FORK_CHANGE | **部分接通。** 活动经历、主观解释、候选来源与持久记忆有不同记录；候选保存有明确入口。证据支持专用链，普通聊天自动归纳的所有来源标签未逐条验收。见 [归纳实现](../plugin/astrbot_plugin_bili_activity/memory_policy.py)、[记忆导出](../scripts/m6_data.py#L111)。 |
| D3 | 主人与角色偏好独立 · UPSTREAM / FORK_CHANGE | **数据模型可达；实机案例待验证。** 主人偏好记录独立于角色 `df_interest`，相反方向不会自动成为单向归纳。无需安排一次真实对话强制反驳。见 [主人偏好](../plugin/astrbot_plugin_bili_activity/life.py#L737)、[候选归纳](../plugin/astrbot_plugin_bili_activity/memory_policy.py#L196)。 |
| E1 | 主动联系账本 · UPSTREAM / FORK_CHANGE | **生活出口已生效。** 3 条生活侧 `sent`，日上限 3、未回复上限 2、留白 5 分钟、冷却 90 分钟。账本记录受控派送尝试，缺回执标为不确定。社区主动聊天未装，其多消息回合在正式机无可验收对象。见 [账本](../plugin/astrbot_plugin_bili_activity/contact.py)。 |
| E2 | 主动反馈 · UPSTREAM / FORK_CHANGE | **部分接通。** 主人私聊已有实际 owner event，最近 13:37；群 @ 和反应未开放、未实测。 |
| E3 | 心境与发送许可 · UPSTREAM / CONFIG_ONLY | **已开、隔离覆盖。** `contact_drive` 可缩短留白和冷却；最终派送复查暂停、收件人和额度。真实某次恰好 3:45 或 67:30 的时序未测。见 [派送前检查](../plugin/astrbot_plugin_bili_activity/contact.py#L167)。 |
| E4 | 暂停与受托交付 · FISH_EXTENSION | **已生效。** 本次正式库 2 条提醒送达、2 项委托已完成并送达；11 月 14 日的组合验收核对了两类 OneBot ID，暂停期间普通主动投递为零。任务有原消息与收件范围，结果不明不重发。见 [提醒](../plugin/astrbot_plugin_bili_activity/reminders.py#L65)、[委托](../plugin/astrbot_plugin_bili_activity/commissioned.py)、[实测](AFFECTIVE_BRIDGE_V11.md#L33)。 |
| E5 | 未回复边界 · UPSTREAM / FORK_CHANGE | **未发现新增强制升级表达。** 现有联系代码用未回复计数阻止投递；原核心保留想念与别扭动力学。没有观测到另装 EmotionAI 或社区主动聊天。 |
| F1 | 多模态 · UPSTREAM / FORK_CHANGE | **部分接通。** QQ 实测可播放百炼 `qwen-audio-3.1-tts-flash`、实机听写、旺柴表情和截图；正式视频抽帧分析有证据。表情心境实机效果待验，语音心境参数未接。见 [语音发送](../plugin/astrbot_plugin_bili_activity/main.py#L2197)、[实机记录](OWNER_WORK_ACCEPTANCE.md)。 |
| F2 | 原版房间与星海 · UPSTREAM / FORK_CHANGE | **部分接通。** ThinkBook 回环两页 HTTP 200，活动接口返回旧快照标记；台式机有跨机摘要缓存，但当时本机 8767 未监听。局域网手机直达仍未开放。见 [只读观测](../deploy/memory_observatory/server.py)、[跨机同步](../deploy/desktop_manager/thinkbook.py#L130)。 |
| F3 | 权威状态与双机运维 · FISH_EXTENSION | **部分接通。** 已实测远控、重启后恢复、自动普通备份拉回、可读导出与私有还原包隔离恢复。正式原路径覆盖、选择性及全量开发记忆迁移延后。见 [控制台](DESKTOP_MANAGER.md)、[恢复检查点](THINKBOOK_RECOVERY_CHECKPOINT.md)。 |

## 四条实际数据流

1. **自主生活**。定时器 → LifePlanner → 只读手机和 B 站媒体服务 → 活动库原始经历与可选评价 → 明确正负经 Bridge 入心境库，来源候选供 LivingMemory → 继续、休息或经联系闸门择机分享。已确认到自主视频实际抽帧并记录。DSH 支线在独立库用真实执行验证，正式自然进队尚无记录；工坊和项目支线的开关关闭。
2. **受托工作**。主人 OneBot 消息 → 委托接受记录和收件会话 → 受托 DSH 按文件模式生成带来源成果 → 核验产物及交互用量 → OneBot 回执写回任务。定时提醒走同样的主人收件身份。已确认社交暂停期间，两类结果各到达一次。
3. **主动联系**。生活活动发现可分享内容 → ContactLedger 按主人、心境驱动力、暂停、留白、冷却、未回复及日限判断 → OneBot 发送 → 有消息 ID 才登记送达 → 主人真实好友事件计入互动。正式可证 3 条生活侧送达；社区来源未安装。
4. **记忆与心境展示**。生活与私聊事实 → 分别进入活动 SQLite、LivingMemory 与 Affective Core → 只读投影 → ThinkBook 本地页面或 SSH 摘要 → 台式机房间及星海。确认 ThinkBook 页面可达和台式机缓存存在；当前页面新鲜度及本机房间进程须分别检查，手机入口未接。

## 最小候选与讨论

| 目标 | 可选改动、影响与回退 |
| --- | --- |
| 独立使用 ThinkBook 的浏览器或桌面 | 当前 AstrBot Computer Use 是 `none`。4.29.4 的 `local` 入口向聊天代理挂接 Shell、Python、文件读写与搜索，进程身份是 FishHomeOps；此入口本身不能证明能控制真实 Windows GUI。先按用途选现有文件专用 DSH 或独立受限浏览器/桌面，沿用 Runner 隔离方向。权限、网络及公开写入边界通过测试后再决定开放；回退是关闭该工具入口。无需先改原作者插件。 |
| 把跨日工作接到正式生活 | 复用已有项目及工坊开关，部署本地 Windows 导出补丁，单独观察停止、续跑与来源回执。版本升级重点复查 Windows 路径与上游工坊，关闭功能开关及回退该文件即可保留既有成果。 |
| 让昂贵工作偏向低谷 | 先改 DSH 固定默认估算并使用实际用量校准，保持高峰低成本、时效任务与低谷自主选择。价格表需核对当期官方规则后再改；目前以实现范围报告。 |
| 心境语音及手机房间入口 | 语音用现有表达画像向已验收百炼路径显式传参，回退至固定音色。手机展示复用只读摘要和原有页面，回退局域网展示端口即可。两者可分别验收。 |

需要用户讨论的是电脑使用的首个实际场景、这项权限是否只用于主人交办，还是也开放给自主生活。人格措辞与情绪表现无需新增决议。

## 审查后增量

- 已确认电脑执行兼顾主人委托与自主生活。ThinkBook Windows 上 AstrBot 本机执行隔离探针返回 `unsupported`。锁定的 DSH 0.2.2-rc.1 依赖已有 `dsh-pwsh-sandbox` 和 `dsh-sandbox-local` 的 Windows ACL restricted-token 后端。优先在 FishHomeRunner 下验证受沙箱约束的 PowerShell 路径。普通 PTY 的工作目录参数只指定启动位置，Windows 后端说明明确承诺写入约束，读取与网络边界须另测。拟验收工作区内创建虚拟环境、安装依赖、运行代码、保存产物，以及拒绝读取或写入宿主的正式库、配置和凭据。受托和自主执行分别归各自账本。
- 已安装工坊 Windows Markdown 导出并开启工坊、跨日项目。AstrBot 已重连 QQ，未触发人工项目。原源码与配置备份在 ThinkBook 的 `%LOCALAPPDATA%\FishHome\rollback\workshop-20261114-140719`。项目实际跨日、作品交付待自然运行验收。
- 台式机远程工作台已通过 SSH 打开 ThinkBook 的房间与星海，并通过页面、同步和筛选接口实测。
- 已在 ThinkBook 部署 LifePlanner 候选过滤。DSH 任务进入延期后，在到期前让出活动机会，低谷再次作为候选。定向套件 65 项通过，覆盖替代活动和原 DSH 任务复用。更新后仅 AstrBot 重启，NapCat 保持原进程，OneBot 已重连。源码回退副本在 ThinkBook 的 `%LOCALAPPDATA%\FishHome\rollback\dsh-peak-20261114-142245`。通用电脑任务的峰谷估算仍待接入。
- ThinkBook 使用已安装的 DSH 0.2.2-rc.1、Windows ACL runner，在 FishHomeRunner 下运行隔离探针。系统命令可以启动，临时工作区外的测试文件读写被拒绝；当前工作区内创建文件同样被拒绝，直接启动正式 AstrBot 的 Python 返回 Win32 5，符合现有安装隔离。另用 Runner 进程亲自新建工作目录复测，仍未通过目录内写入。本次未读取真实敏感数据。官方包自身说明 ACL 后端限制写入，读取、网络和进程可见性不能视为同等隔离。保留原运行配置，待在专用 Runner Python 和工作区上取得正常正向回执，再按需求验证外部读取、依赖安装与模型账本。
- 另以合成占位变量实测，受限命令能继承父进程的环境变量。现有两路 DSH 由控制台把计费网关票据注入环境，因此通用执行不能直接挂在当前带票据进程上并依赖 ACL 隔离。接入条件是独立执行环境只收到任务所需数据，凭据和计费留在执行环境外；随后验收 Python、工作区读写和两种任务归账。生产执行权限仍未开启。
- 后续在 ThinkBook 用安装缓存中的便携 Python 3.12.10 和 Runner 自己创建的临时工作区复验。标准账户下成功创建虚拟环境、离线安装自制样例 wheel 并导入，测试子进程没有收到合成凭据变量。经 DSH Windows ACL runner 运行同一 Python 时，`python.exe -V` 和 `pythonw.exe` 均以 `0xC0000135` 退出，脚本未开始。补全临时运行目录和依赖搜索路径后仍可复现。Windows 版本为 CoreCountrySpecific，未发现现成的 Windows Sandbox、Hyper-V 服务或可运行的 WSL 发行版。本轮仅使用临时文件，未安装宿主环境或改正式权限。保留 DSH 的委托和自主调度入口，后续应先确定一个独立的执行后端，再做挂接与正反验收，避免把 Runner 标准账户的正向测试算成沙箱验收。
- 独立环境补查：ThinkBook 的 `wsl --status` 返回 50，未发现相关运行服务，无可用发行版；通过现有 SSH 查询 Windows 可选组件需要提升权限（DISM 返回 740）。备用 `fishvm` 的 SSH 连接超时。台式机已验证 `deploy/dsh_bridge/verify_python_workspace.py` 能离线建虚拟环境、安装并导入样例包，且会把工作区外样例可读写、模拟凭据可见判为失败。建议预备仅用于这一能力的 ThinkBook WSL2 执行环境。启用及重启需要用户批准。验收时先由受信宿主放置临时的外部样例，再验证执行侧只接触任务内容、无法接触正式宿主数据与网关；环境内能够运行 Python 和安装依赖后，再把委托与自主两条入口接入。
- 用户随后选择 Windows 原生路线。ThinkBook 实测现用 `FishHomeRunner` 无法打开 AstrBot 和控制台的正式配置，但可打开两路 DSH 的启动日志。测试只尝试打开文件，未读取内容。因此通用任务拟使用独立标准账户 `FishHomeCompute`。
- 已将 `deploy/dsh_bridge/Prepare-ComputeProbe.ps1` 和 `verify_python_workspace.py` 复制到 ThinkBook。由本机管理员启动一次后，脚本创建或复用该标准账户，用隔离的临时样例测试 Python、虚拟环境、离线 pip 安装、凭据环境变量和指定文件的读取权限。回执仅含布尔值，临时目录清理后账户保留。生产服务未改。待首次运行取得回执，再验证正式工作区的 ACL、DSH 网关访问边界和两条任务入口。指定文件的验证通过只证明这些目标的访问结果。
- ThinkBook 首轮回执确认 `FishHomeCompute` 可在临时工作区创建虚拟环境、离线安装并导入样例包，外部样例读写被拒，列出的凭据环境变量未出现。正式文件读取检查未通过。SSH 只读查看 ACL 确认 AstrBot 配置继承了 `Authenticated Users` 的 `Modify` 权限，而显式拒绝只列了 `FishHomeRunner`。两路 DSH 日志在 SSH 会话中无法直接检查。已上传新版脚本，`-ProtectInstall` 由本机管理员给安装根目录添加只针对 Compute SID 的继承拒绝规则，回执逐项列出三个文件的读写状态。PowerShell 解析及 Python 模拟失败路径已在台式机验证，等待 ThinkBook 实测。需撤销该安装根规则时，可在管理员 PowerShell 对安装根运行 `icacls.exe $root /remove:d ('*' + (Get-LocalUser FishHomeCompute).SID.Value)`。正式服务接线待做。
- ThinkBook 管理员回执确认修复后 AstrBot 配置、两路 DSH 日志均拒绝 `FishHomeCompute` 读写，临时 Python 工作区测试仍通过。随后通过现有 SSH 做无凭据探测，两路 DSH 的 `session/list`、计费余额和两路模型入口均返回 401。模型入口测试发送的是无效 JSON，未调用付费模型。PhoneMCP 8009 和媒体 MCP 8011 的事件流入口返回 200，本机 5037 和 5555 可建立 TCP 连接。只验证了连接，没有调用手机工具或执行 ADB 命令。HTTP 探测来自普通 SSH 会话，待用实际执行进程复验本机和局域网边界。
- Windows 原生下一步：先用无网络能力的 AppContainer 启动便携 Python，向容器只开放指定工作区和运行时读取，验证子进程中 `venv`、离线 `pip` 仍可用，同时验证正式数据和本机控制接口不可达。依赖安装先使用工作区已有的 wheel，在线依赖获取另行验证。ThinkBook 已装 DSH 0.2.2-rc.1 的 `dsh-mcp-client` 支持 stdio，可用作受托和自主两路共用的宿主侧工具接线候选。该桥仅转交任务和成果，由宿主侧执行权限检查，模型与计费仍在既有 DSH 进程。AppContainer 和工具接线目前尚未部署或实测。微软文档：https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer 。
- ThinkBook 的 AppContainer 试验已通过。先在运维登录会话验证，再通过现有控制台的 `run_as` 路径以 `FishHomeCompute` 重跑 `deploy/dsh_bridge/probe_appcontainer.py`。便携 Python 启动和工作区写入成功，合成外部文件读写被拒，选定的模型和控制台凭据变量未出现。容器内实际检查七个本机端口 5037、5555、59934、59935、6197、8009、8011 均未建立连接。离线安装合成 wheel、虚拟环境导入以及子进程继续持有 AppContainer 令牌全部通过。容器配置未授予网络 capability。该结果仅覆盖列出的测试目标和临时工作区。正式 DSH MCP 注册、真实任务交付和在线依赖仍未验收，正式服务未改。
- 接线验证：使用 ThinkBook 实装的两份 DSH Web 配置分别启动临时实例。直接覆盖旧文件预设的 `config.plugins` 会丢失该预设的 `config.id`，实际会话创建失败。改用独立计算预设后，两路均通过本机 Remote API 创建空白会话，stdio MCP 均收到 `tools/list` 并返回样例工具。没有提交模型请求。下一步为正式计算预设接上受控执行器，分别验收受托和自主派发、产物与原账本归账，再上线。正式 DSH 实例未改。
- 执行器验证：`deploy/dsh_bridge/compute_mcp.py` 实现 `run_python`，以 `FishHomeCompute` 身份在无网络 capability 的临时 AppContainer 内运行，返回限长文本输出。ThinkBook 通过真实 MCP 消息验证了会话初始化、工具发现、脚本写文件后返回合计 `5`，以及脚本超时时终止它启动的 Python 子进程。AppContainer 启动时绑定了 Windows Job Object。测试发现运维端 `run_as` 在此账号的首次启动环境可能提供 `C:\Users\Default`，验证端改从已登记的 SID 用户目录明确传入 `USERPROFILE` 和 `LOCALAPPDATA`，正式启动也需按账户读取登记路径。当前工具验证使用独立临时测试进程，尚未把实际 MCP 工具送进 DSH 模型回合，也没有接入来源文件、报告交付或计费账本。本轮未调用付费模型，正式实例未修改。
- 实际 DSH 接线的离线验收：`compute-preset.yml` 使用单独 `fishhome-compute` 预设，`compute_proxy.py` 将 stdio 请求转到专用账户的本机认证执行器，无票据请求返回 401。ThinkBook 上两路临时 DSH 均成功选 Flash、接受提示，模拟模型实际发出 `mcp__fishhome_compute__run_python` 调用，并从工具结果读到 `sample.csv` 合计 `5` 及 Markdown 来源。DSH 另发的辅助模型请求没有工具列表，不能拿该请求判断注册失败。既有派发入口通过 Bridge 的 `life_compute_enabled`、`commission_compute_enabled` 单独选择新预设，默认关闭，源文件复核、产物校验和归账流程沿用既有入口。受托 3 项、自主集成 24 项、计费 7 项本地测试通过。正式部署的 worker 生命周期、票据文件 ACL 及真实两路归账仍待验收，现有正式实例未重启。
- 增加同机离线端到端计费验收。ThinkBook 两路临时 DSH 经真实 FishHome 计费网关、临时 SQLite 和本机模拟 Anthropic 上游运行。先跑自主时只结算自主 ¥0.00066，交互为零。跑完交互后交互结算 ¥0.00066，自主数值原样，两路预留均为零。两路都收到计算报告。模拟模型只核对工具返回，无 QQ 写回。正式开关、控制台对 worker 的启停及持久部署还未接入。正式发布前用会话级数据目录和双方访问验证约束 compute token 文件，再以真实模型确认产物落盘。
- 控制台已实现 Windows Python 执行器的单独启停、计算账户密码的 DPAPI 保存和计算开关。打开开关后需重启两路 DSH。启动时先由控制台确认执行器通过认证探测，再使对应的 DSH 加载专用预设，并在 Bridge 取得连接票据后开启该路计算能力。关闭开关立即清除 Bridge 计算开关，再重启两路 DSH 可恢复原预设，随后可关闭计算服务。ThinkBook 使用独立临时状态、目录和端口实测候选控制台，成功启动并取得 200，重复启动识别为已运行，停服后端口消失。实际使用两种账户打开文件，Runner 可读 worker 票据，Compute 读取被拒，测试没有读取票据内容。控制台本地 7 项回归通过。Windows 将解压目录移动后保留原 ACL，现已为目标运行时目录显式赋予两个账户的执行权限。正式控制台仍运行旧版本，尚未用真实模型落盘及交付。
- ThinkBook 正式切换完成。首次等待就绪超时，恢复旧配置和服务后延长启动等待并重试。第二次实际确认执行器、两路 DSH、AstrBot 和 OneBot 均就绪，两路 DSH 的进程参数带计算 overlay，Bridge 两路计算开关均为真，控制台诊断的 OneBot 连接与 DSH Bridge 通过。回退副本在 ThinkBook FishHomeOps 的 `%LOCALAPPDATA%\FishHome\manager\before-compute-20261114-184528`。本轮部署未发送验收 QQ 或运行指定的两条付费验收任务。人格库只读检查确认 v2.3.1 与 v2.2.1 回退条目均存在。ThinkBook 有 11 月 14 日 18:43 的普通备份，台式机现有校验回执是同日 00:03 的自动快照。

复查方法：在台式机用已配置 SSH 检查端口和脱敏开关，以 SQLite `mode=ro` 汇总活动、票据与账本；用 `PYTHONPATH=tests` 运行 `python -B -m unittest -q test_bili_life_integration test_bili_projects test_fish_affect_bridge test_bili_contact test_owner_reminders test_commissioned_files test_fish_cost test_fish_m6`，102 项通过。本次无真实发送、付费调用或数据写入；以上历史 QQ 实测按原回执引用。
