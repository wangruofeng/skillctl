---
name: rf-skill-doctor
description: "诊断本地 skill 管理健康（全局仓与项目 .claude/skills）：软链接完整性、锁文件一致性、SKILL.md 规范检查、skills-link --force 备份检测与清理（--clean-backups）、一键安全修复（--autofix）。用于 skill 健康检查/诊断、修复 skill 问题、排查 skill 没生效/没同步/找不到、删除 .bak 备份。用法、参数与修复见正文。"
version: 1.3.0
---

# Skill Doctor

Diagnose the local **single-store, multi-consumer** skill model. All paths are auto-detected and overridable, so the tool works on any equivalent layout, not just the one below.

- **Store** (source of truth, real dir): `~/.agents/skills`
- **Consumers** (mirrors via symlink): `~/.claude/skills`, `~/.zcode/skills`, `~/.cursor/skills` → store
- **Lock file** (skill manager): `~/.agents/.skill-lock.json`

Project directories (`.claude/skills` inside a git repo) are a *different* model — but the default run diagnoses both: the global store first, then the cwd's project skills appended as a second section (see [Project mode](#project-mode---project)).

## 安装为全局命令（可选）

一键安装 `skills-doctor` 命令（alias），之后可在任意目录直接诊断：

> 下面脚本路径中的 `{baseDir}` 指本 SKILL.md 所在目录（即本 skill 目录），运行时替换为实际路径。

```bash
bash {baseDir}/scripts/install.sh              # 安装/更新
bash {baseDir}/scripts/install.sh --uninstall  # 卸载
```

- 存在 `~/.zshrc`（或登录 shell 为 zsh）→ 写入 `~/.zshrc`；否则写入 `~/.bash_profile`
- 重复执行幂等：自动更新指向路径，不产生重复条目
- 安装后执行 `source` 配置文件（或新开终端）生效

## Run

```bash
python3 scripts/skill_doctor.py            # global store + auto project check (cwd)
python3 scripts/skill_doctor.py --json     # machine-readable JSON
python3 scripts/skill_doctor.py --fix      # relink broken consumer symlinks -> store
python3 scripts/skill_doctor.py --autofix  # 一键应用全部安全修复（含 --fix / --clean-backups）
python3 scripts/skill_doctor.py --clean-backups  # 删除 skills-link --force 备份
python3 scripts/skill_doctor.py --version  # 查看版本（与 SKILL.md frontmatter 同步）

# SKILL.md 规范检查（必填 name / description / version）
python3 scripts/check_skill_spec.py        # 自动发现 ./skills
python3 scripts/check_skill_spec.py path   # 指定目录或单个 skill
python3 scripts/check_skill_spec.py --json
```

From any cwd, point at the script directly:

```bash
python3 ~/.agents/skills/rf-skill-doctor/scripts/skill_doctor.py
python3 ~/.agents/skills/rf-skill-doctor/scripts/check_skill_spec.py
```

When invoked as a skill, run `scripts/skill_doctor.py` with no arguments first — it already covers the current project. Use `--json` only when feeding another tool, and `--fix` / `--autofix` only when the user asks to repair. `--project` runs *only* the project part; `--no-project` restricts a default run to the global store. For frontmatter-only linting (name/description/version), run `scripts/check_skill_spec.py`.

## What it checks

1. **Topology** — which path is the real store vs a symlink, each link's target, and inode.
2. **Symlink integrity** — every consumer must be a symlink whose target exists and resolves to the store. States: `ok` / `missing` / `dangling` / `not-a-link (real dir)` / `stray-target`.
3. **Store consistency** — all consumers resolve to the *same inode* as the store, proving one source of truth with no silent copies or desync.
4. **Lock-file reconciliation** — compares `~/.agents/.skill-lock.json` against folders on disk:
   - **orphans** = skill folder on disk but not tracked by the manager
   - **missing/stale** = tracked by the manager but no folder on disk
5. **Per-skill validity** — each skill folder has a `SKILL.md` whose frontmatter parses and has non-empty `name` (hyphen-case) and `description`; `version` is optional (shown when present, never fails/warns if absent). `name` should match the directory name. Also flags non-skill junk entries (dotfile metadata like `.DS_Store` / manifests are ignored). For a stricter lint that **requires** `version`, use `scripts/check_skill_spec.py`.
6. **Symlinked skill sources** — skill dirs inside the scanned dir that are themselves symlinks (reverse-link pattern, e.g. source lives in a separate repo). Healthy links pass and are surfaced; dangling ones fail. This is how a "real dir" store turns out not to be fully self-contained.
7. **force backups** — leftover `*.bak-<14-digit timestamp>` entries created by `skills-link --force` (dir or file). WARN when found: they hold a stale copy and, because they contain a `SKILL.md`, agents load them as duplicate skills (they are excluded from the skill count / SKILL.md checks). Clean up with `--clean-backups`, which only deletes a backup whose same-named skill currently exists; if the name is gone the backup may be the only copy, so it is skipped with guidance.
8. **consumer-dir per-skill links** (user mode) — real-dir agent skill dirs (`~/.codex/skills`, `~/.cursor/skills`, …) that can't be whole-dir symlinked (system skills must coexist) hold per-skill symlinks of their own; every one of them is checked for dangling targets. Whole-dir-symlink consumers (`~/.claude/skills`, `~/.zcode/skills`) are skipped — they follow their target and their link health is already covered by check 2. Override the scanned dirs with `--agent-dirs`.

## Project mode (--project)

```bash
python3 scripts/skill_doctor.py --project            # diagnose ONLY <cwd>/.claude/skills
python3 scripts/skill_doctor.py --project ~/code/foo # diagnose another project
python3 scripts/skill_doctor.py --project --json     # machine-readable
```

The **default run appends the same project diagnosis for the cwd automatically** whenever `<cwd>/.claude/skills` exists and is not the global store itself (running from `$HOME` is skipped — `~/.claude/skills` *is* the store there). `--no-project` disables the auto section. In `--json` output the auto section appears under a top-level `"project"` key.

Project skills are **git-managed and their own source of truth** — no consumer symlink topology or lock file is expected:

- A missing project lock (`<project>/.claude/.skill-lock.json`) is a PASS ("git is the source of truth; reconciliation skipped"), not a warning. If a project lock *does* exist it is reconciled like the global one.
- Checks 1/5/6 from above run against `<project>/.claude/skills` (SKILL.md validity, junk, reverse symlinks).
- **Cross-store duplicates** (project mode only): a skill name that exists in both the project and the global store. If both resolve to the same path it's one source (symlinked, PASS); two real copies are a **dual source of truth** (WARN) with a drift summary — how many files differ / exist only on one side. Use `--store` to point the cross-reference at a non-default global store.
- **Single source of truth / .gitignore** (project mode only, when the project is a git repo): `.claude/skills` is the only skill tree that should be versioned. Existing non-source agent dirs (`.zcode/skills`, `.codex/skills`, `.cursor/skills`, `.agents/skills`, plus any other discovered `.<agent>/skills`) must be covered by `.gitignore` — otherwise WARN; if already tracked by git, FAIL. Common mirror paths that do not exist yet but are not ignored get a softer WARN so rules can be pre-seeded. If the project is not under git, this check is skipped (PASS). Recommended ignore entries: `.codex/`, `.cursor/`, `.zcode/`, `.agents/`.

## Read the report

The human-readable report is printed in Simplified Chinese; the `--json` payload keeps English keys and the English `check` slug. Each scope gets a header with its path lines and a right-aligned tally, then one aligned `检查项 | 状态 | 消息` row per finding, grouped by check, with details dimmed underneath. `$HOME` is abbreviated to `~`, and a detail list longer than 8 lines is truncated with a `…（其余 N 行见 --json）` marker. Exit codes are script-friendly: `0` healthy, `1` warnings only, `2` one or more failures.

Common interpretations:
- A **stray/dangling consumer** (FAIL) means that agent (Claude Code / Zcode) is reading a different or broken skill set — relink with `--fix`.
- A **real-dir consumer** (WARN) is an independent store that will silently desync from the main one.
- **Orphans** are usually fine (hand-added skills the manager doesn't know about) but won't survive a manager-driven reinstall. **Stale** lock entries usually mean skills were removed from disk but the lock file wasn't updated.
- **skill-md failures** point at the exact skill whose `SKILL.md` is broken — fix that skill's frontmatter. A name≠dir or non-hyphen-case `name` is only a WARN.
- **links findings** show skills whose real source lives outside the scanned dir — intended (relocate pattern) but remember the store alone can't restore them.
- **duplicate warnings** (project sections) mean the same skill is maintained in two places; keep one and delete or symlink the other before the copies drift further.
- In a **default run with two sections**, findings and exit code are combined — the final tally is the sum of both scopes, and the exit code is the worst of them.

## Repairs (--fix) and cleanup (--clean-backups)

`--fix` performs only the **safe, deterministic** repair: recreate a consumer symlink that is missing, dangling, or pointing elsewhere so it targets the store. It **never** deletes a real directory (it may hold real skills) — it prints guidance instead. After `--fix`, re-run without the flag to confirm a clean report. In project mode there are no consumers, so `--fix` is a no-op.

`--clean-backups` removes `skills-link --force` backups (`<name>.bak-<timestamp>`, user store and project `.claude/skills` alike). A backup is deleted only when the same-named skill exists at the same level (the link has taken over, the backup is obsolete); otherwise it is kept as the possible only copy and reported for manual handling.

## Autofix (--autofix)

`--autofix` applies **every safe, deterministic repair in one shot** — it subsumes `--fix` and `--clean-backups` (combining the flags is harmless: each action runs exactly once). All repairs run *before* the diagnosis, so the report reflects the post-fix state. Repairs, per scope:

1. **Consumer symlink relink** — same as `--fix`.
2. **Lock stale-entry pruning** — removes `skills` keys from the lock JSON whose skill dirs no longer exist on disk. The lock is backed up first as `<lock>.bak-<14-digit-ts>` (next to the lock, outside the store); other fields (`version` …) and key order are preserved.
3. **SKILL.md name alignment** — when frontmatter `name` ≠ directory name, rewrites `name:` to the directory name (single-line, byte-precise replacement inside the frontmatter block; CRLF preserved). **Real directories only** — symlinked skill dirs (reverse-link pattern) are never written through, because the store link name is not guaranteed to equal the source dir name; they get a guidance WARN with the source path instead. The original file is backed up as `SKILL.md.bak-<ts>` next to it.
4. **force-backup cleanup** — same as `--clean-backups`.
5. **Dangling per-skill links in real agent dirs** — inside real-dir agent dirs (`~/.codex/skills`, …): a dangling link whose same-named skill exists in the store is relinked to it; one whose target is gone *and* has no store sibling is removed (no data can be lost — the target no longer exists).
6. **Project-mode git repairs** (explicit `--project` and the auto cwd project section alike) — first appends missing recommended entries (`.codex/`, `.cursor/`, `.zcode/`, `.agents/`) to the repo-toplevel `.gitignore` (idempotent; entries already covered by other ignore patterns are detected via `git check-ignore` and skipped), then untracks any tracked non-source skill dir with `git rm -r --cached` (working-tree files untouched; the staged removal takes effect on commit, and collaborators lose the mirror dirs on pull — they rebuild via rf-skill-sync).

**Never touched** (guidance-only, these are the WARN/FAIL items that remain after `--autofix`): real-dir consumers, invalid lock JSON, dual-source duplicate copies, junk entries, a source-ignored `.claude/skills`, symlinked-dir name mismatches (reason above), and FAIL-level SKILL.md problems (missing frontmatter / `name` / `description` — synthesizing content is out of scope).

**Idempotent**: every action's trigger predicate (link already correct, no stale keys, name already equal, no `.bak`, entries already present, already untracked) is false on a second run — re-running yields PASS no-ops only. Backups (`.bak-<ts>` beside the lock and each edited `SKILL.md`) are yours to delete after verifying.

`--version` prints the script version; keep `SKILL_VERSION` in the script and this file's frontmatter `version` in sync (same commit).

## Overrides

```text
--project [PATH]     diagnose ONLY <PATH>/.claude/skills (default cwd); project mode
--no-project         skip the automatic cwd project check in the default run
--store PATH         user mode: real store dir; project mode / auto project section:
                     cross-reference store (default ~/.agents/skills)
--consumers a,b,c    consumer paths, user mode only (default ~/.claude/skills,~/.zcode/skills,~/.cursor/skills)
--agent-dirs a,b,c   user-level agent skill dirs scanned for internal per-skill symlink
                     health, user mode only (default ~/.claude/skills,~/.zcode/skills,
                     ~/.codex/skills,~/.cursor/skills); whole-dir-symlink dirs are skipped
--lock PATH          lock file (user mode; default ~/.agents/.skill-lock.json; project mode
                     always uses <project>/.claude/.skill-lock.json)
```
