#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = ["google-genai", "pillow"]
# ///
"""
Banana - AI Image Generation Tool using Gemini image models.

Generates images from text prompts using Google GenAI SDK.
Supports interactive mode for resolution and aspect ratio selection.

Usage:
    python3 banana.py "draw a cute cat"
    python3 banana.py "a sunset" -o sunset.png -r 4K -a 16:9
    python3 banana.py "a cat" --interactive

Environment Variables:
    BANANA_API_ENDPOINT: API endpoint (default: https://panpanpan.59188888.xyz)
    BANANA_API_KEY: API key
    BANANA_MODEL: Model name (default: gemini-3-pro-image-preview)
"""
import sys
import os
from datetime import datetime

try:
    from google import genai
    from google.genai import types
except ImportError:
    sys.stderr.write("ERROR: google-genai package not installed. Run: pip install google-genai\n")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    sys.stderr.write("ERROR: pillow package not installed. Run: pip install pillow\n")
    sys.exit(1)

DEFAULT_API_ENDPOINT = os.environ.get('BANANA_API_ENDPOINT', 'https://panpanpan.59188888.xyz')
DEFAULT_API_KEY = os.environ.get('BANANA_API_KEY', 'sk-LWZMkaIP0iBl2i1k3qStlnN7UNDHhVfh7WEyrtVE9Z1wM4iV')
DEFAULT_MODEL = os.environ.get('BANANA_MODEL', 'gemini-3-pro-image-preview')
DEFAULT_OUTPUT_DIR = '.'

# 支持的分辨率 (必须大写 K)
RESOLUTIONS = ['1K', '2K', '4K']

# 支持的宽高比
ASPECT_RATIOS = ['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9']


def log_error(msg: str):
    sys.stderr.write(f"ERROR: {msg}\n")


def log_info(msg: str):
    sys.stderr.write(f"INFO: {msg}\n")


def interactive_select(title: str, options: list, default_idx: int = 0) -> str:
    """交互式选择"""
    print(f"\n{title}")
    print("-" * 40)
    for i, opt in enumerate(options):
        marker = " (default)" if i == default_idx else ""
        print(f"  [{i + 1}] {opt}{marker}")
    print()

    while True:
        try:
            choice = input(f"请选择 [1-{len(options)}] (直接回车选择默认): ").strip()
            if choice == "":
                return options[default_idx]
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx]
            print(f"请输入 1 到 {len(options)} 之间的数字")
        except ValueError:
            print("请输入有效的数字")
        except KeyboardInterrupt:
            print("\n已取消")
            sys.exit(0)


def parse_args():
    """解析命令行参数"""
    prompt = None
    output_file = None
    output_dir = DEFAULT_OUTPUT_DIR
    model = DEFAULT_MODEL
    resolution = None  # None 表示需要交互选择
    aspect_ratio = None  # None 表示需要交互选择
    interactive = False

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ('-o', '--output'):
            if i + 1 < len(sys.argv):
                output_file = sys.argv[i + 1]
                i += 2
            else:
                log_error('-o requires a path')
                sys.exit(1)
        elif arg in ('-d', '--output-dir'):
            if i + 1 < len(sys.argv):
                output_dir = sys.argv[i + 1]
                i += 2
            else:
                log_error('-d requires a directory')
                sys.exit(1)
        elif arg in ('-m', '--model'):
            if i + 1 < len(sys.argv):
                model = sys.argv[i + 1]
                i += 2
            else:
                log_error('-m requires a model name')
                sys.exit(1)
        elif arg in ('-r', '--resolution'):
            if i + 1 < len(sys.argv):
                resolution = sys.argv[i + 1].upper()
                if resolution not in RESOLUTIONS:
                    log_error(f'Resolution must be one of: {", ".join(RESOLUTIONS)}')
                    sys.exit(1)
                i += 2
            else:
                log_error('-r requires a resolution')
                sys.exit(1)
        elif arg in ('-a', '--aspect-ratio'):
            if i + 1 < len(sys.argv):
                aspect_ratio = sys.argv[i + 1]
                if aspect_ratio not in ASPECT_RATIOS:
                    log_error(f'Aspect ratio must be one of: {", ".join(ASPECT_RATIOS)}')
                    sys.exit(1)
                i += 2
            else:
                log_error('-a requires an aspect ratio')
                sys.exit(1)
        elif arg in ('-i', '--interactive'):
            interactive = True
            i += 1
        elif arg in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        elif arg.startswith('-'):
            log_error(f'Unknown option: {arg}')
            sys.exit(1)
        elif prompt is None:
            prompt = arg
            i += 1
        else:
            i += 1

    if not prompt:
        log_error('Prompt required')
        print_usage()
        sys.exit(1)

    # 如果没有指定分辨率或宽高比，或者显式要求交互模式，则进入交互选择
    if interactive or resolution is None or aspect_ratio is None:
        print(f"\n🍌 Banana Image Generator")
        print(f"📝 Prompt: {prompt[:60]}{'...' if len(prompt) > 60 else ''}")

        if resolution is None:
            resolution = interactive_select(
                "📐 选择分辨率 (Resolution):",
                RESOLUTIONS,
                default_idx=2  # 默认 4K
            )

        if aspect_ratio is None:
            aspect_ratio = interactive_select(
                "📏 选择宽高比 (Aspect Ratio):",
                ASPECT_RATIOS,
                default_idx=0  # 默认 1:1
            )

    return {
        'prompt': prompt,
        'output_file': output_file,
        'output_dir': output_dir,
        'model': model,
        'resolution': resolution,
        'aspect_ratio': aspect_ratio
    }


def print_usage():
    print("""
🍌 Banana - AI Image Generation (Google GenAI SDK)

Usage:
    python3 banana.py "<prompt>" [options]

Options:
    -o, --output        Output file path
    -d, --output-dir    Output directory (default: .)
    -m, --model         Model (default: gemini-3-pro-image-preview)
    -r, --resolution    Resolution: 1K, 2K, 4K (interactive if not specified)
    -a, --aspect-ratio  Aspect ratio (interactive if not specified)
    -i, --interactive   Force interactive mode for all options
    -h, --help          Show help

Resolutions:
    1K    Standard resolution
    2K    Medium resolution
    4K    High resolution (Gemini 3 Pro only)

Aspect Ratios:
    1:1   Square
    2:3, 3:2   Portrait/Landscape (photo)
    3:4, 4:3   Portrait/Landscape (classic)
    4:5, 5:4   Portrait/Landscape (social)
    9:16, 16:9   Vertical/Horizontal (video)
    21:9  Ultra-wide

Environment Variables:
    BANANA_API_ENDPOINT  API endpoint
    BANANA_API_KEY       API key
    BANANA_MODEL         Default model

Examples:
    # Interactive mode (will ask for resolution and aspect ratio)
    python3 banana.py "a cute orange cat"

    # Non-interactive with all options
    python3 banana.py "sunset" -r 4K -a 16:9 -o sunset.png

    # Chinese prompt
    python3 banana.py "一只可爱的橘猫在窗台上晒太阳" -r 4K -a 1:1
""", file=sys.stderr)


def generate_filename(prompt: str, output_dir: str, resolution: str, aspect_ratio: str) -> str:
    """生成文件名"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in ' _-' else '_' for c in prompt[:20]).strip().replace(' ', '_')
    ar_safe = aspect_ratio.replace(':', 'x')
    return os.path.join(output_dir, f"banana_{timestamp}_{resolution}_{ar_safe}_{safe}.png")


def main():
    args = parse_args()

    print(f"\n{'='*50}")
    print(f"🍌 Generating image...")
    print(f"{'='*50}")
    log_info(f"Prompt: {args['prompt'][:50]}...")
    log_info(f"Model: {args['model']}")
    log_info(f"Resolution: {args['resolution']}")
    log_info(f"Aspect Ratio: {args['aspect_ratio']}")
    log_info(f"API Endpoint: {DEFAULT_API_ENDPOINT}")

    # 配置 GenAI 客户端
    client = genai.Client(
        api_key=DEFAULT_API_KEY,
        http_options={'api_version': 'v1beta', 'base_url': DEFAULT_API_ENDPOINT}
    )

    try:
        log_info("Sending request...")

        # 生成内容配置
        config = types.GenerateContentConfig(
            response_modalities=['TEXT', 'IMAGE'],
            image_config=types.ImageConfig(
                aspect_ratio=args['aspect_ratio'],
                image_size=args['resolution']
            )
        )

        response = client.models.generate_content(
            model=args['model'],
            contents=args['prompt'],
            config=config
        )

        log_info(f"Response received, parts: {len(response.parts) if response.parts else 0}")

        if not response.parts:
            log_error("Empty response")
            sys.exit(1)

        image_saved = False
        text_parts = []

        for part in response.parts:
            if part.text is not None:
                text_parts.append(part.text)
            elif part.inline_data is not None:
                # 获取图像
                image = part.as_image()

                # 确定输出路径
                if args['output_file']:
                    output_path = args['output_file']
                else:
                    output_path = generate_filename(
                        args['prompt'],
                        args['output_dir'],
                        args['resolution'],
                        args['aspect_ratio']
                    )

                # 确保目录存在
                dir_path = os.path.dirname(output_path)
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)

                # 保存图像
                image.save(output_path)
                print(f"\n✅ Image saved: {output_path}")
                print(f"   Resolution: {args['resolution']}, Aspect: {args['aspect_ratio']}")
                image_saved = True

        # 输出文本部分
        if text_parts:
            print("\n📝 Model response:")
            for text in text_parts:
                print(f"   {text}")

        if not image_saved and not text_parts:
            log_error("No image or text in response")
            sys.exit(1)

        sys.exit(0)

    except Exception as e:
        log_error(f"Request failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
