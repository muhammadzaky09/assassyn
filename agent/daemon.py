"""Main daemon orchestrator for the Assassyn development agent."""

import logging
import os
import threading
import time
from pathlib import Path
from .watcher import FileWatcher
from .utils import (
    REPO_ROOT,
    FileChangeTracker,
    load_env,
    validate_files,
    check_tests_exist,
    run_tests
)
from .handlers import (
    display_status,
    display_empty_state,
    display_session_header,
    display_summary,
    get_user_choice,
    handle_ai_analysis,
    check_doc_sync
)

WATCH_DIRS = [
    REPO_ROOT / "python" / "assassyn",
    REPO_ROOT / "docs",
]

BATCH_DELAY = 2.0
LOG_FILE = REPO_ROOT / "agent" / "agent.log"
LOG_LEVEL = "INFO"
CLAUDE_CODE_PATH = os.getenv("CLAUDE_CODE_PATH", "claude")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)

logger = logging.getLogger(__name__)


class AssassynDevAgent:
    """Main orchestrator for the Assassyn development agent."""

    def __init__(self):
        self.change_tracker = FileChangeTracker(WATCH_DIRS)
        self.watcher = FileWatcher(WATCH_DIRS, self.on_file_changed)
        self.processing = False
        self.waiting_for_input = False

        if not CLAUDE_CODE_PATH:
            raise RuntimeError(
                "Claude Code is required to run the Proactive Agent.\n"
                "Install Claude Agent SDK: pip install claude-agent-sdk"
            )

        logger.info("Proactive Agent initialized with Claude Code")

    def on_file_changed(self, file_path):
        """Handle file change events from the watcher."""
        try:
            mtime = file_path.stat().st_mtime
        except FileNotFoundError:
            return

        with self.change_tracker.lock:
            should_start = self.change_tracker.track_change(file_path, mtime)
            display_status(self.change_tracker.changed_files, self.waiting_for_input)

        if should_start and not self.processing:
            self.processing = True
            threading.Thread(target=self._run_interactive_loop, daemon=True).start()

    def _run_interactive_loop(self):
        """Run the interactive loop for processing file changes."""
        while True:
            time.sleep(BATCH_DELAY)

            files = self.change_tracker.get_changed_files()
            if not files:
                self.processing = False
                display_empty_state()
                return

            session = self._prepare_session(files)

            waiting_for_input_flag = [self.waiting_for_input]
            choice = get_user_choice(self.change_tracker, files, session['has_tests'], waiting_for_input_flag)
            self.waiting_for_input = waiting_for_input_flag[0]

            if not choice:
                continue

            if choice in ['a', 'ai']:
                handle_ai_analysis(files)
                continue

            self._execute_user_action(choice, files)
            display_summary(
                session['has_doc_issues'],
                session['has_lint_issues'],
                bool(session['files'])
            )

            self.processing = False
            return

    def _prepare_session(self, files):
        """Prepare session by displaying header and validating files."""
        display_session_header(files)
        has_doc_issues, has_lint_issues = validate_files(files)
        has_tests = check_tests_exist()
        return {
            'files': files,
            'has_doc_issues': has_doc_issues,
            'has_lint_issues': has_lint_issues,
            'has_tests': has_tests
        }

    def _execute_user_action(self, choice, files):
        """Execute the user's chosen action."""
        actions = {
            'reset': lambda: self.change_tracker.accept_changes(files),
            'r': lambda: self.change_tracker.accept_changes(files),
            'doc': lambda: check_doc_sync(files),
            'd': lambda: check_doc_sync(files),
            'test': lambda: (run_tests(), self.change_tracker.accept_changes(files)),
            't': lambda: (run_tests(), self.change_tracker.accept_changes(files)),
        }

        action = actions.get(choice)
        if action:
            return action()
        elif choice not in ['n', 'no']:
            if check_tests_exist():
                run_tests()
            self.change_tracker.accept_changes(files)

    def start(self):
        """Start the daemon."""
        load_env()
        print("Environment loaded.\n")

        self.change_tracker.initialize_baseline()
        self.watcher.start()
        display_status(self.change_tracker.changed_files, self.waiting_for_input)

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping agent...")
        finally:
            self.watcher.stop()


def main():
    """Main entry point."""
    agent = AssassynDevAgent()
    agent.start()


if __name__ == '__main__':
    main()
