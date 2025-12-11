# AI Agent Tutorial

A quick guide to using Assassyn's proactive development agent.

## What Does It Do?

The agent watches your code and automatically:
- ✓ Runs linters when you save files
- ✓ Checks if documentation exists
- ✓ Runs tests (only if you confirm)
- ✓ Detects TODO comments (AI mode)

## Quick Start



### Start the Agent

From the repository root:

```bash
./start-agent.sh
```

You should see:

```
Loading Assassyn environment...
Environment loaded.

25 files tracked

Agent running (log: agent/agent.log)
Press Ctrl+C to stop
[0 changes]
```


### AI-Enhanced Prompt

When you save a file with AI enabled:

```
============================================================
Changed: python/assassyn/ir/ops.py
============================================================

Options: [t]est | [a]i analyze | [d]oc sync | [n]o | [r]eset
Choice:
```

**Options:**
- **t**: Run tests
- **a**: Scan for TODOs and optionally implement them with AI
- **d**: Check if documentation is up-to-date
- **n**: Skip everything
- **r**: Reset (accept changes without action)

### Using AI to Implement TODOs

Add a TODO comment in your code:

```python
# TODO: Add input validation
def divide(a, b):
    return a / b
```

Save the file, press **a**:

```
1 TODO(s):
  [1] python/assassyn/ir/arith.py:5 - Add input validation

Implement with AI? [y/N]: y

→ Implementing 1 TODO(s)...
  [1/1] python/assassyn/ir/arith.py:5 - Add input validation
  ✓ TODO 1 completed

→ Running tests...
✓ All tests passed
```

The AI will read your code, implement the TODO, and run tests automatically.

## Tips & Tricks

### 1. Multiple Files Changed

If you save multiple files quickly, the agent batches them:

```
Changed 3 files: ir/arith.py, ir/ops.py, ir/module.py
```

### 2. Watch Logs in Real-Time

Open a second terminal:

```bash
tail -f agent/agent.log
```


### 3. Linting Errors

If you have syntax errors, the agent catches them immediately:

```
⚠ Syntax Error (line 42):
invalid syntax
    return a +
```

Fix the error and save again.

### 4. Documentation Checks

The agent warns if `.md` documentation is missing:

```
WARNING - Missing documentation: python/assassyn/ir/arith.md
```

With AI enabled, press **d** to automatically generate documentation.


## Stopping the Agent

Press **Ctrl+C**:

```
^C
Stopping agent...
```

