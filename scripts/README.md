# Windows 本机控制脚本

`fish.ps1` 的 M1 目标是 `local-dev`。在仓库根目录使用已准备好的 `fish-home-dev` Python。

```powershell
$python = Join-Path $env:USERPROFILE '.conda\envs\fish-home-dev\python.exe'
.\scripts\fish.ps1 doctor -Python $python
.\scripts\fish.ps1 status
```

`doctor` 调用上游 `deploy-kit/preflight.py` 检查部署前环境。`status` 显示 AstrBot 6185 和 NapCat 6099 的本机 TCP 可达性。

## 模拟 SQLite 快照与恢复

`-MockDatabase` 需显式指定测试库。省略 `-Archive` 时生成 `$env:FISH_HOME\backups\m1-mock-<UTC 时间>`。恢复目标为一个新的目录，默认只预览，提供 `-Apply` 后才写入。

```powershell
.\scripts\fish.ps1 backup -Python $python -MockDatabase 'C:\test\mock.sqlite3'
.\scripts\fish.ps1 verify -Python $python -Archive $archive
.\scripts\fish.ps1 restore -Python $python -Archive $archive -Destination 'C:\test\restored'
.\scripts\fish.ps1 restore -Python $python -Archive $archive -Destination 'C:\test\restored' -Apply
```

将 `$archive` 设为 `backup` 输出的实际归档路径。归档包含 `mock.sqlite3` 和 `manifest.json`，以 SQLite Online Backup 生成并经校验后发布。正式机快照、远端部署、导出和房间同步按 M6 的合同实施。
