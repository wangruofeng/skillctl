#!/bin/bash
# link.sh — 将仓库内的所有 skill 软链接到统一目录（默认 ~/.agents/skills）
# 方向：skill 仓库 → 用户全局目录（多仓库 skill 汇聚到一处，仓库是唯一事实源）。
# 源目录默认自动探测（git 根下 skills/，其次 .claude/skills/），--source 覆盖；
# 链接使用绝对路径（与 ~/.agents/skills 现有约定一致；项目内相对链接请用 rf-skill-sync）。
# 幂等：正确链接跳过、指向错误的修复；目标中真实目录/文件默认跳过，--force 强制覆盖。
# 清理只摘除指向本仓库且已失效的链接，目标目录中其他来源的内容一律不动。
# 兼容 macOS 自带 bash 3.2：不使用关联数组、realpath -m，空数组不展开。
# 用法:
#   ./link.sh                     链接到 ~/.agents/skills
#   ./link.sh <目标目录>           链接到指定目录
#   ./link.sh --source <目录>     指定源目录
#   ./link.sh --dry-run           预览，不做任何修改
#   ./link.sh --remove            摘除目标目录中指向本仓库的所有链接
#   ./link.sh --force             强制覆盖目标中的真实目录/文件（破坏性，慎用）

set -euo pipefail

usage() {
  cat <<'EOF'
用法: link.sh [--source <目录>] [--target <目录>] [目标目录] [--dry-run] [--force] [--remove]

将源目录下的所有 skill（含 SKILL.md 的一级子目录）软链接到目标目录。
  源目录:   默认自动探测 —— git 根下 skills/，其次 .claude/skills/；--source 覆盖
  目标目录: 默认 ~/.agents/skills；位置参数或 --target 覆盖

选项:
  --source <目录>   指定源 skill 目录
  --target <目录>   指定目标目录（与位置参数等价，后者优先）
  --dry-run         预览，不做任何修改
  --force, -f       强制覆盖目标中同名真实目录/文件（破坏性：会被移动到系统 Trash，慎用）
  --remove          摘除目标目录中指向本仓库的所有链接（含失效链接）
  -h, --help        显示本帮助
EOF
}

SOURCE=""
TARGET="${HOME}/.agents/skills"
DRY_RUN=false
FORCE=false
REMOVE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      [[ $# -ge 2 ]] || { echo "错误: --source 需要一个参数" >&2; exit 1; }
      SOURCE="$2"; shift 2 ;;
    --target)
      [[ $# -ge 2 ]] || { echo "错误: --target 需要一个参数" >&2; exit 1; }
      TARGET="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --force|-f) FORCE=true; shift ;;
    --remove)  REMOVE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    -*)        echo "错误: 未知参数: $1" >&2; usage >&2; exit 1 ;;
    *)         TARGET="$1"; shift ;;
  esac
done

# --- 定位源目录 ---
if [[ -z "$SOURCE" ]]; then
  if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    ROOT="$git_root"
  else
    ROOT="$PWD"
  fi
  for cand in "$ROOT/skills" "$ROOT/.claude/skills"; do
    if [[ -d "$cand" ]]; then
      SOURCE="$cand"
      break
    fi
  done
fi

if [[ -n "$SOURCE" && ! -d "$SOURCE" ]]; then
  echo "错误: --source 指定的源目录不存在: $SOURCE" >&2
  exit 1
fi
if [[ -z "$SOURCE" ]]; then
  echo "错误: 未找到源 skill 目录（已尝试: ${ROOT:-$PWD}/skills、${ROOT:-$PWD}/.claude/skills）" >&2
  echo "  请用 --source <目录> 显式指定" >&2
  exit 1
fi
SRC_ABS="$(cd -P "$SOURCE" && pwd)"

echo "=== Skill 全局链接 ==="
echo "源目录: $SRC_ABS"
echo "目标:   $TARGET"
$DRY_RUN && echo "模式: 预览（不会实际修改）"
$FORCE && echo "模式: 强制覆盖（真实目录/文件将备份为 *.bak-<时间戳>）"
$REMOVE && echo "模式: 摘除（移除指向本仓库的链接）"
echo

# --- 目标目录 ---
if [[ "$REMOVE" == true && ! -d "$TARGET" ]]; then
  echo "目标目录不存在: $TARGET，无需摘除"
  exit 0
fi

if [[ ! -d "$TARGET" ]]; then
  echo "＋ 创建目标目录: $TARGET"
  if [[ "$DRY_RUN" == true ]]; then
    TGT_ABS="$TARGET"   # 预览模式下目录尚不存在，直接使用给定路径
  else
    mkdir -p "$TARGET"
    TGT_ABS="$(cd -P "$TARGET" && pwd)"
  fi
else
  TGT_ABS="$(cd -P "$TARGET" && pwd)"
fi

# 防自引用：源与目标为同一目录时退出
if [[ "$TGT_ABS" == "$SRC_ABS" ]]; then
  echo "错误: 源目录与目标目录相同: $SRC_ABS" >&2
  exit 1
fi

# --- 收集源 skills（仅统计含 SKILL.md 的一级子目录） ---
source_names=()
ignored_names=()
for entry in "$SRC_ABS"/*/; do
  [[ -d "$entry" ]] || continue
  name="$(basename "$entry")"
  if [[ -f "$entry/SKILL.md" ]]; then
    source_names+=("$name")
  else
    ignored_names+=("$name")
  fi
done

echo "源 skills (${#source_names[@]}):"
if [[ ${#source_names[@]} -gt 0 ]]; then
  printf "  %s\n" "${source_names[@]}" | sort
fi
if [[ ${#ignored_names[@]} -gt 0 ]]; then
  echo "  忽略（无 SKILL.md）: ${ignored_names[*]}"
fi
echo

# --- 摘除模式：删除目标中指向本仓库的所有链接，然后结束 ---
if [[ "$REMOVE" == true ]]; then
  removed=0
  for link in "$TGT_ABS"/*; do
    [[ -L "$link" ]] || continue
    cur="$(readlink "$link")"
    if [[ "$cur" == "$SRC_ABS" || "$cur" == "$SRC_ABS"/* ]]; then
      echo "  摘除: $(basename "$link")"
      $DRY_RUN || rm "$link"
      removed=$((removed + 1))
    fi
  done
  echo
  echo "=== 完成: 摘除 $removed 个链接 ==="
  exit 0
fi

if [[ ${#source_names[@]} -eq 0 ]]; then
  echo "源目录下没有包含 SKILL.md 的子目录，仅执行清理。"
  echo
fi

# --- 链接：为每个源 skill 创建/修复软链接 ---
echo "▶ 链接到: $TGT_ABS"
added=0
fixed=0
kept=0
blocked=0
forced=0
if [[ ${#source_names[@]} -gt 0 ]]; then
  for name in "${source_names[@]}"; do
    link="$TGT_ABS/$name"
    expected="$SRC_ABS/$name"
    if [[ -L "$link" ]]; then
      current="$(readlink "$link")"
      if [[ "$current" == "$expected" ]]; then
        kept=$((kept + 1))
      else
        echo "  修复: $name (原指向 $current)"
        $DRY_RUN || ln -sfn "$expected" "$link"
        fixed=$((fixed + 1))
      fi
    elif [[ -e "$link" ]]; then
      if [[ "$FORCE" == true ]]; then
        backup="${link}.bak-$(date +%Y%m%d%H%M%S)"
        echo "  覆盖: $name (真实目录/文件 → 备份为 $(basename "$backup"))"
        if [[ "$DRY_RUN" != true ]]; then
          mv "$link" "$backup"
          ln -s "$expected" "$link"
        fi
        forced=$((forced + 1))
      else
        echo "  跳过: $name (目标已存在真实目录/文件，不覆盖；可加 --force 强制)"
        blocked=$((blocked + 1))
      fi
    else
      echo "  新增: $name"
      $DRY_RUN || ln -s "$expected" "$link"
      added=$((added + 1))
    fi
  done
fi

# --- 清理：仅删除指向本仓库、但源 skill 已不存在的失效链接 ---
removed=0
for link in "$TGT_ABS"/*; do
  [[ -L "$link" ]] || continue
  name="$(basename "$link")"
  cur="$(readlink "$link")"
  if [[ "$cur" == "$SRC_ABS"/* && ! -e "$link" ]]; then
    echo "  删除: $name (源 skill 已不存在)"
    $DRY_RUN || rm "$link"
    removed=$((removed + 1))
  fi
done

echo "  结果: +$added / ~$fixed / -$removed / =$kept (新增/修复/删除/已存在)"
if [[ $forced -gt 0 ]]; then
  echo "  注意: $forced 个真实目录/文件被强制覆盖（已备份为 *.bak-<时间戳>，确认无误后可删除）"
fi
if [[ $blocked -gt 0 ]]; then
  echo "  注意: $blocked 个名字被真实目录/文件占用，未覆盖"
fi
echo
echo "=== 链接完成 ==="
