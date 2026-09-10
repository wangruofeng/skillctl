# skillctl 项目约定

本项目是为 AI Agent 提供的工具集控制中心。

## 目录结构

```
skillctl/
├── skills/           ← 核心工具 skills
│   ├── rf-skill-init/    ← 环境初始化工具
│   ├── rf-skill-sync/    ← 跨环境同步工具
│   ├── rf-skill-installer/ ← 快速安装工具
│   ├── rf-commit-push/    ← Git 自动化工具
│   └── rf-skill-doctor/   ← 状态诊断工具
├── docs/             ← 文档
├── AGENTS.md -> CLAUDE.md  ← 兼容入口（软链接）
├── CLAUDE.md         ← 项目约定（本文件）
├── README.md         ← 中文主文档
└── LICENSE           ← MIT License
```

## 规则

- **保持工具属性**：本项目仅存放 Claude 基础架构相关的工具类 Skill。
- **命名**：
  - Skill 目录 / `name`：`rf-` 前缀；管理类用单数 `rf-skill-<action>`（如 `rf-skill-init`、`rf-skill-sync`）；纯动作类直接 `rf-<action>`（如 `rf-commit-push`）。
  - CLI alias：集合名词用复数产品前缀 `skills-<action>`（如 `skills-init`、`skills-sync`），与 `.claude/skills`、`npx skills` 对齐；不必与 skill 名字符串相同。
- **SKILL.md 格式**：每个 skill 必须有 YAML frontmatter，`name`、`description`、`version` 为必填字段。
- **脚本位置**：可执行脚本一律放 `scripts/` 子目录，SKILL.md 用 `{baseDir}` 指代路径。
- **隔离**：脚本逻辑应尽量减少对特定文件系统的依赖，通过参数传入路径。
- **软链接同步**：全局安装路径为 `~/.agents/skills/`，通过软链接指向本项目中的 `skills/` 目录。

## 常用命令

- **安装全局 alias**: `bash skills/rf-skill-init/scripts/install.sh` / `bash skills/rf-skill-sync/scripts/install.sh` / `bash skills/rf-skill-doctor/scripts/install.sh`
- **初始化新项目**: `skills-init` 或 `/rf-skill-init`
- **同步项目 Skill**: `skills-sync` 或 `/rf-skill-sync`
- **诊断 skill 健康**: `skills-doctor` 或 `/rf-skill-doctor`