---
name: rf-skill-link
description: "将 skill 仓库（skills/、.claude/skills/、根下平铺、或仓库根本身即单个 skill）下的所有 skill 软链接到统一目录（默认 ~/.agents/skills），多仓库 skill 一处汇聚。用于把仓库下的 skill 链接到用户目录/全局 skill 目录，如「把 XX 仓库的 skill 链接到 ~/.agents/skills」「把 web-access 链接到全局」。幂等可重跑，自动清理指向本仓库的失效链接。用法与参数见正文。"
version: 1.3.0
---

# Skill 全局链接

把一个 skill 仓库下的所有 skill 以软链接方式汇聚到统一目录（默认 `~/.agents/skills`）。**仓库是唯一事实源，全局目录只持有链接**——仓库内更新即时生效；仓库内删除 skill 后重跑本工具即可清理全局链接。

与 `rf-skill-init` / `rf-skill-sync` 的方向不同：那两个处理**项目内** `.claude/skills` → 其他 agent 目录；本工具处理**仓库 → 用户全局目录**（见文末分工表）。

## 安装为全局命令（可选）

一键安装 `skills-link` 命令（alias），之后可在任意仓库目录直接链接：

> 下面脚本路径中的 `{baseDir}` 指本 SKILL.md 所在目录（即本 skill 目录），运行时替换为实际路径。

```bash
bash {baseDir}/scripts/install.sh              # 安装/更新
bash {baseDir}/scripts/install.sh --uninstall  # 卸载
```

- 存在 `~/.zshrc`（或登录 shell 为 zsh）→ 写入 `~/.zshrc`；否则写入 `~/.bash_profile`
- 重复执行幂等：自动更新指向路径，不产生重复条目
- 安装后执行 `source` 配置文件（或新开终端）生效

## 使用

```bash
# 自动探测源目录，链接到 ~/.agents/skills（推荐）
bash {baseDir}/scripts/link.sh

# 预览模式，不做任何修改
bash {baseDir}/scripts/link.sh --dry-run

# 指定目标目录
bash {baseDir}/scripts/link.sh ~/my-skills

# 指定源目录（不依赖自动探测）
bash {baseDir}/scripts/link.sh --source ~/code/foo/skills --target ~/.agents/skills

# 摘除本仓库在目标目录中的所有链接
bash {baseDir}/scripts/link.sh --remove

# 强制覆盖目标中同名的真实目录/文件（原内容备份为 *.bak-<时间戳>）
bash {baseDir}/scripts/link.sh --force
```

作为 skill 触发时（`/rf-skill-link` 或「把仓库 skill 链接到全局目录」），在目标仓库根目录直接运行无参命令即可；用户指定了其他目标目录时传位置参数。不确定时先跑 `--dry-run` 预览。

## 行为

- **自动探测源目录**：git 仓库根下 `skills/`，其次 `.claude/skills/`，再次 git 根 `SKILL.md`（单 skill 仓库，如 web-access，仓库根本身就是一个 skill，整体作为一个链接、链接名取目录名），最后回退到 git 仓库根一级目录（skill 直接平铺在根下的仓库，如 khazix-skills；要求根下存在含 `SKILL.md` 的一级子目录）；都不匹配或不在 git 仓库时，用 `--source` 显式指定（`--source` 指向的目录本身含 `SKILL.md` 时同样按单 skill 仓库处理）
- **仅链接合法 skill**：源目录下只有包含 `SKILL.md` 的一级子目录才算 skill；其余（无 `SKILL.md` 的目录）列出并忽略
- **绝对路径软链接**：与 `~/.agents/skills` 的现有约定一致，目标目录里的链接可读性好；同项目内的相对链接场景请用 `rf-skill-sync`
- **幂等**：指向正确的链接跳过；指向错误的自动修复；重复运行安全
- **不覆盖真实内容**：目标中同名真实目录或普通文件默认跳过并警告；加 `--force`（`-f`）强制覆盖——原内容先重命名为 `<名字>.bak-<时间戳>` 保留在目标目录同级，确认无误后可手动删除，可配合 `--dry-run` 先预览
- **清理只针对本仓库**：仅删除指向本仓库、但源 skill 已不存在的失效链接；目标目录中其他来源的链接与真实目录一律不动（全局目录是多仓库共享的）
- **自引用保护**：源目录与目标目录相同时报错退出
- **`--remove` 摘除**：删除目标目录中所有指向本仓库的链接，把仓库从全局目录摘除

## 与 rf-skill-init / rf-skill-sync 的分工

| Skill | 方向 | 时机 |
|---|---|---|
| `rf-skill-init` | 项目内 `.claude/skills` → 项目内其他 agent 目录 | 新项目一次性初始化 |
| `rf-skill-sync` | 项目内 `.claude/skills` → 项目内其他 agent 目录 | 日常增删 skill 后同步 |
| `rf-skill-link` | 仓库 `skills/` → 用户全局目录（默认 `~/.agents/skills`） | 把仓库的 skill 汇聚到全局 |

## 输出示例

```
=== Skill 全局链接 ===
源目录: /Users/rd/Documents/ai/personal/skills/skillctl/skills
目标:   ~/.agents/skills

源 skills (5):
  rf-commit-push
  rf-skill-doctor
  rf-skill-init
  rf-skill-installer
  rf-skill-link

▶ 链接到: /Users/rd/.agents/skills
  新增: rf-commit-push
  新增: rf-skill-doctor
  新增: rf-skill-init
  新增: rf-skill-installer
  新增: rf-skill-link
  结果: +5 / ~0 / -0 / =0 (新增/修复/删除/已存在)

=== 链接完成 ===
```
