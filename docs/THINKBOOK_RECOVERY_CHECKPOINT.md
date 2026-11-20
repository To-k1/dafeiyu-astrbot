# ThinkBook 快速还原检查点

2026-11-13。目标为「台式机本地源码和一键安装器 + ThinkBook 私有运行状态还原包」。台式机保存归档，ThinkBook 继续是唯一正式运行实例。

## 已取得

- 在线原生 ZIP `thinkbook-online-private-20261113T033133Z.zip` 在台式机 `D:/Share/code/FishHome/backups`，包含五份原生库，包括 LivingMemory 的私聊库。校验及独立目录恢复已通过。
- 停写状态 TAR `thinkbook-astrbot-state-20261113T033340Z.tar` 已经拉到台式机 `D:/Share/code/FishHome/backups/thinkbook-recovery-20261113` 并核对传输完整性。它包含整个 AstrBot `data`，包括核心会话库、插件状态、向量索引和本机配置。此包含敏感数据，保存在私有备份目录。尚未解包演练恢复。捕获后已重启 ThinkBook 计费与 AstrBot，确认 OneBot 自动重连。
- 整套程序、模型和下载缓存复制已中断。远端 `%LOCALAPPDATA%/FishHome/full-copy-staging` 和台式机上述恢复目录可能仍有该步骤的临时文件。只清理经核对属于这次程序复制的 `thinkbook-program-files-20261113.tar` 或其 `.partial`。
- 台式机控制台「备份与记忆」已新增「生成 ThinkBook 还原包」。该入口通过 `fishbook` 拉取，成功后在历史备份中显示。2026-11-13 的 `thinkbook-recovery-20261113T092729.770820Z.zip` 已在隔离目录解包，82 个文件、11 个 SQLite 数据库通过检查。备份后十个服务均运行，NapCat 与 AstrBot 的 OneBot 连接已重新建立。
- 普通备份修复了 ThinkBook 安装包没有 Git 提交号时的失败。已从 ThinkBook 控制台生成 `thinkbook-backup-20261113T150256.874300Z.zip`，与当日自动快照一起同步到台式机。普通包在隔离目录恢复了四个原生数据库，原私有还原包隔离解包校验了 87 个条目。
- ThinkBook 托盘按北京时间每日保存一份普通快照，当日已有已校验的手动快照时可复用。台式机托盘启动后及每 15 分钟补拉，界面提供手动同步和最后校验成功时间。仅自动快照按日、周、月轮换。注册 Windows 计划任务被拒绝，自动化随两台机器的托盘运行。ThinkBook 已配置登录时启动，跨日触发和断网后自动补拉尚待现场观察。
- 新版安装器从台式机当前源码和旧包的固定依赖重建。Windows 隔离演练复验了损坏缓存、下载中断、半成品 Python 环境，以及 AstrBot 补丁的初次应用和重复运行。Ollama 官方下载在本次演练中超时，继续使用台式机上同版本的程序文件，并跳过重新下载 `bge-m3`。已有普通备份和含 87 个文件的私有还原包分别在隔离目录重新解包并校验。
- 隔离安装走完六个阶段并编译 FishHome.exe。控制台识别 10 项服务，AstrBot 面板返回 HTTP 200，活动插件、Affective Core 和 FishAffectBridge 加载成功。空配置下 LivingMemory 尚未生成数据库。改用解包后的原生库验证，原版房间和星海均返回 HTTP 200。未在 ThinkBook 正式目录执行覆盖式还原。
- 2026-11-13 23:56 生成并拉回 `thinkbook-recovery-20261113T155613.361782Z.zip`，重新解包校验 111 个文件。备份后托管服务的端口均恢复，OneBot 重连。普通备份通过真实 SSH 从空目录补拉，重复执行保留首次校验回执。11 月 14 日零点后，ThinkBook 自动生成普通快照 `thinkbook-backup-daily-20261113T160229.456050Z.zip`，台式机于 00:03 收到。
- 台式机控制台新增「导出 ThinkBook 记忆」。使用正式机现有的只读 JSONL 和 Markdown 导出，校验传输后写入台式机 `exports`。已从新版控制台入口实际保存，验收时活跃记忆和项目各为零条，包内无私聊原文。
- 用最新还原包的隔离副本执行完整恢复入口。实际替换了隔离环境的 AstrBot 状态、NapCat 配置、控制台状态与 Runner 工作区，保留原插件及回退副本。此演练模拟了 Windows 账户、DPAPI 与停机检查，正式机原账户和原路径覆盖恢复仍待实测。

## 续做

1. 安装器支持在原 ThinkBook 账户和安装路径读取 `Restore-FishHome.zip`，程序初始化后恢复状态。待安排正式机维护窗口，生成当前私有还原包，再验证原账户、原路径的覆盖恢复和重连。新 Windows 用户或系统重装后需要重填 DPAPI 绑定凭据，QQ 与雷电登录由外部组件处理。
2. 台式机房间和星海已切至 ThinkBook 只读快照，另见 [控制台记录](DESKTOP_MANAGER.md)。

## 2026-11-18 正式恢复前核查

- 正式机现用的自主和受托文件工作区在 `D:/FishHome/workspaces/dsh/`，共享区及 QQ 收件箱在 `D:/FishHome/workspaces/shared/`，均在一键安装目录之外。`scripts/thinkbook_recovery.py` 的 v1 清单只打包 AstrBot 运行数据、NapCat 配置、指定控制台文件和 Runner 用户目录中的四个旧目录。新工作区不在该清单。正式恢复前要补齐并独立恢复这三处目录，再验证当前 DSH 工作区配置和普通文件可读；不能把 v1 包的数据库恢复验证当作新工作区也能重建。
- 本次核查只读取正式配置与备份元数据。最近的普通自动快照在 11-18 00:14 生成。正式覆盖前仍需要维护窗口内的最新私有还原包及用户确认。

## 新版工作区还原

- 新增 v2 私有还原包，覆盖安装目录旁的 `workspaces/shared`（含 QQ 收件箱）、`workspaces/dsh/life`、`workspaces/dsh/commission` 与已有的 `workspaces/dsh/projects`。清单保存空目录；旧 v1 包可继续用，恢复时保留现有外部工作区。
- 同账户原路径恢复会保留原工作区目录作为回退。新工作区在写入文件前建立专用权限，Runner 可使用工作区；有已配置 Compute 时，仅给其项目目录写权限。
- 台式机隔离覆盖实测了目录、文件、原版本回退副本和 v1 兼容。ThinkBook 隔离目录以真实 Runner 核对三处已恢复的文件，以真实 Compute 写入项目样例并确认共享收件箱拒绝读取。正式目录覆盖仍等主人确认维护窗口。

## 2026-11-18 20:49 正式恢复验收

- 主人授权后在同一维护窗口停下 AstrBot、两路 DSH、Compute、B 站伴侣、计费、NapCat、房间与星海。在 ThinkBook 原账户生成 `thinkbook-recovery-20261118T124550.461066Z.zip`，拉回台式机 `D:/Share/code/FishHome/backups/`。包共 6962 个文件，含 38 个工作区文件、13 个工作区目录。写入窗口在抓取和恢复之间保持关闭。
- 首次抓取被 Compute 私有项目的 `.fishhome-wheels` 拒绝。识别为可重建缓存后，仅排除项目根的 `.fishhome-wheels` 和 `.fishhome-deps`，重试成功。项目资料、运行状态和共享附件进入私有包，缓存未被复制或改权限。
- 在相同的 FishHomeOps 账户和安装路径执行 v2 覆盖。逐字节核对全部 38 份工作区文件，13 个目录全在。Runner 能读回三处工作区；测试角色 A 的「第一段合作会话」仍选中，QQ 长报告和附件的原任务及补发回执仍在。首次运行后的检查脚本误把工作区根拼接两次，已更正检查路径并复核实际目录。
- 两路 DSH 真实列出 7 和 35 个会话，OneBot 已建立连接，星海 `/` 与房间 `/room` 都返回 HTTP 200。此次恢复未手动触发任何模型任务。原工作区、AstrBot 数据和控制台文件按 `before-restore-dc15dfebd7` 保留。已将 v2 恢复脚本装入 ThinkBook 项目，旧工具位于 `%LOCALAPPDATA%/FishHome/manager/before-recovery-tool-20261118-204942`。
