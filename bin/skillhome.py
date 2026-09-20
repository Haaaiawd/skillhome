#!/usr/bin/env python3
"""SkillHome — 跨 Agent 统一 Skill 管理（单文件，三平台）。

Windows: NTFS junction（不需要管理员权限）
Linux/macOS: symlink

依赖：Python 3.8+，零第三方包。
"""
import os
import re
import sys
import json
import shutil
import fnmatch
import hashlib
import platform
import zipfile
import tempfile
import subprocess
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

# Windows 控制台 UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ============================================================
# 平台与路径
# ============================================================
IS_WINDOWS = platform.system() == "Windows"
HOME = Path.home()
HOME_ROOT = HOME / ".skillhome"
CENTRAL_SKILLS = HOME_ROOT / "skills"
BIN_DIR = HOME_ROOT / "bin"
CONFIG_PATH = HOME_ROOT / "config.json"
LOG_FILE = HOME_ROOT / "skillhome.log"
STATE_PATH = HOME_ROOT / "state.json"  # 本机上下文状态，不参与 cloud sync
BACKUP_DIR = HOME_ROOT / "backups"
NOTIFY_DIR = HOME_ROOT / "notifications"
HERMES_ENV_PATH = HOME / ".hermes" / ".env"

DEFAULT_SKIP_NAMES = [".system", ".git", ".temp", "_shared"]
DEFAULT_SIMILARITY_THRESHOLD = 0.95

# 项目 skill 识别规则（config.json 的 scopeRules 可覆盖；
# 配置里显式写 "scopeRules": {} 可完全关闭默认识别）
DEFAULT_SCOPE_RULES = {
    "duly": ["duly-*"],
    "paper-wechat": ["paper-wechat-*", "拆解-*"],
    "skillhome": ["skillhome-*"],
}

# 已知 agent skill 目录模式（相对于用户主目录）
if IS_WINDOWS:
    KNOWN_PATTERNS = [
        ".devin\\skills", ".agents\\skills", ".claude\\skills",
        ".codeium\\windsurf\\skills", ".codex\\skills",
        ".cursor\\skills-cursor", ".cursor\\skills",
        ".qoderworkcn\\skills", ".qoder\\skills",
        ".gemini\\antigravity\\skills", ".gemini\\skills",
        ".cc-switch\\skills", ".config\\devin\\skills",
        ".roo\\skills", ".kilocode\\skills", ".kiro\\skills",
        ".trae\\skills", ".lingma\\skills", ".qwen\\skills",
        ".copilot\\skills", ".continue\\skills", ".cline\\skills",
        ".antigravity\\skills", ".marscode\\skills", ".cagent\\skills",
        ".bito\\skills", ".comate\\skills", ".codeverse\\skills",
        ".continuum\\skills", ".kimi-code\\skills", ".trae-aicc\\skills",
    ]
else:
    KNOWN_PATTERNS = [
        ".devin/skills", ".agents/skills", ".claude/skills",
        ".codex/skills", ".cursor/skills", ".gemini/skills",
        ".config/claude/skills", ".config/codex/skills",
        ".config/devin/skills", ".continue/skills", ".cline/skills",
        ".roo/skills", ".kilocode/skills", ".kiro/skills",
        ".trae/skills", ".lingma/skills", ".qwen/skills",
        ".copilot/skills", ".local/share/claude/skills",
        ".local/share/codex/skills",
        ".hermes/skills", ".opencloud/skills",
    ]

# 深度扫描排除的顶级目录
EXCLUDE_TOP = {
    ".skillhome", ".cache", ".npm", ".cargo", ".rustup", ".conda",
    ".anaconda", ".m2", ".gradle", ".docker", ".ollama", ".ssh",
    ".gnupg", ".kube", ".android", ".dotnet", ".openjfx", ".platformio",
    ".ipython", ".jupyter", ".keras", ".matplotlib", ".vscode-R",
    ".xlwings", ".dbus-keyrings", ".ms-ad", ".aws", ".azure",
    ".oracle_jre_usage", ".windows-build-tools", ".npm-cache",
    ".mcp-auth", ".smithery", ".chub", ".cpz",
    "node_modules", "AppData", "OneDrive", ".git", "Desktop",
    "Documents", "Downloads", "Music", "Videos", "Pictures",
    "Contacts", "Favorites", "Searches", "Saved Games", "Links",
    "Templates", "Recent", "Cookies", "NetHood", "PrintHood",
    "SendTo", "Start Menu", "Local Settings", "Application Data",
    "My Documents", "Library", "source", "src", "public", "rules",
    "ai_completion", "audiodump",
    ".vscode", ".windsurf", ".antigravity",
}

# 深度扫描排除的路径片段
EXCLUDE_PATH_PATTERNS = [
    "extensions", "builtin", ".tmp", ".github", "node_modules",
    ".git", "computer-use", "vendor_imports", "curated",
]

# skill 标识文件
SKILL_MARKERS = ["SKILL.md", ".skill-metadata.yaml"]


# ============================================================
# 日志
# ============================================================
# 控制台输出按 LOG_LEVEL 过滤；日志文件始终全量写入（事后可诊断）。
# -v/--verbose -> DEBUG，-q/--quiet -> WARN 及以上。
LOG_LEVEL = "INFO"
_LOG_RANK = {"DEBUG": 10, "INFO": 20, "OK": 20, "DRY": 20,
             "WARN": 30, "ERROR": 40}


def _log_file_only(msg, level="INFO"):
    """只写日志文件，不打印到控制台（用于 rclone 等外部输出留存）。"""
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}][{level}] {msg}\n")
    except Exception:
        pass


def log(msg, level="INFO"):
    line = f"[{datetime.now().strftime('%H:%M:%S')}][{level}] {msg}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if _LOG_RANK.get(level, 20) < _LOG_RANK.get(LOG_LEVEL, 20):
        return
    color = {
        "WARN": "\033[33m", "ERROR": "\033[31m", "OK": "\033[32m",
        "DRY": "\033[36m", "INFO": "\033[90m", "DEBUG": "\033[90m",
    }.get(level, "\033[90m")
    reset = "\033[0m"
    print(f"{color}{line}{reset}")


# ============================================================
# 平台链接操作
# ============================================================
def is_link(path: Path) -> bool:
    """判断是否是 junction 或 symlink。"""
    if path.is_symlink():
        return True
    # Windows junction: is_symlink() 在 Python 里对 junction 返回 False
    if IS_WINDOWS and path.exists():
        try:
            # junction 的 reparse 点会被 os.path.islink 检测到？
            # 实际上 Python 3.8+ 对 junction 返回 False，需要额外检测
            import stat
            st = os.lstat(str(path))
            if stat.S_ISLNK(st.st_mode):
                return True
            # 检测 reparse 点属性
            if hasattr(st, 'st_reparse_tag') and st.st_reparse_tag != 0:
                return True
        except (OSError, AttributeError):
            pass
    return False


def remove_link(path: Path):
    """删除 junction 或 symlink，不删除目标内容。"""
    if not path.exists() and not path.is_symlink():
        return
    if IS_WINDOWS and not path.is_symlink():
        # Windows junction: 用 rmdir 删除 junction 本身
        subprocess.run(["cmd", "/c", "rmdir", str(path)],
                       capture_output=True, text=True)
    else:
        # symlink: os.unlink 只删链接不删目标
        try:
            os.unlink(str(path))
        except OSError:
            shutil.rmtree(str(path), ignore_errors=True)


def create_link(link: Path, target: Path) -> bool:
    """创建 junction (Windows) 或 symlink (Unix)。"""
    if not target.exists():
        log(f"链接目标不存在: {target}", "ERROR")
        return False
    if link.exists() or link.is_symlink():
        remove_link(link)
    if IS_WINDOWS:
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, text=True)
    else:
        try:
            os.symlink(str(target), str(link))
        except OSError as e:
            log(f"创建 symlink 失败: {e}", "ERROR")
            return False
    return link.exists()


def _is_under(path: Path, root: Path) -> bool:
    """path 是否位于 root 之下（纯词法比较，不解析 symlink）。"""
    try:
        Path(os.path.normpath(str(path))).relative_to(
            Path(os.path.normpath(str(root))))
        return True
    except ValueError:
        return False


def _link_raw_target(child: Path):
    """symlink 的原始（未解析）目标绝对路径；非 symlink 返回 None。"""
    if not child.is_symlink():
        return None
    try:
        raw = Path(os.readlink(str(child)))
    except OSError:
        return None
    if raw.is_absolute():
        return raw
    return Path(os.path.normpath(str(child.parent / raw)))


def _prune_agent_dir(agent_dir: Path, agent_name: str,
                     skip_names=None, dry_run=False):
    """清理 agent 目录下的死链，并把绕经其它目录的链式链接重指为中央直链。

    只处理链接，绝不触碰真实目录。返回 (dead_removed, repointed)。
    """
    skip = skip_names or DEFAULT_SKIP_NAMES
    removed = repointed = 0
    try:
        children = list(agent_dir.iterdir())
    except (PermissionError, OSError):
        return removed, repointed
    for child in children:
        if child.name in skip or not is_link(child):
            continue
        if not child.exists():
            # 死链：中央有同名 skill 则重建直链，否则删除
            central = CENTRAL_SKILLS / child.name
            if central.is_dir():
                log(f"[{agent_name}] 修复死链 {child.name} -> 重指中央", "WARN")
                if not dry_run:
                    remove_link(child)
                    create_link(child, central)
                repointed += 1
            else:
                log(f"[{agent_name}] 清理死链 {child.name} "
                    f"(目标 {os.readlink(str(child)) if child.is_symlink() else '?'} 不存在)",
                    "WARN")
                if not dry_run:
                    remove_link(child)
                removed += 1
            continue
        # 链式链接：最终落在中央但原始目标绕经其它目录（如 .agents/skills）
        raw = _link_raw_target(child)
        if raw is None:
            continue  # junction 或无 readlink，无法判定链条，保持原样
        try:
            resolved = Path(os.path.realpath(str(child)))
        except OSError:
            continue
        if (_is_under(resolved, CENTRAL_SKILLS)
                and not _is_under(raw, CENTRAL_SKILLS)):
            log(f"[{agent_name}] 链式链接 {child.name} -> 重指 {resolved}", "WARN")
            if not dry_run:
                remove_link(child)
                create_link(child, resolved)
            repointed += 1
    return removed, repointed


# ============================================================
# 配置读写
# ============================================================
def load_config():
    if not CONFIG_PATH.exists():
        return None
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return raw
    except Exception as e:
        print(f"config.json 解析失败: {e}")
        return None


def _config_scaffold():
    return {
        "version": "1.0",
        "createdAt": datetime.now().isoformat(),
        "userProfile": str(HOME),
        "centralSkills": str(CENTRAL_SKILLS),
        "agentDirs": {},
        "skipNames": DEFAULT_SKIP_NAMES,
        "similarityThreshold": DEFAULT_SIMILARITY_THRESHOLD,
        "scopeRules": dict(DEFAULT_SCOPE_RULES),
    }


def save_config(agent_dirs, skip_names=None, similarity_threshold=None):
    # merge 写回：保留 cloudRemote 等本函数不管理的字段
    cfg = load_config() or _config_scaffold()
    cfg.setdefault("createdAt", datetime.now().isoformat())
    cfg.update({
        "version": "1.0",
        "userProfile": str(HOME),
        "centralSkills": str(CENTRAL_SKILLS),
        "agentDirs": agent_dirs,
        "skipNames": skip_names or DEFAULT_SKIP_NAMES,
        "similarityThreshold": similarity_threshold or DEFAULT_SIMILARITY_THRESHOLD,
    })
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def patch_config(updates=None, deletes=()):
    """对 config.json 做增量更新；不存在时先建脚手架（agentDirs 为空）。"""
    cfg = load_config() or _config_scaffold()
    for k in deletes:
        cfg.pop(k, None)
    if updates:
        cfg.update(updates)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def init_dirs():
    HOME_ROOT.mkdir(parents=True, exist_ok=True)
    CENTRAL_SKILLS.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 特征识别
# ============================================================
def count_skill_children(path: Path, skip_names=None) -> int:
    """计算目录下有多少个 skill 子目录。"""
    if not path.is_dir():
        return 0
    skip = skip_names or DEFAULT_SKIP_NAMES
    count = 0
    try:
        for child in path.iterdir():
            if not child.is_dir() or child.name in skip:
                continue
            for marker in SKILL_MARKERS:
                if (child / marker).exists():
                    count += 1
                    break
    except (PermissionError, OSError):
        pass
    return count


def is_skill_repo(path: Path, skip_names=None) -> bool:
    """特征识别：是否是 skill 仓库。"""
    if not path.is_dir():
        return False
    sc = count_skill_children(path, skip_names)
    if sc >= 2:
        return True
    if sc >= 1 and "skill" in path.name.lower():
        return True
    return False


def get_dir_info(path: Path, skip_names=None):
    """返回 (real, links, empty) 计数。"""
    skip = skip_names or DEFAULT_SKIP_NAMES
    real = links = empty = 0
    try:
        for child in path.iterdir():
            if not child.is_dir() or child.name in skip:
                continue
            if is_link(child):
                links += 1
            elif not any(child.iterdir()):
                empty += 1
            else:
                real += 1
    except (PermissionError, OSError):
        pass
    return real, links, empty


def derive_agent_name(path: Path) -> str:
    """从路径推导 agent 名称。"""
    try:
        rel = str(path.relative_to(HOME))
    except ValueError:
        rel = str(path)
    parts = rel.replace("\\", "/").split("/")
    name_parts = []
    for p in parts:
        if p.startswith("skill"):
            continue
        if p.endswith("skill") or p.endswith("skills"):
            continue
        name_parts.append(p)
    if not name_parts:
        return "unknown"
    name = "-".join(name_parts)
    name = name.lstrip(".")
    return name


# ============================================================
# discover — 自检索扫描
# ============================================================
def cmd_discover(interactive=False, merge=True, dry_run=False):
    """扫描发现 skill 目录并写入 config.json。

    merge=True（默认）：保留已配置的 agentDirs，只新增发现的目录；
    merge=False（--replace）：整体覆盖，恢复旧的 init/discover 行为。
    返回最终写入（或将写入）的 agentDirs dict；无发现时返回 None。
    """
    log("=== SkillHome 自检索 ===")
    if not dry_run:
        init_dirs()
    found = {}  # ordered: 用 dict 保持插入顺序 (Python 3.7+)

    # 阶段 1：快速探测已知模式（存在即登记，包括空目录——
    # 空目录同样是扩散目标，否则新装 agent 永远收不到链接）
    print("  [1/2] 探测已知路径模式...")

    for pattern in KNOWN_PATTERNS:
        path = HOME / pattern
        if path.is_dir():
            agent_name = derive_agent_name(path)
            if agent_name in found:
                # 命名碰撞：用父目录名做后缀
                parent = path.parent.name
                agent_name = f"{agent_name}-{parent}".lstrip(".")
            if agent_name in found:
                continue
            found[agent_name] = str(path)
            real, links, empty = get_dir_info(path)
            sc = count_skill_children(path)
            status = f"real={real}" if real > 0 else (
                f"links={links}" if links > 0 else "empty"
            )
            print(f"  [OK] {agent_name} => {path} ({status}, {sc} skills)")

    # 阶段 2：深度扫描
    print("  [2/2] 深度扫描（特征识别，不依赖目录名）...")
    found_paths = list(found.values())
    new_count = 0

    try:
        top_dirs = [d for d in HOME.iterdir()
                    if d.is_dir() and d.name.startswith(".")
                    and d.name not in EXCLUDE_TOP]
    except (PermissionError, OSError):
        top_dirs = []

    for top in top_dirs:
        try:
            for candidate in walk_dirs(top, max_depth=3):
                # 排除 .skillhome 自身
                if str(candidate).startswith(str(HOME_ROOT)):
                    continue
                # 排除路径片段
                skip = False
                for pat in EXCLUDE_PATH_PATTERNS:
                    if pat in str(candidate):
                        skip = True
                        break
                if skip:
                    continue
                # 排除已找到目录的子目录
                for fp in found_paths:
                    if str(candidate).startswith(fp) or fp.startswith(str(candidate)):
                        skip = True
                        break
                if skip:
                    continue
                # 纯特征识别
                if not is_skill_repo(candidate):
                    continue
                # 已找到？
                if str(candidate) in found_paths:
                    continue

                agent_name = derive_agent_name(candidate)
                if agent_name in found:
                    # 用父目录名做后缀
                    parent = candidate.parent.name
                    agent_name = f"{agent_name}-{parent}".lstrip(".")
                if agent_name in found:
                    continue

                found[agent_name] = str(candidate)
                found_paths.append(str(candidate))
                new_count += 1
                real, links, empty = get_dir_info(candidate)
                sc = count_skill_children(candidate)
                status = f"real={real}" if real > 0 else (
                    f"links={links}" if links > 0 else "empty"
                )
                print(f"  [NEW] {agent_name} => {candidate} ({status}, {sc} skills)")
        except (PermissionError, OSError):
            continue

    if not found:
        print("\n未发现任何 skill 目录")
        return None

    # 交互模式
    final = {}
    if interactive:
        print("\n确认纳入的目录:")
        for k, v in found.items():
            resp = _ask(f"  纳入 {k} => {v}? [Y/n] ")
            if resp.lower() != "n":
                final[k] = v
    else:
        final = found

    if not final:
        print("未选择任何目录")
        return None

    # 保留已有配置的 skipNames 和 threshold；merge 模式下同时保留 agentDirs
    existing = load_config() if CONFIG_PATH.exists() else None
    skip_names = existing.get("skipNames") if existing else DEFAULT_SKIP_NAMES
    threshold = existing.get("similarityThreshold") if existing else DEFAULT_SIMILARITY_THRESHOLD

    if merge and existing:
        merged = dict(existing.get("agentDirs", {}))
        added = []
        for k, v in final.items():
            if k in merged and merged[k] != v:
                print(f"  [保留] {k} 已配置为 {merged[k]}，忽略新发现 {v}")
                continue
            if k not in merged:
                added.append(k)
            merged[k] = v
        if added:
            print(f"  [merge] 新增 {len(added)} 个目录: {', '.join(added)}")
        print(f"  [merge] 保留已配置 {len(existing.get('agentDirs', {}))} 个目录")
        final = merged

    # 项目 skill 识别：按 scopeRules / frontmatter 补写中央仓库 meta
    marked = _refresh_scope_metadata(
        rules=_scope_rules(existing or {}), dry_run=dry_run)
    if marked:
        print(f"  [scope] 识别到 {len(marked)} 个项目 skill"
              f"{'（预览）' if dry_run else ''}: "
              + ", ".join(f"{n}[{p or '?'}]" for n, p in marked[:10])
              + (f" 等 {len(marked)} 个" if len(marked) > 10 else ""))

    if dry_run:
        print(f"\n[DRY] 将写入 {len(final)} 个 skill 目录到 config.json（预览，未写入）")
        return final

    save_config(final, skip_names, threshold)
    print(f"\n=== 完成 ===")
    print(f"发现 {len(final)} 个 skill 目录，已写入 config.json")
    print(f"  {CONFIG_PATH}")
    print(f"\n下一步: skillhome sync")
    return final


def walk_dirs(root: Path, max_depth=3):
    """递归遍历子目录，yield 所有目录。"""
    try:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if child.name in EXCLUDE_TOP:
                continue
            yield child
            if max_depth > 1:
                yield from walk_dirs(child, max_depth - 1)
    except (PermissionError, OSError):
        return


# ============================================================
# 相似度计算
# ============================================================
def get_dir_hashes(path: Path) -> dict:
    """计算目录下所有文件的相对路径 -> SHA256 哈希。"""
    hashes = {}
    if not path.is_dir():
        return hashes
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        # 排除元数据文件
        if f.name in (".skillhome.json", ".skill-metadata.yaml"):
            continue
        try:
            rel = str(f.relative_to(path)).replace("\\", "/").lower()
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            hashes[rel] = h
        except (PermissionError, OSError):
            continue
    return hashes


def calc_similarity(hashes_a: dict, hashes_b: dict) -> float:
    all_keys = set(hashes_a.keys()) | set(hashes_b.keys())
    if not all_keys:
        return 1.0
    same = sum(1 for k in all_keys
               if k in hashes_a and k in hashes_b
               and hashes_a[k] == hashes_b[k])
    return round(same / len(all_keys), 4)


def get_skill_mtime(path: Path):
    """获取 skill 目录的最新修改时间。"""
    skill_file = path / "SKILL.md"
    if skill_file.exists():
        return skill_file.stat().st_mtime
    latest = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                m = f.stat().st_mtime
                if m > latest:
                    latest = m
            except (PermissionError, OSError):
                continue
    return latest


def is_empty_dir(path: Path) -> bool:
    try:
        return not any(path.iterdir())
    except (PermissionError, OSError):
        return False


# ============================================================
# 元数据
# ============================================================
def read_meta(skill_path: Path):
    meta_file = skill_path / ".skillhome.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def write_meta(skill_path: Path, meta: dict):
    meta_file = skill_path / ".skillhome.json"
    meta_file.write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8"
    )


def _next_backup_path(name: str) -> Path:
    """backups/ 下 <name>.bak.<时间戳> 的唯一路径；重名追加 -2/-3...。"""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    dest = BACKUP_DIR / f"{name}.bak.{stamp}"
    n = 1
    while dest.exists():
        n += 1
        dest = BACKUP_DIR / f"{name}.bak.{stamp}-{n}"
    return dest


# ============================================================
# 项目 scope 识别
# ============================================================
# 项目 skill（scope=project）只保留在中央仓库，sync 时不扩散到各
# agent 目录；--include-project 可强制扩散。识别依据按优先级：
#   1. SKILL.md frontmatter: scope: project / project: <name>
#      （显式 scope: global 可关闭自动识别）
#   2. scopeRules 模式命中 skill 名（fnmatch，另附 <proj>- 前缀兜底）
#   3. 来源路径目录段命中项目名（如 .../duly/.agents/skills/x）
# meta 里 "scope_manual": true 表示人工指定 scope，跳过自动识别。
def _scope_rules(cfg=None):
    """生效的 scopeRules：config.json 有该键则用其值（可为 {}），否则用默认。"""
    if cfg is None:
        cfg = load_config() or {}
    rules = cfg.get("scopeRules")
    if isinstance(rules, dict):
        return rules
    return DEFAULT_SCOPE_RULES


def _skill_frontmatter(skill_path: Path) -> dict:
    """解析 SKILL.md frontmatter 顶层 key: value（无第三方依赖）。"""
    sm = skill_path / "SKILL.md"
    if not sm.is_file():
        return {}
    try:
        text = _decode_text(sm.read_bytes())
    except OSError:
        return {}
    s = text.lstrip()
    if not s.startswith("---"):
        return {}
    data = {}
    for line in s[3:].splitlines():
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$", line)
        if m:
            data[m.group(1).lower()] = m.group(2).strip().strip("\"'")
    return data


def _match_project_by_name(sname: str, rules: dict):
    """scopeRules 模式或 <proj>- 前缀命中 skill 名，返回项目名。"""
    low = sname.lower()
    for proj, patterns in rules.items():
        pats = patterns if isinstance(patterns, list) else [patterns]
        for pat in pats:
            if fnmatch.fnmatchcase(low, str(pat).lower()):
                return proj
        if low.startswith(str(proj).lower() + "-"):
            return proj
    return None


def _match_project_by_path(paths, rules: dict):
    """任一来源路径的目录段命中项目名，返回项目名。

    忽略末段（skill 目录名本身）——路径规则针对的是父级项目目录，
    如 .../duly/.agents/skills/foo；否则名为 skillhome 的 skill
    会被自身目录名误判为项目 skill。
    """
    segs = set()
    for p in paths or []:
        for seg in Path(str(p)).parts[:-1]:
            segs.add(seg.lower())
    for proj in rules:
        if str(proj).lower() in segs:
            return proj
    return None


def detect_project_scope(sname: str, skill_path=None, origin_paths=None,
                         rules=None):
    """识别项目 skill。返回 (is_project, project_name|None)。"""
    if rules is None:
        rules = _scope_rules()
    fm = _skill_frontmatter(skill_path) if skill_path else {}
    fm_scope = str(fm.get("scope", "")).strip().lower()
    fm_project = fm.get("project") or None
    if fm_scope == "global":
        return False, None
    if fm_scope == "project":
        proj = fm_project or _match_project_by_name(sname, rules)
        if not proj:
            paths = ([str(skill_path)] if skill_path else []) \
                + list(origin_paths or [])
            proj = _match_project_by_path(paths, rules)
        return True, proj
    if fm_project:
        return True, fm_project
    hit = _match_project_by_name(sname, rules)
    if hit:
        return True, hit
    paths = ([str(skill_path)] if skill_path else []) \
        + list(origin_paths or [])
    hit = _match_project_by_path(paths, rules)
    if hit:
        return True, hit
    return False, None


def _skill_scope_info(cpath: Path, sname: str, rules=None, origin_paths=None):
    """skill 的有效 (scope, project)。meta 中 scope_manual 优先，否则自动识别。"""
    meta = read_meta(cpath)
    if (meta and meta.get("scope_manual")
            and meta.get("scope") in ("global", "project")):
        return meta["scope"], meta.get("project")
    is_proj, proj = detect_project_scope(sname, cpath, origin_paths, rules)
    return ("project" if is_proj else "global"), proj


def _refresh_scope_metadata(rules=None, dry_run=False):
    """扫描中央仓库，自动识别项目归属并补写 meta 的 scope/project。

    不覆盖 scope_manual 的人工指定；识别为 project 时把 auto 的
    global 标记翻转为 false（global_manual 人工开关豁免）。
    返回 [(sname, project)] 识别结果。
    """
    if not CENTRAL_SKILLS.exists():
        return []
    if rules is None:
        rules = _scope_rules()
    marked = []
    for d in sorted(CENTRAL_SKILLS.iterdir()):
        if not d.is_dir():
            continue
        meta = read_meta(d)
        if (meta and meta.get("scope_manual")
                and meta.get("scope") in ("global", "project")):
            continue
        is_proj, proj = detect_project_scope(d.name, d, rules=rules)
        if is_proj:
            marked.append((d.name, proj))
        if not meta:
            continue  # 新 skill 的 meta 由 sync 阶段 4 创建，这里不代建
        scope = "project" if is_proj else "global"
        changed = False
        if meta.get("scope") != scope:
            meta["scope"] = scope
            changed = True
        if proj and meta.get("project") != proj:
            meta["project"] = proj
            changed = True
        elif not proj and meta.pop("project", None) is not None:
            changed = True
        if is_proj and meta.get("global") and not meta.get("global_manual"):
            meta["global"] = False
            changed = True
        if changed and not dry_run:
            write_meta(d, meta)
    return marked


def _parse_scope_flags(args):
    """提取 --scope/--project。返回 (scope, project, 剩余 args)。"""
    scope = "all"
    project = None
    rest = []
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a == "--scope":
            if i + 1 < len(args):
                scope = args[i + 1].strip().lower()
                skip = True
            continue
        if a.startswith("--scope="):
            scope = a.split("=", 1)[1].strip().lower()
            continue
        if a == "--project":
            if i + 1 < len(args):
                project = args[i + 1].strip()
                skip = True
            continue
        if a.startswith("--project="):
            project = a.split("=", 1)[1].strip()
            continue
        rest.append(a)
    return scope, project, rest


# ============================================================
# 项目上下文 — 活跃项目状态与按需扩散
# ============================================================
def load_state():
    """读取本机上下文状态（~/.skillhome/state.json）；缺失/损坏返回 {}。"""
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _project_skills(project, rules=None):
    """中央仓库中归属 project 的项目 skill：{name: central_path}。"""
    if rules is None:
        rules = _scope_rules()
    found = {}
    if not project or not CENTRAL_SKILLS.exists():
        return found
    for d in CENTRAL_SKILLS.iterdir():
        if not d.is_dir():
            continue
        scope, proj = _skill_scope_info(d, d.name, rules)
        if scope == "project" and proj == project:
            found[d.name] = d
    return found


def _known_projects(rules=None):
    """可选项目集合：scopeRules 键 ∪ 中央仓库中识别出的 project。"""
    if rules is None:
        rules = _scope_rules()
    projs = set(rules.keys())
    if CENTRAL_SKILLS.exists():
        for d in CENTRAL_SKILLS.iterdir():
            if not d.is_dir():
                continue
            scope, proj = _skill_scope_info(d, d.name, rules)
            if scope == "project" and proj:
                projs.add(proj)
    return sorted(projs)


def _infer_project_from_path(path, projects):
    """按路径段（含末段）匹配项目名，取最深命中；无命中返回 None。"""
    try:
        parts = Path(path).resolve().parts
    except OSError:
        parts = Path(path).parts
    lookup = {str(p).lower(): p for p in projects}
    hit = None
    for seg in parts:
        if seg.lower() in lookup:
            hit = lookup[seg.lower()]
    return hit


def _print_project_choices(projects, rules):
    if projects:
        print("可选项目:")
        for p in projects:
            print(f"  {p}  ({len(_project_skills(p, rules))} 个 skill)")
    print("用法: skillhome use <project> | --none [--dry-run]")


def _apply_project_links(project, agent_dirs, rules=None, dry_run=False,
                         prev_names=()):
    """让各 agent 目录的项目链接与活跃项目一致（幂等）。

    - project 的项目 skill：缺链则建；已有链接跳过；真实目录不覆盖。
    - 其它项目的项目 skill 链接：移除（global_manual 人工扩散豁免）。
    - prev_names 中已不在中央仓库的遗留链接：一并移除。
    返回 (项目 skill 名列表, 新建数, 移除数, 已有数)。
    """
    if rules is None:
        rules = _scope_rules()
    want = _project_skills(project, rules)
    stale = set()
    if CENTRAL_SKILLS.exists():
        for d in CENTRAL_SKILLS.iterdir():
            if not d.is_dir():
                continue
            scope, proj = _skill_scope_info(d, d.name, rules)
            if scope != "project" or proj == project:
                continue
            meta = read_meta(d) or {}
            if meta.get("global") and meta.get("global_manual"):
                continue
            stale.add(d.name)
    stale.update(n for n in prev_names
                 if n not in want and not (CENTRAL_SKILLS / n).is_dir())

    created = removed = kept = 0
    for agent_name, adir_str in (agent_dirs or {}).items():
        adir = Path(adir_str)
        if not adir.is_dir():
            continue
        for name in sorted(stale):
            lp = adir / name
            if (lp.exists() or lp.is_symlink()) and is_link(lp):
                log(f"[{agent_name}] 移除项目链接: {name}")
                removed += 1
                if not dry_run:
                    remove_link(lp)
        for name, cpath in sorted(want.items()):
            lp = adir / name
            if lp.exists() or lp.is_symlink():
                if is_link(lp):
                    kept += 1
                else:
                    log(f"[{agent_name}] {name} 为真实目录，跳过（不覆盖）",
                        "WARN")
                continue
            log(f"[{agent_name}] 链接项目 skill: {name}")
            created += 1
            if not dry_run:
                create_link(lp, cpath)
    return sorted(want), created, removed, kept


def cmd_use(args):
    """激活/切换/退出项目上下文：skillhome use <project> | --none | (推断)。"""
    cfg = load_config()
    if not cfg:
        print("config.json 不存在，请先运行: skillhome init")
        return
    args = args or []
    dry_run = "--dry" in args or "--dry-run" in args
    rest = [a for a in args if a not in ("--dry", "--dry-run")]
    agent_dirs = cfg.get("agentDirs", {})
    rules = _scope_rules(cfg)
    projects = _known_projects(rules)
    prev = load_state()
    prev_names = prev.get("activatedSkills") or []

    if rest and rest[0] in ("--none", "none", "off", "-"):
        _names, _c, removed, _k = _apply_project_links(
            None, agent_dirs, rules, dry_run=dry_run, prev_names=prev_names)
        if not dry_run:
            save_state({
                "activeProject": None,
                "activatedSkills": [],
                "activatedAt": datetime.now().isoformat(),
                "pwd": str(Path.cwd()),
            })
        print(f"{'[DRY] ' if dry_run else ''}已退出项目上下文: "
              f"清理项目链接 {removed} 条，全局 skills 不受影响")
        return

    project = rest[0] if rest else None
    if project is not None and project.startswith("-"):
        print(f"未知参数: {project}")
        _print_project_choices(projects, rules)
        return
    if project is None:
        project = _infer_project_from_path(Path.cwd(), projects)
        if not project:
            print(f"无法从当前目录推断项目: {Path.cwd()}")
            _print_project_choices(projects, rules)
            return
        print(f"当前目录推断项目: {project}")
    elif project not in projects:
        print(f"未知项目: {project}")
        _print_project_choices(projects, rules)
        print("若为新项目，请先在 config.json 的 scopeRules 中登记。")
        return

    names, created, removed, kept = _apply_project_links(
        project, agent_dirs, rules, dry_run=dry_run, prev_names=prev_names)
    if not names:
        print(f"提示: 项目 '{project}' 暂无对应项目 skill"
              f"（scope=project 且 project={project}）")
    if not dry_run:
        save_state({
            "activeProject": project,
            "activatedSkills": names,
            "activatedAt": datetime.now().isoformat(),
            "pwd": str(Path.cwd()),
        })
    print(f"{'[DRY] ' if dry_run else ''}活跃项目: {project} | "
          f"项目 skill {len(names)} 个 | "
          f"新建链接 {created} | 已有 {kept} | 清理 {removed}")
    if names:
        print("  " + ", ".join(names))


def cmd_context(args):
    """显示当前上下文；--ensure 补齐缺失的项目链接并清理过期链接。"""
    cfg = load_config()
    if not cfg:
        print("config.json 不存在，请先运行: skillhome init")
        return
    args = args or []
    ensure = "--ensure" in args
    rules = _scope_rules(cfg)
    agent_dirs = cfg.get("agentDirs", {})
    state = load_state()
    project = state.get("activeProject")

    print("SkillHome 上下文")
    if not project:
        print("  活跃项目: (无 — 仅全局 skills 生效)")
        inferred = _infer_project_from_path(
            Path.cwd(), _known_projects(rules))
        if inferred:
            print(f"  当前目录: {Path.cwd()}")
            print(f"  推断项目: {inferred}"
                  f" → skillhome use {inferred} 激活")
        return

    skills = _project_skills(project, rules)
    print(f"  活跃项目: {project}")
    print(f"  激活时间: {state.get('activatedAt') or '-'}")
    print(f"  记录路径: {state.get('pwd') or '-'}")
    print(f"  项目 skill ({len(skills)}): "
          + (", ".join(sorted(skills)) if skills else "-"))

    missing = 0
    for agent_name, adir_str in agent_dirs.items():
        adir = Path(adir_str)
        if not adir.is_dir():
            continue
        miss = [n for n in sorted(skills) if not is_link(adir / n)]
        missing += len(miss)
        if miss:
            print(f"  [{agent_name}] 缺 {len(miss)} 条链接: "
                  + ", ".join(miss))
    if missing == 0:
        print("  链接状态: 全部就绪")
    elif ensure:
        names, created, removed, kept = _apply_project_links(
            project, agent_dirs, rules,
            prev_names=state.get("activatedSkills") or [])
        save_state({**state, "activatedSkills": names})
        print(f"  已补齐: 新建 {created} | 已有 {kept} | 清理 {removed}")
    else:
        print("  修复: skillhome context --ensure")


# ============================================================
# sync — 核心同步逻辑
# ============================================================
def cmd_sync(dry_run=False, incremental=True, verbose=False, prune=False,
             include_project=False):
    cfg = load_config()
    if not cfg:
        print("config.json 不存在，请先运行: skillhome init")
        return None

    agent_dirs = cfg.get("agentDirs", {})
    skip_names = cfg.get("skipNames", DEFAULT_SKIP_NAMES)
    threshold = cfg.get("similarityThreshold", DEFAULT_SIMILARITY_THRESHOLD)
    scope_rules = _scope_rules(cfg)

    report = {"migrated": 0, "created": 0, "skipped": 0,
              "pruned": 0, "repointed": 0, "conflicts": 0, "reals_left": 0,
              "scope_skipped": 0}

    log(f"=== SkillHome 同步开始 (mode: {'incremental' if incremental else 'full'}"
        f"{', dry-run' if dry_run else ''}{', prune' if prune else ''}"
        f"{', include-project' if include_project else ''}) ===")

    # 阶段 1：扫描
    skill_registry = {}  # name -> {real_locations, link_locations, real_paths}
    central_distribution = {}

    if CENTRAL_SKILLS.exists():
        for cs in CENTRAL_SKILLS.iterdir():
            if not cs.is_dir():
                continue
            meta = read_meta(cs)
            # 无 meta 的中央 skill（如云端 pull 下来的）也要注册，
            # 否则永远不会进入分发阶段
            central_distribution[cs.name] = (
                list(meta["sources"]) if meta and meta.get("sources") else []
            )

    for agent_name, agent_dir_str in agent_dirs.items():
        agent_dir = Path(agent_dir_str)
        if not agent_dir.is_dir():
            continue
        try:
            children = [c for c in agent_dir.iterdir()
                        if c.is_dir() and c.name not in skip_names]
        except (PermissionError, OSError):
            continue
        for child in children:
            sname = child.name
            linked = is_link(child)

            if sname not in skill_registry:
                skill_registry[sname] = {
                    "real_locations": [],
                    "link_locations": [],
                    "real_paths": {},
                }

            if linked:
                skill_registry[sname]["link_locations"].append(agent_name)
            else:
                if is_empty_dir(child):
                    log(f"[{agent_name}] {sname} 空目录，清理", "WARN")
                    if not dry_run:
                        shutil.rmtree(child, ignore_errors=True)
                    continue
                skill_registry[sname]["real_locations"].append(agent_name)
                skill_registry[sname]["real_paths"][agent_name] = str(child)

    for sname, sources in central_distribution.items():
        if sname not in skill_registry:
            skill_registry[sname] = {
                "real_locations": [], "link_locations": [], "real_paths": {}
            }

    # 项目 scope 识别用的来源路径（迁移前的真实目录位置）
    origin_paths = {sn: list(info["real_paths"].values())
                    for sn, info in skill_registry.items()}

    # 阶段 2：迁移真实存储到中央
    log("=== 阶段 2: 迁移真实存储 ===")
    skill_distribution = {}
    central_path_of = {}

    for sname in sorted(skill_registry.keys()):
        info = skill_registry[sname]
        real_agents = info["real_locations"]
        link_agents = info["link_locations"]
        all_agents = sorted(set(real_agents + link_agents))

        if sname in central_distribution:
            all_agents = sorted(set(all_agents + central_distribution[sname]))

        central_existing = CENTRAL_SKILLS / sname
        central_exists = central_existing.exists()

        if not real_agents:
            if central_exists:
                central_path_of[sname] = central_existing
                skill_distribution[sname] = all_agents
            continue

        if len(real_agents) == 1 and not central_exists:
            src_agent = real_agents[0]
            src_path = Path(info["real_paths"][src_agent])
            log(f"{sname} : 迁移 ({src_agent} -> central)")
            report["migrated"] += 1
            if not dry_run:
                shutil.move(str(src_path), str(central_existing))
            central_path_of[sname] = central_existing
            skill_distribution[sname] = all_agents

        elif len(real_agents) >= 1 and central_exists:
            central_hashes = get_dir_hashes(central_existing)
            central_mtime = get_skill_mtime(central_existing)
            # 唯一真实副本与中央同名 = 同名更新（用户就地大改），
            # 即使相似度低于阈值也写回中央，不产生 -- 孤儿变体；
            # -- 后缀只保留给多个真实副本同轮冲突的场景
            single_source = len(real_agents) == 1

            for src_agent in real_agents:
                src_path = Path(info["real_paths"][src_agent])
                src_hashes = get_dir_hashes(src_path)
                src_mtime = get_skill_mtime(src_path)
                sim = calc_similarity(central_hashes, src_hashes)
                log(f"{sname} : 检测到 {src_agent} 的真实副本，与中央相似度={sim}", "WARN")

                if sim >= threshold:
                    if src_mtime > central_mtime:
                        log(f"{sname} : 以 {src_agent} 版本更新中央 (较新)")
                        if not dry_run:
                            shutil.rmtree(central_existing, ignore_errors=True)
                            shutil.move(str(src_path), str(central_existing))
                        central_mtime = src_mtime
                        central_hashes = src_hashes
                    else:
                        log(f"{sname} : 保留中央版本，删除 {src_agent} 副本 (较旧)")
                        if not dry_run:
                            shutil.rmtree(src_path, ignore_errors=True)
                elif single_source:
                    # 同名更新：较新版本进中央，被替换的一版备份到 backups/
                    bak = _next_backup_path(sname)
                    if src_mtime > central_mtime:
                        log(f"{sname} : {src_agent} 为同名更新 (sim={sim})，"
                            f"写回中央，旧版备份 -> {bak.name}")
                        if not dry_run:
                            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(central_existing), str(bak))
                            shutil.move(str(src_path), str(central_existing))
                        central_mtime = src_mtime
                        central_hashes = src_hashes
                    else:
                        log(f"{sname} : {src_agent} 差异副本较旧 (sim={sim})，"
                            f"保留中央版本，副本备份 -> {bak.name}")
                        if not dry_run:
                            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(src_path), str(bak))
                else:
                    suffixed = f"{sname}--{src_agent}"
                    suffixed_central = CENTRAL_SKILLS / suffixed
                    log(f"{sname} : 相似度不足，保留为 {suffixed}")
                    report["conflicts"] += 1
                    if not dry_run:
                        shutil.move(str(src_path), str(suffixed_central))
                    central_path_of[suffixed] = suffixed_central
                    skill_distribution[suffixed] = [src_agent]
                    if src_agent in all_agents:
                        all_agents.remove(src_agent)

            central_path_of[sname] = central_existing
            skill_distribution[sname] = all_agents

        elif len(real_agents) > 1 and not central_exists:
            log(f"{sname} : 多个真实来源 ({', '.join(real_agents)})，计算相似度...", "WARN")
            base_agent = real_agents[0]
            base_path = Path(info["real_paths"][base_agent])
            merged_hashes = get_dir_hashes(base_path)
            merged_mtime = get_skill_mtime(base_path)
            merged_agent = base_agent
            merge_notes = []

            for i in range(1, len(real_agents)):
                other = real_agents[i]
                other_path = Path(info["real_paths"][other])
                other_hashes = get_dir_hashes(other_path)
                other_mtime = get_skill_mtime(other_path)
                sim = calc_similarity(merged_hashes, other_hashes)
                log(f"  相似度 {merged_agent} vs {other} = {sim}", "DEBUG")

                if sim >= threshold:
                    if other_mtime > merged_mtime:
                        merged_agent = other
                        merged_hashes = other_hashes
                        merged_mtime = other_mtime
                        merge_notes.append(f"合并 {other} (较新, sim={sim}) 覆盖前版")
                    else:
                        merge_notes.append(f"合并 {other} (较旧, sim={sim}) 保留前版")
                else:
                    suffixed = f"{sname}--{other}"
                    suffixed_central = CENTRAL_SKILLS / suffixed
                    log(f"  保留为独立条目: {suffixed}")
                    report["conflicts"] += 1
                    if not dry_run:
                        shutil.move(str(other_path), str(suffixed_central))
                    central_path_of[suffixed] = suffixed_central
                    skill_distribution[suffixed] = [other]
                    if other in all_agents:
                        all_agents.remove(other)

            merged_path = Path(info["real_paths"][merged_agent])
            log(f"{sname} : 合并完成，主来源 {merged_agent}")
            report["migrated"] += 1
            if not dry_run:
                shutil.move(str(merged_path), str(central_existing))
                for a in real_agents:
                    if a != merged_agent:
                        rp = info["real_paths"].get(a)
                        if rp and Path(rp).exists():
                            shutil.rmtree(rp, ignore_errors=True)

            central_path_of[sname] = central_existing
            skill_distribution[sname] = all_agents
            if not dry_run and merge_notes:
                write_meta(central_existing, {
                    "name": sname,
                    "sources": all_agents,
                    "merged": True,
                    "merge_notes": merge_notes,
                    "merged_at": datetime.now().isoformat(),
                })

    # 阶段 3：链接管理
    log("=== 阶段 3: 链接管理 ===")

    if prune:
        for agent_name, agent_dir_str in agent_dirs.items():
            agent_dir = Path(agent_dir_str)
            if not agent_dir.is_dir():
                continue
            removed, repointed = _prune_agent_dir(
                agent_dir, agent_name, skip_names=skip_names, dry_run=dry_run)
            report["pruned"] += removed
            report["repointed"] += repointed
        if report["pruned"] or report["repointed"]:
            log(f"链接清理: 死链 {report['pruned']} 条 | 重指 {report['repointed']} 条")
        else:
            log("链接清理: 无死链/链式链接", "DEBUG")

    if incremental:
        for sname in sorted(skill_distribution.keys()):
            cpath = central_path_of.get(sname)
            if not cpath or not cpath.exists():
                continue
            meta = read_meta(cpath)
            # meta 缺失时套用阶段 4 的默认规则：非 -- 变体默认 global
            is_global = (meta or {}).get("global", "--" not in sname)
            scope, project = _skill_scope_info(
                cpath, sname, scope_rules, origin_paths.get(sname))

            if scope == "project" and not include_project:
                log(f"{sname} : 项目 skill"
                    f"{f' ({project})' if project else ''}，"
                    f"仅保留中央仓库，跳过扩散", "DEBUG")
                report["scope_skipped"] += 1
                continue

            target_agents = skill_distribution[sname]
            if is_global or (scope == "project" and include_project):
                target_agents = sorted(agent_dirs.keys())
                log(f"{sname} : global 标记，扩散到所有 agent", "DEBUG")

            for agent_name in target_agents:
                agent_dir_str = agent_dirs.get(agent_name)
                if not agent_dir_str:
                    continue
                agent_dir = Path(agent_dir_str)
                if not agent_dir.is_dir():
                    continue
                link_path = agent_dir / sname
                if not link_path.exists() and not link_path.is_symlink():
                    log(f"[{agent_name}] 创建缺失链接: {sname}")
                    report["created"] += 1
                    if not dry_run:
                        create_link(link_path, cpath)
                else:
                    report["skipped"] += 1
    else:
        # 完整模式：先删旧链接，再重建
        for agent_name, agent_dir_str in agent_dirs.items():
            agent_dir = Path(agent_dir_str)
            if not agent_dir.is_dir():
                continue
            try:
                children = [c for c in agent_dir.iterdir()
                            if c.is_dir() and c.name not in skip_names]
            except (PermissionError, OSError):
                continue
            for child in children:
                if is_link(child):
                    log(f"[{agent_name}] 删除旧链接: {child.name}")
                    if not dry_run:
                        remove_link(child)

        for sname in sorted(skill_distribution.keys()):
            cpath = central_path_of.get(sname)
            if not cpath or not cpath.exists():
                continue
            meta = read_meta(cpath)
            is_global = (meta or {}).get("global", "--" not in sname)
            scope, project = _skill_scope_info(
                cpath, sname, scope_rules, origin_paths.get(sname))

            if scope == "project" and not include_project:
                log(f"{sname} : 项目 skill"
                    f"{f' ({project})' if project else ''}，"
                    f"仅保留中央仓库，跳过扩散", "DEBUG")
                report["scope_skipped"] += 1
                continue

            target_agents = skill_distribution[sname]
            if is_global or (scope == "project" and include_project):
                target_agents = sorted(agent_dirs.keys())
                log(f"{sname} : global 标记，扩散到所有 agent", "DEBUG")

            for agent_name in target_agents:
                agent_dir_str = agent_dirs.get(agent_name)
                if not agent_dir_str:
                    continue
                agent_dir = Path(agent_dir_str)
                if not agent_dir.is_dir():
                    continue
                link_path = agent_dir / sname
                log(f"[{agent_name}] 创建链接: {sname}")
                report["created"] += 1
                if not dry_run:
                    create_link(link_path, cpath)

    # 阶段 4：更新元数据
    log("=== 阶段 4: 更新元数据 ===")
    for sname in sorted(skill_distribution.keys()):
        cpath = central_path_of.get(sname)
        if not cpath or not cpath.exists():
            continue
        existing = read_meta(cpath)
        desired_sources = skill_distribution[sname]
        scope, project = _skill_scope_info(
            cpath, sname, scope_rules, origin_paths.get(sname))
        default_global = "--" not in sname and scope != "project"

        if not existing:
            if not dry_run:
                new_meta = {
                    "name": sname,
                    "sources": desired_sources,
                    "merged": False,
                    "global": default_global,
                    "scope": scope,
                    "created_at": datetime.now().isoformat(),
                }
                if project:
                    new_meta["project"] = project
                write_meta(cpath, new_meta)
                if scope == "project":
                    log(f"{sname} : 识别为项目 skill"
                        f"{f' ({project})' if project else ''}，"
                        f"不扩散", "DEBUG")
                elif default_global:
                    log(f"{sname} : 新 skill，默认标记为 global", "DEBUG")
        else:
            needs_update = False
            current_sources = existing.get("sources", [])
            if current_sources != desired_sources:
                existing["sources"] = desired_sources
                needs_update = True
            if "global" not in existing:
                existing["global"] = default_global
                needs_update = True
                if default_global:
                    log(f"{sname} : 补充 global 标记（默认规则）", "DEBUG")
            if not existing.get("scope_manual"):
                if existing.get("scope") != scope:
                    existing["scope"] = scope
                    needs_update = True
                if project and existing.get("project") != project:
                    existing["project"] = project
                    needs_update = True
                elif not project and existing.pop("project", None) is not None:
                    needs_update = True
                # 识别为项目 skill -> 自动标记 global: false
                # （global_manual 人工开关豁免）
                if (scope == "project" and existing.get("global")
                        and not existing.get("global_manual")):
                    existing["global"] = False
                    needs_update = True
                    log(f"{sname} : 识别为项目 skill"
                        f"{f' ({project})' if project else ''}，"
                        f"自动标记 global: false", "DEBUG")
            if needs_update and not dry_run:
                write_meta(cpath, existing)

    # 项目上下文：活跃项目的 skills 保持链接（sync --full 重建后同样生效），
    # 其它项目的链接清出。--include-project 显式全量扩散时不做清理。
    state = load_state()
    active = state.get("activeProject")
    if active and not include_project:
        names, p_created, p_removed, _p_kept = _apply_project_links(
            active, agent_dirs, scope_rules, dry_run=dry_run,
            prev_names=state.get("activatedSkills") or [])
        report["created"] += p_created
        if p_created or p_removed:
            log(f"活跃项目 {active}: 补齐项目链接 {p_created} | "
                f"清理其它项目 {p_removed}")
        else:
            log(f"活跃项目 {active}: {len(names)} 个项目 skill 链接已就绪",
                "DEBUG")
        if not dry_run and names != (state.get("activatedSkills") or []):
            state["activatedSkills"] = names
            save_state(state)

    # 汇总
    real_count = 0
    for agent_name, agent_dir_str in agent_dirs.items():
        agent_dir = Path(agent_dir_str)
        if not agent_dir.is_dir():
            continue
        try:
            for child in agent_dir.iterdir():
                if child.is_dir() and child.name not in skip_names:
                    if not is_link(child):
                        real_count += 1
        except (PermissionError, OSError):
            continue
    report["reals_left"] = real_count
    log(f"=== 完成 | 中央 skill: {len(skill_distribution)} | 残留真实目录: {real_count} ===", "OK")
    log(f"同步报告: 迁移 {report['migrated']} | 新建链接 {report['created']} | "
        f"已有(跳过) {report['skipped']} | 死链清理 {report['pruned']} | "
        f"链式重指 {report['repointed']} | 冲突变体 {report['conflicts']} | "
        f"项目 skill 未扩散 {report['scope_skipped']}", "OK")
    return report


# ============================================================
# status
# ============================================================
def cmd_status():
    cfg = load_config()
    if not cfg:
        print("config.json 不存在，请先运行: skillhome init")
        return

    agent_dirs = cfg.get("agentDirs", {})
    skip_names = cfg.get("skipNames", DEFAULT_SKIP_NAMES)

    central_count = sum(1 for d in CENTRAL_SKILLS.iterdir() if d.is_dir()) if CENTRAL_SKILLS.exists() else 0
    total_links = 0
    total_reals = 0
    rows = []

    for agent_name, agent_dir_str in agent_dirs.items():
        agent_dir = Path(agent_dir_str)
        if not agent_dir.is_dir():
            rows.append((agent_name, 0, 0, "NOT_FOUND"))
            continue
        links = reals = 0
        try:
            for child in agent_dir.iterdir():
                if not child.is_dir() or child.name in skip_names:
                    continue
                if is_link(child):
                    links += 1
                else:
                    reals += 1
        except (PermissionError, OSError):
            pass
        total_links += links
        total_reals += reals
        status = "OK" if reals == 0 else "NEEDS_SYNC"
        rows.append((agent_name, links, reals, status))

    print("SkillHome 状态")
    print(f"  中央仓库: {CENTRAL_SKILLS}")
    print(f"  skill 总数: {central_count}")
    print(f"  链接总数: {total_links}")
    print(f"  残留真实目录: {total_reals}")
    print(f"  agent 目录数: {len(agent_dirs)}")
    active = load_state().get("activeProject")
    if active:
        n = len(load_state().get("activatedSkills") or [])
        print(f"  活跃项目: {active} ({n} 个项目 skill)")
    else:
        print("  活跃项目: (无)")
    print()
    print(f"  {'Agent':<25} {'Links':>8} {'Reals':>8}  Status")
    print(f"  {'-'*25} {'-'*8} {'-'*8}  {'-'*12}")
    for name, links, reals, status in rows:
        print(f"  {name:<25} {links:>8} {reals:>8}  {status}")


# ============================================================
# list / search
# ============================================================
def _iter_skill_rows(scope_filter="all", project_filter=None):
    """遍历中央仓库，产出 (name, scope, project, sources, merged, desc)。
    应用 --scope / --project 过滤。"""
    if not CENTRAL_SKILLS.exists():
        return []
    rules = _scope_rules()
    rows = []
    for s in sorted([d for d in CENTRAL_SKILLS.iterdir() if d.is_dir()],
                    key=lambda x: x.name):
        meta = read_meta(s)
        scope, project = _skill_scope_info(s, s.name, rules)
        if scope_filter == "global" and scope == "project":
            continue
        if scope_filter == "project" and scope != "project":
            continue
        if project_filter and project != project_filter:
            continue
        sources = meta.get("sources", []) if meta else []
        merged = meta.get("merged", False) if meta else False
        desc = _skill_frontmatter(s).get("description", "")
        rows.append((s.name, scope, project or "",
                     ", ".join(sources), "Y" if merged else "", desc))
    return rows


def _print_skill_rows(rows):
    print(f"  {'Skill':<35} {'Scope':<8} {'Project':<14} "
          f"{'Sources':<28} Merged")
    print(f"  {'-'*35} {'-'*8} {'-'*14} {'-'*28} {'-'*6}")
    for name, scope, project, sources, merged, _ in rows:
        print(f"  {name:<35} {scope:<8} {project:<14} "
              f"{sources:<28} {merged}")
    print(f"\n共 {len(rows)} 个 skill")


def cmd_list(args=None):
    if not CENTRAL_SKILLS.exists():
        print("中央仓库不存在")
        return
    scope_filter, project_filter, _ = _parse_scope_flags(args or [])
    if scope_filter not in ("all", "global", "project"):
        print(f"未知 scope: {scope_filter}（可选: all | global | project）")
        return
    _print_skill_rows(_iter_skill_rows(scope_filter, project_filter))


def cmd_search(args):
    scope_filter, project_filter, rest = _parse_scope_flags(args or [])
    if scope_filter not in ("all", "global", "project"):
        print(f"未知 scope: {scope_filter}（可选: all | global | project）")
        return
    if not rest:
        print("用法: skillhome search <关键词> [--scope all|global|project] "
              "[--project <name>]")
        print("  匹配 skill 名与 SKILL.md frontmatter 的 description")
        return
    if not CENTRAL_SKILLS.exists():
        print("中央仓库不存在")
        return
    kw = " ".join(rest).lower()
    rows = [r for r in _iter_skill_rows(scope_filter, project_filter)
            if kw in r[0].lower() or kw in r[5].lower()]
    _print_skill_rows(rows)


# ============================================================
# link / unlink
# ============================================================
def cmd_link(skill_name, agent_name):
    cfg = load_config()
    if not cfg:
        print("请先运行: skillhome init")
        return
    if not skill_name or not agent_name:
        print("用法: skillhome link <skill> <agent>")
        print(f"agent 名称: {', '.join(cfg.get('agentDirs', {}).keys())}")
        return
    agent_dirs = cfg.get("agentDirs", {})
    if agent_name not in agent_dirs:
        print(f"未知 agent: {agent_name}")
        print(f"可选: {', '.join(agent_dirs.keys())}")
        return
    central_path = CENTRAL_SKILLS / skill_name
    if not central_path.exists():
        print(f"中央仓库没有此 skill: {skill_name}")
        return
    link_path = Path(agent_dirs[agent_name]) / skill_name
    if link_path.exists() or link_path.is_symlink():
        if is_link(link_path):
            print(f"已存在链接: {link_path}")
            return
        else:
            print(f"目标位置已有真实目录，不覆盖: {link_path}")
            return
    if create_link(link_path, central_path):
        print(f"已创建链接: {agent_name}/{skill_name} -> central")
        # 更新元数据 sources
        meta = read_meta(central_path)
        if meta:
            sources = meta.get("sources", [])
            if agent_name not in sources:
                sources.append(agent_name)
                meta["sources"] = sources
                write_meta(central_path, meta)
    else:
        print("创建链接失败")


def cmd_unlink(skill_name, agent_name):
    cfg = load_config()
    if not cfg:
        print("请先运行: skillhome init")
        return
    if not skill_name or not agent_name:
        print("用法: skillhome unlink <skill> <agent>")
        return
    agent_dirs = cfg.get("agentDirs", {})
    if agent_name not in agent_dirs:
        print(f"未知 agent: {agent_name}")
        return
    link_path = Path(agent_dirs[agent_name]) / skill_name
    if not link_path.exists() and not link_path.is_symlink():
        print(f"路径不存在: {link_path}")
        return
    if is_link(link_path):
        remove_link(link_path)
        print(f"已移除链接: {agent_name}/{skill_name}")
        # 更新元数据 sources
        central_path = CENTRAL_SKILLS / skill_name
        meta = read_meta(central_path)
        if meta:
            sources = meta.get("sources", [])
            if agent_name in sources:
                sources.remove(agent_name)
                meta["sources"] = sources
                write_meta(central_path, meta)
    else:
        print(f"这不是链接，不删除真实目录: {link_path}")


# ============================================================
# global on/off
# ============================================================
def cmd_global(skill_name, action):
    cfg = load_config()
    if not cfg:
        print("请先运行: skillhome init")
        return
    if not skill_name:
        print("用法: skillhome global <skill> [on|off]")
        print("  on  — 标记为全局，sync 时自动扩散到所有 agent")
        print("  off — 取消全局标记，仅保留来源 agent 的链接")
        # 列出当前 global skill
        if CENTRAL_SKILLS.exists():
            globals_list = []
            for d in CENTRAL_SKILLS.iterdir():
                if not d.is_dir():
                    continue
                meta = read_meta(d)
                if meta and meta.get("global"):
                    globals_list.append(d.name)
            if globals_list:
                print("\n当前全局 skill:")
                for g in globals_list:
                    print(f"  {g}")
        return
    central_path = CENTRAL_SKILLS / skill_name
    if not central_path.exists():
        print(f"中央仓库没有此 skill: {skill_name}")
        return
    meta = read_meta(central_path) or {}
    turn_on = action not in ("off", "false", "0")
    meta["global"] = turn_on
    meta["global_manual"] = True  # 人工开关，自动 scope 识别不再改动 global
    if "name" not in meta:
        meta["name"] = skill_name
    if "sources" not in meta:
        meta["sources"] = []
    if "merged" not in meta:
        meta["merged"] = False
    if "created_at" not in meta:
        meta["created_at"] = datetime.now().isoformat()
    write_meta(central_path, meta)
    if turn_on:
        print(f"{skill_name} 已标记为全局，下次 sync 将扩散到所有 agent")
        if meta.get("scope") == "project":
            print(f"注意：该 skill 为项目 scope"
                  f"（{meta.get('project') or '?'}），"
                  f"默认仍不扩散，sync 需加 --include-project")
    else:
        print(f"{skill_name} 已取消全局标记")
        print("注意：已存在的链接不会自动移除，需要手动 unlink 或跑完整 sync")


# ============================================================
# add — 安装 skill 到中央仓库
#   - 本地 zip / 目录：直接装入中央仓库（不依赖 npx，绕过上游编码校验）
#   - 远程源（owner/repo、URL）：包装 npx skills add
# ============================================================
def _decode_text(raw):
    """尝试多种编码解码，返回第一个成功的。覆盖中文 skill 常见的 GBK/GB18030 场景。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _find_skill_root(start):
    """在 start 下定位含 SKILL.md / .skill-metadata.yaml 的目录，优先顶层。"""
    markers = ("SKILL.md", ".skill-metadata.yaml")
    if any((start / m).is_file() for m in markers):
        return start
    # 常见 zip 结构：name/SKILL.md
    for child in sorted(start.iterdir()):
        if child.is_dir() and any((child / m).is_file() for m in markers):
            return child
    # 再深一层兜底
    for child in sorted(start.iterdir()):
        if not child.is_dir():
            continue
        for grand in sorted(child.iterdir()):
            if grand.is_dir() and any((grand / m).is_file() for m in markers):
                return grand
    return None


def _parse_skill_name(skill_root):
    """从 SKILL.md frontmatter 读 name；读不到就用目录名。"""
    sm = skill_root / "SKILL.md"
    if sm.is_file():
        text = _decode_text(sm.read_bytes())
        if text.lstrip().startswith("---"):
            body = text.lstrip()
            end = body.find("---", 3)
            if end != -1:
                fm = body[3:end]
                for line in fm.splitlines():
                    m = re.match(r'^name:\s*(.+?)\s*$', line.strip())
                    if m:
                        return m.group(1).strip().strip('"\'')
    return skill_root.name


def install_local_to_central(src, is_zip):
    """把本地 zip 或目录直接装进中央仓库，返回 skill name（失败返回 None）。"""
    tmp = None
    if is_zip:
        tmp = Path(tempfile.mkdtemp(prefix="skillhome_add_"))
        try:
            with zipfile.ZipFile(src) as z:
                z.extractall(tmp)
        except zipfile.BadZipFile:
            print(f"无法解压（坏 zip）：{src}")
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        scan_root = tmp
    else:
        scan_root = src

    skill_root = _find_skill_root(scan_root)
    if not skill_root:
        print("未找到 SKILL.md / .skill-metadata.yaml，不是有效的 skill 包")
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        return None

    name = _parse_skill_name(skill_root)
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "unnamed_skill"
    dest = CENTRAL_SKILLS / safe

    if dest.exists():
        bak_dir = BACKUP_DIR
        bak_dir.mkdir(parents=True, exist_ok=True)
        bak = bak_dir / (dest.name + ".bak." + datetime.now().strftime("%Y%m%d%H%M%S"))
        shutil.move(str(dest), str(bak))
        print(f"已存在 {safe}，旧版本备份到 {bak}")

    shutil.copytree(str(skill_root), str(dest))

    # SKILL.md 非 UTF-8 时统一转码，避免下游 agent 读取乱码
    sm = dest / "SKILL.md"
    if sm.is_file():
        raw = sm.read_bytes()
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            sm.write_text(_decode_text(raw), encoding="utf-8")
            print("SKILL.md 编码已转换为 UTF-8")

    is_proj, proj = detect_project_scope(safe, dest, [str(src)])
    new_meta = {
        "name": safe,
        "sources": [str(src)],
        "merged": False,
        "global": not is_proj,
        "scope": "project" if is_proj else "global",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    if proj:
        new_meta["project"] = proj
    write_meta(dest, new_meta)

    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return safe


def cmd_add(args):
    if not args:
        print("用法: skillhome add <source> [options]")
        print("示例:")
        print("  skillhome add ./my-skill               本地目录直接装入中央仓库")
        print("  skillhome add ./pack.zip -g            本地 zip 直接装入中央仓库")
        print("  skillhome add vercel-labs/agent-skills -g   远程源走 npx skills add")
        print("  skillhome add owner/repo --skill frontend-design")
        return

    source = args[0]
    src_path = Path(source).expanduser()

    is_local_zip = src_path.is_file() and source.lower().endswith(".zip")
    is_local_dir = src_path.is_dir()

    if is_local_zip or is_local_dir:
        print(f"本地{'zip' if is_local_zip else '目录'}: {src_path}")
        name = install_local_to_central(src_path, is_zip=is_local_zip)
        if not name:
            return
        meta = read_meta(CENTRAL_SKILLS / name) or {}
        if meta.get("scope") == "project":
            proj = meta.get("project")
            print(f"已装入中央仓库: {name}（识别为项目 skill"
                  f"{f' [{proj}]' if proj else ''}，"
                  f"不扩散；--include-project 可强制）")
        else:
            print(f"已装入中央仓库: {name}（默认全局共享）")
        print("\nsync 到各 agent ...")
        cmd_sync(incremental=True)
        print("\n完成。")
        return

    # 远程源：包装 npx skills add（用 which 解析到的全路径，Windows 上是 npx.cmd）
    npx = shutil.which("npx")
    if not npx:
        print("找不到 npx，请先安装 Node.js")
        print("提示：本地 zip/目录可直接装入中央仓库，无需 npx")
        return
    print(f"运行: npx skills add {' '.join(args)}")
    result = subprocess.run([npx, "skills", "add"] + args)
    if result.returncode != 0:
        print(f"npx skills add 失败 (exit {result.returncode})")
        return
    print("\nsync 到 SkillHome 中央仓库 ...")
    cmd_sync(incremental=True)
    print("\n完成。新 skill 已纳入中央仓库，全局共享已生效。")


# ============================================================
# cloud — rclone 双向云同步（可选，方案 B）
# ============================================================
# 同步根固定为 ~/.skillhome/skills/。config.json / skillhome.log /
# backups/ 是它的兄弟节点，天然不进云端，无需排除规则。
# .skillhome.json 随 skill 同步（global 标记跨机传播）；其中 sources
# 可能含他机 agent 名，建链时 agent_dirs.get() 找不到即自动跳过，无害。
# rclone 是外部二进制：未安装 / 未配置 remote 时本地功能零影响。
# bisync 是真双向 —— pull 与 push 共用同一调用，差别只在本地 sync 的时机。
CLOUD_DEFAULT_SUBDIR = "skillhome/skills"

# 只排 OS 垃圾，不排任何 skill 内容
CLOUD_EXCLUDES = [
    ".DS_Store", "._*",
    "Thumbs.db", "desktop.ini", "~$*",
]


def _rclone_bin():
    return shutil.which("rclone")


def _rclone_install_hint():
    print("未检测到 rclone，云同步不可用。安装方法：")
    if IS_WINDOWS:
        print("  winget install rclone")
    elif platform.system() == "Darwin":
        print("  brew install rclone")
    else:
        print("  sudo apt install rclone    (发行版包可能偏旧)")
        print("  或: curl https://rclone.org/install.sh | sudo bash")
    print("安装后运行 `rclone config` 创建 remote（如 gdrive）。无浏览器的")
    print("机器可在本机 `rclone authorize \"drive\"` 取 token 贴回配置。")


def _remote_name_and_path(spec):
    """规范化用户输入为 (remote 名, 完整 rclone 路径)。

    'gdrive' / 'gdrive:'   -> ('gdrive', 'gdrive:skillhome/skills')
    'gdrive:foo/bar'       -> ('gdrive', 'gdrive:foo/bar')
    """
    spec = spec.strip().rstrip("/")
    if ":" in spec:
        name, _, sub = spec.partition(":")
        name = name.strip()
        sub = sub.strip().strip("/")
        path = f"{name}:{sub}" if sub else f"{name}:{CLOUD_DEFAULT_SUBDIR}"
    else:
        name = spec
        path = f"{spec}:{CLOUD_DEFAULT_SUBDIR}"
    return name, path


def _rclone_listremotes(rclone):
    """返回已注册 remote 名集合（如 {'gdrive:'}）；查询失败返回 None。"""
    try:
        r = subprocess.run([rclone, "listremotes"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        return set(l.strip() for l in r.stdout.splitlines() if l.strip())
    except Exception:
        return None


def _rclone_bisync_flags(rclone):
    """探测本机 rclone 的 bisync 支持哪些 flag（版本差异兜底）。"""
    try:
        r = subprocess.run([rclone, "bisync", "--help"],
                           capture_output=True, text=True, timeout=20)
        return set(re.findall(r"--([a-z0-9-]+)", r.stdout + r.stderr))
    except Exception:
        return set()


def _cloud_require_ready():
    """公共守卫。成功返回 (cfg, rclone, remote_path)，失败打印原因返回 None。"""
    rclone = _rclone_bin()
    if not rclone:
        _rclone_install_hint()
        return None
    cfg = load_config()
    if not cfg:
        print("config.json 不存在，请先运行: skillhome init")
        print("（新机器顺序: rclone config -> cloud remote set -> cloud pull -> init）")
        return None
    spec = cfg.get("cloudRemote")
    if not spec or not cfg.get("cloudEnabled", True):
        print("云同步未配置。先运行: skillhome cloud remote set <remote>")
        print("示例: skillhome cloud remote set gdrive:")
        return None
    _, remote_path = _remote_name_and_path(spec)
    return cfg, rclone, remote_path


# ============================================================
# install — 注册全局命令
# ============================================================
def _shim_path() -> Path:
    """全局命令 shim 的安装位置。"""
    if IS_WINDOWS:
        return HOME / "bin" / "skillhome.cmd"
    return HOME / ".local" / "bin" / "skillhome"


def _detect_shell_rc():
    """按 $SHELL 推断 rc 文件；fish/未知返回 None（只给提示不改文件）。"""
    shell = os.path.basename(os.environ.get("SHELL", ""))
    if shell == "zsh":
        return HOME / ".zshrc"
    if shell == "bash":
        return HOME / ".bashrc"
    if shell in ("sh", "dash", "ksh"):
        return HOME / ".profile"
    return None


def cmd_install(dry_run=False):
    """注册 skillhome 全局命令（幂等，可重复跑）。

    1) 把当前脚本物化到 ~/.skillhome/bin/skillhome.py（文档化路径）
    2) 写 ~/.local/bin/skillhome shim（Windows: %USERPROFILE%\\bin\\skillhome.cmd）
    3) shim 目录不在 PATH 时，追加 export 到 shell rc
    """
    log("=== SkillHome install ===")
    script_src = Path(__file__).resolve()
    installed = BIN_DIR / "skillhome.py"

    if script_src == installed:
        log(f"脚本已在位: {installed}")
    elif dry_run:
        log(f"[DRY] 物化脚本 {script_src} -> {installed}", "DRY")
    else:
        init_dirs()
        shutil.copy2(str(script_src), str(installed))
        log(f"脚本已物化: {installed}", "OK")

    shim = _shim_path()
    if IS_WINDOWS:
        content = f'@echo off\r\npython "{script_src}" %*\r\n'
    else:
        content = (
            "#!/bin/sh\n"
            "# SkillHome global wrapper\n"
            f'if [ -f "{script_src}" ]; then\n'
            f'  exec python3 "{script_src}" "$@"\n'
            "fi\n"
            'exec python3 "$HOME/.skillhome/bin/skillhome.py" "$@"\n'
        )

    existing_cmd = shutil.which("skillhome")
    if existing_cmd:
        try:
            other = Path(existing_cmd).resolve() != shim.resolve()
        except OSError:
            other = True
        if other:
            log(f"PATH 中已有其他 skillhome 命令: {existing_cmd}（不覆盖，仅写本工具 shim）",
                "WARN")

    if shim.exists():
        try:
            if shim.read_text(encoding="utf-8") == content:
                log(f"shim 已是最新: {shim}")
                content = None
        except OSError:
            pass
    if content is not None:
        if dry_run:
            log(f"[DRY] 写入 shim: {shim}", "DRY")
        else:
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_text(content, encoding="utf-8")
            if not IS_WINDOWS:
                shim.chmod(0o755)
            log(f"全局命令已安装: {shim}", "OK")

    # PATH：shim 目录不在 PATH 时追加到 shell rc（不去重 PATH 自身）
    if IS_WINDOWS:
        if str(shim.parent).lower() not in os.environ.get("PATH", "").lower():
            print(f"提示: 将 {shim.parent} 加入用户 PATH 后可直接使用 skillhome")
        return
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    if str(shim.parent) in path_dirs:
        log(f"{shim.parent} 已在 PATH 中")
        return
    rc = _detect_shell_rc()
    export_line = f'export PATH="{shim.parent}:$PATH"'
    if rc is None:
        print(f"提示: {shim.parent} 不在 PATH。请自行加入 shell 配置：")
        print(f"  {export_line}")
        return
    if dry_run:
        log(f"[DRY] 将追加 PATH 到 {rc}", "DRY")
        return
    try:
        existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
        if str(shim.parent) in existing:
            log(f"{rc} 已包含 {shim.parent}，跳过")
        else:
            with rc.open("a", encoding="utf-8") as f:
                f.write(f"\n# Added by skillhome install\n{export_line}\n")
            log(f"已将 {shim.parent} 写入 PATH: {rc}", "OK")
    except OSError as e:
        print(f"写入 {rc} 失败: {e}")
        print(f"请手动加入: {export_line}")
    print(f"当前会话立即生效: {export_line}")


# ============================================================
# doctor — 一键体检
# ============================================================
def _agent_dir_health(agent_dir: Path, skip_names):
    """统计 agent 目录的链接健康度。

    返回 dict: links/reals/dead/chained/external/missing。
    chained = 最终落在中央但原始目标绕经其它目录（应重指为直链）。
    external = 指向中央仓库之外的链接（可能是用户自建，只报告不动）。
    """
    h = {"links": 0, "reals": 0, "dead": 0, "chained": 0,
         "external": 0, "missing": not agent_dir.is_dir(),
         "dead_names": [], "chained_names": [], "external_names": []}
    if h["missing"]:
        return h
    try:
        children = [c for c in agent_dir.iterdir()
                    if c.name not in skip_names]
    except (PermissionError, OSError):
        return h
    for child in children:
        if not child.is_dir() and not child.is_symlink():
            continue
        if not is_link(child):
            if child.is_dir():
                h["reals"] += 1
            continue
        h["links"] += 1
        if not child.exists():
            h["dead"] += 1
            h["dead_names"].append(child.name)
            continue
        raw = _link_raw_target(child)
        try:
            resolved = Path(os.path.realpath(str(child)))
        except OSError:
            resolved = None
        if resolved is None:
            continue
        if _is_under(resolved, CENTRAL_SKILLS):
            if raw is not None and not _is_under(raw, CENTRAL_SKILLS):
                h["chained"] += 1
                h["chained_names"].append(child.name)
        else:
            h["external"] += 1
            h["external_names"].append(child.name)
    return h


def cmd_doctor(args):
    fix = "--fix" in args
    issues = []

    def check(ok, ok_msg, bad_msg):
        print(f"  [{'OK' if ok else '!!'}] {ok_msg if ok else bad_msg}")
        if not ok:
            issues.append(bad_msg)
        return ok

    print("=== SkillHome doctor ===\n")

    # -- 安装状态 --
    print("[安装]")
    installed_py = BIN_DIR / "skillhome.py"
    check(installed_py.is_file(),
          f"脚本已物化: {installed_py}",
          f"~/.skillhome/bin/ 无脚本 -> 运行 skillhome install")
    shim = _shim_path()
    on_path = shutil.which("skillhome")
    if not shim.exists():
        check(False, "", f"全局 shim 缺失: {shim} -> 运行 skillhome install")
    elif not on_path:
        check(False, "", f"shim 存在但 {shim.parent} 不在 PATH")
    else:
        check(str(Path(on_path).resolve()) == str(shim.resolve())
              or str(on_path) == str(shim),
              f"全局命令: {on_path}",
              f"PATH 中的 skillhome 指向其他位置: {on_path}")

    # -- 配置 --
    print("\n[配置]")
    cfg = load_config()
    if not check(cfg is not None,
                 f"config.json 正常（{CONFIG_PATH}）",
                 "config.json 缺失或损坏 -> 运行 skillhome init"):
        print(f"\n共发现 {len(issues)} 个问题")
        return
    agent_dirs = cfg.get("agentDirs", {})
    check(bool(agent_dirs), f"agentDirs: {len(agent_dirs)} 个",
          "agentDirs 为空 -> 运行 skillhome discover")
    skip_names = cfg.get("skipNames", DEFAULT_SKIP_NAMES)

    # -- 中央仓库 --
    print("\n[中央仓库]")
    if CENTRAL_SKILLS.is_dir():
        n = sum(1 for d in CENTRAL_SKILLS.iterdir() if d.is_dir())
        check(True, f"{CENTRAL_SKILLS} ({n} skills)", "")
    else:
        check(False, "", f"中央仓库不存在: {CENTRAL_SKILLS}")

    # -- agent 目录健康 --
    print("\n[agent 目录]")
    totals = {"dead": 0, "chained": 0, "external": 0,
              "reals": 0, "missing": 0}
    for name, dstr in agent_dirs.items():
        d = Path(dstr)
        h = _agent_dir_health(d, skip_names)
        for k in totals:
            totals[k] += h[k]
        if h["missing"]:
            check(False, "", f"{name}: 目录不存在 {d}")
        elif h["dead"] or h["chained"]:
            check(False, "",
                  f"{name}: 死链 {h['dead']} | 链式 {h['chained']}"
                  f" | 外部 {h['external']} | 残留真实 {h['reals']}"
                  f"  (例: {', '.join((h['dead_names'] + h['chained_names'])[:5])})")
        else:
            check(True,
                  f"{name}: links={h['links']} reals={h['reals']} external={h['external']}",
                  "")
    check(totals["dead"] == 0, "无死链",
          f"共 {totals['dead']} 条死链 -> skillhome sync --prune 或 doctor --fix")
    check(totals["chained"] == 0, "无链式链接",
          f"共 {totals['chained']} 条链式链接（绕经非中央路径）-> doctor --fix 重指直链")
    check(totals["reals"] == 0, "无残留真实目录",
          f"共 {totals['reals']} 个残留真实目录 -> skillhome sync")

    # -- 未登记的已知目录 --
    registered = set(agent_dirs.values())
    unreg = [p for p in KNOWN_PATTERNS
             if (HOME / p).is_dir() and str(HOME / p) not in registered]
    if unreg:
        print(f"\n[提示] {len(unreg)} 个已知目录存在但未登记（可能是空目录）:")
        for p in unreg[:8]:
            print(f"  {HOME / p}")
        print("  需要纳入扩散: skillhome discover")

    # -- 云同步 --
    print("\n[云同步]")
    rclone = _rclone_bin()
    check(rclone is not None, f"rclone: {rclone}", "rclone 未安装（云同步不可用）")
    spec = cfg.get("cloudRemote")
    if spec:
        name, rpath = _remote_name_and_path(spec)
        check(True, f"cloudRemote: {spec} -> {rpath}", "")
        if rclone:
            remotes = _rclone_listremotes(rclone)
            if remotes is not None:
                check(f"{name}:" in remotes, f"remote '{name}:' 已注册",
                      f"remote '{name}:' 未在 rclone 注册 -> rclone config")
        last = cfg.get("cloudLastSync")
        if last:
            try:
                age = datetime.now() - datetime.fromisoformat(last)
                ok = age.days < 7
                check(ok, f"上次同步: {last[:19]} ({age.days} 天前)",
                      f"上次同步: {last[:19]} ({age.days} 天前，建议 cloud sync)")
            except ValueError:
                check(True, f"上次同步: {last}", "")
        else:
            check(False, "", "cloudLastSync 从未写入（bisync 尚未成功过）")
    else:
        print("  [--] 未配置 cloudRemote（可选）: skillhome cloud remote set <name>")

    print(f"\n共发现 {len(issues)} 个问题" if issues else "\n体检通过，未发现问题")
    if issues and not fix:
        print("修复: skillhome doctor --fix（死链/链式链接/缺失 shim）")

    if fix:
        print("\n-- 修复 --")
        if not installed_py.is_file() or not shim.exists():
            cmd_install()
        removed = repointed = 0
        for name, dstr in agent_dirs.items():
            d = Path(dstr)
            if not d.is_dir():
                continue
            r, p = _prune_agent_dir(d, name, skip_names=skip_names)
            removed += r
            repointed += p
        log(f"doctor --fix: 清理死链 {removed} | 重指 {repointed}", "OK")
        if removed or repointed:
            print("已修复链接问题。建议再跑: skillhome sync")


# ============================================================
# init — 五步编排
# ============================================================
def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _confirm(prompt: str, default=True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    resp = _ask(f"{prompt} {suffix} ").lower()
    if not resp:
        return default
    return resp not in ("n", "no")


def _quick_register_known_dirs():
    """兜底：把已存在的已知 agent 目录登记进 agentDirs（不深度扫描）。

    ~/.agents/skills 与 ~/.hermes/skills 在其父目录存在但 skills/ 缺失时
    补建——这两个是 Hermes 的标准读取目录。返回新增 agent 名列表。
    """
    cfg = load_config() or _config_scaffold()
    agent_dirs = dict(cfg.get("agentDirs") or {})
    added = []
    for pattern in KNOWN_PATTERNS:
        path = HOME / pattern
        if not path.is_dir() or str(path) in agent_dirs.values():
            continue
        name = derive_agent_name(path)
        if name in agent_dirs:
            name = f"{name}-{path.parent.name}".lstrip(".")
        if name in agent_dirs:
            continue
        agent_dirs[name] = str(path)
        added.append(name)
    for base in (HOME / ".agents", HOME / ".hermes"):
        skills_dir = base / "skills"
        if base.is_dir() and not skills_dir.exists():
            try:
                skills_dir.mkdir()
                if str(skills_dir) not in agent_dirs.values():
                    agent_dirs[derive_agent_name(skills_dir)] = str(skills_dir)
                    added.append(skills_dir.parent.name.lstrip("."))
            except OSError:
                pass
    if added:
        patch_config({"agentDirs": agent_dirs})
    return added


def cmd_init(args):
    """五步编排：install -> discover -> remote picker -> first pull -> sync。"""
    dry = "--dry" in args or "--dry-run" in args
    yes = "-y" in args or "--yes" in args
    no_cloud = "--no-cloud" in args
    interactive = sys.stdin.isatty() and not yes

    print("=== SkillHome init ===" + ("  [DRY-RUN 预览]" if dry else ""))

    print("\n[1/5] 注册全局命令 ...")
    cmd_install(dry_run=dry)

    print("\n[2/5] 发现 skill 目录 ...")
    cmd_discover(interactive=interactive, merge=True, dry_run=dry)

    print("\n[3/5] 云同步 remote ...")
    cfg = load_config() or {}
    if no_cloud:
        print("  已跳过 (--no-cloud)")
    elif cfg.get("cloudRemote") and not dry:
        print(f"  已配置: {cfg['cloudRemote']}")
    else:
        rclone = _rclone_bin()
        if not rclone:
            print("  未安装 rclone，跳过云同步配置（本地功能不受影响）")
        else:
            remotes = _rclone_listremotes(rclone) or set()
            if not remotes:
                print("  rclone 无已注册 remote，跳过（先运行 rclone config）")
            elif dry:
                print(f"  [DRY] 可选 remote: {', '.join(sorted(remotes))}")
            elif len(remotes) == 1:
                pick = next(iter(remotes))
                if yes or (sys.stdin.isatty()
                           and _confirm(f"  使用 remote {pick} ?", True)):
                    patch_config({"cloudRemote": pick, "cloudEnabled": True})
                    print(f"  cloudRemote = {pick}")
                else:
                    print(f"  跳过，稍后可运行: skillhome cloud remote set {pick}")
            else:
                rl = sorted(remotes)
                print("  可用 remote:")
                for i, r in enumerate(rl):
                    print(f"    [{i + 1}] {r}")
                choice = _ask("  选择编号 (回车跳过): ")
                if choice.isdigit() and 1 <= int(choice) <= len(rl):
                    pick = rl[int(choice) - 1]
                    patch_config({"cloudRemote": pick, "cloudEnabled": True})
                    print(f"  cloudRemote = {pick}")
                else:
                    print("  跳过，稍后可运行: skillhome cloud remote set <name>")

    print("\n[4/5] 首次云端拉取 ...")
    cfg = load_config() or {}
    if no_cloud or not cfg.get("cloudRemote"):
        print("  跳过（未配置 remote）")
    elif dry:
        print("  [DRY] 将执行: skillhome cloud pull（含自动扩散）")
    elif yes:
        _cloud_run("pull", [])
    elif sys.stdin.isatty() and _confirm("  现在从云端拉取 skills? ", True):
        _cloud_run("pull", [])
    else:
        # 非交互环境不自动触发网络操作；需显式 -y
        print("  跳过，稍后可运行: skillhome cloud pull")

    print("\n[5/5] 本地收敛 + 扩散 ...")
    report = cmd_sync(dry_run=dry, incremental=True)

    print("\n=== init 完成 ===" + ("  [DRY-RUN，未做任何改动]" if dry else ""))
    if report:
        print(f"  中央 skill: 见上方 | 新建链接 {report['created']} | "
              f"残留真实目录 {report['reals_left']}")
    print("  常用命令: skillhome status | skillhome doctor | skillhome sync")
    if (load_config() or {}).get("cloudRemote"):
        print("  云同步:   skillhome cloud sync")


# ------------------------------------------------------------
# cloud 前置自动备份
# ------------------------------------------------------------
# 每次 pull/push/sync 前把 skills/（含每个 skill 的 .skillhome.json）
# 和 config.json 复制到 backups/pre-cloud-sync-<ts>/。skillhome.log 与
# backups/ 自身不进备份。保留最近 3 份，超出自动轮转；备份失败即终止同步。
CLOUD_BACKUP_PREFIX = "pre-cloud-sync-"
CLOUD_BACKUP_KEEP = 3


def _dir_size(path: Path) -> int:
    """目录/文件总字节数；不可读文件跳过。"""
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    if not path.is_dir():
        return total
    try:
        for f in path.rglob("*"):
            try:
                if f.is_file() and not f.is_symlink():
                    total += f.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _fmt_size(n) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)}B" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def _cloud_backup_sources():
    """(源, 备份内相对路径)。.skillhome.json 在 skills/<name>/ 内，随目录一起备份。"""
    items = []
    if CENTRAL_SKILLS.is_dir():
        items.append((CENTRAL_SKILLS, "skills"))
    if CONFIG_PATH.is_file():
        items.append((CONFIG_PATH, "config.json"))
    return items


def _cloud_backup_dirs():
    """全部 pre-cloud-sync 备份目录，按时间戳升序（旧 -> 新）。"""
    if not BACKUP_DIR.is_dir():
        return []
    return sorted(d for d in BACKUP_DIR.iterdir()
                  if d.is_dir() and d.name.startswith(CLOUD_BACKUP_PREFIX))


def _cloud_backup_rotate(protect=None):
    """只保留最近 CLOUD_BACKUP_KEEP 份；protect 指向的目录豁免删除。"""
    dirs = _cloud_backup_dirs()
    extra = len(dirs) - CLOUD_BACKUP_KEEP
    if extra <= 0:
        return
    for old in dirs:
        if extra <= 0:
            break
        if protect is not None and old == protect:
            continue
        log(f"[cloud] 清理旧备份: {old}")
        shutil.rmtree(str(old), ignore_errors=True)
        extra -= 1


def _cloud_backup(protect=None):
    """cloud pull/push/sync 前置备份。成功返回 Path，失败返回 None（须终止同步）。"""
    t0 = datetime.now()
    stamp = t0.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{CLOUD_BACKUP_PREFIX}{stamp}"
    n = 1
    while dest.exists():
        n += 1
        dest = BACKUP_DIR / f"{CLOUD_BACKUP_PREFIX}{stamp}-{n}"

    log(f"[cloud] 备份中... {dest}")
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        need = sum(_dir_size(src) for src, _ in _cloud_backup_sources())
        free = shutil.disk_usage(str(BACKUP_DIR)).free
        if free < need:
            log(f"[cloud] 磁盘空间不足：备份约需 {_fmt_size(need)}，"
                f"可用 {_fmt_size(free)}，已终止同步", "ERROR")
            return None
        dest.mkdir()
        for src, rel in _cloud_backup_sources():
            target = dest / rel
            if src.is_dir():
                shutil.copytree(str(src), str(target), symlinks=True)
            else:
                shutil.copy2(str(src), str(target))
    except KeyboardInterrupt:
        shutil.rmtree(str(dest), ignore_errors=True)
        log(f"[cloud] 备份中断，已清理不完整备份: {dest}", "WARN")
        return None
    except Exception as e:
        shutil.rmtree(str(dest), ignore_errors=True)
        log(f"[cloud] 备份失败，已清理不完整备份 ({e})", "ERROR")
        return None

    size = _dir_size(dest)
    elapsed = (datetime.now() - t0).total_seconds()
    log(f"[cloud] 备份完成: {dest} ({_fmt_size(size)}, {elapsed:.1f}s)", "OK")
    _cloud_backup_rotate(protect=protect)
    return dest


def _cloud_backups():
    dirs = _cloud_backup_dirs()
    if not dirs:
        print("没有可用备份")
        print("备份在每次 cloud pull/push/sync 前自动创建，保留最近 "
              f"{CLOUD_BACKUP_KEEP} 份。")
        return
    print(f"可用备份（{BACKUP_DIR}）:")
    for d in reversed(dirs):  # 新 -> 旧
        size = _dir_size(d)
        try:
            nfiles = sum(1 for f in d.rglob("*") if f.is_file())
        except OSError:
            nfiles = 0
        print(f"  {d.name}   {_fmt_size(size):>10}   {nfiles} 文件")
    print("\n恢复: skillhome cloud restore <backup-name>")


def _cloud_restore(name):
    if not name:
        print("用法: skillhome cloud restore <backup-name>")
        _cloud_backups()
        return
    # 只接受 backups/ 下的备份目录名，防止路径穿越
    if name.startswith("/") or ".." in name or "/" in name or "\\" in name:
        print(f"非法备份名: {name}")
        return
    src = BACKUP_DIR / name
    if not name.startswith(CLOUD_BACKUP_PREFIX) or not src.is_dir():
        print(f"备份不存在: {name}")
        _cloud_backups()
        return

    # 恢复前先给当前状态做一次安全备份（protect 防止轮转误删 src）
    log("[cloud] 恢复前备份当前状态...")
    if _cloud_backup(protect=src) is None:
        log("[cloud] 当前状态备份失败，已终止恢复", "ERROR")
        return

    log(f"[cloud] 恢复备份: {src}")
    bk_skills = src / "skills"
    if bk_skills.is_dir():
        staging = HOME_ROOT / (".restore-staging-"
                               + datetime.now().strftime("%Y%m%d%H%M%S"))
        if CENTRAL_SKILLS.exists():
            shutil.move(str(CENTRAL_SKILLS), str(staging))
        try:
            shutil.copytree(str(bk_skills), str(CENTRAL_SKILLS), symlinks=True)
        except Exception as e:
            shutil.rmtree(str(CENTRAL_SKILLS), ignore_errors=True)
            if staging.exists():
                shutil.move(str(staging), str(CENTRAL_SKILLS))
            log(f"[cloud] skills/ 恢复失败，已回滚原目录 ({e})", "ERROR")
            return
        shutil.rmtree(str(staging), ignore_errors=True)
    else:
        log("[cloud] 备份中无 skills/，跳过", "WARN")

    bk_cfg = src / "config.json"
    if bk_cfg.is_file():
        try:
            shutil.copy2(str(bk_cfg), str(CONFIG_PATH))
        except OSError as e:
            log(f"[cloud] config.json 恢复失败: {e}", "ERROR")

    log(f"[cloud] 恢复完成: {name} -> {HOME_ROOT}", "OK")
    cfg = load_config()
    if cfg and cfg.get("agentDirs"):
        log("[cloud] 恢复后自动扩散 ...")
        cmd_sync(incremental=True)
    else:
        print("建议运行 `skillhome init` 发现 agent 目录后扩散。")


def _cloud_snapshot():
    """中央仓库文件指纹 relpath -> sha256，用于统计同步引起的本地变化。"""
    snap = {}
    if not CENTRAL_SKILLS.exists():
        return snap
    for f in CENTRAL_SKILLS.rglob("*"):
        if not f.is_file():
            continue
        try:
            rel = str(f.relative_to(CENTRAL_SKILLS)).replace("\\", "/")
            snap[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        except (PermissionError, OSError):
            continue
    return snap


def _cloud_report(before, dry):
    """对比快照，输出 新增/更新/删除/冲突 统计。"""
    after = _cloud_snapshot()
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after)
                     if before[k] != after[k])
    conflicts = [k for k in added if "conflict" in Path(k).name.lower()]
    prefix = "[DRY] " if dry else ""
    print(f"\n=== {prefix}同步结果（本地侧变化）===")
    print(f"  新增 {len(added)} | 更新 {len(changed)} | "
          f"删除 {len(removed)} | 冲突 {len(conflicts)}")
    for c in conflicts[:10]:
        print(f"  [conflict] {c}")
    if len(conflicts) > 10:
        print(f"  ... 另有 {len(conflicts) - 10} 个冲突文件")
    if conflicts:
        print("  冲突方已保留为 *.conflictN，请手动取舍后重新 push。")


def _run_bisync(rclone, remote_path, resync=False, dry=False, verbose=False):
    """执行 rclone bisync，流式透传输出。返回 (ok, captured_lines)。"""
    supported = _rclone_bisync_flags(rclone)

    def opt(name, *vals):
        return [f"--{name}"] + [str(v) for v in vals] if name in supported else []

    cmd = [rclone, "bisync", str(CENTRAL_SKILLS), remote_path]
    cmd += opt("resilient") + opt("recover")
    cmd += opt("conflict-resolve", "newer")   # 胜者保留原名，败者 -> *.conflictN
    cmd += opt("conflict-loser", "num")
    cmd += opt("create-empty-src-dirs")
    cmd += ["--stats-one-line", "--stats", "10s"]
    for pat in CLOUD_EXCLUDES:
        cmd += opt("exclude", pat)
    if resync:
        # newer：首次基线/恢复时让较新版本获胜，避免陈旧本地覆盖云端
        cmd += opt("resync") + opt("resync-mode", "newer")
    if dry:
        cmd += opt("dry-run")
    if verbose:
        cmd += ["-v"]

    log(f"rclone bisync {CENTRAL_SKILLS} <-> {remote_path}")
    captured = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True, errors="replace")
    except OSError as e:
        log(f"无法启动 rclone: {e}", "ERROR")
        return False, captured
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            captured.append(line)
            print(f"  {line}")
            _log_file_only(line, "RCLONE")
        proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        print("\n已中断。--resilient 状态下重跑即可恢复。")
        return False, captured
    ok = proc.returncode == 0
    _log_file_only(f"bisync exit={proc.returncode} ok={ok}", "RCLONE")
    return ok, captured


def _cloud_run(mode, args):
    """pull: bisync -> 本地 sync；push: 本地 sync -> bisync；sync: 两者。

    pull/sync 成功后默认自动扩散到各 agent 目录（--no-fanout 关闭）；
    --dry 时扩散也以 dry-run 预览，不做任何改动。
    """
    resync = "--resync" in args
    dry = "--dry" in args or "--dry-run" in args
    verbose = "--verbose" in args or "-v" in args
    no_fanout = "--no-fanout" in args

    ready = _cloud_require_ready()
    if not ready:
        return
    cfg, rclone, remote_path = ready

    name = _remote_name_and_path(cfg["cloudRemote"])[0]
    remotes = _rclone_listremotes(rclone)
    if remotes is not None and f"{name}:" not in remotes:
        print(f"rclone 中未注册 remote '{name}:'")
        print("已配置 remote: " + (", ".join(sorted(remotes)) or "(无)"))
        print("先运行 `rclone config` 创建，或 `cloud remote set` 改用已有 remote。")
        return

    init_dirs()

    # 同步前自动备份；备份失败即终止，不允许无备份同步
    if dry:
        log("[cloud] dry-run 模式，跳过备份")
    elif _cloud_backup() is None:
        return

    if not cfg.get("cloudLastSync") and not resync:
        log("首次同步，自动建立基线 (--resync --resync-mode newer)")
        resync = True

    if mode in ("push", "sync"):
        if cfg.get("agentDirs"):
            log(f"先收敛本地: skillhome sync (incremental{', dry' if dry else ''})")
            cmd_sync(dry_run=dry, incremental=True)

    before = _cloud_snapshot()
    ok, captured = _run_bisync(rclone, remote_path,
                               resync=resync, dry=dry, verbose=verbose)
    if not ok:
        log("rclone bisync 失败，本地链接未刷新（rclone 输出见 skillhome.log）",
            "ERROR")
        print("\n修复指引（按情况三选一）:")
        print("  1) 直接重跑同一命令（--resilient 可续传）")
        print("  2) 若要求重建基线: 重跑并显式加 --resync")
        print("  3) 若本地已损坏: skillhome cloud backups 查看备份，"
              "cloud restore <name> 恢复")
        return

    if not dry:
        patch_config({"cloudLastSync": datetime.now().isoformat()})
    _cloud_report(before, dry)

    # 自动扩散：pull/sync 成功后把中央 skills 链接到各 agent 目录
    if mode in ("pull", "sync"):
        if no_fanout:
            log("[cloud] 跳过自动扩散 (--no-fanout)")
        else:
            if not cfg.get("agentDirs"):
                # 兜底：快速登记已知目录（含 ~/.agents/skills、~/.hermes/skills）
                added = _quick_register_known_dirs()
                if added:
                    log(f"[cloud] 兜底登记 {len(added)} 个 agent 目录: "
                        f"{', '.join(added)}")
                    cfg = load_config() or cfg
            if cfg.get("agentDirs"):
                log(f"[cloud] 自动扩散到 agent 目录"
                    f"{'（dry 预览）' if dry else ''} ...")
                report = cmd_sync(dry_run=dry, incremental=True)
                if report:
                    log("[cloud] 扩散完成: "
                        f"新建链接 {report['created']} | "
                        f"已有 {report['skipped']} | "
                        f"冲突 {report['conflicts']} | "
                        f"残留真实 {report['reals_left']}", "OK")
            else:
                print("\n云端 skills 已落位中央仓库，但本机尚未发现 agent 目录。")
                print("运行 `skillhome init` 完成建链。")


def _cloud_remote(rest):
    if not rest:
        cfg = load_config() or {}
        spec = cfg.get("cloudRemote")
        if spec:
            _, path = _remote_name_and_path(spec)
            print(f"cloudRemote:  {spec}  ->  {path}")
            print(f"cloudEnabled: {cfg.get('cloudEnabled', True)}")
            print(f"上次同步:     {cfg.get('cloudLastSync') or '从未'}")
        else:
            print("未配置 cloudRemote")
        print("\n用法: skillhome cloud remote set <remote> | unset")
        print("  set gdrive:        同步到 gdrive:skillhome/skills")
        print("  set gdrive:mydir   同步到 gdrive:mydir")
        return

    action = rest[0].lower()
    if action == "set":
        if len(rest) < 2 or not rest[1].strip():
            print("用法: skillhome cloud remote set <remote>")
            return
        spec = rest[1].strip()
        name, path = _remote_name_and_path(spec)
        patch_config({"cloudRemote": spec, "cloudEnabled": True})
        print(f"cloudRemote = {spec}  ->  同步路径 {path}")
        rclone = _rclone_bin()
        if rclone:
            remotes = _rclone_listremotes(rclone)
            if remotes is not None and f"{name}:" not in remotes:
                print(f"注意: rclone 尚未注册 '{name}:'，先 `rclone config` 创建")
        else:
            _rclone_install_hint()
        print("下一步: skillhome cloud pull")
    elif action == "unset":
        patch_config({"cloudEnabled": False, "cloudLastSync": None},
                     deletes=("cloudRemote",))
        print("已清除 cloudRemote，云同步停用（本地功能不受影响）")
    else:
        print("用法: skillhome cloud remote [set <remote> | unset]")


def _cloud_status():
    print("SkillHome 云同步状态")
    rclone = _rclone_bin()
    if rclone:
        ver = ""
        try:
            r = subprocess.run([rclone, "version"],
                               capture_output=True, text=True, timeout=15)
            if r.stdout:
                ver = r.stdout.splitlines()[0].strip()
        except Exception:
            pass
        print(f"  rclone:       {rclone}  ({ver})" if ver else f"  rclone:       {rclone}")
    else:
        print("  rclone:       未安装")
        _rclone_install_hint()

    cfg = load_config() or {}
    spec = cfg.get("cloudRemote")
    print(f"  cloudRemote:  {spec or '(未配置)'}")
    if spec:
        name, path = _remote_name_and_path(spec)
        print(f"  同步路径:     {path}")
        print(f"  cloudEnabled: {cfg.get('cloudEnabled', True)}")
        print(f"  上次同步:     {cfg.get('cloudLastSync') or '从未（首次自动 --resync）'}")
        if rclone:
            remotes = _rclone_listremotes(rclone)
            if remotes is None:
                print("  remote 注册:  无法查询（rclone listremotes 失败）")
            elif f"{name}:" in remotes:
                print(f"  remote 注册:  {name}: OK")
            else:
                print(f"  remote 注册:  {name}: 未注册！已有: "
                      + (", ".join(sorted(remotes)) or "(无)"))
                print("               运行 `rclone config` 创建该 remote")

    n_conflicts = 0
    if CENTRAL_SKILLS.exists():
        try:
            n_conflicts = sum(
                1 for f in CENTRAL_SKILLS.rglob("*")
                if f.is_file() and "conflict" in f.name.lower())
        except (PermissionError, OSError):
            pass
    print(f"  本地冲突文件: {n_conflicts}")
    print(f"  中央仓库:     {CENTRAL_SKILLS}")
    if not spec:
        print("\n启用: skillhome cloud remote set <remote>")


def _cloud_help():
    print("""
  skillhome cloud status                     云同步状态（rclone / remote / 上次同步）
  skillhome cloud remote                     查看当前 remote
  skillhome cloud remote set <name>          设置 remote（gdrive: -> gdrive:skillhome/skills）
  skillhome cloud remote unset               清除配置并停用
  skillhome cloud pull [--resync] [--dry] [-v] [--no-fanout]   云端 -> 本地（bisync 后自动扩散）
  skillhome cloud push [--resync] [--dry] [-v]   本地 -> 云端（先收敛本地再 bisync）
  skillhome cloud sync [--resync] [--dry] [-v] [--no-fanout]   双向：收敛 -> bisync -> 扩散
  skillhome cloud backups                      列出本地备份（保留最近 3 份）
  skillhome cloud restore <backup-name>        从指定备份恢复 skills/ + config.json

  只同步 ~/.skillhome/skills/；config.json / skillhome.log / backups/ 不上云。
  冲突文件保留为 *.conflictN。首次同步自动 --resync 建立基线。
  pull/push/sync 前自动备份到 ~/.skillhome/backups/pre-cloud-sync-<ts>/，
  备份失败则终止同步；--dry 不改动数据，跳过备份。
""")


def cmd_cloud(args):
    sub = args[0].lower() if args else "help"
    if sub in ("help", "-h", "--help"):
        _cloud_help()
    elif sub == "status":
        _cloud_status()
    elif sub == "remote":
        _cloud_remote(args[1:])
    elif sub in ("pull", "push", "sync"):
        _cloud_run(sub, args[1:])
    elif sub == "backups":
        _cloud_backups()
    elif sub == "restore":
        _cloud_restore(args[1] if len(args) > 1 else None)
    else:
        print(f"未知 cloud 子命令: {sub}")
        _cloud_help()
        sys.exit(1)


# ============================================================
# config
# ============================================================
def cmd_config():
    if not CONFIG_PATH.exists():
        print("config.json 不存在，请先运行: skillhome init")
        return
    print(f"配置文件: {CONFIG_PATH}")
    print()
    raw = load_config()
    print(f"  userProfile: {raw.get('userProfile')}")
    print(f"  centralSkills: {raw.get('centralSkills')}")
    print(f"  similarityThreshold: {raw.get('similarityThreshold')}")
    print(f"  skipNames: {', '.join(raw.get('skipNames', []))}")
    rules = _scope_rules(raw)
    tag = "scopeRules" if isinstance(raw.get("scopeRules"), dict) \
        else "scopeRules (默认)"
    print(f"  {tag}:")
    for proj, pats in rules.items():
        pats = pats if isinstance(pats, list) else [pats]
        print(f"    {proj}: {', '.join(str(p) for p in pats)}")
    spec = raw.get("cloudRemote")
    print(f"  cloudRemote: {spec or '(未配置)'}")
    if spec:
        print(f"  cloudEnabled: {raw.get('cloudEnabled', True)}")
        print(f"  cloudLastSync: {raw.get('cloudLastSync') or '从未'}")
    print("  agentDirs:")
    for name, path in raw.get("agentDirs", {}).items():
        print(f"    {name}: {path}")


# ============================================================
# help
# ============================================================
def cmd_help():
    print("""
  SkillHome — 跨 Agent 统一 Skill 管理（Python 单文件，三平台）

  命令:
    skillhome init              首次初始化五步编排：install → discover → remote → pull → sync
                                (-y 全自动 | --no-cloud 跳过云端 | --dry-run 预览)
    skillhome install           注册全局命令：物化脚本 + 写 ~/.local/bin/skillhome + 配 PATH
    skillhome discover          重新扫描发现 skill 目录（默认 merge；--replace 覆盖；-i 逐条确认）
    skillhome sync              手动触发增量同步（--dry-run 预览 | --prune 清理死链）
    skillhome sync --full       完整同步（重建所有链接）
    skillhome sync --include-project   同步时把项目 skill 也扩散到所有 agent
    skillhome doctor            体检：死链/链式链接/bin 目录/remote/agent 目录健康（--fix 修复）
    skillhome status            查看当前状态（含活跃项目）
    skillhome use <project>     激活项目上下文：链接该项目 skills，清理其它项目链接
    skillhome use               不带参数时按当前目录路径推断项目
    skillhome use --none        退出项目上下文，仅保留全局 skills（--dry-run 预览）
    skillhome context           显示活跃项目与已激活 skills（--ensure 补齐缺失链接）
    skillhome list              列出所有 skill 及其分布
                                （--scope all|global|project 过滤；--project <name> 按项目过滤）
    skillhome search <关键词>    按名称/description 检索 skill（支持同样的 --scope/--project）
    skillhome link <skill> <agent>    把 skill 链接到 agent 目录
    skillhome unlink <skill> <agent>  从 agent 目录移除链接
    skillhome global <skill> [on|off] 设置/取消全局共享
    skillhome add <source> [options]  安装 skill：本地 zip/目录直接入中央仓库，远程源走 npx
    skillhome sync --cloud      本地同步后接云端双向同步（需先配置 remote）
    skillhome cloud status      云同步状态（rclone / remote / 上次同步）
    skillhome cloud remote set|unset <name>  设置/清除 rclone remote
    skillhome cloud pull|push|sync  云端双向同步（bisync 后自动扩散；--no-fanout 关闭）
    skillhome cloud backups     列出同步前自动备份（保留最近 3 份）
    skillhome cloud restore <name>  从指定备份恢复 skills/ 与 config.json（恢复后自动扩散）
    skillhome cloud help        云同步详细用法
    skillhome config            查看当前配置
    skillhome help              显示此帮助

  通用选项:
    -v, --verbose   DEBUG 级别输出    -q, --quiet   只输出 WARN/ERROR
    --dry-run       预览不改动        --prune       清理死链与链式链接
    --source <s>    通知分流（assistant|majordomo，默认 assistant）
    --no-notify     禁用完成通知（默认开启，报告写入 ~/.skillhome/notifications/）

  项目 skill（scope=project）:
    只保留在中央仓库，sync 时不扩散。识别依据：
      - SKILL.md frontmatter: scope: project 或 project: <name>
        （scope: global 显式关闭识别）
      - config.json 的 scopeRules: {"项目名": ["模式-*"]}（fnmatch）
      - 来源路径目录段命中项目名（如 .../duly/.agents/skills/x）
    覆盖: .skillhome.json 写 "scope_manual": true + "scope": "global|project"

  项目上下文（~/.skillhome/state.json，本机状态，不随 cloud sync）:
    use 记录 activeProject + activatedSkills；sync 自动维持该上下文的链接，
    agent 会话启动时可用 `skillhome context --ensure` 自愈缺失链接。

  中央仓库: %s
  配置文件: %s

  首次使用:
    1. skillhome init     (一条命令完成装机：注册命令+发现目录+云端+扩散)
    2. skillhome status   (检查状态)
    3. skillhome doctor   (体检)

  平台:
    Windows: NTFS junction（不需要管理员权限）
    Linux/macOS: symlink
    依赖: Python 3.8+
""" % (CENTRAL_SKILLS, CONFIG_PATH))


# ============================================================
# notify — 任务完成通知（双 Hermes 分流）
# ============================================================
# 两个 Hermes 实例：assistant（助手 Vela）与 majordomo（大管家 Vela）。
# 每个通知报告带 source 字段，飞书消息只发给对应负责人。
# 分流规则：优先 FEISHU_<SOURCE>_CHANNEL / FEISHU_<SOURCE>_APP_ID /
# FEISHU_<SOURCE>_APP_SECRET（.env 或进程环境变量），缺省回退到共享的
# FEISHU_HOME_CHANNEL / FEISHU_APP_ID / FEISHU_APP_SECRET。
# 无凭证时静默跳过发送（JSON 报告照写）；发送失败只记 WARN，绝不阻塞主任务。
NOTIFY_SOURCES = ("assistant", "majordomo")
_HERMES_ENV_CACHE = None


def _hermes_env():
    """惰性读取 ~/.hermes/.env 为 dict（进程环境变量优先，见 _notify_env）。"""
    global _HERMES_ENV_CACHE
    if _HERMES_ENV_CACHE is not None:
        return _HERMES_ENV_CACHE
    env = {}
    try:
        for line in HERMES_ENV_PATH.read_text(
                encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                env[k] = v
    except OSError:
        pass
    _HERMES_ENV_CACHE = env
    return env


def _notify_env(key):
    """进程环境变量优先，其次 ~/.hermes/.env。"""
    return os.environ.get(key) or _hermes_env().get(key)


def _feishu_api_base(domain):
    """FEISHU_DOMAIN -> API base。feishu/lark 为别名，其余按主机名/URL 处理。"""
    d = (domain or "feishu").strip()
    low = d.lower()
    if low in ("feishu", "open.feishu.cn"):
        return "https://open.feishu.cn"
    if low in ("lark", "open.larksuite.com"):
        return "https://open.larksuite.com"
    if low.startswith("http://") or low.startswith("https://"):
        return d.rstrip("/")
    return f"https://{d}"


def _feishu_send(source, text):
    """发飞书文本消息给 source 对应的负责人。

    返回 True 发送成功；None 无凭证（静默跳过）；False 发送失败（已记 WARN）。
    """
    prefix = f"FEISHU_{source.upper()}_"
    app_id = _notify_env(prefix + "APP_ID") or _notify_env("FEISHU_APP_ID")
    app_secret = (_notify_env(prefix + "APP_SECRET")
                  or _notify_env("FEISHU_APP_SECRET"))
    chat_id = (_notify_env(prefix + "CHANNEL")
               or _notify_env("FEISHU_HOME_CHANNEL"))
    if not (app_id and app_secret and chat_id):
        return None
    base = _feishu_api_base(_notify_env("FEISHU_DOMAIN"))
    try:
        req = urllib.request.Request(
            f"{base}/open-apis/auth/v3/tenant_access_token/internal",
            data=json.dumps(
                {"app_id": app_id, "app_secret": app_secret}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            tok = json.loads(r.read().decode())
        token = tok.get("tenant_access_token")
        if not token:
            log(f"[notify] 飞书 token 获取失败: {tok.get('msg')}", "WARN")
            return False
        body = {"receive_id": chat_id, "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False)}
        req = urllib.request.Request(
            f"{base}/open-apis/im/v1/messages?receive_id_type=chat_id",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
        if resp.get("code") == 0:
            return True
        log(f"[notify] 飞书发送失败: {resp.get('msg')}", "WARN")
        return False
    except Exception as e:
        log(f"[notify] 飞书发送异常: {e}", "WARN")
        return False


def notify(source, task_type, status, result=None):
    """任务完成通知：写 JSON 报告到 ~/.skillhome/notifications/ 并按
    source 分流发飞书。任何内部失败只记 WARN，绝不影响主任务。"""
    try:
        if source not in NOTIFY_SOURCES:
            source = "assistant"
        result = result or {}
        NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%d-%H%M%S")

        report = {
            "source": source,
            "task_type": task_type,
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": status,
            "task": result.get("task", f"skillhome {task_type}"),
            "duration_seconds": result.get("duration_seconds"),
        }
        if status == "success":
            report["result"] = result.get("result", {})
        else:
            report["error"] = result.get("error", "unknown error")

        fname = f"{source}-{task_type}-{stamp}.json"
        fpath = NOTIFY_DIR / fname
        n = 1
        while fpath.exists():
            n += 1
            fpath = NOTIFY_DIR / f"{source}-{task_type}-{stamp}-{n}.json"

        if status == "success":
            summary = report["result"].get("summary", "done")
            text = (f"✅ SkillHome 任务完成\n"
                    f"任务：{task_type}\n"
                    f"负责人：{source}\n"
                    f"耗时：{report['duration_seconds']}s\n"
                    f"结果：{summary}")
        else:
            text = (f"❌ SkillHome 任务失败\n"
                    f"任务：{task_type}\n"
                    f"负责人：{source}\n"
                    f"错误：{report['error']}")

        sent = _feishu_send(source, text)
        report["feishu_sent"] = bool(sent)
        fpath.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                         encoding="utf-8")
        log(f"[notify] 报告已写入 {fpath.name} "
            f"(feishu_sent={report['feishu_sent']})", "DEBUG")
        return fpath
    except Exception as e:
        log(f"[notify] 通知模块异常（已忽略）: {e}", "WARN")
        return None


def _parse_notify_flags(args):
    """提取 --source/--no-notify。返回 (source, enabled, 剩余 args)。

    --source 支持 `--source majordomo` 与 `--source=majordomo` 两种写法；
    非法值回退 assistant 并记 WARN。
    """
    source = "assistant"
    enabled = True
    rest = []
    skip = False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if a == "--source":
            if i + 1 < len(args):
                source = args[i + 1].strip().lower()
                skip = True
            continue
        if a.startswith("--source="):
            source = a.split("=", 1)[1].strip().lower()
            continue
        if a == "--no-notify":
            enabled = False
            continue
        rest.append(a)
    if source not in NOTIFY_SOURCES:
        if source != "assistant":
            log(f"[notify] 未知 source '{source}'，回退为 assistant", "WARN")
        source = "assistant"
    return source, enabled, rest


def _notify_task_type(cmd, args):
    """该命令是否产生完成通知；返回 task_type 或 None。"""
    if cmd in ("init", "doctor", "sync"):
        return cmd
    if cmd == "cloud" and args and args[0].lower() in ("pull", "push", "sync"):
        return f"cloud-{args[0].lower()}"
    return None


# ============================================================
# 主分发
# ============================================================
def main():
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(0)

    # 全局日志级别：-v/--verbose 开 DEBUG，-q/--quiet 只留 WARN+
    global LOG_LEVEL
    argv = sys.argv[1:]
    if "--verbose" in argv or "-v" in argv:
        LOG_LEVEL = "DEBUG"
    elif "--quiet" in argv or "-q" in argv:
        LOG_LEVEL = "WARN"

    cmd = argv[0].lower()
    source, notify_on, args = _parse_notify_flags(argv[1:])
    task_type = _notify_task_type(cmd, args)
    t0 = time.monotonic()
    cmd_result = None

    try:
        if cmd == "init":
            cmd_init(args)
        elif cmd == "discover":
            cmd_discover(
                interactive=("-i" in args or "--interactive" in args),
                merge=("--replace" not in args),
                dry_run=("--dry" in args or "--dry-run" in args),
            )
        elif cmd == "install":
            cmd_install(dry_run=("--dry" in args or "--dry-run" in args))
        elif cmd == "doctor":
            cmd_doctor(args)
        elif cmd == "sync":
            dry_run = "--dry" in args or "--dry-run" in args
            incremental = "--full" not in args
            verbose = "--verbose" in args or "-v" in args
            prune = "--prune" in args
            include_project = "--include-project" in args
            cmd_result = cmd_sync(dry_run=dry_run, incremental=incremental,
                                  verbose=verbose, prune=prune,
                                  include_project=include_project)
            if "--cloud" in args:
                cfg = load_config()
                if (cfg and cfg.get("cloudRemote")
                        and cfg.get("cloudEnabled", True)):
                    _cloud_run("sync", args)
                else:
                    print("未配置云同步，已跳过云端步骤")
                    print("启用: skillhome cloud remote set <remote>")
        elif cmd == "status":
            cmd_status()
        elif cmd == "use":
            cmd_use(args)
        elif cmd == "context":
            cmd_context(args)
        elif cmd == "list":
            cmd_list(args)
        elif cmd == "search":
            cmd_search(args)
        elif cmd == "link":
            if len(args) >= 2:
                cmd_link(args[0], args[1])
            else:
                cmd_link(None, None)
        elif cmd == "unlink":
            if len(args) >= 2:
                cmd_unlink(args[0], args[1])
            else:
                cmd_unlink(None, None)
        elif cmd == "global":
            skill = args[0] if len(args) >= 1 else None
            action = args[1] if len(args) >= 2 else None
            cmd_global(skill, action)
        elif cmd == "add":
            cmd_add(args)
        elif cmd == "cloud":
            cmd_cloud(args)
        elif cmd == "config":
            cmd_config()
        elif cmd == "help":
            cmd_help()
        else:
            print(f"未知命令: {cmd}")
            cmd_help()
            sys.exit(1)
    except Exception as e:
        if task_type and notify_on:
            notify(source, task_type, "failed", {
                "task": f"skillhome {task_type}",
                "duration_seconds": round(time.monotonic() - t0, 1),
                "error": str(e),
            })
        raise

    if task_type and notify_on:
        result = {"summary": f"skillhome {task_type} 完成"}
        if isinstance(cmd_result, dict):
            result = {
                "summary": (f"迁移 {cmd_result.get('migrated', 0)} | "
                            f"新建链接 {cmd_result.get('created', 0)} | "
                            f"冲突 {cmd_result.get('conflicts', 0)} | "
                            f"残留真实 {cmd_result.get('reals_left', 0)}"),
                "report": cmd_result,
            }
        notify(source, task_type, "success", {
            "task": f"skillhome {task_type}",
            "duration_seconds": round(time.monotonic() - t0, 1),
            "result": result,
        })


if __name__ == "__main__":
    main()
