#!/bin/bash

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Starting Proactive Agent for Assassyn..."

if ! python -c "import watchdog" 2>/dev/null; then
    echo "Installing agent dependencies..."
    pip install -r agent/requirements.txt
fi

echo "Loading Assassyn environment..."
source setup.sh
echo "Starting daemon..."
python -m agent.daemon
