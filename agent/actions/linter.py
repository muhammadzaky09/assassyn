import subprocess
from ..config import REPO_ROOT
from ..env_handler import run_with_env


def run_linter(file_path):
    """Run pylint on Python file."""
    # Check syntax first
    try:
        compile(file_path.read_text(), str(file_path), 'exec')
    except SyntaxError as e:
        return False, f"⚠ Syntax Error (line {e.lineno}):\n{e.msg}\n{e.text or ''}"

    pylintrc = REPO_ROOT / "python" / "assassyn" / ".pylintrc"
    try:
        result = run_with_env(['pylint', f'--rcfile={pylintrc}', str(file_path)],
                              capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, "✓ Pylint OK"
        errors = '\n'.join(result.stdout.strip().split('\n')[:5])
        return False, f"⚠ Pylint:\n{errors}"
    except subprocess.TimeoutExpired:
        return False, "Pylint timed out"
    except OSError as e:
        return False, f"Pylint failed: {e}"
