"""
Tests für Phase-0-Bugfix: doppelte run_rss_ingestion / run_full_workflow in email_agent.py

Prüft, dass die korrekten (zweiten) Definitionen aktiv sind, und verifiziert
das Verschwinden der toten Duplikate durch Inspektion des Quellcodes.
"""

import inspect
import re
import pytest
from pathlib import Path


EMAIL_AGENT_FILE = Path(__file__).parent.parent / "email_agent.py"


def _read_source() -> str:
    return EMAIL_AGENT_FILE.read_text(encoding="utf-8")


class TestNoDuplicateFunctions:
    def test_run_rss_ingestion_defined_once(self):
        """run_rss_ingestion darf nur einmal im Quellcode definiert sein."""
        src = _read_source()
        matches = re.findall(r'^def run_rss_ingestion\b', src, re.MULTILINE)
        assert len(matches) == 1, (
            f"run_rss_ingestion ist {len(matches)}x definiert – "
            "doppelte Definition muss entfernt werden (Phase-0-Fix)"
        )

    def test_run_full_workflow_defined_once(self):
        """run_full_workflow darf nur einmal im Quellcode definiert sein."""
        src = _read_source()
        matches = re.findall(r'^def run_full_workflow\b', src, re.MULTILINE)
        assert len(matches) == 1, (
            f"run_full_workflow ist {len(matches)}x definiert – "
            "doppelte Definition muss entfernt werden (Phase-0-Fix)"
        )

    def test_active_run_rss_ingestion_delegates_to_run_all_providers_rss(self):
        """Die aktive Version soll run_all_providers_rss für 'all' aufrufen."""
        import email_agent
        import inspect
        src = inspect.getsource(email_agent.run_rss_ingestion)
        assert "run_all_providers_rss" in src, (
            "Die aktive run_rss_ingestion delegiert nicht an run_all_providers_rss"
        )
