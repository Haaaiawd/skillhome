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
import hashlib
import platform
import zipfile
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime

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

DEFAULT_SKIP_NAMES = [".system", ".git", ".temp", "_shared"]
DEFAULT_SIMILARITY_THRESHOLD = 0.95

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
        ".hermes/skills", ".hermes/capabilities",
        ".opencloud/skills", ".opencloud/agent-skills",
        ".opencloud/learned",
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
def log(msg, level="INFO"):
    color = {
        "WARN": "\033[33m", "ERROR": "\033[31m", "OK": "\033[32m",
        "DRY": "\033[36m", "INFO": "\033[90m", "DEBUG": "\033[90m",
    }.get(level, "\033[90m")
    reset = "\033[0m"
    line = f"[{datetime.now().strftime('%H:%M:%S')}][{level}] {msg}"
    print(f"{color}{line}{reset}")
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


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


def save_config(agent_dirs, skip_names=None, similarity_threshold=None):
    cfg = {
        "version": "1.0",
        "createdAt": datetime.now().isoformat(),
        "userProfile": str(HOME),
        "centralSkills": str(CENTRAL_SKILLS),
        "agentDirs": agent_dirs,
        "skipNames": skip_names or DEFAULT_SKIP_NAMES,
        "similarityThreshold": similarity_threshold or DEFAULT_SIMILARITY_THRESHOLD,
    }
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
        if p in ("capabilities", "agents", "learned"):
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
def cmd_discover(force=False, interactive=False):
    log("=== SkillHome 自检索 ===")
    init_dirs()
    found = {}  # ordered: 用 dict 保持插入顺序 (Python 3.7+)

    # 阶段 1：快速探测已知模式
    print("  [1/2] 探测已知路径模式...")

    for pattern in KNOWN_PATTERNS:
        path = HOME / pattern
        if is_skill_repo(path):
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
        return

    # 交互模式
    final = {}
    if interactive:
        print("\n确认纳入的目录:")
        for k, v in found.items():
            resp = input(f"  纳入 {k} => {v}? [Y/n] ").strip()
            if resp.lower() != "n":
                final[k] = v
    else:
        final = found

    if not final:
        print("未选择任何目录")
        return

    # 保留已有配置的 skipNames 和 threshold
    existing = None
    if not force and CONFIG_PATH.exists():
        existing = load_config()

    skip_names = existing.get("skipNames") if existing else DEFAULT_SKIP_NAMES
    threshold = existing.get("similarityThreshold") if existing else DEFAULT_SIMILARITY_THRESHOLD

    save_config(final, skip_names, threshold)
    print(f"\n=== 完成 ===")
    print(f"发现 {len(final)} 个 skill 目录，已写入 config.json")
    print(f"  {CONFIG_PATH}")
    print(f"\n下一步: skillhome sync")


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


# ============================================================
# sync — 核心同步逻辑
# ============================================================
def cmd_sync(dry_run=False, incremental=True, verbose=False):
    cfg = load_config()
    if not cfg:
        print("config.json 不存在，请先运行: skillhome init")
        return

    agent_dirs = cfg.get("agentDirs", {})
    skip_names = cfg.get("skipNames", DEFAULT_SKIP_NAMES)
    threshold = cfg.get("similarityThreshold", DEFAULT_SIMILARITY_THRESHOLD)

    log(f"=== SkillHome 同步开始 (mode: {'incremental' if incremental else 'full'}) ===")

    # 阶段 1：扫描
    skill_registry = {}  # name -> {real_locations, link_locations, real_paths}
    central_distribution = {}

    if CENTRAL_SKILLS.exists():
        for cs in CENTRAL_SKILLS.iterdir():
            if not cs.is_dir():
                continue
            meta = read_meta(cs)
            if meta and meta.get("sources"):
                central_distribution[cs.name] = list(meta["sources"])

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
            if not dry_run:
                shutil.move(str(src_path), str(central_existing))
            central_path_of[sname] = central_existing
            skill_distribution[sname] = all_agents

        elif len(real_agents) >= 1 and central_exists:
            central_hashes = get_dir_hashes(central_existing)
            central_mtime = get_skill_mtime(central_existing)

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
                    else:
                        log(f"{sname} : 保留中央版本，删除 {src_agent} 副本 (较旧)")
                        if not dry_run:
                            shutil.rmtree(src_path, ignore_errors=True)
                else:
                    suffixed = f"{sname}--{src_agent}"
                    suffixed_central = CENTRAL_SKILLS / suffixed
                    log(f"{sname} : 相似度不足，保留为 {suffixed}")
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
                    if not dry_run:
                        shutil.move(str(other_path), str(suffixed_central))
                    central_path_of[suffixed] = suffixed_central
                    skill_distribution[suffixed] = [other]
                    if other in all_agents:
                        all_agents.remove(other)

            merged_path = Path(info["real_paths"][merged_agent])
            log(f"{sname} : 合并完成，主来源 {merged_agent}")
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

    if incremental:
        for sname in sorted(skill_distribution.keys()):
            cpath = central_path_of.get(sname)
            if not cpath or not cpath.exists():
                continue
            meta = read_meta(cpath)
            is_global = meta and meta.get("global", False)

            target_agents = skill_distribution[sname]
            if is_global:
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
                    if not dry_run:
                        create_link(link_path, cpath)
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
            is_global = meta and meta.get("global", False)

            target_agents = skill_distribution[sname]
            if is_global:
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
        default_global = "--" not in sname

        if not existing:
            if not dry_run:
                write_meta(cpath, {
                    "name": sname,
                    "sources": desired_sources,
                    "merged": False,
                    "global": default_global,
                    "created_at": datetime.now().isoformat(),
                })
                if default_global:
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
            if needs_update and not dry_run:
                write_meta(cpath, existing)

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
    log(f"=== 完成 | 中央 skill: {len(skill_distribution)} | 残留真实目录: {real_count} ===", "OK")


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
    print()
    print(f"  {'Agent':<25} {'Links':>8} {'Reals':>8}  Status")
    print(f"  {'-'*25} {'-'*8} {'-'*8}  {'-'*12}")
    for name, links, reals, status in rows:
        print(f"  {name:<25} {links:>8} {reals:>8}  {status}")


# ============================================================
# list
# ============================================================
def cmd_list():
    if not CENTRAL_SKILLS.exists():
        print("中央仓库不存在")
        return
    skills = sorted([d for d in CENTRAL_SKILLS.iterdir() if d.is_dir()],
                    key=lambda x: x.name)
    rows = []
    for s in skills:
        meta = read_meta(s)
        sources = []
        merged = False
        if meta:
            sources = meta.get("sources", [])
            merged = meta.get("merged", False)
        rows.append((s.name, ", ".join(sources), "Y" if merged else ""))
    print(f"  {'Skill':<35} {'Sources':<30} Merged")
    print(f"  {'-'*35} {'-'*30} {'-'*6}")
    for name, sources, merged in rows:
        print(f"  {name:<35} {sources:<30} {merged}")
    print(f"\n共 {len(rows)} 个 skill")


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
        bak_dir = HOME_ROOT / "backups"
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

    write_meta(dest, {
        "name": safe,
        "sources": [str(src)],
        "merged": False,
        "global": True,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    })

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
    skillhome init              首次初始化：自动扫描发现 skill 目录，生成 config.json
    skillhome discover          重新扫描发现 skill 目录
    skillhome sync              手动触发增量同步
    skillhome sync --full       完整同步（重建所有链接）
    skillhome status            查看当前状态
    skillhome list              列出所有 skill 及其分布
    skillhome link <skill> <agent>    把 skill 链接到 agent 目录
    skillhome unlink <skill> <agent>  从 agent 目录移除链接
    skillhome global <skill> [on|off] 设置/取消全局共享
    skillhome add <source> [options]  安装 skill：本地 zip/目录直接入中央仓库，远程源走 npx
    skillhome config            查看当前配置
    skillhome help              显示此帮助

  中央仓库: %s
  配置文件: %s

  首次使用:
    1. skillhome init     (自动扫描发现 skill 目录)
    2. skillhome sync     (迁移到中央仓库 + 创建链接)
    3. skillhome status   (检查状态)

  平台:
    Windows: NTFS junction（不需要管理员权限）
    Linux/macOS: symlink
    依赖: Python 3.8+
""" % (CENTRAL_SKILLS, CONFIG_PATH))


# ============================================================
# 主分发
# ============================================================
def main():
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(0)

    cmd = sys.argv[1].lower()
    args = sys.argv[2:]

    if cmd == "init":
        cmd_discover(force=True)
    elif cmd == "discover":
        cmd_discover(force=True)
    elif cmd == "sync":
        dry_run = "--dry" in args
        incremental = "--full" not in args
        verbose = "--verbose" in args
        cmd_sync(dry_run=dry_run, incremental=incremental, verbose=verbose)
    elif cmd == "status":
        cmd_status()
    elif cmd == "list":
        cmd_list()
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
    elif cmd == "config":
        cmd_config()
    elif cmd == "help":
        cmd_help()
    else:
        print(f"未知命令: {cmd}")
        cmd_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
