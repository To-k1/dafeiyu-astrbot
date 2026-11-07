# Windows dev / ThinkBook prod 日常运维手册（设计合同 v0.7）

## 日常职责

- 台式机运行 Codex、GitHub、原版房间与记忆星海、Windows 控制命令；保存经校验历史 `backups/` 和可读 `exports/`。
- ThinkBook 接电常驻、不休眠，用户登录 Windows 后自动运行 AstrBot、NapCat、DSH、Fish Extensions、Android 模拟器/PhoneMCP；它持有**唯一 prod 活状态**。
- VMware Ubuntu 不参与正常启动与备份，只作为 Linux 特定兼容实验；不要求 WSL2。

## First-run 实地表（M0/M1 填写）

| 项 | 真实值/证据 |
|---|---|
| 台式机 `FISH_HOME` 与 Codex 工作区 | 用户已定；现场 `echo $env:FISH_HOME` |
| Fork origin / upstream / main / fish-home commit | TODO |
| Windows AstrBot Launcher / NapCat 版本和 data 路径 | TODO |
| LivingMemory / LifePlanner / 项目 DB 与备份机制 | TODO |
| 台式机 `memory_observatory` Windows 启动方式 | TODO |
| ThinkBook Windows 版本/CPU/硬盘与 SSH IP/host-key | TODO |
| Android 模拟器 / PhoneMCP / B站 App / ADB 版本 | TODO |
| DSH / Bridge 固定版本、RPC/usage/取消功能 | TODO |
| Windows 登录后恢复次序、账户和 ACL | TODO |

## 远程连接与房间

1. 用户同意时在 ThinkBook 安装 OpenSSH Server，配置受控部署账户、公钥、host key 校验、Windows 防火墙私有网允许规则。台式机 `~/.ssh/config` 配 `fishbook` 后测试 `ssh fishbook`。
2. 正式管理 WebUI 仅通过台式机到 ThinkBook 的 OpenSSH 本地端口转发访问；内部端口依实机核查，不把 DSH RPC 暴露 LAN。
3. 原作者房间在台式机本地 `localhost` 启动；生产数据由只读快照适配，刷新失败/过期应明确显示。不要把房间当生产数据库的编辑入口。

## 登录后启动和定时维护

- 关闭 Windows 自动睡眠，允许关闭屏幕；合盖行为在 ThinkBook 电源设置中实际验证，保证散热和电源可靠。
- 模拟器是图形 GUI，不能指望无人登录 Windows 就恢复。计划任务/启动器要设置**登录用户环境**的启动触发、启动顺序、健康检查、异常重试和日志轮转；重启后用户登录再观察运行状态。
- 定时一致性快照由 ThinkBook 本地计划任务完成，不依赖台式机开机。台式机一旦可达，则拉取所有未下载成功归档并校验。

## 管理动作和停工条件

- `deploy` 必须指定已测试 Git SHA；更新之前备份并检查 schema，失败可回滚程序，但不能擅自回滚已经产生的新记忆。
- `backup`、`export-memory` 和 `room-sync` 分离；命令实际由 M1/M6 Codex 编写，当前只是合同。
- 如需切换同一 QQ 从台式机 dev 到 ThinkBook prod，必须先确认 dev 已退出和任务安全保存；正式登录/扫码由用户处理。
- public_write_enabled 总开关默认关闭；任何未知平台工具或外部网络绕行风险都标为未验收。
- 遇到数据库错误、路径冲突、QQ 不断重连、费用越限或设备断线，先暂停自主后台工作、保留日志和保护备份，不自动覆盖其他机器数据。
