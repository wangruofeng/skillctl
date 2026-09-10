#!/bin/bash
# sync.sh — 将 .claude/skills 同步到其他 agent 目录（软连接）
# 支持：项目级调用（从 .claude/skills/rf-skill-sync/ 运行）
#      用户级调用（在任何项目目录下通过 Claude Code skill 调用）
set -euo pipefail

# 从当前工作目录向上查找 git 仓库根目录
if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  PROJECT_ROOT="$git_root"
else
  # 如果不在 git 仓库，使用当前工作目录
  PROJECT_ROOT="$PWD"
fi

CLAUDE_SKILLS="$PROJECT_ROOT/.claude/skills"

# 源目录不存在时直接报错退出，避免 glob 不展开把字面量 * 当成 skill 名
if [[ ! -d "$CLAUDE_SKILLS" ]]; then
  echo "错误: 源目录不存在: $CLAUDE_SKILLS" >&2
  echo "  请在包含 .claude/skills/ 的项目根目录运行" >&2
  exit 1
fi

# 默认目标
TARGETS=()
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      echo "用法: $0 [--dry-run] [目标目录 ...]"
      echo "  默认目标: .codex/skills"
      echo "  示例: $0 .codex/skills .cursor/skills"
      exit 0
      ;;
    *)
      TARGETS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  # 自动识别项目根目录下的 ".<agent>/skills" 目录（仅一级隐藏目录）
  echo "  正在自动扫描 .xxx/skills 目录..."
  # 仅匹配一级隐藏目录 .<agent>/skills；跳过更深层嵌套（如 .git/modules/.claude/skills）
  # 与 git 内部目录 .git/...，避免误把 git 子模块镜像库当成同步目标
  skills_re='^\.[^/]+/skills$'
  while IFS= read -r -d '' dir; do
    # 转换为相对于项目根目录的路径
    rel_path="${dir#$PROJECT_ROOT/}"
    if [[ "$rel_path" =~ $skills_re \
          && "$rel_path" != ".claude/skills" \
          && "$rel_path" != .git/* ]]; then
      TARGETS+=("$rel_path")
    fi
  done < <(find "$PROJECT_ROOT" -maxdepth 2 -type d -name "skills" \
            -path "*/.[^/]*/skills" -not -path "*/.git/*" -print0 2>/dev/null | sort -z)

  # 如果没有找到，使用默认值
  if [[ ${#TARGETS[@]} -eq 0 ]]; then
    TARGETS=(".codex/skills")
  fi
fi

echo "=== Skill 同步 ==="
echo "源目录: .claude/skills/"
echo "目标: ${TARGETS[*]}"
$DRY_RUN && echo "模式: 预览（不会实际修改）"
echo

# 收集源 skills 列表
source_names=()
for skill_dir in "$CLAUDE_SKILLS"/*/; do
  name="$(basename "$skill_dir")"
  [[ "$name" == "rf-skill-sync" ]] && continue
  source_names+=("$name")
done

echo "源 skills (${#source_names[@]}):"
printf "  %s\n" "${source_names[@]}" | sort
echo

for target_rel in "${TARGETS[@]}"; do
  target_dir="$PROJECT_ROOT/$target_rel"
  echo "▶ 处理目标: $target_rel"

  if [[ ! -d "$target_dir" ]]; then
    echo "  目录不存在，创建: $target_dir"
    $DRY_RUN || mkdir -p "$target_dir"
  fi

  added=0
  removed=0
  skipped=0

  # 1) 为每个源 skill 创建或更新软连接
  for name in "${source_names[@]}"; do
    link="$target_dir/$name"
    expected_target="../../.claude/skills/$name"

    if [[ -L "$link" ]]; then
      current="$(readlink "$link")"
      if [[ "$current" == "$expected_target" ]]; then
        ((skipped++))
      else
        echo "  更新: $name (指向错误 → 修复)"
        $DRY_RUN || ln -sfn "$expected_target" "$link"
        ((added++))
      fi
    elif [[ -d "$link" ]]; then
      echo "  跳过: $name (是真实目录，不是软连接)"
      ((skipped++))
    elif [[ -e "$link" ]]; then
      echo "  跳过: $name (是文件，不是软连接)"
      ((skipped++))
    else
      echo "  新增: $name"
      $DRY_RUN || ln -s "$expected_target" "$link"
      ((added++))
    fi
  done

  # 2) 删除断连/失效的软连接
  for link in "$target_dir"/*; do
    [[ ! -L "$link" ]] && continue
    name="$(basename "$link")"
    if [[ ! -e "$link" ]]; then
      echo "  删除: $name (源 skill 已不存在)"
      $DRY_RUN || rm "$link"
      ((removed++))
    fi
  done

  echo "  结果: +$added / -$removed / =$skipped (新增/删除/已存在)"
  echo
done

echo "=== 同步完成 ==="
