# SkillHome 私人云端同步方案

> 基于 `bin/skillhome.py`（1273 行，单文件零依赖）全量阅读 + `~/.skillhome` 实测：
> 149 skills / 30MB / 2279 文件 / **0 个内部 symlink** / 14 agent 目录 / ~1900 链接。
> 定位：**私人多机同步**，非多人协作。约束：简单优先、可选开启、本地模式不受影响、不公开暴露。

---

## 一、结论

**推荐方案 B（rclone 封装）：在 skillhome.py 中新增 `push` / `pull` 子命令，底层调用 rclone bisync 同步 `~/.skillhome/skills/` 到 Google Drive。**

理由一句话版：**方案 A 在你主力的 Linux 机器上根本跑不起来（Google Drive 没有官方 Linux 客户端），方案 C 是用几百行代码 + OAuth 运维负担去重新发明 rclone，方案 B 是唯一同时满足"三平台可用 / 可选开启 / 一次配置 / 符合 skillhome 手动同步哲学"的选项。**

具体：

1. **架构适配性好。** skillhome 的真相全在 `~/.skillhome/skills/`（真实目录），agent 侧的 ~1900 条链接是纯本地产物，由 `sync` 阶段 3 随时重建。所以跨机同步**只需要搬 `skills/` 这一个目录**，链接层、发现层、冲突合并层一行不用动。同步完跑一次 `cmd_sync(incremental=True)` 就完成本机物化——rclone 天然嵌进现有四阶段流程之前。

2. **方案 A 有硬伤。** Google Drive 桌面端不支持 Linux；Dropbox 虽支持 Linux，但默认的"在线 only"占位符模式下，agent 读 skill 会触发按需下载，且同步中间态（半个目录）对 agent 可见。更根本的问题：它把同步时机交给了一个 skillhome 看不见的黑盒，与项目"no daemon, 手动 sync"的设计哲学相悖。

3. **方案 C 得不偿失。** 直连 Drive API 需要注册 Google Cloud 项目、走 OAuth consent screen（Testing 状态的 app refresh token **7 天过期**）、实现 diff/增量/冲突逻辑——这些都是 rclone 已经做好并经过大规模验证的事。换来唯一好处是少装一个二进制，但代价是打破"零第三方依赖"或手写数百行 urllib OAuth，维护成本最高。

4. **可选性干净。** rclone 是外部二进制，`push`/`pull` 命令检测到 `shutil.which("rclone") is None` 时报清晰错误即可。不装 rclone 的用户看到的 skillhome 和今天完全一样。

5. **顺带的灵活性。** rclone 一次配置后可切任意后端（Drive / Dropbox / OneDrive / S3 / SFTP 自有 VPS），不锁定 Google；headless 服务器可用 `rclone authorize` 在另一台机器完成授权再贴回 token。

> **备选值得一提：私有 git 仓库。** 本仓库 `docs/cloud-dynamization-plan.md` 的 P1 阶段已分析过 git transport——私有 GitHub repo + `push`/`pull` 子命令，免费、自带版本历史、复用现有 SSH key、零新凭据。它和方案 B 是同一形态（手动 push/pull 封装外部工具），实现成本几乎相同，还多送版本回溯能力。如果"记得 push"可以接受，git 其实略优；如果更想要"云盘语义"（不依赖 GitHub、可用 cron 做成准自动），选 rclone。**两者共用同一个实现计划，只是 transport 不同。**

---

## 二、三方案对比

| 维度 | A. 云盘客户端同步 | B. rclone 封装（推荐） | C. Drive API 直连 |
|---|---|---|---|
| **原理** | `skills/` 移入云盘同步目录（或 symlink 指过去），由 Dropbox/Drive 客户端维持多机一致 | `skillhome push/pull` 调 `rclone bisync` 双向同步 `skills/` 到 `gdrive:skillhome/` | 代码内直接调 Drive REST API，OAuth + files.list/md5Checksum diff + 上传下载 |
| **代码改动** | 0 行 | ~120 行（2-3 个新子命令 + subprocess 封装） | 400+ 行（OAuth flow、增量 diff、冲突、分页、重试） |
| **新依赖** | 云盘客户端（重型 GUI 应用） | rclone 单二进制（可选） | google-api-python-client 或手写 OAuth（破零依赖） |
| **Linux 支持** | Drive 无官方客户端 ❌；Dropbox 有 | ✅ 全平台一致 | ✅ 但全靠自维护 |
| **同步时机** | 后台自动、时机不可控 | 手动（可 cron 自动化） | 手动 |
| **冲突语义** | 产生 `conflicted copy` 文件散落 skill 内，skillhome 不认识 | bisync 保留双份为 `*.conflictN`，可见、不丢数据 | 需自己实现，默认 last-writer-wins |
| **原子性** | 差：agent 可读到同步到一半的目录 | 中：文件级逐条，但同步后再 `sync` 刷新链接，对 agent 一致 | 取决于自实现质量 |
| **agent 读离线** | 占位符模式下可能触发网络拉取（需钉住"离线可用"） | 文件始终是本地真实文件 ✅ | 同 B ✅ |
| **凭据/安全** | 云盘账号登录，私有目录天然不公开 | rclone OAuth token 存 `~/.config/rclone/`，scope 可选 drive.file 最小化 | 需自建 GCP 项目；Testing 态 refresh token 7 天过期，需定期重新授权 |
| **多后端** | 锁定单一云盘厂商 | 40+ 后端随时换 | 锁定 Drive |
| **维护成本** | 低（但出问题时难诊断） | 低（rclone 社区维护 transport） | 高（OAuth/API 变更都要自己跟） |
| **对现有资产影响** | `skills/` 目录需搬迁+symlink，config 不能进云盘 | 零搬迁，原地同步 | 零搬迁 |

补充——**什么都不用改的部分**（三方案共有）：agent 目录、~1900 条链接、`discover`、`sync` 四阶段、`link/unlink/global/add` 命令、`config.json` 语义。云同步只发生在 `skills/` 这一层之下。

---

## 三、推荐方案（B）实现计划

### 设计要点

- **同步范围：只同步 `~/.skillhome/skills/`。** `config.json`（绝对路径、本机 agentDirs）、`skillhome.log`、`backups/` 都是机器私有，天然不进云端——通过"只把 `skills/` 当同步根"实现，不需要任何排除规则技巧。
- **`.skillhome.json` 随 skills 一起同步。** `global` 标记和 `merge_notes` 应该跨机传播；`sources` 里混入他机 agent 名无害——`sync` 阶段 3 建链时 `agent_dirs.get(agent_name)` 找不到就跳过（`skillhome.py` L710-L714）。代价只是 meta 文件有噪音。
- **用 `rclone bisync` 而非 `rclone sync`。** bisync 是真双向（本地删了远端也删、两边都改保留双份），私人多机场景下 `sync`（单向镜像）会在"机器 B 本地产生的 skill"上翻车。
- **filter 排除 OS 垃圾**：`.DS_Store`、`Thumbs.db`、`desktop.ini`（bisync `--exclude` 参数即可）。

### 新增命令

| 命令 | 行为 |
|---|---|
| `skillhome pull` | `rclone bisync gdrive:skillhome/skills ~/.skillhome/skills` → 成功后 `cmd_sync(incremental=True)` 刷新链接 |
| `skillhome push` | 先 `cmd_sync(incremental=True)` 收敛本地 → 再 `rclone bisync`（bisync 本身双向，push/pull 共用同一调用，差别只在 pull 后跑本地 sync） |
| `skillhome sync --cloud` | `pull` + `push` 的便利组合 |
| `skillhome remote` | 显示/设置 config.json 里的 `cloudRemote` 字段（如 `gdrive:skillhome`） |

首次使用 `rclone bisync --resync` 建立基线（把首个 `--resync` 暴露为 `skillhome pull --init` 或自动检测）。

### 步骤

1. **用户侧一次性配置**：`apt install rclone`（或官网包）→ `rclone config` → 选 Google Drive，浏览器完成 OAuth。headless 机器用 `rclone authorize "drive"` 在有浏览器的机器上取 token 贴回。
2. **config.json 增量**：新增可选字段 `"cloudRemote": "gdrive:skillhome"`。缺省 = 未启用，`push/pull` 提示如何开启。
3. **代码增量**（全部新增，不改现有函数）：
   - `_require_rclone()`：`shutil.which("rclone")` 守卫，缺失时报安装指引；
   - `_rclone_bisync()`：拼参数（`--resilient --check-access=false --conflict-larger --exclude` OS 垃圾），subprocess 调用，返回码非 0 时不跑本地 sync 并原样透出 rclone 输出；
   - `cmd_pull()` / `cmd_push()` / `cmd_remote()`；
   - `main()` 加三个分支 + `sync --cloud` 分支；`cmd_help()` 加说明。
   - 预计 +120~150 行，仍是单文件零第三方包（rclone 是外部进程，不是 Python 依赖）。
4. **新机器 bootstrap 文档**：装 rclone → `rclone config` → `skillhome remote gdrive:skillhome` → `skillhome pull` → `skillhome init`（发现本机 agent 目录）→ `skillhome sync`。README 加一节。
5. **（可选）准自动化**：`cron`/`systemd timer` 每日 `skillhome pull && skillhome sync`。写进文档作为可选配方，不做进代码——守住"no daemon"承诺。

### 冲突与失败语义

- 两台机器离线改同一 skill → bisync 保留双方，败者命名 `xxx.conflict1`，`skillhome list`/肉眼可见，手动取舍——与现有"`<95% 相似度保留双份`"哲学一致。
- bisync 中途断网 → 下次重跑自愈（bisync 有自己的 listing 状态）；本地 `sync` 只在 bisync 成功后执行，agent 永远不会看到半成品链接。
- rclone 不存在 / 未配置 remote → 命令报清晰错误退出，本地功能零影响。

### 风险与限制（如实说明）

- **bisync 官方仍标 beta**（多年如此，实际成熟）。保守替代：`pull` 用 `rclone copy --update`、新机器初始化用 `rclone copy`，牺牲双向删除换稳定。
- **手动纪律**：和 git 一样要"记得 push"。这是所有"无 daemon"方案的固有代价，用 cron 配方缓解。
- **`sources` 噪音**：`.skillhome.json` 会累积其他机器的 agent 名。当前无害；将来做 `machineId` 前缀（cloud-dynamization-plan 的 P0-3）可根治，但不阻塞本方案。
- **token 安全**：rclone.conf 里的 refresh token 等价于 Drive 访问权，已是用户态文件权限保护，不新增暴露面；skills 本身不敏感，Drive 私有目录满足"不公开暴露"。

---

## 四、同步前自动备份（已实现补充机制）

bisync 是双向同步，最坏情况（误判删除、resync 覆盖、冲突取舍失误）会直接改写本地 `skills/`。方案 B 上线后在 `_cloud_run()` 前置了一道**本地快照**兜底：任何 `cloud pull` / `push` / `sync` 在触碰数据之前先完成备份，**备份失败即终止同步**——绝不在无备份状态下跑 bisync。

### 备份内容

只备份与恢复所需的最小集合，写入 `~/.skillhome/backups/pre-cloud-sync-<YYYYMMDD-HHMMSS>/`：

| 内容 | 备份内路径 | 说明 |
|---|---|---|
| `~/.skillhome/skills/` | `skills/` | 整个中央仓库；每个 skill 的 `.skillhome.json` 元数据随目录一起备份 |
| `~/.skillhome/config.json` | `config.json` | 运行时配置（agentDirs、cloudRemote 等） |

不进备份：`skillhome.log`（可再生的日志）、`backups/` 自身（避免递归膨胀）、其他临时文件。

### 时序

```
cloud pull/push/sync
  └─ _cloud_require_ready()   # rclone / config / remote 守卫
  └─ remote 注册检查
  └─ _cloud_backup()          # ← 新增：skills/ + config.json -> backups/
  │     ├─ 磁盘空间检查（free < 预估大小 → 终止）
  │     ├─ copytree/copy2（异常/Ctrl+C → 清理半成品目录 → 终止）
  │     └─ _cloud_backup_rotate()  # 保留最近 3 份，超出删除并打日志
  └─ cmd_sync 收敛 / rclone bisync / 刷新链接   # 原有流程不变
```

- 备份成功才进入后续任何变更步骤；bisync 失败也保留备份。
- `--dry` 不改动任何数据，跳过备份。
- `restore` 复用 `_cloud_backup()` 先备份当前状态（可回退），并对恢复源加 `protect` 豁免轮转，避免"恢复最旧备份时被自己的安全备份轮转掉"。
- 恢复 `skills/` 采用 staging 目录先挪后拷，失败自动回滚原目录。

### 新增子命令

| 命令 | 行为 |
|---|---|
| `skillhome cloud backups` | 列出全部 `pre-cloud-sync-*` 备份（名称/大小/文件数，新→旧） |
| `skillhome cloud restore <backup-name>` | 从指定备份恢复 `skills/` 与 `config.json`（先备份当前状态） |

### 与既有 `backups/` 用法的关系

`backups/` 此前已被 `add` 命令复用（覆盖安装时旧 skill 存为 `<name>.bak.<ts>`）。两类产物共存：轮转只匹配 `pre-cloud-sync-` 前缀，不会误删 `.bak.` 目录；反之亦然。
