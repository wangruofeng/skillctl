---
name: rf-skill-sync
description: "通过软链接将 .claude/skills 同步到其他 agent 目录（.codex/skills、.cursor/skills 等），保持跨环境 skill 即时同步。"
version: 1.0.0
---

# Skill 同步工具

通过软链接将 `.claude/skills/` 下的所有 skill 同步到其他 agent 目录。一次同步，所有环境即时生效。

## 安装为全局命令（可选）

一键安装 `skills-sync` 命令（alias），之后可在任意项目目录直接同步：

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
# 自动扫描并同步（推荐）
/rf-skill-sync

# 预览模式
bash {baseDir}/scripts/sync.sh --dry-run

# 自动识别模式
bash {baseDir}/scripts/sync.sh

# 手动指定目标
bash {baseDir}/scripts/sync.sh .codex/skills .cursor/skills
```

## 行为

- **源目录守卫**：`.claude/skills/` 不存在时直接报错退出，不做任何修改
- **自动发现目标**：仅扫描项目根目录下的一级 `.<agent>/skills/` 目录，跳过更深层嵌套（如 `.git/modules/.claude/skills`）与 `.git/` 内部目录，避免误同步污染 git 子模块镜像库
- **相对路径软链接**：`../../.claude/skills/xxx`，跨机器可用
- **自排除**：不同步 `rf-skill-sync` 自身
- **自动清理**：删除指向已不存在 skill 的断连软链接
- **不覆盖真实目录**：检测到真实目录时跳过并警告

详细策略：`references/policy.md`
检查清单：`references/checklist.md`
接口定义：`agents/interface.yaml`
质量评估：`evals/`
风险报告：`reports/output-risk-profile.md`

## 输出示例

```
=== Skill 同步 ===
源目录: .claude/skills/
目标: .codex/skills .cursor/skills

源 skills (5):
  rf-style-blog
  rf-wx-to-md
  ...

▶ 处理目标: .codex/skills
  新增: rf-style-blog
  跳过: rf-wx-to-md
  删除: old-skill (源 skill 已不存在)
  结果: +1 / -1 / =1 (新增/删除/已存在)

=== 同步完成 ===
```
