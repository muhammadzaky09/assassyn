#!/bin/bash

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Starting Proactive Agent for Assassyn..."

# Check if AI is enabled
if [ "${ASSASSYN_AI_ENABLED:-true}" = "true" ]; then
    # Check if Claude Code is available
    CLAUDE_CODE_PATH=$(which claude)
    if ! command -v ${CLAUDE_CODE_PATH:-claude-code} &> /dev/null; then
        echo ""
        echo "⚠️  WARNING: Claude Code not found"
        echo ""
        echo "AI features require Claude Code. To enable AI:"
        echo "  1. Make sure Claude Code is installed and in your PATH"
        echo "  2. Or set CLAUDE_CODE_PATH to the executable location:"
        echo "       export CLAUDE_CODE_PATH='/path/to/claude-code'"
        echo ""
        echo "Alternatively, disable AI features:"
        echo "       export ASSASSYN_AI_ENABLED=false"
        echo ""
        exit 1
    else
        echo "using Claude Code integration"
    fi
fi

if ! python -c "import watchdog" 2>/dev/null; then
    echo "Installing agent dependencies..."
    pip install -r python/requirements.txt
fi

echo "Loading Assassyn environment..."
source setup.sh
echo "Starting daemon..."
python -m agent.daemon
