# Fish Home M1 报告

2026-11-07 · 台式机 Windows 原生开发实例

## 结果

- 当前 Windows 账户下建立独立开发实例。AstrBot 4.29.4 启动及重启后均从 `127.0.0.1:6185` 返回 HTTP 200，复用了同一个 `data_v4.db`。官方 Launcher 0.3.14 的发布资源直连超时，GitHub CLI 下载也遇到 TLS 握手超时。本机改用固定版本的官方 Python 包，面板静态资源随包提供。 [AstrBot 原生包部署](https://docs.astrbot.app/en/deploy/astrbot/package.html)
- NapCat 4.18.38 从已有自包含 ZIP 解出全新目录。原生组件加载成功，未扫码时 `127.0.0.1:6099/webui/` 返回 HTTP 200，系统监听记录确认为回环地址。 [NapCat 固定发行版](https://github.com/NapNeko/NapCatQQ/releases/tag/v4.18.38)
- LivingMemory 2.9.1（源码 `1c6a471`）已安装并在两次启动中加载。插件通过配置校验，等待 Embedding 和 LLM 提供商后继续初始化。真实记忆写入、召回与库格式验证属于 M2。 [LivingMemory 固定源码](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory/tree/1c6a471c9af9ae45a4ccb3a8cec5695c917de50b)
- 开发环境 Python 3.12.16，应用使用独立 venv。运行环境的 `pip check` 通过。应用配置中的模型提供商和消息平台均为零。验证后两个服务均已停止。M0 基线及其他组件锁定版本见 [M0_REPORT.md](M0_REPORT.md)。

## 本机启动与数据

在 PowerShell 里可以用下列方式重启已经建立的测试实例。第一条启动 AstrBot，另一窗口运行 NapCat。按 Ctrl+C 停止各自进程。

```powershell
$astrbot = 'C:\Users\15800\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\FishHome\m1\astrbot-4.29.4'
$env:ASTRBOT_ROOT = $astrbot
Set-Location -LiteralPath $astrbot
& .\.venv\Scripts\python.exe -m astrbot.cli run
```

```powershell
& 'C:\Users\15800\AppData\Local\FishHome\m1\napcat-4.18.38\napcat.bat'
```

AstrBot 实例用官方 `python -m astrbot.cli init --yes` 初始化。其数据位于 `$astrbot\data`，包含配置、核心 SQLite 库、`plugins` 和 `plugin_data`。NapCat 使用独立解压目录下的 `napcat\config`。首次生成的本机面板凭据留在该实例的本地配置及启动日志。

## 模拟快照与恢复

- [fish.ps1](scripts/fish.ps1) 提供 `doctor`、`status`、`backup`、`verify`、`restore` 的 M1 本机入口。命令见 [scripts/README.md](scripts/README.md)。`doctor` 复用上游预检，返回成功及可选 `.env` 提示。运行中 `status` 检出了 AstrBot，收尾时两个端口均不可达。
- 使用测试 SQLite 库，保持连接打开并关闭 WAL 自动 checkpoint。对 WAL 中两条已提交记录取快照后，源库新增第三条记录。验证归档、预览恢复，再恢复到新目录，恢复库仅含前两条。现有目标被拒绝，损坏一字节的测试归档也被拒绝。
- 保留通过校验的模拟档案 `D:\Share\code\FishHome\backups\m1-fixture-vjti753k`。归档通过 SQLite Online Backup 生成，写入临时目录，经完整性检查和校验后发布。演练与实际 AstrBot、LivingMemory 数据分离。

## 交付和边界

- 本地功能分支 `feature/m1-windows-dev`，以 `fish-home` 的 M0 提交 `2713f16` 为基础。新增两个控制脚本和本报告，更新使用说明与 M2 QQ 验收决策。
- 回退时先停止开发进程。本地功能改动可用普通 Git revert 撤回，模拟档案和开发实例独立于 Git。
- M2 使用已有专用 QQ 小号。只允许一个管理员 QQ 私聊触发，其他 QQ 私聊及群聊应在模型、记忆与工具之前被忽略。真实扫码、Flash 和嵌入提供商接入、管理员过滤联调属于 M2。
