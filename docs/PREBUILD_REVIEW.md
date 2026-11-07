# Codex 开工前总审查：双 Windows 已决与未证实项 v0.7

> 这是需求/公开源码文档审查，不是已在你的 Windows 台式机或 ThinkBook 跑完的测试。M0 需要锁定上游提交 SHA、记录真实路径和版本。

## 结论

原版已经拥有大量我们的目标能力（QQ、人格、LivingMemory、LifePlanner、Goals、Workshop、跨日项目、B站互动、记忆星海和小房间、Windows PhoneMCP/ADB）。**我们不应该再造完整 Coordinator、第二套记忆或小房间。** 新增主要集中于 DSH 连通、交互与自主各 ¥5/预算日的逐请求成本门控及峰谷偏好、Windows 双机远程观测与部署、完整备份和可读导出、默认关闭的跨平台公共写入总开关。任何“现成可用”必须由本机证据验证。

## 从 v0.6 到 v0.7 的架构变更

| 旧 v0.6 条件 | 现在生效的条件 |
|---|---|
| VMware Ubuntu 部署，然后迁移到实体 Ubuntu | 台式机 Windows 开发和测试，ThinkBook Windows 11 正式运行；旧 VMware 只备用 |
| Linux 账户无 sudo 与 Docker 组 | Windows 非管理员标准用户 + ACL + 独立管理员部署身份 |
| `fishvm`/`fishbox` 作为必经 SSH 目标 | 台式机 dev 本机，生产 `fishbook` 是待配置 Windows OpenSSH；无强制 WSL2 |
| 永远不允许公开社交写入 | 默认关的全局总开关，用户可开启但仍须平台审批/额度/授权 |
| 小房间与记忆星海留到 P2 | 原版已有，实现位置是台式机本地，M2 早期验收 |
| Fish Coordinator 可能独立主导任务调度 | Fish Extensions 只做 DSH 与成本、必要执行状态薄适配；上游生活系统继续负责选择 |
| QQ 与测试记忆迁移未定 | 同 QQ 默认可选独立；测试记忆默认 none，可 selective/full |
| 多个 Windows 路径变量/SSH 环境参数 | 唯一用户变量 `FISH_HOME`；GitHub 使用已登录 `gh` |

## 最高风险（请 Codex M0 审计）

1. **Windows NapCat/QQ 版本兼容**：原作者 Windows 文档记录过特定构建组合崩溃和自包含模式；不能盲装最新版后称稳定。
2. **房间 Windows→Windows 只读同步**：上游有 Windows 本地页和面向远端 Linux 的 SSH 读取器；不能假设直接改 SSH host 即可读取 Windows DB。
3. **DSH Bridge**：插件和官方 DSH API 端口/协议/浏览器/usage/取消兼容不确定；版本变化可能破坏任务与预算。
4. **B站写入控制**：原作者有已有行为权限与平台限额，但不能用全局 UI 开关宣称任意 Shell 绝无网络写入能力。
5. **记忆/生活备份**：Linux `backup.sh` 与 LivingMemory 自带备份不代表 Windows 全量 Fish Home 档案已经实现；任务检查点、来源、Android 登录态与 prod 数据清单需逐项核验。
6. **Windows GUI 无人值守**：用户选择登录后自恢复，不要求未登录 GUI 常驻；模拟器视频推荐、ADB/音频与内存上限需实测。
7. **公开 Fork 泄漏**：既有本地文档、Git 历史、日志/配置要检查，不能把个人聊天或 Key 连同 Markdown/压缩包推送 GitHub。

## 禁止 Codex 在 M0 做的事

不要强推/删除本地目录、不要启动真实 QQ 自主活动、不要要求私人凭据、不要实现完整 Planner/Coordinator、不要安装 WSL2 或替换现有 VMware、不要修改/覆盖 ThinkBook 的真实记忆；只作 Git bootstrap 和静态审计、最小无凭据探针，并生成 M0 结果。

## 上游参考（需以当前 SHA 复核）

- <https://github.com/whyever77/dafeiyu-astrbot>
- <https://github.com/whyever77/dafeiyu-astrbot/blob/main/docs/部署指南.md>
- <https://github.com/whyever77/dafeiyu-astrbot/blob/main/deploy/memory_observatory/ROOM_V02.md>
- <https://github.com/whyever77/dafeiyu-astrbot/blob/main/CHANGELOG.md>
- <https://github.com/Fantasality/astrbot_plugin_dsh_bridge>
