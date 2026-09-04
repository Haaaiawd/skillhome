<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:6C5CE7,50:00B894,100:0984E3&height=180&section=header&text=SkillHome&fontSize=70&fontColor=ffffff&animation=fadeIn&fontAlignY=38&desc=AI%20Agent%20%E7%BB%9F%E4%B8%80%20Skill%20%E7%AE%A1%E7%90%86&descSize=18&descAlignY=56" width="100%"/>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="#痛点">痛点</a> · <a href="#安装">安装</a> · <a href="#自动检索">自动检索</a> · <a href="#命令">命令</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/Windows-NTFS-0078D6?style=for-the-badge&logo=windows&logoColor=white"/>
  <img src="https://img.shields.io/badge/Linux-symlink-FCC624?style=for-the-badge&logo=linux&logoColor=black"/>
  <img src="https://img.shields.io/badge/macOS-symlink-000000?style=for-the-badge&logo=apple&logoColor=white"/>
  <img src="https://img.shields.io/badge/License-MIT-00B894?style=for-the-badge"/>
</p>

---

> *"你的 Agent 可以有很多个，但 Skill 应该只有一个家。"*

SkillHome 把分散在所有 AI Agent 里的 skill 统一收归到一个中央仓库。各 Agent 通过 junction/symlink 读取——一个源头，没有重复副本，不会静默丢失。自动发现按结构特征识别 skill 仓库（不依赖目录名），冲突处理按内容相似度合并或保留变体，全局共享让新 skill 默认分发给所有 Agent。

没有守护进程，没有后台服务——需要时跑一次 `skillhome sync` 就行。

## 痛点

### 1. 多 Agent 协同时的 Skill 共享

你同时用 Codex、Devin、Claude Code、Windsurf、Cursor、Gemini——每个 agent 有自己的 skill 目录。给一个 agent 装了 skill，其他 agent 看不到。你手动复制，skill 更新后副本过期，你搞不清哪个版本在哪。

**SkillHome 怎么解决：** 添加一次 skill → 进入中央仓库 → 自动分发给所有 agent。新 skill 默认 `global`——一次 `skillhome add` 或 `skillhome sync`，机器上每个 agent 都能用。

### 2. 多环境下的 Skill 集中管理与防丢失

你的 skill 散落在云服务器、多个 Claude/Codex 环境、不同用户配置里。有的在 `~/.codex/skills`，有的在 `~/.config/devin/skills`，有的在你忘了的地方。路径变了、环境重建了，skill 就静默丢失——agent 就是找不到，也不报错。

**SkillHome 怎么解决：** 所有 skill 收归到 `~/.skillhome/skills/`。各 agent 目录变成 junction 指向中央。AI 永远知道去哪找 skill——一个路径，一个源头。不再因为路径漂移而"skill not found"。

### 3. 自我进化的 Skill 被遗忘

像 Open Cloud 和 Hermes 这类 agent，会通过自我学习自己写 skill——但它们存到用户从不会去检查的隐蔽路径。更新、重装、配置重置之后，这些自我进化的 skill 就消失了。AI 提升了自己，然后忘了提升在哪。

**SkillHome 怎么解决：** 自动检索按**结构特征**扫描，不依赖目录名。一个目录只要有 ≥2 个子目录含 `SKILL.md`，它就是 skill 仓库——不管它叫什么。只要 skill 遵循标准的 `SKILL.md` / `.skill-metadata.yaml` 约定，就能被发现、收归、保存在中央仓库。不会丢。

## 架构

```
                    ┌─────────────────────────────────────────┐
                    │         ~/.skillhome/skills/             │
                    │           （中央仓库）                    │
                    │                                         │
                    │   crux/  architect-prompts/  docx/  ...  │
                    └──────────────┬──────────────────────────┘
                                   │
              ┌────────┬───────────┼───────────┬────────┐
              │        │           │           │        │
              ▼        ▼           ▼           ▼        ▼
         .codex/    .devin/    .claude/   .cursor/  .gemini/
         skills/    skills/    skills/    skills/   skills/
              │        │           │           │        │
           junction  junction  junction  junction  junction
```

每个 agent 看自己的 skill 目录，跟以前一模一样。文件是真实的、可读的，行为和本地目录完全一致——但它们都指向同一个源头。

## 自动检索

核心卖点。`skillhome init` 扫描你的用户目录，找到每一个 skill 仓库——**不需要知道它们叫什么名字**。

### 工作原理

**阶段一——快速探测：** 检查 ~30 个已知 agent 路径（`~/.codex/skills`、`~/.claude/skills` 等）。快速，覆盖常见情况。

**阶段二——特征识别深度扫描：** 递归扫描所有 `.` 开头的目录（深度 3）。对每个目录，检查**结构**，不看名字：

```
这个目录是 skill 仓库吗？
  │
  ├── ≥2 个子目录有 SKILL.md 或 .skill-metadata.yaml → 是
  │   （多个 skill 条目 = 强信号）
  │
  ├── 1 个子目录有 SKILL.md + 目录名含 "skill" → 是
  │   （名字是弱确认）
  │
  └── 其他 → 不是
```

这意味着：
- 任何 agent 的 `skills/` 目录下有多个含 SKILL.md 的子目录 → **能找到**
- 任何自我进化的 skill 仓库，只要遵循约定 → **能找到**
- `~/.cache/something` 有个随机 SKILL.md → **不会找到**（只有 1 个，不是仓库）

### 排除什么

VSCode 扩展、Trae 内置目录、Codex `vendor_imports/curated`、`node_modules`、缓存目录。这些里面的 skill 属于扩展或工具本身，不是你的。

## 安装

```bash
npx skills add Haaaiawd/skillhome -g
```

或从 [GitHub Releases](https://github.com/Haaaiawd/skillhome/releases/latest) 下载 `skillhome.zip`。

> **前提：** Python 3.8+。零第三方依赖。

## 命令

| 命令 | 作用 |
|---|---|
| `skillhome init` | 首次初始化。自动发现所有 skill 目录，生成 `config.json` |
| `skillhome discover` | 重新扫描（装了新 agent 后运行） |
| `skillhome sync` | 迁移新 skill 到中央 + 修复缺失的 junction |
| `skillhome sync --full` | 完整同步：重建所有链接（用于修复失效链接） |
| `skillhome status` | 查看中央 skill 数、junction 数、各 agent 分布 |
| `skillhome list` | 列出所有 skill 及其来源 |
| `skillhome link <skill> <agent>` | 把某个 skill 暴露给指定 agent |
| `skillhome unlink <skill> <agent>` | 从指定 agent 移除某个 skill |
| `skillhome global <skill> [on\|off]` | 设置/取消全局共享（sync 时自动扩散到所有 agent） |
| `skillhome add <source> [options]` | 包装 `npx skills add`，安装后自动 sync 到中央 |
| `skillhome config` | 查看当前配置 |
| `skillhome help` | 显示帮助 |

**可选别名：**

Windows（PowerShell profile）：
```powershell
function skillhome { python "$env:USERPROFILE\.skillhome\bin\skillhome.py" @args }
```

Linux/macOS（`~/.bashrc` 或 `~/.zshrc`）：
```bash
alias skillhome='python3 ~/.skillhome/bin/skillhome.py'
```

## 工作原理

### 同步逻辑

`skillhome sync` 分四个阶段：

1. **扫描** — 读取 `config.json` 中所有 agent 目录，区分真实目录和 junction
2. **迁移** — 把真实 skill 目录移入中央仓库，原位置替换为 junction
3. **冲突处理** — 两个 agent 有同名 skill 时：
   - 相似度 ≥ 95%（按文件哈希）→ **合并**，保留较新版本，来源合并记录
   - 相似度 < 95% → **都保留**，给变体加来源后缀（如 `docx--gemini`）
4. **分发** — 为每个应该看到该 skill 的 agent 创建 junction

### 全局共享

默认情况下，每个新 skill 自动标记 `global: true`。`skillhome sync` 会在所有 agent 目录下创建 junction——不只是来源 agent。

例外：冲突变体（名字带 `--` 后缀）不会 global。你需要手动选择共享哪个版本：

```
# 把 Gemini 的 docx 共享给 Devin
skillhome link docx--gemini devin

# 取消某个 skill 的全局共享
skillhome global some-skill off
```

### 冲突处理

```
Agent A 有 "docx"（QoderWork 的实现）
Agent B 有 "docx"（Gemini 的实现）
                │
                ▼
    文件哈希对比（SHA-256）
                │
        ┌───────┴───────┐
        ▼               ▼
   ≥ 95% 相似       < 95% 不同
        │               │
        ▼               ▼
    合并：保留      都保留：
    较新版本        docx（A 的版本）
    来源合并        docx--B（B 的版本）
```

两个版本都存活，不丢数据。每个 skill 目录下的 `.skillhome.json` 记录其来源 agent。

## 配置

`~/.skillhome/config.json` 是运行时配置，由 `skillhome init` 自动生成：

```json
{
  "version": "1.0",
  "userProfile": "/home/you",
  "centralSkills": "/home/you/.skillhome/skills",
  "agentDirs": {
    "codex": "/home/you/.codex/skills",
    "devin": "/home/you/.devin/skills",
    "claude": "/home/you/.claude/skills"
  },
  "skipNames": [".system", ".git", ".temp", "_shared"],
  "similarityThreshold": 0.95
}
```

编辑 `agentDirs` 可以添加新 agent 或移除不需要管理的。

## 平台支持

| 平台 | 链接类型 | 需要管理员权限 |
|---|---|---|
| Windows | NTFS junction | 否 |
| Linux | symlink | 否（对 skill 目录有写权限即可） |
| macOS | symlink | 否（对 skill 目录有写权限即可） |

SkillHome 通过 `platform.system()` 自动检测平台，使用对应的链接方式。发现模式按平台区分——Windows 探测 `~\.codex\skills`，Unix 探测 `~/.codex/skills` 和 `~/.config/codex/skills`。

## 文件结构

```
~/.skillhome/
├── config.json                # 运行时配置（自动生成）
├── skills/                    # 中央仓库（真实文件）
│   ├── crux/
│   │   └── .skillhome.json    # 元数据：来源、global 标记
│   ├── architect-prompts/
│   └── ...
├── bin/
│   └── skillhome.py           # 单文件 CLI（全部命令）
└── skills/
    └── skillhome/
        └── SKILL.md           # Skill 定义（供 skills.sh）
```

## 可移植性

零硬编码。所有路径从 `Path.home()` 推导。部署到另一台机器：

1. 用上面任一方式安装
2. 运行 `skillhome init`
3. 运行 `skillhome sync`

不需要管理员权限，没有后台服务，没有启动脚本。

## 限制

- **需要 Python 3.8+**。零第三方依赖。
- **无守护进程** — 同步是手动触发的。skill 变化是低频事件，后台 watcher 只增加复杂度不增加价值。

## 许可

MIT
