# Fish Home · Windows 双机统一实施顺序 M0–M7（v0.7）

**Codex 第一轮只做 M0，完成报告即停止。** 每阶段进入下一阶段前需要用户查看审计报告和具体变更；不能用纯 mock 结果冒充 QQ/Android/DSH 实联。

| 里程碑 | 优先复用 | 允许新增 | 成功闸门 |
|---|---|---|---|
| **M0** | GitHub Fork、Windows 上游源码、原版房间/星海、Life/Goals/Projects、PhoneMCP、DSH Bridge | 文档、版本清单和无凭据探针 | Codex 自动 Fork/clone/branch（保护已有 repo），锁 Git SHA；源码证据、Windows 路线风险、dev/prod 数据地图与最小 M1 清单；不触真实 QQ/付费 API |
| **M1** | 原版 Windows AstrBot Launcher、NapCat、LivingMemory 安装；已有 Windows `deploy-kit` | 最小 `fish.ps1 doctor/status` 与 mock snapshot/verify/restore | 台式机可在 dev 环境复现启动；首次数据库一致性备份/隔离恢复；不依赖 WSL2/VMware |
| **M2** | 原版台式机房间/星海、Windows 安卓模拟器 + PhoneMCP/ADB、QQ/Flash/生活插件 | 只针对 Windows/路径的薄适配 | QQ 身份 dev 安全联调、来源/记忆召回、房间真实数据不混 demo、安卓 B站推荐首页/播放/ADB 操作；可分并行子项，资源不够不得阻塞其他业务 |
| **M3** | DSH 官方 Windows 版本、Bridge | 最薄 task adapter | 真正执行一个受限文件/浏览器任务；产物存在、来源和 usage 可核对；记录断线与取消结果 |
| **M4** | 原版 LifePlanner/Goals/Workshop/跨日状态 | DSH 最小 checkpoint / result adapter | 原版选题 → DSH 执行 → 成果回记原版；重启不中断项目或重复副作用，`needs_review` 正确处理 |
| **M5** | 上游 budget、B站互动开关/审批、调度钩子 | 人民币费用门控、峰谷偏好、全局公共写入总开关 | FakeClock/跨日/模型与工具 usage 测试；双层公开写授权验证；无 pre-call hard hook 时关闭无人值守付费运行 |
| **M6** | Windows OpenSSH、原版房间 SSH 读取器、备份 API | `fish.ps1 deploy/backup/export-memory/verify/restore/room-sync`、Windows 定时快照、只读远程 Windows 适配 | ThinkBook 接收锁定 SHA（不清空 prod），台式机小房间可查看真实 ThinkBook；全量/可读导出分流；`none/selective/full` 三种迁移模式验收；恢复演练 |
| **M7** | 人格、分享协调、日志/原版状态 | 自启动编排、诊断/告警、体验微调 | ThinkBook 登录后自动恢复 AstrBot/NapCat/DSH/模拟器；多日运行、可暂停、费用和备份状态可查；原作者更新可安全合并后经测试发布 |

## 每阶段固定交付报告

- 锁定上游、Fork、DSH/Bridge、AstrBot/NapCat/LivingMemory/PhoneMCP 的版本/commit。
- 原版已实现 vs 配置即可实现 vs 薄适配 vs 必须新增的具体源码位置/理由。
- 修改文件、用户账户/权限与凭据边界、真实运行命令、日志和测试分类（静态/mock/本机/在线/连续运行）。
- 状态备份点、schema 影响、失败后恢复方式；新提交 SHA/可追踪分支/推送状态。
- 阶段尚未完成的项目明确写 `TODO`，不推导“已经成功”。完成后停下，等待下一阶段指令。

## 先不做的事情

不重写一个新 Planner 或新生活数据库，不为满足 UI 里程碑重做小房间/星海，不强制安装 WSL2/Ubuntu VM，不承诺无人登录启动 Android GUI，不无视原版 Windows NapCat 已有兼容风险，不先开发超大 Coordinator。
