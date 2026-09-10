#!/bin/bash
# install.sh — 一键安装 skills-sync 命令
# 在 shell 配置文件中创建/更新 alias skills-sync，指向本 skill 的 sync.sh
# 规则：存在 ~/.zshrc（或登录 shell 为 zsh）→ 写入 ~/.zshrc；否则写入 ~/.bash_profile
# 用法:
#   ./install.sh              安装/更新 skills-sync 命令
#   ./install.sh --uninstall  卸载 skills-sync 命令

set -euo pipefail

ALIAS_NAME="skills-sync"
MARKER_BEGIN="# >>> skills-sync (rf-skill-sync) >>>"
MARKER_END="# <<< skills-sync (rf-skill-sync) <<<"
# 历史 marker（重命名前），安装与卸载时一并清理
LEGACY_MARKERS=(
  "# >>> skills-sync (rf-sync-skills) >>>|# <<< skills-sync (rf-sync-skills) <<<"
)

# 定位 sync.sh（与本脚本同目录），转为绝对路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SH="$SCRIPT_DIR/sync.sh"

usage() {
  echo "用法: $0 [--uninstall]"
  echo "  （无参数）       安装/更新 alias $ALIAS_NAME → sync.sh"
  echo "  --uninstall     移除 alias $ALIAS_NAME"
}

# 选择写入的 shell 配置文件：优先 .zshrc，其次 .bash_profile
choose_rc() {
  if [[ -f "$HOME/.zshrc" || "$SHELL" == */zsh ]]; then
    echo "$HOME/.zshrc"
  else
    echo "$HOME/.bash_profile"
  fi
}

# 删除一对 begin/end marker 之间的管理块
remove_marked_block() {
  local rc="$1" begin="$2" end="$3"
  [[ -f "$rc" ]] || return 0
  grep -qF "$begin" "$rc" 2>/dev/null || return 0
  sed -i.bak "/^${begin}\$/,/^${end}\$/d" "$rc"
  rm -f "${rc}.bak"
}

# 删除当前 + 历史管理块（幂等：重复安装不产生重复条目）
remove_block() {
  local rc="$1"
  [[ -f "$rc" ]] || return 0
  remove_marked_block "$rc" "$MARKER_BEGIN" "$MARKER_END"
  local pair begin end
  for pair in "${LEGACY_MARKERS[@]}"; do
    begin="${pair%%|*}"
    end="${pair#*|}"
    remove_marked_block "$rc" "$begin" "$end"
  done
}

has_any_block() {
  local rc="$1"
  [[ -f "$rc" ]] || return 1
  grep -qF "$MARKER_BEGIN" "$rc" 2>/dev/null && return 0
  local pair begin
  for pair in "${LEGACY_MARKERS[@]}"; do
    begin="${pair%%|*}"
    grep -qF "$begin" "$rc" 2>/dev/null && return 0
  done
  return 1
}

ACTION="install"
case "${1:-}" in
  --uninstall) ACTION="uninstall" ;;
  -h|--help)   usage; exit 0 ;;
  "")          ;;
  *)           usage >&2; exit 1 ;;
esac

if [[ "$ACTION" == "uninstall" ]]; then
  # 卸载时同时检查两处配置，避免切换过 shell 后残留
  found=false
  for rc in "$HOME/.zshrc" "$HOME/.bash_profile"; do
    if has_any_block "$rc"; then
      remove_block "$rc"
      echo "✓ 已从 $rc 移除 $ALIAS_NAME"
      found=true
    fi
  done
  $found || echo "未发现已安装的 ${ALIAS_NAME}，无需卸载"
  exit 0
fi

# --- 安装 ---
if [[ ! -f "$SYNC_SH" ]]; then
  echo "错误: 未找到 sync.sh（${SYNC_SH}）" >&2
  exit 1
fi

RC_FILE="$(choose_rc)"
touch "$RC_FILE"

if has_any_block "$RC_FILE"; then
  echo "检测到已有安装，更新指向路径..."
fi

remove_block "$RC_FILE"

# 文件末尾若无换行符，先补一个，避免拼到最后一行
[[ -n "$(tail -c 1 "$RC_FILE")" ]] && echo >> "$RC_FILE"

alias_line="alias ${ALIAS_NAME}='bash \"${SYNC_SH}\"'"
{
  printf '\n%s\n' "$MARKER_BEGIN"
  printf '%s\n' "$alias_line"
  printf '%s\n' "$MARKER_END"
} >> "$RC_FILE"

echo "✓ 已安装命令: $ALIAS_NAME"
echo "    $alias_line"
echo "  配置文件: $RC_FILE"
echo "  生效方式: source ${RC_FILE}（或新开一个终端）"
echo "  用法:     ${ALIAS_NAME} [--dry-run] [目标目录 ...]"
