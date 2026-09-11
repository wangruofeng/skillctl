#!/bin/bash
# init.sh — 初始化项目 skill 目录配置
# 以 .claude/skills 为基准（不存在时自动创建），
# 创建 zcode / codex 等 agent 的项目级 skill 目录，并用软链接共享基准目录中的 skill；
# 同时统一 CLAUDE.md / AGENTS.md：
#   都不存在时创建 CLAUDE.md 并将 AGENTS.md 软链接到它；
#   仅 AGENTS.md 存在时，以它的内容创建 CLAUDE.md，AGENTS.md 改为软链接；
#   两个都是真实文件时比较内容——一致则 AGENTS.md 链到 CLAUDE.md，
#   不一致则由用户选择保留方（--keep 或交互询问），另一方改为软链接
set -euo pipefail

# 定位项目根目录：git 仓库根，否则当前目录
if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  PROJECT_ROOT="$git_root"
else
  PROJECT_ROOT="$PWD"
fi

CLAUDE_SKILLS_REL=".claude/skills"
CLAUDE_SKILLS="$PROJECT_ROOT/$CLAUDE_SKILLS_REL"
CLAUDE_MD="$PROJECT_ROOT/CLAUDE.md"
AGENTS_MD="$PROJECT_ROOT/AGENTS.md"
AGENTS_LINK_TARGET="CLAUDE.md"

DRY_RUN=false
DIRS_ONLY=false
FORCE=false
CUSTOM_TARGETS=false
TARGETS=()
KEEP=""

usage() {
  cat <<'EOF'
用法: init.sh [--dry-run] [--dirs-only] [--force] [--keep <claude|agents>] [目标目录 ...]

初始化项目 skill 目录配置，以 .claude/skills 为基准（唯一事实源）：
  1. 统一 CLAUDE.md / AGENTS.md：
     - 都不存在 → 创建 CLAUDE.md，AGENTS.md 软链接到它
     - 仅 AGENTS.md 存在 → 以它的内容创建 CLAUDE.md，AGENTS.md 改为软链接
     - 内容一致（均为真实文件）→ 以 CLAUDE.md 为准，AGENTS.md 改为软链接
     - 内容不一致 → 选择保留方（--keep 或交互询问），另一方改为软链接
  2. .claude/skills 不存在时自动创建
  3. 创建各 agent 的项目级 skill 目录（默认 .zcode/skills .codex/skills）
  4. 将基准目录下的 skill 以相对路径软链接到各目标目录

选项:
  --dry-run    预览模式，不做任何修改
  --dirs-only  只创建目录与 agent 文档，不建立 skill 软链接
  --force      跳过「已初始化」快速检查，强制执行并输出完整报告
  --keep <f>   CLAUDE.md 与 AGENTS.md 内容不一致时指定保留方：
               claude（保留 CLAUDE.md）或 agents（保留 AGENTS.md）；
               不指定且在终端交互运行时会现场询问
  -h, --help   显示帮助

示例:
  init.sh                                # 默认初始化 .zcode/skills .codex/skills（已初始化则跳过）
  init.sh --force                        # 强制重新执行
  init.sh --keep agents                  # 两文档不一致时保留 AGENTS.md 内容
  init.sh .codex/skills .cursor/skills   # 自定义目标目录
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)   DRY_RUN=true; shift ;;
    --dirs-only) DIRS_ONLY=true; shift ;;
    --force)     FORCE=true; shift ;;
    --keep)
      case "${2:-}" in
        claude|CLAUDE)   KEEP="claude"; shift 2 ;;
        agents|AGENTS)   KEEP="agents"; shift 2 ;;
        *) echo "错误: --keep 需要 claude 或 agents" >&2; exit 1 ;;
      esac
      ;;
    -h|--help)   usage; exit 0 ;;
    *)           CUSTOM_TARGETS=true; TARGETS+=("$1"); shift ;;
  esac
done

# 默认目标：zcode 与 codex 的项目级 skill 目录
if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=(".zcode/skills" ".codex/skills")
fi

# ── 0) 快速跳过：已完整初始化时直接退出 ────────────────────
# 仅默认参数下生效：--dry-run / --dirs-only / --force / 自定义目标均走完整流程
is_agent_docs_ready() {
  # 已统一为「一个事实源 + 一个软链接」即就绪，两个方向均可：
  #   a) CLAUDE.md 为真实文件，AGENTS.md → CLAUDE.md（默认方向）
  #   b) AGENTS.md 为真实文件，CLAUDE.md → AGENTS.md（保留 AGENTS.md 后的方向）
  if [[ -f "$CLAUDE_MD" && ! -L "$CLAUDE_MD" \
     && -L "$AGENTS_MD" && "$(readlink "$AGENTS_MD")" == "$AGENTS_LINK_TARGET" ]]; then
    return 0
  fi
  if [[ -f "$AGENTS_MD" && ! -L "$AGENTS_MD" \
     && -L "$CLAUDE_MD" && "$(readlink "$CLAUDE_MD")" == "AGENTS.md" ]]; then
    return 0
  fi
  return 1
}

is_initialized() {
  is_agent_docs_ready || return 1
  [[ -d "$CLAUDE_SKILLS" ]] || return 1
  local target_rel target_dir skill_dir name
  for target_rel in "${TARGETS[@]}"; do
    [[ "$target_rel" == "$CLAUDE_SKILLS_REL" ]] && continue
    target_dir="$PROJECT_ROOT/$target_rel"
    [[ -d "$target_dir" ]] || return 1
    for skill_dir in "$CLAUDE_SKILLS"/*/; do
      # 基准目录为空时 glob 不展开，这里过滤掉字面量
      [[ -d "$skill_dir" ]] || continue
      name="$(basename "$skill_dir")"
      [[ "$name" == "rf-skill-init" ]] && continue
      [[ -L "$target_dir/$name" ]] || return 1
      [[ "$(readlink "$target_dir/$name")" == "../../$CLAUDE_SKILLS_REL/$name" ]] || return 1
    done
  done
  return 0
}

# 写入最小 CLAUDE.md 模板（仅在文件不存在时调用）
write_claude_md_template() {
  cat > "$CLAUDE_MD" <<'EOF'
# 项目约定

（由 rf-skill-init 自动创建，请按项目需要补充。）

## 目录与 Skill

- 项目 skill 基准目录：`.claude/skills/`（唯一事实源）
- `AGENTS.md` 软链接到本文件，供其他 Agent 读取同一套约定
EOF
}

# 交互询问保留 CLAUDE.md 还是 AGENTS.md；结果写入 PROMPT_CHOICE（claude/agents），跳过则为空
prompt_keep_choice() {
  PROMPT_CHOICE=""
  local answer
  while true; do
    read -r -p "  保留哪一个的内容？[1] CLAUDE.md  [2] AGENTS.md  [q] 暂不统一: " answer || { echo; return; }
    case "$answer" in
      1|c|claude) PROMPT_CHOICE="claude"; return ;;
      2|a|agents) PROMPT_CHOICE="agents"; return ;;
      q|Q)        echo; return ;;
      *)          echo "  请输入 1 / 2 / q" ;;
    esac
  done
}

# CLAUDE.md 与 AGENTS.md 均为真实文件时的统一逻辑：
# 内容一致 → 以 CLAUDE.md 为准，AGENTS.md 改为软链接；
# 不一致 → 选择保留方（--keep > 交互询问 > 跳过），另一方改为软链接
unify_agent_docs() {
  if cmp -s "$CLAUDE_MD" "$AGENTS_MD"; then
    echo "  ✓ CLAUDE.md 与 AGENTS.md 内容一致，以 CLAUDE.md 为准"
    echo "  ～ AGENTS.md 改为软链接 → CLAUDE.md"
    $DRY_RUN || ln -sfn "$AGENTS_LINK_TARGET" "$AGENTS_MD"
    return
  fi

  echo "  ！ CLAUDE.md 与 AGENTS.md 内容不一致:"
  echo "      CLAUDE.md: $(wc -l < "$CLAUDE_MD" | tr -d ' ') 行 / $(wc -c < "$CLAUDE_MD" | tr -d ' ') 字节"
  echo "      AGENTS.md: $(wc -l < "$AGENTS_MD" | tr -d ' ') 行 / $(wc -c < "$AGENTS_MD" | tr -d ' ') 字节"

  local choice="$KEEP"
  if [[ -z "$choice" ]] && ! $DRY_RUN && [[ -t 0 ]]; then
    prompt_keep_choice
    choice="$PROMPT_CHOICE"
  fi

  case "$choice" in
    claude)
      echo "  ～ 保留 CLAUDE.md，AGENTS.md 改为软链接 → CLAUDE.md"
      $DRY_RUN || ln -sfn "$AGENTS_LINK_TARGET" "$AGENTS_MD"
      ;;
    agents)
      echo "  ～ 保留 AGENTS.md，CLAUDE.md 改为软链接 → AGENTS.md"
      $DRY_RUN || ln -sfn "AGENTS.md" "$CLAUDE_MD"
      ;;
    *)
      echo "  ？ 未选择保留方，暂不统一（终端交互运行可选择，或用 --keep claude / --keep agents 重跑）"
      ;;
  esac
}

# 确保 CLAUDE.md 存在，并统一 CLAUDE.md / AGENTS.md（其一为软链接指向另一个）
ensure_agent_docs() {
  echo "▶ Agent 文档"

  # 仅当 CLAUDE.md 是「原本就存在」的真实文件时才可能与 AGENTS.md 比较统一；
  # claude_copied 标记本次运行是否从 AGENTS.md 复制新建了 CLAUDE.md
  local claude_is_real=false
  local claude_copied=false
  local agents_is_real=false

  # 先探测 AGENTS.md 是否为真实文件，决定 CLAUDE.md 缺失时的创建方式
  if [[ -f "$AGENTS_MD" && ! -L "$AGENTS_MD" ]]; then
    agents_is_real=true
  fi

  # CLAUDE.md
  if [[ -L "$CLAUDE_MD" ]]; then
    if [[ -e "$CLAUDE_MD" ]]; then
      echo "  ✓ CLAUDE.md 已存在（软链接 → $(readlink "$CLAUDE_MD")）"
    else
      echo "  ！ CLAUDE.md 是断链软链接，跳过（不覆盖）: $(readlink "$CLAUDE_MD")"
    fi
  elif [[ -f "$CLAUDE_MD" ]]; then
    echo "  ✓ CLAUDE.md 已存在"
    claude_is_real=true
  elif [[ -e "$CLAUDE_MD" ]]; then
    echo "  ！ CLAUDE.md 存在但不是普通文件，跳过"
  elif $agents_is_real; then
    echo "  ＋ 创建 CLAUDE.md（内容复制自 AGENTS.md）"
    $DRY_RUN || cp "$AGENTS_MD" "$CLAUDE_MD"
    claude_copied=true
  else
    echo "  ＋ 创建 CLAUDE.md"
    $DRY_RUN || write_claude_md_template
  fi

  # AGENTS.md
  if [[ -L "$AGENTS_MD" ]]; then
    current="$(readlink "$AGENTS_MD")"
    if [[ "$current" == "$AGENTS_LINK_TARGET" ]]; then
      echo "  ✓ AGENTS.md → $AGENTS_LINK_TARGET"
    else
      echo "  ～ 修复 AGENTS.md (原指向 $current → $AGENTS_LINK_TARGET)"
      $DRY_RUN || ln -sfn "$AGENTS_LINK_TARGET" "$AGENTS_MD"
    fi
  elif [[ -f "$AGENTS_MD" ]]; then
    # 已是反向统一状态：CLAUDE.md → AGENTS.md（AGENTS.md 为事实源）
    if [[ -L "$CLAUDE_MD" && "$(readlink "$CLAUDE_MD")" == "AGENTS.md" ]]; then
      echo "  ✓ AGENTS.md 为事实源（CLAUDE.md → AGENTS.md）"
    elif $claude_copied; then
      # CLAUDE.md 刚从 AGENTS.md 复制而来，内容已并入，直接改为软链接
      echo "  ～ AGENTS.md 改为软链接 → CLAUDE.md"
      $DRY_RUN || ln -sfn "$AGENTS_LINK_TARGET" "$AGENTS_MD"
    elif $claude_is_real; then
      unify_agent_docs
    else
      echo "  ！ 跳过 AGENTS.md（真实文件，不覆盖；如需统一请手动处理后再运行）"
    fi
  elif [[ -e "$AGENTS_MD" ]]; then
    echo "  ！ AGENTS.md 存在但不是普通文件，跳过"
  else
    # 目标 CLAUDE.md 尚不存在时（dry-run 或 CLAUDE 被跳过），仍创建相对链接
    echo "  ＋ 链接 AGENTS.md → $AGENTS_LINK_TARGET"
    $DRY_RUN || ln -s "$AGENTS_LINK_TARGET" "$AGENTS_MD"
  fi
  echo
}

if ! $DRY_RUN && ! $DIRS_ONLY && ! $FORCE && ! $CUSTOM_TARGETS && is_initialized; then
  echo "✓ skill 目录已初始化，跳过（目标: ${TARGETS[*]}）"
  echo "  强制重新执行并查看详情: skills-init --force"
  exit 0
fi

echo "=== Skill 目录初始化 ==="
echo "项目根: $PROJECT_ROOT"
if $DRY_RUN; then
  echo "模式: 预览（不会实际修改）"
fi
echo

# ── 1) Agent 文档：CLAUDE.md + AGENTS.md 软链接 ────────────
ensure_agent_docs

# ── 2) 基准目录 ────────────────────────────────────────────
if [[ -d "$CLAUDE_SKILLS" ]]; then
  echo "✓ 基准目录已存在: $CLAUDE_SKILLS_REL"
else
  echo "＋ 创建基准目录: $CLAUDE_SKILLS_REL"
  $DRY_RUN || mkdir -p "$CLAUDE_SKILLS"
fi

# ── 3) 目标目录 ────────────────────────────────────────────
for target_rel in "${TARGETS[@]}"; do
  # 基准目录自身不能作为目标（会自引用循环）
  if [[ "$target_rel" == "$CLAUDE_SKILLS_REL" ]]; then
    echo "！ 跳过基准目录自身: $target_rel"
    continue
  fi
  if [[ -d "$PROJECT_ROOT/$target_rel" ]]; then
    echo "✓ 目标已存在: $target_rel"
  else
    echo "＋ 创建目标目录: $target_rel"
    $DRY_RUN || mkdir -p "$PROJECT_ROOT/$target_rel"
  fi
done
echo

# ── 4) 软链接基准目录中的 skill ────────────────────────────
if $DIRS_ONLY; then
  echo "（--dirs-only：跳过 skill 软链接）"
else
  # 收集基准目录下的 skill（自排除，避免工具自身被链接）
  source_names=()
  for skill_dir in "$CLAUDE_SKILLS"/*/; do
    # 基准目录为空时 glob 不展开，这里过滤掉字面量
    [[ -d "$skill_dir" ]] || continue
    name="$(basename "$skill_dir")"
    [[ "$name" == "rf-skill-init" ]] && continue
    source_names+=("$name")
  done

  if [[ ${#source_names[@]} -eq 0 ]]; then
    echo "基准目录为空，没有可链接的 skill"
    echo "提示: 之后往 .claude/skills/ 添加 skill 后，可运行 rf-skill-sync 同步到各 agent 目录"
  else
    echo "基准 skills (${#source_names[@]}):"
    printf "  %s\n" "${source_names[@]}"
    echo

    for target_rel in "${TARGETS[@]}"; do
      # 基准目录自身已在上面跳过
      [[ "$target_rel" == "$CLAUDE_SKILLS_REL" ]] && continue
      target_dir="$PROJECT_ROOT/$target_rel"
      echo "▶ 链接到: $target_rel"

      # 计数用 x=$((x+1)) 而非 ((x++))：后者在 x=0 时返回非零，
      # 会触发 set -e 提前退出
      added=0
      fixed=0
      skipped=0
      for name in "${source_names[@]}"; do
        link="$target_dir/$name"
        expected_target="../../$CLAUDE_SKILLS_REL/$name"
        if [[ -L "$link" ]]; then
          if [[ "$(readlink "$link")" == "$expected_target" ]]; then
            skipped=$((skipped+1))
          else
            echo "  修复: $name (原指向 $(readlink "$link"))"
            $DRY_RUN || ln -sfn "$expected_target" "$link"
            fixed=$((fixed+1))
          fi
        elif [[ -d "$link" ]]; then
          echo "  跳过: $name (真实目录，不覆盖)"
          skipped=$((skipped+1))
        elif [[ -e "$link" ]]; then
          echo "  跳过: $name (普通文件，不覆盖)"
          skipped=$((skipped+1))
        else
          echo "  链接: $name"
          $DRY_RUN || ln -s "$expected_target" "$link"
          added=$((added+1))
        fi
      done
      echo "  结果: +$added / ~$fixed / =$skipped (新增/修复/已存在)"
      echo
    done
  fi
fi

echo "=== 初始化完成 ==="
