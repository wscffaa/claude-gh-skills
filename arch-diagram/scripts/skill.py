#!/usr/bin/env python3
"""
学术架构图生成器 - 文件系统即状态机设计 v2.3

设计理念：
- 每个任务有独立目录，用文件存在标记进度
- 支持断点续传：程序随时可中断，重启后自动接续
- 支持渐进式迭代：每次修改生成新版本，保留历史
- 支持自动专家审阅：三专家并行审阅，自动迭代优化
- 极致透明：所有中间产物可查看，便于调试

目录结构:
experiments/visualizations/architecture/{task_id}/
├── input.json              # [阶段0] 输入参数存档
├── code_snapshot.py        # [阶段0] 代码快照
├── analysis.md             # [阶段1] 代码分析结果
├── architect_prompt.md     # [阶段2] 发给 Architect 的 prompt
├── visual_schema.md        # [阶段2] Visual Schema 输出
├── versions/               # [阶段3] 渲染版本管理
│   ├── v1/
│   │   ├── renderer_prompt.md
│   │   ├── diagram.jpg
│   │   ├── response.txt
│   │   └── review/                 # [阶段3.5] 专家审阅
│   │       ├── codex_prompt.md
│   │       ├── codex_review.md
│   │       ├── gemini_prompt.md
│   │       ├── gemini_review.md
│   │       ├── claude_prompt.md
│   │       ├── claude_review.md
│   │       ├── consensus.json      # 共识结果
│   │       └── iteration_decision.json  # 迭代决策
│   ├── v2/
│   │   ├── feedback.md     # 用户迭代反馈
│   │   ├── renderer_prompt.md
│   │   ├── diagram.jpg
│   │   └── response.txt
│   └── ...
└── latest_version.txt      # 最新版本号

使用方式:
    # 新建任务
    python3 skill.py --arch_code_path basicofr/archs/freqmamba_arch.py

    # 恢复任务（断点续传）
    python3 skill.py --resume freqmamba_arch_20251231_160000

    # 渐进式迭代（基于上一版本 + 反馈）
    python3 skill.py --resume task_id --iterate --feedback "修改文字标注为数学符号风格"

    # 启用自动专家审阅（三专家并行审阅，自动迭代直到通过）
    python3 skill.py --arch_code_path arch.py --auto-review

    # 自定义审阅参数
    python3 skill.py --resume task_id --auto-review --review-threshold 7 --max-iterations 3

    # 列出所有任务
    python3 skill.py --list

    # 强制重新执行
    python3 skill.py --resume freqmamba_arch_20251231_160000 --force
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List


# =============================================================================
# 配置
# =============================================================================

DEFAULT_BASE_DIR = "experiments/visualizations/architecture"
LESSONS_SPEC_PATH = "specs/406-arch-diagram-lessons/README.md"


# =============================================================================
# Lessons Learned 系统
# =============================================================================

def load_lessons_from_spec() -> List[Dict[str, Any]]:
    """从 Spec 文件读取已审批的 Lessons

    返回格式:
    [
        {
            "id": "LESSON-001",
            "title": "文本重叠",
            "category": "text-overlap",
            "pattern": "...",
            "solution": "...",
            "prompt_enhancement": "..."
        },
        ...
    ]
    """
    spec_path = Path(LESSONS_SPEC_PATH)
    if not spec_path.exists():
        # 尝试相对于项目根目录
        spec_path = Path.cwd() / LESSONS_SPEC_PATH

    if not spec_path.exists():
        print(f"   ⚠️  Lessons Spec 不存在: {LESSONS_SPEC_PATH}", file=sys.stderr)
        return []

    try:
        with open(spec_path, 'r', encoding='utf-8') as f:
            content = f.read()

        lessons = []
        import re

        # 匹配 ### LESSON-XXX: 标题 格式的块
        lesson_pattern = r'### (LESSON-\d+): (.+?)\n(.*?)(?=\n### |\n---|\Z)'
        matches = re.findall(lesson_pattern, content, re.DOTALL)

        for lesson_id, title, body in matches:
            lesson = {
                "id": lesson_id,
                "title": title.strip(),
            }

            # 提取各字段
            category_match = re.search(r'\*\*Category\*\*:\s*`([^`]+)`', body)
            if category_match:
                lesson["category"] = category_match.group(1)

            pattern_match = re.search(r'\*\*Pattern\*\*:\s*(.+?)(?=\n-|\Z)', body)
            if pattern_match:
                lesson["pattern"] = pattern_match.group(1).strip()

            solution_match = re.search(r'\*\*Solution\*\*:\s*(.+?)(?=\n-|\Z)', body)
            if solution_match:
                lesson["solution"] = solution_match.group(1).strip()

            # 提取 Prompt Enhancement（代码块内容）
            enhancement_match = re.search(r'\*\*Prompt Enhancement\*\*:\s*```\s*(.+?)```', body, re.DOTALL)
            if enhancement_match:
                lesson["prompt_enhancement"] = enhancement_match.group(1).strip()

            lessons.append(lesson)

        return lessons

    except Exception as e:
        print(f"   ⚠️  读取 Lessons Spec 失败: {e}", file=sys.stderr)
        return []


def generate_lessons_prompt_section(lessons: List[Dict[str, Any]]) -> str:
    """将 Lessons 转换为 Prompt 注入段落"""
    if not lessons:
        return ""

    lines = [
        "",
        "[LESSONS LEARNED - Historical Issues to Avoid]",
        "The following issues have been identified in previous generations. STRICTLY follow these guidelines:",
        ""
    ]

    for lesson in lessons:
        enhancement = lesson.get("prompt_enhancement", "")
        if enhancement:
            lines.append(f"# {lesson.get('id', 'LESSON')}: {lesson.get('title', 'Unknown')}")
            lines.append(enhancement)
            lines.append("")

    lines.append("[END LESSONS LEARNED]")
    lines.append("")

    return "\n".join(lines)


def extract_new_lessons_from_review(consensus: Dict[str, Any], reviews: Dict[str, str]) -> List[Dict[str, Any]]:
    """从专家审阅中提取新的潜在 Lessons

    返回待审批的 Lesson 列表
    """
    issues = consensus.get("issues", [])
    if not issues:
        return []

    # 只提取高优先级问题（多专家提及）
    high_priority = [i for i in issues if i.get("priority") == "high"]

    pending_lessons = []
    for i, issue in enumerate(high_priority[:3]):  # 最多3条
        pending_lessons.append({
            "id": f"PENDING-{i+1:03d}",
            "title": issue.get("issue", "Unknown")[:50],
            "category": "unknown",  # 需要用户分类
            "pattern": issue.get("issue", ""),
            "mentioned_by": issue.get("mentioned_by", []),
            "proposed_solution": "",  # 需要用户填写
            "proposed_enhancement": "",  # 需要用户填写
        })

    return pending_lessons


def append_pending_lesson_to_spec(lesson: Dict[str, Any], task_id: str) -> bool:
    """将待审批的 Lesson 追加到 Spec 的 Pending 区域"""
    spec_path = Path.cwd() / LESSONS_SPEC_PATH

    if not spec_path.exists():
        print(f"   ⚠️  Lessons Spec 不存在", file=sys.stderr)
        return False

    try:
        with open(spec_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 找到 "Pending Lessons" 区域
        pending_marker = "## Pending Lessons (待审批)"
        if pending_marker not in content:
            print(f"   ⚠️  未找到 Pending Lessons 区域", file=sys.stderr)
            return False

        # 构建新的 pending lesson 条目
        new_entry = f"""
### {lesson['id']}: {lesson['title']}
- **Category**: `{lesson.get('category', 'unknown')}`
- **Pattern**: {lesson.get('pattern', 'TBD')}
- **Mentioned By**: {', '.join(lesson.get('mentioned_by', []))}
- **Proposed Solution**: *待填写*
- **Proposed Prompt Enhancement**:
  ```
  *待填写具体的 Prompt 指令*
  ```
- **Discovered**: {datetime.now().strftime('%Y-%m-%d')}
- **Source**: {task_id}
"""

        # 替换 "*当前无待审批项*" 或追加到 Pending 区域
        if "*当前无待审批项*" in content:
            content = content.replace("*当前无待审批项*", new_entry.strip())
        else:
            # 在 "---" 分隔符前插入
            insert_pos = content.find("---", content.find(pending_marker))
            if insert_pos > 0:
                content = content[:insert_pos] + new_entry + "\n" + content[insert_pos:]

        with open(spec_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return True

    except Exception as e:
        print(f"   ⚠️  追加 Pending Lesson 失败: {e}", file=sys.stderr)
        return False


def get_next_lesson_number() -> int:
    """获取下一个可用的 Lesson 编号"""
    spec_path = Path.cwd() / LESSONS_SPEC_PATH
    if not spec_path.exists():
        return 1

    with open(spec_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 查找所有 LESSON-XXX 编号
    matches = re.findall(r'### LESSON-(\d+):', content)
    if not matches:
        return 1
    return max(int(m) for m in matches) + 1


def approve_lesson_to_spec(lesson: Dict[str, Any], task_id: str) -> bool:
    """将 Lesson 直接写入已审批区域（同步审批模式）

    Args:
        lesson: 包含 title, category, pattern, solution, prompt_enhancement 的字典
        task_id: 来源任务 ID

    Returns:
        是否成功添加
    """
    spec_path = Path.cwd() / LESSONS_SPEC_PATH

    if not spec_path.exists():
        print(f"   ⚠️  Lessons Spec 不存在", file=sys.stderr)
        return False

    try:
        with open(spec_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 获取下一个编号
        lesson_num = get_next_lesson_number()
        lesson_id = f"LESSON-{lesson_num:03d}"

        # 构建已审批 lesson 条目
        prompt_enhancement = lesson.get('prompt_enhancement', '*待填写*')
        new_entry = f"""
### {lesson_id}: {lesson['title']}
- **Category**: `{lesson.get('category', 'unknown')}`
- **Pattern**: {lesson.get('pattern', 'TBD')}
- **Solution**: {lesson.get('solution', '*待填写*')}
- **Prompt Enhancement**:
  ```
  {prompt_enhancement}
  ```
- **Approved**: {datetime.now().strftime('%Y-%m-%d')}
- **Source**: {task_id}
"""

        # 找到 "Lessons (用户已审批)" 区域的末尾，在 "Pending Lessons" 之前插入
        pending_marker = "## Pending Lessons (待审批)"
        if pending_marker in content:
            # 在 Pending Lessons 之前插入新 Lesson
            insert_pos = content.find(pending_marker)
            # 往前找到最后一个 ---
            sep_pos = content.rfind("---", 0, insert_pos)
            if sep_pos > 0:
                content = content[:sep_pos] + new_entry + "\n" + content[sep_pos:]
            else:
                content = content[:insert_pos] + new_entry + "\n" + content[insert_pos:]

        with open(spec_path, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"   ✅ 已添加 {lesson_id}: {lesson['title'][:30]}...")
        return True

    except Exception as e:
        print(f"   ⚠️  添加 Lesson 失败: {e}", file=sys.stderr)
        return False


# =============================================================================
# TaskState: 文件系统状态机（支持版本管理）
# =============================================================================

class TaskState:
    """任务状态管理器 - 用文件系统追踪进度，支持版本管理"""

    def __init__(self, task_id: str, base_dir: str = DEFAULT_BASE_DIR):
        self.task_id = task_id
        self.task_dir = Path(base_dir) / task_id
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir = self.task_dir / "versions"

    # --- 文件路径属性 ---
    @property
    def input_json(self) -> Path:
        """输入参数存档"""
        return self.task_dir / "input.json"

    @property
    def code_snapshot(self) -> Path:
        """代码快照"""
        return self.task_dir / "code_snapshot.py"

    @property
    def analysis_md(self) -> Path:
        """代码分析结果"""
        return self.task_dir / "analysis.md"

    @property
    def architect_prompt(self) -> Path:
        """Architect 完整 prompt"""
        return self.task_dir / "architect_prompt.md"

    @property
    def visual_schema(self) -> Path:
        """Visual Schema 输出"""
        return self.task_dir / "visual_schema.md"

    @property
    def latest_version_file(self) -> Path:
        """最新版本号文件"""
        return self.task_dir / "latest_version.txt"

    @property
    def review_config(self) -> Path:
        """审阅配置文件"""
        return self.task_dir / "review_config.json"

    # --- 版本管理方法 ---
    def get_latest_version(self) -> int:
        """获取最新版本号"""
        if self.latest_version_file.exists():
            try:
                return int(self.latest_version_file.read_text().strip())
            except ValueError:
                pass

        # 从 versions 目录推断
        if self.versions_dir.exists():
            versions = [d.name for d in self.versions_dir.iterdir() if d.is_dir() and d.name.startswith('v')]
            if versions:
                nums = [int(v[1:]) for v in versions if v[1:].isdigit()]
                if nums:
                    return max(nums)
        return 0

    def set_latest_version(self, version: int):
        """设置最新版本号"""
        self.latest_version_file.write_text(str(version))

    def get_version_dir(self, version: int) -> Path:
        """获取指定版本目录"""
        return self.versions_dir / f"v{version}"

    def get_version_review_dir(self, version: int) -> Path:
        """获取指定版本的审阅目录"""
        return self.get_version_dir(version) / "review"

    def get_review_file(self, version: int, filename: str) -> Path:
        """获取审阅相关文件路径"""
        return self.get_version_review_dir(version) / filename

    def get_latest_version_dir(self) -> Optional[Path]:
        """获取最新版本目录"""
        version = self.get_latest_version()
        if version > 0:
            return self.get_version_dir(version)
        return None

    def create_new_version(self) -> tuple[int, Path]:
        """创建新版本目录，返回 (版本号, 目录路径)"""
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        new_version = self.get_latest_version() + 1
        version_dir = self.get_version_dir(new_version)
        version_dir.mkdir(parents=True, exist_ok=True)
        return new_version, version_dir

    def get_version_diagram(self, version: int) -> Optional[Path]:
        """获取指定版本的架构图"""
        diagram = self.get_version_dir(version) / "diagram.jpg"
        return diagram if diagram.exists() else None

    def get_latest_diagram(self) -> Optional[Path]:
        """获取最新版本的架构图"""
        version = self.get_latest_version()
        if version > 0:
            return self.get_version_diagram(version)
        return None

    def list_versions(self) -> List[Dict[str, Any]]:
        """列出所有版本信息"""
        versions = []
        if not self.versions_dir.exists():
            return versions

        for vdir in sorted(self.versions_dir.iterdir()):
            if vdir.is_dir() and vdir.name.startswith('v'):
                try:
                    num = int(vdir.name[1:])
                    info = {
                        "version": num,
                        "dir": vdir,
                        "has_diagram": (vdir / "diagram.jpg").exists(),
                        "has_feedback": (vdir / "feedback.md").exists(),
                    }
                    # 获取创建时间
                    prompt_file = vdir / "renderer_prompt.md"
                    if prompt_file.exists():
                        info["created"] = datetime.fromtimestamp(prompt_file.stat().st_mtime)
                    versions.append(info)
                except ValueError:
                    continue

        return sorted(versions, key=lambda x: x["version"])

    # --- 兼容性: 旧版 diagram.jpg 迁移到 v1 ---
    def migrate_legacy_diagram(self):
        """将旧版 diagram.jpg 迁移到 versions/v1/"""
        legacy_diagram = self.task_dir / "diagram.jpg"
        legacy_prompt = self.task_dir / "renderer_prompt.md"
        legacy_response = self.task_dir / "response.txt"

        if legacy_diagram.exists() and not self.versions_dir.exists():
            print("📦 迁移旧版文件到 versions/v1/...")
            v1_dir = self.get_version_dir(1)
            v1_dir.mkdir(parents=True, exist_ok=True)

            shutil.move(str(legacy_diagram), str(v1_dir / "diagram.jpg"))
            if legacy_prompt.exists():
                shutil.move(str(legacy_prompt), str(v1_dir / "renderer_prompt.md"))
            if legacy_response.exists():
                shutil.move(str(legacy_response), str(v1_dir / "response.txt"))

            self.set_latest_version(1)
            print(f"   ✓ 已迁移到: {v1_dir}")

    # --- 状态检查 ---
    def stage_complete(self, stage: str) -> bool:
        """检查某阶段是否完成（文件存在即完成）"""
        stage_files = {
            "input": self.input_json,
            "snapshot": self.code_snapshot,
            "analysis": self.analysis_md,
            "schema": self.visual_schema,
            "diagram": self.get_latest_diagram(),
        }
        target = stage_files.get(stage)
        return target is not None and target.exists()

    def review_stage_complete(self, version: int, stage: str) -> bool:
        """检查版本的审阅阶段是否完成

        stage 可选值:
        - "codex": codex_review.md 存在
        - "gemini": gemini_review.md 存在
        - "claude": claude_review.md 存在
        - "consensus": consensus.json 存在
        - "decision": iteration_decision.json 存在
        """
        review_dir = self.get_version_review_dir(version)
        stage_files = {
            "codex": "codex_review.md",
            "gemini": "gemini_review.md",
            "claude": "claude_review.md",
            "consensus": "consensus.json",
            "decision": "iteration_decision.json",
        }
        filename = stage_files.get(stage)
        if not filename:
            return False
        return (review_dir / filename).exists()

    def get_review_status(self, version: int) -> Dict[str, bool]:
        """获取版本的所有审阅状态"""
        return {
            "codex": self.review_stage_complete(version, "codex"),
            "gemini": self.review_stage_complete(version, "gemini"),
            "claude": self.review_stage_complete(version, "claude"),
            "consensus": self.review_stage_complete(version, "consensus"),
            "decision": self.review_stage_complete(version, "decision"),
        }

    def get_status(self) -> Dict[str, Any]:
        """获取所有阶段状态"""
        latest_version = self.get_latest_version()
        latest_diagram = self.get_latest_diagram()

        return {
            "input": self.input_json.exists(),
            "snapshot": self.code_snapshot.exists(),
            "analysis": self.analysis_md.exists(),
            "architect_prompt": self.architect_prompt.exists(),
            "schema": self.visual_schema.exists(),
            "latest_version": latest_version,
            "has_diagram": latest_diagram is not None and latest_diagram.exists(),
        }

    def print_status(self):
        """打印当前状态"""
        status = self.get_status()
        print(f"\n🎯 Task: {self.task_id}")
        print("━" * 50)

        stages = [
            ("input", "input.json", "保存输入参数"),
            ("snapshot", "code_snapshot.py", "代码快照"),
            ("analysis", "analysis.md", "代码分析"),
            ("architect_prompt", "architect_prompt.md", "Architect Prompt"),
            ("schema", "visual_schema.md", "Visual Schema"),
        ]

        for key, filename, desc in stages:
            icon = "✓" if status[key] else " "
            print(f"[{icon}] {filename:<22} ({desc})")

        # 版本信息
        print("━" * 50)
        versions = self.list_versions()
        if versions:
            print(f"📂 版本历史 ({len(versions)} 个版本):")
            for v in versions:
                icon = "✓" if v["has_diagram"] else "⏳"
                feedback_mark = " [有反馈]" if v["has_feedback"] else ""
                created = v.get("created", "")
                if created:
                    created = created.strftime("%H:%M:%S")
                print(f"   [{icon}] v{v['version']:<3} {created}{feedback_mark}")
                version_dir = v["dir"]
                diagram_file = version_dir / "diagram.jpg"
                version_sub_items = []

                if diagram_file.exists():
                    size_mb = diagram_file.stat().st_size / (1024 * 1024)
                    version_sub_items.append({
                        "type": "diagram",
                        "text": f"diagram.jpg ({size_mb:.1f}MB)"
                    })

                review_status = self.get_review_status(v["version"])
                consensus_data = self.load_consensus(v["version"])
                decision_data = self.load_iteration_decision(v["version"])
                has_review = any(review_status.values()) or consensus_data is not None or decision_data is not None

                if has_review:
                    def _format_score(val: Any) -> Optional[str]:
                        if isinstance(val, (int, float)):
                            return f"{val:.1f}" if isinstance(val, float) else str(val)
                        return None

                    review_icon = "✅" if review_status.get("consensus") or review_status.get("decision") else "⏳"
                    avg_score = None
                    stage_scores: Dict[str, Any] = {}

                    if isinstance(consensus_data, dict):
                        avg_score = consensus_data.get("average_score") or consensus_data.get("score")
                        scores_map = consensus_data.get("scores")
                        if isinstance(scores_map, dict):
                            stage_scores.update(scores_map)
                        else:
                            for key in ("codex", "gemini", "claude", "consensus"):
                                if key in consensus_data:
                                    stage_scores[key] = consensus_data.get(key)
                        if avg_score is None and stage_scores:
                            numeric_scores = [s for s in stage_scores.values() if isinstance(s, (int, float))]
                            if numeric_scores:
                                avg_score = sum(numeric_scores) / len(numeric_scores)

                    score_text = _format_score(avg_score)
                    decision_text = None
                    if isinstance(decision_data, dict):
                        decision_text = decision_data.get("decision") or decision_data.get("action") or decision_data.get("result")
                    if decision_text is None and isinstance(consensus_data, dict):
                        decision_text = consensus_data.get("decision") or consensus_data.get("action")

                    review_summary_parts = []
                    if score_text:
                        review_summary_parts.append(f"{score_text}/10")
                    if decision_text:
                        arrow = "→ " if review_summary_parts else ""
                        review_summary_parts.append(f"{arrow}{decision_text}")
                    review_summary = " ".join(review_summary_parts).strip()
                    summary_line = f"review: {review_icon}"
                    if review_summary:
                        summary_line += f" {review_summary}"

                    stage_lines = []
                    for stage_key, stage_label in [
                        ("codex", "codex"),
                        ("gemini", "gemini"),
                        ("claude", "claude"),
                        ("consensus", "consensus"),
                        ("decision", "decision"),
                    ]:
                        stage_icon = "✓" if review_status.get(stage_key) else " "
                        line = f"[{stage_icon}] {stage_label}"
                        stage_score = _format_score(stage_scores.get(stage_key))
                        if stage_key in ("codex", "gemini", "claude") and stage_score:
                            line += f": {stage_score}/10"
                        elif stage_key == "consensus" and score_text:
                            line += f": {score_text}/10"
                        elif stage_key == "decision" and decision_text:
                            line += f": {decision_text}"
                        stage_lines.append(line)

                    version_sub_items.append({
                        "type": "review",
                        "text": summary_line.strip(),
                        "lines": stage_lines
                    })

                for idx, item in enumerate(version_sub_items):
                    connector = "└──" if idx == len(version_sub_items) - 1 else "├──"
                    prefix = f"        {connector} "
                    if item["type"] == "diagram":
                        print(f"{prefix}{item['text']}")
                    elif item["type"] == "review":
                        print(f"{prefix}{item['text']}")
                        nested_prefix = "        " + ("    " if idx == len(version_sub_items) - 1 else "│   ")
                        for ridx, line in enumerate(item["lines"]):
                            nested_connector = "└──" if ridx == len(item["lines"]) - 1 else "├──"
                            print(f"{nested_prefix}{nested_connector} {line}")
        else:
            print("📂 版本历史: (无)")

        print("━" * 50)

    def clear(self):
        """清除所有文件（强制重新执行）"""
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir)
        self.task_dir.mkdir(parents=True, exist_ok=True)

    def clear_versions(self):
        """只清除版本目录（保留分析和 schema）"""
        if self.versions_dir.exists():
            shutil.rmtree(self.versions_dir)
        self.latest_version_file.unlink(missing_ok=True)

    def load_input(self) -> Optional[Dict[str, Any]]:
        """加载已保存的输入参数"""
        if self.input_json.exists():
            with open(self.input_json, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def load_consensus(self, version: int) -> Optional[Dict[str, Any]]:
        """加载版本的共识结果"""
        consensus_file = self.get_review_file(version, "consensus.json")
        if consensus_file.exists():
            with open(consensus_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def load_iteration_decision(self, version: int) -> Optional[Dict[str, Any]]:
        """加载版本的迭代决策"""
        decision_file = self.get_review_file(version, "iteration_decision.json")
        if decision_file.exists():
            with open(decision_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None


# =============================================================================
# 模板和配置加载（保持原有函数）
# =============================================================================

def load_architect_template(inject_lessons: bool = True) -> str:
    """加载 Architect Prompt 模板，可选注入历史 Lessons"""
    skill_template = Path(__file__).parent.parent / "templates" / "architect_prompt.txt"

    template_content = ""
    if skill_template.exists():
        with open(skill_template, 'r', encoding='utf-8') as f:
            template_content = f.read()
    else:
        project_template = Path.cwd() / "01_Architect_Prompt_Full.md"
        if project_template.exists():
            print(f"⚠️  使用项目根目录模板: {project_template}", file=sys.stderr)
            with open(project_template, 'r', encoding='utf-8') as f:
                content = f.read()
                if "```" in content:
                    start = content.find("```") + 3
                    end = content.rfind("```")
                    if end > start:
                        template_content = content[start:end].strip()
                else:
                    template_content = content
        else:
            print(f"❌ 未找到 Architect 模板文件", file=sys.stderr)
            sys.exit(1)

    # 注入历史 Lessons
    if inject_lessons:
        lessons = load_lessons_from_spec()
        if lessons:
            lessons_section = generate_lessons_prompt_section(lessons)
            print(f"   📚 已注入 {len(lessons)} 条历史经验到 Architect Prompt")
            # 在模板末尾添加 Lessons
            template_content = template_content + "\n" + lessons_section

    return template_content


def load_renderer_template(use_advanced: bool = False, inject_lessons: bool = True) -> str:
    """加载 Renderer Prompt 模板，可选注入历史 Lessons"""
    template_name = "renderer_prompt_advanced.txt" if use_advanced else "renderer_prompt_basic.txt"
    skill_template = Path(__file__).parent.parent / "templates" / template_name

    template_content = ""
    if skill_template.exists():
        with open(skill_template, 'r', encoding='utf-8') as f:
            template_content = f.read()
    else:
        project_template = Path.cwd() / "02_Renderer_Prompt_Full.md"
        if project_template.exists():
            print(f"⚠️  使用项目根目录模板: {project_template}", file=sys.stderr)
            with open(project_template, 'r', encoding='utf-8') as f:
                content = f.read()
                if "```" in content:
                    start = content.find("```") + 3
                    end = content.find("```", start)
                    if end > start:
                        template_content = content[start:end].strip()
                else:
                    template_content = content
        else:
            print(f"❌ 未找到 Renderer 模板文件", file=sys.stderr)
            sys.exit(1)

    # 注入历史 Lessons
    if inject_lessons:
        lessons = load_lessons_from_spec()
        if lessons:
            lessons_section = generate_lessons_prompt_section(lessons)
            print(f"   📚 已注入 {len(lessons)} 条历史经验到 Renderer Prompt")
            # 在模板开头添加 Lessons（优先级更高）
            template_content = lessons_section + "\n" + template_content

    return template_content


def load_iteration_template() -> str:
    """加载迭代 Prompt 模板"""
    template_path = Path(__file__).parent.parent / "templates" / "renderer_prompt_iterate.txt"

    if template_path.exists():
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()

    # 默认迭代模板
    return """**Architecture Diagram Iterative Refinement**

I have provided the previous version of the architecture diagram. Please refine it based on the following feedback while maintaining all correct elements.

**IMPORTANT: This is an ITERATIVE refinement task.**
- Keep everything that is correct from the previous version
- Only modify the specific aspects mentioned in the feedback
- Maintain the same overall layout and structure unless explicitly asked to change

**Previous Version Feedback & Refinement Request:**
{feedback}

**Original Visual Schema (for reference):**
{visual_schema_content}

**Style Requirements:**
- Maintain CVPR/NeurIPS academic standard
- Use mathematical notation for labels (e.g., $X_t$, $M_{fused}$)
- Keep 3D isometric cuboids for feature maps
- Light pastel colors, thin black outlines
- Clean legend at bottom

**Output:** Generate the refined architecture diagram incorporating the feedback while preserving all correct elements from the previous version.
"""


def load_model_config() -> Dict[str, str]:
    """加载模型配置"""
    config_file = Path(__file__).parent / 'config.json'
    default_config = {
        'architect': 'gpt-5.2',
        'renderer': 'gemini-3-pro-image-16x9-4k'
    }

    if not config_file.exists():
        return default_config

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        active_profile = config_data.get('active_profile', 'default')
        profile = config_data.get('profiles', {}).get(active_profile, {})

        return {
            'architect': profile.get('architect', default_config['architect']),
            'renderer': profile.get('renderer', default_config['renderer'])
        }
    except Exception as e:
        print(f"⚠️  配置文件读取失败，使用默认配置: {e}", file=sys.stderr)
        return default_config


# =============================================================================
# Pipeline 阶段函数
# =============================================================================

def save_input(state: TaskState, args: argparse.Namespace):
    """阶段0: 保存输入参数"""
    print("\n📥 [阶段0] 保存输入参数...")

    input_data = {
        "task_id": state.task_id,
        "created_at": datetime.now().isoformat(),
        "arch_code_path": args.arch_code_path,
        "paper_content": args.paper_content,
        "model_architect": args.model_architect,
        "model_renderer": args.model_renderer,
        "reference_images": args.reference_images,
    }

    with open(state.input_json, 'w', encoding='utf-8') as f:
        json.dump(input_data, f, ensure_ascii=False, indent=2)

    print(f"   ✓ 已保存: {state.input_json}")

    # 保存代码快照
    if args.arch_code_path and Path(args.arch_code_path).exists():
        shutil.copy(args.arch_code_path, state.code_snapshot)
        print(f"   ✓ 代码快照: {state.code_snapshot}")


def run_analysis(state: TaskState):
    """阶段1: 代码分析"""
    print("\n🔍 [阶段1] 代码分析...")

    input_data = state.load_input()
    if not input_data:
        print("❌ 未找到输入参数", file=sys.stderr)
        return False

    arch_code_path = input_data.get('arch_code_path')
    if not arch_code_path:
        # 如果没有代码路径，使用 paper_content
        paper_content = input_data.get('paper_content', '')
        if paper_content:
            with open(state.analysis_md, 'w', encoding='utf-8') as f:
                f.write(f"# 论文内容分析\n\n{paper_content}")
            print(f"   ✓ 直接使用论文内容: {state.analysis_md}")
            return True
        print("❌ 无输入内容", file=sys.stderr)
        return False

    analysis_prompt = f"""分析架构代码 @{arch_code_path}，提取以下信息用于生成架构图：

1. **核心模块识别**:
   - 列出主要的网络模块类（如 Encoder, Decoder, Attention, etc.）
   - 识别创新模块（标注为 Innovation A/B/C）

2. **数据流分析**:
   - 输入张量形状和类型
   - 主要的前向传播路径
   - 输出张量形状和类型
   - 关键的中间特征（feature maps）

3. **创新点标注**:
   - 识别论文的核心贡献模块
   - 标记这些模块在数据流中的位置

4. **架构描述**:
   - 用自然语言描述整体架构布局
   - 适合用于 Architect Prompt 的输入格式

输出格式：清晰的分段 Markdown 文本，包含上述 4 个部分。
"""

    try:
        print(f"   调用 Codex 分析: {arch_code_path}")

        result = subprocess.run(
            ["codex", "exec", "-"],
            input=analysis_prompt,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            print(f"❌ 代码分析失败: {result.stderr}", file=sys.stderr)
            return False

        analysis_result = result.stdout.strip()

        with open(state.analysis_md, 'w', encoding='utf-8') as f:
            f.write(f"# 代码分析结果\n\n")
            f.write(f"**源文件**: `{arch_code_path}`\n")
            f.write(f"**分析时间**: {datetime.now().isoformat()}\n\n")
            f.write("---\n\n")
            f.write(analysis_result)

        print(f"   ✓ 已保存: {state.analysis_md} ({len(analysis_result)} chars)")
        return True

    except subprocess.TimeoutExpired:
        print("❌ 代码分析超时", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("❌ 未找到 codex CLI", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ 代码分析异常: {e}", file=sys.stderr)
        return False


def run_architect(state: TaskState):
    """阶段2: 生成 Visual Schema"""
    print("\n🏗️  [阶段2] 生成 Visual Schema...")

    # 读取分析结果
    if not state.analysis_md.exists():
        print("❌ 未找到代码分析结果", file=sys.stderr)
        return False

    with open(state.analysis_md, 'r', encoding='utf-8') as f:
        paper_content = f.read()

    input_data = state.load_input()
    model = input_data.get('model_architect', 'gpt-5.2') if input_data else 'gpt-5.2'

    # 加载模板并构建 prompt
    architect_template = load_architect_template()
    full_prompt = architect_template.replace("{paper_content}", paper_content)

    # 保存完整 prompt（便于调试）
    with open(state.architect_prompt, 'w', encoding='utf-8') as f:
        f.write(f"# Architect Prompt\n\n")
        f.write(f"**模型**: {model}\n")
        f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
        f.write("---\n\n")
        f.write(full_prompt)
    print(f"   ✓ Prompt 已保存: {state.architect_prompt}")

    try:
        print(f"   调用 {model} 生成 Visual Schema...")

        env = os.environ.copy()
        env["CODEX_MODEL"] = model

        result = subprocess.run(
            ["codex", "exec", "-"],
            input=full_prompt,
            capture_output=True,
            text=True,
            env=env,
            timeout=300
        )

        if result.returncode != 0:
            print(f"❌ Architect 调用失败: {result.stderr}", file=sys.stderr)
            return False

        visual_schema = result.stdout.strip()

        # 保存 Visual Schema
        with open(state.visual_schema, 'w', encoding='utf-8') as f:
            f.write(f"# Visual Schema\n\n")
            f.write(f"**生成时间**: {datetime.now().isoformat()}\n")
            f.write(f"**模型**: {model}\n\n")
            f.write("---\n\n")
            f.write(visual_schema)

        print(f"   ✓ 已保存: {state.visual_schema} ({len(visual_schema)} chars)")
        return True

    except subprocess.TimeoutExpired:
        print("❌ Architect 调用超时", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("❌ 未找到 codex CLI", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ Architect 调用异常: {e}", file=sys.stderr)
        return False


def run_renderer(state: TaskState, feedback: Optional[str] = None, extra_reference_images: Optional[List[str]] = None):
    """阶段3: 渲染架构图（支持版本迭代）

    迭代模式使用 Gemini 的 inpainting 功能：
    - 将上一版本图作为基础图（第一个 content part）
    - 使用编辑指令而非重新生成
    - 模型会基于原图进行局部修改，保持连续性
    """

    # 迁移旧版文件（如果存在）
    state.migrate_legacy_diagram()

    # 创建新版本
    new_version, version_dir = state.create_new_version()
    is_iteration = new_version > 1 and feedback is not None

    if is_iteration:
        print(f"\n🎨 [阶段3] 渲染架构图 v{new_version}（图像编辑模式）...")
    else:
        print(f"\n🎨 [阶段3] 渲染架构图 v{new_version}...")

    # 读取 Visual Schema
    if not state.visual_schema.exists():
        print("❌ 未找到 Visual Schema", file=sys.stderr)
        return False

    with open(state.visual_schema, 'r', encoding='utf-8') as f:
        visual_schema = f.read()

    input_data = state.load_input()
    model = input_data.get('model_renderer', 'gemini-3-pro-image-16x9-4k') if input_data else 'gemini-3-pro-image-16x9-4k'
    reference_images = input_data.get('reference_images') if input_data else None

    # 合并额外参考图
    if extra_reference_images:
        reference_images = (reference_images or []) + extra_reference_images

    # 提取 Schema 核心内容
    schema_content = visual_schema
    if "---BEGIN PROMPT---" in visual_schema:
        start_idx = visual_schema.find("---BEGIN PROMPT---") + len("---BEGIN PROMPT---")
        end_idx = visual_schema.find("---END PROMPT---")
        if end_idx > start_idx:
            schema_content = visual_schema[start_idx:end_idx].strip()

    # 获取上一版本图（用于迭代编辑）
    prev_diagram = None
    if is_iteration:
        prev_diagram = state.get_version_diagram(new_version - 1)
        if prev_diagram and prev_diagram.exists():
            print(f"   📎 基础图（将被编辑）: v{new_version - 1}")
        else:
            print(f"   ⚠️  未找到上一版本图，将重新生成")
            is_iteration = False

    # 构建 prompt
    if is_iteration:
        # 迭代模式：使用 inpainting 风格的编辑指令
        iteration_template = load_iteration_template()
        full_prompt = iteration_template.replace("{feedback}", feedback).replace("{visual_schema_content}", schema_content)

        # 保存反馈
        feedback_file = version_dir / "feedback.md"
        with open(feedback_file, 'w', encoding='utf-8') as f:
            f.write(f"# 迭代反馈 v{new_version}\n\n")
            f.write(f"**基于版本**: v{new_version - 1}\n")
            f.write(f"**编辑模式**: inpainting\n")
            f.write(f"**时间**: {datetime.now().isoformat()}\n\n")
            f.write("---\n\n")
            f.write(feedback)
        print(f"   ✓ 反馈已保存: {feedback_file}")
    else:
        # 首次生成或非迭代：使用标准模板
        use_advanced = reference_images is not None and len(reference_images) > 0
        renderer_template = load_renderer_template(use_advanced=use_advanced)
        full_prompt = renderer_template.replace("{visual_schema_content}", schema_content)

    # 保存完整 prompt
    renderer_prompt_file = version_dir / "renderer_prompt.md"
    with open(renderer_prompt_file, 'w', encoding='utf-8') as f:
        f.write(f"# Renderer Prompt v{new_version}\n\n")
        f.write(f"**模型**: {model}\n")
        f.write(f"**参考图**: {reference_images}\n")
        f.write(f"**迭代模式**: {is_iteration}\n")
        f.write(f"**编辑基础图**: {prev_diagram if is_iteration else 'N/A'}\n")
        f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
        f.write("---\n\n")
        f.write(full_prompt)
    print(f"   ✓ Prompt 已保存: {renderer_prompt_file}")

    try:
        from openai import OpenAI
        import base64

        print(f"   连接 Gemini API...")

        client = OpenAI(
            base_url="http://127.0.0.1:8888/v1",
            api_key=os.getenv("OPENAI_API_KEY", "sk-placeholder")
        )

        # 构建消息内容
        content_parts = []

        # 迭代模式：先放基础图（inpainting 的关键）
        if is_iteration and prev_diagram:
            try:
                with open(prev_diagram, 'rb') as img_file:
                    img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                    img_type = "image/png" if str(prev_diagram).lower().endswith('.png') else "image/jpeg"
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{img_type};base64,{img_base64}"}
                    })
                    print(f"   📎 已加载基础图（用于编辑）: {prev_diagram}")
            except Exception as e:
                print(f"⚠️  加载基础图失败: {e}", file=sys.stderr)

        # 添加文本 prompt
        content_parts.append({"type": "text", "text": full_prompt})

        # 添加参考图（风格参考）
        if reference_images:
            for img_path in reference_images:
                try:
                    with open(img_path, 'rb') as img_file:
                        img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                        img_type = "image/png" if img_path.lower().endswith('.png') else "image/jpeg"
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{img_type};base64,{img_base64}"}
                        })
                        print(f"   📎 已加载参考图（风格引导）: {img_path}")
                except Exception as e:
                    print(f"⚠️  加载参考图失败 {img_path}: {e}", file=sys.stderr)

        print(f"   发送渲染请求（{'编辑模式' if is_iteration else '生成模式'}）...")

        response = client.chat.completions.create(
            model=model,
            extra_body={"size": "1216x896"},
            messages=[{"role": "user", "content": content_parts}]
        )

        result = response.choices[0].message.content

        # 保存完整响应
        response_file = version_dir / "response.txt"
        with open(response_file, 'w', encoding='utf-8') as f:
            f.write(f"# Renderer Response v{new_version}\n\n")
            f.write(f"**生成时间**: {datetime.now().isoformat()}\n")
            f.write(f"**模型**: {model}\n\n")
            f.write("---\n\n")
            f.write(result)
        print(f"   ✓ 响应已保存: {response_file}")

        # 提取并保存图像
        if "data:image" in result and "base64," in result:
            import re

            match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=]+)', result)
            if match:
                try:
                    image_data = base64.b64decode(match.group(1))
                    diagram_file = version_dir / "diagram.jpg"
                    with open(diagram_file, "wb") as f:
                        f.write(image_data)

                    # 更新最新版本号
                    state.set_latest_version(new_version)

                    print(f"   ✓ 图像已保存: {diagram_file}")
                    print(f"      版本: v{new_version}")
                    print(f"      文件大小: {len(image_data) / 1024:.2f} KB")
                    return True
                except Exception as e:
                    print(f"⚠️  Base64 解码失败: {e}", file=sys.stderr)

        print("⚠️  响应中未找到图像数据")
        return False

    except ImportError:
        print("❌ 缺少 openai 模块，请运行: pip3 install openai", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ Renderer 调用异常: {e}", file=sys.stderr)
        return False


# =============================================================================
# 专家审阅相关函数
# =============================================================================

def load_review_template(expert_type: str) -> str:
    """加载专家审阅 prompt 模板

    Args:
        expert_type: "codex" | "gemini" | "claude"
    """
    template_map = {
        "codex": "review_codex_technical.txt",
        "gemini": "review_gemini_design.txt",
        "claude": "review_claude_academic.txt",
    }
    template_file = Path(__file__).parent.parent / "templates" / template_map.get(expert_type, "")

    if template_file.exists():
        with open(template_file, 'r', encoding='utf-8') as f:
            return f.read()

    print(f"⚠️  未找到审阅模板: {template_file}", file=sys.stderr)
    return ""


def run_expert_review(state: TaskState, version: int) -> bool:
    """阶段3.5: 运行专家审阅（调用 parallel-agent）

    文件系统状态机：
    - 输入: versions/v{N}/diagram.jpg
    - 输出: versions/v{N}/review/{expert}_review.md, consensus.json
    """
    print(f"\n🔍 [阶段3.5] 专家审阅 v{version}...")

    version_dir = state.get_version_dir(version)
    review_dir = state.get_version_review_dir(version)
    review_dir.mkdir(parents=True, exist_ok=True)

    diagram_path = version_dir / "diagram.jpg"
    if not diagram_path.exists():
        print(f"❌ 未找到架构图: {diagram_path}", file=sys.stderr)
        return False

    # 加载 Visual Schema 作为参考
    visual_schema = ""
    if state.visual_schema.exists():
        with open(state.visual_schema, 'r', encoding='utf-8') as f:
            visual_schema = f.read()

    # 构建 parallel-agent 任务
    experts = [
        ("codex", "codex", "gpt-5.1-codex-max"),
        ("gemini", "gemini", "gemini-3-pro-preview"),
        ("claude", "claude", "claude-opus-4-5-20251101"),
    ]

    tasks_yaml_parts = []
    for expert_id, backend, model in experts:
        # 检查是否已完成
        if state.review_stage_complete(version, expert_id):
            print(f"   ⏭️  跳过 {expert_id}（已存在）")
            continue

        # 加载模板并填充
        template = load_review_template(expert_id)
        prompt = template.replace("{visual_schema}", visual_schema)

        # 保存 prompt
        prompt_file = review_dir / f"{expert_id}_prompt.md"
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(prompt)

        tasks_yaml_parts.append(f"""---TASK---
id: {expert_id}_review
backend: {backend}
model: {model}
images: {diagram_path}
workdir: {state.task_dir}
---CONTENT---
{prompt}
""")

    if not tasks_yaml_parts:
        print("   ✅ 所有专家审阅已完成")
    else:
        # 保存任务定义
        tasks_yaml = "\n".join(tasks_yaml_parts)
        tasks_file = review_dir / "parallel_tasks.yaml"
        with open(tasks_file, 'w', encoding='utf-8') as f:
            f.write(f"# Auto-generated for version v{version} review\n")
            f.write(f"# Task ID: {state.task_id}\n")
            f.write(f"# Generated: {datetime.now().isoformat()}\n\n")
            f.write(tasks_yaml)
        print(f"   ✓ 任务定义: {tasks_file}")

        # 调用 parallel-agent
        parallel_agent_script = Path(__file__).parent.parent.parent / "parallel-agent" / "scripts" / "skill.py"

        if not parallel_agent_script.exists():
            print(f"❌ 未找到 parallel-agent: {parallel_agent_script}", file=sys.stderr)
            return False

        try:
            print(f"   🚀 调用 parallel-agent 执行 {len(tasks_yaml_parts)} 个审阅任务...")

            result = subprocess.run(
                ["python3", str(parallel_agent_script)],
                input=tasks_yaml,
                capture_output=True,
                text=True,
                timeout=600  # 10 分钟超时
            )

            # 解析结果并保存到各专家文件
            output = result.stdout

            # 简单解析：按任务 ID 分割结果
            for expert_id, _, _ in experts:
                if state.review_stage_complete(version, expert_id):
                    continue

                # 提取该专家的输出（简化处理）
                marker = f"--- Task: {expert_id}_review ---"
                if marker in output:
                    start = output.find(marker)
                    end = output.find("--- Task:", start + len(marker))
                    if end == -1:
                        end = len(output)
                    expert_output = output[start:end].strip()
                else:
                    expert_output = f"[审阅结果]\n\n{output[:2000]}"

                review_file = review_dir / f"{expert_id}_review.md"
                with open(review_file, 'w', encoding='utf-8') as f:
                    f.write(f"# {expert_id.capitalize()} Expert Review\n\n")
                    f.write(f"**Version**: v{version}\n")
                    f.write(f"**Timestamp**: {datetime.now().isoformat()}\n\n")
                    f.write("---\n\n")
                    f.write(expert_output)

                print(f"   ✓ {expert_id}_review.md")

            if result.returncode != 0:
                print(f"⚠️  parallel-agent 返回非零状态: {result.returncode}")
                print(f"   stderr: {result.stderr[:500] if result.stderr else 'N/A'}")

        except subprocess.TimeoutExpired:
            print("❌ parallel-agent 执行超时", file=sys.stderr)
            return False
        except Exception as e:
            print(f"❌ parallel-agent 执行失败: {e}", file=sys.stderr)
            return False

    # 生成共识（如果所有审阅完成）
    if not state.review_stage_complete(version, "consensus"):
        return generate_consensus(state, version)

    return True


def generate_consensus(state: TaskState, version: int) -> bool:
    """生成共识结果

    文件系统状态机：
    - 输入: versions/v{N}/review/{expert}_review.md
    - 输出: versions/v{N}/review/consensus.json
    """
    print(f"   📊 生成共识...")

    review_dir = state.get_version_review_dir(version)

    # 读取所有审阅结果
    reviews = {}
    scores = {}
    all_issues = []

    for expert_id in ["codex", "gemini", "claude"]:
        review_file = review_dir / f"{expert_id}_review.md"
        if review_file.exists():
            with open(review_file, 'r', encoding='utf-8') as f:
                content = f.read()
                reviews[expert_id] = content

                # 尝试提取评分（简单正则匹配）
                import re
                score_match = re.search(r'"score"\s*:\s*(\d+)', content)
                if score_match:
                    scores[expert_id] = int(score_match.group(1))
                else:
                    # 备选：查找 X/10 格式
                    score_match = re.search(r'(\d+)/10', content)
                    if score_match:
                        scores[expert_id] = int(score_match.group(1))
                    else:
                        scores[expert_id] = 7  # 默认分数

                # 提取问题（简单匹配 "issue" 字段）
                issue_matches = re.findall(r'"issue"\s*:\s*"([^"]+)"', content)
                for issue in issue_matches:
                    all_issues.append({
                        "issue": issue,
                        "mentioned_by": [expert_id],
                    })

    # 合并重复问题
    merged_issues = []
    for issue in all_issues:
        found = False
        for merged in merged_issues:
            # 简单相似度检查（包含关系）
            if issue["issue"].lower() in merged["issue"].lower() or merged["issue"].lower() in issue["issue"].lower():
                merged["mentioned_by"].extend(issue["mentioned_by"])
                found = True
                break
        if not found:
            merged_issues.append(issue)

    # 标记优先级
    for issue in merged_issues:
        issue["mentioned_by"] = list(set(issue["mentioned_by"]))
        issue["priority"] = "high" if len(issue["mentioned_by"]) >= 2 else "medium"

    # 计算平均分
    avg_score = sum(scores.values()) / len(scores) if scores else 0

    consensus = {
        "version": version,
        "timestamp": datetime.now().isoformat(),
        "scores": scores,
        "avg_score": round(avg_score, 2),
        "issues": sorted(merged_issues, key=lambda x: -len(x["mentioned_by"])),
        "suggestions": [i["issue"] for i in merged_issues[:5]],  # Top 5 建议
    }

    # 保存共识
    consensus_file = review_dir / "consensus.json"
    with open(consensus_file, 'w', encoding='utf-8') as f:
        json.dump(consensus, f, ensure_ascii=False, indent=2)

    print(f"   ✓ consensus.json (avg: {avg_score:.1f}/10)")

    # 显示各专家评分
    for expert_id, score in scores.items():
        print(f"      [{expert_id}]: {score}/10")

    return True


def load_feedback_synthesis_template() -> str:
    """加载反馈合成模板"""
    template_path = Path(__file__).parent.parent / "templates" / "feedback_synthesis.txt"

    if template_path.exists():
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()

    # 默认模板
    return """Convert these expert issues into actionable image editing instructions:
{issues_text}

Output specific visual changes (e.g., "Move label X to position Y", "Change font size to Z")."""


def synthesize_feedback_with_llm(state: TaskState, version: int, consensus: Dict[str, Any]) -> str:
    """使用 LLM 将专家反馈合成为结构化修改指令

    文件系统状态机：
    - 输入: versions/v{N}/review/{expert}_review.md, consensus.json
    - 输出: versions/v{N}/review/synthesized_feedback.md
    """
    review_dir = state.get_version_review_dir(version)

    # 加载模板
    template = load_feedback_synthesis_template()

    # 读取专家审阅原文
    expert_issues = {}
    for expert_id in ["codex", "gemini", "claude"]:
        review_file = review_dir / f"{expert_id}_review.md"
        if review_file.exists():
            with open(review_file, 'r', encoding='utf-8') as f:
                content = f.read()
                # 提取问题部分（简化处理）
                expert_issues[expert_id] = content[-2000:] if len(content) > 2000 else content

    # 构建 prompt
    scores = consensus.get("scores", {})
    issues = consensus.get("issues", [])

    # 格式化专家问题
    def format_expert_issues(expert_id: str) -> str:
        if expert_id not in expert_issues:
            return "No issues reported."
        # 提取 JSON 中的 issues 部分
        content = expert_issues[expert_id]
        import re
        issue_matches = re.findall(r'"issue"\s*:\s*"([^"]+)"', content)
        if issue_matches:
            return "\n".join(f"- {issue}" for issue in issue_matches[:5])
        return "No structured issues found."

    synthesis_prompt = template.format(
        codex_score=scores.get("codex", "N/A"),
        codex_issues=format_expert_issues("codex"),
        gemini_score=scores.get("gemini", "N/A"),
        gemini_issues=format_expert_issues("gemini"),
        claude_score=scores.get("claude", "N/A"),
        claude_issues=format_expert_issues("claude"),
        avg_score=consensus.get("avg_score", 0),
        high_priority_count=len([i for i in issues if i.get("priority") == "high"]),
        total_issues=len(issues),
    )

    # 保存合成 prompt
    synthesis_prompt_file = review_dir / "synthesis_prompt.md"
    with open(synthesis_prompt_file, 'w', encoding='utf-8') as f:
        f.write(synthesis_prompt)

    try:
        print(f"   🤖 调用 LLM 合成反馈...")

        result = subprocess.run(
            ["codex", "exec", "-"],
            input=synthesis_prompt,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode == 0 and result.stdout.strip():
            synthesized = result.stdout.strip()

            # 保存合成结果
            feedback_file = review_dir / "synthesized_feedback.md"
            with open(feedback_file, 'w', encoding='utf-8') as f:
                f.write(f"# Synthesized Feedback v{version}\n\n")
                f.write(f"**Generated**: {datetime.now().isoformat()}\n\n")
                f.write("---\n\n")
                f.write(synthesized)

            print(f"   ✓ synthesized_feedback.md ({len(synthesized)} chars)")
            return synthesized
        else:
            print(f"   ⚠️  LLM 合成失败，使用简单反馈")

    except subprocess.TimeoutExpired:
        print(f"   ⚠️  LLM 合成超时，使用简单反馈")
    except Exception as e:
        print(f"   ⚠️  LLM 合成异常: {e}")

    # 回退到简单反馈
    return None


def make_iteration_decision(state: TaskState, version: int, threshold: int = 8) -> bool:
    """阶段3.6: 生成迭代决策

    文件系统状态机：
    - 输入: versions/v{N}/review/consensus.json
    - 输出: versions/v{N}/review/iteration_decision.json, synthesized_feedback.md
    """
    print(f"   🎯 生成迭代决策...")

    review_dir = state.get_version_review_dir(version)

    # 加载共识
    consensus = state.load_consensus(version)
    if not consensus:
        print("❌ 未找到共识结果", file=sys.stderr)
        return False

    avg_score = consensus.get("avg_score", 0)
    issues = consensus.get("issues", [])
    high_priority_issues = [i for i in issues if i.get("priority") == "high"]

    # 决策逻辑
    should_iterate = avg_score < threshold and len(issues) > 0

    generated_feedback = ""
    if should_iterate:
        reason = f"avg_score ({avg_score:.1f}) < threshold ({threshold})"
        if high_priority_issues:
            reason += f", 有 {len(high_priority_issues)} 个高优先级问题"

        # 尝试使用 LLM 合成结构化反馈
        synthesized = synthesize_feedback_with_llm(state, version, consensus)

        if synthesized:
            generated_feedback = synthesized
        else:
            # 回退：简单列表格式
            feedback_parts = [
                "## Refinement Instructions for Imagen 3\n",
                "### PRIORITY FIXES"
            ]
            for i, issue in enumerate(issues[:5], 1):
                priority_mark = "**[HIGH]**" if issue.get("priority") == "high" else "[medium]"
                mentioned = ", ".join(issue.get("mentioned_by", []))
                feedback_parts.append(f"{i}. {priority_mark} {issue['issue']} (mentioned by: {mentioned})")

            feedback_parts.append("\n### STYLE GUIDANCE")
            feedback_parts.append("- Maintain CVPR academic standard")
            feedback_parts.append("- Use mathematical notation for labels")
            feedback_parts.append("- Keep existing layout structure")

            generated_feedback = "\n".join(feedback_parts)

            # 保存简单反馈
            feedback_file = review_dir / "synthesized_feedback.md"
            with open(feedback_file, 'w', encoding='utf-8') as f:
                f.write(f"# Simple Feedback v{version}\n\n")
                f.write(f"**Generated**: {datetime.now().isoformat()}\n")
                f.write(f"**Mode**: Fallback (LLM unavailable)\n\n")
                f.write("---\n\n")
                f.write(generated_feedback)
    else:
        reason = f"avg_score ({avg_score:.1f}) >= threshold ({threshold})" if avg_score >= threshold else "无改进建议"

    decision = {
        "version": version,
        "timestamp": datetime.now().isoformat(),
        "avg_score": avg_score,
        "threshold": threshold,
        "should_iterate": should_iterate,
        "reason": reason,
        "generated_feedback": generated_feedback,
        "feedback_method": "llm_synthesized" if should_iterate and synthesize_feedback_with_llm else "simple_list",
    }

    # 保存决策
    decision_file = review_dir / "iteration_decision.json"
    with open(decision_file, 'w', encoding='utf-8') as f:
        json.dump(decision, f, ensure_ascii=False, indent=2)

    if should_iterate:
        print(f"   → 需要迭代 ({reason})")
    else:
        print(f"   ✅ 审阅通过 ({reason})")

    return True


def extract_and_propose_lessons(state: TaskState, version: int, learn_mode: bool = False) -> List[Dict[str, Any]]:
    """从审阅中提取新 Lessons（同步模式 - 返回待审批列表供外部确认）

    Args:
        state: 任务状态
        version: 版本号
        learn_mode: 是否启用学习模式

    Returns:
        提取的待审批 Lesson 列表（供外部审批流程使用）
    """
    if not learn_mode:
        return []

    consensus = state.load_consensus(version)
    if not consensus:
        return []

    # 提取新的待审批 Lessons
    pending_lessons = extract_new_lessons_from_review(consensus, {})

    if not pending_lessons:
        print("   📝 未发现新的高优先级问题")
        return []

    print(f"\n   📝 发现 {len(pending_lessons)} 条新问题模式:")
    for i, lesson in enumerate(pending_lessons, 1):
        print(f"      [{i}] {lesson['title']}")
        print(f"          类别: {lesson.get('category', 'unknown')}")
        print(f"          提及: {', '.join(lesson.get('mentioned_by', []))}")

    # 输出 JSON 到状态目录供外部审批
    pending_file = state.versions_dir / f"v{version}" / "pending_lessons.json"
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    with open(pending_file, 'w', encoding='utf-8') as f:
        json.dump(pending_lessons, f, ensure_ascii=False, indent=2)
    print(f"\n   💾 待审批列表已保存: {pending_file}")
    print(f"   💡 确认后调用 approve_lesson_to_spec() 将 Lesson 写入 Spec")

    return pending_lessons


# =============================================================================
# Pipeline 主流程
# =============================================================================

def run_pipeline(state: TaskState, args: argparse.Namespace, force: bool = False, iterate: bool = False):
    """运行完整 pipeline，支持断点续传、迭代和自动审阅

    自动审阅模式 (--auto-review):
    - 渲染后自动调用三位专家审阅
    - 基于共识分数决定是否继续迭代
    - 达到阈值或最大迭代次数后停止
    """

    if force:
        print("\n⚠️  强制模式：清除所有已有文件")
        state.clear()

    # 迁移旧版文件
    state.migrate_legacy_diagram()

    state.print_status()

    # 获取自动审阅参数
    auto_review = getattr(args, 'auto_review', False)
    max_iterations = getattr(args, 'max_iterations', 5)
    review_threshold = getattr(args, 'review_threshold', 8)

    # 迭代模式：只重新渲染
    if iterate and args.feedback:
        print("\n🔄 迭代模式：基于上一版本 + 反馈生成新版本")

        if not state.stage_complete("schema"):
            print("❌ 迭代模式需要已有 Visual Schema", file=sys.stderr)
            return False

        if not run_renderer(state, feedback=args.feedback, extra_reference_images=args.reference_images):
            print("\n❌ 迭代渲染失败")
            state.print_status()
            return False

        # 如果启用自动审阅，对新版本进行审阅
        if auto_review:
            version = state.get_latest_version()
            if not run_expert_review(state, version):
                print("⚠️  专家审阅失败，但图像已生成")
            else:
                make_iteration_decision(state, version, threshold=review_threshold)
                # 提取新 Lessons（如果启用学习模式）
                learn_mode = getattr(args, 'learn', False)
                extract_and_propose_lessons(state, version, learn_mode=learn_mode)

        print("\n" + "=" * 50)
        print("🎉 迭代完成!")
        print("=" * 50)
        state.print_status()
        return True

    # 阶段0: 保存输入
    if not state.stage_complete("input"):
        save_input(state, args)
    else:
        print(f"\n⏭️  跳过阶段0（已存在: {state.input_json.name}）")

    # 阶段1: 代码分析
    if not state.stage_complete("analysis"):
        if not run_analysis(state):
            print("\n❌ 阶段1失败，流程终止")
            state.print_status()
            return False
    else:
        print(f"\n⏭️  跳过阶段1（已存在: {state.analysis_md.name}）")

    # 阶段2: 生成 Visual Schema
    if not state.stage_complete("schema"):
        if not run_architect(state):
            print("\n❌ 阶段2失败，流程终止")
            state.print_status()
            return False
    else:
        print(f"\n⏭️  跳过阶段2（已存在: {state.visual_schema.name}）")

    # 阶段3: 渲染图像
    if not state.stage_complete("diagram"):
        if not run_renderer(state, extra_reference_images=args.reference_images):
            print("\n❌ 阶段3失败，流程终止")
            state.print_status()
            return False
    else:
        print(f"\n⏭️  跳过阶段3（已有版本: v{state.get_latest_version()}）")
        print("   💡 使用 --iterate --feedback \"修改需求\" 进行迭代")

    # 阶段3.5-4: 自动审阅循环
    if auto_review:
        print("\n" + "=" * 50)
        print("🔄 自动审阅模式启动")
        print(f"   阈值: {review_threshold}/10 | 最大迭代: {max_iterations}")
        print("=" * 50)

        iteration_count = 0
        while iteration_count < max_iterations:
            version = state.get_latest_version()
            print(f"\n📍 审阅循环 #{iteration_count + 1} (v{version})")

            # 检查是否已有该版本的审阅
            if state.review_stage_complete(version, "decision"):
                # 加载已有决策
                decision = state.load_iteration_decision(version)
                if decision and not decision.get("should_iterate", False):
                    print(f"   ✅ v{version} 已通过审阅 (avg: {decision.get('avg_score', 'N/A')}/10)")
                    break
                elif decision and decision.get("should_iterate"):
                    # 使用已有反馈继续迭代
                    feedback = decision.get("generated_feedback", "")
                    if feedback:
                        print(f"   📝 使用已有反馈继续迭代...")
                        if not run_renderer(state, feedback=feedback, extra_reference_images=args.reference_images):
                            print("❌ 迭代渲染失败")
                            break
                        iteration_count += 1
                        continue
            else:
                # 执行专家审阅
                if not run_expert_review(state, version):
                    print("❌ 专家审阅失败")
                    break

                # 生成迭代决策
                if not make_iteration_decision(state, version, threshold=review_threshold):
                    print("❌ 决策生成失败")
                    break

                # 提取新 Lessons（如果启用学习模式）
                learn_mode = getattr(args, 'learn', False)
                extract_and_propose_lessons(state, version, learn_mode=learn_mode)

            # 加载新决策
            decision = state.load_iteration_decision(version)
            if not decision:
                print("❌ 无法加载决策结果")
                break

            if not decision.get("should_iterate", False):
                print(f"\n✅ 审阅通过！最终版本: v{version}")
                break

            # 需要迭代
            feedback = decision.get("generated_feedback", "")
            if not feedback:
                print("⚠️  需要迭代但无反馈，停止循环")
                break

            print(f"\n🔄 执行自动迭代 (v{version} → v{version + 1})...")
            if not run_renderer(state, feedback=feedback, extra_reference_images=args.reference_images):
                print("❌ 迭代渲染失败")
                break

            iteration_count += 1

        if iteration_count >= max_iterations:
            print(f"\n⚠️  达到最大迭代次数 ({max_iterations})")

        print("\n" + "=" * 50)
        print(f"🔍 自动审阅完成 (迭代 {iteration_count} 次)")
        print("=" * 50)

    print("\n" + "=" * 50)
    print("🎉 架构图生成完成!")
    print("=" * 50)
    state.print_status()

    return True


def list_tasks(base_dir: str = DEFAULT_BASE_DIR):
    """列出所有任务及状态"""
    base_path = Path(base_dir)

    if not base_path.exists():
        print(f"📂 任务目录不存在: {base_dir}")
        return

    tasks = [d for d in base_path.iterdir() if d.is_dir()]

    if not tasks:
        print(f"📂 没有找到任务")
        return

    print(f"\n📂 任务列表 ({base_dir})")
    print("=" * 70)

    for task_dir in sorted(tasks):
        state = TaskState(task_dir.name, base_dir)
        status = state.get_status()
        latest_version = status["latest_version"]

        # 状态图标
        if status["has_diagram"]:
            icon = "✅"
        elif status["schema"]:
            icon = "🔶"
        elif status["analysis"]:
            icon = "🔷"
        else:
            icon = "⬜"

        version_str = f"v{latest_version}" if latest_version > 0 else "v0"
        print(f"{icon} {task_dir.name:<40} [{version_str}]")

    print("=" * 70)
    print("图例: ✅ 有架构图 | 🔶 Schema已生成 | 🔷 分析已完成 | ⬜ 刚开始")


def generate_task_id(arch_code_path: Optional[str] = None) -> str:
    """生成任务 ID"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if arch_code_path:
        basename = Path(arch_code_path).stem
        return f"{basename}_{timestamp}"
    else:
        return f"task_{timestamp}"


# =============================================================================
# 主入口
# =============================================================================

def main():
    model_config = load_model_config()

    parser = argparse.ArgumentParser(
        description="学术架构图生成器 - 文件系统即状态机设计 v2.3（支持渐进式迭代 + 自动专家审阅）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 新建任务
  python3 skill.py --arch_code_path basicofr/archs/freqmamba_arch.py

  # 恢复任务（断点续传）
  python3 skill.py --resume freqmamba_arch_20251231_160000

  # 渐进式迭代（基于上一版本 + 反馈）
  python3 skill.py --resume task_id --iterate --feedback "修改文字标注为数学符号风格"

  # 启用自动专家审阅（三专家并行审阅，自动迭代直到通过）
  python3 skill.py --arch_code_path arch.py --auto-review

  # 自定义审阅参数
  python3 skill.py --resume task_id --auto-review --review-threshold 7 --max-iterations 3

  # 列出所有任务
  python3 skill.py --list

  # 强制重新执行
  python3 skill.py --resume freqmamba_arch_20251231_160000 --force
        """
    )

    # 输入参数
    parser.add_argument(
        "--arch_code_path",
        type=str,
        help="架构代码路径（代码分析模式）"
    )
    parser.add_argument(
        "--paper_content",
        type=str,
        help="论文内容/方法描述（论文模式）"
    )

    # 任务管理
    parser.add_argument(
        "--resume",
        type=str,
        metavar="TASK_ID",
        help="恢复已有任务（断点续传）"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有任务及状态"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新执行所有阶段"
    )

    # 迭代参数
    parser.add_argument(
        "--iterate",
        action="store_true",
        help="迭代模式：基于上一版本生成新版本"
    )
    parser.add_argument(
        "--feedback",
        type=str,
        help="迭代反馈/修改需求（与 --iterate 配合使用）"
    )

    # 自动审阅参数（默认启用）
    parser.add_argument(
        "--auto-review",
        dest="auto_review",
        action="store_true",
        default=True,
        help="启用自动专家审阅模式（Codex/Gemini/Claude 三专家并行审阅）[默认启用]"
    )
    parser.add_argument(
        "--no-auto-review",
        dest="auto_review",
        action="store_false",
        help="禁用自动专家审阅模式"
    )
    parser.add_argument(
        "--max-iterations",
        dest="max_iterations",
        type=int,
        default=5,
        help="自动审阅最大迭代次数 (默认: 5)"
    )
    parser.add_argument(
        "--review-threshold",
        dest="review_threshold",
        type=int,
        default=8,
        help="审阅通过阈值，1-10分 (默认: 8)"
    )
    parser.add_argument(
        "--learn",
        action="store_true",
        help="启用学习模式：从审阅中提取新问题到 Spec 待审批区（需手动审批后生效）"
    )
    parser.add_argument(
        "--approve-lessons",
        type=str,
        help="审批指定的 Lessons（逗号分隔的索引，如 '1,2,3' 或 'all'）。需配合 --task-id 使用。"
    )
    parser.add_argument(
        "--task-id",
        type=str,
        help="指定任务 ID（用于审批 Lessons 时定位 pending_lessons.json）"
    )

    # 输出配置
    parser.add_argument(
        "--output_path",
        type=str,
        default=DEFAULT_BASE_DIR,
        help=f"输出基础目录 (默认: {DEFAULT_BASE_DIR})"
    )

    # 模型配置
    parser.add_argument(
        "--model_architect",
        type=str,
        default=model_config['architect'],
        help=f"Architect 模型 (默认: {model_config['architect']})"
    )
    parser.add_argument(
        "--model_renderer",
        type=str,
        default=model_config['renderer'],
        help=f"Renderer 模型 (默认: {model_config['renderer']})"
    )
    parser.add_argument(
        "--reference_images",
        type=str,
        nargs='+',
        help="参考图像路径（支持多张）"
    )

    args = parser.parse_args()

    # 列出任务模式
    if args.list:
        list_tasks(args.output_path)
        return

    # 审批 Lessons 模式
    if getattr(args, 'approve_lessons', None):
        task_id = getattr(args, 'task_id', None)
        if not task_id:
            print("❌ 错误: --approve-lessons 需要配合 --task-id 使用", file=sys.stderr)
            sys.exit(1)

        # 查找 pending_lessons.json
        state = TaskState(task_id, args.output_path)
        # 找到最新版本的 pending_lessons.json
        pending_file = None
        if state.versions_dir.exists():
            versions = sorted(state.versions_dir.glob("v*"))
            for v in reversed(versions):
                pf = v / "pending_lessons.json"
                if pf.exists():
                    pending_file = pf
                    break

        if not pending_file or not pending_file.exists():
            print(f"❌ 未找到待审批列表: {task_id}", file=sys.stderr)
            print("   请先运行 --auto-review --learn 模式生成待审批列表", file=sys.stderr)
            sys.exit(1)

        with open(pending_file, 'r', encoding='utf-8') as f:
            pending_lessons = json.load(f)

        if not pending_lessons:
            print("📝 没有待审批的 Lessons")
            return

        # 解析要审批的索引
        approve_arg = args.approve_lessons.strip().lower()
        if approve_arg == 'all':
            indices = list(range(len(pending_lessons)))
        else:
            try:
                indices = [int(i.strip()) - 1 for i in approve_arg.split(',')]
            except ValueError:
                print("❌ 无效的索引格式，请使用 '1,2,3' 或 'all'", file=sys.stderr)
                sys.exit(1)

        # 审批选中的 Lessons
        approved_count = 0
        print(f"\n📋 审批 Lessons (任务: {task_id}):\n")
        for idx in indices:
            if 0 <= idx < len(pending_lessons):
                lesson = pending_lessons[idx]
                if approve_lesson_to_spec(lesson, task_id):
                    approved_count += 1
            else:
                print(f"   ⚠️  索引 {idx + 1} 超出范围，跳过", file=sys.stderr)

        print(f"\n✅ 已审批 {approved_count}/{len(indices)} 条 Lessons")
        print(f"   下次运行 arch-diagram 时将自动应用这些经验")
        return

    # 确定任务 ID
    if args.resume:
        task_id = args.resume
        print(f"\n📂 恢复任务: {task_id}")
    elif args.arch_code_path or args.paper_content:
        task_id = generate_task_id(args.arch_code_path)
        print(f"\n📂 新建任务: {task_id}")
    else:
        print("❌ 错误: 必须提供 --arch_code_path、--paper_content 或 --resume 之一", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    # 创建状态管理器
    state = TaskState(task_id, args.output_path)

    # 恢复模式下，从已保存的输入加载参数
    if args.resume and state.input_json.exists():
        saved_input = state.load_input()
        if saved_input:
            # 使用保存的参数，但允许命令行覆盖
            if not args.arch_code_path:
                args.arch_code_path = saved_input.get('arch_code_path')
            if not args.paper_content:
                args.paper_content = saved_input.get('paper_content')
            if args.model_architect == model_config['architect']:
                args.model_architect = saved_input.get('model_architect', args.model_architect)
            if args.model_renderer == model_config['renderer']:
                args.model_renderer = saved_input.get('model_renderer', args.model_renderer)
            # 注意：reference_images 不从保存的输入加载，允许每次迭代使用不同参考图

    # 运行 pipeline
    success = run_pipeline(state, args, force=args.force, iterate=args.iterate)

    if not success:
        print("\n💡 提示: 修复问题后，使用以下命令继续:")
        print(f"   python3 skill.py --resume {task_id}")
        sys.exit(1)


if __name__ == "__main__":
    main()