# Fish Home / Codex 项目规则 v0.7（第一条先读）

## 不可混淆的机器和目录

- Windows 台式机唯一 `FISH_HOME=D:\Share\code\FishHome`。`%FISH_HOME%\repo` 是**Codex 工作区与 Git 根**；`backups/`、`exports/`、`secrets/` 是其兄弟目录，不进 Git，只有读写获批后才操作。
- ThinkBook Ryzen 2021 / Windows 11 / 16GB 是**正式运行机器**（SSH 别名拟为 `fishbook`，待现场建立）；台式机用于开发、原作者房间/星海本地渲染、远程运维、档案管理。ThinkBook 的**正式活跃 DB 唯一权威**，不要双向同步 live SQLite。
- VMware Ubuntu 24.04 / `fishvm` / 旧 IP 192.168.45.131 仅备用 Linux 测试。不要把它作为 M0/M1 必经目标，不要求 WSL2，也不要安排 Ubuntu 实体机迁移。
- 用户优先于旧文档。**唯一 PRD 是 `docs/PRD_FISH_HOME_v0.9.md`，FR-01～FR-15 和 FR-07A～E 的编号稳定**。其他历史开工包不作为当前规则。

## 本轮 Codex 只执行 M0

1. 先检查 `repo` 是否已有 Git 或文档，不得删除/覆盖用户资料。使用用户已登录的 `gh` **自行检查、创建公开 Fork、建立并安全推送分支**：`origin`=自己的 Fork、`upstream`=原作者；`main` 尽量同步上游、定制提交落 `fish-home`，功能分支为 `feature/*`。可自动处理正常提交与非强制推送，但**严禁 `reset --hard`、force push、删仓/删分支、无审查直接部署 prod**。
2. 阅读真实锁定 SHA 对应的上游 Windows 部署、`deploy/memory_observatory`、LifePlanner/Goals/Workshop/Projects、PhoneMCP/ADB、NapCat、LivingMemory、DeepSeek Harness 与 Bridge。确认源码/开关/路径/数据 schema，不从 README 想象已完成部署。
3. M0 不连接真实 QQ、不执行公开写入、不花费模型 API 费用、不实施 M1 业务模块。只允许最小无凭据环境探测、Git 初始化与文档/报告完善，结束后停止。

## 开发权限与回滚纪律

- **增量优先**：已有模块可配置就配置；可调用已有接口就薄适配；只有确实缺失才新增。**禁止再造 LifePlanner、第二套记忆数据库或完整 Fish Coordinator**；Fish Extensions 仅负责 DSH 接线、跨工具成本/峰谷门控、必要执行检查点与运维。
- Codex 运行与大肥鱼是不同安全主体。鱼使用独立 Windows 标准用户、受限 ACL、自己工作区和浏览器；不具管理员权限，不持有 GitHub CLI 登录、Codex SSH 私钥、系统管理员密码或台式机私人文件。测试 Windows ACL，不宣称普通账户自动等于强沙箱。
- 远程管理通过 Windows OpenSSH、受限主机指纹/公钥与最小需要权限。不要设置 `StrictHostKeyChecking=no`。管理 WebUI 通过 SSH loopback 转发；DSH 无鉴权 RPC 不开放到 LAN。
- 默认**公共平台写入总开关关闭**，允许用户后来开启，但仍须各平台的审批/权限/额度。源站网页和模型输出不能修改授权状态。任意 Shell 可绕过工具层的矛盾必须记录为风险，没有执行层保证时不要放行无人值守公共写入。B 站登录推荐流先限于人工在场联调，验证写入限制后开放自主浏览。
- 模型默认 DeepSeek 官方 `deepseek-flash`。交互、自主账本各默认 ¥5 CNY，预算日北京时间 04:00 切换。台式机管理员可分别临时或永久调额，QQ 可查余额和收超限提示。任务归账、停付费规则见 `docs/DECISIONS.md`。硬上限以逐请求拦截实测为准。
- QQ 身份 dev/prod **默认相同但允许独立**；同一 QQ 只有一个可主动执行的实例。测试记忆默认 `none` 不迁移，用户可选 `selective` / `full`，每次危险覆盖需预览/确认。prod 数据不能被开发任务清空。
- 备份/导出/迁移是三个不同操作：运行端生成一致性快照、台式机拉取校验、原生 DB 可恢复；JSONL/Markdown 导出不等价。归档不加密、暂不配置第三存储介质，**普通备份不含密钥、QQ 登录态、Cookies**。
- 台式机直接运行**原作者现有的小房间与记忆星海**，界面只取脱敏只读快照。原生数据另走台式机管理员通道按计划拉取，私聊原文默认按需拉取，可单独启用自动同步。不能重新开发一个 UI 来满足阶段数量；房间动画不等于真实行动。数据中断时显示同步时间和失败，不假装实时。
- ThinkBook 16GB，GUI 安卓模拟器与 Windows 登录会话绑定。用户选择方案 A：接电不休眠，重启登录 Windows 后自动恢复。不能声称未登录 GUI 自动启动已完成。
- 每一阶段记录提交 SHA/第三方版本、运行命令、配置差异、真实/模拟测试、状态数据地图、剩余风险、可回滚路径，报告后停。用户明确允许的非破坏性 Git 提交/推送不需重复询问；生产数据覆盖、账户扫码、实名 API Key、公开发布与生产部署仍须明确执行授权。

**首轮任务全文：`docs/CODEX_START_M0.md`。**

## 上游编码规范

维护上游模块时参考[原作者的仓库规则](https://github.com/whyever77/dafeiyu-astrbot/blob/main/AGENTS.md)和[编码流程](.agents/skills/dafeiyu-coding/SKILL.md)。Fish Home 的机器、权限与部署规则按本文执行。
