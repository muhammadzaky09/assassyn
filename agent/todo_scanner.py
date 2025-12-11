
import re
from pathlib import Path
from dataclasses import dataclass


@dataclass
class TodoItem:
    """A single TODO item found in the codebase."""

    file_path: Path
    line_number: int
    author: str
    description: str


class TodoScanner:
    """Fast regex-based TODO scanner."""

    TODO_PATTERN = re.compile(r'#\s*TODO(?:\(@?(\w+)\))?\s*[:\-]?\s*(.+)')

    @staticmethod
    def scan_file(file_path: Path) -> list[TodoItem]:
        todos = []
        lines = file_path.read_text().splitlines()
        for i, line in enumerate(lines, 1):
            if match := TodoScanner.TODO_PATTERN.search(line):
                todos.append(
                    TodoItem(
                        file_path=file_path,
                        line_number=i,
                        author=match.group(1) or "",
                        description=match.group(2).strip(),
                    )
                )

        return todos

    @staticmethod
    def scan_files(files: list[Path]) -> list[TodoItem]:
        return [todo for f in files for todo in TodoScanner.scan_file(f)]
