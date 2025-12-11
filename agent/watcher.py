import fnmatch
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.absolute()
IGNORE_PATTERNS = [
    line.strip() for line in (REPO_ROOT / ".agentignore").read_text().splitlines()
    if line.strip() and not line.startswith('#')
] if (REPO_ROOT / ".agentignore").exists() else []

class FileWatcher(FileSystemEventHandler):
    def __init__(self, watch_dirs, callback):
        self.observer = Observer()
        self.callback = callback
        for d in watch_dirs:
            if d.exists():
                self.observer.schedule(self, str(d), recursive=True)

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if not path.is_relative_to(REPO_ROOT):
            return
        rel = str(path.relative_to(REPO_ROOT))
        if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, f"*/{p}") for p in IGNORE_PATTERNS):
            return
        self.callback(path)

    def start(self):
        self.observer.start()

    def stop(self):
        self.observer.stop()
        self.observer.join()
