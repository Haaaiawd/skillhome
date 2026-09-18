# SkillHome UX 调研与改进方案

> 调研对象：`bin/skillhome.py`（1909 行，单文件零依赖）+ 本机 `~/.skillhome/` 实测。
> 实测环境：225 个中央 skill、14 个 agentDirs、`cloudRemote: gdrive:`。
> 本报告只做调研与方案设计，不改代码。

---

## 1. 现状分析

### 1.1 当前完整工作流

```
安装:  npx skills add Haaaiawd/skillhome -g   (只装 SKILL.md，脚本不进 ~/.skillhome/bin/)
首次:  python3 bin/skillhome.py init          (实际 = discover(force=True)，扫描写 config.json)
收敛:  python3 bin/skillhome.py sync          (迁移真实目录→中央 + 建 symlink)
云同步: python3 bin/skillhome.py cloud remote set gdrive:
        python3 bin/skillhome.py cloud pull|push|sync
使用:  agent 通过 symlink 读 ~/.skillhome/skills/<name>
```

### 1.2 传播链路（cloud → 本地 → Hermes）

```
gdrive:skillhome/skills
   ↕ rclone bisync          (pull/push/sync 前自动备份；冲突 → *.conflictN)
~/.skillhome/skills/        (中央仓库 + .skillhome.json 元数据)
   ↑ sync 阶段2: shutil.move 迁移 agent 侧真实目录
   ↓ sync 阶段3: os.symlink 分发；global 标记 → 扩散到全部 agentDirs
~/.agents/skills  ~/.hermes/skills  ~/.config/devin/skills  ...
```

**fanout 机制其实已存在**：`_cloud_run` 在 pull/sync 成功后会调 `cmd_sync(incremental=True)` 重建链接（L1643-1649）。问题在于它被三道闸门挡着。

### 1.3 缺失环节（含本机实证）

| # | 缺失 | 实证 |
|---|------|------|
| 1 | **fanout 以 bisync 成功为前提**，失败即整体 return | 今日 3 次 cloud 运行（03:16/03:18/03:25）log 都重复"首次同步"，`cloudLastSync` 从未写入 → bisync 未成功 → 链接刷新被跳过 |
| 2 | **rclone 输出不落日志**，失败原因事后不可查 | `_run_bisync` 只 print 到 stdout（L1582-1585），log 里既无成功记录也无失败详情 |
| 3 | **agentDirs 为空 → fanout 静默跳过** | `_cloud_run` L1644 的 `if cfg.get("agentDirs")`；且**空 skills 目录无法被 discover 识别**（`is_skill_repo` 需 ≥1-2 个含 SKILL.md 的子目录），新装的 Hermes 空目录永远进不了 config |
| 4 | **云端删除的 skill → agent 侧死链不清理** | sync 阶段3 只"补缺失"不"删多余"；实测 `~/.hermes/skills` 有 2 条死链（`ai-image-generation`、`image-create`），需 `sync --full` 才重建 |
| 5 | **无全局命令** | `~/.skillhome/bin/` 是空目录，PATH 中无 `skillhome`，shell 无 alias；唯一入口是 repo 里的 `python3 bin/skillhome.py` |
| 6 | **init 无编排** | `init` 与 `discover` 是同一函数（L1860-1863），不装命令、不查 rclone、不提示 remote、不做首次 pull |
| 7 | **链式链接不修复** | `~/.hermes/skills` 有 32 条 symlink 指向 `../../.agents/skills/*` 而非中央，`is_link()` 视为健康，sync 不纠正 |

---

## 2. UX 改进方案（按用户反馈的三个问题）

### 问题一：云同步后不自动扩散

- **问题描述**：期望 `cloud pull` 后 Hermes 立刻能看到新 skill；实际 fanout 依赖 bisync 成功 + agentDirs 非空 + 非 dry，任一不满足就静默停在中央仓库。
- **解决方案**：fanout 改为默认动作并显式报告；bisync 失败时把 rclone 输出 tee 进 `skillhome.log` 并给出修复指引；agentDirs 为空时触发一次受限 discovery 兜底；新增 `--prune` 清理死链。
- **命令设计**：`skillhome cloud pull`（自动扩散，输出"新建 N 链接/清理 M 死链"）、`--no-fanout` 关闭、`--dry` 时连 fanout 也 dry-run。
- **副作用评估**：fanout 只补缺失链接、不覆盖真实目录（`create_link` 本就 only-when-absent）；`--prune` 只删"指向中央的 symlink"，不碰真实目录和外部链接，安全。风险点是"云端误删→全网死链"，靠现有 pre-sync 备份 + `cloud restore` 兜底。

### 问题二：没有 `skillhome` 全局命令

- **问题描述**：期望任意目录敲 `skillhome <cmd>`；实际必须 `python3 <repo>/bin/skillhome.py`，`~/.skillhome/bin/` 空、无 PATH 注册。
- **解决方案**：新增 `skillhome install`（并入 init）——把脚本物化到 `~/.skillhome/bin/skillhome.py`，在 `~/.local/bin/` 写 3 行 shim；检测 PATH 不在则打印 shell 配置提示。Windows 写 `skillhome.cmd`。备选：pyproject + `pipx install`，但破坏单文件分发，不推荐。
- **命令设计**：`skillhome install`（幂等，可重复跑）；装好后全命令直接用。
- **副作用评估**：只新增两个文件；已存在 `skillhome` 命令时提示而非覆盖；不污染 repo。

### 问题三：首次使用体验差

- **问题描述**：期望一条命令完成装机；实际要手动跑 init→sync→rclone config→remote set→pull，五步且顺序不直观。
- **解决方案**：`init` 升级为编排器：① 自安装（脚本+shim）→ ② discover（merge 语义，含空目录登记）→ ③ 检测 rclone/remotes，有 remote 未配置时提示一键 set → ④ 可选首次 `cloud pull` + fanout → ⑤ 输出下一步摘要。
- **命令设计**：`skillhome init`（交互式）；`skillhome init -y` 全非交互默认；`--no-cloud` 跳过云端；`--dry` 全预览。
- **副作用评估**：现 `discover` 会**整体覆盖** agentDirs（新机器上是清空已配置项的隐患），改 merge 后可避免；每步可跳过；不删任何已有数据。

---

## 3. 具体改进项

### 3.1 自动扩散机制

**现状**：pull/sync 成功后已调 `cmd_sync(incremental=True)`，但有三道静默闸门 + 无死链清理。

**方案**：
1. fanout 无条件报告化——`_cloud_report` 增加"新建链接 X / 清理死链 Y / 跳过 agent Z"计数。
2. `cloud pull --dry` 时追加 `cmd_sync(dry_run=True)`，让用户预览扩散结果（现在 dry 直接跳过整段）。
3. bisync 失败：rclone 全程输出 tee 到 `skillhome.log`（现在只进 stdout，事后零线索），并打印"可重跑 / `--resync` / `cloud restore`"三选一指引。
4. 新增 `skillhome sync --prune`：删除指向中央但目标已消失的 symlink；`cloud pull` 成功后自动跑（仅删 symlink，绝不删真实目录）。
5. agentDirs 为空 → 自动跑一次快速 discovery（只探测 KNOWN_PATTERNS，不做深度扫描）再 fanout；仍为空才提示 init。

**冲突策略**（云端 vs 本地同名）：沿用现有两层——文件级由 bisync 存 `*.conflictN`；skill 级由 sync 相似度规则收敛（≥0.95 取新、<0.95 单一真实副本同名更新+备份、多副本 `--` 后缀保留且不 global）。云端拉来的新 skill 无 meta → 默认 global → 全量扩散（已实现，保留）。**不新增策略，只补齐执行与可见性。**

### 3.2 全局命令注册

**方案**：`skillhome install` 子命令（同时被 `init` 调用）：
1. `cp bin/skillhome.py ~/.skillhome/bin/skillhome.py`（解决 bin/ 空目录的现状——SKILL.md 里文档化的路径本来就不存在）
2. 写 `~/.local/bin/skillhome`：
   ```sh
   #!/bin/sh
   exec python3 "$HOME/.skillhome/bin/skillhome.py" "$@"
   ```
   chmod +x；Windows 对应写 `%USERPROFILE%\bin\skillhome.cmd`。
3. `shutil.which("skillhome")` 检测：已存在非本工具命令 → 警告不覆盖；`~/.local/bin` 不在 PATH → 打印对应 shell 的 export 行。

**副作用**：零覆盖（幂等+存在检测）；更新方式 = 重跑 install。

### 3.3 首次运行体验

**`skillhome init` 编排五步**（每步可跳过，`-y` 全自动）：

1. **install**：3.2 的自安装。
2. **discover（merge 模式）**：现有扫描逻辑不变，但改为 merge——保留已配置的 agentDirs，新增发现的；并**登记"存在但为空"的已知目录**（如 `~/.hermes/skills`），解决"空目录无法被发现 → 永远收不到扩散"的结构性缺口。登记前逐条确认（或 `-y` 全收）。
3. **cloud 检测**：`shutil.which("rclone")` + `rclone listremotes` → 若已有 remote 且未配置 `cloudRemote`，列出可选项让用户挑（免手敲）；无 rclone → 打印安装指引并跳过。
4. **首次 pull**：配置了 remote 时询问是否 `cloud pull`（自动走 3.1 的扩散+报告）。
5. **sync + 摘要**：跑增量 sync，输出 per-agent 链接数 + 死链数 + 下一步提示。

**副作用**：不删数据、不覆盖 config 已有键；空目录登记只是把路径写进 agentDirs，不创建目录（目录不存在时 sync 会安全跳过——`agent_dir.is_dir()` 守卫已存在）。

### 3.4 其他 UX 优化建议

按价值排序：

1. **`skillhome doctor`（新增，强烈建议）**：一键体检——bin/ 脚本在不在、shim 在不在 PATH、死链数、链式/外部链接数（本机实测 32 条链式 + 2 条死链 sync 完全看不见）、残留真实目录、cloudLastSync 距今、remote 注册状态。`doctor --fix` 修复死链+纠正链式链接为指向中央。
2. **日志门禁**：`log()` 现在无级别过滤，DEBUG 永远打印（`--verbose` 是死参数）。加全局 level：默认 INFO，`-v` 开 DEBUG，`-q` 只留 WARN/ERROR。
3. **`status` 增强**：追加 cloudLastSync、死链数、"存在但未登记的已知目录"提示行——把"该跑什么"直接告诉用户。
4. **discover merge 语义**（同 3.3-2）：默认 merge，`--replace` 才整体覆盖；否则新机器/重装会静默丢配置。
5. **`cloud restore` 后自动 fanout**：现在只 print "建议运行 sync"，直接跑掉更顺。
6. **`.bak.*` 备份轮转**：冲突备份 `name.bak.<ts>` 无上限（只有 pre-cloud-sync 轮转保留 3 份），加 `clean` 命令或并入轮转策略。
7. **`add` 元数据修正**：本地安装写 `sources: [绝对路径]`，该字段语义是 agent 名——改 `sources: ["local"]` 或留空，避免噪音。
8. **入口统一**：`sync --cloud` 与 `cloud sync` 功能重叠，保留 `cloud sync`，`sync --cloud` 打印 deprecation 指向。
9. **发现粒度提示**：`discover` 把 Hermes 自带的 11 个 `hermes-agent/*/skills` 内建仓库也收编进中央（本机 config 可见）——可能非用户本意，`init` 时对"疑似工具内建仓库"给出标注，让用户 opt-out。

---

## 附：方案优先级建议

| 优先级 | 项 | 理由 |
|---|---|---|
| P0 | 3.2 全局命令 + 3.3 init 编排 | 直接命中两个用户痛点，纯增量、零风险 |
| P0 | 3.1-3 bisync 输出落 log | 没有它，"同步了但没扩散"永远不可诊断（本机已在发生） |
| P1 | 3.1-1/2/4 fanout 报告 + dry 预览 + prune | 扩散可见化 + 死链治理 |
| P1 | 3.4-1 doctor | 把上述所有状态问题变成一条命令可查 |
| P2 | 3.4 其余项 | 打磨项，可随做随加 |
