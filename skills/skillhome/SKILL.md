---
name: skillhome
description: Manage a unified skill repository across multiple AI agent applications (Windows, Linux, macOS). Use when the user asks to share, sync, map, or migrate skills between agents (Codex, Devin, Claude Code, Windsurf, Cursor, Gemini, Open Cloud, Hermes, etc.), unify skill storage, fix broken skill junctions, or says "skillhome", "统一管理 skill", "skill 映射", "skill 同步", "共享 skill", "在 Devin 里用 Codex 的 skill", or similar cross-agent skill requests. To install a new skill from skills.sh, use "skillhome add <source> -g" (wraps npx skills add, auto-syncs to central repo, global-shares to all agents). To install SkillHome itself, run "npx skills add Haaaiawd/skillhome -g" or download from https://github.com/Haaaiawd/skillhome/releases.
---

# SkillHome｜跨 Agent 统一 Skill 管理

SkillHome 把分散在多个 agent 应用里的 skill 集中到一个中央仓库，各 agent 目录下只保留 junction/symlink 指向中央。需要时手动跑一次 sync，无常驻进程。

中央仓库位于 `~/.skillhome/skills/`，脚本在 `~/.skillhome/bin/skillhome.py`，配置在 `~/.skillhome/config.json`。路径从用户主目录推导，可移植。

## 首次安装

```bash
npx skills add Haaaiawd/skillhome -g
```

或从 [GitHub Releases](https://github.com/Haaaiawd/skillhome/releases/latest) 下载 `skillhome.zip`。

> 前提：Python 3.8+。零第三方依赖。

## 判断该做什么

先跑 `skillhome status` 读取当前状态，根据结果选择动作：

- **config.json 不存在** → 跑 `skillhome init`，自检索扫描用户目录发现所有 skill 存放地并生成配置
- **用户装了新 agent 或新 skill 目录** → 跑 `skillhome discover` 更新 config.json，再 `skillhome sync`
- **用户想从 skills.sh 安装新 skill** → 跑 `skillhome add <source>`（包装 npx skills add，安装后自动 sync 到中央 + 全局共享）
- **有残留真实目录（Reals > 0）** → 跑 `skillhome sync`，迁移到中央并替换为 junction/symlink
- **用户想在某个 agent 里用另一个 agent 的 skill** → 跑 `skillhome link <skill> <agent>`
- **用户想从某个 agent 移除一个 skill** → 跑 `skillhome unlink <skill> <agent>`
- **用户想看有哪些 skill、各自来自哪里** → 跑 `skillhome list`
- **junction/symlink 指向旧路径或失效** → 跑 `skillhome sync --full`（重建所有链接）

不要在不需要时主动跑 sync。skill 变化是低频事件，每次 sync 前先看 status。

## 执行命令

### Windows

```powershell
python "$env:USERPROFILE\.skillhome\bin\skillhome.py" <command>
```

可选别名（加到 PowerShell profile）：
```powershell
function skillhome { python "$env:USERPROFILE\.skillhome\bin\skillhome.py" @args }
```

### Linux / macOS

```bash
python3 ~/.skillhome/bin/skillhome.py <command>
```

可选别名（加到 `~/.bashrc` 或 `~/.zshrc`）：
```bash
alias skillhome='python3 ~/.skillhome/bin/skillhome.py'
```

## sync 的行为

`skillhome sync` 做四件事，不需要用户干预：

1. 扫描 config.json 中所有 agent 目录，识别真实目录 vs junction/symlink
2. 把真实 skill 目录移入中央仓库，原位置替换为 junction（Windows）或 symlink（Linux/macOS）
3. 同名 skill 按文件哈希算相似度：≥95% 合并取较新版本、来源合并记录；<95% 加来源后缀保留为独立条目（如 `xlsx--qoderworkcn`）
4. 补齐缺失的链接，更新 `.skillhome.json` 元数据

增量模式（`skillhome sync` 默认）只补缺失链接、迁移新增真实目录，不破坏已有链接。完整模式（`skillhome sync --full`）会重建所有链接，用于修复指向旧路径的链接。

## 全局共享

默认情况下，每个新 skill 自动标记 `global: true`。`skillhome sync` 会在所有 agent 目录下创建链接——不只是来源 agent。

例外：冲突变体（名字带 `--` 后缀）不会 global。需要手动选择共享哪个版本：

```
skillhome link docx--gemini devin   # 把 Gemini 的 docx 共享给 Devin
skillhome global some-skill off     # 取消某个 skill 的全局共享
```

## 自检索的识别规则

`skillhome init` / `skillhome discover` 扫描用户目录，按**结构特征**识别 skill 仓库，不依赖目录名：

- **≥2 个子目录含 `SKILL.md` 或 `.skill-metadata.yaml`** → 确认是 skill 仓库（多个 skill 条目 = 强信号）
- **1 个子目录含 SKILL.md + 目录名含 "skill"** → 也确认（名字是弱辅助）
- 其他 → 不是

阶段 1 快速探测 ~30 个已知 agent 路径模式。阶段 2 深度扫描 `.` 开头的目录（深度 3），纯靠结构特征发现未知 agent 的 skill 仓库——即使它叫 `capabilities`、`learned` 或其他非 "skill" 名字。

排除：VSCode 扩展、Trae builtin、Codex `vendor_imports/curated`、`node_modules`、缓存目录。这些里面的 skill 属于扩展或工具本身，不是用户管理的。不要把它们纳入。

## link / unlink 的语义

`skillhome link <skill> <agent>` 在指定 agent 的 skill 目录下创建链接指向中央仓库的 skill。skill 必须已存在于中央仓库。如果目标位置已有真实目录，不覆盖，先让用户处理。

`skillhome unlink <skill> <agent>` 只删除链接，不删除中央仓库里的真实文件。如果目标不是链接，不操作。

## 停止条件

- status 显示 0 残留真实目录且用户没有新的 link/unlink 需求 → 完成，不需要再 sync
- sync 已执行且输出显示"残留真实目录: 0" → 完成
- 用户只想查看信息（status/list/config）→ 给出信息即完成，不主动触发 sync
- config.json 不存在且用户不想初始化 → 不要强行 init，先说明情况让用户决定

## 平台支持

- **Windows**：NTFS junction，不需要管理员权限
- **Linux/macOS**：symlink，需要对 skill 目录的写权限
- 依赖：Python 3.8+，零第三方包
- 路径分隔符和发现模式按平台自动切换
