"""Utilities and file tracking for the Assassyn development agent."""

import hashlib
import logging
import subprocess
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.absolute()
TEST_TIMEOUT = 500

_env_cache = None

logger = logging.getLogger(__name__)


# ============================================================
# ENVIRONMENT AND EXECUTION UTILITIES
# ============================================================

def load_env():
    """Load environment variables from setup.sh."""
    global _env_cache
    if _env_cache is not None:
        return _env_cache

    result = subprocess.run(
        ['bash', '-c', f'source {REPO_ROOT}/setup.sh && env'],
        capture_output=True, text=True, cwd=REPO_ROOT
    )

    _env_cache = dict(line.partition('=')[::2] for line in result.stdout.splitlines() if '=' in line)
    return _env_cache


def run_with_env(cmd, **kwargs):
    """Run a command with the project environment."""
    return subprocess.run(cmd, env=load_env(), cwd=REPO_ROOT, **kwargs)


# ============================================================
# VALIDATION UTILITIES
# ============================================================

def run_linter(file_path):
    """Run syntax check and pylint on a Python file.

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        compile(file_path.read_text(), str(file_path), 'exec')
    except SyntaxError as e:
        return False, f"Syntax Error (line {e.lineno}):\n{e.msg}\n{e.text or ''}"

    pylintrc = REPO_ROOT / "python" / "assassyn" / ".pylintrc"

    result = run_with_env(['pylint', f'--rcfile={pylintrc}', str(file_path)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        return True, "Pylint OK"
    errors = '\n'.join(result.stdout.strip().split('\n')[:5])
    return False, f"Pylint:\n{errors}"


def check_documentation_exists(source_file):
    """Check if a Python file has corresponding documentation.

    Returns:
        str | None: Error message if documentation is missing, None otherwise
    """
    expected_doc = (source_file.parent / f"{source_file.stem}.md"
                    if source_file.name == '__init__.py'
                    else source_file.with_suffix('.md'))
    if expected_doc.exists():
        return None
    return f"Missing documentation: {expected_doc.relative_to(source_file.parents[2])}"


def validate_files(files):
    """Validate Python files for documentation and linting issues.

    Returns:
        tuple: (has_doc_issues: bool, has_lint_issues: bool)
    """
    has_doc_issues = False
    has_lint_issues = False

    for file_path in files:
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


def check_tests_exist():
    """Check if test directories exist in the project."""
    return any((REPO_ROOT / "python" / test_dir).exists() for test_dir in ["unit-tests", "ci-tests"])


def run_tests():
    """Run all tests using make test-all.

    Returns:
        bool: True if tests passed, False otherwise
    """
    result = run_with_env(['make', 'test-all'], capture_output=True, text=True, timeout=TEST_TIMEOUT)
    if result.returncode == 0:
        logger.info("✓ All tests passed")
        return True
    else:
        logger.error(f"✗ Tests failed\n{result.stdout[-500:]}")
        return False


# ============================================================
# FILE CHANGE TRACKING
# ============================================================

class FileChangeTracker:
    """Tracks file changes using mtime and MD5 hash signatures."""

    def __init__(self, watch_dirs):
        self.baseline_data = {}
        self.changed_files = set()
        self.lock = threading.Lock()
        self.watch_dirs = watch_dirs

    def initialize_baseline(self):
        """Build initial baseline of all tracked files."""
        for watch_dir in self.watch_dirs:
            for py_file in watch_dir.rglob("*.py"):
                if file_data := self._compute_signature(py_file):
                    self.baseline_data[py_file] = file_data
        print(f"{len(self.baseline_data)} files tracked\n")

    def track_change(self, file_path, mtime):
        """Track a file change and determine if it's a new change.

        Returns:
            bool: True if this is a new file change, False otherwise
        """
        baseline = self.baseline_data.get(file_path)

        if not baseline:
            self.baseline_data[file_path] = self._compute_signature(file_path)
            return False

        baseline_mtime, baseline_hash = baseline
        if mtime == baseline_mtime:
            return False

        current_hash = self._compute_hash(file_path)
        if not current_hash:
            return False

        if current_hash == baseline_hash:
            self.changed_files.discard(file_path)
            return False

        was_new = file_path not in self.changed_files
        self.changed_files.add(file_path)
        return was_new

    def get_changed_files(self):
        """Get list of changed files.

        Returns:
            list | None: List of changed file paths, or None if no changes
        """
        with self.lock:
            return list(self.changed_files) if self.changed_files else None

    def accept_changes(self, files):
        """Accept changes and update baseline for given files."""
        with self.lock:
            for f in files:
                if file_data := self._compute_signature(f):
                    self.baseline_data[f] = file_data
            self.changed_files.clear()
        print("Changes accepted.\n")

    def _compute_hash(self, file_path):
        """Compute MD5 hash of file contents."""
        return hashlib.md5(file_path.read_bytes()).hexdigest()

    def _compute_signature(self, file_path):
        """Compute file signature (mtime, hash).

        Returns:
            tuple: (mtime: float, hash: str)
        """
        mtime = file_path.stat().st_mtime
        hash_val = hashlib.md5(file_path.read_bytes()).hexdigest()
        return (mtime, hash_val)
