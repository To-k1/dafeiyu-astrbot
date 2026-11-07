# 开工包版本变更记录 v0.7

来源基线：用户历史 `大肥鱼_Codex开工包_v0.3.zip`、整合后 `大肥鱼_Codex开工文档包_v0.6.zip` 与后续明确讨论。当前用户请求是生成**定稿前完整开工包**，并不意味着已有任何脚本或远程服务实机成功。

## 本次保留的关键内容

- 以 **v0.6 完整 PRD** 为底稿修订为 `PRD_FISH_HOME_v0.9.md`，稳定保留 FR-01～FR-15 与 FR-07A～E；原版上下文 1k–2k/4k–8k/0.5k–1.5k/1k–2k/≤2k 的分层建议、`needs_review`、工作日峰谷 FakeClock、成本偏好等都保留。
- 上游同步流程 `main` / `fish-home` / `feature/*`、优先配置/复用/薄适配、DSH Bridge 的兼容审计、SQLite 一致性归档、¥5 CNY 每日预算、只用 Flash 等延续。

## 这版明确替换/废弃的旧假设

| 原 v0.6 | 现在 v0.7 |
|---|---|
| Windows→VM Ubuntu→实体 Ubuntu | **Windows 台式机 dev/control→Windows ThinkBook prod**，VMware 备用，无需 WSL2 |
| `fishvm`/`fishbox` 部署主线、systemd timer | `local-dev`/`fishbook`、Windows OpenSSH / Windows 任务计划程序 |
| Linux fish 无 sudo | 独立 Windows 标准用户、Windows ACL 与非管理员执行 |
| 小房间/星海 P2 可选 | 上游已实现，**台式机本地显示与远程 Windows 读取适配**，M2/M6 验证 |
| B站及公网永久不允许写入 | 默认关总开关 + 平台独立开关/审批/额度，可明确授权开启 |
| Fish Coordinator 可能是独立大系统 | Fish Extensions 薄适配，不重写上游生活决策/记忆/项目 |
| GitHub Fork/用户账户待定 | 用户 `gh` 已登录，授权 Codex 自行创建公开 Fork、克隆、分支、正常推送 |
| Windows 可能多个变量 | **只设 `FISH_HOME`**，其它路径由其计算 |
| 开发 QQ 和记忆迁移未定 | QQ 默认同号、可独立；测试记忆默认不迁移，支持选择性/完整迁移 |
| Ubuntu 实体机无桌面依赖 | ThinkBook 接电不休眠，**登录 Windows 后** GUI 程序恢复，16GB 实测资源上限 |

## 被重写的过期文档

为避免旧策略被 Codex 误当成有效规则，v0.7 **没有原样携带** v0.6 的 `VERSION_COMPARISON_AND_FIXES.md`（历史 v0.3/0.4 比较）、旧 `PRD_FISH_HOME_v0.8.md` 和 Linux 操作指南的正文；取而代之的是上述新文件。旧 ZIP 仍可作为历史来源，但不应与此包同时解压进工作区当两套有效 PRD。

## 未验证项不能在本包宣布通过

原作者小房间 Windows→Windows SSH 读取、NapCat+QQ 最新组合、DSH Bridge 与官方 DSH、ThinkBook GUI 模拟器长期稳定性、费用请求前强拦截、公共写工具的硬权限边界、真实 Windows DB 的全量恢复。均为 M0/M1+ 应实测的工作项。
