import subprocess
from .config import REPO_ROOT

_env_cache = None

def load_env():
    """Load environment from setup.sh once at startup."""
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
    """Run command with pre-loaded environment."""
    return subprocess.run(cmd, env=load_env(), cwd=REPO_ROOT, **kwargs)
