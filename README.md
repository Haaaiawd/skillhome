<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:6C5CE7,50:00B894,100:0984E3&height=180&section=header&text=SkillHome&fontSize=70&fontColor=ffffff&animation=fadeIn&fontAlignY=38&desc=Unified%20Skill%20Management%20for%20AI%20Agents&descSize=18&descAlignY=56" width="100%"/>
</p>

<p align="center">
  <a href="README.zh-CN.md">中文文档</a> · <a href="#pain-points">Pain Points</a> · <a href="#installation">Installation</a> · <a href="#auto-discovery">Auto-Discovery</a> · <a href="#commands">Commands</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/Windows-NTFS-0078D6?style=for-the-badge&logo=windows&logoColor=white"/>
  <img src="https://img.shields.io/badge/Linux-symlink-FCC624?style=for-the-badge&logo=linux&logoColor=black"/>
  <img src="https://img.shields.io/badge/macOS-symlink-000000?style=for-the-badge&logo=apple&logoColor=white"/>
  <img src="https://img.shields.io/badge/License-MIT-00B894?style=for-the-badge"/>
</p>

---

> *"You can have many AI agents, but your skills should have only one home."*

SkillHome unifies skills across all your AI agents into a single central repository. Each agent reads from it through junctions or symlinks — one source of truth, no duplicate copies, no silent loss. Auto-discovery finds skill repos by structure (not by name), conflict resolution merges or preserves variants by content similarity, and global sharing distributes new skills to every agent by default.

No daemons, no background services — just run `skillhome sync` when you need it.

## Pain Points

### 1. Skill sharing across multiple agents

You use Codex, Devin, Claude Code, Windsurf, Cursor, Gemini — each has its own skill directory. When you add a skill to one agent, the others can't see it. You copy files manually. When a skill updates, copies go stale. You lose track of which version lives where.

**SkillHome fixes this:** Add a skill once → it goes to the central repo → automatically distributed to every agent. New skills are `global` by default — one `skillhome add` or `skillhome sync` and every agent on your machine can use it.

### 2. Centralized management across environments

You have skills scattered across cloud servers, multiple Claude/Codex environments, different user profiles. Some are in `~/.codex/skills`, some in `~/.config/devin/skills`, some in places you forgot about. When a path changes or an environment is rebuilt, skills go missing silently — the agent just can't find them anymore.

**SkillHome fixes this:** All skills are collected into `~/.skillhome/skills/`. Agent directories become junctions pointing to the center. The AI always knows where to find skills — one path, one source of truth. No more "skill not found" because a path drifted.

### 3. Self-evolved skills that get forgotten

Agents like Open Cloud and Hermes can write their own skills through self-learning — but they save them to obscure paths the user never checks. After an update, a reinstall, or a profile reset, these self-evolved skills vanish. The AI improved itself, then forgot the improvement.

**SkillHome fixes this:** Auto-discovery scans by **structural features**, not by directory name. If a directory has ≥2 subdirectories each containing `SKILL.md`, it's a skill repo — regardless of what it's called. Self-evolved skills in `~/.hermes/capabilities` or `~/.opencloud/learned` are found, collected, and preserved in the central repo. Nothing gets lost.

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │         ~/.skillhome/skills/             │
                    │         (Central Repository)             │
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

Each agent sees its own skill directory as if nothing changed. The files are real, readable, and behave exactly like local directories. But they all point to one source of truth.

## Auto-Discovery

The headline feature. `skillhome init` scans your home directory and finds every skill repository — **without knowing what they're called**.

### How it works

**Phase 1 — Fast probe:** Checks ~30 known agent paths (`~/.codex/skills`, `~/.claude/skills`, etc.). Quick, covers the common case.

**Phase 2 — Feature-based deep scan:** Recursively scans all dot-directories up to depth 3. For each directory, it checks the **structure**, not the name:

```
Is this a skill repo?
  │
  ├── ≥2 subdirectories with SKILL.md or .skill-metadata.yaml → YES
  │   (multiple skill entries = strong signal)
  │
  ├── 1 subdirectory with SKILL.md + directory name contains "skill" → YES
  │   (name is a weak confirmation)
  │
  └── otherwise → NO
```

This means:
- `~/.hermes/capabilities` with 5 learned skills → **found** (name doesn't matter)
- `~/.opencloud/agent-skills` with 3 self-written skills → **found**
- `~/.some-random-dir/skills` with 10 skills → **found**
- `~/.cache/something` with a random SKILL.md → **not found** (only 1, not a repo)

### What's excluded

VSCode extensions, Trae builtins, Codex `vendor_imports/curated`, `node_modules`, cache directories. These contain skills that belong to the extension or the tool, not to you.

## Installation

```bash
npx skills add Haaaiawd/skillhome -g
```

Or download [`skillhome.zip`](https://github.com/Haaaiawd/skillhome/releases/latest) from GitHub Releases.

> **Prerequisite:** Python 3.8+. Zero third-party dependencies.

## Commands

| Command | What it does |
|---|---|
| `skillhome init` | First-time setup. Auto-discovers all skill directories, generates `config.json` |
| `skillhome discover` | Re-scan for skill directories (run after installing a new agent) |
| `skillhome sync` | Migrate new skills to central repo + repair missing junctions |
| `skillhome sync --full` | Full sync: rebuild all links (use to fix broken/stale links) |
| `skillhome status` | Show central skill count, junction count, per-agent breakdown |
| `skillhome list` | List all skills with their source agents |
| `skillhome link <skill> <agent>` | Expose a skill to a specific agent |
| `skillhome unlink <skill> <agent>` | Remove a skill from an agent's directory |
| `skillhome global <skill> [on\|off]` | Mark a skill as globally shared (auto-distributes to all agents on sync) |
| `skillhome add <source> [options]` | Wrapper for `npx skills add` — installs then auto-syncs to central repo |
| `skillhome config` | Show current configuration |
| `skillhome help` | Show help |

**Optional alias:**

Windows (PowerShell profile):
```powershell
function skillhome { python "$env:USERPROFILE\.skillhome\bin\skillhome.py" @args }
```

Linux/macOS (`~/.bashrc` or `~/.zshrc`):
```bash
alias skillhome='python3 ~/.skillhome/bin/skillhome.py'
```

## How It Works

### Sync Logic

`skillhome sync` runs four phases:

1. **Scan** — Read all agent directories from `config.json`, classify each entry as real or junction
2. **Migrate** — Move real skill directories into `~/.skillhome/skills/`, replace originals with junctions
3. **Resolve conflicts** — When two agents have a skill with the same name:
   - Similarity ≥ 95% (by file hash) → **merge**, keep the newer version, union the source list
   - Similarity < 95% → **keep both**, suffix the variant with its source (e.g. `docx--gemini`)
4. **Distribute** — Create junctions for all agents that should see each skill

### Global Sharing

By default, every new skill is marked `global: true`. This means `skillhome sync` automatically creates junctions in **all** agent directories — not just the source agent.

The exception: conflict variants (names with `--` suffix) are never global. You choose which version to share manually:

```
# Share Gemini's docx with Devin
skillhome link docx--gemini devin

# Stop sharing a skill globally
skillhome global some-skill off
```

### Conflict Resolution

```
Agent A has "docx" (QoderWork's implementation)
Agent B has "docx" (Gemini's implementation)
                │
                ▼
    File hash comparison (SHA-256)
                │
        ┌───────┴───────┐
        ▼               ▼
   ≥ 95% similar    < 95% different
        │               │
        ▼               ▼
    Merge: keep    Keep both:
    newer version  docx (A's version)
    union sources  docx--B (B's version)
```

Both versions survive. No data loss. The `.skillhome.json` metadata file in each skill records its source agents.

## Configuration

`~/.skillhome/config.json` is the runtime config, auto-generated by `skillhome init`:

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

Edit `agentDirs` to add a new agent or remove one you don't want managed.

## Platform Support

| Platform | Link Type | Admin Required |
|---|---|---|
| Windows | NTFS junction | No |
| Linux | symlink | No (write access to skill dirs) |
| macOS | symlink | No (write access to skill dirs) |

SkillHome auto-detects the platform via `platform.system()` and uses the appropriate link type. Discovery patterns are platform-specific — Windows probes `~\.codex\skills`, Unix probes `~/.codex/skills` and `~/.config/codex/skills`.

## File Structure

```
~/.skillhome/
├── config.json                # Runtime config (auto-generated)
├── skills/                    # Central repository (real files)
│   ├── crux/
│   │   └── .skillhome.json    # Metadata: sources, global flag
│   ├── architect-prompts/
│   └── ...
├── bin/
│   └── skillhome.py           # Single-file CLI (all commands)
└── skills/
    └── skillhome/
        └── SKILL.md           # Skill definition (for skills.sh)
```

## Portability

Zero hardcoding. All paths derive from `Path.home()`. To deploy on another machine:

1. Install via any method above
2. Run `skillhome init`
3. Run `skillhome sync`

No admin privileges required. No background services. No startup scripts.

## Limitations

- **Python 3.8+** required. Zero third-party dependencies.
- **No daemon** — sync is manual by design. Skills change infrequently; a background watcher adds complexity without value.

## License

MIT
