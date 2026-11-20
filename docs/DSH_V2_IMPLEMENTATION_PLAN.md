# 大肥鱼「左膀右臂」v2.0 最小实施与权限矩阵

核查与现状见 [运行证据](DSH_V2_RUNTIME_AUDIT.md)。继续使用现有 FishHome 活动、目标、任务检查点、FishAffectBridge 和 LivingMemory。执行结果可由 DSH 提供，角色自己的评价和长期记忆决策沿现有入口。

## ThinkBook 现行 B 站视觉路由

已读取正式活动插件的 `control.json`。`flash_text_only_media=true`，`autonomous_visual_enabled=true`，自主 provider 已配置。现行 `df_bili_watch` 由伴侣取帧，活动插件附上时间戳和 `image_url`，用 `describe_with_flash` 通过本机 6197 直接调用 DeepSeek 官方 `deepseek-flash`。交互和自主按各自账本走。`flash_text_only_media` 是旧字段名，设为 true 时在这里恰恰启用 Flash 图像分支。自主单轮现有视觉门控仍生效。[B 站活动插件](../plugin/astrbot_plugin_bili_activity/main.py#L2782) · [人民币网关](../deploy/fish_cost.py#L17)

源码还保留 `face_convert._vl_describe` 的百炼视觉分支，供不用 Flash 媒体路由的配置使用。现行 B 站亲看使用 DeepSeek 视觉，百炼音频听写是独立功能。`dsh_agent_tasks_enabled` 只控制自主文件任务，正式 DSH 视频代看有独立开关与验收记录。

用户确认桌面先按独立标准账户及测试窗口接入，现有模拟器继续使用 PhoneMCP。先用现有 DeepSeek，Go 订阅验收延后。

补充确认：窗口不可用时保留待办，文件和后台媒体任务继续。本地视频默认仅本机处理，确需提交云端时另行确认。公开 B 站视频的 DSH 委托优先验证 Go 订阅的 `deepseek-v4.1-flash` 路由，使用 Go 限额；肥鱼亲看的既有视觉调用仍走原账本和现行每轮限制。验证时须核对 `billing_source`、`allowance`、图像内容和模型路由；完整订阅余量仍以官方 Console 为准。[OpenCode 计费用量字段](https://opencode.ai/docs/console/api/inference/#cost)

ThinkBook 现场回读确认交流和电池的合盖动作均为 `0`，即不采取动作。后台文件和媒体任务不依赖物理屏幕。窗口点击及截图另需在专用会话实际合盖验收。第一段本地视频已通过新的 `deploy.dsh_bridge.local_video` stdio MCP 抽帧测试，合成短片返回工作区内的帧路径和时间点；未挂到正式 DSH，尚无视觉识别或音频转写。[合盖策略](https://learn.microsoft.com/en-us/windows-hardware/customize/power-settings/power-button-and-lid-settings-lid-switch-close-action)

用户确认默认优先委托 DSH 处理公开搜索、视频分析和适合交付成果的任务。派发前只需要任务说明、链接或已授权素材名，肥鱼无需预读原始资料。回传短摘要、可核查来源、时间点和不确定范围；完整报告按需查看。肥鱼可以选择亲自体验或复核，届时分别记录亲历与代理报告。现有自主文件任务入口已按此更新运行提示，未改人格正文。

公开 B 站已实现独立取证预设 `fishhome-public-video`，通过 `python -B -m deploy.dsh_bridge.public_video --render-preset <临时配置文件>` 生成。DSH 只需接收完整 BV 号或公开链接，工具自行查分 P、下载并抽帧，通过 MCP `ImageContent` 交给当前 DSH 会话。公开读取无账号 Cookie，工具返回字幕和时间戳，不传原始音频。锁定版 DSH 已验证预设可加载；36 项媒体和生活路径测试通过。实际公开短片 0–8 秒返回 0.0 和 7.5 秒两张图，当前区间无字幕。未调用模型。正式派发、Go 模型图像传递与账单、网页搜索接线仍待实测。ThinkBook 控制台只读返回 OpenCode Key 尚未保存。

后续检查点：主人已保存 ThinkBook Key 并确认关闭 Extra Usage；官方 Go 模型目录返回 `deepseek-v4.1-flash`。从 ThinkBook 单发左右随机颜色的 128×64 合成图，HTTP 200，识别顺序正确。回执是 `billing_source=go`、`cost=0`、`allowance=0.00002241`，237 输入和 3 输出 Token。正式模型未切换。新增 `deploy/dsh_bridge/go_profile.py` 的独立单会话配置，并在桌面锁定版 DSH 0.2.2-rc.1 通过真实 Remote API 对本机替身验收：工具被调用、图片进入模型请求、Go 会话请求头到达。可复跑脚本位于本机 `payload/go-dsh-image-probe.py`，只使用合成图片和假 Key。下一步把这两段串在隔离实例，用各自的计费回执验证真实 Go，并给肥鱼接只传链接/问题、回短报告的正式入口，再接网页搜索。

本轮接续：ThinkBook 隔离 DSH 0.2.2-rc.1 调用 MCP 取得合成图片，真实 Go `deepseek-v4.1-flash` 正确识别紫色、蓝色顺序。请求工具和提交图片两次请求的 `billing_source` 都为 `go`，`cost` 都是 0，合计 `allowance=0.00021269`。正式 DSH、AstrBot 主模型和人格未切换。`go_relay.py` 用本机短期会话凭据转发，Go Key 保留在中继进程；用量回执缺失或出现额外扣费时停止后续转发。`go_video_task.py` 只派发公开 BV/链接和问题，短摘要、采样证据和用量分字段返回；媒体预设核对 BV 和分 P。相关 30 项单元及插件集成测试通过。

锁定版 DSH 在台式机以本机替身模型完成一次真实 Remote API 代看链路。首个模型请求只含链接和问题。由 DSH 调用公开视频工具，抽出 `BV1g8Wq6DEXj` 第 1 P 开头的 `0.0`、`7.5` 秒两帧，两帧实际进入下一次请求。最终回出短摘要、视频链接、cid、采样区间和时间点。这次替身调用没有产生模型费用，摘要仅用于验证回传，不能作为画面解读。验收发现 DSH 默认额外调用模型生成会话标题，隔离预设已关闭该请求。台式机可复跑 `payload/dsh-public-local-e2e.py`。

全量测试以 Windows UTF-8 和 Git Bash 运行。Conda 环境通过 940 项、跳过 20 项，三组控制台测试因该环境缺 `psutil` 无法加载；用已含此依赖的 AstrBot 解释器补跑这三组 17 项全部通过。没有为测试更改现有 Python 环境。

## DSH 视频代看接线

正式视频代看选用用户已配置的 OpenCode Go `deepseek-v4.1-flash`，Extra Usage 已由用户关闭。FishHomeRunner 按任务启动隔离 DSH，使用本机模型中继和仅能取本次公开视频的只读工具。Go Key 仍由控制台进程持有。

`df_dsh_video` 在主人私聊和生活活动可用时作为默认，模型沿用旧 `df_bili_watch` 时也会按默认模式转到同一 DSH 派发。肥鱼只给公开链接或 BV 和问题，不预读原视频；DSH 自己取得画面与字幕。失败时如实回报，不自动切换到亲看。肥鱼可自己决定亲看或复核，用 `df_bili_watch(mode="personal")` 走 DeepSeek 官方 `deepseek-flash`。代看报告写 `dsh_video` / `system`，记录实际样本和来源；亲历继续写 `video` / `agent`。原有自主文件委托沿用 `df_file_research`。通用网页搜索还需接入单独的只读工具。

在 ThinkBook 控制台的 OpenCode 区域关闭「DSH 视频代看」即可隐藏派发工具。正式计费、机器人主模型及两路文件 DSH 无需为此切换。

ThinkBook 隔离验收使用正式 FishHomeRunner 账户和真实 Go。`BV1g8Wq6DEXj?p=1` 的 0–8 秒只抽了 0.0 和 7.5 秒两帧，Go 返回了画面描述和帧内文字。没有音轨转写，字幕轨道为空。两次均返回 `billing_source=go`、`cost=0`，合计 `allowance=0.00049292`。媒体连接退出顺序修正后，整条任务及工作进程正常退出。活动、媒体和派发共 89 项测试通过，控制台接线及权限隔离 18 项通过。普通研究目标可以引用受托报告作为完成证据，兴趣与个人亲历仍按老的证据类型过滤。

2026-11-16 06:13 已部署并启用 ThinkBook 正式派发。仅重启控制台和 AstrBot，NapCat 和两路文件 DSH 的 PID 保持原值。现场回读开关、Bridge 凭据存在、主服务端口、授权入口拒绝无效链接均通过。第一轮 QQ 实测模型仍选择旧工具，数据库出现亲看记录。06:17 随即修复旧工具默认模式，保留 `personal` 显式亲看选项。只更新活动插件并重启 AstrBot，NapCat 和两路文件 DSH 的 PID 保持原值。默认委托、错误分 P 和显式亲看边界已加回归，活动和媒体相关测试共 90 项通过。第一次部署及默认修复的代码回滚副本分别位于 ThinkBook `%LOCALAPPDATA%\FishHome\manager\before-dsh-video-20261116-061316` 和 `%LOCALAPPDATA%\FishHome\manager\before-dsh-video-default-20261116-061752`。

第二轮主人 QQ 验收未在请求中点名 DSH，实际回执明确说明由小肥鱼代看。正式数据库按该 BV 回读只新增一条 `dsh_video` / `system`，未新增 `video` / `agent`。真实取样为 10.0、11.0、12.5、13.0 秒，以及旧派发提示要求的 0.0、7.5 秒预览。06:21 已部署提示修正，只有未指定区间时才预览开头，指定区间直接取样。新增回归通过，尚未另跑 QQ。仅重载控制台，AstrBot、NapCat 和两路文件 DSH 的 PID 保持原值。该次回滚副本位于 ThinkBook `%LOCALAPPDATA%\FishHome\manager\before-dsh-video-range-20261116-062140`。

## 2026-11-16 部署前核查

### Go 订阅与本地调度

- ThinkBook 的公开视频 DSH 直接通过 `GoRelay` 请求 `deepseek-v4.1-flash`，已经独立于人民币网关和峰谷门控。既有两路 DSH 文件任务仍走 DeepSeek 官方网关，自主文件任务的延期仍在 `dsh_task.py`，待切到 Go 的任务才按 Go 归账。预算按实际路由，取消、暂停、续跑按任务处理。百炼听写单独记时长。 [已确认决定](DECISIONS.md) · [Go 中继](../deploy/dsh_bridge/go_relay.py) · [文件任务调度](../plugin/astrbot_plugin_bili_activity/dsh_task.py)
- OpenCode 自己对 DeepSeek V4.1 Flash 公布了峰期和谷期的订阅消耗费率。FishHome 的 Go 派发由供应商窗口和实际 `allowance` 回执决定，无需再加一套本地峰谷择时。 [Go 模型用量表](https://opencode.ai/docs/console/go#usage-limits)
- OpenCode 官方服务有只读 `GET /api/experimental/usage/limits`。从已登录 Console 工作区返回滚动、周、月三组用量，含百分比、是否限额和重置时间。FishHome 目前只接推理 Key，现场未发现 ThinkBook SSH 的 PATH 中有 OpenCode CLI 或正在运行的 OpenCode 进程。单次推理 `opencode.allowance` 也无法覆盖其他客户端的用量。下一步由控制台读取已认证的 OpenCode 服务，只给肥鱼用量状态、刷新时间和查询时间；未连接或读取失败显示未知。Go 拒绝新请求时保留任务安排，等待提供方额度恢复，维持已关闭的 Extra Usage。 [OpenCode 用量 API](https://opencode.ai/docs/api#usage) · [Go 额度窗口](https://opencode.ai/docs/console/go#usage-limits) · [推理回执](https://opencode.ai/docs/console/api/inference/#cost)

### B 站亲看与代看的听写

- 已核对 ThinkBook 的已安装源码和生效配置。FaceConvert 没有独立 `config.json` 模型覆盖，使用默认 `qwen3-asr-flash` 和 SDK 默认中国内地 DashScope 地址，凭据回退到现有百炼 TTS provider。主人私聊显式亲看时，只在无字幕且取得音轨后呼叫 ASR，每次最多处理五段。已配置自主 provider 的自主亲看分支不会触发这段 ASR。DSH 代看预设目前只取帧和字幕。 [亲看分支](../plugin/astrbot_plugin_bili_activity/main.py#L2932) · [FaceConvert 调用](../plugin/astrbot_plugin_face_convert/main.py#L390) · [代看媒体工具](../deploy/dsh_bridge/public_video.py#L69)
- 阿里云中国站列出北京区的 `qwen3-asr-flash` 为 ¥0.00022/秒，合资格免费额度 36,000 秒，有效期 90 天。实际剩余量和有效期须在同一百炼空间的「模型用量 → 免费额度」查看。本轮未发送音频、未读取百炼账单。为 DSH 加听写时可复用同模型和宿主侧百炼调用，按实际音频段单独计量。回传带时间点的转写，和字幕轨、帧内读字、DSH 摘要分别标注。 [百炼定价](https://help.aliyun.com/zh/model-studio/model-pricing) · [免费用量查询](https://help.aliyun.com/zh/model-studio/model-usage-statistics)

### dsh-bili-humanism

- 已审查 v1.11.5 的 manifest 和工具源码。它是 Cordis DSH 插件，能分批交付取帧附件、记录字幕或转写的覆盖范围，也能将现成的带时间戳 ASR 分段导入已有条目。它的本地 ASR 是 `faster-whisper`，FishHome 已使用 WBI 取 B 站字幕并实现按问题范围取帧，所以优先借用「来源、分段、未覆盖区间」的输出形式，保留现有只读取材和 Go 派发。 [工具源码](https://github.com/InariNGT-Ofiicial/dsh-bili-humanism/blob/main/index.js) · [本地媒体脚本](https://github.com/InariNGT-Ofiicial/dsh-bili-humanism/blob/main/scripts/media.py) · [FishHome 字幕请求](../deploy/bili_activity/bili_api.py#L375)
- 直接装到当前 `0.2.2-rc.1` 视频预设没有兼容证据，上游的 peer 声明和测试覆盖为 `0.2.1-rc.2/rc.3`。历史、收藏、点赞和投币默认关闭，但人格积累及观影后提问默认开启，读取也支持自带 Cookie。若另试完整插件，使用独立 profile 和媒体资料目录，关闭人格、提问、定时取稿及平台写入；FishHome 的活动记录和 LivingMemory 仍负责正式经历、意见与记忆。此轮只读审查。借用源码时保留 MIT 声明。 [兼容说明](https://github.com/InariNGT-Ofiicial/dsh-bili-humanism/blob/main/docs/DSH_COMPATIBILITY.md) · [许可证](https://github.com/InariNGT-Ofiicial/dsh-bili-humanism/blob/main/LICENSE)

### Windows 桌面与个人软件

继续复用 FishHomeCompute 的无网络 Python 来处理后台任务。Windows 窗口先在独立标准账户的已登录会话，用一个登记的测试应用验证启动、截图、操作、回读和正常关闭。个人应用管理按已登记应用提供状态、启动及正常退出，有未保存内容就保留。ThinkBook 合盖动作已核实为不休眠，GUI 合盖和锁屏后的行为要单独实测。手机直接访问房间与星海暂缓。通用网页搜索留到本轮核查之后接入。 [现有运行拓扑](DSH_V2_RUNTIME_AUDIT.md) · [已确认决定](DECISIONS.md)

### 实施顺序与第三方候选

- 先做 DSH 默认查网页，再做 Windows 桌面和个人软件、峰谷调度和任务控制、媒体和订阅查询。每项先完成可自行执行的验证，把登录、实际窗口操作和 QQ 回执等需要主人的验收集中在末尾。Codex 的 5 小时额度剩 10% 时记录可复跑的断点并暂停，和 DSH 的 Go 额度分开。
- 主人确认四项建议并调整 Q5。DSH 的网页、媒体和两路文件模型统一接 OpenCode Go `deepseek-v4.1-flash`。旧文件任务切换时保留受托/自主归属、各自工作区、来源与结果回执；只改模型的订阅归账与恢复条件。FishHome 的人民币额度和峰谷继续用于肥鱼亲自执行的官方 DeepSeek 路由。
- Windows 首轮用记事本验收。工作区依赖和已批准来源的便携工具可自行安装，新增 GUI 软件先由主人确认。高峰延期、被主人打断或 Go 限额的任务在条件恢复后回待选，由肥鱼重新决定；手动暂停由主人恢复，取消不再续，重启后先查不明结果。公开视频优先用字幕，缺字幕或明确要听时可以请求百炼 ASR；免费额度耗尽后先询问。
- 主人确认 `dsh-bili-humanism` 可在后续接入 DSH，先做隔离兼容验证。它产生的手账保留在 DSH 自己的工作区，肥鱼可以只读查询或在代看报告里引用。手账内容不自动进入 FishHome 正式生活经历、LivingMemory 或人格提示。FishHome 只记录带来源的代看报告，亲历仍独立。 [项目仓库](https://github.com/InariNGT-Ofiicial/dsh-bili-humanism)
- 主人报告百炼免费额度有效期还剩 87 天，剩余秒数未回读。另记录 `dsh-tieba-dive` 为贴吧浏览候选，等待自有功能完成后与 B 站插件一起讨论。本轮不审查、不安装。 [用户提供的仓库](https://github.com/Nekomeow114/dsh-tieba-dive)

## 2026-11-16 ThinkBook 实施检查点

- **网页**。08:08 启用默认 DSH 网页搜索。DSH 用 AnySearch 公共索引取片段、用 Go 整理，回传摘要和出处。肥鱼用 `df_web_search(mode="personal")` 可亲自查。受限账户真实检索到 Python `pathlib` 文档，主人 QQ 默认路径留待统一验收。回滚副本在 ThinkBook `%LOCALAPPDATA%\FishHome\manager\before-dsh-search-20261116-080824`。
- **Windows**。首批只登记 Notepad。已部署桌面代理、派发工具和控制台接线，开关待专用桌面配对。已在替身驱动验证路径限制、保存回读和正常退出，真实窗口、锁屏和合盖验证待做。没有登记其他软件。可在控制台关闭 DSH Windows 桌面。
- **文件与调度**。08:37 将正式自主、受托两路一起切到 Go `deepseek-v4.1-flash`。两条隔离实测各给出 `sample.csv` 合计 5、各有 4 笔 Go 回执，测试人民币账本未变化。正式服务均已启动并回读模型目录。肥鱼亲自执行的官方 DeepSeek 路由保留人民币账本和峰谷判断。受托任务已支持提交前暂停、恢复、取消和 Go 初次限额等待。运行中任务控制单独验证。回滚副本在 ThinkBook `%LOCALAPPDATA%\FishHome\manager\before-dsh-go-files-20261116-083725`。
- **媒体与额度**。默认 B 站代看此前已完成真实 QQ 验收。百炼听写接线已部署，等主人在供应商侧启用免费额度用完即停。Go 额度已接 `/go额度` 及肥鱼工具，现场返回 `unknown`，等待配对已登录的 OpenCode 本机服务。ASR 免费额度截止与取消行为有本地替身验证，尚无正式请求回执。

### 集中验收

1. 在 ThinkBook 创建本地标准账户 `FishHomeDesktop`。在管理员 PowerShell 执行准备脚本，登录 `FishHomeDesktop` 后在该桌面的 PowerShell 执行启动脚本。把代理令牌直接填进 FishHome 控制台的「DSH Windows 桌面」区域，再启用。验收时保持该桌面解锁，使用工作区内的样例 `.txt`。

   ```powershell
   $root = 'D:\FishHome\FishHome-ThinkBook-OneClick-Repair-v2-2026-11-12'
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$root\repo\deploy\dsh_bridge\Prepare-DesktopBroker.ps1" -InstallRoot $root
   ```

   ```powershell
   $box = "$env:ProgramData\FishHomeDesktop"
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$box\Start-DesktopBroker.ps1" -Python "$box\python\python.exe"
   ```

2. 用拥有 Go 订阅的 Console 工作区登录 ThinkBook 本机 OpenCode，带 `OPENCODE_PASSWORD` 启动 `opencode serve --hostname 127.0.0.1 --port 28894`，在 FishHome 控制台保存同一个「OpenCode 本机额度查询密码」。用 `/go额度` 验证窗口用量和刷新时间。
3. 在百炼的 `qwen3-asr-flash` 免费额度处开启「用完即停」，再在 FishHome 控制台点「已在百炼开启用完即停，启用 DSH 听写」。之后可选公开视频的短区间，让肥鱼明确派 DSH 核对音轨，不用字幕替代听写结果。
4. 主人 QQ 分别发送「帮我查 Python `pathlib` 官方文档，简述 `Path.glob` 并给出处」和 `/委托 sample.csv 请统计 count 列合计并注明来源`。桌面配对完成后再发送记事本验收，要求写入 `check.txt`、保存回读、正常关闭。日志核对默认由 DSH 提交、来源一致、没有重复调用或交付。

受托任务控制的台式机回归命令为 `python -B -m unittest discover -s tests -p test_commissioned_files.py`，使用 `fish-home-dev` 环境。Windows 的 asyncio 测试需要本机回环。运行中暂停回归从 `session.cancel` 到 `turn/end` 回执，再以新请求继续同一会话并只交付一次。自主活动用现有 LifePlanner 任务及前台打断恢复，其他 DSH 媒体和桌面调用尚无跨进程续跑。

### 历史查询、私聊派发与听写验收

2026-11-16 11:56 已部署 `df_dsh_history`、`df_dsh_submit_file`、运行中受托任务控制、两路 `/dsh ping` 和 `/go额度` 窗口用量显示。已从主人 QQ 查回旧视频报告，原来那条代看记录仍为一条。12:08 把私聊文件工具纳入默认选择，但一次普通统计问法仍被模型交给 AstrBot 本机工具。12:16 已补直接路由，当主人好友私聊的统计、整理或分析请求匹配一个已登记文件名时直接进入受托队列。查旧报告、要求亲做和未登记物理路径不会走这条默认路由。其他问法仍由肥鱼选择正式文件工具。只重启 AstrBot，NapCat 和两路文件 DSH 保持原进程。最新回滚副本 `%LOCALAPPDATA%\FishHome\manager\before-dsh-file-controls-20261116-121617`。

- 历史查询按主人私聊隔离，跨 `/new`。可按 BV、文件名、关键词、任务 ID 或经历 ID 查。视频和网页复用原报告，报告记录不会重新派发。已读回 `BV1g8Wq6DEXj` 的代看证据；`19e66d62de45` 的 `sample.csv` 文件报告也经原 DSH 会话读回，查询未调用模型。公开视频本体和抽帧中间文件按原流程清理。报告不可读时提供现有摘要，并标记原报告不可用。
- 文件派发使用正式 `FishHomeRunner` Go 通道。`/委托` 仍可直接入队，肥鱼也可在生活轮次自主发起文件任务。Windows Python 经单独工作区运行，所需输入和产物由该文件任务传递。AstrBot 的直接文件权限不限制 DSH 已授权的目录。新增物理目录需要相应的 Windows ACL 与 DSH 工作目录配置，批准命令级插件本身不会授予目录权限。 [cc-safety-net 项目说明](https://github.com/kenryu42/cc-safety-net)
- 主人确认百炼已开启用完即停后，已启用 ThinkBook DSH 听写，并通过正式 Worker 对公开样例视频 2–7 秒取得 `qwen3-asr-flash` 转写回执。OpenCode 本机服务 `28894` 和桌面代理 `59937` 当时都未监听，仍须配对。原版 Bridge 的临时 CLI 配置与正式后端凭据属不同通道。
- 12:16 同时放开创作轮次的 `df_file_research` 和公开视频代看。已有目标的创作轮次可以主动安排 DSH 草稿，下一轮可通过正式后台执行，保持现有手机租约。回归测试验证了手机由前台占用的情形。项目回看轮次继续限于选定阶段与已引用材料。
- 总控制台「使用帮助 · 主人 QQ 与 DSH」已补齐操作示例并从 ThinkBook 实际页面回读。台式机控制台共用同一份 HTML。ThinkBook 页面回滚副本 `%LOCALAPPDATA%\FishHome\manager\before-dsh-help-20261116-120005`。
- ThinkBook 的原版 Bridge 配置表和已保存配置仍显示旧 `dsh_ask`、`dsh_ping`、`/dsh scan` 说明。正式 Fork 的 `main.py` 不读取这两个旧提示字段，亦不注册旧工具。实际能力说明由活动插件按主人私聊的工具权限提供，修改旧文本不会改变当前运行行为。

### 仍有命令门槛的入口

| 功能 | 当前路径 |
| --- | --- |
| 定时提醒 | 只有 `/提醒` 创建、列表和取消入口。普通聊天不会通过这一入口写入持久提醒。 |
| 暂停和恢复主动联系、自主活动 | 当前只识别代码列出的几种整句，其他措辞可能只获口头答复。放宽自然语义时须保留明确授权和真实状态回执。 |
| 语音 | 语音模式开关有独立固定命令。单条语音已交由肥鱼自主选择，主人要求纯文字时不可用。 |
| 委托管理 | 已有 `/委托 列表/暂停/恢复/取消`；普通语言已可查历史。暂停、恢复和取消仍由命令产生实际状态变化。 |
| 截图 | 有严格匹配的快捷整句，普通聊天也可以主动选择截图与分享工具。 |
| 记忆与公开平台操作 | 记忆候选由功能开关、身份和数据来源决定，没有固定口令。平台写入仍受审批和额度约束。 |

源码入口见 `plugin/astrbot_plugin_bili_activity/main.py` 的 `incoming`、`guard_phone`、`voice_allowed`、`owner_commission_command` 和 `memory_candidate_allowed`。

### 肥鱼自主使用 DSH 与单条语音

- 2026-11-16 12:33 已部署 ThinkBook，并更新总控制台帮助。只重启 AstrBot，NapCat 和两路 DSH 进程保持原值。回滚副本 `%LOCALAPPDATA%\FishHome\manager\before-dsh-file-controls-20261116-123328`。本地全量回归执行 1000 项，跳过 19 项。普通私聊的模型选路和语音现场回执待验证。
- 主人随后确认可播放语音。12:34 的文件问题仍走 AstrBot 本机终端；只读检查会话发现此前三次同类问题都调用了 `astrbot_execute_shell`。12:42 改用 AstrBot 原生工作区权限策略，关闭默认配置的管理员本机执行，保留本地文件工具。模型获得本轮正式 DSH 工具时，提示词列当前已登记素材，说明两边工作区各自独立。普通消息仍不自动进入委托，也不需要清空会话。只重启 AstrBot，NapCat 和两路 DSH 保持原进程。回滚副本 `%LOCALAPPDATA%\FishHome\manager\before-dsh-file-controls-20261116-124242`。本轮 68 项针对性回归通过，真实模型选路待 QQ 回执。
- 12:43 模型转用 `astrbot_shell_session` 和工作区 `grep`，原生权限已成功拦截终端。正式日志确认 `df_dsh_submit_file` 已注册，两插件在主人配置启用，仍无本轮模型工具选择成功的证据。12:50 从该会话工具表移除已无权限的 Shell 会话入口，把 DSH 素材与能力放在系统提示开头，并在主人普通对话首次构建工具时记录文件工具、历史工具的可见性布尔值。日志不记录聊天内容。最新回滚副本 `%LOCALAPPDATA%\FishHome\manager\before-dsh-file-controls-20261116-125023`；49 项针对性回归通过。后续从下一次普通对话回读 visibility 和 `via_agent`，无需请主人反复发送同一道题。
- 12:52:24 主人普通私聊的请求钩子记录 `file_tool=True` 和 `history_tool=True`。12:52:33 回写对话包含文字回复，未记录任何工具调用。正式活动库未新增肥鱼主动派发的 DSH 文件任务。这次验证了请求中的工具可见性，尚未验证模型会主动选择执行。
- 13:04 已把工作区读取和 grep 的未命中结果接入 DSH 素材提示。只对本轮已可用 DSH 的主人私聊生效，不改本地成功结果，不自动创建队列。ThinkBook 仅重启 AstrBot，NapCat 和两路正式 DSH 进程保持原值。回滚副本 `%LOCALAPPDATA%\FishHome\manager\before-dsh-file-controls-20261116-130400`。51 项相关回归通过。后台验收脚本 `payload/accept-thinkbook-dsh-choice.py` 使用本地样例、正式已登记的合成 `sample.csv`、真实交互模型和 Go，不发 QQ，不写 LivingMemory。用实际注册的工具生成 schema，在正式网关出口请求记录工具名和参数是否存在，不记录正文。
- 后台隔离验收首次修正了真实 AstrBot 工具构造参数；第二次已捕获发送给计费入口的两份工具定义与 `tool_choice=auto`，但本地样例的回答未通过断言，尚未走到 DSH 样例。脚本已补齐本地重读处理和两个场景的输出，供下一次复跑。命令 `pwsh -NoProfile -ExecutionPolicy Bypass -File ./payload/Invoke-ThinkBookStage.ps1 -Entry payload/accept-thinkbook-dsh-choice.py`。
- 13:06 主人用普通问法确认肥鱼自行调用 DSH，13:08 收到正式报告，`sample.csv` 最大值为 `pen,3`。进度口头报出了内部编号 `898f57b792a5`，后台结果又发了整份报告和执行目录。待调整普通聊天的进度回复默认藏编号，并按原问题简答。先跑 `payload/check-thinkbook-latest-dsh-turn.py` 回读任务来源和 Go 回执，同时核对隔离验收进程输出；无需重复发 QQ。此处按 Codex 5 小时额度断点暂停。
- 普通聊天由模型选择亲自处理或调用 `df_dsh_submit_file`，入口不按主人消息的关键词和文件名自动入队。现有 `df_file_research` 为自主生活和创作使用，支持素材整理、草稿和受控 Python。公开网页、公开 B 站代看和查历史沿用原有入口。
- 聊天工具支持短等和后台两种使用方式。原任务编号留在工具结果和活动索引，可跨会话用 `df_dsh_history` 查询。短等内完成的结果直接给模型，超过短等期限由原队列继续；后台 QQ 结果正文不附任务编号。只有主人使用 `/委托` 才收到用于列表、暂停、恢复和取消的委托编号。此路新任务固定走 Go；已存在的历史记录仍可读。
- `tts_speak` 在主人好友私聊可自行选用，尊重纯文字要求。单轮最多尝试一次，默认只发语音，确认 QQ 接收后避免再发重复文字。主动联系仍由原有闸门控制。
- 现阶段文件输入仍是各路已登记素材。新建稿件及报告写入 DSH 的产物目录，计算代码跑在已部署的专用 Windows Python 工作区。没有新增第三方目录的权限配置。

### 上一轮额度断点

最新需求：主人希望肥鱼和正式 DSH 共用一个 ThinkBook 目录，放文件一次即可使用，取消该目录的逐文件素材登记。额度剩 5%，本轮只记录需求。恢复后优先验证在主人 QQ 的 AstrBot 工作区创建 `shared` 子目录，只为这个子目录授予 `FishHomeRunner` 读写，并通过正式 DSH 的工作目录配置和文件工具接入它。保留现有 `commission-files`、`m3-file-check` 及旧报告读取。验收覆盖 AstrBot 与 DSH 双向读写、目录外不可达，以及控制台显示实际路径。未修改正式机或现有权限。

另记主人反馈：肥鱼说自己不能访问网络搜索，待核实。恢复后先检查 ThinkBook 搜索开关、主人私聊和生活轮次里的 `df_web_search` 工具可见性，再从后台验证默认 DSH 搜索能否返回公开来源。区分工具未提供、模型未调用、后台检索失败三种情况。无需主人重复发消息，也不开放 AstrBot 本机执行或额外网络权限来替代这条链路。

另记台式机经 SSH 转发访问 ThinkBook 自主 DSH WebUI（`127.0.0.1:15934`）的故障：点击新建工作区无反应；安装插件时报「无法获取插件信息」，随后是 `pnpm` 未识别的 Windows 命令错误，中文显示乱码。待分别核对目录选择器的实际后端及转发行为，以及 `FishHomeRunner` 正式 DSH 进程能否通过自己的 PATH 调用 `pnpm`。用只读插件信息查询验证工具链恢复，安装第三方插件另按既有接入范围处理。当前仅记录，未修改正式服务。

### 2026-11-16 共享目录、搜索与转发验收

- 19:07 已部署共享目录 `D:\FishHome\FishHome-ThinkBook-OneClick-Repair-v2-2026-11-12\dev\astrbot-4.29.4\data\workspaces\FishQQ_FriendMessage_<主人QQ号>\shared`。两路 Bridge 可直接接受 `shared/文件名`，仍保留原两路工作区和登记素材。首层 CSV、Markdown、TXT、JSON 会直接出现在素材名单，已知子目录可用相对文件名，拒绝上一级、链接及硬链接。产物写 `shared/output`。控制台「ThinkBook 远程控制 → 自主生活与主动联系设置 → DSH 文件素材」显示路径。Runner Modify ACL 只在共享目录新增；现有 AstrBot 目录规则保留。
- 正式 DSH 启动脚本将自带 Node 工具目录加到其进程 PATH，并让转发 WebUI 使用浏览器目录选择。已用同一浏览器 RPC 检查目录列表、创建临时子目录、查询包注册表信息，三个接口均返回成功。未安装任何第三方插件。浏览器已有标签页需刷新，旧票据失效时从远程控制台重新点开。
- 日志确认历史搜索调用超过 AstrBot 的 120 秒工具时限。网页搜索现在把实际 Go 模型任务的时限控制在 72 秒，主人私聊工具清单在就绪时显式提供 `df_web_search`。正式默认搜索使用 `Python pathlib Path.glob official docs` 返回 200 与 `docs.python.org` 来源。未发 QQ。
- 首次启动等待过短触发自动回滚，延长 AstrBot 启动窗口后部署成功。NapCat 与计费进程保持原 PID。回滚工件 `%LOCALAPPDATA%\FishHome\manager\before-dsh-shared-workspace-20261116-190704`。本地全量回归 1002 项，跳过 19 项。
- 两路正式 DSH 都从各自会话读取了新建且未单独登记的合成 CSV；独立 `FishHomeRunner` 写的文件也由 AstrBot 运行账户读到。逻辑路径向父目录跳转已被拒绝。第一次完整 Go 试跑返回上游 401，确认是计费网关所加载的 Go 凭据与控制台保存的不同。在两路空闲时重启计费网关并回读确认已加载现有配置；没有修改任何密钥。
- 随后完整 Go 文件任务通过。它读取未登记的共享 CSV、正确引用 `shared/` 来源，把可回读的报告写入 `shared/output`，收到 5 笔 Go 订阅回执。验收用临时输入和产物已清理。

台式机受托回归 5 项通过。运行中暂停、取消和续跑补丁，以及 `/go额度` 显示百分比的补丁仍在工作区。最后一次更新在备份旧文件时因暂存模块被导入即执行而退出，未覆盖正式文件；已加入口保护。下轮从 `payload/update-thinkbook-file-controls.py` 重跑，使用上一轮的交互会话暂存方式。暂存根需包含 `deploy-thinkbook-go-files.py`、`payload/deploy-thinkbook-video.py` 和本次更新的四个插件源文件。脚本会核对正式两路空闲，保留回滚副本，只重启 AstrBot。回读运行后再做集中验收。同时确认 Go 使用范围的实际回执、运行中取消的 DSH 终止事件及真实 Windows 窗口。

## ThinkBook 上的 DSH CLI

用 `ssh -t fishbook powershell.exe -NoLogo` 进入 FishHomeOps PowerShell。现场已核实版本和以下路径：

```powershell
$dsh = 'D:\FishHome\FishHome-ThinkBook-OneClick-Repair-v2-2026-11-12\dev\dsh-0.2.2-rc.1'
$node = Join-Path $dsh 'runtime\node.exe'
$cli = Join-Path $dsh 'node_modules\@deepseek-ai\dsh\lib\bin.js'
& $node $cli --version
& $node $cli --help
```

用 `& $node $cli tui` 打开当前用户的终端界面。插件安装入口为 `& $node $cli plugin --profile web add <package>`。CLI 按调用用户的 `DSH_HOME` 查找该 profile。正式自主和受托都由 FishHomeRunner 启动，分别设置 `%LOCALAPPDATA%\FishHome\DSH` 和 `%LOCALAPPDATA%\FishHome\DSH-Commission`，并叠加随部署启动的 patch。在 FishHomeOps SSH 会话安装个人插件不会安装到这两路。

升级时从 `$dsh\runtime\npm.cmd` 在新的暂存目录安装锁定版本，例如 `npm.cmd install --prefix <暂存目录> --save-exact @deepseek-ai/dsh@<版本>`。先通过新版本的 CLI 和原有 patch 验证 Remote API、两类文件任务及插件兼容性，再切换控制台服务路径，保留旧目录用于回切。两路正式服务路径目前由控制台定义。[正式启动脚本](../deploy/dsh_bridge/Start-DSH-Compute.cmd) · [控制台服务清单](../deploy/desktop_manager/manager.py#L147)

## 权限矩阵

| 级别 | 实际范围 | 实现和验收 |
| --- | --- | --- |
| 日常自主 | 个人项目和产物目录、自建工作说明、只读机器资源、自己登记的测试应用 | 账户及 ACL 授予个人工作区写入。作业记录来源、启动 PID、执行路径及创建时间；只读资源结果包含实际可用内存。对自己登记的空闲应用可请求正常退出；窗口拒绝、状态不明或仍有未完成工作时保留。 |
| 条件自主 | 从已批准的来源安装用户级依赖；在独立环境验证个人工具；只读获取已获准媒体；专用桌面内已授权应用 | 已实测的离线 wheel 可先复用。将工具依赖和桌面应用限制落实到独立账户、应用许可和进程边界；只读媒体方法复用现有伴侣。工具目录可回退版本。 |
| 主人批准 | 系统服务、账户、提权、额外目录和网络访问、新第三方扩展权限、公开操作、付款或不可恢复删除、向外部服务新增上传类别 | 沿用现有授权，批准记录关联具体任务和动作。先给出影响对象、可恢复性和未保存工作。 |
| 核心保护 | AstrBot/FishHome 安装根、正式人格、LivingMemory/Affective DB、QQ、联系闸门、网关及预算凭据、控制台 | 保持 FishHomeOps 持有的运行与控制目录，FishHomeRunner 继续做后台委托，FishHomeCompute 沙箱只获分配的任务输入。拟接 GUI 的账户没有控制台、QQ、预算文件或核心写权限。现有实测范围为指定样本，GUI/插件引入后补反向校验。 |

## 接入顺序

1. **复核现有计算通道。** 对正式两路先做只读工具枚举，然后用同份小型自制 CSV 完成隔离验收，覆盖任务 ID、实际 `run_python` 工具事件、产物、归账、重启后的状态、失败与交付。涉及真实模型的试验单独使用批准的测试额度。取消先复用提交前取消；运行中只有 DSH 返回终止回执才记为已停止，已执行的动作不自动回滚。关闭 `life_compute_enabled`、`commission_compute_enabled` 并重启两路即可回到文件预设。
2. **验证 Windows 桌面。** 在测试目录和独立预设尝试官方原生 Windows provider，通过与肥鱼相同的 Remote API 枚举并操作一扇普通测试窗口。Controller 按桌面串行安排观察、操作和回读，并在窗口变化或锁屏时退出，避免与运维桌面争焦点。如果官方许可每会话需要确认，保留它，应用管理另走下条；当前版本适配或图像路由若失败，可用应用专用 UI Automation 工具对测试目标验证。回滚是卸载独立预设或关闭该工具入口，现有 DSH 仍继续服务文件任务。 [Windows 提供者](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/experimental/cua-windows/README.md)
3. **软件和内存。** 先提供 CPU、可用 RAM/磁盘及个人任务清单。闭环验证登记启动、查忙闲、正常关闭、退出回读。先用独立样例程序验证有未保存内容时保留，再用 16 GB ThinkBook 测活动并发与模拟器共存。限制从已登记进程及作业组导出，核心进程列表由控制台服务依赖确定。关闭工具后仍保留人工控制台。
4. **个人 Agent。** 工作说明存个人工作区，启动新任务时读取指定版本并记录版本及实际生效的预设。权限来自宿主选定的固定工具清单。第一阶段只导入工作说明正文，第三方可执行扩展进入单独的无凭据测试 profile。发布、卸载和回退基于个人目录，不改 `.dsh` 主机配置。OpenCode 的 Agent 配置单独核对，不按 DSH persona 文件直接装载。 [OpenCode Agents](https://opencode.ai/docs/agents)
5. **让 DSH 自己取媒体证据。** 优先让 DSH 的任务工具调用现有伴侣的受控预处理接口，由 DSH 自选片段、拿元数据/字幕/转写和帧证据、追加一次取样。先用短视频验证，发给肥鱼时只传摘要、来源、时间戳和按需引用。现行路由无法传图就保留字幕分析及未观察画面标记，或用已批准的专用视觉工具返回有来源的观测。记录为「委托结果」；本人另行观看时由原活动工具记录。关闭媒体预设恢复原文件工具。 [媒体伴侣](../deploy/bili_activity/service.py#L71)
6. **Go 接入。** 使用独立 profile 对照实际模型、会话标记和订阅回执。现有主模型及两个 DeepSeek 账本照常运作，Extra Usage 由用户在 Go Console 设置。 [DSH 集成](https://opencode.ai/console/integrations/deepseek-harness)

第 2、3 步可并行验证，避免把软件启动和关闭系在 GUI provider 上。首轮只读核查完成后，先确认窗口、资料外发和订阅的范围，再进行相应的写入和真实验收。
