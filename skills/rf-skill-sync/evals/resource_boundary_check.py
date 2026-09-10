#!/usr/bin/env python3
"""
资源边界检查
验证 skill 包结构是否符合规范，没有无用的装饰性文件
"""

import os
import re

def check_skill():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 读取 SKILL.md
    with open(os.path.join(root, "SKILL.md")) as f:
        skill_content = f.read()

    results = {
        "directories_found": [],
        "directories_referenced": [],
        "unused_resources": [],
        "quality_density": 0,
    }

    # 检查存在的目录
    for d in ["agents", "references", "scripts", "evals", "reports", "assets"]:
        dir_path = os.path.join(root, d)
        if os.path.isdir(dir_path) and os.listdir(dir_path):
            results["directories_found"].append(d)
            # 检查是否在 SKILL.md 中被引用
            if d in skill_content:
                results["directories_referenced"].append(d)
            else:
                results["unused_resources"].append(d)

    # 计算质量密度
    quality_factors = [
        "agents" in results["directories_found"],
        "references" in results["directories_found"],
        "scripts" in results["directories_found"],
        "evals" in results["directories_found"],
        "reports" in results["directories_found"],
        "interface.yaml" in os.listdir(os.path.join(root, "agents")) if os.path.exists(os.path.join(root, "agents")) else False,
    ]
    results["quality_density"] = sum(quality_factors) / len(quality_factors)

    # 计算初始加载 token 预算（估算）
    skill_tokens = len(skill_content) / 4  # 粗略估算：1 token ≈ 4 字符
    results["estimated_skill_tokens"] = skill_tokens
    results["budget_tier"] = "scaffold" if skill_tokens < 700 else "production" if skill_tokens < 1000 else "library"

    return results

if __name__ == "__main__":
    import json
    result = check_skill()
    print(json.dumps(result, indent=2, ensure_ascii=False))
