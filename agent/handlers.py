"""UI handlers and AI-powered features for the Assassyn development agent."""

import asyncio
import logging
import os
import select
import subprocess
import sys
import termios
import threading
import time
import tty
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ToolUseBlock, ToolResultBlock
from .todo_scanner import TodoScanner
from .utils import REPO_ROOT, run_with_env

logger = logging.getLogger(__name__)

CLAUDE_CODE_PATH = os.getenv("CLAUDE_CODE_PATH", "claude")


# ============================================================
# SESSION UI - Display session state and status
# ============================================================

def display_status(changed_files, waiting_for_input):
    """Display current status of changed files."""
    if changed_files:
        files_str = ', '.join(str(f.relative_to(REPO_ROOT)) for f in sorted(changed_files))
        status = f"[{len(changed_files)} changed] {files_str}"
    else:
        status = "[0 changes]"

    if waiting_for_input:
        print(f"\n{status}")
    else:
        print(f"\r{status}", end='', flush=True)


def display_session_header(files):
    """Display header for a new session with changed files."""
    os.system('clear' if os.name == 'posix' else 'cls')
    print("=" * 60)
    if len(files) == 1:
        print(f"Changed: {files[0].relative_to(REPO_ROOT)}")
    else:
        file_list = ', '.join(str(f.relative_to(REPO_ROOT)) for f in files)
        print(f"Changed {len(files)} files: {file_list}")
    print("=" * 60)


def display_summary(has_doc_issues, has_lint_issues, has_files):
    """Display summary of validation results."""
    if has_doc_issues:
        print("Documentation issues detected (see log)")
    if has_lint_issues:
        print("Linting issues detected (see log)")
    elif has_files:
        print("Pylint clear")


def display_empty_state():
    """Clear screen and display empty state."""
    os.system('clear' if os.name == 'posix' else 'cls')
    print("[0 changes]")


# ============================================================
# USER INTERACTION - Get user choices with change monitoring
# ============================================================

def get_user_choice(change_tracker, files, has_tests, waiting_for_input_flag):
    """Get user choice with interruptible input that monitors for file changes.

    Args:
        change_tracker: FileChangeTracker instance
        files: List of changed files
        has_tests: Whether tests exist
        waiting_for_input_flag: Mutable list [bool] to track input state

    Returns:
        str | None: User's choice, or None if interrupted by file changes
    """
    # Start monitoring for changes while waiting for input
    event = threading.Event()
    original_files = set(files)
    original_mtimes = {f: f.stat().st_mtime for f in files if f.exists()}

    def monitor_changes():
        """Background thread that monitors for file changes."""
        while not event.is_set():
            time.sleep(0.1)
            with change_tracker.lock:
                current_files = set(change_tracker.changed_files)
                if current_files != original_files:
                    event.set()
                    break
                for f in current_files:
                    if f.stat().st_mtime != original_mtimes.get(f):
                        event.set()
                        break

    monitor_thread = threading.Thread(target=monitor_changes, daemon=True)
    monitor_thread.start()

    # Get user input
    waiting_for_input_flag[0] = True
    choice = _prompt_for_choice(has_tests, event)
    waiting_for_input_flag[0] = False

    # Stop monitoring
    event.set()
    monitor_thread.join()

    return choice


def _prompt_for_choice(has_tests, interrupt_event):
    """Prompt user for action choice with interruptible input.

    Returns:
        str | None: User input or None if interrupted
    """
    try:
        old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())

        if has_tests:
            print(f"\nOptions: [t]est | [a]i analyze | [d]oc sync | [n]o | [r]eset")
        else:
            print("No tests found")
            print("Options: [a]i analyze | [d]oc sync | [n]o | [r]eset")

        # Interruptible input
        print("Choice: ", end='', flush=True)
        response = []

        while not interrupt_event.is_set():
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

        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    except Exception:
        pass

    return None


# ============================================================
# TODO IMPLEMENTATION - AI-powered TODO handling
# ============================================================

def handle_ai_analysis(files):
    """Scan files for TODOs and offer AI implementation."""
    todos = TodoScanner.scan_files(files)

    if not todos:
        print("No TODOs found")
        return

    print(f"\n{len(todos)} TODO(s):")
    for i, todo in enumerate(todos, 1):
        rel = todo.file_path.relative_to(REPO_ROOT)
        author = f"@{todo.author}: " if todo.author else ""
        print(f"  [{i}] {rel}:{todo.line_number} - {author}{todo.description}")

    response = input("\nImplement with AI? [y/N]: ")
    if response.lower() == 'y':
        implement_todos(todos)


def implement_todos(todos):
    """Implement TODOs using Claude Agent SDK.

    Implements each TODO sequentially, then runs verification tests.
    """
    if not todos:
        return

    print(f"Implementing {len(todos)} TODO(s)...")

    async def run_implementation():
        """Async implementation workflow."""
        stderr_lines = []

        # Implement each TODO
        for i, todo in enumerate(todos, 1):
            rel_path = todo.file_path.relative_to(REPO_ROOT)
            print(f"\n  [{i}/{len(todos)}] {rel_path}:{todo.line_number} - {todo.description}")

            prompt = (
                f"Implement TODO at {todo.file_path}:{todo.line_number}: {todo.description}\n\n"
                f"IMPORTANT: Only modify source code files. "
                f"Do NOT run git commands (git add, git commit, git push, etc.). "
                f"Do NOT stage or commit changes."
                f"Please read and apply all rule files in `.cursor/rules/` directory (*.mdc). "
                f"Load them into working context."
            )

            def stderr_cb(line: str):
                stderr_lines.append(line)
                logger.debug(f"SDK stderr: {line}")

            options = ClaudeAgentOptions(
                allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
                permission_mode="acceptEdits",
                cwd=str(REPO_ROOT),
                stderr=stderr_cb,
                continue_conversation=(i > 1)
            )

            try:
                await _process_ai_messages(prompt, options)
                print(f"TODO {i} completed")
            except Exception as e:
                logger.error(f"Failed TODO {i}: {e}")
                if stderr_lines:
                    for line in stderr_lines[-20:]:
                        logger.error(f"  {line}")
                raise

        # Run verification tests
        print("\n→ Running tests...")

        def test_stderr_cb(line: str):
            stderr_lines.append(line)
            logger.debug(f"SDK stderr: {line}")

        test_options = ClaudeAgentOptions(
            allowed_tools=["Bash"],
            permission_mode="acceptEdits",
            cwd=str(REPO_ROOT),
            stderr=test_stderr_cb,
            continue_conversation=True
        )

        test_prompt = (
            "Run 'make test-all' to verify the changes. "
            "Do a debugging session to fix the issues if any."
            "Do NOT run git commands. Do NOT stage or commit changes."
        )

        await _process_ai_messages(test_prompt, test_options)

    asyncio.run(run_implementation())


async def _process_ai_messages(prompt, options):
    """Process AI messages and display tool usage.

    Shows which files are being Read/Edit/Written and displays command output.
    """
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                # Show tool invocations
                if isinstance(block, ToolUseBlock):
                    path = block.input.get("file_path") or block.input.get("path")
                    if path:
                        print(f"    → {block.name}: {path}")
                    elif block.name == "Bash":
                        cmd = block.input.get("command", "")[:50]
                        print(f"    → {block.name}: {cmd}...")

                # Show tool output (especially Bash results)
                elif isinstance(block, ToolResultBlock):
                    output = str(block.content or "").strip()
                    if output:
                        print(output, flush=True)


# ============================================================
# DOCUMENTATION SYNC - Manage documentation files
# ============================================================

def check_doc_sync(files):
    """Check if documentation files are missing or outdated."""
    doc_issues = []

    for file_path in files:
        if file_path.suffix != '.py':
            continue

        md_path = file_path.with_suffix('.md')

        if not md_path.exists():
            doc_issues.append({
                'file': file_path,
                'issue': 'missing',
                'md_path': md_path
            })
        else:
            py_mtime = file_path.stat().st_mtime
            md_mtime = md_path.stat().st_mtime

            if md_mtime < py_mtime:
                doc_issues.append({
                    'file': file_path,
                    'issue': 'outdated',
                    'md_path': md_path
                })

    if not doc_issues:
        print("All files have up-to-date documentation")
        return

    print(f"\n{len(doc_issues)} documentation issue(s) found:")
    for i, issue in enumerate(doc_issues, 1):
        rel = issue['file'].relative_to(REPO_ROOT)
        if issue['issue'] == 'missing':
            print(f"  [{i}] {rel} - Missing .md file")
        else:
            print(f"  [{i}] {rel} - Documentation outdated")

    if CLAUDE_CODE_PATH:
        response = input("\nUpdate documentation with AI? [y/N]: ")
        if response.lower() == 'y':
            update_documentation(doc_issues)
    else:
        print("\nClaude Code unavailable for auto-update")


def update_documentation(doc_issues):
    """Update documentation files using Claude Code."""
    print(f"\n→ Updating {len(doc_issues)} documentation file(s)...")

    for i, issue in enumerate(doc_issues, 1):
        rel = issue['file'].relative_to(REPO_ROOT)
        print(f"\n  [{i}/{len(doc_issues)}] {rel}")

        if issue['issue'] == 'missing':
            prompt = (
                f"Create documentation at {issue['md_path']} for the Python file {issue['file']}. "
                f"Follow CLAUDE.md and .cursor read-the-doc.mdc guidelines."
            )
        else:
            prompt = (
                f"Update documentation at {issue['md_path']} to match changes in {issue['file']}. "
                f"Follow CLAUDE.md guidelines."
            )

        cmd = [
            CLAUDE_CODE_PATH, "-p", prompt,
            "--allowedTools=Read,Write,Edit"
        ]

        try:
            result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, timeout=300)
            if result.returncode == 0:
                print(f"Documentation updated")
            else:
                print(f"Failed to update documentation")
        except Exception as e:
            print(f"Error: {e}")

    print("\nDocumentation sync complete")
