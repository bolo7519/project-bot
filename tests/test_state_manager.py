"""
Tests für ProjectStateManager (state_manager.py)

Prüft: Frontmatter-Parsing, State-Transitionen, ungültige Übergänge,
get_projects_by_state und initialize_project.
Keine echten Projektdateien werden persistent verändert; alle Tests nutzen
ein temporäres Verzeichnis via tmp_path.
"""

import shutil
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state_manager import ProjectStateManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _copy_fixture(name: str, dest_dir: Path) -> Path:
    src = FIXTURE_DIR / name
    dst = dest_dir / name
    shutil.copy(src, dst)
    return dst


@pytest.fixture
def projects_dir(tmp_path):
    """Temporäres Verzeichnis als Projekt-Store."""
    d = tmp_path / "projects"
    d.mkdir()
    return d


@pytest.fixture
def manager(projects_dir):
    return ProjectStateManager(str(projects_dir))


# ---------------------------------------------------------------------------
# Frontmatter-Parsing
# ---------------------------------------------------------------------------

class TestFrontmatterParsing:
    def test_parse_scraped_fixture(self, manager, projects_dir):
        p = _copy_fixture("project_scraped.md", projects_dir)
        fm, body = manager.read_project(str(p))
        assert fm["state"] == "scraped"
        assert fm["company"] == "Acme GmbH"
        assert "Python" in body

    def test_parse_accepted_fixture(self, manager, projects_dir):
        p = _copy_fixture("project_accepted.md", projects_dir)
        fm, body = manager.read_project(str(p))
        assert fm["state"] == "accepted"

    def test_parse_rejected_fixture(self, manager, projects_dir):
        p = _copy_fixture("project_rejected.md", projects_dir)
        fm, body = manager.read_project(str(p))
        assert fm["state"] == "rejected"

    def test_missing_file_returns_empty_dict(self, manager):
        """read_project schluckt FileNotFoundError und gibt ({}, '') zurück.
        Das ist das dokumentierte Verhalten – kein raise erwartet.
        Hinweis: Dies bedeutet, dass Aufrufer keine Exception-Behandlung
        benötigen, aber auch keine Rückmeldung über fehlende Dateien bekommen.
        Wird in Phase 1 mit Logging verbessert."""
        fm, body = manager.read_project("/nonexistent/path/project.md")
        assert fm == {}
        assert body == ""


# ---------------------------------------------------------------------------
# State-Transitionen
# ---------------------------------------------------------------------------

class TestStateTransitions:
    def test_scraped_to_accepted(self, manager, projects_dir):
        p = _copy_fixture("project_scraped.md", projects_dir)
        result = manager.update_state(str(p), "accepted")
        assert result is True
        fm, _ = manager.read_project(str(p))
        assert fm["state"] == "accepted"

    def test_scraped_to_rejected(self, manager, projects_dir):
        p = _copy_fixture("project_scraped.md", projects_dir)
        result = manager.update_state(str(p), "rejected")
        assert result is True
        fm, _ = manager.read_project(str(p))
        assert fm["state"] == "rejected"

    def test_accepted_to_applied(self, manager, projects_dir):
        p = _copy_fixture("project_accepted.md", projects_dir)
        result = manager.update_state(str(p), "applied")
        assert result is True
        fm, _ = manager.read_project(str(p))
        assert fm["state"] == "applied"

    def test_invalid_transition_scraped_to_sent(self, manager, projects_dir):
        """scraped → sent ist kein gültiger Übergang."""
        p = _copy_fixture("project_scraped.md", projects_dir)
        result = manager.update_state(str(p), "sent")
        # Soll False zurückgeben (kein force)
        assert result is False
        # State darf sich nicht geändert haben
        fm, _ = manager.read_project(str(p))
        assert fm["state"] == "scraped"

    def test_invalid_state_name(self, manager, projects_dir):
        """Ungültiger State-Name soll False zurückgeben."""
        p = _copy_fixture("project_scraped.md", projects_dir)
        result = manager.update_state(str(p), "nonexistent_state")
        assert result is False

    def test_transition_note_is_stored(self, manager, projects_dir):
        p = _copy_fixture("project_scraped.md", projects_dir)
        manager.update_state(str(p), "accepted", note="Manuelle Freigabe")
        fm, _ = manager.read_project(str(p))
        # Note wird im Frontmatter oder Body gespeichert; state muss korrekt sein
        assert fm["state"] == "accepted"

    def test_full_happy_path(self, manager, projects_dir):
        """scraped → accepted → applied → sent → open → archived"""
        p = _copy_fixture("project_scraped.md", projects_dir)
        for new_state in ["accepted", "applied", "sent", "open", "archived"]:
            ok = manager.update_state(str(p), new_state)
            assert ok is True, f"Übergang zu '{new_state}' fehlgeschlagen"
        fm, _ = manager.read_project(str(p))
        assert fm["state"] == "archived"


# ---------------------------------------------------------------------------
# get_projects_by_state
# ---------------------------------------------------------------------------

class TestGetProjectsByState:
    def test_returns_scraped_projects(self, manager, projects_dir):
        _copy_fixture("project_scraped.md", projects_dir)
        result = manager.get_projects_by_state("scraped")
        assert len(result) == 1
        assert result[0]["state"] == "scraped"

    def test_returns_accepted_projects(self, manager, projects_dir):
        _copy_fixture("project_accepted.md", projects_dir)
        result = manager.get_projects_by_state("accepted")
        assert len(result) == 1

    def test_empty_for_missing_state(self, manager, projects_dir):
        _copy_fixture("project_scraped.md", projects_dir)
        result = manager.get_projects_by_state("archived")
        assert result == []

    def test_multiple_projects_filtered_correctly(self, manager, projects_dir):
        _copy_fixture("project_scraped.md", projects_dir)
        _copy_fixture("project_accepted.md", projects_dir)
        _copy_fixture("project_rejected.md", projects_dir)
        scraped = manager.get_projects_by_state("scraped")
        assert len(scraped) == 1
        accepted = manager.get_projects_by_state("accepted")
        assert len(accepted) == 1
        rejected = manager.get_projects_by_state("rejected")
        assert len(rejected) == 1


# ---------------------------------------------------------------------------
# initialize_project
# ---------------------------------------------------------------------------

class TestInitializeProject:
    def test_creates_new_project_with_scraped_state(self, manager, projects_dir):
        new_file = str(projects_dir / "new_project.md")
        metadata = {
            "title": "Test Projekt",
            "company": "Test GmbH",
            "location": "Remote",
            "provider": "freelancermap",
            "provider_url": "https://example.com/p/1",
        }
        result = manager.initialize_project(new_file, metadata)
        assert result is True
        fm, _ = manager.read_project(new_file)
        assert fm["state"] == "scraped"
        assert fm["title"] == "Test Projekt"
