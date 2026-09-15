# SkillHome 云端动态化改造方案

> 基于 `bin/skillhome.py`（1273 行，单文件零依赖）与 `skills/skillhome/SKILL.md` 的架构分析。
> 实测现状：`~/.skillhome/skills/` 149 个 skill、14 个 agent 目录、约 1900 个 symlink，全部链接健康、残留真实目录 ≈ 0。

---

## 一、结论先行

**当前架构不能直接支持"云端动态"，但骨架是对的。**

- **不是瓶颈的部分（反而可保留）**：symlink/junction 分发模型。Agent 只认本地路径，不关心链接指向哪里——这意味着"云端内容落地到哪里"对 agent 完全透明，中央目录可以平滑退化为"云端缓存的物化层"，14 个 agent 目录、约 1900 条链接一行都不用动。
- **真正的瓶颈有三个，按严重程度排序**：

| # | 瓶颈 | 为什么是瓶颈 |
|---|------|-------------|
| 1 | **中央仓库没有"版本身份"** | 真相 = "哪个目录存在 + mtime 谁新"。SHA256 相似度只做两两比较、用完即弃，不落盘。云端多写者（多机器/多用户）收敛必须依赖确定性的 content hash + 版本号，现在完全没有。这是根本障碍。 |
| 2 | **真相在本地文件系统，无远程层** | 代码里没有任何 transport 抽象：`sync` 的输入只有 config.json 里的本地路径。`Path.home()` 直写绝对路径进 config（`userProfile`、`centralSkills`、`agentDirs`），天然单机绑定。 |
| 3 | **元数据模型混了三层语义** | `.skillhome.json` 同时存：skill 身份（name）、分发状态（sources/global）、同步历史（merged/merge_notes）。sources 里记的是**本机 agent 名**（路径推导），跨机器没有命名空间。云端化后"谁部署给谁"必须和"skill 是什么"解耦。 |

次要瓶颈（不致命但要处理）：

- `sync` 是**就地突变**（`shutil.move`/`rmtree`），无 staging、无原子性、无锁——云端 pull 到一半断网会留下半个仓库。
- 冲突合并是**有损**的：≥95% 相似时直接删旧留新。单机可接受，多写者下会静默吃掉另一台机器的合理改动。
- 零依赖约束（stdlib only）是卖点也是枷锁：没有 HTTP 客户端习惯、没有 auth 概念。好在 `urllib` 够用，或可把 transport 交给 git/rsync 这类外部工具。

---

## 二、目标形态

```
            ┌──────────────────────────────┐
            │      Remote Registry          │
            │  (git repo / S3 / HTTP 服务)  │
            │  manifest + 内容寻址存储       │
            └──────────┬───────────────────┘
                       │  pull / push（确定性版本）
            ┌──────────▼───────────────────┐
            │   ~/.skillhome/skills/        │
            │   本地物化层（= 云端缓存）      │
            │   manifest.json（版本索引）     │
            └──────────┬───────────────────┘
                       │  symlink/junction（不变）
        ┌──────┬───────┼───────┬──────┐
        ▼      ▼       ▼       ▼      ▼
     .agents  .devin  .claude  ...  .hermes
```

核心思想：**中央仓库从"唯一真相"降级为"真相的本地投影"**。链接层、发现层、分发逻辑全部不动，只在中央仓库背后接一条可重放的同步通道。

---

## 三、分阶段方案

### P0 —— 本地数据模型云端就绪（纯本地改造，无网络）

**改什么**

1. **引入中央 manifest**：新增 `~/.skillhome/manifest.json`，记录 `name → {version, content_hash(整目录树哈希), sources, global, updated_at}`。把现在散在 149 个 `.skillhome.json` 里的状态收敛成单一索引；`.skillhome.json` 保留写入以兼容，但 manifest 为权威。
2. **内容寻址**：把 `get_dir_hashes()` 的结果（相对路径→SHA256）固化进 manifest，作为每个 skill 的 `content_id`。冲突判断从"两两现算"变成"和 manifest 里的哈希比"。
3. **机器身份**：config.json 增加 `machineId`（hostname+随机 salt），`sources` 语义升级为 `agent@machine` 可选格式；老格式无前缀时视为本机。
4. **导出能力**：新增 `skillhome export` → 生成 `skills-bundle.tar.gz + manifest`，作为 P1 一切 transport 的最小公倍数。

**不动什么**

- `sync` 四阶段流程、链接创建/删除、`discover` 扫描逻辑、`.skillhome.json` 文件本身——全部原样。
- 149 个 skill 目录和约 1900 条链接零改动；manifest 是增量产物，可由现有状态一次性重建。

**风险**

- manifest 与 `.skillhome.json` 双写不一致 → 定一个权威（manifest），`.skillhome.json` 降级为只读副本；加 `skillhome doctor` 校验一致性。
- 工作量低，约 200-300 行增量。

---

### P1 —— 接入远程同步（单机 → 多机）

**改什么**

1. **Transport 抽象**：`remote` 层只定义两个动作——`fetch() → staging dir`、`publish(manifest delta)`。首选实现用 **git**（`~/.skillhome/skills` 初始化为 git repo 或独立 bare remote）：
   - 零新依赖、自带版本历史与认证（SSH/HTTPS token）、天然适配"内容寻址 + 快照"。
   - 备选：S3/OSS + manifest（适合多人共享 registry）、自建 HTTP registry（P2 再考虑）。
2. **原子 pull**：远程内容先落到 `~/.skillhome/staging/`，校验 manifest 完整性后原子替换（目录改名 swap），成功后再跑现有 `sync --incremental` 刷新链接。失败则 staging 丢弃，中央不动——**链接永远不指向半成品**。
3. **跨机冲突策略**：复用现有相似度机制，升级为三路——本地版 vs 远程版 vs manifest 记录的 base 版：
   - 只有一边改 → 取改的一边；
   - 两边都改且 sim ≥ 阈值 → 取新（记录到 merge_notes）；
   - sim < 阈值 → 保留为 `name--machineId` 变体（现有 `--` 后缀机制直接复用，天然不 global）。
4. **新命令**：`skillhome remote add <url>` / `push` / `pull` / `remote status`。

**不动什么**

- agent 目录结构、链接指向（仍指向 `~/.skillhome/skills/<name>`）、`add`/`link`/`unlink`/`global` 命令语义。
- `global: true` 的分发语义仍只作用于**本机** agent 目录——是否全局是部署决策，不随 skill 内容跨机传播（各机的 agentDirs 本就不同）。

**风险**

- 多机并发 push 的 last-writer-wins → git 天然要求先 pull 再 push，把冲突暴露在 pull 侧，由三路合并消化。
- 凭据管理：git 方案复用用户现有 SSH key，不引入新 secret；S3 方案需要密钥——这是选 git 做 P1 的主要原因。
- 体积：149 skills 以 markdown 为主，git 仓库很小，无 LFS 需求（若将来含大二进制再评）。

---

### P2 —— 动态化（手动 sync → 按需/自动）

**改什么**

按"动态"的强弱定义，三个可独立落地的增量：

1. **定时/触发同步**（最弱，收益最直接）：systemd timer / cron / agent 启动 hook 调 `skillhome pull && sync`。无 daemon，符合项目"无后台服务"哲学的最小妥协。
2. **懒物化**（中）：agent 链接的目标从 `skills/<name>` 改为 `cache/<name>`，cache 缺失时由 `skillhome ensure <name>` 回填。symlink 本身无法触发拉取，所以需要 wrapper：agent 侧放 stub 或靠 SKILL.md 索引预拉。**注意这一步会改变链接目标路径**——需要一次 `sync --full` 重建，是三期里唯一触碰现有 1900 条链接的动作。
3. **Registry 模式**（最强）：`skillhome install <name>@<ver>`、`skillhome outdated`、订阅/发布模型；自我进化 skill（Hermes 类 agent 自动写的）自动 push 回 registry，其他机器下次 pull 即得。此时中央仓库彻底变成 read-through cache。

**不动什么**

- 发现机制（`discover` 仍只管本机目录）、冲突合并语义、单文件 CLI 形态（可拆模块但保持单文件分发）。

**风险**

- 懒物化失败 = agent 看到死链。必须有兜底：prefetch 白名单、失败回退到上次缓存、`ensure` 离线时用旧版本。
- 动态拉取引入 supply-chain 面：远程 skill 需签名或至少 hash pinning（manifest 里的 content_id 正好干这个）。
- 自动 push 自我进化 skill 有污染 registry 的风险 → 建议进 `staging/` 命名空间人工 promote，而非直推主仓。

---

## 四、兼容性承诺（硬约束）

| 资产 | 保证 |
|------|------|
| 149 个中央 skill 目录 | P0/P1 不移动、不改名、不改内容；manifest 从现状重建 |
| ~1900 条 symlink | P0/P1 完全不触碰；P2-2（懒物化）是唯一需要重建的环节，且由 `sync --full` 自动完成、可回滚 |
| `config.json` | 只增字段（machineId、remote），旧字段原样 |
| `.skillhome.json` | 继续写入，格式向后兼容 |
| CLI | 现有命令签名不变，新能力全是新增子命令 |

---

## 五、建议：从哪期开始最划算

**直接做 P0，且只做 P0 的前两项（manifest + content_id）。**

理由：

- **P0 是纯收益**：不动任何现有行为，就为后续一切云端能力备好地基。没有 content_id，P1 的三路合并无从谈起——这是必须先付的"技术债首付"。
- **P1 的运输层建议先选 git**：成本最低（一个 remote add 就有版本历史 + 认证 + 原子性）、可逆（不行就删 remote）、且覆盖了"多机同步"这个云端化 80% 的真实价值。自建 registry 放到真正需要多人共享/权限模型时再说。
- **P2 缓行**：当前 149 skills 全量同步秒级完成，"动态拉取"更多是心智负担而非性能必需。先用 P1 的 `pull && sync` 跑通多机，等真实出现"机器多了同步烦"或"自我进化 skill 跨机传播"的痛点，再决定懒物化还是 registry。
- 一句话排序：**P0（必做，先决条件）→ P1-git（高性价比主菜）→ P2 按需点菜**。
