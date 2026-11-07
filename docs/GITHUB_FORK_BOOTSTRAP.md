# Codex 自动 Fork、克隆、分支与长期同步合同 v0.7

**用户已授权**：使用自己已登录的 GitHub CLI `gh` 创建公开 Fork、克隆、建立 `main` 与 `fish-home` 分支、正常 commit/push/PR 管理。**未授权**：删除现有 repo、删除 GitHub 仓库、`reset --hard`、强制推送、丢弃本地未提交文档、未经检查即发布 ThinkBook 生产实例。

## 1. M0 首先探测，不做破坏性重置

```powershell
$root = $env:FISH_HOME
if (-not $root) { throw 'FISH_HOME 未设置；请在新终端检查' }
$repo = Join-Path $root 'repo'
gh auth status
Test-Path $repo
if (Test-Path (Join-Path $repo '.git')) {
  git -C $repo status --short
  git -C $repo remote -v
  git -C $repo branch -avv
}
```

切勿输出令牌。使用 `gh repo view whyever77/dafeiyu-astrbot` 检查原作者仓库；需当场确认 `defaultBranchRef` 的名称（不假设永远 `main`）。用户的 Fork 名称必须从 `gh` 得到，不靠猜账号。

## 2. 如果尚无 Fork 和仓库

1. 检查是否已存在用户 Fork：`gh repo list --fork --limit 200` 等；**避免创建重复 Fork**。
2. 只有在需要时 `gh repo fork whyever77/dafeiyu-astrbot --clone=false`；**`gh repo fork` 没有 `--public` 选项**，源仓库是公开的，创建后必须用 `gh repo view <实际Fork仓库> --json visibility,isFork` 确认可见性与 Fork 身份。不要猜 GitHub 登录用户名。
3. **`repo` 若含用户文件，绝不能直接在非空目录执行会覆盖文件的 clone**。应先生成文件清单和校验和；在用户授权的 `FISH_HOME` 临时工作目录克隆，然后把经比对的文档合并；或在已有仓库内安全添加远端并 fetch。操作前记录回退位置，不能直接删除旧 `repo`。
4. `repo` 已存在且为空时，在其父目录克隆 Fork 并指定目标 `repo`；克隆命令/SSH 或 HTTPS 由本机 `gh` 实际提供，**不猜 Fork URL**。
5. 设置 `origin`=用户公开 Fork，`upstream`=`https://github.com/whyever77/dafeiyu-astrbot.git`（仅在不存在时添加），`git fetch --prune upstream`。确认无自定义变动的 `main` 对齐上游，定制文档/代码提交到 `fish-home`。
6. 仅在还不存在 `fish-home` 时从干净上游分支新建；若已存在则保留原有历史。新建 `feature/*` 完成每项增量；通过测试后合并回 `fish-home` 并正常 push。

## 3. 正常上游同步（工作区干净且 main 可 FF 时）

```powershell
# 先检查 git status 与目标保护，不要盲运行
# git fetch --prune upstream
# git switch main
# git merge --ff-only upstream/main
# git switch fish-home
# git merge main
# 测试、记下差异和 SHA 后 git push origin fish-home
```

`upstream/main` 只是示例；真实默认分支以 M0 现场 `git remote show upstream` 为准。若 `main` 被用户修改、需要历史重写、上游变化影响数据库或核心补丁，停下报告并设计受控合并，不自动强推。可以正常向自己的 Fork 推送 `fish-home` 和经过确认的 `main` fast-forward 同步。

## 4. GitHub 与项目数据的边界

`repo` 内允许代码、模板、固定版本清单、文档和非敏感 mock 测试；**不允许**真实 `.env`、token、Cookie、QQ 扫码、活数据库、记忆全文备份、安卓模拟器用户资料、私有研究成果。对公开 Fork 的历史泄漏要在 push 前做扫描。主机上的 `gh` 登录只用于 Codex/开发账户，运行鱼的标准用户无权使用凭据。

## 5. 每次升级

记录双方 commit 和插件版本 → 查原作者新功能是否已经实现我们的需求 → 合并至独立测试分支 → Windows dev 回归（QQ/生活/房间/PhoneMCP/DSH/费用/备份）→ **人工确认** 生产发布。不能因为无 Git 冲突就忽略功能或 schema 变化。
