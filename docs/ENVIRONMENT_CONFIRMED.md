# 环境基线与旧环境退役界限 v0.7

## 已知确凿信息

- **台式机（dev/control/观测）**：Windows；用户指定根目录 `D:\Share\code\FishHome`；`repo/` 为唯一 Codex 工作区；`backups/`、`exports/`、`secrets/` 与 repo 同级。Windows 用户环境变量只设置 `FISH_HOME`，用户已通过 `gh` 登录 GitHub。
- **ThinkBook（prod）**：Lenovo ThinkBook 2021 锐龙版，Windows 11、16GB RAM；用户选运行模式 A：电源接入、禁系统睡眠，允许屏幕休眠，Windows 重启后人工登录，然后程序自动恢复。CPU/系统版别/硬盘空间不在已有证据中，不得编造。
- **旧 VMware VM**：Ubuntu 24.04、8GB/120GB，用户之前报告 SSH 已通，旧 IP `192.168.45.131`，可选别名 `fishvm`；它现在**只是备用的 Linux 兼容测试环境**，不作为生产、备份或迁移链的硬要求。
- **WSL2**：不强制安装。有 Linux 专属排查再选择 VMware；除非 VMware 无法解决明确的 Windows 互操作要求才讨论 WSL2。
- **SSH 目标**：建议为 ThinkBook 配 `fishbook`，但真实 IP、登录名、已安装 OpenSSH Server 及 host fingerprint 未在本包内验证。别名为实施目标，不是已成功联网的事实。
- **单机目录**：ThinkBook 实际安装路径由上游 Windows Launcher/NapCat/插件及数据位置决定；不能把台式机 D 盘路径直接写死到另一台。
- **数据与密钥**：正式数据唯一活跃权威 ThinkBook；备份/导出保存在台式机。明文备份、暂不配置外接设备，但不同机器间复制成功后形成独立物理副本。密钥默认不进普通归档。

## 不得被旧 v0.6 覆盖的决定

`fishbox`（实体 Ubuntu）、Linux `fish` 无 sudo 用户、VM→Ubuntu 数据恢复、强制安装 Docker/systemd、把小房间留作 P2、永远阻断公开互动等均属**被替代的旧方案**。新的 Windows 对应策略分别是 `fishbook`、Windows 标准用户/ACL、任务计划程序、原版房间优先复用、公共写入双重开关。

## 唯一环境变量

```powershell
[Environment]::SetEnvironmentVariable('FISH_HOME','D:\Share\code\FishHome','User')
```

重开终端/Codex 验证 `$env:FISH_HOME`；`gh auth status` 验证已登录的状态，禁止把 `gh auth token` 的结果写日志。API Key 可在运行时以进程作用域环境变量注入，但 Windows 用户级永久环境变量并非保密保险箱；优先使用受限账户可读的 Credential Manager / DPAPI 等方式，并使鱼的任意 Shell 环境拿不到部署者密钥。具体支持方式以真实组件为准。
