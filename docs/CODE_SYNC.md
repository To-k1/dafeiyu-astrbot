# 台式机、ThinkBook 与 GitHub 的手动代码同步

代码、配置与运行状态分别同步。ThinkBook 的正式数据库是唯一有效运行状态；不要把两台机器的 live SQLite 相互覆盖。控制台已有的设置同步用于其列出的配置项。普通备份和还原包用于恢复状态，不替代安装新代码。

## 台式机提交和同步 GitHub

当前公开发布工作树位于台式机项目的 `payload\fishhome-public`，分支为 `fish-home`。开发主工作树保留了历史私人材料，不能直接把整段历史合并进公开分支。先将需要发布的源码复制到公开工作树并检查差异，再按文件暂存；不要整目录暂存密钥、运行数据或未公开的人格。

在台式机 PowerShell：

```powershell
cd D:\Share\code\FishHome\repo\payload\fishhome-public
git status --short
git fetch origin
git fetch upstream
git merge --ff-only origin/fish-home
git diff
# 将已审查的文件逐项 git add 后：
git diff --cached
git commit -m "描述实际修改"
git push origin fish-home
```

有未提交改动或分叉时先处理本机差异，不强制覆盖。检查原作者更新用 `git log HEAD..upstream/main --oneline`；有更新时 `git merge upstream/main`，审查冲突、测试通过后推送。2026-11-18 核对时公开分支已经包含 upstream/main `6427435`。

## 把已提交代码安装到 ThinkBook

ThinkBook 当前从安装包解压部署，没有可直接拉取的 Git 工作树；AstrBot 实际加载的是 `dev\astrbot-4.29.4\data\plugins` 中的代码。只替换 `repo` 尚未完成插件更新。

1. 在控制台查看没有正在运行的委托和组会；等待结束或正常暂停。保存桌面程序内容后停止相关服务，再退出 ThinkBook 托盘。更新 DSH 启动文件时须同时停止两个 DSH。
2. 在台式机公开工作树生成、传送代码包：

```powershell
git fetch origin
$codePackage = Join-Path $env:TEMP 'fishhome-code.zip'
git archive --format=zip --output=$codePackage origin/fish-home
scp -o StrictHostKeyChecking=yes $codePackage fishbook:fishhome-code.zip
ssh -o StrictHostKeyChecking=yes fishbook
```

3. 在已连接的 ThinkBook PowerShell 中执行，`$installRoot` 按实际安装位置填写：

```powershell
$installRoot = 'D:\FishHome\FishHome-ThinkBook-OneClick-Repair-v2-2026-11-12'
$sourceRoot = Join-Path $installRoot 'repo'
$previousSource = Join-Path $installRoot ('repo.before-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Copy-Item -LiteralPath $sourceRoot -Destination $previousSource -Recurse
Expand-Archive -LiteralPath "$env:USERPROFILE\fishhome-code.zip" -DestinationPath $sourceRoot -Force
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$sourceRoot\deploy\desktop_manager\install.ps1" -InstallActivity -InstallBudgetBridge -InstallAffectiveBridge -InstallOwnerWork
```

此安装步骤编译托盘、更新活动/心境/Bridge 插件及 DSH 启动文件，保留运行端配置、记忆和账户凭据。安装器针对已支持的 AstrBot 4.29.4；失败时先查看报错，不启动半更新的服务。以后依赖版本或 AstrBot 内核补丁变化须按对应版本说明执行，以上操作不会自动更新全部第三方软件。

桌面 broker 的代码受管理员 ACL 保护。若本次也更新它，在 ThinkBook 管理员 PowerShell 另运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$installRoot\repo\deploy\dsh_bridge\Update-DesktopBroker.ps1" -InstallRoot $installRoot
```

叠加解压不会删除新版本已经移除的旧源码。若这次更新包含删除或改名，需要对照提交差异处理对应旧文件；不要用解压成功代替版本安装验收。

4. 回到 FishHomeOps 桌面，在 PowerShell 运行 `& "$installRoot\dev\FishHome.exe" --background --start-all`。确认服务状态，并在 QQ 验证 `/dsh ping` 和一条普通回复。GUI 桌面功能要求 Ops 已登录且解锁；SSH 登录不会提供交互桌面。

源码回滚可从 `$previousSource` 恢复相应文件，再重新运行同一安装步骤；受保护 broker 更新前的副本保存在 `ProgramData\FishHomeDesktop\windows_desktop.before-*.py`。这些是代码回滚，不会回滚正式记忆或数据库。新代码若包含数据库迁移，需要另按迁移说明处理。

## 从 ThinkBook 取回自己修改的代码

不要将整个安装目录回传后覆盖台式机。先把实际改过的插件源码复制回 ThinkBook 的 `repo` 对应文件；例如 `data\plugins\astrbot_plugin_dsh_bridge\main.py` 对应 `repo\deploy\dsh_bridge\m3_qq.py`。然后在台式机取回指定文件到临时目录比较：

```powershell
$reviewFolder = Join-Path $env:TEMP 'fishhome-code-review'
New-Item -ItemType Directory -Force -Path $reviewFolder | Out-Null
scp -o StrictHostKeyChecking=yes fishbook:D:/FishHome/FishHome-ThinkBook-OneClick-Repair-v2-2026-11-12/repo/deploy/dsh_bridge/m3_qq.py $reviewFolder
git diff --no-index deploy/dsh_bridge/m3_qq.py "$reviewFolder\m3_qq.py"
```

确认差异后复制到台式机开发工作树和公开工作树的对应位置，测试、提交并推送。同一文件两端都改过时人工合并，不以修改时间直接覆盖。密钥、cookies、QQ 会话和 DB 不属于代码回传内容；完整还原仍走已有还原包。
