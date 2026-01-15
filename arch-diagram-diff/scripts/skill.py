#!/usr/bin/env python3
"""
Baseline 差异驱动的学术架构图生成器 v1.0

设计理念：
- 基于已有 Baseline 架构图进行差异分析和增量修改
- 复用 Baseline 的布局、风格、主图结构
- 只修改差异部分（创新点 → inset ABC）
- 保持论文图风格一致性

流程：
1. 阶段0: 输入 + Baseline 加载
2. 阶段1: 架构差异分析 → diff_analysis.md（暂停等待确认）
3. 阶段2: 增量 Schema 生成 → visual_schema.md
4. 阶段3: 图像编辑渲染 → diagram.jpg (inpainting 模式)

目录结构:
experiments/visualizations/architecture-diff/{task_id}/
├── input.json              # 输入参数
├── baseline_info.json      # Baseline 信息
├── code_snapshot.py        # 新项目代码快照
├── diff_analysis.md        # 差异分析结果（用户确认点）
├── diff_confirmed.json     # 用户确认标记
├── visual_schema.md        # Visual Schema
├── versions/
│   └── v1/
│       ├── renderer_prompt.md
│       ├── diagram.jpg
│       └── response.txt
└── latest_version.txt
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List


# =============================================================================
# 配置
# =============================================================================

DEFAULT_BASE_DIR = "experiments/visualizations/architecture-diff"
BASELINES_DIR = Path(__file__).parent.parent.parent / "arch-diagram" / "baselines"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Baseline 代码路径映射（可扩展）
BASELINE_CODE_MAP = {
    "MambaOFR": "basicofr/archs/mambaofr_arch.py",
    "RTN": "basicofr/archs/rtn_arch.py",
}

# Baseline Visual Schema 路径（如果有已生成的）
BASELINE_SCHEMA_MAP = {
    "MambaOFR": None,  # 第一次使用时会生成
    "RTN": None,
}


# =============================================================================
# Baseline 管理
# =============================================================================

def list_available_baselines() -> List[str]:
    """列出可用的 Baseline"""
    if not BASELINES_DIR.exists():
        return []

    baselines = []
    for f in BASELINES_DIR.iterdir():
        if f.suffix.lower() in ['.jpg', '.jpeg', '.png']:
            baselines.append(f.stem)
    return sorted(baselines)


def get_baseline_image(baseline_name: str) -> Optional[Path]:
    """获取 Baseline 图像路径"""
    for ext in ['.jpeg', '.jpg', '.png']:
        path = BASELINES_DIR / f"{baseline_name}{ext}"
        if path.exists():
            return path
    return None


def get_baseline_code_path(baseline_name: str) -> Optional[str]:
    """获取 Baseline 代码路径"""
    return BASELINE_CODE_MAP.get(baseline_name)


# =============================================================================
# TaskState: 文件系统状态机
# =============================================================================

class TaskState:
    """任务状态管理器"""

    def __init__(self, task_id: str, base_dir: str = DEFAULT_BASE_DIR):
        self.task_id = task_id
        self.task_dir = Path(base_dir) / task_id
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir = self.task_dir / "versions"

    # --- 文件路径属性 ---
    @property
    def input_json(self) -> Path:
        return self.task_dir / "input.json"

    @property
    def baseline_info(self) -> Path:
        return self.task_dir / "baseline_info.json"

    @property
    def code_snapshot(self) -> Path:
        return self.task_dir / "code_snapshot.py"

    @property
    def baseline_code_snapshot(self) -> Path:
        return self.task_dir / "baseline_code_snapshot.py"

    @property
    def diff_analysis(self) -> Path:
        return self.task_dir / "diff_analysis.md"

    @property
    def diff_confirmed(self) -> Path:
        return self.task_dir / "diff_confirmed.json"

    @property
    def visual_schema(self) -> Path:
        return self.task_dir / "visual_schema.md"

    @property
    def latest_version_file(self) -> Path:
        return self.task_dir / "latest_version.txt"

    # --- 版本管理 ---
    def get_latest_version(self) -> int:
        if self.latest_version_file.exists():
            try:
                return int(self.latest_version_file.read_text().strip())
            except ValueError:
                pass
        if self.versions_dir.exists():
            versions = [d.name for d in self.versions_dir.iterdir() if d.is_dir() and d.name.startswith('v')]
            if versions:
                nums = [int(v[1:]) for v in versions if v[1:].isdigit()]
                if nums:
                    return max(nums)
        return 0

    def set_latest_version(self, version: int):
        self.latest_version_file.write_text(str(version))

    def get_version_dir(self, version: int) -> Path:
        return self.versions_dir / f"v{version}"

    def create_new_version(self) -> tuple[int, Path]:
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        new_version = self.get_latest_version() + 1
        version_dir = self.get_version_dir(new_version)
        version_dir.mkdir(parents=True, exist_ok=True)
        return new_version, version_dir

    def get_version_diagram(self, version: int) -> Optional[Path]:
        diagram = self.get_version_dir(version) / "diagram.jpg"
        return diagram if diagram.exists() else None

    def get_latest_diagram(self) -> Optional[Path]:
        version = self.get_latest_version()
        if version > 0:
            return self.get_version_diagram(version)
        return None

    # --- 状态检查 ---
    def stage_complete(self, stage: str) -> bool:
        stage_files = {
            "input": self.input_json,
            "baseline": self.baseline_info,
            "snapshot": self.code_snapshot,
            "diff": self.diff_analysis,
            "confirmed": self.diff_confirmed,
            "schema": self.visual_schema,
            "diagram": self.get_latest_diagram(),
        }
        target = stage_files.get(stage)
        return target is not None and target.exists()

    def get_status(self) -> Dict[str, Any]:
        return {
            "input": self.input_json.exists(),
            "baseline": self.baseline_info.exists(),
            "snapshot": self.code_snapshot.exists(),
            "diff": self.diff_analysis.exists(),
            "confirmed": self.diff_confirmed.exists(),
            "schema": self.visual_schema.exists(),
            "latest_version": self.get_latest_version(),
            "has_diagram": self.get_latest_diagram() is not None,
        }

    def print_status(self):
        status = self.get_status()
        print(f"\n🎯 Task: {self.task_id}")
        print("━" * 60)

        stages = [
            ("input", "input.json", "输入参数"),
            ("baseline", "baseline_info.json", "Baseline 信息"),
            ("snapshot", "code_snapshot.py", "代码快照"),
            ("diff", "diff_analysis.md", "差异分析"),
            ("confirmed", "diff_confirmed.json", "⚠️ 用户确认"),
            ("schema", "visual_schema.md", "Visual Schema"),
        ]

        for key, filename, desc in stages:
            icon = "✓" if status[key] else " "
            highlight = " ←← 等待确认" if key == "confirmed" and not status[key] and status["diff"] else ""
            print(f"[{icon}] {filename:<25} ({desc}){highlight}")

        print("━" * 60)
        version = status["latest_version"]
        if version > 0:
            diagram = self.get_latest_diagram()
            if diagram and diagram.exists():
                size_kb = diagram.stat().st_size / 1024
                print(f"📂 最新版本: v{version} ({size_kb:.1f} KB)")
            else:
                print(f"📂 最新版本: v{version} (无图像)")
        else:
            print("📂 版本历史: (无)")
        print("━" * 60)

    def load_input(self) -> Optional[Dict[str, Any]]:
        if self.input_json.exists():
            with open(self.input_json, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def load_baseline_info(self) -> Optional[Dict[str, Any]]:
        if self.baseline_info.exists():
            with open(self.baseline_info, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    def clear(self):
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir)
        self.task_dir.mkdir(parents=True, exist_ok=True)


# =============================================================================
# 模板加载
# =============================================================================

def load_template(name: str) -> str:
    """加载模板文件"""
    template_path = TEMPLATES_DIR / f"{name}.txt"
    if template_path.exists():
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    print(f"⚠️  未找到模板: {template_path}", file=sys.stderr)
    return ""


# =============================================================================
# Pipeline 阶段函数
# =============================================================================

def save_input(state: TaskState, args: argparse.Namespace):
    """阶段0: 保存输入参数和 Baseline 信息"""
    print("\n📥 [阶段0] 保存输入参数...")

    # 保存输入参数
    input_data = {
        "task_id": state.task_id,
        "created_at": datetime.now().isoformat(),
        "arch_code_path": args.arch_code_path,
        "baseline": args.baseline,
        "baseline_code_path": args.baseline_code_path,
    }
    with open(state.input_json, 'w', encoding='utf-8') as f:
        json.dump(input_data, f, ensure_ascii=False, indent=2)
    print(f"   ✓ 已保存: {state.input_json}")

    # 保存 Baseline 信息
    baseline_image = get_baseline_image(args.baseline)
    baseline_code = args.baseline_code_path or get_baseline_code_path(args.baseline)

    baseline_data = {
        "name": args.baseline,
        "image_path": str(baseline_image) if baseline_image else None,
        "code_path": baseline_code,
    }
    with open(state.baseline_info, 'w', encoding='utf-8') as f:
        json.dump(baseline_data, f, ensure_ascii=False, indent=2)
    print(f"   ✓ Baseline: {args.baseline}")
    if baseline_image:
        print(f"      图像: {baseline_image}")
    if baseline_code:
        print(f"      代码: {baseline_code}")

    # 保存代码快照
    if args.arch_code_path and Path(args.arch_code_path).exists():
        shutil.copy(args.arch_code_path, state.code_snapshot)
        print(f"   ✓ 新项目代码快照: {state.code_snapshot}")

    if baseline_code and Path(baseline_code).exists():
        shutil.copy(baseline_code, state.baseline_code_snapshot)
        print(f"   ✓ Baseline 代码快照: {state.baseline_code_snapshot}")


def run_diff_analysis(state: TaskState, timeout_sec: int = 600) -> bool:
    """阶段1: 架构差异分析"""
    print("\n🔍 [阶段1] 架构差异分析...")

    input_data = state.load_input()
    baseline_data = state.load_baseline_info()

    if not input_data or not baseline_data:
        print("❌ 未找到输入或 Baseline 信息", file=sys.stderr)
        return False

    # 读取代码内容
    new_code = ""
    if state.code_snapshot.exists():
        with open(state.code_snapshot, 'r', encoding='utf-8') as f:
            new_code = f.read()

    baseline_code = ""
    if state.baseline_code_snapshot.exists():
        with open(state.baseline_code_snapshot, 'r', encoding='utf-8') as f:
            baseline_code = f.read()

    # 尝试读取 Spec
    spec_content = ""
    spec_candidates = [
        f"specs/*{Path(input_data.get('arch_code_path', '')).stem}*/README.md",
        "specs/*/README.md",
    ]
    # 简化：不搜索 spec，让 Codex 自己找

    # 加载并填充模板
    template = load_template("diff_analysis_prompt")
    prompt = template.format(
        baseline_name=baseline_data.get('name', 'Unknown'),
        baseline_code_path=baseline_data.get('code_path', 'N/A'),
        baseline_image_path=baseline_data.get('image_path', 'N/A'),
        new_code_path=input_data.get('arch_code_path', 'N/A'),
        spec_path="(auto-detect)",
        baseline_code=baseline_code[:15000] if baseline_code else "(无 Baseline 代码)",
        new_code=new_code[:15000] if new_code else "(无新项目代码)",
        spec_content=spec_content[:5000] if spec_content else "(无 Spec)",
    )

    try:
        print(f"   调用 Codex 进行差异分析...")

        result = subprocess.run(
            ["codex", "exec", "-"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_sec
        )

        if result.returncode != 0:
            print(f"❌ 差异分析失败: {result.stderr}", file=sys.stderr)
            return False

        analysis_result = result.stdout.strip()

        with open(state.diff_analysis, 'w', encoding='utf-8') as f:
            f.write(f"# 架构差异分析报告\n\n")
            f.write(f"**Baseline**: {baseline_data.get('name')}\n")
            f.write(f"**新项目**: {input_data.get('arch_code_path')}\n")
            f.write(f"**分析时间**: {datetime.now().isoformat()}\n\n")
            f.write("---\n\n")
            f.write(analysis_result)

        print(f"   ✓ 已保存: {state.diff_analysis} ({len(analysis_result)} chars)")
        return True

    except subprocess.TimeoutExpired:
        print("❌ 差异分析超时", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("❌ 未找到 codex CLI", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ 差异分析异常: {e}", file=sys.stderr)
        return False


def confirm_diff(state: TaskState, note: str = "") -> bool:
    """确认差异分析"""
    if not state.diff_analysis.exists():
        print("❌ 未找到差异分析结果", file=sys.stderr)
        return False

    confirm_data = {
        "confirmed_at": datetime.now().isoformat(),
        "note": note,
    }
    with open(state.diff_confirmed, 'w', encoding='utf-8') as f:
        json.dump(confirm_data, f, ensure_ascii=False, indent=2)

    print(f"   ✓ 已确认差异分析: {state.diff_confirmed}")
    return True


def run_delta_schema(state: TaskState, timeout_sec: int = 600) -> bool:
    """阶段2: 增量 Schema 生成"""
    print("\n🏗️  [阶段2] 增量 Schema 生成...")

    if not state.diff_analysis.exists():
        print("❌ 未找到差异分析结果", file=sys.stderr)
        return False

    # 读取差异分析
    with open(state.diff_analysis, 'r', encoding='utf-8') as f:
        diff_analysis = f.read()

    # 暂时没有 Baseline Schema，使用默认模板
    # TODO: 如果有已保存的 Baseline Schema，可以加载
    baseline_schema = """
[GLOBAL STYLE]
CVPR academic architecture figure. White background. Thin black strokes (≈1–2px). Light pastel fills. Clean vector look. Use LaTeX-style math symbols for variables.
NO legend. NO footnotes. NO bottom annotation strips.

[MAIN DIAGRAM: TOP ROW]
- Inputs: $X_{i-1}$ (frame thumbnail), $X_i$ (frame thumbnail)
- Block order (fixed): Flow Est. → Encoder → Alignment → Masked/Weighted Sum → Embedding → Backbone → Reconstruction → $\\hat{X}_i$

[MAIN DIAGRAM: BOTTOM ROW]
- Inputs: $X_i$ (frame thumbnail), $X_{i+1}$ (frame thumbnail)
- Same fixed block order as top row, output $\\hat{X}_{i+1}$

[INSETS]
(Baseline insets - to be replaced)

[CONNECTIONS]
Follow reference-style wiring: flow guidance into Alignment; encoder features into Alignment; fused features into Masked/Weighted Sum; then Embedding → Backbone → Reconstruction.
"""

    # 加载并填充模板
    template = load_template("delta_schema_prompt")
    prompt = template.format(
        baseline_schema=baseline_schema,
        diff_analysis=diff_analysis,
    )

    try:
        print(f"   调用 Codex 生成增量 Schema...")

        result = subprocess.run(
            ["codex", "exec", "-"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_sec
        )

        if result.returncode != 0:
            print(f"❌ Schema 生成失败: {result.stderr}", file=sys.stderr)
            return False

        schema_result = result.stdout.strip()

        with open(state.visual_schema, 'w', encoding='utf-8') as f:
            f.write(f"# Visual Schema (Diff-based)\n\n")
            f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
            f.write("---\n\n")
            f.write(schema_result)

        print(f"   ✓ 已保存: {state.visual_schema} ({len(schema_result)} chars)")
        return True

    except subprocess.TimeoutExpired:
        print("❌ Schema 生成超时", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("❌ 未找到 codex CLI", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ Schema 生成异常: {e}", file=sys.stderr)
        return False


def run_edit_renderer(state: TaskState, feedback: Optional[str] = None) -> bool:
    """阶段3: 图像编辑渲染（使用 Baseline 图作为编辑基础）"""

    new_version, version_dir = state.create_new_version()
    is_iteration = new_version > 1 and feedback is not None

    print(f"\n🎨 [阶段3] 图像编辑渲染 v{new_version}...")

    # 读取 Visual Schema
    if not state.visual_schema.exists():
        print("❌ 未找到 Visual Schema", file=sys.stderr)
        return False

    with open(state.visual_schema, 'r', encoding='utf-8') as f:
        visual_schema = f.read()

    # 读取差异分析
    diff_analysis = ""
    if state.diff_analysis.exists():
        with open(state.diff_analysis, 'r', encoding='utf-8') as f:
            diff_analysis = f.read()

    # 获取基础图像
    baseline_data = state.load_baseline_info()
    base_image_path = None

    if is_iteration:
        # 迭代模式：使用上一版本
        prev_diagram = state.get_version_diagram(new_version - 1)
        if prev_diagram and prev_diagram.exists():
            base_image_path = prev_diagram
            print(f"   📎 迭代基础图: v{new_version - 1}")
    else:
        # 首次生成：使用 Baseline 图
        if baseline_data and baseline_data.get('image_path'):
            base_image_path = Path(baseline_data['image_path'])
            if base_image_path.exists():
                print(f"   📎 Baseline 基础图: {base_image_path.name}")
            else:
                base_image_path = None

    # 构建 prompt
    template = load_template("edit_render_prompt")

    # 从差异分析提取修改指令
    edit_instructions = "Based on the diff analysis, modify the following areas:"
    main_diagram_edits = "Update Innovation A/B highlights as specified in the Visual Schema."
    inset_a_content = "(Extract from Visual Schema)"
    inset_b_content = "(Extract from Visual Schema)"

    # 简单提取（实际应该更智能地解析）
    if "inset (a)" in visual_schema.lower() or "(a)" in visual_schema:
        # 尝试提取 inset 内容
        pass

    prompt = template.format(
        edit_instructions=edit_instructions,
        main_diagram_edits=main_diagram_edits,
        inset_a_content=inset_a_content,
        inset_b_content=inset_b_content,
        visual_schema_content=visual_schema,
    )

    if feedback:
        prompt = f"**Additional Feedback:**\n{feedback}\n\n" + prompt

    # 保存 prompt
    renderer_prompt_file = version_dir / "renderer_prompt.md"
    with open(renderer_prompt_file, 'w', encoding='utf-8') as f:
        f.write(f"# Renderer Prompt v{new_version}\n\n")
        f.write(f"**Base Image**: {base_image_path}\n")
        f.write(f"**Mode**: {'Iteration' if is_iteration else 'Edit from Baseline'}\n")
        f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
        f.write("---\n\n")
        f.write(prompt)
    print(f"   ✓ Prompt 已保存: {renderer_prompt_file}")

    # 保存反馈（如果有）
    if feedback:
        feedback_file = version_dir / "feedback.md"
        with open(feedback_file, 'w', encoding='utf-8') as f:
            f.write(f"# 迭代反馈 v{new_version}\n\n")
            f.write(f"**时间**: {datetime.now().isoformat()}\n\n")
            f.write("---\n\n")
            f.write(feedback)

    try:
        from openai import OpenAI
        import base64

        print(f"   连接 Gemini API...")

        client = OpenAI(
            base_url="http://127.0.0.1:8888/v1",
            api_key=os.getenv("OPENAI_API_KEY", "sk-placeholder")
        )

        content_parts = []

        # 添加基础图像（关键：inpainting 模式）
        if base_image_path and base_image_path.exists():
            try:
                with open(base_image_path, 'rb') as img_file:
                    img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                    img_type = "image/png" if str(base_image_path).lower().endswith('.png') else "image/jpeg"
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{img_type};base64,{img_base64}"}
                    })
                    print(f"   📎 已加载基础图")
            except Exception as e:
                print(f"⚠️  加载基础图失败: {e}", file=sys.stderr)

        # 添加文本 prompt
        content_parts.append({"type": "text", "text": prompt})

        print(f"   发送渲染请求...")

        model = "gemini-3-pro-image-16x9-4k"
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
# Pipeline 主流程
# =============================================================================

def run_pipeline(state: TaskState, args: argparse.Namespace, force: bool = False):
    """运行完整 pipeline"""

    if force:
        print("\n⚠️  强制模式：清除所有已有文件")
        state.clear()

    state.print_status()

    diff_only = getattr(args, 'diff_only', False)
    confirm = getattr(args, 'confirm_diff', False)

    # 阶段0: 保存输入
    if not state.stage_complete("input"):
        if not args.arch_code_path or not args.baseline:
            print("❌ 新任务必须提供 --arch_code_path 和 --baseline", file=sys.stderr)
            return False
        save_input(state, args)
    else:
        print(f"\n⏭️  跳过阶段0（已存在）")

    # 阶段1: 差异分析
    if not state.stage_complete("diff"):
        if not run_diff_analysis(state):
            print("\n❌ 阶段1失败，流程终止")
            state.print_status()
            return False
    else:
        print(f"\n⏭️  跳过阶段1（已存在: {state.diff_analysis.name}）")

    # 用户确认门禁
    if not state.stage_complete("confirmed"):
        if confirm:
            note = getattr(args, 'confirm_note', '')
            confirm_diff(state, note)
        else:
            print("\n" + "=" * 60)
            print("🧑‍⚖️  用户确认门禁")
            print("=" * 60)
            print(f"请查看差异分析报告: {state.diff_analysis}")
            print("")
            print("确认后继续生成：")
            print(f"  python3 skill.py --resume {state.task_id} --confirm-diff")
            print("")
            print("如需修改分析结果，直接编辑 diff_analysis.md 后再确认。")
            print("=" * 60)
            state.print_status()
            return True

    if diff_only:
        print("\n⏹️  已按 --diff-only 停止（仅差异分析）")
        state.print_status()
        return True

    # 阶段2: 增量 Schema 生成
    if not state.stage_complete("schema"):
        if not run_delta_schema(state):
            print("\n❌ 阶段2失败，流程终止")
            state.print_status()
            return False
    else:
        print(f"\n⏭️  跳过阶段2（已存在: {state.visual_schema.name}）")

    # 阶段3: 图像编辑渲染
    if not state.stage_complete("diagram"):
        feedback = getattr(args, 'feedback', None) if getattr(args, 'iterate', False) else None
        if not run_edit_renderer(state, feedback=feedback):
            print("\n❌ 阶段3失败，流程终止")
            state.print_status()
            return False
    else:
        print(f"\n⏭️  跳过阶段3（已有版本: v{state.get_latest_version()}）")
        print("   💡 使用 --iterate --feedback \"修改需求\" 进行迭代")

    print("\n" + "=" * 60)
    print("🎉 架构图生成完成!")
    print("=" * 60)
    state.print_status()
    return True


def run_iteration(state: TaskState, feedback: str) -> bool:
    """迭代模式"""
    print("\n🔄 迭代模式：基于上一版本 + 反馈生成新版本")

    if not state.stage_complete("schema"):
        print("❌ 迭代模式需要已有 Visual Schema", file=sys.stderr)
        return False

    if not run_edit_renderer(state, feedback=feedback):
        print("\n❌ 迭代渲染失败")
        state.print_status()
        return False

    print("\n" + "=" * 60)
    print("🎉 迭代完成!")
    print("=" * 60)
    state.print_status()
    return True


def list_tasks(base_dir: str = DEFAULT_BASE_DIR):
    """列出所有任务"""
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

        # 状态图标
        if status["has_diagram"]:
            icon = "✅"
        elif status["schema"]:
            icon = "🔶"
        elif status["confirmed"]:
            icon = "🔷"
        elif status["diff"]:
            icon = "⚠️ "  # 等待确认
        else:
            icon = "⬜"

        # Baseline 信息
        baseline_info = state.load_baseline_info()
        baseline_name = baseline_info.get('name', '?') if baseline_info else '?'

        version_str = f"v{status['latest_version']}" if status['latest_version'] > 0 else "v0"
        print(f"{icon} {task_dir.name:<40} [{version_str}] (← {baseline_name})")

    print("=" * 70)
    print("图例: ✅ 有架构图 | 🔶 Schema已生成 | 🔷 已确认 | ⚠️ 等待确认 | ⬜ 刚开始")


def list_baselines():
    """列出可用的 Baseline"""
    baselines = list_available_baselines()

    print(f"\n📚 可用的 Baseline ({BASELINES_DIR})")
    print("=" * 50)

    if not baselines:
        print("(无)")
    else:
        for name in baselines:
            image = get_baseline_image(name)
            code = get_baseline_code_path(name)
            size_kb = image.stat().st_size / 1024 if image else 0
            print(f"  • {name}")
            print(f"      图像: {image.name if image else 'N/A'} ({size_kb:.1f} KB)")
            print(f"      代码: {code or 'N/A'}")

    print("=" * 50)


def generate_task_id(arch_code_path: Optional[str] = None, baseline: Optional[str] = None) -> str:
    """生成任务 ID"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    parts = []
    if arch_code_path:
        parts.append(Path(arch_code_path).stem)
    if baseline:
        parts.append(f"from_{baseline}")

    if parts:
        return f"{'_'.join(parts)}_{timestamp}"
    return f"task_{timestamp}"


# =============================================================================
# 主入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Baseline 差异驱动的学术架构图生成器 v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基于 MambaOFR 生成新架构图
  python3 skill.py --arch_code_path basicofr/archs/freqmamba_arch.py --baseline MambaOFR

  # 确认差异分析后继续
  python3 skill.py --resume task_id --confirm-diff

  # 只运行差异分析
  python3 skill.py --arch_code_path arch.py --baseline MambaOFR --diff-only

  # 迭代修改
  python3 skill.py --resume task_id --iterate --feedback "修改需求"

  # 列出可用 Baseline
  python3 skill.py --list-baselines

  # 列出所有任务
  python3 skill.py --list
        """
    )

    # 输入参数
    parser.add_argument("--arch_code_path", type=str, help="新项目架构代码路径")
    parser.add_argument("--baseline", type=str, help="Baseline 项目名（如 MambaOFR、RTN）")
    parser.add_argument("--baseline_code_path", type=str, help="Baseline 架构代码路径（可选）")

    # 任务管理
    parser.add_argument("--resume", type=str, metavar="TASK_ID", help="恢复已有任务")
    parser.add_argument("--list", action="store_true", help="列出所有任务")
    parser.add_argument("--list-baselines", action="store_true", help="列出可用的 Baseline")
    parser.add_argument("--force", action="store_true", help="强制重新执行")

    # 确认和模式
    parser.add_argument("--confirm-diff", dest="confirm_diff", action="store_true", help="确认差异分析")
    parser.add_argument("--confirm-note", type=str, default="", help="确认备注")
    parser.add_argument("--diff-only", dest="diff_only", action="store_true", help="只运行差异分析")

    # 迭代
    parser.add_argument("--iterate", action="store_true", help="迭代模式")
    parser.add_argument("--feedback", type=str, help="迭代反馈")

    # 输出
    parser.add_argument("--output_path", type=str, default=DEFAULT_BASE_DIR, help="输出目录")

    args = parser.parse_args()

    # 列出 Baseline
    if args.list_baselines:
        list_baselines()
        return

    # 列出任务
    if args.list:
        list_tasks(args.output_path)
        return

    # 确定任务 ID
    if args.resume:
        task_id = args.resume
        print(f"\n📂 恢复任务: {task_id}")
    elif args.arch_code_path and args.baseline:
        task_id = generate_task_id(args.arch_code_path, args.baseline)
        print(f"\n📂 新建任务: {task_id}")
    else:
        print("❌ 错误: 必须提供 --arch_code_path + --baseline 或 --resume", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    # 验证 Baseline
    if args.baseline and args.baseline not in list_available_baselines():
        print(f"❌ 错误: 未找到 Baseline '{args.baseline}'", file=sys.stderr)
        print("可用的 Baseline:")
        for b in list_available_baselines():
            print(f"  • {b}")
        sys.exit(1)

    # 创建状态管理器
    state = TaskState(task_id, args.output_path)

    # 恢复模式下加载已保存的参数
    if args.resume and state.input_json.exists():
        saved_input = state.load_input()
        if saved_input:
            if not args.arch_code_path:
                args.arch_code_path = saved_input.get('arch_code_path')
            if not args.baseline:
                args.baseline = saved_input.get('baseline')
            if not args.baseline_code_path:
                args.baseline_code_path = saved_input.get('baseline_code_path')

    # 迭代模式
    if args.iterate and args.feedback:
        success = run_iteration(state, args.feedback)
        if not success:
            sys.exit(1)
        return

    # 运行 pipeline
    success = run_pipeline(state, args, force=args.force)

    if not success:
        print("\n💡 提示: 修复问题后，使用以下命令继续:")
        print(f"   python3 skill.py --resume {task_id}")
        sys.exit(1)


if __name__ == "__main__":
    main()
