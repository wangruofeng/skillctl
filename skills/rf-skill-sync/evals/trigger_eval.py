#!/usr/bin/env python3
"""
Trigger 评估用例
用于验证 rf-skill-sync skill 的触发描述是否准确
"""

import json

# 正向触发（应该路由到此 skill）
POSITIVE_TRIGGERS = [
    "同步 skills 到 codex",
    "把 .claude/skills 同步到其他 agent",
    "创建 skills 软链接",
    "同步 skill 到 cursor",
    "把所有 skill 链接到 .codex/skills",
    "跨环境同步 skills",
    "更新 skills 软链接",
    "清理失效的 skill 软链接",
    "在新项目中初始化 skills",
]

# 负向触发（不应该路由到此 skill）
NEGATIVE_TRIGGERS = [
    "安装新的 skill",
    "下载 skill",
    "复制文件",
    "git 同步",
    "同步代码到远程",
    "同步 blog 文章",
    "skill 质量检查",
]

# 近邻混淆（容易误触发的边界情况）
NEAR_NEIGHBORS = [
    "安装 skills 到全局",
    "复制 skills 到其他项目",
    "导出 skills 列表",
]

def evaluate(description: str) -> dict:
    """
    简单的评估函数：检查描述中是否包含关键触发词
    """
    trigger_words = ["同步", "sync", "软链接", "软连接", "symlink", "codex", "agent 目录", "skills"]

    positive_score = sum(1 for t in POSITIVE_TRIGGERS if any(w in t for w in trigger_words))
    negative_score = sum(1 for t in NEGATIVE_TRIGGERS if any(w in t for w in trigger_words))

    return {
        "positive_coverage": positive_score / len(POSITIVE_TRIGGERS),
        "negative_leak": negative_score / len(NEGATIVE_TRIGGERS),
        "trigger_words_found": [w for w in trigger_words if w in description],
    }

if __name__ == "__main__":
    # 读取当前 SKILL.md 的 description
    with open("../SKILL.md") as f:
        content = f.read()
        # 提取 frontmatter 中的 description
        import re
        match = re.search(r'description:\s*"([^"]+)"', content)
        if match:
            desc = match.group(1)
            result = evaluate(desc)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("Description not found in SKILL.md")
