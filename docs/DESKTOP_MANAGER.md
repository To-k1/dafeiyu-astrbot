# FishHome 台式机托盘控制台

双击 `%FISH_HOME%\dev\Start-FishHome.cmd` 启动全部组件并打开 WebUI。直接打开旁边的 `FishHome.exe` 可先查看现有状态，再按需启动。右键 FishHome 托盘图标可打开 WebUI、启动全部或退出控制台。

控制台支持 AstrBot、NapCat、Ollama、DeepSeek Harness、PhoneMCP、雷电实例 0、B 站只读伴侣、房间与记忆星海、人民币计费服务。已识别为运行中或正在启动的组件会跳过。可以单独启动、停止或重启识别到的服务。退出控制台时服务继续运行。雷电仍使用正常的图形窗口。

## 切换正式与测试人格

在 **ThinkBook 本机控制台**，或从台式机打开 **ThinkBook 远程控制**，进入「备份与记忆」。按钮旁会显示当前人格 ID，点「切换到测试人格」或「切换到正式人格」。

- `fish-home-v3.0` 为测试，`feiyu-v3.0` 为正式。成对使用同版本正文和工具配置。切换会保存回退副本、新建主人私聊上下文，并短暂重启 AstrBot 和正在运行的星海。旧 QQ 对话仍可在 AstrBot 回查。
- QQ、记忆与小队的真实验收先切到测试人格，完成后切回正式。星海默认打开当前人格分组，其他分组可从下拉列表选择。房间沿用主人范围的生活账本和心境；小弟归档状态由名册管理，要复用旧小弟时按需恢复。
- 2026-11-19 已切至 `feiyu-v3.0`。保留旧人格下 41 条 LivingMemory 和小弟库 21 条记忆，旧名册 7 位均归档。正式星海默认组初始为空，ThinkBook 本机与台式机远程读取均验证通过。状态副本位于 ThinkBook 的 `%LOCALAPPDATA%\FishHome\manager\before-persona-mode-20261119-004239-052042`，程序副本位于同目录的 `before-persona-console-20261119-004219`。

## 小弟角色卡

在 ThinkBook 本机控制台或台式机的 ThinkBook 远程控制中展开「DSH 小队与任务」。出厂资料使用 UTF-8 的 Markdown 或纯文本。

- 「角色卡」写身份、人格和说话方式。上传 `.md` 或 `.txt` 并预览后导入。新小弟可以手填名字，也可沿用文件名。选中已有小弟时只更新人格版本。点该小弟旁的「导出角色卡」可下载当前正文。
- 「单人故事」选一位小弟，上传或下载他的出厂故事正文。「他人印象」从同一入口选择大肥鱼或任一小弟，上传或下载对应文本。作者可直接在正文中提及任何角色，模型按原文理解。
- 「多人故事」上传 `.md` 或 `.txt` 并勾选参与者，列表包含大肥鱼。故事不计入单人故事和他人印象的 12000 字上限。控制台上传仍使用 256 KiB 的请求大小上限。保存后可下载原正文。大肥鱼按需用 `df_story` 查当前分组的故事，再用 ID 读取正文。她自己的印象用 `df_story(action="impressions")` 读取，长文用 `next_offset` 继续。正式和测试人格共用大肥鱼的这份出厂印象。
- 小弟在聊天、派活和组会中会看到出厂资料作为角色背景。实际聊天、任务、组会和记忆继续通过原记录查询。
- 在折叠的「历史记录迁移」可导出完整 ZIP，或把旧档案导入为新小弟。这个入口保留人格版本、历史聊天、任务、组会、长期记忆和关联的出厂资料。
- 2026-11-19 已部署「他人印象」到 ThinkBook，确认控制台入口、帮助、角色卡模板及读取接口可用，AstrBot、OneBot 和两路 DSH 已恢复。源码回退副本在 ThinkBook 的 `%LOCALAPPDATA%\FishHome\manager\before-impressions-20261119-025325`，工作区使用指南原文副本在同目录的 `before-impressions-guide-20261119-025512`。
- 2026-11-19 已部署纯文本角色卡、单人故事、多人故事和统一的他人印象入口。确认了现有测试小弟的角色卡下载及文本读取接口。源码和使用指南的回退副本位于 ThinkBook 的 `%LOCALAPPDATA%\FishHome\manager` 下，分别为 `before-role-texts-20261119-032305` 和 `before-role-texts-guide-20261119-032400`。
- 2026-11-19 已部署多人故事字数限制修复。在 ThinkBook 上用隔离数据验证了 16006 字正文的保存和分页回读，QQ 已重连。源码回退副本为同目录的 `before-long-stories-20261119-033529`。
- 副本有自己的小弟 ID 和会话 ID。历史作为只读资料可从小弟记忆的来源回查，工作进程不会重跑旧任务或补发旧 QQ 通知。聊天首次续接某段导入会话时会参考那段最后几条交流，向 DSH 新建会话，旧 DSH 附件、项目目录和原生会话文件仍需整机还原包或独立转移。
- 有长期记忆时，导入状态先显示进度。副本完成原生 LivingMemory 写入和回读后可用，旧记忆 ID 和出处保留为来源信息，小弟的记忆与大肥鱼自己的 LivingMemory 保持各自归属。
- 角色和故事均可手动归档、恢复，在各自的「已归档」折叠区查找。「测试」是独立标识，归档和恢复都会保留。新角色和故事以创建时选用的正式或测试人格标记。

「近期任务」和「近期组会」有各自的折叠区。默认跟随当前人格，也可从下拉框只读切换到另一组。正式和测试各自提交、查询和调度。QQ 和后台调度跟随实际人格切换。

2026-11-19 已把首次切换到正式人格之前的 20 条任务、2 场组会和 7 位旧角色标为测试。原记录、角色名和归档状态保留。ThinkBook 和台式机远程入口均核对了分组。更新前源码和标签原值保存在 ThinkBook 的 `%LOCALAPPDATA%\FishHome\manager\before-team-library-20261119-021453`。

2026-11-19 已上线 ThinkBook。正式 QQ 已重连，两路 DSH 均已恢复。台式机远程入口已实测角色 ZIP 下载、上传与预览；ThinkBook 用隔离样例实际恢复并检索了一条小弟记忆，回指原话成功。正式名册未新增测试对象。更新前源码留在 ThinkBook 的 `%LOCALAPPDATA%\FishHome\manager\before-dsh-copy-20261119-014702`。

## 台式机试运行

1. 双击 `Start-FishHome.cmd`。组件陆续启动后，在控制台运行「试运行自检」。自检核对唯一的 QQ 主人私聊、OneBot、DSH Bridge、Ollama 向量模型、房间与 Android 连接。
2. 在「FishHome 本机设置」保存机器人快登 QQ、Bridge 主人 QQ、雷电安装目录和 Ollama 模型目录。更改启动选项后重启对应服务。保存主人前，先在 AstrBot 设置唯一 QQ 管理员及其私聊白名单，关闭群聊和私聊的管理员豁免。NapCat 的登录验证和 OneBot 参数仍在 NapCat 面板设置。
3. 在「人民币预算与凭据」保存 DSH 专用标准账户密码、DeepSeek 官方 Key，查看并调节两个账本。可选择只影响本预算日或修改默认额度。
4. 「自主生活与主动联系设置」可修改每日联系次数、未回复次数、留白和冷却时长、免打扰、周期活动与模型上限、峰期 DSH 阈值、项目/工坊上限和 DSH 素材名单。台式机的「ThinkBook 远程控制」修改 ThinkBook 的配置，本机控制台修改本机的配置。素材要先放入对应的 DSH 工作区。
5. 「使用帮助 · 主人 QQ 与 DSH」收录正式 DSH 派发、旧报告查询、工作区与运行代码、`/go额度`、`/dsh ping`，以及提醒、主动联系、语音和截图等用法。
6. 「各模块设置与日志位置」可打开对应目录。AstrBot 的模型、LivingMemory、工具权限在 AstrBot 面板管理。

2026-11-16 已更新 ThinkBook 正式托盘的 `manager.py`、`index.html` 及其配置工具 `deploy/bili_activity/ops.py`。首次接口检查发现正式机的配置工具缺少三个默认值，已回退后按对应源码补齐重试。正式接口随后读回 20 项活动配置、两路素材设置和 QQ 帮助，主动联系日上限仍为 3。只重载托盘和控制台后端，AstrBot、NapCat、两路 DSH 及计算服务的监听进程未变化。原版三文件保存在 ThinkBook FishHomeOps 的 `%LOCALAPPDATA%\FishHome\manager\before-fishhome-console-1794761598113`。台式机以独立测试端口完成 SSH 转发回读；已经打开的远程控制页面需从托盘重新连接以取得新会话。

同日新增「人民币预算与凭据 → OpenCode API Key」。本机和远程控制台均使用当前所选主机 Windows 账户的 DPAPI 保存，页面/API 仅显示保存状态。预定模型为用户指定的 `deepseek-v4.1-flash`。ThinkBook 已上线并回读保存入口，未填写 Key，既有模型路由和运行服务未变。覆盖前两份源文件在 `%LOCALAPPDATA%\FishHome\manager\before-fishhome-opencode-1794763103855`。

### 台式机与 ThinkBook 双向设置同步

- 在台式机本机托盘打开 WebUI，展开「台式机与 ThinkBook 设置同步」，检查两端设置。首次差异默认采用 ThinkBook，可逐项切换；点「确认首次同步」后才启用。之后同一字段两端发生不同修改时，这一项会保留两端值，直到在此处解决。其他字段继续同步。
- 范围是主动联系的四项参数、免打扰、活动用量上限、项目和工坊上限，以及默认交互和自主人民币额度。周期启停、工具和模型开关、素材、凭据、当日临时额度、用量和正式活动数据库各自保留。默认额度同步只修改对端的默认条目。
- 台式机托盘运行时通过现有 `fishbook` SSH 每分钟检查。会话断开后自动重试，重启后依据最近共同设置逐项比较。共同设置记录只存在台式机 `%LOCALAPPDATA%\FishHome\manager\shared-settings-baseline.json`。
- 2026-11-16 已发布 ThinkBook 接口，并在台式机用真实 SSH 完成首次只读预览。唯一差异是台式机免打扰为「未配置」，ThinkBook 为「无免打扰时段」。同步尚未启用。更新前控制台文件备份在 ThinkBook 的 `%LOCALAPPDATA%\FishHome\manager\before-fishhome-settings-1794763681709`。回退时先退出控制台，再恢复旧入口文件及重新打开控制台。

「深海工作台」首页的「去看看肥鱼」打开原版房间，旁边可打开记忆星海、AstrBot、NapCat 和 DSH WebUI。右上可切换深海模式与黑夜模式。

「备份与记忆」可在线生成本机 SQLite 归档、导出可读记忆并查看桌面备份历史。归档写到 `%FISH_HOME%\backups`，导出写到 `%FISH_HOME%\exports`。在线备份分别对长期记忆、活动账本和已安装心境中枢的 `affective.db` 取一致性快照，排除向量索引和原始聊天库。需要含索引的停写归档和隔离恢复时使用 [fish.ps1](../scripts/README.md)。

当前试跑配置只接收管理员 QQ 私聊。公共写入和自主活动处于关闭状态。公共写入总开关在「FishHome 本机设置」，平台开关、审批和额度仍在各模块。控制台启动的 B 站伴侣始终使用只读配置，当前试跑无需导入 Cookie。Ollama 使用当前 Windows 用户的持久模型目录 `%USERPROFILE%\.ollama\models`，通过本机 Vulkan 路径加载 `bge-m3`。

## 人民币计费

- 管理员 QQ 私聊发送 `/额度`，可查看交互与自主两本人民币账，默认各 ¥5，北京时间 00:00 换日。
- 更新官方 DeepSeek API Key 时，在「本机凭据与计费接入」保存后重启「人民币计费」。Key 由 Windows DPAPI 加密保存。待填写期间可查询账本，收费请求在本机返回 503。
- 台式机 AstrBot 的 Flash 请求进入交互账本，DSH 的 Flash 请求进入自主账本。两条路径依赖托盘启动时注入的进程环境。更多配置见 [M5 验收记录](M5_ACCEPTANCE.md)。

## 安装与维护

### 从台式机打开 ThinkBook 控制台

在台式机运行 `powershell -File deploy/desktop_manager/install-thinkbook-tray.ps1` 可编译独立的 `dev/FishHome-ThinkBook.exe`。双击后出现紫色「TB」托盘图标，右键可打开远程控制台、重新连接或退出该入口。退出仅关闭台式机上的远程连接，ThinkBook 的服务继续运行。

在台式机双击仓库 `scripts/Open-ThinkBook-FishHome.cmd`。台式机需有可用的 `fishbook` 公钥 SSH，ThinkBook 的 `FishHomeOps` 桌面需已打开 `FishHome.exe`。页面标题会标明 ThinkBook，服务操作在 ThinkBook 执行，模块页面通过本机回环端口转发。关闭启动窗口会结束该次远程入口。

脚本默认在 ThinkBook 的 `D:\FishHome` 下查找唯一安装目录。其他目录可用 `--root <ThinkBook 安装目录>` 指定。传 `--check` 只读取远程状态。台式机的 `%LOCALAPPDATA%\FishHome\manager\thinkbook-qq-primary` 文件标记 ThinkBook 为正式 QQ 主机。标记存在且台式机 NapCat 未运行时，远程控制台允许启动全部和启动或重启 ThinkBook NapCat。切回台式机时，先停止 ThinkBook NapCat，再移除标记，最后启动台式机 NapCat。

台式机本机控制台打开 `127.0.0.1:8767` 的本地展示，托盘通过 `fishbook` 每 60 秒拉取只读摘要。ThinkBook 远程工作台的按钮通过同一 SSH 连接打开 `127.0.0.1:18767`，由 ThinkBook 的观测服务直接返回房间、星海及只读查询。11 月 14 日在台式机验收两页、近况刷新和星海筛选。手机的局域网入口另行处理。

2026-11-13 已启用上述台式机配置，并通过实际快照和隔离断线试验验证。本轮只重载 ThinkBook 托盘以启用 DSH 票据重试，AstrBot、NapCat 和两路 DSH 的进程保持原样。两路会话列表与 OneBot 连接均可读。远程 QQ 启动和重启通过模拟接口验证，实际远程重启留到维护时段。

2026-11-12 的 ThinkBook 离线验收通过台式机入口完成。Ollama 的启动、重启和停止均成功，本机模型目录含 `bge-m3`。人民币网关以离线模式运行，两本默认额度各 ¥5。AstrBot 初次启动前已补做原生初始化，面板绑定回环，台式机通过转发收到面板页面。测试结束后停回被测服务，ThinkBook 的 NapCat 保持停止。DSH 凭据、PhoneMCP 实机和 QQ 正式切换待后续验收。

2026-11-13 在 ThinkBook 核对了已保存的 DeepSeek 和 Runner 凭据，补建现有 Runner 的 Windows 用户目录与文件工作区，验证该账户可访问 DSH 程序且读取 AstrBot 主配置被拒绝。自主与受托 DSH 均只监听回环，已用保存的 Bridge 票据分别读取会话列表。两条本地 Anthropic 路由分别匹配自主与交互凭据，交叉使用均被拒绝，探针未转发上游。Ollama 实际生成 1024 维向量，PhoneMCP 识别雷电设备并返回截图，B 站只读伴侣通过 MCP 握手。

同日配置 ThinkBook 的唯一主人好友私聊、v2.3.1 人格和 v2.2.1 回退、Flash 交互账本、LivingMemory 向量与总结提供商，以及 Affective Core 和 Bridge。AstrBot 已启动，小房间接入 ThinkBook 新建的真实数据源，脱敏房间快照已通过 SSH 读回台式机。ThinkBook 的 NapCat 保持停止，百炼配置、真实 QQ 和实际收费任务留待切换验收。现有 DSH 票据已手动刷新并验证。仓库的票据等待时间和重试改动尚未安装到 ThinkBook 托盘。

百炼 Key 和音色保存后，启用了单独的 `dashscope_tts` 提供商并重启 ThinkBook 的 AstrBot。启动日志确认模型加载和 `tts_speak` 注册。全局自动语音保持关闭。真实 QQ 语音合成与听写仍需在切换后验收。

ThinkBook 已完成 NapCat 登录后，确认台式机的 NapCat 未运行，再将 ThinkBook 的 OneBot 反向 WebSocket 配置到本机 AstrBot。回读配置并确认连接建立，主人好友私聊白名单仍仅有一项。2026-11-13，主人确认 ThinkBook 的 `/额度`、`/dsh ping`、普通私聊和语音正常。

ThinkBook 的首个真实主人文件委托记录为 `completed`、`sent`，保存 OneBot 消息 ID 与 DSH 用量。对应的五笔 Anthropic 请求均在交互账本结算。

2026-11-13 11:24:00（北京时间），主人确认收到了在 ThinkBook 上创建的定时提醒。活动库只有一条对应的 `sent` 记录，并保存 OneBot 消息 ID。

同日启用 ThinkBook 的 LivingMemory 写入工具。主人显式保存了真实文件委托的候选。最初的测试摘要在远程传输时因编码变成问号，主人通过 LivingMemory 原版编辑纠正，活动候选摘要也同步修正。独立新会话正确召回 `sample.csv` 和主人发起。截图快捷路径改用已有百炼视觉，主人实测收到图与具体画面描述。

ThinkBook 的自主 Flash 指向独立回环账本。已用无效请求验证自主凭据可达、交互凭据被拒绝。在隔离活动库调用真实 LifePlanner 和正式自主 DSH，单次样例投递生成 Markdown 与来源，原始 CSV 保持一致，写回目标、经历和一条待选记忆。正式自主账本对应当天五笔 Anthropic 请求均已结算，合计 ¥0.00433444。已部署的低谷续跑补丁允许周期活动恢复管理员已安排且到期的延期文件任务，仍以原 LifePlanner 选择和现有配额为准。离线覆盖高峰不投递与到低谷不重复创建任务。

同日完成一次正式机 B 站只读活动。受控单次运行通过生活侧联系账本向唯一主人发送 B 站推荐页截图。OneBot 回读图片，主人确认收到并打开。正式 `one_shot_verified` 按原验收工具写入。主人选择无免打扰时段和沿用原版额度主动分享。已启用生活决策、节律、周期调度与联系协调，调度器持久化了首次周期检查时间。公开写入保持关闭。固定文字消息与截图共享联系冷却，本轮只发送了验收截图。需暂停周期活动时在主人私聊发「暂停自主活动」，暂停发信可发「暂时别主动找我」。

清理前归档为台式机私有备份 `thinkbook-recovery-20261113T104504.179532Z.zip`，核对包含委托与推荐页两条测试长期记忆、原生向量索引和活动账本。下一轮待观察首轮自然调度、跨日续跑和 Windows 登录恢复，备份恢复演练后置。

同日 20:39 的自然活动已完成。主人报告的浏览 400 与 `df_screen` 返回图片后由文本计费入口拒绝相符。正式机现用 Flash 单独识别截图或最多四张带时间的抽帧，视觉调用走原交互或自主人民币账本，自主每轮最多一次。快捷截图仍沿用既有百炼配文。B 站导航新增仅在 B 站前台有效的返回操作，并读取返回后的界面。用纯色合成图验证正式自主 Flash 视觉和用量回执，QQ 已重连。部署前文件位于 ThinkBook `%LOCALAPPDATA%\FishHome\rollback\vision-20261113-222353`。

主人在 QQ 实测能描述 B 站推荐页，退出视频后回到推荐页。随后为 B 站伴侣加上已安装的 `imageio-ffmpeg` 回退，以及受信 CDN 镜像回退。重新读取同一公开视频的前 18 秒，ThinkBook 返回五张带时间的抽帧。22:32 主人私聊复验，肥鱼按帧时间返回画面与可见字幕，也返回该片段音轨转写。

开发源在 `deploy/desktop_manager`。从仓库运行 `powershell -File deploy/desktop_manager/install.ps1`，使用 Windows 自带 .NET Framework 编译器生成 `%FISH_HOME%\dev\FishHome.exe`。运行时复用开发实例的 AstrBot Python 环境。覆盖 EXE 前先退出托盘控制台。安装保留旧启动脚本 `Start-FishHome.before-tray.cmd`。`-InstallBudgetBridge` 更新计费 Bridge 并备份原源码。`-InstallActivity` 在 AstrBot 停止时更新活动插件源码并保留原插件副本。运行配置和账本仍在 `data\plugin_data`。本机日志、进程识别记录和加密凭据都在 `%LOCALAPPDATA%\FishHome\manager`。

WebUI 只监听 `127.0.0.1:6196`，由托盘打开的浏览器会话可操作服务。DSH 仍在 `FishHomeRunner` 下运行，托盘启动后会更新签名票据。NapCat 复用原脚本的快登配置。正式 ThinkBook 的路径和启动策略另行验收。

## ThinkBook 第 3 项补验 · 2026-11-14

- 正式机 B 站伴侣再抽取同一已验收视频的前 18 秒，返回 5 个帧时间点。活动库已有 2 条关联自主运行的视频经历，本次查得 0 条自主画面分析结果。两者分别记作媒体服务实测和自然运行待观察。
- Windows 启动目录的快捷方式指向正式 FishHome.exe，带 `--background --start-all`。00:29 正常重启并登录 FishHomeOps 后，十项服务自动启动，雷电就绪、OneBot 重连，两路 DSH 的票据稍后自动刷新，均能实际读取会话。台式机使用新 SSH 转发和正式控制台接口单独重启 NapCat，AstrBot 持续运行，OneBot 再次重连。重启前打开的远程控制入口返回 502，重新连接后可操作。
- 跨日项目和创作工坊正式开关关闭。项目重开数据库后保留阶段与版本、Windows 工坊导出和重复发送拦截均通过隔离测试。正式机仍未安装 Windows 工坊导出改动。
- 11 月 14 日关闭 ThinkBook 的 `life_rhythm_enabled`，维持生活决策和周期活动开启、免打扰为空。原配置由现有管理接口保存在活动目录的 `config_backups`。台式机房间和星海由本机 8767 端口渲染，数据通过 SSH 从 ThinkBook 拉取。高峰延期目前在 DSH 文件任务有执行逻辑，通用自主步骤的成本估算和偏好仍未验收。
- 待办：让手机及台式机可直接打开 ThinkBook 提供的局域网房间和星海，无需启动台式机房间服务。现有服务只绑定回环。复用 `scripts.m6_data.room_bundle` 已有的只读摘要，在 ThinkBook 增加局域网展示入口，验收房间、星海及快照更新时间。控制台管理端继续走本机或 SSH。额度降至 8% 时暂停，尚未修改服务或防火墙。

## 界面、图标和人格源码

- **控制台 WebUI** 的 HTML、CSS 和 JavaScript 都在 [index.html](../deploy/desktop_manager/index.html)。修改页面后刷新控制台即可预览。服务 API 在 [manager.py](../deploy/desktop_manager/manager.py)。
- **鲸鱼图标** 使用用户提供的 PNG 派生 [网页图标](../deploy/desktop_manager/fishhome.png) 和 [Windows 多尺寸 ICO](../deploy/desktop_manager/fishhome.ico)。[install.ps1](../deploy/desktop_manager/install.ps1#L27) 将 ICO 编译到 EXE，[Tray.cs](../deploy/desktop_manager/Tray.cs#L81) 从 EXE 读取托盘图标。
- **大肥鱼人格** 的当前版本与回退操作见 [人格目录](../persona/README.md)。课程内容另放在 [课程记忆-模板.md](../persona/课程记忆-模板.md)。

## 台式机实测

- 已编译并安装 `FishHome.exe`，替换本机一键启动脚本。8 个组件均通过本机状态检测。重开托盘与重复一键启动保留了既有服务进程。单独停止和重启房间后重新收到 HTTP 200，空闲 DSH 经单项重启恢复并重新签发 Bridge 票据。雷电使用原图形启动入口后通过 `emulator-5554` 的 Android 开机探针。
- 2026-11-09 12:00:22 已收到真实管理员 QQ `/额度` 回执，交互和自主各 ¥5，北京时间 04:00 换日。用户保存 Key 后，本机计费入口在线。AstrBot 和 DSH 已完成切换，两条零额度请求均在网关处被拦截，测试后恢复原额度。
- 台式机试运行的记忆归档、清理范围和最新验收结果见 [试运行检查点](M6_CHECKPOINT.md)。

### 本机切换与回滚

AstrBot 的 `deepseek` 提供商与模型配置均引用 `${FISHHOME_INTERACTION_TOKEN}`。DSH 使用 `m5-file-only.yml` 保留原文件权限并附加本机计费配置。托盘在 `metered_dsh: true` 时向 `FishHomeRunner` 进程注入自主凭据。固定 DSH 版本的 Web 配置模块 ID 为 `llm-deepseek`。DSH 重启时，控制台等待启动日志中出现有效连接票据后更新 Bridge。

切换前的 `cmd_config.json`、托盘本地设置和 DSH 启动脚本以 DPAPI 加密保存于 `%LOCALAPPDATA%\FishHome\manager\m5-cutover-before.bin`。需要回滚时先停止 AstrBot 与 DSH，再用当前 Windows 用户解密还原，重开托盘并启动两项服务。SQLite 账本与现有会话保持独立。
