<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:6C5CE7,50:00B894,100:0984E3&height=180&section=header&text=SkillHome&fontSize=70&fontColor=ffffff&animation=fadeIn&fontAlignY=38&desc=AI%20Agent%20%E7%BB%9F%E4%B8%80%20Skill%20%E7%AE%A1%E7%90%86&descSize=18&descAlignY=56" width="100%"/>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="#安装">安装</a> · <a href="#命令">命令</a> · <a href="#工作原理">工作原理</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/PowerShell-7+-5391FE?style=for-the-badge&logo=powershell&logoColor=white"/>
  <img src="https://img.shields.io/badge/Windows-NTFS-0078D6?style=for-the-badge&logo=windows&logoColor=white"/>
  <img src="https://img.shields.io/badge/Platform-Cross--Agent-6C5CE7?style=for-the-badge"/>
  <img src="https://img.shields.io/badge/License-MIT-00B894?style=for-the-badge"/>
</p>

---

一个中央仓库，所有 AI agent 通过 junction 读取。没有守护进程，没有后台服务——需要时跑一次 `skillhome sync` 就行。

## 解决什么问题

你同时用 Codex、Devin、Claude Code、Windsurf、Cursor、Gemini——每个 agent 有自己的 skill 目录。在一个 agent 里装的 skill，其他 agent 看不到。你手动复制，skill 更新后副本过期，junction 断裂，你搞不清哪个版本在哪。

## 怎么解决的

SkillHome 把所有 skill 文件移到一个中央仓库（`~/.skillhome/skills/`），然后把每个 agent skill 目录下的条目替换为 NTFS junction，指回中央。

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

## 安装

### 方式一：通过 skills.sh

把 SkillHome skill 安装到你的 agent——它包含安装说明，agent 可以照着做：

```bash
npx skills add Haaaiawd/skillhome -g
```

然后对 agent 说"帮我设置 SkillHome"，它会下载 bin 脚本并运行初始化。

### 方式二：从 GitHub Release 下载

```powershell
# 下载并解压
mkdir -Force "$env:USERPROFILE\.skillhome"
Invoke-WebRequest -Uri "https://github.com/Haaaiawd/skillhome/releases/latest/download/skillhome.zip" -OutFile "$env:USERPROFILE\.skillhome\skillhome.zip"
Expand-Archive -Path "$env:USERPROFILE\.skillhome\skillhome.zip" -DestinationPath "$env:USERPROFILE\.skillhome" -Force
Remove-Item "$env:USERPROFILE\.skillhome\skillhome.zip"

# 初始化并同步
& "$env:USERPROFILE\.skillhome\bin\skillhome.ps1" init
& "$env:USERPROFILE\.skillhome\bin\skillhome.ps1" sync
```

### 方式三：git clone

```powershell
git clone https://github.com/Haaaiawd/skillhome.git "$env:USERPROFILE\.skillhome"
& "$env:USERPROFILE\.skillhome\bin\skillhome.ps1" init
& "$env:USERPROFILE\.skillhome\bin\skillhome.ps1" sync
```

> **前提：** PowerShell 7（`pwsh`）。脚本会自动探测路径。

## 命令

| 命令 | 作用 |
|---|---|
| `skillhome init` | 首次初始化。扫描 `~/` 发现 skill 目录，生成 `config.json` |
| `skillhome discover` | 重新扫描（装了新 agent 后运行） |
| `skillhome sync` | 迁移新 skill 到中央 + 修复缺失的 junction |
| `skillhome status` | 查看中央 skill 数、junction 数、各 agent 分布 |
| `skillhome list` | 列出所有 skill 及其来源 |
| `skillhome link <skill> <agent>` | 把某个 skill 暴露给指定 agent |
| `skillhome unlink <skill> <agent>` | 从指定 agent 移除某个 skill |
| `skillhome global <skill> [on\|off]` | 设置/取消全局共享（sync 时自动扩散到所有 agent） |
| `skillhome add <source> [options]` | 包装 `npx skills add`，安装后自动 sync 到中央 |
| `skillhome config` | 查看当前配置 |
| `skillhome help` | 显示帮助 |

**可选别名**（加到 PowerShell profile）：
```powershell
Set-Alias skillhome "$env:USERPROFILE\.skillhome\bin\skillhome.ps1"
```

## 工作原理

### 自检索

`skillhome init` 扫描用户目录，识别 skill 存放地。识别特征：

1. 目录名包含 "skill"
2. 子目录有 `SKILL.md` 或 `.skill-metadata.yaml`

先快速探测 ~30 个已知 agent 路径，再深度扫描 `.` 开头的目录。结果写入 `config.json`，可手动编辑增删。

**默认排除：** VSCode 扩展、Trae 内置目录、node_modules、缓存目录。这些里面的 skill 属于扩展，不属于你。

### 同步逻辑

`skillhome sync` 分四个阶段：

1. **扫描** — 读取 `config.json` 中所有 agent 目录，区分真实目录和 junction
2. **迁移** — 把真实 skill 目录移入中央仓库，原位置替换为 junction
3. **冲突处理** — 两个 agent 有同名 skill 时：
   - 相似度 ≥ 95%（按文件哈希）→ **合并**，保留较新版本，来源合并记录
   - 相似度 < 95% → **都保留**，给变体加来源后缀（如 `docx--gemini`）
4. **分发** — 为每个应该看到该 skill 的 agent 创建 junction

### 全局共享

默认情况下，每个新 skill 自动标记 `global: true`。这意味着 `skillhome sync` 会在**所有** agent 目录下创建 junction——不只是来源 agent。

例外：冲突变体（名字带 `--` 后缀）不会 global。你需要手动选择共享哪个版本：

```powershell
# 把 Gemini 的 docx 共享给 Devin
skillhome link docx--gemini devin

# 取消某个 skill 的全局共享
skillhome global some-skill off
```

### 冲突处理

同名 skill 内容不同时：

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
  "userProfile": "C:\\Users\\你",
  "centralSkills": "C:\\Users\\你\\.skillhome\\skills",
  "agentDirs": {
    "codex": "C:\\Users\\你\\.codex\\skills",
    "devin": "C:\\Users\\你\\.devin\\skills",
    "claude": "C:\\Users\\你\\.claude\\skills"
  },
  "skipNames": [".system", ".git", ".temp", "_shared"],
  "similarityThreshold": 0.95
}
```

编辑 `agentDirs` 可以添加新 agent 或移除不需要管理的。

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
│   ├── Config.ps1             # 共享配置模块
│   ├── Discover-SkillDirs.ps1 # 自检索
│   ├── Sync-SkillHome.ps1     # 同步引擎
│   └── skillhome.ps1          # CLI 入口
└── assets/
    └── logo.svg
```

## 可移植性

零硬编码。所有路径从 `$env:USERPROFILE` 推导。部署到另一台机器：

1. 复制 `~/.skillhome/bin/` 到目标机器
2. 运行 `skillhome init`
3. 运行 `skillhome sync`

不需要管理员权限，没有后台服务，没有启动脚本。

## 限制

- **仅 Windows** — 使用 NTFS junction。Linux/macOS 需改用 symlink。
- **需要 PowerShell 7**（`pwsh`）。如果在 PATH 中会自动探测。
- **无守护进程** — 同步是手动触发的。skill 变化是低频事件，后台 watcher 只增加复杂度不增加价值。

## 许可

MIT
