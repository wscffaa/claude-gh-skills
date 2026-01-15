#!/usr/bin/env python3
"""
gh-autopilot 报告生成模块。

生成执行完成报告。
"""

from dataclasses import dataclass
from typing import Optional
from state import StateManager, AutopilotState


@dataclass
class ReportConfig:
    """报告配置"""
    show_details: bool = True
    show_failures: bool = True
    format: str = "text"  # text, json, markdown


class ReportGenerator:
    """报告生成器"""

    BOX_CHARS = {
        "tl": "╔", "tr": "╗", "bl": "╚", "br": "╝",
        "h": "═", "v": "║",
        "ml": "╠", "mr": "╣",
    }

    def __init__(self, state: AutopilotState, config: Optional[ReportConfig] = None):
        self.state = state
        self.config = config or ReportConfig()

    def generate(self) -> str:
        """生成报告"""
        if self.config.format == "json":
            return self._generate_json()
        elif self.config.format == "markdown":
            return self._generate_markdown()
        else:
            return self._generate_text()

    def _generate_text(self) -> str:
        """生成文本格式报告"""
        width = 62
        lines = []
        bc = self.BOX_CHARS

        # 标题
        lines.append(bc["tl"] + bc["h"] * width + bc["tr"])
        title = "🚀 gh-autopilot 完成报告"
        lines.append(bc["v"] + title.center(width) + bc["v"])
        lines.append(bc["ml"] + bc["h"] * width + bc["mr"])

        # 基本信息
        lines.append(self._format_row("📋 需求", self._truncate(self.state.prd_title or self.state.input_source, 45), width))
        lines.append(self._format_row("⏱️  耗时", self._calculate_duration(), width))

        # 分隔线
        lines.append(bc["ml"] + bc["h"] * width + bc["mr"])

        # 统计
        lines.append(bc["v"] + " 📊 执行统计".ljust(width) + bc["v"])
        lines.append(self._format_row("├─ Issue 创建", f"{self.state.total_issues} 个", width))
        lines.append(self._format_row("├─ 成功实现", f"{self.state.success_count} 个", width))
        lines.append(self._format_row("├─ PR 合并", f"{len([r for r in self.state.pr_results if r.get('status') == 'merged'])} 个", width))
        lines.append(self._format_row("└─ 失败项", f"{self.state.failed_count} 个", width))

        # 成功的 PR
        if self.config.show_details and self.state.pr_results:
            merged_prs = [r for r in self.state.pr_results if r.get("status") == "merged"]
            if merged_prs:
                lines.append(bc["ml"] + bc["h"] * width + bc["mr"])
                lines.append(bc["v"] + " ✅ 成功合并的 PR:".ljust(width) + bc["v"])
                for pr in merged_prs[:5]:  # 最多显示 5 个
                    pr_text = f"   - #{pr['pr_number']}"
                    lines.append(bc["v"] + pr_text.ljust(width) + bc["v"])
                if len(merged_prs) > 5:
                    lines.append(bc["v"] + f"   ... 还有 {len(merged_prs) - 5} 个".ljust(width) + bc["v"])

        # 失败项
        if self.config.show_failures and self.state.failed_count > 0:
            lines.append(bc["ml"] + bc["h"] * width + bc["mr"])
            lines.append(bc["v"] + " ❌ 失败项（需人工处理）:".ljust(width) + bc["v"])

            failed_issues = [r for r in self.state.issue_results if r.get("status") == "failed"]
            for issue in failed_issues[:3]:  # 最多显示 3 个
                issue_text = f"   - Issue #{issue['number']}: {self._truncate(issue.get('error', 'Unknown'), 35)}"
                lines.append(bc["v"] + issue_text.ljust(width) + bc["v"])
            if len(failed_issues) > 3:
                lines.append(bc["v"] + f"   ... 还有 {len(failed_issues) - 3} 个".ljust(width) + bc["v"])

        # 底部
        lines.append(bc["bl"] + bc["h"] * width + bc["br"])

        return "\n".join(lines)

    def _generate_markdown(self) -> str:
        """生成 Markdown 格式报告"""
        lines = []

        lines.append("# 🚀 gh-autopilot 完成报告")
        lines.append("")
        lines.append(f"**需求**: {self.state.prd_title or self.state.input_source}")
        lines.append(f"**耗时**: {self._calculate_duration()}")
        lines.append("")

        # 统计表格
        lines.append("## 📊 执行统计")
        lines.append("")
        lines.append("| 指标 | 数量 |")
        lines.append("|------|------|")
        lines.append(f"| Issue 创建 | {self.state.total_issues} |")
        lines.append(f"| 成功实现 | {self.state.success_count} |")
        merged_count = len([r for r in self.state.pr_results if r.get("status") == "merged"])
        lines.append(f"| PR 合并 | {merged_count} |")
        lines.append(f"| 失败项 | {self.state.failed_count} |")
        lines.append("")

        # 成功的 PR
        if self.config.show_details:
            merged_prs = [r for r in self.state.pr_results if r.get("status") == "merged"]
            if merged_prs:
                lines.append("## ✅ 成功合并的 PR")
                lines.append("")
                for pr in merged_prs:
                    lines.append(f"- #{pr['pr_number']}")
                lines.append("")

        # 失败项
        if self.config.show_failures and self.state.failed_count > 0:
            lines.append("## ❌ 失败项")
            lines.append("")
            failed_issues = [r for r in self.state.issue_results if r.get("status") == "failed"]
            for issue in failed_issues:
                lines.append(f"- Issue #{issue['number']}: {issue.get('error', 'Unknown')}")
            lines.append("")

        return "\n".join(lines)

    def _generate_json(self) -> str:
        """生成 JSON 格式报告"""
        import json
        return json.dumps({
            "status": self.state.current_phase,
            "input": self.state.input_source,
            "duration": self._calculate_duration(),
            "statistics": {
                "total_issues": self.state.total_issues,
                "success": self.state.success_count,
                "failed": self.state.failed_count,
                "skipped": self.state.skipped_count,
            },
            "pr_results": self.state.pr_results,
            "issue_results": self.state.issue_results,
            "project_number": self.state.project_number,
        }, ensure_ascii=False, indent=2)

    def _format_row(self, label: str, value: str, width: int) -> str:
        """格式化一行"""
        bc = self.BOX_CHARS
        content = f" {label}: {value}"
        return bc["v"] + content.ljust(width) + bc["v"]

    def _truncate(self, text: str, max_len: int) -> str:
        """截断文本"""
        if len(text) <= max_len:
            return text
        return text[:max_len - 3] + "..."

    def _calculate_duration(self) -> str:
        """计算执行时长"""
        from datetime import datetime

        if not self.state.start_time:
            return "N/A"

        start = datetime.fromisoformat(self.state.start_time)
        end = datetime.fromisoformat(self.state.end_time) if self.state.end_time else datetime.now()

        duration = end - start
        minutes, seconds = divmod(int(duration.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)

        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"


def generate_report(state_manager: StateManager, format: str = "text") -> str:
    """便捷函数：生成报告"""
    config = ReportConfig(format=format)
    generator = ReportGenerator(state_manager.state, config)
    return generator.generate()


if __name__ == "__main__":
    # 测试报告生成
    from state import AutopilotState, IssueResult

    # 模拟状态
    state = AutopilotState(
        run_id="20240116_120000",
        input_source="docs/feature-prd.md",
        start_time="2024-01-16T12:00:00",
        end_time="2024-01-16T12:30:00",
        current_phase="completed",
        prd_title="用户认证功能",
        total_issues=5,
        success_count=4,
        failed_count=1,
        skipped_count=0,
        issue_results=[
            {"number": 1, "title": "实现登录", "status": "success", "pr_number": 10},
            {"number": 2, "title": "实现注册", "status": "success", "pr_number": 11},
            {"number": 3, "title": "实现登出", "status": "success", "pr_number": 12},
            {"number": 4, "title": "密码重置", "status": "success", "pr_number": 13},
            {"number": 5, "title": "OAuth 集成", "status": "failed", "error": "API 限制"},
        ],
        pr_results=[
            {"pr_number": 10, "status": "merged"},
            {"pr_number": 11, "status": "merged"},
            {"pr_number": 12, "status": "merged"},
            {"pr_number": 13, "status": "merged"},
        ],
    )

    # 生成报告
    generator = ReportGenerator(state)

    print("=== Text Report ===")
    print(generator.generate())

    print("\n=== Markdown Report ===")
    generator.config.format = "markdown"
    print(generator.generate())
