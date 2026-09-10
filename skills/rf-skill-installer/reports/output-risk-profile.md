# Output Risk Profile: rf-skill-installer

> Generated: 2026-06-01 | Methodology: YAO Output Quality Risk

| Risk Family | Specific Failure | Severity | Self-Repair |
|---|---|---|---|
| `wrong_url_parse` | 从 URL 错误提取了 owner/repo | high | 确认 owner/repo 格式 |
| `missed_subpath` | 未识别子路径中的 skill 名称 | medium | 检查 URL 中 /tree/main/skills/ |
| `missed_extra_steps` | 未提示 npm install 等额外步骤 | medium | G1: 检查 README |
| `non_github_url` | 输入非 GitHub URL 未提示 | low | 提示用户 |
| `stale_command` | npx skills add 语法已过时 | low | 检查版本兼容 |

## Output Constraints

- 只生成命令，不实际执行
- 仅处理 GitHub URL