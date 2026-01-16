#!/usr/bin/env bash
# AI Coding Tools 一键更新脚本
# 更新: codeagent-wrapper, claude-code, codex, gemini-cli
#
# 使用方法:
#   chmod +x scripts/update-ai-tools.sh
#   ./scripts/update-ai-tools.sh
#
# 依赖:
#   - npm (Node.js 包管理器)
#   - git
#   - python3

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 版本检查函数
get_version() {
    local cmd=$1
    local version_flag=${2:---version}
    if command -v "$cmd" &>/dev/null; then
        $cmd $version_flag 2>&1 | head -1
    else
        echo "not installed"
    fi
}

echo ""
echo "=========================================="
echo "   AI Coding Tools 更新脚本"
echo "=========================================="
echo ""

# 显示当前版本
log_info "当前版本:"
echo "  codeagent-wrapper: $(get_version codeagent-wrapper)"
echo "  claude (claude-code): $(get_version claude)"
echo "  codex (codex-cli): $(get_version codex)"
echo "  gemini: $(get_version gemini)"
echo ""

# 1. 更新 codeagent-wrapper (来自 cexll/myclaude)
log_info "更新 codeagent-wrapper..."
INSTALL_DIR="${INSTALL_DIR:-$HOME/.claude}"
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

if git clone --depth 1 https://github.com/cexll/myclaude.git "$TEMP_DIR/myclaude" 2>/dev/null; then
    cd "$TEMP_DIR/myclaude"
    if python3 install.py --install-dir "$INSTALL_DIR" --module dev --force 2>&1 | grep -v "^$"; then
        log_success "codeagent-wrapper 更新完成"
    else
        log_warn "codeagent-wrapper 更新可能有问题，请检查"
    fi
    cd - >/dev/null
else
    log_error "无法克隆 cexll/myclaude 仓库"
fi

# 2. 更新 npm 全局包
log_info "更新 Claude Code CLI..."
if npm i -g @anthropic-ai/claude-code@latest 2>&1 | tail -3; then
    log_success "claude-code 更新完成"
else
    log_warn "claude-code 更新失败"
fi

log_info "更新 Codex CLI..."
if npm i -g @openai/codex@latest 2>&1 | tail -3; then
    log_success "codex 更新完成"
else
    log_warn "codex 更新失败"
fi

log_info "更新 Gemini CLI..."
if npm i -g @anthropic-ai/gemini-cli@latest 2>&1 | tail -3 || \
   npm i -g @google/gemini-cli@latest 2>&1 | tail -3; then
    log_success "gemini-cli 更新完成"
else
    log_warn "gemini-cli 更新失败 (可能包名不正确)"
fi

echo ""
echo "=========================================="
log_info "更新后版本:"
echo "  codeagent-wrapper: $(get_version codeagent-wrapper)"
echo "  claude (claude-code): $(get_version claude)"
echo "  codex (codex-cli): $(get_version codex)"
echo "  gemini: $(get_version gemini)"
echo "=========================================="
echo ""
log_success "所有更新完成!"
