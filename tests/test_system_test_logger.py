"""Tests for system test report logging."""

import json
import os
import tempfile

from simulation.test_logger import SystemTestLogger


def test_finalize_writes_report_files():
    with tempfile.TemporaryDirectory() as tmp:
        session_id = "test123"
        session_dir = os.path.join(tmp, session_id)
        log = SystemTestLogger.for_session(
            session_id=session_id,
            session_dir=session_dir,
            admin_id="999",
            divisions=["Acuity", "Verity"],
        )
        log.phase("Phase 1 - start", division="Acuity")
        log.success_msg("Draft started", division="Acuity")
        log.error("Something failed", division="Verity")

        report = log.finalize(success=False, error="Verity failed", rolled_back=True)

        assert report["success"] is False
        assert report["error"] == "Verity failed"
        assert report["entry_count"] == 3
        assert os.path.isfile(os.path.join(session_dir, "report.log"))
        assert os.path.isfile(os.path.join(session_dir, "report.json"))

        with open(os.path.join(session_dir, "report.json"), encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["session_id"] == session_id
        assert "Acuity" in loaded["division_results"]
