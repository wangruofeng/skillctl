# skillctl

> Claude Skills Control Center: Initialize project environments, synchronize cross-platform configurations, and quickly install third-party Skills.

---

## Core Tools

This project contains the following core utility Skills to optimize and automate the Claude Skill workflow:

| Skill | Purpose | Core Principle |
| --- | --- | --- |
| [rf-skill-init](skills/rf-skill-init/SKILL.md) | Env Initialization | **Baseline Mode**: Uses `.claude/skills/` as the single source of truth, automatically creating symlink environments for zcode/codex. |
| [rf-skill-sync](skills/rf-skill-sync/SKILL.md) | Cross-env Sync | **Instant Sync**: Maintains consistency across all Agent Skill directories via symlinks and auto-cleans broken links. |
| [rf-skill-installer](skills/rf-skill-installer/SKILL.md) | Quick Install | **One-click Deploy**: Automatically generates `npx skills add` commands from GitHub URLs and checks for dependencies. |
| [rf-commit-push](skills/rf-commit-push/SKILL.md) | Git Automation | **Atomic Operations**: Analyzes diffs to generate conventional commit messages and completes stage, commit, and push in one go. |
| [rf-skill-doctor](skills/rf-skill-doctor/SKILL.md) | Health Check | **Diagnostics**: Scans the single-store, multi-consumer model for link integrity, lock-file consistency, and directory standards. |
| [rf-skill-link](skills/rf-skill-link/SKILL.md) | Global Link | **Repo Aggregation**: Symlinks all skills under the repo's `skills/` into a single directory (default `~/.agents/skills`), aggregating skills from multiple repos in one place. |

## Usage Scenarios

### 1. New Project Initialization
When you need to use Skills in a new project:
- Trigger: `/rf-skill-init`
- Effect: Creates the `.claude/skills` baseline directory and links it to `.zcode/skills` and `.codex/skills`.

### 2. Routine Synchronization
When you add, delete, or update Skills in `.claude/skills`:
- Trigger: `/rf-skill-sync`
- Effect: Automatically updates symlinks in all Agent directories.

### 3. Installing Third-party Skills
When you find a great GitHub Skill repository:
- Trigger: `/rf-skill-installer` with the URL.
- Effect: Generates recommended installation commands (Project-level or Global).

### 4. Rapid Code Commitment
After completing a development milestone:
- Trigger: `/rf-commit-push` or "git commit code".
- Effect: Automatically analyzes changes and pushes to the remote without tedious Git commands.

### 5. Troubleshooting
When a Skill is not working or the directory structure is messy:
- Trigger: `/rf-skill-doctor` or "skill health check".
- Effect: Identifies broken symlinks or non-compliant `SKILL.md` files and provides repair suggestions.

### 6. Aggregating Repo Skills into a Global Directory
When you maintain multiple skill repos and want them exposed through one global directory:
- Trigger: `/rf-skill-link` (at the repo root).
- Effect: Every skill under `skills/` is symlinked into `~/.agents/skills`; repo updates take effect instantly, and removed skills are cleaned up on the next run.

## Installation

### 1. Natural Language Install (Recommended)

Just tell Claude Code what you need in plain language — Claude handles the installation for you:

```text
# Install only the skill suite
Install the skills from this repo for me: https://github.com/wangruofeng/skillctl

# Install both the skill suite and the companion CLI commands
Install https://github.com/wangruofeng/skillctl for me — set up the skills along with the CLI commands like skills-init / skills-sync
```

| Scope | What Claude does | What you get |
| --- | --- | --- |
| Skills only | Installs via `npx skills add` (commands in the next section) | All skills (`/rf-skill-init`, `/rf-commit-push`, ...) available in sessions |
| Skills + CLI | Clones this repo locally, links the skills, and runs each `install.sh` (sections 3 & 4) | Additionally the terminal commands `skills-init` / `skills-sync` / `skills-doctor` / `skills-link`; update via `git pull` |

> CLI commands are aliases pointing at the local clone — keep it in a fixed directory for long-term use.

### 2. Install Skills from This Repo

Install with the [skills CLI](https://github.com/vercel-labs/skills) (recommended):

```bash
# List available skills in this repo
npx skills add wangruofeng/skillctl --list

# Project-level install for Claude Code (recommended: current project + auto-confirm)
npx skills add wangruofeng/skillctl -a claude-code -y

# Project-level install (into .claude/skills/)
npx skills add wangruofeng/skillctl

# Global install (available in all projects)
npx skills add wangruofeng/skillctl -g

# Global install for Claude Code
npx skills add wangruofeng/skillctl -g -a claude-code -y
```

To install a single skill, pass `--skill <name>`:

```bash
npx skills add wangruofeng/skillctl --skill rf-commit-push -a claude-code -y
```

> Prefer project-level + Claude Code by default so skills stay with the project. Use `-g` only when you need cross-project reuse.

### 3. Use from Source

```bash
git clone https://github.com/wangruofeng/skillctl.git
cd skillctl
```

Run `/rf-skill-link` (or `bash skills/rf-skill-link/scripts/link.sh`) to symlink every skill under `skills/` into `~/.agents/skills/` — repo updates take effect instantly. You can also link into a project's `.claude/skills/`, then run `/rf-skill-sync` to sync other Agent directories.

### 4. Install Global CLI Commands (Optional)

Expose `skills-init` / `skills-sync` / `skills-doctor` / `skills-link` in your shell:

```bash
# Install sync tool → skills-sync
bash skills/rf-skill-sync/scripts/install.sh

# Install init tool → skills-init
bash skills/rf-skill-init/scripts/install.sh

# Install doctor tool → skills-doctor
bash skills/rf-skill-doctor/scripts/install.sh

# Install global link tool → skills-link
bash skills/rf-skill-link/scripts/install.sh
```

Then `source ~/.zshrc` (or open a new terminal). Pass `--uninstall` to remove.

## Contribution & License

[MIT](LICENSE) © 2026 wangruofeng
