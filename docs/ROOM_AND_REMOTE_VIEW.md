# 原作者小小房间与记忆星海：台式机本地显示方案 v0.7

## 已知上游证据与成熟度

- `deploy/memory_observatory/ROOM_V02.md`：原作者标记 **2026-10-30，本地实现，未部署**；可运行 `python -B -m deploy.memory_observatory.server --port 8767 --livingmemory-ssh --life-ssh`，提供 `/room`（小房间）和 `/`（记忆星海）；原文件描述了 Windows 本地测试、真实只读记忆接线和虚构 demo。这个命令**针对上游当时环境，不保证已适配 ThinkBook Windows**。
- 小房间是 SVG 场景 + Canvas 动画（**不是 Live2D**），书桌/书架/窗边/休息角动作是观测层表演，不是实时执行证明。记忆类型的空间布局与语义距离不可混淆。
- 上游说明：记忆按页面可见状态每 60 秒自动同步，活动账本有独立「同步近况」按钮，缓存过期需标记旧状态；**不是全数据每 60 秒实时刷新**。
- M0 必须锁上游 SHA 后检查 README、`server.py`、读取器、Windows 路径/进程命令与测试用例；不能直接凭文档认为能连远端 Windows。

## 最终拓扑

```text
台式机（Codex 主机）
  本地 python / browser → http://127.0.0.1:8767/room
  /                → 记忆星海
  同机只读缓存      → 限定的数据快照 + 同步时间/来源信息
             ▲
             │ SSH (fishbook)，Windows 运行账户/密钥受限
             ▼
ThinkBook Windows 11（正式鱼）
  LivingMemory / LifePlanner / Goals / Workshop / activity ledger
  只读导出/数据库一致性快照（不得把 live SQLite 裸拷贝给 UI）
```

## 实施优先级

1. 台式机 **先按锁定版本**跑原版小房间的 demo，确认资源、路由和同步 UI 工作；再连接本机 dev 的真实测试数据，验证不会把 demo 当成真实鱼。
2. 找出上游 `--livingmemory-ssh` / `--life-ssh` 的具体远端 Linux 假设；对 Windows ThinkBook 修改**仅数据适配器**，不要重做前端动画/导航/星海画布。
3. 在 ThinkBook 端经只读身份或受控快照工具提供最小所需数据；可以通过 SSH/SFTP 传规范化 JSON 或一致性 SQLite 快照。远端正式活跃 DB 保持权威。观测层只缓存脱敏记忆摘要、来源标识和必要活动/成果。台式机管理员在独立通道同步原生数据，私聊原文默认按需拉取，可单独启用自动同步。
4. 读取失败和旧缓存标注**最后同步时间、数据源、错误状态**；网络掉线时不自动落到演示数据；多次同步要单飞/去重、不能刺激真实生活系统改变记忆。
5. AstrBot/NapCat 管理 WebUI 可以用 SSH loopback 端口转发独立访问；**小房间是观察页面，备份/恢复/公开写授权是管理操作**，不能因只读 WebUI 需要而给页面部署管理员密钥。

台式机开发实例可通过 `--astrbot-root` 读取本机 LivingMemory 2.9.1。在仓库根目录运行下面的命令。

```powershell
$astrbotRoot = 'D:\Share\code\FishHome\dev\astrbot-4.29.4'
python -B -m deploy.memory_observatory.server --port 8767 --astrbot-root $astrbotRoot `
  --life-data-dir (Join-Path $astrbotRoot 'data\plugin_data\astrbot_plugin_bili_activity')
```

星海入口为 <http://127.0.0.1:8767/>，小房间入口为 <http://127.0.0.1:8767/room>。开发活动账本由上游插件的存储代码初始化，活动关闭时房间显示关闭。活动来源未配置时，小房间显示未知状态。

## 参考

- <https://github.com/whyever77/dafeiyu-astrbot/blob/main/deploy/memory_observatory/ROOM_V02.md>
- <https://github.com/whyever77/dafeiyu-astrbot/tree/main/deploy/memory_observatory>

**M2/M6 必须有真实画面/日志/数据时间戳证据，不凭 repo 中存在 `ROOM_V02.md` 就报告成功。**
