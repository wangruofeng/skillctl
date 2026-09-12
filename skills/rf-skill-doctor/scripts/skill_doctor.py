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

--autofix applies every safe, deterministic repair in one shot (consumer
relink, stale lock-entry pruning, SKILL.md name alignment, backup cleanup,
dangling per-skill link repair in real agent dirs, project .gitignore
backfill + git untrack of mirror dirs). Anything non-deterministic (real-dir
consumers, invalid lock JSON, dual-source copies, ...) stays guidance-only.

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
import time
import unicodedata
from pathlib import Path

HOME = Path.home()
DEFAULT_STORE = HOME / ".agents" / "skills"
DEFAULT_LOCK = HOME / ".agents" / ".skill-lock.json"
DEFAULT_CONSUMERS = [HOME / ".claude" / "skills", HOME / ".zcode" / "skills",
                     HOME / ".cursor" / "skills"]
# 用户级 Agent skill 目录（存在才检查）：整目录链消费端之外的「真实目录型」
# Agent 目录（如 codex，为与系统 skill 共存而保留真实目录）在这里做内部逐 skill 链接体检
DEFAULT_AGENT_DIRS = [HOME / ".claude" / "skills", HOME / ".zcode" / "skills",
                      HOME / ".codex" / "skills", HOME / ".cursor" / "skills"]

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

# 与 SKILL.md frontmatter 的 version 同步（同一 commit 内一起改）
SKILL_VERSION = "1.3.0"

# 目录名 / name 的合法格式：连字符小写，允许尾部点分版本段（github-1.0.0）。
# validate_skill_md 与 autofix 的 name 对齐共用这一份规则。
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*(?:-\d+(?:\.\d+)+)?")


def _ts() -> str:
    """14 位时间戳，与 skills-link --force 的备份命名一致。"""
    return time.strftime("%Y%m%d%H%M%S")


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


# skills-link --force 的备份命名：<skill名>.bak-<YYYYMMDDHHMMSS>（目录或文件）
_BAK_RE = re.compile(r"^(.+)\.bak-\d{14}$")


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
        if _BAK_RE.match(e.name):
            continue  # skills-link --force 备份，含 SKILL.md 但不是 skill，由 backups 检查处理
        if e.is_dir() and (e / "SKILL.md").exists():
            skills.append(e)
        else:
            junk.append(e.name)
    return skills, junk


def find_backups(d: Path) -> list[Path]:
    """rf-skill-link --force 产生的备份条目（*.bak-<14 位时间戳>，目录或文件）。"""
    out: list[Path] = []
    try:
        for e in sorted(d.iterdir()):
            if not e.name.startswith(".") and _BAK_RE.match(e.name):
                out.append(e)
    except OSError:
        pass
    return out


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
    if not _SLUG_RE.fullmatch(name):
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


def check_backups(d: Path, report: Report) -> None:
    """skills-link --force 残留备份：占空间且会被 agent 当作重复 skill 加载。"""
    baks = find_backups(d)
    if not baks:
        return
    names = [b.name for b in baks]
    report.add("WARN", "backups",
               f"发现 {len(baks)} 个 skills-link --force 备份（*.bak-<时间戳>），"
               "会被 agent 识别为重复 skill",
               _trunc(names) + " | 确认链接内容无误后可清理"
               "（--clean-backups：同名 skill 已存在的备份直接删除，不存在的跳过以防误删唯一副本）")


def clean_backups(d: Path, report: Report, check: str = "backups") -> None:
    """删除 skills-link --force 备份。同名 skill 已就位才删；否则备份可能是
    唯一副本，跳过并提示，交由用户决定。"""
    label = "--autofix" if check == "autofix" else "--clean-backups"
    if not d.is_dir():
        report.add("WARN", check, f"{label} 已跳过：目录不存在或不可读：{d}")
        return
    baks = find_backups(d)
    if not baks:
        report.add("PASS", check, "无 skills-link --force 备份需要清理")
        return
    removed = skipped = failed = 0
    for b in baks:
        original = _BAK_RE.match(b.name).group(1)
        if not (d / original).exists():
            report.add("WARN", check,
                       f"{b.name}：同名 skill '{original}' 不存在，备份可能是唯一副本，已跳过",
                       "确认不需要恢复后，可手动删除该备份")
            skipped += 1
            continue
        try:
            if b.is_dir() and not b.is_symlink():
                shutil.rmtree(b)
            else:
                b.unlink()
            report.add("PASS", check, f"{b.name}：已删除（同名 skill '{original}' 正常）")
            removed += 1
        except OSError as e:
            report.add("FAIL", check, f"{b.name}：删除失败：{e}")
            failed += 1
    summary_level = "FAIL" if failed else ("WARN" if skipped else "PASS")
    report.add(summary_level, check,
               f"{label} 汇总：已删除 {removed} 个，跳过 {skipped} 个"
               + (f"，失败 {failed} 个" if failed else ""))


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


def check_agent_dir_links(agent_dirs: list[Path], report: Report) -> None:
    """用户级 Agent skill 目录内部的逐 skill 软链接体检。

    整目录链消费端（目录本身是软链接）跟随目标，内部无独立链接，且其
    健康已由「软链接」检查项负责，这里跳过；真实目录型 Agent 目录
    （如 codex，为与系统 skill 共存而保留真实目录）内部的逐 skill
    链接可能指向已被删除的 skill —— dangling 即 FAIL。
    """
    real_dirs: list[Path] = []
    for d in agent_dirs:
        info = describe(d)
        if info["is_link"] or not info["exists"]:
            continue  # 整目录链消费端（另由 symlink 检查）或未安装的 Agent
        real_dirs.append(d)
    if not real_dirs:
        return
    broken: list[str] = []
    healthy = 0
    for d in real_dirs:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or not e.is_symlink():
                continue
            info = describe(e)
            if not info["target_exists"]:
                report.add("FAIL", "consumer-links",
                           f"{d}：{e.name} 软链接失效 -> {info['target']}")
                broken.append(f"{d.name}/{e.name}")
            else:
                healthy += 1
    if broken:
        report.add("FAIL", "consumer-links",
                   f"{len(real_dirs)} 个真实目录型 Agent 目录中发现 {len(broken)} 个失效的逐 skill 软链接",
                   _trunc(broken))
    elif healthy:
        report.add("PASS", "consumer-links",
                   f"{len(real_dirs)} 个真实目录型 Agent 目录内 {healthy} 个逐 skill 软链接均有效")
    else:
        report.add("PASS", "consumer-links",
                   f"{len(real_dirs)} 个真实目录型 Agent 目录内无逐 skill 软链接")


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


def git_rel_prefix(project_root: Path, git_root: Path) -> str:
    """project_root 相对 git 仓库顶层的路径前缀（项目即顶层时为空串）。"""
    try:
        prefix = project_root.resolve().relative_to(git_root.resolve()).as_posix()
    except ValueError:
        return ""
    return "" if prefix == "." else prefix


def to_git_rel(prefix: str, project_rel: str) -> str:
    """把项目内相对路径换算成相对 git 仓库顶层的路径。"""
    return f"{prefix}/{project_rel}" if prefix else project_rel


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
    rel_prefix = git_rel_prefix(project_root, git_root)

    def to_rel(project_rel: str) -> str:
        return to_git_rel(rel_prefix, project_rel)

    source = project_root / PROJECT_SOURCE_REL
    source_git_rel = to_rel(PROJECT_SOURCE_REL)

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
        git_rel = to_rel(rel)
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
        project_root: Path | None = None, cross_store: Path | None = None,
        agent_dirs: list[Path] | None = None) -> dict:
    sinfo = describe(store)
    model = {
        "mode": "project" if project_root else "user",
        "store": str(store),
        "consumers": [str(c) for c in consumers],
        "lock": str(lock),
        "store_info": sinfo,
    }
    if not project_root:
        model["agent_dirs"] = [str(d) for d in (agent_dirs or [])]
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

    # 5b. skills-link --force backups (both modes)
    if sinfo["exists"] and not sinfo["is_link"]:
        check_backups(store, report)

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

    # 9. real-dir agent skill dirs: internal per-skill symlinks (user mode only)
    if not project_root:
        check_agent_dir_links(agent_dirs or [], report)

    return model


# ----------------------------- repairs -----------------------------
def apply_fix(store: Path, consumers: list[Path], report: Report,
              check: str = "fix") -> None:
    label = "--autofix" if check == "autofix" else "--fix"
    if not store.is_dir():
        report.add("FAIL", check, f"{label} 已中止：store 不是真实目录：{store}")
        return
    fixed = skipped = 0
    for c in consumers:
        ci = describe(c)
        already_ok = ci["is_link"] and ci["target_exists"] and ci["resolved"] == str(store.resolve())
        if already_ok:
            continue
        if ci["exists"] and not ci["is_link"]:
            report.add("WARN", check, f"{c}：真实目录未做改动（请手动合并或迁移）")
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
            report.add("PASS", check, f"{c}：已重新链接 -> {store}（原状态：{reason}）")
            fixed += 1
        except OSError as e:
            report.add("FAIL", check, f"{c}：重新链接失败：{e}")
            skipped += 1
    report.add("PASS" if skipped == 0 else "WARN", check,
               f"{label} 汇总：已重链 {fixed} 个，跳过 {skipped} 个")


def prune_lock_stale(store: Path, lock: Path, report: Report,
                     check: str = "autofix") -> None:
    """--autofix：删除 lock 中磁盘上已不存在的 skills 条目（残留账目）。

    先备份 lock 为 <lock>.bak-<14位时间戳>（与 lock 同目录，不在 store 内，
    不会被备份检查误报）；仅删除 skills 子键，其余字段（version 等）与键序
    原样保留。lock 非法或不可解析时不动作，交由 lock 检查项报 FAIL。
    """
    if not (lock.exists() and store.is_dir() and not store.is_symlink()):
        return  # 这些状态由诊断检查负责报告
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        report.add("WARN", check, f"lock 无法解析，已跳过残留条目清理：{e}")
        return
    skills_map = data.get("skills", {}) if isinstance(data, dict) else {}
    if not isinstance(skills_map, dict):
        report.add("WARN", check, "lock 的 skills 字段不是键值映射，已跳过残留条目清理")
        return
    disk_names = {d.name for d in disk_skills(store)[0]}
    stale = sorted(set(skills_map) - disk_names)
    if not stale:
        report.add("PASS", check, "lock 无残留条目需要清理")
        return
    try:
        bak = lock.with_name(f"{lock.name}.bak-{_ts()}")
        shutil.copy2(lock, bak)
        for k in stale:
            del skills_map[k]
        lock.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    except OSError as e:
        report.add("FAIL", check, f"lock 残留条目清理失败：{e}")
        return
    report.add("PASS", check, f"lock 已清理 {len(stale)} 条残留条目",
               _trunc(stale) + f" | 备份：{_abbr(str(bak))}（验证后可删）")


def autofix_skill_md_names(store: Path, report: Report,
                           check: str = "autofix") -> None:
    """--autofix：frontmatter name 与目录名不一致时，改写为目录名。

    仅处理真实目录：store 里的反向软链接指向外部源仓库，链接名与源目录名
    不保证一致，写穿链接可能改错源文件，所以只汇总为指引。改写在
    frontmatter 块内做单行字节级替换（保留 CRLF 与其余内容），写入前先备份
    SKILL.md.bak-<时间戳> 到同级。
    """
    if not (store.is_dir() and not store.is_symlink()):
        return
    fixed = failed = 0
    link_mismatches: list[str] = []
    for sd in disk_skills(store)[0]:
        sm = sd / "SKILL.md"
        try:
            text = sm.read_text(encoding="utf-8")
        except OSError:
            continue  # 读取失败属 FAIL 级问题，由诊断报告
        data, err = parse_frontmatter(text)
        if err or not isinstance(data, dict):
            continue
        name = str(data.get("name", "")).strip()
        if not name or name == sd.name:
            continue
        if sd.is_symlink():
            link_mismatches.append(f"{sd.name}（源：{describe(sd)['resolved']}）")
            continue
        if not _SLUG_RE.fullmatch(sd.name):
            continue  # 目录名本身不合法时改名会改变 skill 身份，交由人工
        m = _FRONT_RE.match(text)
        if not m:
            continue
        new_fm, n = re.subn(r"(?m)^(name[ \t]*:)[ \t]*.*?(\r?)$",
                            rf"\1 {sd.name}\2", m.group(1), count=1)
        if n != 1:
            report.add("WARN", check,
                       f"{sd.name}：未找到可替换的顶层 name: 行，已跳过")
            continue
        try:
            bak = sm.with_name(f"SKILL.md.bak-{_ts()}")
            shutil.copy2(sm, bak)
            sm.write_text(text[:m.start(1)] + new_fm + text[m.end(1):],
                          encoding="utf-8")
        except OSError as e:
            report.add("FAIL", check, f"{sd.name}：name 对齐失败：{e}")
            failed += 1
            continue
        report.add("PASS", check,
                   f"{sd.name}：name '{name}' -> '{sd.name}'",
                   f"备份：{bak}（验证后可删）")
        fixed += 1
    if link_mismatches:
        report.add("WARN", check,
                   f"{len(link_mismatches)} 个软链接型 skill 的 name 与目录名不一致，"
                   "已跳过（不写穿软链接）",
                   _trunc(link_mismatches) + " | 请到源仓库把 frontmatter 的 name "
                   "改为目录名后同步")
    if fixed or link_mismatches or failed:
        summary_parts = [f"已对齐 {fixed} 个"]
        if link_mismatches:
            summary_parts.append(f"软链接跳过 {len(link_mismatches)} 个")
        if failed:
            summary_parts.append(f"失败 {failed} 个")
        report.add("FAIL" if failed else "PASS", check,
                   "SKILL.md name 对齐汇总：" + "，".join(summary_parts))
    else:
        report.add("PASS", check, "所有真实目录 skill 的 name 均与目录名一致")


def autofix_agent_dir_links(store: Path, agent_dirs: list[Path],
                            report: Report, check: str = "autofix") -> None:
    """--autofix：真实目录型 Agent 目录内失效的逐 skill 软链接。

    store 中同名 skill 存在 -> 重链到 store；不存在 -> 摘除（目标已消失，
    无数据可丢，与 rf-skill-sync 的清理行为一致）。非软链接条目一律不动。
    """
    if not (store.is_dir() and not store.is_symlink()):
        return
    store_names = {d.name for d in disk_skills(store)[0]}
    real_dirs: list[Path] = []
    for d in agent_dirs:
        info = describe(d)
        if info["is_link"] or not info["exists"]:
            continue  # 整目录链消费端或未安装的 Agent
        real_dirs.append(d)
    if not real_dirs:
        return
    relinked = removed = failed = 0
    for d in real_dirs:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or not e.is_symlink():
                continue
            if describe(e)["target_exists"]:
                continue
            try:
                if e.name in store_names:
                    e.unlink()
                    e.symlink_to(store.resolve() / e.name)
                    report.add("PASS", check,
                               f"{d.name}/{e.name}：已重链 -> store（原目标已失效）")
                    relinked += 1
                else:
                    e.unlink()
                    report.add("PASS", check,
                               f"{d.name}/{e.name}：目标已不存在且 store 无同名 skill，已摘除")
                    removed += 1
            except OSError as ex:
                report.add("FAIL", check, f"{d.name}/{e.name}：修复失败：{ex}")
                failed += 1
    if relinked or removed or failed:
        report.add("FAIL" if failed else "PASS", check,
                   f"Agent 目录失效链接汇总：重链 {relinked} 个，摘除 {removed} 个"
                   + (f"，失败 {failed} 个" if failed else ""))
    else:
        report.add("PASS", check,
                   f"{len(real_dirs)} 个真实目录型 Agent 目录内无失效逐 skill 链接")


def _gitignore_missing_entries(git_root: Path) -> list[str]:
    """返回尚缺的推荐 ignore 条目。

    字面已存在（容忍尾斜杠差异）或对应镜像路径已被其他规则 ignore
    （git check-ignore 探测）均视为已覆盖。
    """
    covered: set[str] = set()
    gi = git_root / ".gitignore"
    if gi.is_file():
        try:
            for line in gi.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    covered.add(s.rstrip("/"))
        except OSError:
            pass
    missing: list[str] = []
    for s in GITIGNORE_SUGGESTIONS:
        base = s.rstrip("/")
        if base in covered:
            continue
        if git_check_ignored(git_root, f"{base}/skills"):
            continue
        missing.append(s)
    return missing


def autofix_project(project_root: Path, report: Report,
                    check: str = "autofix") -> None:
    """--autofix（项目模式）：补齐 .gitignore 推荐条目 + 取消跟踪非源头
    skill 目录。先补 ignore 再取消跟踪，untrack 后的文件立即被规则覆盖。"""
    top = git_toplevel(project_root)
    if top is None:
        report.add("PASS", check, "项目不在 git 仓库中，无需 .gitignore / 取消跟踪修复")
        return
    rel_prefix = git_rel_prefix(project_root, top)
    acted = failed = 0

    missing = _gitignore_missing_entries(top)
    if missing:
        gi = top / ".gitignore"
        try:
            existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
            if existing and not existing.endswith("\n"):
                existing += "\n"
            existing += ("\n" if existing else "") \
                + "# agent skill mirrors (added by skills-doctor --autofix)\n" \
                + "\n".join(missing) + "\n"
            gi.write_text(existing, encoding="utf-8")
            report.add("PASS", check,
                       f".gitignore 已补齐 {len(missing)} 条推荐条目",
                       _trunc(missing) + f" | 文件：{_abbr(str(gi))}")
            acted += 1
        except OSError as e:
            report.add("FAIL", check, f".gitignore 补齐失败：{e}")
            failed += 1

    for rel in discover_project_mirror_dirs(project_root):
        grel = to_git_rel(rel_prefix, rel)
        if not git_is_tracked(top, grel):
            continue
        code, _, err = _run_git(top, "rm", "-r", "--cached", "--", grel)
        if code == 0:
            report.add("PASS", check,
                       f"{rel}：已取消 git 跟踪（工作区文件未动）",
                       "待 git commit 生效；协作者 pull 后镜像目录会消失，"
                       "需用 rf-skill-sync 重建本地链接")
            acted += 1
        else:
            report.add("FAIL", check, f"{rel}：git rm -r --cached 失败：{err.strip()}")
            failed += 1

    if acted or failed:
        report.add("FAIL" if failed else "PASS", check,
                   f"项目 git 修复汇总：完成 {acted} 项"
                   + (f"，失败 {failed} 项" if failed else ""))
    else:
        report.add("PASS", check, ".gitignore 已覆盖推荐条目，无非源头 skill 目录被跟踪")


# ----------------------------- rendering -----------------------------
ORDER = ["store", "symlink", "consistency", "lock", "reconcile", "skill-md", "junk",
         "backups", "links", "consumer-links", "duplicate", "sot", "fix", "autofix"]

# 只影响人读的排版；JSON 的 check 字段仍是英文 slug
CHECK_LABEL = {
    "store": "存储目录",
    "symlink": "软链接",
    "consistency": "一致性",
    "lock": "lock 文件",
    "reconcile": "对账",
    "skill-md": "SKILL.md",
    "junk": "非 skill 条目",
    "backups": "force 备份",
    "links": "反向链接",
    "consumer-links": "消费端内链",
    "duplicate": "重名检查",
    "sot": "单一事实源",
    "fix": "修复",
    "autofix": "自动修复",
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
                    "或诊断某个项目的 .claude/skills（--project）。"
                    "--autofix 一键应用全部安全修复（含 --fix 与 --clean-backups）。",
    )
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {SKILL_VERSION}")
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
                        "（仅用户模式；默认 ~/.claude/skills,~/.zcode/skills,~/.cursor/skills）")
    p.add_argument("--agent-dirs", default=None,
                   help="以逗号分隔的用户级 Agent skill 目录（仅用户模式；默认 "
                        "~/.claude/skills,~/.zcode/skills,~/.codex/skills,~/.cursor/skills）。"
                        "整目录链消费端自动跳过，只体检真实目录型目录内部的逐 skill 软链接")
    p.add_argument("--lock", default=None,
                   help="skill 管理器的 lock 文件（用户模式默认 ~/.agents/.skill-lock.json；"
                        "项目模式在存在时使用 <project>/.claude/.skill-lock.json）")
    p.add_argument("--json", action="store_true", dest="as_json", help="输出机器可读的 JSON")
    p.add_argument("--fix", action="store_true",
                   help="重建损坏或指向错误的消费端软链接，使其指向 store")
    p.add_argument("--autofix", action="store_true", dest="autofix",
                   help="一键应用全部安全修复（含 --fix 与 --clean-backups 的行为）："
                        "重链消费端软链接、清理 lock 残留条目、对齐 SKILL.md name"
                        "（仅真实目录，不写穿软链接）、删除 force 备份、修复 Agent 目录"
                        "内失效逐 skill 链接；项目模式下补齐 .gitignore 并取消跟踪"
                        "非源头 skill 目录。不可决断的问题仅保留指引")
    p.add_argument("--clean-backups", action="store_true", dest="clean_backups",
                   help="删除 skills-link --force 产生的 *.bak-<时间戳> 备份"
                        "（同名 skill 已存在才删，否则视为唯一副本跳过）")
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
        if args.fix and not args.autofix:
            # 项目模式没有消费端软链接，--fix 无可修复对象（SKILL.md：no-op）；
            # 不调用 apply_fix，避免对不存在的 .claude/skills 误报 FAIL
            report.add("PASS", "fix", "项目模式下无消费端软链接，--fix 无操作")
        if args.clean_backups or args.autofix:
            clean_backups(store, report,
                          check="autofix" if args.autofix else "backups")
        if args.autofix:
            prune_lock_stale(store, lock, report)
            autofix_skill_md_names(store, report)
            autofix_project(project_root, report)
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
        if args.agent_dirs is not None:
            agent_dirs = [Path(d).expanduser() for d in args.agent_dirs.split(",") if d.strip()]
        else:
            agent_dirs = list(DEFAULT_AGENT_DIRS)
        report = Report()
        if args.fix or args.autofix:
            apply_fix(store, consumers, report,
                      check="autofix" if args.autofix else "fix")
        if args.clean_backups or args.autofix:
            clean_backups(store, report,
                          check="autofix" if args.autofix else "backups")
        if args.autofix:
            prune_lock_stale(store, lock, report)
            autofix_skill_md_names(store, report)
            autofix_agent_dir_links(store, agent_dirs, report)
        model = run(store, consumers, lock, report, agent_dirs=agent_dirs)
        sections.append((model, report, "用户级（全局 store）"))
        # auto project diagnosis for the cwd (skipped when absent, or when the
        # cwd's skills dir is the store itself — e.g. running from $HOME)
        if not args.no_project:
            cwd = Path.cwd()
            proj_skills = cwd / ".claude" / "skills"
            if proj_skills.is_dir() and proj_skills.resolve() != store.resolve():
                preport = Report()
                if args.clean_backups or args.autofix:
                    # 与显式 --project 分支一致：先修复再诊断，检查反映修复后状态
                    clean_backups(proj_skills, preport,
                                  check="autofix" if args.autofix else "backups")
                if args.autofix:
                    prune_lock_stale(proj_skills, cwd / ".claude" / ".skill-lock.json", preport)
                    autofix_skill_md_names(proj_skills, preport)
                    autofix_project(cwd, preport)
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
