from datetime import datetime
from pathlib import Path


def append_log(vault_path: Path, action: str, detail: str) -> None:
    """Append an entry to the vault's log.md."""
    log_file = vault_path / "log.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- **{timestamp}** — {action}: {detail}\n"

    if not log_file.exists():
        log_file.write_text(f"# Activity Log\n\n{entry}")
    else:
        with open(log_file, "a") as f:
            f.write(entry)


def read_log(vault_path: Path, last: int | None = None) -> list[str]:
    """Read log entries. If last is set, return only the N most recent."""
    log_file = vault_path / "log.md"
    if not log_file.exists():
        return []

    lines = [
        line.strip()
        for line in log_file.read_text().splitlines()
        if line.startswith("- **")
    ]

    if last is not None:
        lines = lines[-last:]

    return lines
