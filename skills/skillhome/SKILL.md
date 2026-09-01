---
name: skillhome
description: Manage a unified skill repository across multiple AI agent applications on Windows. Use when the user asks to share, sync, map, or migrate skills between agents (Codex, Devin, Claude Code, Windsurf, Cursor, Gemini, etc.), unify skill storage, fix broken skill junctions, or says "skillhome", "统一管理 skill", "skill 映射", "skill 同步", "共享 skill", "在 Devin 里用 Codex 的 skill", or similar cross-agent skill requests.
---

# SkillHome｜跨 Agent 统一 Skill 管理

SkillHome 把分散在多个 agent 应用里的 skill 集中到一个中央仓库，各 agent 目录下只保留 junction 指向中央。需要时手动跑一次 sync，无常驻进程。

中央仓库位于 `~/.skillhome/skills/`，脚本在 `~/.skillhome/bin/`，配置在 `~/.skillhome/config.json`。所有路径从 `$env:USERPROFILE` 推导，可移植。

## 首次安装

如果 `~/.skillhome/bin/skillhome.ps1` 不存在，需要先安装 SkillHome 工具：

**方式一：从 GitHub Release 下载**

```powershell
# 下载最新 release 并解压到 ~/.skillhome/
mkdir -Force "$env:USERPROFILE\.skillhome"
Invoke-WebRequest -Uri "https://github.com/Haaaiawd/skillhome/releases/latest/download/skillhome.zip" -OutFile "$env:USERPROFILE\.skillhome\skillhome.zip"
Expand-Archive -Path "$env:USERPROFILE\.skillhome\skillhome.zip" -DestinationPath "$env:USERPROFILE\.skillhome" -Force
Remove-Item "$env:USERPROFILE\.skillhome\skillhome.zip"
```

**方式二：手动克隆**

```powershell
git clone https://github.com/Haaaiawd/skillhome.git "$env:USERPROFILE\.skillhome"
```

安装完成后运行初始化：

```powershell
& "$env:USERPROFILE\.skillhome\bin\skillhome.ps1" init
& "$env:USERPROFILE\.skillhome\bin\skillhome.ps1" sync
```

## 判断该做什么

先跑 `skillhome status` 读取当前状态，根据结果选择动作：

- **config.json 不存在** → 跑 `skillhome init`，自检索会扫描用户目录发现所有 skill 存放地并生成配置
- **用户装了新 agent 或新 skill 目录** → 跑 `skillhome discover` 更新 config.json，再 `skillhome sync`
- **用户想从 skills.sh 安装新 skill** → 跑 `skillhome add <source>`（包装 npx skills add，安装后自动 sync 到中央 + 全局共享）
- **有残留真实目录（Reals > 0）** → 跑 `skillhome sync`，迁移到中央并替换为 junction
- **用户想在某个 agent 里用另一个 agent 的 skill** → 跑 `skillhome link <skill> <agent>`
- **用户想从某个 agent 移除一个 skill** → 跑 `skillhome unlink <skill> <agent>`
- **用户想看有哪些 skill、各自来自哪里** → 跑 `skillhome list`
- **junction 指向旧路径或失效** → 跑 `skillhome sync`（完整模式会重建所有 junction）

不要在不需要时主动跑 sync。skill 变化是低频事件，每次 sync 前先看 status。

## 执行命令

所有命令通过 PowerShell 7 执行：

```powershell
& "$env:USERPROFILE\.skillhome\bin\skillhome.ps1" <command>
```

可选别名（加到 PowerShell profile）：
```powershell
Set-Alias skillhome "$env:USERPROFILE\.skillhome\bin\skillhome.ps1"
```

## sync 的行为

`skillhome sync` 做四件事，不需要用户干预：

1. 扫描 config.json 中所有 agent 目录，识别真实目录 vs junction
2. 把真实 skill 目录移入中央仓库，原位置替换为 junction
3. 同名 skill 按文件哈希算相似度：≥95% 合并取较新版本、来源合并记录；<95% 加来源后缀保留为独立条目（如 `xlsx--qoderworkcn`）
4. 补齐缺失的 junction，更新 `.skillhome.json` 元数据

增量模式（`skillhome sync` 默认）只补缺失 junction、迁移新增真实目录，不破坏已有 junction。完整模式（脚本内部 `-Incremental` 未传时）会重建所有 junction，用于修复指向旧路径的 junction。

## 全局共享

默认情况下，每个新 skill 自动标记 `global: true`。`skillhome sync` 会在所有 agent 目录下创建 junction——不只是来源 agent。

例外：冲突变体（名字带 `--` 后缀）不会 global。需要手动选择共享哪个版本：

```powershell
skillhome link docx--gemini devin   # 把 Gemini 的 docx 共享给 Devin
skillhome global some-skill off     # 取消某个 skill 的全局共享
```

## 自检索的识别规则

`skillhome init` / `skillhome discover` 扫描用户目录，识别 skill 存放地：

- 目录名包含 "skill" 且子目录有 `SKILL.md` 或 `.skill-metadata.yaml`
- 阶段 1 快速探测 ~30 个已知 agent 路径模式
- 阶段 2 深度扫描 `.` 开头的目录，排除 IDE 扩展、缓存、node_modules 等

不要把 VSCode 扩展自带的 skills、Trae builtin 目录、或已有 skill 目录的子目录纳入——这些是扩展自带的或嵌套的，不是独立 agent 的 skill 仓库。

## link / unlink 的语义

`skillhome link <skill> <agent>` 在指定 agent 的 skill 目录下创建一个 junction 指向中央仓库的 skill。skill 必须已存在于中央仓库。如果目标位置已有真实目录，不覆盖，先让用户处理。

`skillhome unlink <skill> <agent>` 只删除 junction，不删除中央仓库里的真实文件。如果目标不是 junction，不操作。

## 停止条件

- status 显示 0 残留真实目录且用户没有新的 link/unlink 需求 → 完成，不需要再 sync
- sync 已执行且输出显示"残留真实目录: 0" → 完成
- 用户只想查看信息（status/list/config）→ 给出信息即完成，不主动触发 sync
- config.json 不存在且用户不想初始化 → 不要强行 init，先说明情况让用户决定

## 平台限制

- junction 是 Windows NTFS 特性，跨平台需改用 symlink
- 需要 PowerShell 7（`pwsh`），脚本会自动探测路径
- 不需要管理员权限
