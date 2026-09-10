---
name: rf-skill-init
description: "初始化项目的 skill 环境与 Agent 文档：以 .claude/skills 为基准，创建 .zcode/.codex 等目录并软链接，同时确保 CLAUDE.md 存在、AGENTS.md 链接到它。新项目搭建 skill 环境时使用，细节见正文。"
version: 1.0.0
---

# Skill 目录初始化

一键初始化项目的多 agent skill 目录结构与约定文档。以 `.claude/skills/` 为基准（唯一事实源，skill 只在这里维护），为 zcode 和 codex 创建项目级 skill 目录并建立相对路径软链接；同时确保 `CLAUDE.md` 存在，并将 `AGENTS.md` 软链接到 `CLAUDE.md`——一次初始化，多环境即时可用。

## 安装为全局命令（可选）

一键安装 `skills-init` 命令（alias），之后可在任意项目目录直接初始化；已初始化的项目自动跳过：

> 下面脚本路径中的 `{baseDir}` 指本 SKILL.md 所在目录（即本 skill 目录），运行时替换为实际路径。

```bash
bash {baseDir}/scripts/install.sh              # 安装/更新
bash {baseDir}/scripts/install.sh --uninstall  # 卸载
```

- 存在 `~/.zshrc`（或登录 shell 为 zsh）→ 写入 `~/.zshrc`；否则写入 `~/.bash_profile`
- 重复执行幂等：自动更新指向路径，不产生重复条目
- 安装后执行 `source` 配置文件（或新开终端）生效

## 使用

在 Claude 中直接触发 `/rf-skill-init`（在需要初始化的项目目录下），或运行脚本：

```bash
# 快捷命令（安装 alias 后，推荐）
skills-init                # 已初始化的项目自动跳过
skills-init --force        # 跳过检查，强制执行并输出完整报告

# 完整初始化：创建目录 + 软链接
bash {baseDir}/scripts/init.sh

# 预览模式，不做任何修改
bash {baseDir}/scripts/init.sh --dry-run

# 只创建目录与 agent 文档，不建 skill 软链接
bash {baseDir}/scripts/init.sh --dirs-only

# 自定义目标目录
bash {baseDir}/scripts/init.sh .codex/skills .cursor/skills
```

脚本可从项目任意子目录运行，会自动定位 git 仓库根目录（非 git 项目则用当前目录）。

## 行为

按顺序执行：

1. **Agent 文档**：`CLAUDE.md` 不存在时写入最小模板；`AGENTS.md` 不存在时软链接到 `CLAUDE.md`（已指向错误的软链接会修复；真实文件不覆盖）
2. **基准目录**：`.claude/skills/` 不存在时创建；已存在则保留不动
3. **目标目录**：创建 `.zcode/skills` 和 `.codex/skills`（已存在则跳过）
4. **Skill 软链接**：把基准目录下每个 skill 以相对路径 `../../.claude/skills/<name>` 链接到各目标目录（跨机器可用）

安全约束：

- **快速跳过**：默认参数下检测到已完整初始化（Agent 文档就绪、目录齐备、所有链接正确）时输出一行提示直接退出；`--force` 强制走完整流程
- **幂等**：重复运行安全——已正确的链接跳过，指向错误的自动修复
- **不覆盖真实内容**：目标中同名真实目录或普通文件跳过并警告，绝不覆盖（含已是真实文件的 `AGENTS.md`）
- **自排除**：不链接 `rf-skill-init` 自身
- **基准不可作目标**：`.claude/skills` 被指定为目标时跳过，避免自引用循环
- 基准目录为空时只建目录不建链接，并提示后续同步方式

## 与 rf-skill-sync 的分工

| Skill | 职责 | 时机 |
|---|---|---|
| `rf-skill-init` | Agent 文档 + 建目录 + 建立初始软链接 | 新项目一次性初始化 |
| `rf-skill-sync` | 新增/删除 skill 后重新同步、清理断连链接 | 日常维护 |

初始化之后往 `.claude/skills/` 添加新 skill 时，运行 `rf-skill-sync` 同步到各 agent 目录。

## 输出示例

```
=== Skill 目录初始化 ===
项目根: /path/to/project

▶ Agent 文档
  ＋ 创建 CLAUDE.md
  ＋ 链接 AGENTS.md → CLAUDE.md

＋ 创建基准目录: .claude/skills
＋ 创建目标目录: .zcode/skills
＋ 创建目标目录: .codex/skills

基准 skills (2):
  rf-first-principles
  my-skill

▶ 链接到: .zcode/skills
  链接: rf-first-principles
  链接: my-skill
  结果: +2 / ~0 / =0 (新增/修复/已存在)

▶ 链接到: .codex/skills
  链接: rf-first-principles
  链接: my-skill
  结果: +2 / ~0 / =0 (新增/修复/已存在)

=== 初始化完成 ===
```
