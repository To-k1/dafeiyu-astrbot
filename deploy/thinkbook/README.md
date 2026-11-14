# ThinkBook 安装入口

新装时在 ThinkBook 把 ZIP 全部解压到一个空目录，保留安装脚本、`repo` 和 `extras` 的同级结构，然后双击 `Install-FishHome.cmd`。下载中断后可双击同一个文件重试。还原正式机请先看下方「原 ThinkBook 恢复」，按原安装路径操作。安装预检可运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File Install-FishHome.ps1 -Plan`。

`repo` 由已验收版本的源码导出。安装需要以下独立的发行材料。

| `extras` 文件 | 来源 |
| --- | --- |
| `NapCat.Shell.Windows.Node.zip` | NapCat 4.18.38 官方 Windows Node 原包，可随包提供，安装器优先使用本地副本。 |
| `LivingMemory-2.9.1.zip` | LivingMemory 2.9.1 源码，ZIP 根目录包含 `main.py`。 |
| `DSH-Bridge-1.0.0.zip` | DSH Bridge 1.0.0 源码，ZIP 根目录包含 `dsh_client.py`。 |
| `PhoneMCP-v0.5.0-source.zip` 和 `PhoneMCP-Windows.patch` | 已验收 PhoneMCP 源码及 Windows 兼容补丁。 |
| `Start-DSH-M3.cmd`、`m5-file-only.yml`、`dsh/package.json` 和 `dsh/package-lock.json` | 固定 DSH 0.2.2-rc.1 的受控启动配置与 npm 依赖锁。 |

双击 `Prepare-Runner.cmd` 准备 DSH 标准账户和样例工作区。程序安装和账户准备分别执行，QQ 登录与正式数据切换留给现场验收。迁移默认 `none`，保留目标机既有数据。

台式机可从 Git 工作区运行 `python -B -m deploy.thinkbook.rebuild_bundle <已验收的安装ZIP> <新版安装ZIP>`。新版使用当前源码和旧包里固定版本的依赖材料，私有运行状态请另存为还原包。

## 原 ThinkBook 恢复

台式机 FishHome 控制台的「生成 ThinkBook 还原包」会将正式机的私有状态保存到 `D:\Share\code\FishHome\backups`。恢复时使用原 FishHomeOps Windows 账户和原安装路径，将台式机当前源码、安装脚本、原迁移包的 `extras` 和私有 ZIP 放到新的安装目录。把私有 ZIP 命名为 `Restore-FishHome.zip`，与 `Install-FishHome.cmd` 同级，然后双击安装。安装器完成程序初始化后会验包并还原，恢复阶段中断可重新运行同一安装入口。若系统上的 FishHomeRunner 账户被删除，先恢复或准备该账户并使用原密码。所有服务在恢复前应停止，包括退出托盘。

还原包包含 AstrBot 运行数据及私聊、插件配置、预算与任务记录、NapCat 配置、控制台凭据和两套 DSH 的状态及工作区。Windows 凭据由原账户的 DPAPI 保护。Windows 重装或账户重建后，需要重新填写绑定凭据。QQ 客户端登录态和雷电实例在项目目录之外，必要时重新登录。备份文件应存放在台式机私有备份目录。

可用项目的 Python 执行 `python -B -m scripts.thinkbook_recovery unpack <还原包> <新目录>`，检查内容和 SQLite 完整性。该命令只写入新目录。

恢复测试使用 Windows PowerShell 5.1 和一个可运行的 64 位 Python 3.12。在空临时目录下向 `Test-Install-Recovery.ps1` 传入 `-FixtureRoot` 和 `-Python`，可复验旧缓存、下载中断及半成品环境恢复。
