# Windows 台式机主控 / ThinkBook 局域网远程管理合同 v0.7

## 一、FISH_HOME 与 Codex 工作区

只设置台式机 Windows 用户级 `FISH_HOME`。**Codex 选择其下 `repo`**，Git 根不含数据档案。脚本执行时根据一个根目录派生路径：

```powershell
$root = $env:FISH_HOME
if (-not $root) { throw 'FISH_HOME is not set' }
$repo = Join-Path $root 'repo'
$backups = Join-Path $root 'backups'
$exports = Join-Path $root 'exports'
$secrets = Join-Path $root 'secrets'
```

`FISH_HOME` 是**定位**，不是写权限；Codex 沙盒/Windows ACL 不自动允许改写 `repo` 外兄弟目录。若需要，由用户对 `backups/exports` 定向审批；默认不授予普通脚本读取所有 `secrets` 的权限。私密 API Key 可安全地在服务账户进程环境注入，但不建议放入公开源码或全局明文日志。

## 二、建议 CLI（M1+ 实现前不可调用）

```powershell
.\scripts\fish.ps1 doctor -Target local-dev
.\scripts\fish.ps1 status -Target fishbook
.\scripts\fish.ps1 logs -Target fishbook -Service astrbot
.\scripts\fish.ps1 deploy -Target fishbook -Ref '<verified-git-sha>' -DryRun
.\scripts\fish.ps1 backup -Target fishbook
.\scripts\fish.ps1 export-memory -Target fishbook
.\scripts\fish.ps1 verify -Archive '<archive-path>'
.\scripts\fish.ps1 restore -Target fishbook -Archive '<archive-path>' -DryRun
.\scripts\fish.ps1 room-sync -Target fishbook  # 可独立做只读数据同步
```

**这些命令目前均为实现合同，不是 ZIP 中已经写好的脚本。** 不要把普通 `deploy` 当成要复制/覆盖目标 `data/`；发布前先检查 schema 兼容和目标已有记忆。

## 三、ThinkBook 远程基础

- ThinkBook 用户批准后安装/开启 Windows OpenSSH Server，使用专用部署身份、公钥登录和经核验的主机指纹；建议台式机 `~/.ssh/config` 别名 `fishbook`。ThinkBook 局域网 IP 和账户名由现场决定；按需设置路由器 DHCP 保留地址。
- Win11 防火墙仅允许可信私有网络和台式机地址访问 SSH；不对 Internet 做端口映射；管理端口以 loopback 为主。
- AstrBot WebUI / NapCat WebUI 的端口先查实际监听，再使用 `ssh -N -L 本地端口:127.0.0.1:远端端口 fishbook` 由台式机浏览器访问。`6185`/`6099` 只是常见默认端口，实际以实机为准。
- DSH Web/RPC 可能没有身份验证，不能为方便跨机器而把它绑定到 `0.0.0.0`。Fish Extensions 与 DSH 尽量在同台运行，管理 API 经过受控本机入口。
- 正式用户登录 Windows 后由受审查的计划任务/启动器恢复 AstrBot/NapCat/DSH/PhoneMCP/模拟器；**不承诺未登录时 GUI 模拟器自动恢复**。

## 四、同步方向（非常重要）

```text
GitHub/Fork --(经确认的发布 SHA)--> 台式机 repo --deploy--> ThinkBook 程序
ThinkBook live DB --> 一致性只读快照 --> 本地短期归档 --> SSH --> 台式机 backups
ThinkBook memory --> 真实记录可读导出 --> SSH --> 台式机 exports
ThinkBook activity/memory --> 轻量只读快照 --> 台式机本地房间/星海展示
```

三个读出路径**不是同一份数据**：完整备份可恢复；可读导出便于人阅读；房间快照只是观测缓存。禁止把房间同步回写到 ThinkBook 的 live DB，也禁止用 Git 双向同步活跃 SQLite。

## 五、原子归档与失败约束

- 先在 ThinkBook 用插件官方机制/SQLite backup API 形成一致性原生备份；必要时暂停写入，记录当前任务状态与逻辑时间点。
- 归档包括 manifest/SHA256/schema/源 commit/数据范围，先在目标目录写 `*.partial`，校验后改名为成功版本；网络断开不覆盖上一份可靠归档。
- `restore -DryRun` 检查目标已有记录/版本、差异、磁盘、路径映射和权限；必须在明确授权后才能执行会覆盖/修改正式记忆的恢复。
- ThinkBook 本机计划任务可以在台式机关机时继续定时生成快照；台式机上线后补拉经验证的历史档案。
