
import asyncio
import hashlib
import logging
import os
import select
import subprocess
import sys
import termios
import threading
import time
import tty
from pathlib import Path
from .todo_scanner import TodoScanner
from .watcher import FileWatcher
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ToolUseBlock, ToolResultBlock


REPO_ROOT = Path(__file__).parent.parent.absolute()

WATCH_DIRS = [
    REPO_ROOT / "python" / "assassyn",
    REPO_ROOT / "docs",
]

BATCH_DELAY = 2.0
TEST_TIMEOUT = 500
LOG_FILE = REPO_ROOT / "agent" / "agent.log"
LOG_LEVEL = "INFO"
CLAUDE_CODE_PATH = os.getenv("CLAUDE_CODE_PATH", "claude")

_env_cache = None

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)

logger = logging.getLogger(__name__)



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

def run_linter(file_path):
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
    expected_doc = (source_file.parent / f"{source_file.stem}.md"
                    if source_file.name == '__init__.py'
                    else source_file.with_suffix('.md'))
    if expected_doc.exists():
        return None
    return f"Missing documentation: {expected_doc.relative_to(source_file.parents[2])}"

class AssassynDevAgent:
    def __init__(self):
        self.watcher = FileWatcher(WATCH_DIRS, self.on_file_changed)
        self.baseline_data = {}  
        self.changed_files = set()  
        self.processing = False
        self.waiting_for_input = False
        self.lock = threading.Lock()

        if not CLAUDE_CODE_PATH:
            raise RuntimeError(
                "Claude Code is required to run the Proactive Agent.\n"
                "Install Claude Agent SDK: pip install claude-agent-sdk"
            )

        self.claude_code_path = CLAUDE_CODE_PATH
        logger.info("Proactive Agent initialized with Claude Code")

    def _display_ai_action(self, block):
        if isinstance(block, ToolUseBlock):
            self._display_tool_use(block)
        elif isinstance(block, ToolResultBlock):
            self._display_tool_result(block)

    def _display_tool_use(self, block):
        path = block.input.get("file_path") or block.input.get("path")
        if path:
            print(f"    → {block.name}: {path}")
        elif block.name == "Bash":
            print(f"    → {block.name}: {block.input.get('command', '')[:50]}...")

    def _display_tool_result(self, block):
        output = str(block.content or "").strip()
        if output:
            print(output, flush=True)

    def _compute_hash(self, file_path):
        try:
            return hashlib.md5(file_path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None

    def _compute_signature(self, file_path):
        try:
            mtime = file_path.stat().st_mtime
            hash_val = hashlib.md5(file_path.read_bytes()).hexdigest()
            return (mtime, hash_val)
        except FileNotFoundError:
            return None
 
    def _initialize_baseline(self):
        for watch_dir in WATCH_DIRS:
            for py_file in watch_dir.rglob("*.py"):
                if file_data := self._compute_signature(py_file):
                    self.baseline_data[py_file] = file_data
        print(f"{len(self.baseline_data)} files tracked\n")

    def _display_status(self):
        if self.changed_files:
            files_str = ', '.join(str(f.relative_to(REPO_ROOT)) for f in sorted(self.changed_files))
            status = f"[{len(self.changed_files)} changed] {files_str}"
        else:
            status = "[0 changes]"

        if self.waiting_for_input:
            print(f"\n{status}")
        else:
            print(f"\r{status}", end='', flush=True)

    def on_file_changed(self, file_path):
        try:
            current_mtime = file_path.stat().st_mtime
        except FileNotFoundError:
            # Temporary file was deleted before we could stat it, ignore
            return
        with self.lock:
            self._track_file_change(file_path, current_mtime)

    def _track_file_change(self, file_path, current_mtime):
        baseline = self.baseline_data.get(file_path)

        if not baseline:
            self.baseline_data[file_path] = self._compute_signature(file_path)
            return

        baseline_mtime, baseline_hash = baseline
        if current_mtime == baseline_mtime:
            return

        current_hash = self._compute_hash(file_path)
        if not current_hash:
            return

        if current_hash == baseline_hash:
            self.changed_files.discard(file_path)
            self._display_status()
            return

        was_new = file_path not in self.changed_files
        self.changed_files.add(file_path)
        self._display_status()

        if was_new and not self.processing:
            self.processing = True
            threading.Thread(target=self._run_interactive_loop, daemon=True).start()

    def _monitor_changes(self, should_reprompt, original_files, original_mtimes):
        while not should_reprompt.is_set():
            time.sleep(0.1)
            with self.lock:
                current_files = set(self.changed_files)

                if current_files != original_files:
                    should_reprompt.set()
                    break

                for f in current_files:
                    if f.stat().st_mtime != original_mtimes.get(f):
                        should_reprompt.set()
                        break

    def _validate_files(self, files_to_process):
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
                if file_data := self._compute_signature(f):
                    self.baseline_data[f] = file_data
            self.changed_files.clear()
        print("✓ Changes accepted.\n")

    def _get_changed_files(self):
        with self.lock:
            return list(self.changed_files) if self.changed_files else None

    def _display_empty_state(self):
        os.system('clear' if os.name == 'posix' else 'cls')
        print("[0 changes]")

    def _display_session_header(self, files):
        os.system('clear' if os.name == 'posix' else 'cls')
        print("=" * 60)
        if len(files) == 1:
            print(f"Changed: {files[0].relative_to(REPO_ROOT)}")
        else:
            file_list = ', '.join(str(f.relative_to(REPO_ROOT)) for f in files)
            print(f"Changed {len(files)} files: {file_list}")
        print("=" * 60)

    def _check_tests_exist(self):
        return any((REPO_ROOT / "python" / test_dir).exists() for test_dir in ["unit-tests", "ci-tests"])

    def _prepare_session(self, files):
        self._display_session_header(files)
        has_doc_issues, has_lint_issues = self._validate_files(files)
        has_tests = self._check_tests_exist()
        return {
            'files': files,
            'has_doc_issues': has_doc_issues,
            'has_lint_issues': has_lint_issues,
            'has_tests': has_tests
        }

    def _start_change_monitor(self, files):
        should_reprompt = threading.Event()
        original_files = set(files)
        original_mtimes = {f: f.stat().st_mtime for f in files if f.exists()}

        monitor_thread = threading.Thread(
            target=self._monitor_changes,
            args=(should_reprompt, original_files, original_mtimes),
            daemon=True
        )
        monitor_thread.start()

        return {'event': should_reprompt, 'thread': monitor_thread}

    def _prompt_user_input(self, has_tests, monitor):
        response = None
        try:
            old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())

            if has_tests:
                print(f"\nOptions: [t]est | [a]i analyze | [d]oc sync | [n]o | [r]eset")
                response = self._interruptible_input("Choice: ", monitor['event'])
            else:
                print("No tests found")
                print("Options: [a]i analyze | [d]oc sync | [n]o | [r]eset")
                response = self._interruptible_input("Choice: ", monitor['event'])

            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        finally:
            pass

        return response

    def _get_user_choice(self, session):
        monitor = self._start_change_monitor(session['files'])

        with self.lock:
            self.waiting_for_input = True

        choice = self._prompt_user_input(session['has_tests'], monitor)

        with self.lock:
            self.waiting_for_input = False

        monitor['event'].set()
        monitor['thread'].join()

        return choice

    def _execute_user_action(self, choice, files):
        actions = {
            'reset': lambda: self._accept_changes(files),
            'r': lambda: self._accept_changes(files),
            'ai': lambda: self._handle_ai_analysis(files),
            'a': lambda: self._handle_ai_analysis(files),
            'doc': lambda: self._check_doc_sync(files),
            'd': lambda: self._check_doc_sync(files),
            'test': lambda: (self._run_tests(), self._accept_changes(files)),
            't': lambda: (self._run_tests(), self._accept_changes(files)),
        }

        action = actions.get(choice)
        if action:
            return action()
        elif choice not in ['n', 'no']:
            if self._check_tests_exist():
                self._run_tests()
            self._accept_changes(files)

    def _display_summary(self, session):
        if session['has_doc_issues']:
            print("Documentation issues detected (see log)")
        if session['has_lint_issues']:
            print("Linting issues detected (see log)")
        elif session['files']:
            print("Pylint clear")

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

    def _run_interactive_loop(self):
        while True:
            time.sleep(BATCH_DELAY)

            files = self._get_changed_files()
            if not files:
                self.processing = False
                self._display_empty_state()
                return

            session = self._prepare_session(files)
            choice = self._get_user_choice(session)

            if not choice:
                continue

            if choice in ['a', 'ai']:
                self._handle_ai_analysis(files)
                continue

            self._execute_user_action(choice, files)
            self._display_summary(session)

            with self.lock:
                self.processing = False
            return

    def _handle_ai_analysis(self, files_to_process):
        """Scan TODOs, optionally implement with AI."""

        todos = TodoScanner.scan_files(files_to_process)

        if not todos:
            print("No TODOs found")
            return

        # Show TODOs (minimal)
        print(f"\n{len(todos)} TODO(s):")
        for i, todo in enumerate(todos, 1):
            rel = todo.file_path.relative_to(REPO_ROOT)
            author = f"@{todo.author}: " if todo.author else ""
            print(f"  [{i}] {rel}:{todo.line_number} - {author}{todo.description}")

        # Ask if user wants implementation
        response = input("\nImplement with AI? [y/N]: ")
        if response.lower() == 'y':
            self._implement_todos(todos)

    def _check_doc_sync(self, files_to_process):
        """Check if Python files have corresponding up-to-date .md documentation."""
        doc_issues = []

        for file_path in files_to_process:
            if file_path.suffix != '.py':
                continue

            # Check for corresponding .md file
            md_path = file_path.with_suffix('.md')

            if not md_path.exists():
                doc_issues.append({
                    'file': file_path,
                    'issue': 'missing',
                    'md_path': md_path
                })
            else:
                # Check if .md is older than .py (potentially outdated)
                py_mtime = file_path.stat().st_mtime
                md_mtime = md_path.stat().st_mtime

                if md_mtime < py_mtime:
                    doc_issues.append({
                        'file': file_path,
                        'issue': 'outdated',
                        'md_path': md_path
                    })

        if not doc_issues:
            print("✓ All files have up-to-date documentation")
            return

        # Show issues
        print(f"\n{len(doc_issues)} documentation issue(s) found:")
        for i, issue in enumerate(doc_issues, 1):
            rel = issue['file'].relative_to(REPO_ROOT)
            if issue['issue'] == 'missing':
                print(f"  [{i}] {rel} - Missing .md file")
            else:
                print(f"  [{i}] {rel} - Documentation outdated")

        # Ask if user wants AI to update docs
        if self.claude_code_path:
            response = input("\nUpdate documentation with AI? [y/N]: ")
            if response.lower() == 'y':
                self._update_documentation(doc_issues)
        else:
            print("\nClaude Code unavailable for auto-update")

    def _update_documentation(self, doc_issues):
        """Update documentation using Claude Code."""
        print(f"\n→ Updating {len(doc_issues)} documentation file(s)...")

        for i, issue in enumerate(doc_issues, 1):
            rel = issue['file'].relative_to(REPO_ROOT)
            print(f"\n  [{i}/{len(doc_issues)}] {rel}")

            if issue['issue'] == 'missing':
                prompt = f"Create documentation at {issue['md_path']} for the Python file {issue['file']}. Follow CLAUDE.md and .cursor read-the-doc.mdc guidelines."
            else:
                prompt = f"Update documentation at {issue['md_path']} to match changes in {issue['file']}. Follow CLAUDE.md guidelines."

            cmd = [
                self.claude_code_path, "-p", prompt,
                "--allowedTools=Read,Write,Edit"
            ]

            try:
                result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, timeout=300)
                if result.returncode == 0:
                    print(f"    ✓ Documentation updated")
                else:
                    print(f"    ✗ Failed")
            except Exception as e:
                print(f"    ✗ Error: {e}")

        print("\n✓ Documentation sync complete")

    async def _process_ai_messages(self, prompt, options):
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    self._display_ai_action(block)

    def _build_todo_prompt(self, todo):
        return (
            f"Implement TODO at {todo.file_path}:{todo.line_number}: {todo.description}\n\n"
            f"IMPORTANT: Only modify source code files. "
            f"Do NOT run git commands (git add, git commit, git push, etc.). "
            f"Do NOT stage or commit changes."
            f"Please read and apply all rule files in `.cursor/rules/` directory (*.mdc). Load them into working context."
        )

    def _build_todo_options(self, stderr_cb, continue_conversation):
        return ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
            permission_mode="acceptEdits",
            cwd=str(REPO_ROOT),
            stderr=stderr_cb,
            continue_conversation=continue_conversation
        )

    async def _implement_single_todo(self, todo, index, total, stderr_lines):
        rel_path = todo.file_path.relative_to(REPO_ROOT)
        print(f"\n  [{index}/{total}] {rel_path}:{todo.line_number} - {todo.description}")

        prompt = self._build_todo_prompt(todo)

        def stderr_cb(line: str):
            stderr_lines.append(line)
            logger.debug(f"SDK stderr: {line}")

        options = self._build_todo_options(stderr_cb, index > 1)

        try:
            await self._process_ai_messages(prompt, options)
            print(f"  ✓ TODO {index} completed")
        except Exception as e:
            logger.error(f"Failed TODO {index}: {e}")
            if stderr_lines:
                for line in stderr_lines[-20:]:
                    logger.error(f"  {line}")
            raise

    async def _run_verification_tests(self, stderr_lines):
        print("\n→ Running tests...")

        def stderr_cb(line: str):
            stderr_lines.append(line)
            logger.debug(f"SDK stderr: {line}")

        test_options = ClaudeAgentOptions(
            allowed_tools=["Bash"],
            permission_mode="acceptEdits",
            cwd=str(REPO_ROOT),
            stderr=stderr_cb,
            continue_conversation=True
        )
        test_prompt = (
            "Run 'make test-all' to verify the changes. "
            "Do a debugging session to fix the issues if any."
            "Do NOT run git commands. Do NOT stage or commit changes."
        )
        await self._process_ai_messages(test_prompt, test_options)

    def _implement_todos(self, todos):
        if not todos:
            return

        print(f"→ Implementing {len(todos)} TODO(s)...")

        async def implement_async():
            stderr_lines = []
            for i, todo in enumerate(todos, 1):
                await self._implement_single_todo(todo, i, len(todos), stderr_lines)
            await self._run_verification_tests(stderr_lines)

        asyncio.run(implement_async())


    def start(self):
        load_env()
        print("Environment loaded.\n")

        self._initialize_baseline()
        self.watcher.start()
        self._display_status()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping agent...")
        finally:
            self.watcher.stop()


def main():
    agent = AssassynDevAgent()
    agent.start()


if __name__ == '__main__':
    main()
