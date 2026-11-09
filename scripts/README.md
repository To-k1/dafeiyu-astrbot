# Windows 本机控制脚本

在仓库根目录使用已准备好的 `fish-home-dev` Python。日常启动和设置使用 [FishHome 台式机托盘控制台](../docs/DESKTOP_MANAGER.md)。

```powershell
$python = Join-Path $env:USERPROFILE '.conda\envs\fish-home-dev\python.exe'
.\scripts\fish.ps1 doctor -Python $python
.\scripts\fish.ps1 status -Python $python
```

`doctor` 调用上游 `deploy-kit/preflight.py` 检查部署前环境。`status` 显示本机各服务的 TCP 可达性。QQ、DSH、手机和模型连接使用控制台的「试运行自检」。

## 模拟 SQLite 快照与恢复

`-MockDatabase` 需显式指定测试库。省略 `-Archive` 时生成 `$env:FISH_HOME\backups\m1-mock-<UTC 时间>`。恢复目标为一个新的目录，默认只预览，提供 `-Apply` 后才写入。

```powershell
.\scripts\fish.ps1 backup -Python $python -MockDatabase 'C:\test\mock.sqlite3'
.\scripts\fish.ps1 verify -Python $python -Archive $archive
.\scripts\fish.ps1 restore -Python $python -Archive $archive -Destination 'C:\test\restored'
.\scripts\fish.ps1 restore -Python $python -Archive $archive -Destination 'C:\test\restored' -Apply
```

将 `$archive` 设为 `backup` 输出的实际归档路径。归档包含 `mock.sqlite3` 和 `manifest.json`，以 SQLite Online Backup 生成并经校验后发布。

## 台式机原生归档、可读导出和房间缓存

```powershell
$ref = (git rev-parse HEAD).Trim()
$work = Join-Path $env:TEMP ('fishhome-' + [Guid]::NewGuid())
$archive = Join-Path $work 'native.zip'
.\scripts\fish.ps1 backup -Python $python -Archive $archive -Ref $ref
.\scripts\fish.ps1 verify -Python $python -Archive $archive
.\scripts\fish.ps1 restore -Python $python -Archive $archive -Destination (Join-Path $work 'restored') -Apply
.\scripts\fish.ps1 export-memory -Python $python -Destination (Join-Path $work 'readable')
.\scripts\fish.ps1 room-sync -Python $python -Destination (Join-Path $work 'room.json')
```

在线归档分别为活动账本与 LivingMemory 的 SQLite 文件取一致性快照。停止相关写入进程后可用 `-WritersStopped` 同时归档向量索引和衰减状态。`-Profile memory` 只归档 LivingMemory。私聊缓存需显式 `-IncludePrivateChat`。包含 API Key 的 AstrBot 核心数据库、配置、登录态均排除。恢复只写入新的隔离目录。可读导出为 JSONL 和 Markdown。房间缓存从摘要投影生成，同步失败时保留最后成功的内容和时间。

`migration-preview` 默认 `none`。`selective` 按 `-Record` 检查明确选择的记忆记录及其父文档，`full` 检查已停写的运行归档。`deploy -DryRun -Ref $ref` 只做源版本预览。`deploy -Apply` 在 `-Destination` 生成本地源码 ZIP。ThinkBook 联调和自动快照尚待正式机可用。

## M2 管理员私聊入口

[AstrBot 4.29.4 OneBot 补丁](../deploy/patches/astrbot_4.29.4_qq_admin_ingress.patch) 用于 FishHome 独立实例。它在事件转换和日志之前读取 `admins_id` 中的数字 QQ 号，仅让唯一 QQ 管理员的好友私聊进入后续处理。内置 WebChat 管理员 `astrbot` 仍可保留。请求、通知、群聊、其他账号及缺少唯一 QQ 管理员的配置均在入口拦截。

当前开发环境已应用补丁，下次启动 AstrBot 时加载。重建固定版 venv 时，使用以下 PowerShell 命令。

```powershell
$packages = Join-Path $env:FISH_HOME 'dev\astrbot-4.29.4\.venv\Lib\site-packages'
$patch = Join-Path $env:FISH_HOME 'repo\deploy\patches\astrbot_4.29.4_qq_admin_ingress.patch'
git -C $packages apply --check $patch
git -C $packages apply $patch
```

若需撤回补丁，对同一路径执行 `git -C $packages apply -R $patch`，再重启 AstrBot。
