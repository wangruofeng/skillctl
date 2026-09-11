#!/usr/bin/env python3
"""
rf-skill-doctor — diagnose the local skill management model.

User-level model (auto-detected; every part overridable):
  * REAL store dir holds the actual skill folders        (default ~/.agents/skills)
  * CONSUMER paths mirror the store via symlinks         (default ~/.claude/skills,
                                                          ~/.zcode/skills  ->  store)
  * A skill-manager lock file tracks installed skills    (default ~/.agents/.skill-lock.json)

Project mode (--project [PATH], default cwd):
  * Scans <PATH>/.claude/skills — project skills are their own source of truth
    (git-managed), so no consumer/lock topology is expected and a missing lock is
    not a warning.
  * Checks SKILL.md validity, junk entries, reverse symlinks (skill dirs whose
    real source lives elsewhere), name collisions + content drift against
    the user-level store (dual-source-of-truth risk), and — when the project is a
    git repo — whether non-source agent skill dirs (e.g. .zcode/skills,
    .codex/skills) are covered by .gitignore so only .claude/skills is tracked.


Default run: the user-level diagnosis is followed by the same project diagnosis
for the cwd, appended as a second section, whenever <cwd>/.claude/skills exists
and is not the store itself. --no-project skips it; --project runs project-only.

Reports: topology, per-consumer symlink integrity, store (inode) consistency,
lock-file reconciliation (orphans/missing), per-skill SKILL.md validity,
symlinked skill sources, and (project sections) cross-store duplicates +
single-source-of-truth / .gitignore coverage for non-source skill dirs.

Exit codes: 0 healthy | 1 warnings only | 2 one or more failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

HOME = Path.home()
DEFAULT_STORE = HOME / ".agents" / "skills"
DEFAULT_LOCK = HOME / ".agents" / ".skill-lock.json"
DEFAULT_CONSUMERS = [HOME / ".claude" / "skills", HOME / ".zcode" / "skills"]

# 项目内 skill 唯一事实源；其余 .<agent>/skills 均为镜像，不应入库
PROJECT_SOURCE_REL = ".claude/skills"
# 常见镜像目录（即使尚未创建，也会检查 gitignore 是否已覆盖）
PROJECT_MIRROR_RELS = (
    ".zcode/skills",
    ".codex/skills",
    ".cursor/skills",
    ".agents/skills",
)
# 推荐写入 .gitignore 的条目（与 rf-skill-sync policy 对齐）
GITIGNORE_SUGGESTIONS = (".codex/", ".cursor/", ".zcode/", ".agents/")

_HASH_CAP = 2_000_000  # files larger than this are compared by size only


# ----------------------------- presentation -----------------------------
class C:
    OK = "\033[32m"
    WARN = "\033[33m"
    FAIL = "\033[31m"
    DIM = "\033[2m"
    B = "\033[1m"
    END = "\033[0m"


_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _p(color: str, s: str) -> str:
    return f"{color}{s}{C.END}" if _COLOR else s


ICON = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}


# ----------------------------- report container -----------------------------
class Report:
    def __init__(self) -> None:
        self.findings: list[dict] = []

    def add(self, level: str, check: str, msg: str, detail: str | None = None) -> None:
        self.findings.append({"level": level, "check": check, "msg": msg, "detail": detail})

    @property
    def counts(self) -> dict:
        c = {"pass": 0, "warn": 0, "fail": 0}
        for f in self.findings:
            c[{v: k for k, v in [("pass", "PASS"), ("warn", "WARN"), ("fail", "FAIL")]}[f["level"]]] += 1
        return c

    @property
    def worst(self) -> int:
        if any(f["level"] == "FAIL" for f in self.findings):
            return 2
        if any(f["level"] == "WARN" for f in self.findings):
            return 1
        return 0


# ----------------------------- helpers -----------------------------
def describe(path: Path) -> dict:
    """Describe a path: kind does not follow symlinks; inode/resolved do."""
    d = {
        "path": str(path),
        "exists": path.exists(),
        "is_link": path.is_symlink(),
        "target": None,
        "target_exists": None,
        "inode": None,
        "resolved": None,
    }
    if d["is_link"]:
        d["target"] = os.readlink(path)
        try:
            st = path.stat()  # follows the link
            d["target_exists"] = True
            d["inode"] = st.st_ino
            d["resolved"] = str(path.resolve())
        except OSError:
            d["target_exists"] = False
    else:
        try:
            d["inode"] = path.stat().st_ino
            d["resolved"] = str(path.resolve())
        except OSError:
            pass
    return d


def disk_skills(store: Path) -> tuple[list[Path], list[str]]:
    """A skill = a direct subdir of the store containing SKILL.md. Returns (skills, junk).
    Dotfile entries (.DS_Store, *.json manifests) are metadata, not junk."""
    skills: list[Path] = []
    junk: list[str] = []
    try:
        entries = sorted(store.iterdir())
    except OSError:
        return skills, junk
    for e in entries:
        if e.name.startswith("."):
            continue
        if e.is_dir() and (e / "SKILL.md").exists():
            skills.append(e)
        else:
            junk.append(e.name)
    return skills, junk


_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")


def parse_frontmatter(text: str) -> tuple[dict | None, str | None]:
    """Parse SKILL.md frontmatter. Uses PyYAML if present, else a flat-key fallback."""
    m = _FRONT_RE.match(text)
    if not m:
        return None, "缺少 YAML frontmatter（未找到开头的 '---' ... '---' 块）"
    fm = m.group(1)
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(fm)
        if data is None:
            return {}, None
        if isinstance(data, dict):
            return data, None
        return None, "frontmatter 解析结果不是键值映射（mapping）"
    except Exception:
        pass
    # Minimal flat-key fallback. Handles the common multi-line block form
    # (e.g. "description:" followed by indented lines) that many real skills
    # use — without it those skills are wrongly flagged as empty. Nested
    # mappings like "metadata:" are flattened harmlessly; only top-level
    # name/description matter for validation here.
    data: dict = {}
    current: str | None = None
    block: list[str] = []

    def _flush() -> None:
        nonlocal current, block
        if current is not None and block:
            data[current] = " ".join(block).strip()
        current = None
        block = []

    for line in fm.splitlines():
        if not line.strip():
            continue
        if line[0] not in (" ", "\t"):  # potential top-level key
            kv = _KV_RE.match(line)
            if kv:
                _flush()
                key, val = kv.group(1), kv.group(2).strip()
                if val:
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                        val = val[1:-1]
                    data[key] = val
                else:
                    current = key  # value continues on following indented lines
                continue
        if current is not None:
            block.append(line.strip())
    _flush()
    return data, None


def validate_skill_md(skill_dir: Path) -> tuple[str, str]:
    sm = skill_dir / "SKILL.md"
    try:
        text = sm.read_text(encoding="utf-8")
    except OSError as e:
        return "FAIL", f"SKILL.md 无法读取：{e}"
    data, err = parse_frontmatter(text)
    if err:
        return "FAIL", f"frontmatter 解析失败：{err}"
    name = str(data.get("name", "")).strip()
    desc = str(data.get("description", "")).strip()
    version = str(data.get("version", "") or "").strip()
    if not name:
        return "FAIL", "frontmatter 缺少 'name' 字段"
    if not desc:
        return "FAIL", "缺少 'description' 或内容为空"
    # version 可选：有则附在通过信息里，缺失不失败、不警告
    # 尾部允许点分版本段（如 github-1.0.0）：目录名带版本后缀是 skills 生态
    # 从 plugin cache 复制安装的常见模式，否则与"name 须与目录名一致"自相矛盾
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:-\d+(?:\.\d+)+)?", name):
        return "WARN", f"name '{name}' 不是连字符小写格式"
    if name != skill_dir.name:
        return "WARN", f"frontmatter 中的 name '{name}' 与目录名 '{skill_dir.name}' 不一致"
    if "TODO" in desc:
        return "WARN", "description 中仍残留 TODO 占位符"
    if version:
        return "PASS", f"name={name}  version={version}"
    return "PASS", f"name 校验通过：{name}"


def _tree_snapshot(d: Path) -> dict[str, str]:
    """Relative path -> content hash (size-only for files above _HASH_CAP)."""
    snap: dict[str, str] = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file() or ".DS_Store" in p.parts:
            continue
        rel = p.relative_to(d).as_posix()
        try:
            size = p.stat().st_size
            snap[rel] = f"size:{size}" if size > _HASH_CAP else hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
    return snap


def drift_summary(a: Path, b: Path, label_a: str, label_b: str) -> str:
    sa, sb = _tree_snapshot(a), _tree_snapshot(b)
    only_a = sorted(set(sa) - set(sb))
    only_b = sorted(set(sb) - set(sa))
    differ = sorted(k for k in set(sa) & set(sb) if sa[k] != sb[k])
    if not (differ or only_a or only_b):
        return "当前两份副本内容完全一致"
    detail = []
    if differ:
        detail.append("内容不同：" + _trunc(differ, 6))
    if only_a:
        detail.append(f"仅存在于{label_a}：" + _trunc(only_a, 6))
    if only_b:
        detail.append(f"仅存在于{label_b}：" + _trunc(only_b, 6))
    return (f"{len(differ)} 个文件内容不同，{len(only_a)} 个仅存在于{label_a}，"
            f"{len(only_b)} 个仅存在于{label_b} —— 请合并为单一来源 | " + "；".join(detail))


def check_internal_links(store: Path, report: Report) -> None:
    """Skill dirs inside the scanned dir that are themselves symlinks — the
    reverse-link pattern where the real source lives elsewhere. Healthy links
    pass (but are surfaced); dangling ones fail."""
    if not store.is_dir() or store.is_symlink():
        return
    healthy: list[str] = []
    try:
        entries = sorted(store.iterdir())
    except OSError:
        return
    for e in entries:
        if e.name.startswith(".") or not e.is_symlink():
            continue
        info = describe(e)
        if not info["target_exists"]:
            report.add("FAIL", "links", f"{e.name}：软链接失效 -> {info['target']}")
        else:
            healthy.append(f"{e.name} -> {info['resolved']}")
    if healthy:
        report.add("PASS", "links",
                   f"{len(healthy)} 个 skill 目录本身是软链接，真实源码位于被扫描目录之外"
                   f"（若是有意为之则无妨，但这意味着 store 并非完全自包含）",
                   "; ".join(healthy))


def check_cross_store_duplicates(store: Path, cross: Path, report: Report) -> None:
    """Project mode: same skill name in both project and global store. If both
    resolve to the same path it is one source; two real copies are a dual
    source of truth and get a drift summary."""
    if not cross.is_dir():
        report.add("WARN", "duplicate",
                   f"未找到用于交叉比对的 store，已跳过重名检查：{cross}")
        return
    proj = {d.name: d for d in disk_skills(store)[0]}
    glob = {d.name: d for d in disk_skills(cross)[0]}
    dups = sorted(set(proj) & set(glob))
    if not dups:
        report.add("PASS", "duplicate", "项目与全局 store 中不存在同名 skill")
        return
    dual = [n for n in dups if proj[n].resolve() != glob[n].resolve()]
    shared = [n for n in dups if n not in dual]
    for n in dual:
        report.add("WARN", "duplicate",
                   f"{n}：项目与全局 store 中均存在真实副本（双重来源）",
                   drift_summary(proj[n], glob[n], "项目", "全局"))
    if shared:
        report.add("PASS", "duplicate",
                   f"{len(shared)} 个同名 skill 指向同一来源（软链接）",
                   _trunc(shared))


def _trunc(items: list[str], n: int = 20) -> str:
    return " · ".join(items[:n]) + (" …" if len(items) > n else "")


# ----------------------------- git / single-source-of-truth -----------------------------
def _run_git(project_root: Path, *args: str) -> tuple[int, str, str]:
    """在 project_root 下执行 git；返回 (returncode, stdout, stderr)。"""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def git_toplevel(project_root: Path) -> Path | None:
    """若 project_root 处于 git 工作树内，返回仓库顶层路径，否则 None。"""
    code, out, _ = _run_git(project_root, "rev-parse", "--show-toplevel")
    if code != 0:
        return None
    top = out.strip()
    return Path(top) if top else None


def git_check_ignored(project_root: Path, rel: str) -> bool:
    """路径是否被 gitignore / exclude 规则忽略。"""
    code, _, _ = _run_git(project_root, "check-ignore", "-q", "--", rel)
    return code == 0


def git_is_tracked(project_root: Path, rel: str) -> bool:
    """路径（或目录下任意文件）是否已被 git 跟踪。"""
    code, out, _ = _run_git(project_root, "ls-files", "--", rel)
    return code == 0 and bool(out.strip())


def discover_project_mirror_dirs(project_root: Path) -> list[str]:
    """发现项目根下一层 .<agent>/skills 目录（排除唯一事实源 .claude/skills）。"""
    found: set[str] = set()
    try:
        for child in sorted(project_root.iterdir()):
            if not child.name.startswith(".") or child.name == ".git":
                continue
            if not child.is_dir():
                continue
            skills = child / "skills"
            # 目录存在，或本身是指向别处的软链接
            if skills.is_dir() or skills.is_symlink():
                rel = f"{child.name}/skills"
                if rel != PROJECT_SOURCE_REL:
                    found.add(rel)
    except OSError:
        pass
    # 合并常见镜像路径，便于「尚未创建但应 ignore」的提示
    for rel in PROJECT_MIRROR_RELS:
        if rel != PROJECT_SOURCE_REL:
            found.add(rel)
    return sorted(found)


def check_project_single_source(project_root: Path, report: Report) -> None:
    """项目 skill 单一事实源诊断。

    约定：`.claude/skills` 是唯一应入库的 skill 源；`.zcode/skills`、
    `.codex/skills` 等镜像目录若存在于 git 仓库中，必须被 .gitignore 覆盖，
    且不应已被跟踪，否则会形成双重来源或把软链接噪声提交进仓库。
    """
    top = git_toplevel(project_root)
    if top is None:
        report.add(
            "PASS",
            "sot",
            "项目未纳入 git 版本控制，跳过非源头 skill 的 .gitignore 检查",
        )
        return

    # 尽量在仓库根解释 ignore 规则（子目录项目时仍以 toplevel 为准）
    git_root = top
    try:
        rel_prefix = project_root.resolve().relative_to(git_root.resolve()).as_posix()
    except ValueError:
        rel_prefix = ""

    def to_git_rel(project_rel: str) -> str:
        if not rel_prefix or rel_prefix == ".":
            return project_rel
        return f"{rel_prefix}/{project_rel}"

    source = project_root / PROJECT_SOURCE_REL
    source_git_rel = to_git_rel(PROJECT_SOURCE_REL)

    # 源头不应被整体 ignore（否则「唯一事实源」无法进版本库）
    if source.is_dir() or source.is_symlink():
        if git_check_ignored(git_root, source_git_rel):
            report.add(
                "WARN",
                "sot",
                f"唯一事实源 {PROJECT_SOURCE_REL} 被 gitignore 忽略，"
                "将无法作为项目内可共享的 skill 来源入库",
                "请从 .gitignore 中移除对该路径的忽略（可保留 .claude/settings.local.json 等局部文件规则）",
            )
        else:
            report.add(
                "PASS",
                "sot",
                f"唯一事实源 {PROJECT_SOURCE_REL} 未被 ignore，可作为 git 跟踪来源",
            )
    else:
        report.add(
            "WARN",
            "sot",
            f"未找到唯一事实源目录 {PROJECT_SOURCE_REL}，单一事实源约定不完整",
            "可运行 rf-skill-init / skills-init 创建",
        )

    mirrors = discover_project_mirror_dirs(project_root)
    ignored: list[str] = []
    unignored_existing: list[str] = []
    unignored_absent: list[str] = []
    tracked_bad: list[str] = []

    for rel in mirrors:
        path = project_root / rel
        git_rel = to_git_rel(rel)
        exists = path.is_dir() or path.is_symlink()
        ignored_now = git_check_ignored(git_root, git_rel)
        tracked = git_is_tracked(git_root, git_rel)

        if tracked:
            tracked_bad.append(rel)
            continue  # 已跟踪的路径不会被 ignore 生效，避免重复 WARN
        if ignored_now:
            ignored.append(rel)
        elif exists:
            unignored_existing.append(rel)
        else:
            # 常见镜像尚未创建，但 gitignore 也未覆盖 → 提醒预埋规则
            if rel in PROJECT_MIRROR_RELS:
                unignored_absent.append(rel)

    suggest = "建议在 .gitignore 中添加：\n" + "\n".join(GITIGNORE_SUGGESTIONS)

    if tracked_bad:
        report.add(
            "FAIL",
            "sot",
            f"{len(tracked_bad)} 个非源头 skill 目录已被 git 跟踪（破坏单一事实源）",
            _trunc(tracked_bad) + " | 请停止跟踪并加入 .gitignore："
            + " git rm -r --cached <path>；" + suggest,
        )

    if unignored_existing:
        report.add(
            "WARN",
            "sot",
            f"{len(unignored_existing)} 个非源头 skill 目录存在但未被 .gitignore 忽略",
            _trunc(unignored_existing) + " | " + suggest,
        )

    if unignored_absent and not unignored_existing and not tracked_bad:
        report.add(
            "WARN",
            "sot",
            f"{len(unignored_absent)} 个常见镜像路径尚未被 .gitignore 覆盖"
            f"（目录当前不存在，预埋规则可防止日后误提交）",
            _trunc(unignored_absent) + " | " + suggest,
        )

    if ignored and not unignored_existing and not tracked_bad:
        # 存在的镜像都已 ignore，或至少没有未 ignore 的现存目录
        existing_ignored = [
            r for r in ignored
            if (project_root / r).is_dir() or (project_root / r).is_symlink()
        ]
        if existing_ignored:
            report.add(
                "PASS",
                "sot",
                f"{len(existing_ignored)} 个非源头 skill 目录已被 .gitignore 忽略",
                _trunc(existing_ignored),
            )
        elif ignored:
            report.add(
                "PASS",
                "sot",
                "常见非源头 skill 路径已被 .gitignore 覆盖（目录尚未创建）",
                _trunc(ignored),
            )


# ----------------------------- core diagnostic -----------------------------
def run(store: Path, consumers: list[Path], lock: Path, report: Report,
        project_root: Path | None = None, cross_store: Path | None = None) -> dict:
    sinfo = describe(store)
    model = {
        "mode": "project" if project_root else "user",
        "store": str(store),
        "consumers": [str(c) for c in consumers],
        "lock": str(lock),
        "store_info": sinfo,
    }
    if project_root:
        model["project"] = str(project_root)
        model["cross_store"] = str(cross_store) if cross_store else None

    # 1. store / project skills dir
    if not sinfo["exists"] and not sinfo["is_link"]:
        if project_root:
            # 项目没有 .claude/skills 是合法状态（未安装项目级 skill），
            # 初始化引导由"单一事实源"检查负责，这里不重复报 FAIL
            report.add("PASS", "store", f"项目无 skills 目录：{store}",
                       "该项目下没有 .claude/skills —— 无项目 skill 可诊断。"
                       "如需初始化可运行 skills-init。")
        else:
            report.add("FAIL", "store", f"store 目录不存在：{store}",
                       "请创建该目录或通过 --store 指定。消费端无法镜像一个不存在的 store。")
    elif sinfo["is_link"]:
        report.add("WARN", "store", f"store 路径本身是软链接 -> {sinfo['target']}",
                   "store 应当是真实目录（唯一可信来源）。")
    else:
        label = "项目 skills 目录" if project_root else "真实目录"
        report.add("PASS", "store", f"{label}：{store}（inode {sinfo['inode']}）")

    store_resolved = sinfo["resolved"]

    # 2 & 3. consumers + consistency
    consistent: list[str] = []
    for c in consumers:
        ci = describe(c)
        if not ci["exists"] and not ci["is_link"]:
            report.add("FAIL", "symlink", f"{c}：缺失（既不是软链接，也不存在）")
            continue
        if not ci["is_link"]:
            report.add("WARN", "symlink",
                       f"{c}：真实目录而非软链接（独立 store -> 存在失同步风险）")
            continue
        if not ci["target_exists"]:
            report.add("FAIL", "symlink", f"{c}：软链接失效 -> {ci['target']}")
            continue
        if store_resolved and ci["resolved"] == store_resolved:
            report.add("PASS", "symlink",
                       f"{c} -> {ci['target']}（正确指向 store，inode {ci['inode']}）")
            consistent.append(str(c))
        else:
            report.add("FAIL", "symlink",
                       f"{c}：软链接 -> {ci['target']}，但实际解析到 {ci['resolved']}"
                       f"（期望指向 store {store_resolved}）")
    if consumers:
        report.add("PASS" if len(consistent) == len(consumers) else "FAIL", "consistency",
                   f"{len(consistent)}/{len(consumers)} 个消费端指向 store 的同一 inode")

    # 4. lock file (in project mode a missing lock is expected — git is the source of truth)
    lock_names: set[str] = set()
    if not lock.exists():
        if project_root:
            report.add("PASS", "lock",
                       f"项目无 lock 文件（{lock.name} 不存在）—— git 即可信来源，跳过比对")
        else:
            report.add("WARN", "lock", f"lock 文件缺失：{lock}",
                       "没有 skill 管理器在跟踪。如果你不使用管理器，这属于正常情况；已跳过比对。")
    else:
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            skills_map = data.get("skills", {}) if isinstance(data, dict) else {}
            lock_names = set(skills_map.keys())
            report.add("PASS", "lock",
                       f"JSON 合法，version={data.get('version', '?')}，"
                       f"跟踪 {len(lock_names)} 个 skill")
        except json.JSONDecodeError as e:
            report.add("FAIL", "lock", f"{lock} 中 JSON 格式非法：{e}")

    # reconcile disk vs lock
    disk_names = {d.name for d in disk_skills(store)[0]}
    if lock.exists() and sinfo["exists"] and not sinfo["is_link"]:
        orphans = sorted(disk_names - lock_names)
        missing = sorted(lock_names - disk_names)
        # lock 只是 skills CLI 的安装账本：手动复制/软链接安装的 skill 不在其中
        # 属正常，orphan 仅作信息性提示；missing（残留账目）才是需要清理的问题
        if missing:
            report.add("WARN", "reconcile",
                       f"{len(missing)} 个 skill 被 lock 文件跟踪但磁盘上已不存在（残留记录）",
                       _trunc(missing))
        if orphans:
            report.add("PASS", "reconcile",
                       f"{len(disk_names)} 个 skill 中 {len(orphans)} 个不受 lock 跟踪"
                       "（手动安装或软链接管理，lock 仅记录 skills CLI 安装条目，属正常）",
                       _trunc(orphans))
        if not orphans and not missing:
            report.add("PASS", "reconcile", f"磁盘与 lock 文件一致（{len(disk_names)} 个 skill）")

    # 5. per-skill validity
    if sinfo["exists"] and not sinfo["is_link"]:
        skills, junk = disk_skills(store)
        fails = warns = 0
        for sd in skills:
            level, msg = validate_skill_md(sd)
            if level == "FAIL":
                fails += 1
            elif level == "WARN":
                warns += 1
            if level != "PASS":
                report.add(level, "skill-md", f"{sd.name}: {msg}")
        ok = len(skills) - fails - warns
        if fails:
            report.add("FAIL", "skill-md",
                       f"{ok}/{len(skills)} 个 skill 的 SKILL.md 有效（{fails} 个失败，{warns} 个警告）")
        elif warns:
            report.add("WARN", "skill-md",
                       f"{ok}/{len(skills)} 个 skill 完全有效（{warns} 个警告）")
        else:
            report.add("PASS", "skill-md",
                       f"{len(skills)}/{len(skills)} 个 skill 的 SKILL.md 均有效")
        if junk:
            report.add("WARN", "junk", f"store 中有 {len(junk)} 个非 skill 条目（缺少 SKILL.md）",
                       _trunc(junk))

    # 6. symlinked skill sources (both modes)
    if sinfo["exists"] and not sinfo["is_link"]:
        check_internal_links(store, report)

    # 7. project-vs-global duplicates (project mode only)
    if project_root and cross_store:
        if sinfo["exists"] and not sinfo["is_link"]:
            check_cross_store_duplicates(store, cross_store, report)

    # 8. single source of truth + .gitignore for non-source skill dirs (project mode)
    if project_root:
        check_project_single_source(project_root, report)

    return model


# ----------------------------- repairs -----------------------------
def apply_fix(store: Path, consumers: list[Path], report: Report) -> None:
    if not store.is_dir():
        report.add("FAIL", "fix", f"--fix 已中止：store 不是真实目录：{store}")
        return
    fixed = skipped = 0
    for c in consumers:
        ci = describe(c)
        already_ok = ci["is_link"] and ci["target_exists"] and ci["resolved"] == str(store.resolve())
        if already_ok:
            continue
        if ci["exists"] and not ci["is_link"]:
            report.add("WARN", "fix", f"{c}：真实目录未做改动（请手动合并或迁移）")
            skipped += 1
            continue
        reason = ("缺失" if not ci["is_link"]
                  else "软链接失效" if not ci["target_exists"]
                  else f"指向 {ci['resolved']}")
        try:
            if c.is_symlink():
                c.unlink()
            c.parent.mkdir(parents=True, exist_ok=True)
            c.symlink_to(store.resolve())
            report.add("PASS", "fix", f"{c}：已重新链接 -> {store}（原状态：{reason}）")
            fixed += 1
        except OSError as e:
            report.add("FAIL", "fix", f"{c}：重新链接失败：{e}")
            skipped += 1
    report.add("PASS" if skipped == 0 else "WARN", "fix",
               f"--fix 汇总：已重链 {fixed} 个，跳过 {skipped} 个")


# ----------------------------- rendering -----------------------------
ORDER = ["store", "symlink", "consistency", "lock", "reconcile", "skill-md", "junk",
         "links", "duplicate", "sot", "fix"]

# 只影响人读的排版；JSON 的 check 字段仍是英文 slug
CHECK_LABEL = {
    "store": "存储目录",
    "symlink": "软链接",
    "consistency": "一致性",
    "lock": "lock 文件",
    "reconcile": "对账",
    "skill-md": "SKILL.md",
    "junk": "非 skill 条目",
    "links": "反向链接",
    "duplicate": "重名检查",
    "sot": "单一事实源",
    "fix": "修复",
}

_ICON_COLOR = {"PASS": C.OK, "WARN": C.WARN, "FAIL": C.FAIL}
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _w(s: str) -> int:
    """终端显示宽度：CJK 与 emoji 记 2 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _plain(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def _abbr(s: str) -> str:
    """渲染层把 $HOME 缩成 ~；JSON 仍输出完整路径。"""
    return s.replace(str(HOME), "~")


def _short(s: str, maxlen: int = 52) -> str:
    """过长路径省略中间段：~/Documents/…/knowlege/knowledges"""
    s = _abbr(s)
    if _w(s) <= maxlen:
        return s
    parts = [p for p in s.split("/") if p and p != "~"]
    if not parts:
        return s
    return ("~/…/" if s.startswith("~") else "…/") + "/".join(parts[-3:])


_CLOSERS = "）〕】》」』、。，；：？！%…·”’"   # 不能出现在行首
_OPENERS = "（〔【《「『“‘"                    # 不能出现在行尾
_SEP = "·;,/"                                  # 吸附到前一段，其后可断行
_MAX_DETAIL_LINES = 8                          # 详情最多打印几行，超出提示看 --json


def _segments(text: str) -> list[str]:
    """切成最小不可断单元：ASCII 词整体保留，CJK 逐字可断，标点吸附到前一字。"""
    segs: list[str] = []
    buf = ""
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            if buf:
                segs.append(buf)
                buf = ""
            if segs and _w(segs[-1][-1]) == 2 and (ch in _CLOSERS or segs[-1][-1] in _OPENERS):
                segs[-1] += ch
            else:
                segs.append(ch)
        elif ch == " ":
            if buf:
                segs.append(buf + " ")
                buf = ""
            elif segs:
                segs[-1] += " "
        elif ch in _SEP:
            if buf:
                segs.append(buf)
                buf = ""
            if segs:
                segs[-1] += ch
            else:
                buf = ch
        else:
            buf += ch
    if buf:
        segs.append(buf)
    return segs


def _wrap(text: str, width: int) -> list[str]:
    """按显示宽度折行；中文可在字间断，ASCII 词与路径不会被拦腰截断。"""
    width = max(20, width)
    lines: list[str] = []
    cur = ""
    curw = 0
    for seg in _segments(text):
        sw = _w(seg)
        if cur and curw + sw > width:
            lines.append(cur.rstrip())
            cur, curw = "", 0
        if not cur:
            seg = seg.lstrip()
            sw = _w(seg)
        cur += seg
        curw += sw
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def _width() -> int:
    """报告统一宽度：正文折行、分隔线、右侧计数都对齐到这一列。"""
    try:
        cols = shutil.get_terminal_size().columns
    except OSError:
        cols = 80
    return max(58, min(cols - 2, 76))


def _icon(level: str) -> str:
    return _p(_ICON_COLOR[level], ICON[level])


def _tally(report: Report) -> str:
    """分组标题右侧的紧凑计数。"""
    c = report.counts
    return (f"{_icon('PASS')} {c['pass']}  {_icon('WARN')} {c['warn']}  "
            f"{_icon('FAIL')} {c['fail']}")


def _tally_verbose(p: int, w: int, f: int) -> str:
    """结论行用的完整计数。"""
    return (f"{_icon('PASS')} {p} 通过   {_icon('WARN')} {w} 警告   "
            f"{_icon('FAIL')} {f} 失败")


def _right(label: str, tail: str) -> str:
    """把 tail 右对齐到报告右边界。"""
    gap = max(2, _width() - _w(label) - _w(_plain(tail)))
    return f"{_p(C.B, label)}{' ' * gap}{tail}"


def _meta_lines(model: dict) -> list[str]:
    """scope 的路径信息：键左对齐成一列（消费端/模式由下方检查项覆盖，不再重复）。"""
    if model.get("mode") == "project":
        pairs = [("skills", model["store"]),
                 ("交叉比对", model.get("cross_store") or "（无）"),
                 ("lock", model["lock"])]
    else:
        pairs = [("store", model["store"]),
                 ("lock", model["lock"])]
    kw = max(_w(k) for k, _ in pairs)
    return [_p(C.DIM, f"  {_pad(k, kw)}  {_abbr(v)}") for k, v in pairs]


def _findings_lines(report: Report, width: int) -> list[str]:
    """每个检查项占一列：检查名 | 状态 | 消息，详情缩进到消息下方。"""
    groups: list[tuple[str, list[dict]]] = []
    seen: set[str] = set()
    for chk in ORDER:
        items = [f for f in report.findings if f["check"] == chk]
        if items:
            groups.append((chk, items))
            seen.add(chk)
    for f in report.findings:
        if f["check"] not in seen:
            groups.append((f["check"], [f]))
            seen.add(f["check"])
    if not groups:
        return []
    labels = [CHECK_LABEL.get(c, c) for c, _ in groups]
    lw = max(_w(label) for label in labels)
    lines: list[str] = []
    for (_, items), label in zip(groups, labels):
        for n, f in enumerate(items):
            # 宽度按无色版本计算：_w() 会把 ANSI 色码当可见字符，导致
            # 彩色（tty）模式下折行过早、续行缩进过量。渲染时再套色。
            head = f"  {_pad(label if n == 0 else '', lw)}  "
            icon = _icon(f["level"])
            head_w = _w(head) + _w(ICON[f["level"]]) + 2
            for i, seg in enumerate(_wrap(_abbr(f["msg"]), width - head_w)):
                lines.append((head + icon + "  " + seg) if i == 0 else " " * head_w + seg)
            detail = f.get("detail")
            if detail:
                indent = " " * head_w
                body = _wrap(_abbr(detail), width - _w(indent))
                if len(body) > _MAX_DETAIL_LINES:
                    hidden = len(body) - (_MAX_DETAIL_LINES - 1)
                    body = body[:_MAX_DETAIL_LINES - 1] + [f"…（其余 {hidden} 行见 --json）"]
                for seg in body:
                    lines.append(_p(C.DIM, indent + seg))
    return lines


def render_human(sections: list[tuple[dict, Report, str]]) -> None:
    """sections: (model, report, label) — one per diagnosed scope."""
    width = _width()
    rule = _p(C.DIM, "─" * width)
    lines = [f"{_p(C.B, 'rf-skill-doctor')}  —  skill 管理健康检查", ""]
    tp = tw = tf = 0
    for i, (model, report, label) in enumerate(sections):
        if i:
            lines.append("")
        lines.append(_right(label, _tally(report)))
        lines.extend(_meta_lines(model))
        lines.append("")
        lines.extend(_findings_lines(report, width))
        lines.append(rule)
        c = report.counts
        tp += c["pass"]
        tw += c["warn"]
        tf += c["fail"]
    lines.append(_right("合计", _tally_verbose(tp, tw, tf)))
    print("\n".join(lines))


# ----------------------------- cli -----------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="skill_doctor.py",
        description="诊断本地 skill 管理模型（store + 软链接消费端 + lock 文件），"
                    "或诊断某个项目的 .claude/skills（--project）。",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--project", nargs="?", const=".", default=None, metavar="PATH",
                      help="仅诊断 <PATH>/.claude/skills（默认当前目录）。项目 skill 由 git 管理、"
                           "自成可信来源：检查 SKILL.md 有效性、非 skill 条目、反向软链接，"
                           "与全局 store 的重名/内容漂移，以及 git 仓库中非源头 skill 目录"
                           "（.zcode/.codex 等）是否已被 .gitignore 覆盖。不加此参数时，默认运行会自动"
                           "附加一份对当前目录的项目诊断。")
    mode.add_argument("--no-project", action="store_true", dest="no_project",
                      help="默认运行中跳过对当前目录的自动项目诊断（与 --project 互斥）")
    p.add_argument("--store", default=None,
                   help="真实的 skill store 目录（用户模式）；项目模式下为交叉比对的目标 store"
                        "（默认 ~/.agents/skills）")
    p.add_argument("--consumers", default=None,
                   help="以逗号分隔的消费端路径，预期通过软链接指向 store"
                        "（仅用户模式；默认 ~/.claude/skills,~/.zcode/skills）")
    p.add_argument("--lock", default=None,
                   help="skill 管理器的 lock 文件（用户模式默认 ~/.agents/.skill-lock.json；"
                        "项目模式在存在时使用 <project>/.claude/.skill-lock.json）")
    p.add_argument("--json", action="store_true", dest="as_json", help="输出机器可读的 JSON")
    p.add_argument("--fix", action="store_true",
                   help="重建损坏或指向错误的消费端软链接，使其指向 store")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    sections: list[tuple[dict, Report, str]] = []
    if args.project is not None:
        project_root = Path(args.project).expanduser().resolve()
        store = project_root / ".claude" / "skills"
        consumers: list[Path] = []
        lock = project_root / ".claude" / ".skill-lock.json"
        cross_store: Path | None = Path(args.store).expanduser() if args.store else DEFAULT_STORE
        report = Report()
        if args.fix:
            # 项目模式没有消费端软链接，--fix 无可修复对象（SKILL.md：no-op）；
            # 不调用 apply_fix，避免对不存在的 .claude/skills 误报 FAIL
            report.add("PASS", "fix", "项目模式下无消费端软链接，--fix 无操作")
        model = run(store, consumers, lock, report,
                    project_root=project_root, cross_store=cross_store)
        sections.append((model, report, f"项目（{_short(str(project_root))}）"))
    else:
        store = Path(args.store).expanduser() if args.store else DEFAULT_STORE
        if args.consumers is not None:
            consumers = [Path(c).expanduser() for c in args.consumers.split(",") if c.strip()]
        else:
            consumers = list(DEFAULT_CONSUMERS)
        lock = Path(args.lock).expanduser() if args.lock else DEFAULT_LOCK
        report = Report()
        if args.fix:
            apply_fix(store, consumers, report)
        model = run(store, consumers, lock, report)
        sections.append((model, report, "用户级（全局 store）"))
        # auto project diagnosis for the cwd (skipped when absent, or when the
        # cwd's skills dir is the store itself — e.g. running from $HOME)
        if not args.no_project:
            cwd = Path.cwd()
            proj_skills = cwd / ".claude" / "skills"
            if proj_skills.is_dir() and proj_skills.resolve() != store.resolve():
                preport = Report()
                pmodel = run(proj_skills, [], cwd / ".claude" / ".skill-lock.json", preport,
                             project_root=cwd, cross_store=store)
                sections.append((pmodel, preport, f"项目（{_short(str(cwd))}）"))
    worst = max(r.worst for _, r, _ in sections)
    if args.as_json:
        total = {"pass": 0, "warn": 0, "fail": 0}
        for _, r, _ in sections:
            for k in total:
                total[k] += r.counts[k]
        payload = {
            "skill": "rf-skill-doctor",
            "model": sections[0][0],
            "findings": sections[0][1].findings,
            "summary": {**total, "exit_code": worst},
        }
        if len(sections) > 1:
            pmodel, preport, _ = sections[1]
            payload["project"] = {"model": pmodel, "findings": preport.findings}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        render_human(sections)
    return worst


if __name__ == "__main__":
    sys.exit(main())
