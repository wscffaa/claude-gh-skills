#!/usr/bin/env python3
"""
Experience Index: 自动检索历史经验 + 经验沉淀

两种模式：
1. 检索模式（默认）: 从规则文件检索匹配的经验
2. 沉淀模式（--harvest）: 从项目产物中提取新经验并更新规则文件

检索模式 Usage:
    python3 experience_index.py --scene "wavelet mamba" --project wavemamba --types wavelet,mamba
    python3 experience_index.py --scene "DCN 可变形" --project dcnmamba --types dcn --json

沉淀模式 Usage:
    python3 experience_index.py --harvest --project wavemamba
    python3 experience_index.py --harvest --project wavemamba --error "AMP 与 wavelet 不兼容"
    python3 experience_index.py --harvest --project wavemamba --pattern "使用 autocast(enabled=False)"
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# 规则目录（.claude/rules/experience/ - Claude Code 官方规范）
RULES_DIR = Path(__file__).parent.parent.parent.parent / "rules" / "experience"


def load_rules(rule_file: str) -> list[dict]:
    """加载并解析规则文件

    规则格式:
    ## 规则 N: 规则名称
    - 触发条件: 关键词 OR 关键词
    - 风险等级: high | medium | low
    - 提示信息: 预警信息
    - 加载文档: 文档路径
    - 建议服务: 服务1, 服务2
    - 模式文件: 模式文件路径
    """
    path = RULES_DIR / rule_file
    if not path.exists():
        return []

    rules = []
    content = path.read_text(encoding="utf-8")

    # 匹配规则块: ## 规则 N: 名称 ... (到下一个 ## 规则 或文件结束)
    rule_pattern = r"## 规则 (\d+|[A-Z]\d+): (.+?)\n([\s\S]+?)(?=\n## 规则|\Z)"

    for match in re.finditer(rule_pattern, content):
        rule_id = match.group(1).strip()
        name = match.group(2).strip()
        body = match.group(3).strip()

        rule = {
            "id": rule_id,
            "name": name,
            "trigger": "",
            "files": [],
            "risk_level": "medium",
            "message": "",
            "services": [],
            "patterns": [],
        }

        for line in body.split("\n"):
            line = line.strip()
            if line.startswith("- 触发条件:"):
                rule["trigger"] = line.replace("- 触发条件:", "").strip()
            elif line.startswith("- 加载文档:"):
                rule["files"].append(line.replace("- 加载文档:", "").strip())
            elif line.startswith("- 风险等级:"):
                rule["risk_level"] = line.replace("- 风险等级:", "").strip()
            elif line.startswith("- 提示信息:"):
                rule["message"] = line.replace("- 提示信息:", "").strip()
            elif line.startswith("- 建议服务:"):
                services = line.replace("- 建议服务:", "").strip()
                rule["services"] = [s.strip() for s in services.split(",")]
            elif line.startswith("- 模式文件:"):
                rule["patterns"].append(line.replace("- 模式文件:", "").strip())

        rules.append(rule)

    return rules


def extract_keywords(trigger: str) -> list[str]:
    """从触发条件中提取关键词

    触发条件格式: 关键词1 OR 关键词2 OR "多词短语"
    """
    keywords = []

    # 处理引号内的多词短语
    quoted = re.findall(r'"([^"]+)"', trigger)
    keywords.extend(quoted)

    # 移除引号内容后处理 OR 分隔的关键词
    remaining = re.sub(r'"[^"]+"', "", trigger)
    for part in remaining.split(" OR "):
        part = part.strip()
        if part and part.lower() not in ("or", "and"):
            keywords.append(part)

    return [kw.lower() for kw in keywords if kw]


def match_rules(rules: list[dict], scene: str, types: list[str]) -> list[dict]:
    """匹配规则

    匹配逻辑: 场景描述或创新类型中包含任一触发关键词
    """
    matched = []
    search_text = scene.lower()
    type_keywords = [t.lower() for t in types]

    for rule in rules:
        trigger_keywords = extract_keywords(rule.get("trigger", ""))

        # 检查是否匹配
        for kw in trigger_keywords:
            if kw in search_text or kw in type_keywords or any(kw in t for t in type_keywords):
                matched.append(rule)
                break

    return matched


def experience_index(scene: str, project: str, types: list[str]) -> dict:
    """主检索函数

    Args:
        scene: 场景描述
        project: 项目 slug
        types: 创新类型列表

    Returns:
        包含 context/risk/service/pattern 四类结果的字典
    """
    result = {
        "project": project,
        "scene": scene,
        "types": types,
        "context": {"files": []},
        "risk": {"alerts": []},
        "service": {"suggestions": []},
        "pattern": {"files": []},
    }

    # 1. 加载并匹配 context-rules
    context_rules = load_rules("context-rules.md")
    for rule in match_rules(context_rules, scene, types):
        result["context"]["files"].extend(rule.get("files", []))

    # 2. 加载并匹配 risk-rules
    risk_rules = load_rules("risk-rules.md")
    for rule in match_rules(risk_rules, scene, types):
        alert = {
            "level": rule.get("risk_level", "medium"),
            "error_id": f"E{rule.get('id', '000')}" if rule.get("id", "").isdigit() else rule.get("id", ""),
            "name": rule.get("name", ""),
            "message": rule.get("message", ""),
        }
        if alert["message"]:  # 只添加有提示信息的
            result["risk"]["alerts"].append(alert)

    # 3. 加载并匹配 service-rules
    service_rules = load_rules("service-rules.md")
    for rule in match_rules(service_rules, scene, types):
        for svc in rule.get("services", []):
            suggestion = {
                "baseline": svc,
                "reason": rule.get("message", rule.get("name", "")),
            }
            result["service"]["suggestions"].append(suggestion)

    # 4. 加载并匹配 pattern-rules
    pattern_rules = load_rules("pattern-rules.md")
    for rule in match_rules(pattern_rules, scene, types):
        result["pattern"]["files"].extend(rule.get("patterns", []))

    # 5. 去重
    result["context"]["files"] = list(dict.fromkeys(result["context"]["files"]))
    result["pattern"]["files"] = list(dict.fromkeys(result["pattern"]["files"]))

    # 6. 按风险等级排序 (high > medium > low)
    level_order = {"high": 0, "medium": 1, "low": 2}
    result["risk"]["alerts"].sort(key=lambda x: level_order.get(x["level"], 1))

    return result


def print_human_readable(result: dict) -> None:
    """输出人类可读格式"""
    print(f"\n{'='*60}")
    print(f"📋 经验检索结果: {result['project']}")
    print(f"   场景: {result['scene']}")
    if result["types"]:
        print(f"   类型: {', '.join(result['types'])}")
    print(f"{'='*60}\n")

    # 风险预警
    if result["risk"]["alerts"]:
        print("⚠️  风险预警:")
        for alert in result["risk"]["alerts"]:
            level_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(alert["level"], "⚪")
            error_id = f"[{alert['error_id']}] " if alert["error_id"] else ""
            print(f"   {level_icon} {error_id}{alert['message']}")
        print()

    # 上下文文档
    if result["context"]["files"]:
        print("📚 相关文档:")
        for f in result["context"]["files"]:
            print(f"   - {f}")
        print()

    # 服务建议
    if result["service"]["suggestions"]:
        print("🔧 Baseline/服务建议:")
        for svc in result["service"]["suggestions"]:
            print(f"   - {svc['baseline']}: {svc['reason']}")
        print()

    # 代码模式
    if result["pattern"]["files"]:
        print("📝 推荐代码模式:")
        for f in result["pattern"]["files"]:
            print(f"   - {f}")
        print()

    # 无结果提示
    if not any([
        result["risk"]["alerts"],
        result["context"]["files"],
        result["service"]["suggestions"],
        result["pattern"]["files"],
    ]):
        print("ℹ️  未找到匹配的历史经验\n")


# ============================================================
# 沉淀模式 (--harvest)
# ============================================================

def find_spec_dir(project: str) -> Optional[Path]:
    """查找项目 spec 目录"""
    specs_dir = Path("specs")
    if not specs_dir.exists():
        return None

    for d in specs_dir.iterdir():
        if d.is_dir() and f"proj-{project}" in d.name.lower():
            return d
    return None


def get_next_rule_id(rule_file: str) -> str:
    """获取下一个规则 ID"""
    path = RULES_DIR / rule_file
    if not path.exists():
        return "1"

    content = path.read_text(encoding="utf-8")
    ids = re.findall(r"## 规则 (\d+):", content)
    if not ids:
        return "1"
    return str(max(int(i) for i in ids) + 1)


def extract_keywords_from_text(text: str) -> list[str]:
    """从文本中提取关键词（用于生成触发条件）"""
    # 常见的创新类型关键词
    known_keywords = [
        "wavelet", "小波", "DWT", "IDWT", "pytorch_wavelets",
        "mamba", "ssm", "SS2D", "状态空间",
        "DCN", "deformable", "可变形", "偏移量",
        "attention", "transformer", "注意力",
        "flow", "光流", "RAFT", "对齐",
        "depth", "深度", "ZoeDepth",
        "AMP", "autocast", "混合精度",
        "频域", "fourier", "fft",
        "视频", "video", "5D", "temporal",
    ]

    text_lower = text.lower()
    matched = []
    for kw in known_keywords:
        if kw.lower() in text_lower:
            matched.append(kw)

    return matched[:5]  # 最多5个关键词


def append_rule(rule_file: str, rule_id: str, name: str, trigger: str,
                message: str, level: str = None, file_path: str = None) -> bool:
    """追加规则到规则文件"""
    path = RULES_DIR / rule_file
    if not path.exists():
        return False

    # 构建规则内容
    rule_content = f"\n## 规则 {rule_id}: {name}\n"
    rule_content += f"- 触发条件: {trigger}\n"
    if level:
        rule_content += f"- 风险等级: {level}\n"
    rule_content += f"- 提示信息: {message}\n"
    if file_path:
        if "risk" in rule_file:
            pass  # risk-rules 不需要文件路径
        elif "pattern" in rule_file:
            rule_content += f"- 模式文件: {file_path}\n"
        elif "context" in rule_file:
            rule_content += f"- 加载文档: {file_path}\n"

    # 追加到文件
    with open(path, "a", encoding="utf-8") as f:
        f.write(rule_content)

    return True


def scan_project_artifacts(spec_dir: Path) -> dict:
    """扫描项目产物，提取潜在的新经验"""
    artifacts = {
        "errors": [],
        "patterns": [],
        "files_scanned": [],
    }

    # 扫描 error_report.md
    error_report = spec_dir / "debug" / "error_report.md"
    if error_report.exists():
        artifacts["files_scanned"].append(str(error_report))
        content = error_report.read_text(encoding="utf-8")
        # 提取错误描述（简单启发式）
        for line in content.split("\n"):
            if "错误" in line or "Error" in line or "失败" in line:
                artifacts["errors"].append(line.strip()[:200])

    # 扫描 backprop_log.md
    backprop_log = spec_dir / "backprop" / "backprop_log.md"
    if backprop_log.exists():
        artifacts["files_scanned"].append(str(backprop_log))
        content = backprop_log.read_text(encoding="utf-8")
        # 提取修复模式
        for line in content.split("\n"):
            if "修复" in line or "解决" in line or "方案" in line:
                artifacts["patterns"].append(line.strip()[:200])

    return artifacts


def harvest_experience(project: str, error: str = None, pattern: str = None) -> dict:
    """沉淀经验主函数

    Args:
        project: 项目 slug
        error: 手动指定的错误描述
        pattern: 手动指定的模式描述

    Returns:
        沉淀报告
    """
    report = {
        "project": project,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "spec_dir": None,
        "rules_added": {
            "risk": [],
            "pattern": [],
            "context": [],
        },
        "skipped": [],
        "leanspec_synced": False,
    }

    # 查找 spec 目录
    spec_dir = find_spec_dir(project)
    if spec_dir:
        report["spec_dir"] = str(spec_dir)

    # 手动记录错误
    if error:
        keywords = extract_keywords_from_text(error)
        if keywords:
            trigger = " OR ".join(keywords)
            rule_id = get_next_rule_id("risk-rules.md")
            name = f"来自 {project} 的错误"

            if append_rule("risk-rules.md", rule_id, name, trigger, error, level="high"):
                report["rules_added"]["risk"].append({
                    "id": rule_id,
                    "name": name,
                    "trigger": trigger,
                    "message": error,
                })
        else:
            report["skipped"].append(f"无法从错误描述中提取关键词: {error[:50]}...")

    # 手动记录模式
    if pattern:
        keywords = extract_keywords_from_text(pattern)
        if keywords:
            trigger = " OR ".join(keywords)
            rule_id = get_next_rule_id("pattern-rules.md")
            name = f"来自 {project} 的模式"

            if append_rule("pattern-rules.md", rule_id, name, trigger, pattern):
                report["rules_added"]["pattern"].append({
                    "id": rule_id,
                    "name": name,
                    "trigger": trigger,
                    "message": pattern,
                })
        else:
            report["skipped"].append(f"无法从模式描述中提取关键词: {pattern[:50]}...")

    # 自动扫描（如果没有手动指定）
    if not error and not pattern and spec_dir:
        artifacts = scan_project_artifacts(spec_dir)
        report["files_scanned"] = artifacts["files_scanned"]

        # 目前只报告发现，不自动添加（避免噪音）
        if artifacts["errors"]:
            report["potential_errors"] = artifacts["errors"][:3]
        if artifacts["patterns"]:
            report["potential_patterns"] = artifacts["patterns"][:3]

    # 如果有新增规则，触发 lean-spec 同步
    if any(report["rules_added"].values()):
        try:
            sync_to_leanspec()
            report["leanspec_synced"] = True
        except Exception as e:
            report["leanspec_sync_error"] = str(e)

    return report


def sync_to_leanspec() -> None:
    """触发 lean-spec 同步脚本"""
    import subprocess
    sync_script = Path(__file__).parent.parent.parent.parent / "scripts" / "sync_error_to_leanspec.py"
    if sync_script.exists():
        subprocess.run(["python3", str(sync_script)], check=True, capture_output=True)


def print_harvest_report(report: dict) -> None:
    """输出沉淀报告"""
    print(f"\n{'='*60}")
    print(f"📥 经验沉淀报告: {report['project']}")
    print(f"   时间: {report['timestamp']}")
    if report.get("spec_dir"):
        print(f"   Spec: {report['spec_dir']}")
    print(f"{'='*60}\n")

    # 新增规则
    has_new = False
    for rule_type, rules in report["rules_added"].items():
        if rules:
            has_new = True
            type_name = {"risk": "风险规则", "pattern": "模式规则", "context": "上下文规则"}.get(rule_type, rule_type)
            print(f"✅ 新增 {type_name}:")
            for r in rules:
                print(f"   - 规则 {r['id']}: {r['name']}")
                print(f"     触发: {r['trigger']}")
                print(f"     内容: {r['message'][:80]}...")
            print()

    # 跳过的
    if report.get("skipped"):
        print("⏭️  跳过:")
        for s in report["skipped"]:
            print(f"   - {s}")
        print()

    # 潜在发现（自动扫描结果）
    if report.get("potential_errors"):
        print("🔍 发现潜在错误（需手动确认后沉淀）:")
        for e in report["potential_errors"]:
            print(f"   - {e[:80]}...")
        print("   使用: --error \"<描述>\" 手动沉淀")
        print()

    if report.get("potential_patterns"):
        print("🔍 发现潜在模式（需手动确认后沉淀）:")
        for p in report["potential_patterns"]:
            print(f"   - {p[:80]}...")
        print("   使用: --pattern \"<描述>\" 手动沉淀")
        print()

    # lean-spec 同步状态
    if report.get("leanspec_synced"):
        print("📦 lean-spec 同步: ✅ 已同步到 specs/5xx-std-error-*")
        print()
    elif report.get("leanspec_sync_error"):
        print(f"📦 lean-spec 同步: ❌ 失败 - {report['leanspec_sync_error']}")
        print()

    if not has_new and not report.get("potential_errors") and not report.get("potential_patterns"):
        print("ℹ️  无新经验需要沉淀\n")


def main():
    parser = argparse.ArgumentParser(
        description="Experience Index: 自动检索历史经验 + 经验沉淀",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 检索模式
  python3 experience_index.py --scene "wavelet 小波变换" --project wavemamba --types wavelet

  # 沉淀模式 - 自动扫描
  python3 experience_index.py --harvest --project wavemamba

  # 沉淀模式 - 记录错误
  python3 experience_index.py --harvest --project wavemamba --error "AMP 与 wavelet 不兼容"

  # 沉淀模式 - 记录模式
  python3 experience_index.py --harvest --project wavemamba --pattern "使用 autocast(enabled=False)"
        """,
    )

    # 模式选择
    parser.add_argument("--harvest", action="store_true", help="沉淀模式：从项目产物提取经验")

    # 检索模式参数
    parser.add_argument("--scene", help="场景描述（检索模式必需）")
    parser.add_argument("--types", default="", help="创新类型，逗号分隔")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # 共用参数
    parser.add_argument("--project", help="项目 slug（两种模式都需要）")

    # 沉淀模式参数
    parser.add_argument("--error", help="手动记录错误描述（沉淀模式）")
    parser.add_argument("--pattern", help="手动记录模式描述（沉淀模式）")

    args = parser.parse_args()

    # 沉淀模式
    if args.harvest:
        if not args.project:
            parser.error("--harvest 模式需要 --project 参数")

        report = harvest_experience(args.project, args.error, args.pattern)

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_harvest_report(report)
        return

    # 检索模式
    if not args.scene:
        parser.error("检索模式需要 --scene 参数")
    if not args.project:
        parser.error("检索模式需要 --project 参数")

    # 解析类型
    types = [t.strip() for t in args.types.split(",") if t.strip()]

    # 执行检索
    result = experience_index(args.scene, args.project, types)

    # 输出结果
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human_readable(result)


if __name__ == "__main__":
    main()
