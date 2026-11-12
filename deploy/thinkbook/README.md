# ThinkBook 安装入口

将本目录的安装与账户准备脚本复制到迁移包根目录，与 `repo` 和 `extras` 同级。在 ThinkBook 解压到新目录后，双击 `Install-FishHome.cmd`。安装预检可在该目录运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File Install-FishHome.ps1 -Plan`。

`repo` 由已验收版本的源码导出。安装需要以下独立的发行材料。

| `extras` 文件 | 来源 |
| --- | --- |
| `NapCat.Shell.Windows.Node.zip` | NapCat 4.18.38 官方 Windows Node 原包，可随包提供，安装器优先使用本地副本。 |
| `LivingMemory-2.9.1.zip` | LivingMemory 2.9.1 源码，ZIP 根目录包含 `main.py`。 |
| `DSH-Bridge-1.0.0.zip` | DSH Bridge 1.0.0 源码，ZIP 根目录包含 `dsh_client.py`。 |
| `PhoneMCP-v0.5.0-source.zip` 和 `PhoneMCP-Windows.patch` | 已验收 PhoneMCP 源码及 Windows 兼容补丁。 |
| `Start-DSH-M3.cmd`、`m5-file-only.yml`、`dsh/package.json` 和 `dsh/package-lock.json` | 固定 DSH 0.2.2-rc.1 的受控启动配置与 npm 依赖锁。 |

双击 `Prepare-Runner.cmd` 准备 DSH 标准账户和样例工作区。程序安装和账户准备分别执行，QQ 登录与正式数据切换留给现场验收。迁移默认 `none`，保留目标机既有数据。

恢复测试使用 Windows PowerShell 5.1 和一个可运行的 64 位 Python 3.12。在空临时目录下向 `Test-Install-Recovery.ps1` 传入 `-FixtureRoot` 和 `-Python`，可复验旧缓存、下载中断及半成品环境恢复。
