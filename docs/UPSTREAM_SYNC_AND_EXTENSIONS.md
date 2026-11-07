# 长期 Fork 同步与增量开发规范 v0.7

## 分支图

```text
whyever77/dafeiyu-astrbot upstream/(实际默认分支)
               │ fetch / fast-forward
               ▼
自己的公开 Fork origin/main  ← 尽量保持原作者历史
               │ merge 已审计变动
               ▼
        fish-home（所有我们自己的定制和文档）
               ├─ feature/dsh-adapter
               ├─ feature/windows-backup
               ├─ feature/room-win-ssh
               └─ feature/public-write-gate
```

M0 不可以假设 `main` 已经干净或默认分支名必定为 `main`：先检查 `git status`、`git branch -avv` 和 `git remote -v`，检查 `gh auth status`；Fork 未存在再自行创建。用户明确允许 Codex 正常 commit/push，但禁止强推、删历史、未经审查的生产发布。详见 `docs/GITHUB_FORK_BOOTSTRAP.md`。

## 架构增量原则

- 上游 LifePlanner、Goals、Workshop、记忆、B站互动执行器、房间/星海已经有实现或实验：先复用，避免第二套生活系统。
- 自己的主要新增范围是 Fish Extensions 的 DSH 适配/usage/成本/执行检查点、Windows 备份导出、双机远程管理、Windows 房间只读读取适配、总公共写入开关。名字/路径与上游冲突时遵从现有 API，而不是拷贝上游模块修改第二份。
- 真有必要改原作者代码：保持 hook 小、标明理由/源码路径/补丁，针对同一功能写回归；不要要求“零改上游文件”而以复制状态机替代。

## 上游更新流程

1. 从上游安全 `fetch`，记录 SHA 和 changelog；不触当前 prod。
2. `main` 干净时 fast-forward；若有自定义提交，先保护历史并报告，不用 `reset --hard`。
3. 在 `feature/upstream-sync-...` 或 `fish-home` 上受控 merge；发生冲突不直接全选 ours/theirs。
4. 检查 Windows AstrBot/NapCat/QQ 构建、生活/记忆数据结构、原版房间 API、PhoneMCP、DSH Bridge、公开互动权限和费用计量变化。
5. Windows dev 回归，完成快照/恢复测试；通过且用户批准后部署 ThinkBook 的指定 commit，不能自动追上游最新版。
6. 回退程序不等于可以回滚数据库；发布前需要确认 schema 与备份/恢复兼容。

## 安全回归的七类重点

源码历史与版本、Life/Goals/记忆单一真相、原版观测台适配、PhoneMCP App/权限、公共写总开关与各平台权限、¥5 费用门控、完整一致性备份和生产恢复。尤其 B站原版新增任何写入动作后需重新确认总开关仍生效，不能因 Git merge 无冲突而自动放开。还需检测上传到公开 Fork 的意外密钥/记忆。

## 变更证明模板

```text
上游 SHA / Fork SHA / 发布 SHA：
涉及的原作者源码函数、状态表和权限开关：
能直接复用的能力：
必须新增的最小接口：
修改原文件的范围与理由：
Windows 开发环境测试证据：
ThinkBook 发布前备份与回滚：
不确定处 / 是否允许进入下一阶段：
```
