"""
Persistent logging for multi-division system tests.

Writes human-readable and JSON reports under <DATA_DIR>/system_test_backup/<session_id>/
alongside the rollback snapshot.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from config import Config

logger = logging.getLogger(__name__)

LogLevel = Literal["info", "success", "warn", "error", "phase"]

BACKUP_ROOT = os.path.join(Config.DATA_DIR, "system_test_backup")
LATEST_REPORT_JSON = os.path.join(BACKUP_ROOT, "latest_report.json")
LATEST_REPORT_LOG = os.path.join(BACKUP_ROOT, "latest_report.log")


@dataclass
class LogEntry:
    timestamp: float
    level: LogLevel
    message: str
    division: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "iso": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "level": self.level,
            "division": self.division,
            "message": self.message,
        }

    def format_line(self) -> str:
        ts = datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        prefix = f"[{self.division}] " if self.division else ""
        tag = self.level.upper().ljust(5)
        return f"{ts}  {tag}  {prefix}{self.message}"


@dataclass
class SystemTestLogger:
    session_id: str
    session_dir: str
    admin_id: str
    divisions: list[str]
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    success: bool = False
    failure_reason: str | None = None
    rolled_back: bool = False
    _entries: list[LogEntry] = field(default_factory=list)
    _division_ok: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def for_session(
        cls,
        session_id: str,
        session_dir: str,
        admin_id: str,
        divisions: list[str],
    ) -> SystemTestLogger:
        os.makedirs(session_dir, exist_ok=True)
        return cls(
            session_id=session_id,
            session_dir=session_dir,
            admin_id=admin_id,
            divisions=divisions,
        )

    def log(
        self,
        message: str,
        *,
        level: LogLevel = "info",
        division: str | None = None,
    ) -> None:
        entry = LogEntry(
            timestamp=time.time(),
            level=level,
            message=message,
            division=division,
        )
        self._entries.append(entry)
        line = entry.format_line()
        if level == "error":
            logger.error("[SystemTestReport] %s", line)
        elif level == "warn":
            logger.warning("[SystemTestReport] %s", line)
        else:
            logger.info("[SystemTestReport] %s", line)
        self._append_to_log_file(line)

    def phase(self, message: str, *, division: str | None = None) -> None:
        self.log(message, level="phase", division=division)

    def success_msg(self, message: str, *, division: str | None = None) -> None:
        self.log(message, level="success", division=division)

    def warn(self, message: str, *, division: str | None = None) -> None:
        self.log(message, level="warn", division=division)

    def error(self, message: str, *, division: str | None = None) -> None:
        self.log(message, level="error", division=division)

    def mark_division_ok(self, division: str) -> None:
        self._division_ok[division] = True

    def mark_division_failed(self, division: str, exc: BaseException) -> None:
        self._division_ok[division] = False
        self.error(f"{division} failed: {exc}", division=division)

    @property
    def report_log_path(self) -> str:
        return os.path.join(self.session_dir, "report.log")

    @property
    def report_json_path(self) -> str:
        return os.path.join(self.session_dir, "report.json")

    def _append_to_log_file(self, line: str) -> None:
        try:
            with open(self.report_log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            logger.error("[SystemTestReport] Could not write log: %s", e)

    def finalize(
        self,
        *,
        success: bool,
        error: str | None = None,
        rolled_back: bool = False,
    ) -> dict[str, Any]:
        self.finished_at = time.time()
        self.success = success
        self.failure_reason = error
        self.rolled_back = rolled_back

        duration = (self.finished_at or time.time()) - self.started_at
        by_division: dict[str, list[dict]] = {d: [] for d in self.divisions}
        for entry in self._entries:
            key = entry.division or "_global"
            if key not in by_division:
                by_division[key] = []
            by_division[key].append(entry.to_dict())

        report: dict[str, Any] = {
            "session_id": self.session_id,
            "session_dir": self.session_dir,
            "admin_id": self.admin_id,
            "divisions": self.divisions,
            "started_at": self.started_at,
            "started_at_iso": datetime.fromtimestamp(
                self.started_at, tz=timezone.utc
            ).isoformat(),
            "finished_at": self.finished_at,
            "finished_at_iso": datetime.fromtimestamp(
                self.finished_at, tz=timezone.utc
            ).isoformat(),
            "duration_seconds": round(duration, 2),
            "success": success,
            "error": error,
            "rolled_back": rolled_back,
            "division_results": {
                d: self._division_ok.get(d, success and error is None)
                for d in self.divisions
            },
            "entries_by_scope": by_division,
            "entry_count": len(self._entries),
            "report_log": self.report_log_path,
            "report_json": self.report_json_path,
            "archive_log": self._archive_run_log(),
        }

        try:
            with open(self.report_json_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            with open(LATEST_REPORT_JSON, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            if os.path.isfile(self.report_log_path):
                with open(self.report_log_path, "r", encoding="utf-8") as src:
                    with open(LATEST_REPORT_LOG, "w", encoding="utf-8") as dst:
                        dst.write(src.read())
        except OSError as e:
            logger.error("[SystemTestReport] Could not write JSON report: %s", e)

        summary_lines = [
            "=" * 60,
            "ARMA DRAFT — SYSTEM TEST REPORT",
            "=" * 60,
            f"Session:    {self.session_id}",
            f"Result:     {'PASSED' if success else 'FAILED'}",
            f"Duration:   {duration:.1f}s",
            f"Rollback:   {'yes' if rolled_back else 'no'}",
        ]
        if error:
            summary_lines.append(f"Error:      {error}")
        summary_lines.append("")
        summary_lines.append("Division results:")
        for d in self.divisions:
            ok = report["division_results"].get(d, "?")
            summary_lines.append(f"  {d}: {'OK' if ok else 'FAIL'}")
        summary_lines.append("")
        summary_lines.append(f"Full log:   {self.report_log_path}")
        summary_lines.append(f"JSON:       {self.report_json_path}")
        summary_lines.append("=" * 60)

        header = "\n".join(summary_lines) + "\n"
        try:
            existing = ""
            if os.path.isfile(self.report_log_path):
                with open(self.report_log_path, "r", encoding="utf-8") as f:
                    existing = f.read()
            with open(self.report_log_path, "w", encoding="utf-8") as f:
                f.write(header + existing)
        except OSError:
            pass

        return report

    def _archive_run_log(self) -> str:
        """Copy this run's log next to the bot log, one file per attempt."""
        league = Path(Config.LEAGUE_DIR) if Config.LEAGUE_DIR else Path(".")
        dest_dir = league / "logs" / "system-tests"
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{self.session_id}.log"
            if os.path.isfile(self.report_log_path):
                shutil.copy2(self.report_log_path, dest)
            return str(dest)
        except OSError as e:
            logger.error("[SystemTestReport] Could not archive run log: %s", e)
            return ""

    @classmethod
    def load_latest_report(cls) -> dict[str, Any] | None:
        if not os.path.isfile(LATEST_REPORT_JSON):
            return None
        try:
            with open(LATEST_REPORT_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
