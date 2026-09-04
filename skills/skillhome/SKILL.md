---
name: skillhome
description: >-
  Manage a unified skill repository across multiple AI agent applications
  (Windows, Linux, macOS). Use when the user asks to share, sync, map, or
  migrate skills between agents (Codex, Devin, Claude Code, Windsurf, Cursor,
  Gemini, Open Cloud, Hermes, etc.), unify skill storage, fix broken skill
  junctions, or says "skillhome", "统一管理 skill", "skill 映射",
  "skill 同步", "共享 skill", "在 Devin 里用 Codex 的 skill", or similar
  cross-agent skill requests. To install a skill, use
  "skillhome add <source> -g": local zip/directory is installed directly
  into the central repo (no npx needed, handles non-UTF-8 SKILL.md);
  remote sources (owner/repo, URL) wrap `npx skills add`. Auto-syncs and
  global-shares to all agents. To install SkillHome itself, run
  "npx skills add Haaaiawd/skillhome -g" or download from
  https://github.com/Haaaiawd/skillhome/releases.
---

# SkillHome

Centralize scattered agent skills into `~/.skillhome/skills/`. Each agent
directory keeps only a junction (Windows) or symlink (Linux/macOS) pointing
to the central copy. Manual sync, no daemon.

Script: `~/.skillhome/bin/skillhome.py`
Config: `~/.skillhome/config.json`

## Install

```bash
npx skills add Haaaiawd/skillhome -g
```

Or download `skillhome.zip` from
https://github.com/Haaaiawd/skillhome/releases — extract to `~/.skillhome/`.

Requires Python 3.8+, zero third-party dependencies.

## Status-driven actions

Run `status` first, then act based on the result:

- **config.json missing** → `init` — auto-discover skill directories,
  generate config
- **New agent or skill repo installed** → `discover` then `sync`
- **Reals > 0** → `sync` — migrate real dirs to central, replace with links
- **Broken/stale links** → `sync --full` — rebuild all links
- **User wants a skill from skills.sh** → `add <source> -g` — local
  zip/dir installs directly; remote source wraps `npx skills add`;
  auto-syncs to central, global-shares
- **Expose one skill to one agent** → `link <skill> <agent>`
- **Remove a skill from an agent** → `unlink <skill> <agent>`
- **User just wants info** → `status` / `list` / `config` — answer and stop

Do not run `sync` without reason. Check `status` first every time.

## Commands

All commands invoked as:

```
python ~/.skillhome/bin/skillhome.py <command>
```

(Windows: `python` ; Linux/macOS: `python3` if `python` is absent.)

| Command | Action |
|---|---|
| `init` | First-time setup: discover all skill dirs, write config.json |
| `discover` | Re-scan home dir for skill dirs (run after installing new agent) |
| `sync` | Migrate real dirs to central + repair missing links (incremental) |
| `sync --full` | Rebuild all links (fix broken/stale links) |
| `status` | Show central count, link count, per-agent breakdown |
| `list` | List all skills with source agents |
| `link <skill> <agent>` | Create link from agent dir to central skill |
| `unlink <skill> <agent>` | Remove link from agent dir (keeps central file) |
| `global <skill> [on\|off]` | Toggle global sharing flag |
| `add <source> [opts]` | Install skill: local zip/dir → central repo directly; remote → `npx skills add`; auto-sync |
| `config` | Show current configuration |
| `help` | Show help |

## sync behavior

Four phases, no user intervention needed:

1. **Scan** — read agent dirs from config, classify each entry as real or link
2. **Migrate** — move real skill dirs into central, replace originals with
   junction/symlink
3. **Resolve conflicts** — same-name skills compared by SHA-256 file hash:
   - similarity ≥ 0.95 → merge, keep newer version, union source list
   - similarity < 0.95 → keep both, suffix variant with source
     (e.g. `docx--gemini`)
4. **Distribute** — create missing links, update `.skillhome.json` metadata

Incremental (default): only migrate new real dirs and create missing links.
Full (`--full`): delete and rebuild all links — use when links point to
stale paths.

## Global sharing

New skills default to `global: true` — `sync` creates links in **all** agent
dirs, not just the source.

Conflict variants (name contains `--`) are never global. Share them
manually:

```
python ~/.skillhome/bin/skillhome.py link docx--gemini devin
python ~/.skillhome/bin/skillhome.py global some-skill off
```

## Discovery rules

`init` / `discover` scans the home directory by **structure**, not by name:

- ≥ 2 subdirs with `SKILL.md` or `.skill-metadata.yaml` → confirmed skill repo
- 1 such subdir + dir name contains "skill" → also confirmed
- otherwise → not a skill repo

Phase 1: fast-probe ~30 known agent paths. Phase 2: deep-scan dot-directories
up to depth 3 — finds repos regardless of name (e.g. `~/.hermes/capabilities`,
`~/.opencloud/learned`).

Excluded: VSCode extensions, Trae builtins, Codex `vendor_imports/curated`,
`node_modules`, cache dirs. These skills belong to the tool, not the user.

## link / unlink

`link <skill> <agent>` — create a link in the agent's skill dir pointing to
the central skill. Skill must exist in central. If target already has a real
dir, do not overwrite — tell the user to handle it first.

`unlink <skill> <agent>` — remove the link only. Never delete the central
copy. If target is not a link, do not touch it.

## Stop conditions

- `status` shows 0 real dirs and no pending link/unlink → done, do not sync
- `sync` output shows "残留真实目录: 0" → done
- User only asked for info (status/list/config) → answer and stop
- config.json missing and user declines init → do not force, explain and stop

## Platform

- Windows: NTFS junction via `cmd /c mklink /J` — no admin required
- Linux/macOS: symlink via `os.symlink` — needs write access to skill dirs
- Auto-detected by `platform.system()`
