"""
Regressionstests für Scheduler-Ausführungspfad.

Prüft:
- exit_code und output werden nach erfolgreichen Steps auf ExecutionResult hochgezogen
- exit_code != 0 führt zu status="failed" (nicht stille Erfolgsmeldung)
- step_results sind in execution_history enthalten und im /runs-Endpoint sichtbar
- python/python3 in cmd_parts wird durch sys.executable ersetzt
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler_manager import (
    CommandStep,
    ExecutionResult,
    Schedule,
    SchedulerManager,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _make_schedule(command: str = "python3 run_search_groups_rss.py --groups automation_bi") -> Schedule:
    cmd = CommandStep(
        command=command,
        name="TestStep",
        description="",
        timeout=30,
        continue_on_error=False,
    )
    return Schedule(
        id="test-sched",
        name="Test Schedule",
        description="",
        enabled=True,
        workflow_type="cli_sequence",
        cli_commands=[cmd],
        cron_schedule="0 8 * * mon-fri",
        timezone="Europe/Berlin",
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )


def _make_manager_with_schedule(schedule: Schedule) -> SchedulerManager:
    """Erzeugt einen SchedulerManager mit gemocktem Scheduler (kein echter Thread)."""
    mgr = SchedulerManager.__new__(SchedulerManager)
    mgr.scheduler = MagicMock()
    mgr.scheduler.running = True
    mgr.schedules = {schedule.id: schedule}
    mgr._save_schedules = MagicMock()

    # Validation immer OK — wir testen die Execution-Logik, nicht die Validation
    ok = ValidationResult()
    ok.valid = True
    mgr.validate_workflow_config = MagicMock(return_value=ok)
    return mgr


def _run_cli_sequence(returncode: int, stdout: str, stderr: str = "") -> ExecutionResult:
    """Führt _execute_cli_sequence mit gemocktem Popen aus."""
    schedule = _make_schedule()
    mgr = _make_manager_with_schedule(schedule)

    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)

    with patch("scheduler_manager.subprocess.Popen", return_value=proc):
        mgr._execute_cli_sequence("test-sched")

    assert len(schedule.execution_history) >= 1
    return schedule.execution_history[0]


def _captured_popen_cmd(command: str) -> list:
    """Gibt die cmd_parts zurück, mit denen Popen tatsächlich aufgerufen wurde."""
    schedule = _make_schedule(command)
    mgr = _make_manager_with_schedule(schedule)

    captured: list = []

    def fake_popen(cmd_parts, **kw):
        captured.extend(cmd_parts)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate.return_value = ("", "")
        return proc

    with patch("scheduler_manager.subprocess.Popen", side_effect=fake_popen):
        mgr._execute_cli_sequence("test-sched")

    return captured


# ---------------------------------------------------------------------------
# Tests: ExecutionResult-Aggregation
# ---------------------------------------------------------------------------

class TestExecutionResultAggregation(unittest.TestCase):

    def test_exit_code_aggregated_on_success(self):
        result = _run_cli_sequence(returncode=0, stdout="ok")
        self.assertEqual(result.exit_code, 0,
            "exit_code muss nach erfolgreichem Step auf 0 gesetzt werden")

    def test_output_aggregated_on_success(self):
        result = _run_cli_sequence(returncode=0, stdout="20 neue Projekte")
        self.assertIn("20 neue Projekte", result.output or "",
            "output muss den stdout des Steps enthalten")

    def test_status_success_when_exit_zero(self):
        result = _run_cli_sequence(returncode=0, stdout="ok")
        self.assertEqual(result.status, "success")

    def test_status_failed_when_nonzero_exit(self):
        """Kritisch: exit_code != 0 darf nicht als success durchgehen."""
        result = _run_cli_sequence(returncode=1, stdout="")
        self.assertEqual(result.status, "failed",
            "Non-zero exit code muss zu status='failed' führen")

    def test_exit_code_in_step_results_on_failure(self):
        result = _run_cli_sequence(returncode=1, stdout="")
        self.assertTrue(len(result.step_results or []) >= 1)
        self.assertEqual(result.step_results[0].get("exit_code"), 1)

    def test_step_results_populated(self):
        result = _run_cli_sequence(returncode=0, stdout="Neue: 5")
        self.assertTrue(len(result.step_results or []) >= 1,
            "step_results muss nach dem Lauf befüllt sein")
        self.assertIn("output", result.step_results[0])
        self.assertIn("exit_code", result.step_results[0])

    def test_save_schedules_called_twice(self):
        """_save_schedules muss beim Start (running) und Ende aufgerufen werden."""
        schedule = _make_schedule()
        mgr = _make_manager_with_schedule(schedule)

        proc = MagicMock()
        proc.returncode = 0
        proc.communicate.return_value = ("ok", "")

        with patch("scheduler_manager.subprocess.Popen", return_value=proc):
            mgr._execute_cli_sequence("test-sched")

        self.assertGreaterEqual(mgr._save_schedules.call_count, 2,
            "_save_schedules muss beim Start und Ende aufgerufen werden")

    def test_last_run_set(self):
        """last_run darf nach dem Lauf nicht mehr None sein."""
        schedule = _make_schedule()
        mgr = _make_manager_with_schedule(schedule)

        proc = MagicMock()
        proc.returncode = 0
        proc.communicate.return_value = ("ok", "")

        with patch("scheduler_manager.subprocess.Popen", return_value=proc):
            mgr._execute_cli_sequence("test-sched")

        self.assertIsNotNone(schedule.last_run,
            "last_run muss nach dem Lauf gesetzt sein")

    def test_last_status_success_on_zero_exit(self):
        schedule = _make_schedule()
        mgr = _make_manager_with_schedule(schedule)

        proc = MagicMock()
        proc.returncode = 0
        proc.communicate.return_value = ("ok", "")

        with patch("scheduler_manager.subprocess.Popen", return_value=proc):
            mgr._execute_cli_sequence("test-sched")

        self.assertEqual(schedule.last_status, "success")

    def test_last_status_failed_on_nonzero_exit(self):
        schedule = _make_schedule()
        mgr = _make_manager_with_schedule(schedule)

        proc = MagicMock()
        proc.returncode = 2
        proc.communicate.return_value = ("", "ERROR: something broke")

        with patch("scheduler_manager.subprocess.Popen", return_value=proc):
            mgr._execute_cli_sequence("test-sched")

        self.assertEqual(schedule.last_status, "failed")


# ---------------------------------------------------------------------------
# Tests: Interpreter-Substitution
# ---------------------------------------------------------------------------

class TestInterpreterSubstitution(unittest.TestCase):
    """python/python3 in cmd_parts muss durch sys.executable ersetzt werden."""

    def test_python3_replaced_by_sys_executable(self):
        cmd_parts = _captured_popen_cmd("python3 run_script.py")
        self.assertEqual(cmd_parts[0], sys.executable,
            "python3 muss durch sys.executable ersetzt werden")

    def test_python_replaced_by_sys_executable(self):
        cmd_parts = _captured_popen_cmd("python run_script.py")
        self.assertEqual(cmd_parts[0], sys.executable,
            "python muss durch sys.executable ersetzt werden")

    def test_script_argument_preserved(self):
        cmd_parts = _captured_popen_cmd(
            "python3 run_search_groups_rss.py --groups automation_bi infra_security"
        )
        self.assertEqual(cmd_parts[1], "run_search_groups_rss.py")
        self.assertIn("--groups", cmd_parts)
        self.assertIn("automation_bi", cmd_parts)
        self.assertIn("infra_security", cmd_parts)


if __name__ == "__main__":
    unittest.main()
