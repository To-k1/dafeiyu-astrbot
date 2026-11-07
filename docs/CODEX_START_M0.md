# 给 Codex 的第一轮完整执行指令：只执行 M0

你正在 Windows 台式机上工作。已定根路径 `%FISH_HOME%`（用户设置值为 `D:\Share\code\FishHome`），**Codex 项目工作区必须是 `%FISH_HOME%\repo`**，不要将 backups/exports/私人目录纳入 Git，也不要假设 Linux `fishvm` 是必经服务器。

先读 `AGENTS.md`、`docs/DECISIONS.md`、`docs/PRD_FISH_HOME_v0.9.md`、`docs/UPSTREAM_REUSE.md`、`docs/GITHUB_FORK_BOOTSTRAP.md`、`docs/ROOM_AND_REMOTE_VIEW.md`、`docs/IMPLEMENTATION_PLAN.md`、`docs/ACCEPTANCE.md`。

**用户明确授权用已登录的 GitHub CLI 自行完成公开 Fork、clone、分支、正常 commit 和 push。** 请执行但先审查磁盘已有资料，避免覆盖、强推和删除。目标上游为 `https://github.com/whyever77/dafeiyu-astrbot`；`origin` 必须是实际认证账号下的 Fork，不能猜账号或仓库 URL。`main` 跟随上游，`fish-home` 承载定制，后续 `feature/*` 开发。若本地 `repo` 非空或已有旧文档先保护、备份、逐文件比较后再合并，不可清空。

本轮 M0 只做以下事项：

1. 检查 Windows 当前 `$env:FISH_HOME`、`git`、`gh auth status`、工作区/git 状态；创建/核对公开 Fork、`origin/upstream`、本地 `main`/`fish-home`；将当前开工文档只提交到定制分支。允许非破坏性 push，但不得 `reset --hard`、`push --force`、删仓或误覆盖。
2. 锁定上游 commit 和发行组件版本，按源码检查原版 Windows AstrBot Launcher / NapCat / deploy-kit、LivingMemory、LifePlanner/Goals/Workshop/跨日项目、原版 B站公共互动权限与 Android PhoneMCP/ADB。区分公开文档、上游本地测试、我们电脑实际测试；不得把 README 功能表当运行证明。
3. 重点确认原版 `deploy/memory_observatory` 的 `/room` / 记忆星海代码、Windows 本地服务和 `--livingmemory-ssh` / `--life-ssh` 读取器的 Linux 假设。方案是在**台式机本地运行界面**、未来通过 Windows OpenSSH `fishbook` 读取 ThinkBook 的只读状态。M0 只完成证据审计，不重写房间。
4. 核验 DSH 官方 Windows 路线和 `astrbot_plugin_dsh_bridge` 的固定版本、端口/鉴权/usage/headless/BrowserTools/重启恢复与接入钩子，列出真实可复用和需要薄适配的范围。**不得开发新 Fish Coordinator**。
5. 查明 Windows 原生运行端的数据源/backup API，给出 dev/prod 隔离、ThinkBook→台式机完整备份与 JSONL/Markdown 导出/房间观测快照的三条数据流；不要直接复制写入中的 SQLite DB。
6. 审计 Fish Extensions 费用门控和公共平台写入总开关的可行性：交互和自主各默认 ¥5/预算日、北京时间 04:00 切换、管理员独立调额、官方 `deepseek-flash`、非紧急高额任务低谷延期；若没有真实 pre-call hook，不得承诺硬上限。公开互动默认关闭、开启后仍尊重平台审批/额度，任意 Shell/HTTP 绕过风险须明确。
7. 现场核对目标 ThinkBook 是 Windows 11 / 16GB 的**计划目标**，不假装 SSH/Android/QQ 连接已完成。用户选择“接电不休眠、重启人工登录后恢复”，不要求安装 WSL2/重启 VMware。开发测试同 QQ 默认、记忆不迁移默认，但都必须可配置。
8. 输出 `M0_REPORT.md`，含版本 SHA、命令、Git push 状态、源码路径/符号、Windows 数据地图、原版能力分级、薄适配设计、M1 最小任务/验收、风险、回滚步骤。**结束后停止，不直接开始 M1，不触发真实 QQ/API 计费。**

不把 SSH Key、GitHub token、DeepSeek Key、QQ Cookie、记忆原文或用户文件推送至公开 Fork。用户已决定的产品参数不再重复问；只有真实执行必需的授权动作再单独说明。
