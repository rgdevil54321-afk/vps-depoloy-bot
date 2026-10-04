"""Structured Logging Center - rotating file logs, search, export, and stats."""
import os
import re
import shutil
import logging
import logging.handlers

from core.services import metrics, DATA_DIR, BASE_DIR

logger = logging.getLogger("turtle.logging")

LOG_PATH = os.path.join(BASE_DIR, "bot.log")

_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 3

_LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b", re.IGNORECASE)


class LogManager:
    """Manages the rotating bot log file with search, filter, and export."""

    def __init__(self):
        self._handler = logging.handlers.RotatingFileHandler(
            LOG_PATH,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        self._handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        self._handler.setLevel(logging.DEBUG)

        root = logging.getLogger("turtle")
        root.setLevel(logging.DEBUG)
        root.addHandler(self._handler)

        logger.info("LogManager initialised - writing to %s", LOG_PATH)

    # -- logger access ------------------------------------------------------

    def get_logger(self, name: str) -> logging.Logger:
        """Return a stdlib logger that writes through the rotating handler."""
        child = logging.getLogger(name)
        child.setLevel(logging.DEBUG)
        if self._handler not in child.handlers:
            child.addHandler(self._handler)
        return child

    # -- reading ------------------------------------------------------------

    def _read_lines(self, path: str = None) -> list:
        target = path or LOG_PATH
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                return f.readlines()
        except FileNotFoundError:
            return []

    def tail(self, n: int = 50) -> list:
        """Return the last *n* lines from bot.log."""
        lines = self._read_lines()
        return lines[-n:]

    def search(self, query: str, level: str = None, limit: int = 50) -> list:
        """Return lines containing *query*, optionally filtered by level."""
        results = []
        for line in reversed(self._read_lines()):
            if query.lower() not in line.lower():
                continue
            if level:
                if not re.search(r"\b" + re.escape(level.upper()) + r"\b", line, re.IGNORECASE):
                    continue
            results.append(line)
            if len(results) >= limit:
                break
        results.reverse()
        return results

    def filter_by_level(self, level: str, n: int = 50) -> list:
        """Return the last *n* lines matching *level* (e.g. 'ERROR')."""
        target = level.upper()
        results = []
        for line in reversed(self._read_lines()):
            if re.search(r"\b" + re.escape(target) + r"\b", line, re.IGNORECASE):
                results.append(line)
                if len(results) >= n:
                    break
        results.reverse()
        return results

    def filter_by_category(self, category: str, n: int = 50) -> list:
        """Return lines whose logger name contains *category*.

        Log lines are expected in the format
        ``YYYY-MM-DD HH:MM:SS [LEVEL] turtle.<category>: message``.
        """
        results = []
        cat_lower = category.lower()
        for line in reversed(self._read_lines()):
            line_lower = line.lower()
            if cat_lower in line_lower:
                results.append(line)
                if len(results) >= n:
                    break
        results.reverse()
        return results

    # -- stats --------------------------------------------------------------

    def get_stats(self) -> dict:
        """Aggregate statistics about the log file."""
        lines = self._read_lines()
        total = len(lines)
        error_count = 0
        warning_count = 0

        for line in lines:
            upper = line.upper()
            if " ERROR " in upper or upper.startswith("ERROR "):
                error_count += 1
            elif " WARNING " in upper or upper.startswith("WARNING "):
                warning_count += 1

        try:
            file_size = os.path.getsize(LOG_PATH)
        except OSError:
            file_size = 0

        metrics.set("logs.total_lines", total)
        metrics.set("logs.error_count", error_count)
        metrics.set("logs.warning_count", warning_count)

        return {
            "total_lines": total,
            "error_count": error_count,
            "warning_count": warning_count,
            "file_size_bytes": file_size,
        }

    # -- maintenance --------------------------------------------------------

    def clear(self) -> bool:
        """Truncate the log file. Returns True on success."""
        try:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.truncate(0)
            logger.info("Log file cleared")
            metrics.inc("logs.cleared")
            return True
        except OSError as e:
            logger.error("Failed to clear log file: %s", e)
            return False

    def export_logs(self, path: str, n: int = None) -> int:
        """Copy current log content to *path*. Returns number of lines written.

        If *n* is provided only the last *n* lines are exported.
        """
        lines = self._read_lines()
        if n is not None:
            lines = lines[-n:]

        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        except OSError:
            pass

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            logger.info("Exported %d log lines to %s", len(lines), path)
            metrics.inc("logs.exported")
            return len(lines)
        except OSError as e:
            logger.error("Failed to export logs: %s", e)
            return 0

    def get_log_files(self) -> list:
        """Return a list of all log files (main + rotated backups)."""
        files = []
        log_dir = os.path.dirname(LOG_PATH)
        base_name = os.path.basename(LOG_PATH)

        for entry in sorted(os.listdir(log_dir)):
            if entry == base_name or entry.startswith(base_name + "."):
                full = os.path.join(log_dir, entry)
                if os.path.isfile(full):
                    files.append(full)

        return files
