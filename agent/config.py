"""Configuration for the Proactive Agent."""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.absolute()

WATCH_DIRS = [
    REPO_ROOT / "python" / "assassyn",
    REPO_ROOT / "docs",
]

IGNORE_PATTERNS = [
    line.strip() for line in (REPO_ROOT / ".agentignore").read_text().splitlines()
    if line.strip() and not line.startswith('#')
] if (REPO_ROOT / ".agentignore").exists() else []

BATCH_DELAY = 2.0
TEST_TIMEOUT = 300
LOG_FILE = REPO_ROOT / "agent" / "agent.log"
LOG_LEVEL = "INFO"
ENABLE_NOTIFICATIONS = True
NOTIFICATION_SOUND = True
