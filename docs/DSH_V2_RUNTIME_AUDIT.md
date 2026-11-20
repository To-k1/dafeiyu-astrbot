# 大肥鱼「左膀右臂」v2.0 现状核查与证据

2026-11-16。核查对象为 v2.0 增量交接稿、台式机 FishHome 源码 `feature/fish-affect-bridge`（`44feaab`）及 ThinkBook 正式实例。正式安装副本独立于台式机 Git。按现场回读、历史实测、源码与配置三种证据标注。未找到交接稿所列的「左膀右臂」前一版；本轮以收到的 v2.0 为准。

## 拓扑与电脑操作断点

- 台式机通过已有 SSH 管理 ThinkBook。正式 QQ 路径是 NapCat → AstrBot 4.29.4 → FishHome 活动插件 / DSH Bridge。历史进程回读显示 AstrBot 由 FishHomeOps 启动，DSH 则由 FishHomeRunner 运行；计算执行器采用 FishHomeCompute。雷电和 PhoneMCP/ADB 是另一路手机工具。见 [运行审查](FISHHOME_RUNTIME_REVIEW_2026-11-14.md#L7) 和 [控制台进程定义](../deploy/desktop_manager/manager.py#L156)。
- 受托与自主都是 `dsh web` 服务的本机 Remote API 后台会话。分别使用 59935 和 59934，限定本机且有认证。调用经 [派发工厂](../deploy/dsh_bridge/m3_qq.py#L29) 和 [Remote 传输](../deploy/dsh_bridge/dsh_remote.py#L42)。交接稿中的 headless 在本项目指鱼不经 WebUI 发起的后台通道，实际入口与 `dsh headless` CLI 不同。
- 最后已证实成功的环节是两个后台会话分别以测试模型调用 Windows `run_python` 并取得产物；旧文件预设另有真实模型的受托及受控自主任务。计算调用经 [专用预设](../deploy/dsh_bridge/compute-preset.yml#L1) 和 [认证执行器](../deploy/dsh_bridge/compute_mcp.py#L126)。该预设声明文件工具和计算 MCP，既有文件预设也没有桌面提供者。当前缺口是 GUI provider、专用交互式桌面和应用许可尚未接通。尚无证据支持「Web 有 GUI 但 Remote 看不到」；也没有必要先让鱼遥控 DSH Web。见 [计算部署记录](FISHHOME_RUNTIME_REVIEW_2026-11-14.md#L78)。
- AstrBot 现有 PhoneMCP 真机录屏、导航和截图走模拟器，Python 经 DSH 对工作区运行。未验证 AstrBot 本身操作 Windows 鼠标、键盘或应用窗口。官方 DSH 当前有实验性 Windows CUA；依赖匹配 helper、可访问且未锁屏的桌面，当前提供者的应用级许可需按会话处理，应用启动另需入口。上游可用性只用于选方案。 [DSH Computer Use](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/computer-use.md) · [Windows 提供者](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/experimental/cua-windows/README.md)

本轮 SSH 回读显示 DSH 安装包为 `0.2.2-rc.1`，安装树未发现名称匹配 CUA 或 OpenCode 的可选包。59934、59935、59936 均在 `127.0.0.1` 监听，Bridge 的 `life_compute_enabled`、`commission_compute_enabled` 均为真。AstrBot 6185/6199、计费 6197、PhoneMCP 8009 与 B 站伴侣 8011 也在本机监听；NapCat 管理端 6099 监听所有 IPv4 地址，监听状态不证明防火墙放行。所列进程均报 Windows Session 1。SSH 账户无权读取 CIM 进程树及进程所有者，现存身份说明引用 11 月 14 日实测；本轮没有重新确认桌面是否锁屏、每路有效工具列表或 OneBot 消息交付。

## 能力矩阵

| 范围 | 状态和证据 | 待补 |
| --- | --- | --- |
| T1 真实委托、自主任务 | 文件受托历史通过真实 QQ，产物和交互账单可回查。ThinkBook 的隔离活动库通过真实自主 DSH 派发和自主账本结算。目标、来源、报告按现有活动模型管理。 [受托回执](DESKTOP_MANAGER.md#L48) · [自主回执](M4_CHECKPOINT.md#L33) | 自然排队及正式计算预设下的两条真实模型回执待验证。 |
| T2 Windows GUI | 项目未接入。模拟器手机链有现场证据；Python 执行器没有 GUI 工具。 [手机与媒体验收](DESKTOP_MANAGER.md#L62) · [计算工具](../deploy/dsh_bridge/compute_mcp.py#L18) | 同一后台调用能选中独立样例窗口、完成动作、取得截图证据；对比同一预设的 Web 和 Remote 工具列表；验证锁屏和焦点冲突的处理。 |
| T3 独立工作区、核心权限 | 已部分验证。FishHomeCompute 为标准账户；正式安装根对 Compute 的继承拒绝通过了指定配置和两路 DSH 日志读写测试。AppContainer 样例验证工作区写入、离线 wheel、受限网络、凭据环境和超时子进程清理。 [ACL 与探针](FISHHOME_RUNTIME_REVIEW_2026-11-14.md#L68) · [保护脚本](../deploy/dsh_bridge/Prepare-ComputeProbe.ps1#L60) | 尚未通过 GUI 和个人插件测试；三项实际文件的 ACL 回执不能覆盖整台机器。带网关票据的 DSH Host 与计算沙箱必须维持分离。 |
| T4 进程、软件、内存 | 部分实现。控制台管理员能启停既有服务；Compute 的 Job Object 验过超时清理。未找到给肥鱼的系统资源观察、个人应用登记、优雅退出或可用 RAM 复核工具。 [控制台](../deploy/desktop_manager/manager.py#L156) · [计算探针](FISHHOME_RUNTIME_REVIEW_2026-11-14.md#L79) | 样例应用正常关闭和退出复核，应用有未保存内容或下载时保留；并发和内存限制以 16 GB 实测定。 |
| T5 工作伙伴的说明和插件 | 部分实现。DSH 已支持固定 persona 和 preset；文件预设存在 `agent-instructions` 配置。当前项目没有鱼可管理的版本化说明、按会话选择或个人插件发布流程。 [文件预设](../deploy/dsh_bridge/m3-file-only.yml#L5) · [计算预设](../deploy/dsh_bridge/compute-preset.yml#L1) | 新任务记录实际使用的说明版本，进行两轮行为对照。个人插件只在独立测试环境验证、安装和撤销。 |
| T6 DSH 独立处理媒体 | 部分实现。B 站伴侣已实测按时间抽帧、语音转写和视频证据；正式同一视频开头 18 秒抽出 5 帧。已知 DSH 文件和计算预设禁用直接读图，现有 DSH 工具没有 B 站抽帧、ASR 或分析结果接线。 [媒体实测](DESKTOP_MANAGER.md#L62) · [计算预设](../deploy/dsh_bridge/compute-preset.yml#L16) | 把媒体能力作为 DSH 可调用的接口，验收短测试视频及仅字幕降级。同一段结果区分直接画面、字幕、ASR 和推论。 |
| T7 持久性、生活、心境和记忆 | 部分实测。委托任务及 QQ 投递状态分开持久化，可恢复轮询或标记不确定；现有主人暂停闲聊期间仍完成受托交付。自主 DSH 走目标租约及检查点。B 站经历召回、心境与计划查询已有正式回执；项目重开及工坊导出有隔离证据。异议适配覆盖有来源的活动候选，LivingMemory 自身的自动归纳无通用结构化异议入口。 [委托状态机](../plugin/astrbot_plugin_bili_activity/commissioned.py#L64) · [自主检查点](../plugin/astrbot_plugin_bili_activity/dsh_task.py#L160) · [真实 QQ 回执](UNIQUE_LIFE_AUDIT_2026-11-14.md#L76) | 通用已运行任务的停止、续做、更改说明、长期结果索引尚未完整；自然跨日、有效兴趣候选现场以及媒体代看/亲看两种归属的完整回链待验。 |
| T8 费用 | 两路 DeepSeek 分账已有真实文件任务及模拟计算账单。Go 的订阅、provider、会话元数据和实际扣费无现场证据；ASR/视觉与 Go 的统一费用视图未完成。 [计算账单记录](FISHHOME_RUNTIME_REVIEW_2026-11-14.md#L81) | 分别验证模型、Go 与媒体服务；保持原 ¥5 自主预算及批准条件。Go 的 Extra Usage 是单独开关。 [OpenCode Go](https://opencode.ai/docs/console/go#usage-beyond-limits) |

## v3.0 和上下文

本轮只读确认 ThinkBook 的默认配置人格 ID 是 `fish-home-v3.0`，该人格已存在，唯一好友私聊规则与该私聊当前对话都没有指定单独人格。没有发送 QQ 或捕获实际发往模型的完整请求，单轮有效正文及模型侧截断仍待验证。AstrBot 源码先选人格并写入系统提示，再执行插件钩子。[旧注入链审查](UNIQUE_LIFE_AUDIT_2026-11-14.md#L48) 可用于核对顺序；其中「旧版仍生效」属于历史配置。

当前主人私聊存储历史共 172 个消息条目，其中 59 个为工具结果，工具结果字符串合计 13,485 字符，最长 1,357 字符。只跨机返回计数，未导出正文。这个统计不含结构化内容里的文本，无法计算工具占全部文本或模型输入 Token 的比例，也没有按工具来源确定其中多少来自 DSH。活动提示会追加标记为资料的摘要；自主 DSH 证据保存报告前 500 字，受托交付会带最多 1,200 字。下一步用合成报告比较正常和较长会话的实际输入结构，确认模型收到的段落长度、消息角色及压缩范围。 [报告摘要](../plugin/astrbot_plugin_bili_activity/dsh_task.py#L219) · [受托交付](../plugin/astrbot_plugin_bili_activity/commissioned.py#L86) · [活动上下文](../plugin/astrbot_plugin_bili_activity/main.py#L2444)

本轮为只读核查，无生产写入、部署、发送消息或模型费用。

### 后续回读：正式自主 DSH 已有产物

2026-11-16 回读正式活动库，生活调度和自主任务开关均开启。发现一条 `agent_selected=true` 的计划及一条由该入口生成的 `completed` 任务。对应经历发生于北京时间 11 月 15 日 10:57，带生活 run、同一目标、DSH 会话、报告、来源回执及 `verified` 标记。任务归自主账本，回写 Token 为未缓存输入 826、缓存读 3,712、输出 2,960，未单独复计人民币扣款。普通私聊仍由 `guard_phone` 移除 `df_file_research`，只在生活轮次提供，这是普通私聊不能直接派出该类自主任务的明确代码原因。 [工具裁剪](../plugin/astrbot_plugin_bili_activity/main.py#L2352) · [经历回写](../plugin/astrbot_plugin_bili_activity/dsh_task.py#L214)
