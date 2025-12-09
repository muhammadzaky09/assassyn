"""Main daemon for the Proactive Agent (Phase 0)."""

import logging
import hashlib
import threading
import os
import sys
import termios
import tty
import time
import select
from pathlib import Path

from .config import WATCH_DIRS, LOG_FILE, LOG_LEVEL, REPO_ROOT, BATCH_DELAY, TEST_TIMEOUT
from .watcher import FileWatcher
from .analyzers import check_documentation_exists
from .actions import run_linter
from .env_handler import run_with_env

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)

logger = logging.getLogger(__name__)


class ProactiveAgent:

    def __init__(self):
        self.watcher = FileWatcher(WATCH_DIRS, self.on_file_changed)
        self.baseline_data = {}  # Original file state: {Path: (mtime, hash)}
        self.changed_files = set()  # Files that differ from baseline
        self.processing = False
        self.waiting_for_input = False
        self.lock = threading.Lock()
        logger.info("Proactive Agent initialized")

    def _get_file_data(self, file_path):
        """Get file mtime and hash."""
        try:
            mtime = file_path.stat().st_mtime
            hash_val = hashlib.md5(file_path.read_bytes()).hexdigest()
            return (mtime, hash_val)
        except Exception:
            return None

    def _initialize_baseline(self):
        """Snapshot all current files as baseline."""
        print("Building baseline snapshot...")
        for watch_dir in WATCH_DIRS:
            for py_file in watch_dir.rglob("*.py"):
                if file_data := self._get_file_data(py_file):
                    self.baseline_data[py_file] = file_data
        print(f"Baseline: {len(self.baseline_data)} files tracked\n")

    def _update_status(self, force_newline=False):
        """Print current change status."""
        if force_newline and not self.waiting_for_input:
            print()

        if self.changed_files:
            files_str = ', '.join(str(f.relative_to(REPO_ROOT)) for f in sorted(self.changed_files))
            status = f"[{len(self.changed_files)} changed] {files_str}"
        else:
            status = "[0 changes]"

        if self.waiting_for_input:
            # During input, print status on a new line above the prompt
            print(f"\n{status}")
        else:
            # Normal operation, update in-place
            print(f"\r{status}", end='', flush=True)

    def on_file_changed(self, file_path):
        if file_path.suffix != '.py':
            return
        try:
            current_mtime = file_path.stat().st_mtime
        except Exception:
            return
        with self.lock:
            self._handle_file_change(file_path, current_mtime)

    def _handle_file_change(self, file_path, current_mtime):
        baseline_data = self.baseline_data.get(file_path)

        if baseline_data is None:
            # New file - store both mtime and hash
            if file_data := self._get_file_data(file_path):
                self.baseline_data[file_path] = file_data
            return

        baseline_mtime, baseline_hash = baseline_data

        # Fast path: mtime unchanged = no change
        if current_mtime == baseline_mtime:
            return

        # mtime changed - check if content actually changed
        try:
            current_hash = hashlib.md5(file_path.read_bytes()).hexdigest()
        except Exception:
            return

        # Check for content revert
        if current_hash == baseline_hash:
            # Content reverted to baseline
            if file_path in self.changed_files:
                self.changed_files.discard(file_path)
                if not self.waiting_for_input:
                    self._update_status(force_newline=True)
            return

        # File actually changed
        was_new_file = file_path not in self.changed_files
        self.changed_files.add(file_path)
        if not self.waiting_for_input:
            self._update_status(force_newline=True)

        if was_new_file and not self.processing:
            self.processing = True
            threading.Thread(target=self._prompt_user, daemon=True).start()

    def _monitor_changes(self, should_reprompt, original_files, original_mtimes):
        """Monitor for file changes during input - uses mtime only for speed."""
        while not should_reprompt.is_set():
            time.sleep(0.1)  # Check every 100ms
            with self.lock:
                current_files = set(self.changed_files)

                # Check if file set changed (added/removed)
                if current_files != original_files:
                    should_reprompt.set()
                    break

                # Check if mtime of existing files changed
                for f in current_files:
                    try:
                        current_mtime = f.stat().st_mtime
                        if current_mtime != original_mtimes.get(f):
                            should_reprompt.set()
                            break
                    except Exception:
                        pass

    def _process_files(self, files_to_process):
        has_doc_issues = False
        has_lint_issues = False

        for file_path in files_to_process:
            if doc_error := check_documentation_exists(file_path):
                logger.warning(doc_error)
                has_doc_issues = True
            
            success, msg = run_linter(file_path)
            file_rel = file_path.relative_to(REPO_ROOT)
            if not success:
                logger.warning(f"[{file_rel}] {msg}")
                has_lint_issues = True
            else:
                logger.info(f"[{file_rel}] {msg}")
        
        return has_doc_issues, has_lint_issues

    def _run_tests(self):
        try:
            result = run_with_env(['make', 'test-all'], capture_output=True, text=True, timeout=TEST_TIMEOUT)
            if result.returncode == 0:
                logger.info("✓ All tests passed")
                return True
            else:
                logger.error(f"✗ Tests failed\n{result.stdout[-500:]}")
                return False
        except Exception as e:
            logger.error(f"✗ Test error: {e}")
            return False

    def _accept_changes(self, files_to_process):
        with self.lock:
            for f in files_to_process:
                if file_data := self._get_file_data(f):
                    self.baseline_data[f] = file_data
            self.changed_files.clear()
        print("✓ Changes accepted.\n")

    def _interruptible_input(self, prompt: str, should_reprompt: threading.Event):
        """Input function that can be interrupted by file changes."""
        print(prompt, end='', flush=True)
        response = []

        while not should_reprompt.is_set():
            if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                char = sys.stdin.read(1)
                if char == '\n':
                    print()
                    return ''.join(response).strip().lower()
                elif char == '\x7f':  # Backspace
                    if response:
                        response.pop()
                        print('\b \b', end='', flush=True)
                else:
                    response.append(char)
                    print(char, end='', flush=True)
        return None

    def _prompt_user(self):
        """
        Main user interaction loop.
        Runs in a dedicated thread.
        Loops to handle interruptions (re-prompts) efficiently.
        """
        while True:
            time.sleep(BATCH_DELAY)

            with self.lock:
                if not self.changed_files:
                    self.processing = False
                    os.system('clear' if os.name == 'posix' else 'cls')
                    print("[0 changes]")
                    return
                
                # Update files list
                files_to_process = list(self.changed_files)

            os.system('clear' if os.name == 'posix' else 'cls')
            print(f"{'='*60}")
            if len(files_to_process) == 1:
                print(f"Changed: {files_to_process[0].relative_to(REPO_ROOT)}")
            else:
                print(f"Changed {len(files_to_process)} files: {', '.join(str(f.relative_to(REPO_ROOT)) for f in files_to_process)}")
            print('='*60)

            has_doc_issues, has_lint_issues = self._process_files(files_to_process)

            # Check if tests exist
            has_tests = any((REPO_ROOT / "python" / test_dir).exists() for test_dir in ["unit-tests", "ci-tests"])

            # Setup monitoring for THIS prompt session
            should_reprompt = threading.Event()
            original_files = set(files_to_process)
            original_mtimes = {f: f.stat().st_mtime for f in files_to_process if f.exists()}

            monitor_thread = threading.Thread(
                target=self._monitor_changes, 
                args=(should_reprompt, original_files, original_mtimes), 
                daemon=True
            )
            monitor_thread.start()

            with self.lock:
                self.waiting_for_input = True

            response = None
            try:
                old_settings = termios.tcgetattr(sys.stdin)
                tty.setcbreak(sys.stdin.fileno())

                if has_tests:
                    print(f"\nRunning ALL tests")
                    response = self._interruptible_input("Run tests? [Y/n/reset]: ", should_reprompt)
                else:
                    print("No tests found")
                    response = self._interruptible_input("Accept changes anyway? [Y/n]: ", should_reprompt)
                
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            finally:
                should_reprompt.set()  # Ensure monitor stops
                monitor_thread.join()  # Wait for it to clean up
                with self.lock:
                    self.waiting_for_input = False

            # Case 1: Interrupted by new file change
            if response is None:
                # Loop back to top to refresh files and prompt
                continue

            # Case 2: User provided input
            if response == 'reset':
                self._accept_changes(files_to_process)
                print("✓ Baseline reset. All changes accepted.\n")
            elif response not in ['n', 'no']:
                if has_tests:
                    self._run_tests()
                self._accept_changes(files_to_process)

            if has_doc_issues:
                print("⚠ Documentation issues detected (see log)")
            if has_lint_issues:
                print("⚠ Linting issues detected (see log)")
            elif files_to_process:
                print("✓ Pylint clear")

            with self.lock:
                self.processing = False
            return

    def start(self):
        logger.info(f"Starting agent (log: {LOG_FILE})")
        print("Loading Assassyn environment...")
        from .env_handler import load_env
        load_env()
        print("Environment loaded.\n")

        self._initialize_baseline()
        self.watcher.start()
        print(f"Agent running (log: {LOG_FILE})")
        print("Press Ctrl+C to stop")
        self._update_status()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping agent...")
        finally:
            self.watcher.stop()


def main():
    """Entry point for the daemon."""
    agent = ProactiveAgent()
    agent.start()


if __name__ == '__main__':
    main()
