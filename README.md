# skillctl

> Claude Skills 控制中心：初始化项目环境、同步跨平台配置、快速安装第三方 Skill。

[English](README.en.md) · 中文

---

## 核心工具

本项目包含以下核心工具类 Skill，用于优化和自动化 Claude 的 Skill 使用流程：

| Skill | 用途 | 核心原理 |
| --- | --- | --- |
| [rf-skill-init](skills/rf-skill-init/SKILL.md) | 环境初始化 | **基座模式**：以 `.claude/skills/` 为唯一事实源，自动为 zcode/codex 创建软链接环境。 |
| [rf-skill-sync](skills/rf-skill-sync/SKILL.md) | 跨环境同步 | **即时同步**：通过软链接保持项目内所有 Agent 的 Skill 目录一致，自动清理失效链接。 |
| [rf-skill-installer](skills/rf-skill-installer/SKILL.md) | 快速安装 | **一键部署**：从 GitHub URL 自动生成 `npx skills add` 命令并检查安装依赖。 |
| [rf-commit-push](skills/rf-commit-push/SKILL.md) | Git 自动化 | **原子操作**：分析 Diff 生成规范 Commit Message，并一口气完成暂存、提交与推送。 |
| [rf-skill-doctor](skills/rf-skill-doctor/SKILL.md) | 状态诊断 | **健康检查**：扫描单库多环境（Single-store）的链接完整性、Lock 文件一致性与目录规范。 |
| [rf-skill-link](skills/rf-skill-link/SKILL.md) | 全局链接 | **仓库汇聚**：将仓库 `skills/` 下所有 skill 软链接到统一目录（默认 `~/.agents/skills`），多仓库 skill 一处汇聚。 |

## 使用场景

### 1. 新项目初始化
当你在新项目目录下需要使用 Skill 时：
- 触发：`/rf-skill-init`
- 效果：创建 `.claude/skills` 基准目录，并与 `.zcode/skills`、`.codex/skills` 建立关联。

### 2. 日常同步
当你在 `.claude/skills` 下新增、删除或更新了 Skill 时：
- 触发：`/rf-skill-sync`
- 效果：自动更新所有 Agent 目录下的软链接。

### 3. 安装第三方 Skill
当你看到一个不错的 GitHub Skill 仓库时：
- 触发：`/rf-skill-installer` 并提供 URL
- 效果：生成推荐的安装命令（项目级或全局）。

### 4. 快速提交代码
完成一个阶段性开发任务后：
- 触发：`/rf-commit-push` 或 `git 提交代码`
- 效果：自动分析改动并推送到远端，无需繁琐的 Git 命令。

### 5. 故障排查
当发现 Skill 没生效或目录混乱时：
- 触发：`/rf-skill-doctor` 或 `skill 健康检查`
- 效果：定位断连的软链接或不符合规范的 `SKILL.md` 并提供修复建议。

### 6. 汇聚仓库 Skill 到全局目录
当你维护着多个 skill 仓库、希望统一暴露到一个全局目录时：
- 触发：`/rf-skill-link`（在仓库根目录）
- 效果：仓库 `skills/` 下所有 skill 以软链接进入 `~/.agents/skills`，仓库更新即时生效；删除 skill 后重跑即自动清理。

## 安装

### 1. 安装本仓库 Skill

通过 [skills CLI](https://github.com/vercel-labs/skills) 一键安装（推荐）：

```bash
# 查看本仓库可用 skill
npx skills add wangruofeng/skillctl --list

# 项目级安装到 Claude Code（推荐：仅当前项目 + 自动确认）
npx skills add wangruofeng/skillctl -a claude-code -y

# 项目级安装（写入当前项目 .claude/skills/）
npx skills add wangruofeng/skillctl

# 全局安装（所有项目可用）
npx skills add wangruofeng/skillctl -g

# 全局安装到 Claude Code
npx skills add wangruofeng/skillctl -g -a claude-code -y
```

只装某一个 skill 时，可加 `--skill <name>`，例如：

```bash
npx skills add wangruofeng/skillctl --skill rf-commit-push -a claude-code -y
```

> 默认推荐「项目级 + Claude Code」：skill 跟随项目、不污染全局。仅在需要跨项目复用时再选 `-g`。

### 2. 从源码本地使用

```bash
git clone https://github.com/wangruofeng/skillctl.git
cd skillctl
```

运行 `/rf-skill-link`（或 `bash skills/rf-skill-link/scripts/link.sh`）把 `skills/` 下各 skill 软链到 `~/.agents/skills/`，仓库内更新即时生效；也可链到项目的 `.claude/skills/`，再用 `/rf-skill-sync` 同步到其他 Agent 目录。

### 3. 安装全局 CLI 命令（可选）

方便在终端直接调用 `skills-init` / `skills-sync` / `skills-doctor` / `skills-link`：

```bash
# 安装同步工具 → skills-sync
bash skills/rf-skill-sync/scripts/install.sh

# 安装初始化工具 → skills-init
bash skills/rf-skill-init/scripts/install.sh

# 安装诊断工具 → skills-doctor
bash skills/rf-skill-doctor/scripts/install.sh

# 安装全局链接工具 → skills-link
bash skills/rf-skill-link/scripts/install.sh
```

安装后执行 `source ~/.zshrc`（或新开终端）即可使用。卸载加 `--uninstall`。

## 贡献与 License

[MIT](LICENSE) © 2026 wangruofeng
