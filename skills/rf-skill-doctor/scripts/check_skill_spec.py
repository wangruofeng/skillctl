#!/usr/bin/env python3
"""check_skill_spec.py — 检查 skill 的 SKILL.md 规范性。

必填 frontmatter 字段：name、description、version。
额外检查（警告）：name 连字符小写、name 与目录名一致、version 形如 semver。

用法:
  python3 check_skill_spec.py                  # 自动发现 ./skills 或当前目录下的 skill
  python3 check_skill_spec.py path/to/skills   # 扫描目录下每个子 skill
  python3 check_skill_spec.py path/to/one      # 检查单个 skill 目录
  python3 check_skill_spec.py --json           # 机器可读输出
  python3 check_skill_spec.py -q               # 仅摘要 + 退出码

退出码: 0 全部通过 / 1 仅有警告 / 2 存在失败
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

REQUIRED_FIELDS = ("name", "description", "version")


@dataclass
class Finding:
    level: str  # PASS | WARN | FAIL
    code: str
    message: str


@dataclass
class SkillReport:
    path: str
    dir_name: str
    findings: list[Finding] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def worst(self) -> str:
        order = {"PASS": 0, "WARN": 1, "FAIL": 2}
        if not self.findings:
            return "PASS"
        return max(self.findings, key=lambda f: order[f.level]).level


def parse_frontmatter(text: str) -> tuple[dict | None, str | None]:
    """解析 SKILL.md frontmatter。优先 PyYAML，否则用扁平键回退。"""
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
        if line[0] not in (" ", "\t"):
            kv = _KV_RE.match(line)
            if kv:
                _flush()
                key, val = kv.group(1), kv.group(2).strip()
                if val:
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                        val = val[1:-1]
                    data[key] = val
                else:
                    current = key
                continue
        if current is not None:
            block.append(line.strip())
    _flush()
    return data, None


def _field_str(data: dict, key: str) -> str:
    val = data.get(key, "")
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        # YAML 可能把 1.0.0 误解析失败，或把 1 解析成 int
        return str(val).strip()
    return str(val).strip()


def check_skill(skill_dir: Path) -> SkillReport:
    report = SkillReport(path=str(skill_dir.resolve()), dir_name=skill_dir.name)
    sm = skill_dir / "SKILL.md"
    if not sm.is_file():
        report.findings.append(Finding("FAIL", "missing-skill-md", "缺少 SKILL.md"))
        return report

    try:
        text = sm.read_text(encoding="utf-8")
    except OSError as e:
        report.findings.append(Finding("FAIL", "unreadable", f"SKILL.md 无法读取：{e}"))
        return report

    data, err = parse_frontmatter(text)
    if err or data is None:
        report.findings.append(
            Finding("FAIL", "frontmatter", f"frontmatter 解析失败：{err}")
        )
        return report

    name = _field_str(data, "name")
    desc = _field_str(data, "description")
    version = _field_str(data, "version")
    report.fields = {"name": name, "description": desc, "version": version}

    # 必填
    if not name:
        report.findings.append(Finding("FAIL", "name", "缺少必填字段 name 或内容为空"))
    if not desc:
        report.findings.append(
            Finding("FAIL", "description", "缺少必填字段 description 或内容为空")
        )
    if not version:
        report.findings.append(
            Finding("FAIL", "version", "缺少必填字段 version 或内容为空")
        )

    # 格式警告（仅在字段存在时）
    if name:
        if not _NAME_RE.match(name):
            report.findings.append(
                Finding(
                    "WARN",
                    "name-format",
                    f"name '{name}' 不是连字符小写格式（建议 [a-z0-9-]+）",
                )
            )
        if name != skill_dir.name:
            report.findings.append(
                Finding(
                    "WARN",
                    "name-mismatch",
                    f"name '{name}' 与目录名 '{skill_dir.name}' 不一致",
                )
            )
    if desc and "TODO" in desc:
        report.findings.append(
            Finding("WARN", "description-todo", "description 中仍残留 TODO 占位符")
        )
    if version and not _SEMVER_RE.match(version):
        report.findings.append(
            Finding(
                "WARN",
                "version-format",
                f"version '{version}' 不符合 semver（建议 x.y.z）",
            )
        )

    if not report.findings:
        report.findings.append(
            Finding(
                "PASS",
                "ok",
                f"规范通过：name={name}  version={version}",
            )
        )
    return report


def discover_skills(paths: list[Path]) -> list[Path]:
    """从参数或默认位置发现 skill 目录。"""
    found: list[Path] = []

    def consider(p: Path) -> None:
        p = p.resolve()
        if not p.exists():
            return
        if p.is_file() and p.name == "SKILL.md":
            found.append(p.parent)
            return
        if p.is_dir() and (p / "SKILL.md").is_file():
            found.append(p)
            return
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.name.startswith("."):
                    continue
                if child.is_dir() and (child / "SKILL.md").is_file():
                    found.append(child)

    if paths:
        for p in paths:
            consider(p)
    else:
        cwd = Path.cwd()
        # 优先本仓库 skills/，其次 cwd 自身（可能是 skill 根或单 skill）
        for candidate in (cwd / "skills", cwd):
            before = len(found)
            consider(candidate)
            if len(found) > before:
                break

    # 去重并保持顺序
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(rp)
    return unique


def render_human(reports: list[SkillReport], quiet: bool = False) -> str:
    lines: list[str] = []
    fail = warn = ok = 0
    for r in reports:
        w = r.worst
        if w == "FAIL":
            fail += 1
        elif w == "WARN":
            warn += 1
        else:
            ok += 1

    lines.append("=== Skill 规范检查 ===")
    lines.append(
        f"扫描 {len(reports)} 个 skill  ·  通过 {ok}  ·  警告 {warn}  ·  失败 {fail}"
    )
    lines.append("")

    if quiet:
        # 安静模式只列非 PASS 的 skill
        for r in reports:
            if r.worst == "PASS":
                continue
            mark = "✗" if r.worst == "FAIL" else "！"
            lines.append(f"{mark} {r.dir_name}")
            for f in r.findings:
                if f.level == "PASS":
                    continue
                lines.append(f"    [{f.level}] {f.message}")
        if fail == 0 and warn == 0:
            lines.append("全部通过。")
        return "\n".join(lines)

    for r in reports:
        status = {"PASS": "✓", "WARN": "！", "FAIL": "✗"}[r.worst]
        lines.append(f"{status} {r.dir_name}")
        for f in r.findings:
            if f.level == "PASS" and len(r.findings) == 1:
                lines.append(f"    {f.message}")
            elif f.level != "PASS":
                lines.append(f"    [{f.level}] {f.message}")
        lines.append("")

    if fail:
        lines.append("结果: 未通过（存在失败项）")
    elif warn:
        lines.append("结果: 通过（带警告）")
    else:
        lines.append("结果: 全部通过")
    return "\n".join(lines)


def exit_code(reports: list[SkillReport]) -> int:
    worst = 0
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    for r in reports:
        worst = max(worst, order[r.worst])
    return worst  # 0 / 1 / 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查 skill 的 SKILL.md 规范性（必填 name / description / version）"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="skill 目录、含多个 skill 的父目录，或 SKILL.md 路径（默认自动发现）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("-q", "--quiet", action="store_true", help="仅输出摘要与问题项")
    args = parser.parse_args(argv)

    skills = discover_skills(list(args.paths))
    if not skills:
        msg = "未发现任何 skill（目录下需有 SKILL.md）"
        if args.json:
            print(json.dumps({"ok": False, "error": msg, "skills": []}, ensure_ascii=False))
        else:
            print(f"错误: {msg}", file=sys.stderr)
        return 2

    reports = [check_skill(p) for p in skills]

    if args.json:
        payload = {
            "ok": exit_code(reports) == 0,
            "exit_code": exit_code(reports),
            "required_fields": list(REQUIRED_FIELDS),
            "summary": {
                "total": len(reports),
                "pass": sum(1 for r in reports if r.worst == "PASS"),
                "warn": sum(1 for r in reports if r.worst == "WARN"),
                "fail": sum(1 for r in reports if r.worst == "FAIL"),
            },
            "skills": [
                {
                    "path": r.path,
                    "dir_name": r.dir_name,
                    "status": r.worst,
                    "fields": r.fields,
                    "findings": [asdict(f) for f in r.findings],
                }
                for r in reports
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_human(reports, quiet=args.quiet))

    return exit_code(reports)


if __name__ == "__main__":
    sys.exit(main())
