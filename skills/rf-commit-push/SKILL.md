---
name: rf-commit-push
description: "git 提交并推送代码：分析改动、生成规范 commit message、暂存、提交、推送。涉及「提交代码」「commit」「push」时使用。"
version: 1.0.0
---

# Git 提交并推送

分析当前仓库改动，生成规范的 commit message，依次执行 **暂存 → 提交 → 推送**。全程先看再动，不盲目 `git add -A`。

## 前置检查（动手前先看清楚）

1. **确认在 git 仓库内**：当前目录是否属于某个 git 仓库？不是则停止并告知用户。
2. **查看改动全貌**：
   ```bash
   git status          # 哪些文件变了（已暂存 / 未暂存 / 未跟踪）
   git diff            # 未暂存的具体改动
   git diff --staged   # 已暂存的具体改动
   ```
3. **识别敏感文件**：在改动里扫描并**主动提示用户确认**，不要直接提交：
   - 密钥 / 凭证：`.env`、`*.pem`、`*.key`、`id_rsa`、`credentials`、`secrets`
   - 私有配置：`.claude/settings.local.json`、`.npmrc`、`.netrc`
   - 大文件或二进制：图片、视频、数据集、`*.log`、`node_modules`、`dist/`、构建产物
   - 检查 `.gitignore` 是否已覆盖这些

> 如果发现敏感文件，**停下来询问用户**：是否要提交？是否应该加入 `.gitignore`？得到明确答复再继续。

## 1. 生成 commit message

基于 `git diff`（不仅是文件名）归纳**做了什么、为什么**，遵循 **Conventional Commits**：

```
<type>(<scope>): <subject>

<body 可选，说明动机/背景>
```

- **type**：`feat`(新功能) / `fix`(修 bug) / `refactor`(重构) / `perf`(性能) / `docs`(文档) / `test`(测试) / `chore`(杂项) / `style`(格式) / `ci`(CI) / `build`(构建)
- **subject**：祈使句、中文优先、≤50 字、句末不加句号
- **scope**：可选，标识影响范围（模块/组件名）
- 改动较多但同主题，用一句概括 subject + body 分点说明；改动跨越多个不相关主题，**建议用户拆成多次提交**而不是混在一起

**直接用生成的 message 提交，不向用户确认。** 此 skill 的目标是**最快完成提交推送**，全程不停顿等待确认。

## 2. 暂存（stage）

- 默认只**暂存相关文件**，按需 `git add <具体文件>`，避免无脑 `git add -A`
- 只有在确认所有改动确实都属于本次提交时，才用 `git add -A`
- 新增的文件记得显式 `git add`

## 3. 提交（commit）

```bash
git commit -m "<message>"
# 多行 message 用多个 -m
git commit -m "<type>(<scope>): <subject>" -m "<body>"
```

- 提交后用 `git log -1 --stat` 确认本次提交内容符合预期
- 提交失败（如 pre-commit hook 拦截、lint 报错）：**不要** `--no-verify` 强推，先看报错、修复后重新提交，除非用户明确要求跳过

## 4. 推送（push）

```bash
git push                          # 有上游分支时
git push -u origin <branch>       # 当前分支无上游时，设置并推送
```

- 推送被拒（`! [rejected]` non-fast-forward）：说明远端有新提交，先 `git pull --rebase` 处理冲突，再推送。冲突解决后确认无遗漏再继续。
- 推送完成后报告：**推送到的分支、远端仓库、本次提交摘要**。

## 边界（不做什么）

- ❌ 不擅自创建 / 切换 / 删除分支（除非用户要求）
- ❌ 不擅自 `git push --force`（危险操作，必须用户明确同意）
- ❌ 不擅自执行 `git reset --hard` / `git clean` 等破坏性命令
- ❌ 不绕过 hook 强行提交
- ✅ 若改动为空、没有远端、或处于 detached HEAD，停止并告知用户

## 一句话流程

`看 diff → 扫敏感文件 → 拟 message → 按需 add → commit → (确认上游) push → 报告结果`

> 整个流程**不停顿、不等待用户确认**，一口气执行到底（敏感文件除外）。
