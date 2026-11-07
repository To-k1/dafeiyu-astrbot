# Windows 控制脚本预定位置

**目前没有已实现的 `fish.ps1`。** 本 ZIP 只提供 M1+ 合同；不要向用户报告备份、迁移或部署脚本已经可用。

Codex 根据 M0 审计、通过可靠复现后，未来实现：`doctor`、`status`、`logs`、`deploy`、`backup`、`export-memory`、`verify`、`restore`、`room-sync`。dev 默认本机 Windows，prod 通过 Windows OpenSSH `fishbook`，无强制 `fishvm` / WSL2。

- 从 `$env:FISH_HOME` 派生 repo/backups/exports/secrets，不需要额外路径环境变量。
- `backups`/`exports` 写入可能需要 Codex 沙盒/ACL 定向审批；`secrets` 不作为普通输出位置。
- ThinkBook 原生一致性快照后才下载到台式机，保留 SHA/manifest/`.partial` 失败标志。
- `restore` 默认 dry-run，已有 prod 记录的覆盖必须确认；默认关闭后台自治任务。
- 房间/星海使用原作者前端，不在 `scripts/` 重造新 UI；只提供远端 Windows→台式机只读状态适配。
