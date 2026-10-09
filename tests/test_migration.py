"""
Tests für migrate_to_v2.py — Migrationsskript (Phase 1).

Geprüft:
- Dry-run ändert keine Dateien
- Migration schreibt Schema v2
- Idempotenz: bereits migrierte Dateien werden übersprungen
- Backup wird angelegt
- Kollisionserkennung: zwei Dateien mit gleicher URL → Abbruch
- Kein automatischer Merge unterschiedlicher Projekte
- Rollback möglich (Backup bleibt erhalten)
"""

import shutil
import pytest
from pathlib import Path

from migrate_to_v2 import run_migration, check_id_collisions
from project_record import CURRENT_SCHEMA_VERSION


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

V1_CONTENT = """\
---
title: "Test Projekt v1"
company: "Acme GmbH"
location: "Remote"
provider: "freelancermap"
provider_url: "https://www.freelancermap.de/projekt/11111"
state: scraped
created_at: "2026-10-01T10:00:00"
updated_at: "2026-10-01T10:00:00"
---

## Projektbeschreibung

Testprojekt für Migrationstests.
"""

V1_CONTENT_B = """\
---
title: "Anderes Projekt v1"
company: "Beta Corp"
location: "München"
provider: "gulp"
provider_url: "https://www.gulp.de/projekt/22222"
state: accepted
created_at: "2026-10-02T08:00:00"
---

## Projektbeschreibung

Ein anderes Projekt mit anderer URL.
"""

V2_CONTENT = """\
---
schema_version: 2
project_id: url-aabbcc112233
title: "Bereits migriert"
company: "Migrated Corp"
state: scraped
---

## Body
"""

COLLISION_A = """\
---
title: "Doppeltes Projekt A"
provider: "freelancermap"
provider_url: "https://www.freelancermap.de/projekt/99999"
state: scraped
created_at: "2026-10-01T10:00:00"
---
"""

COLLISION_B = """\
---
title: "Doppeltes Projekt B (gleiche URL!)"
provider: "gulp"
provider_url: "https://www.freelancermap.de/projekt/99999"
state: accepted
created_at: "2026-10-01T11:00:00"
---
"""


@pytest.fixture
def projects_dir(tmp_path):
    """Leeres Projektverzeichnis."""
    d = tmp_path / "projects"
    d.mkdir()
    return d


def write_project(projects_dir, name, content):
    path = projects_dir / name
    path.write_text(content, encoding="utf-8")
    return path


# ──────────────────────────────────────────────────────────────────────────────
# Dry-run
# ──────────────────────────────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_does_not_modify_file(self, projects_dir):
        """Dry-run ändert keine Datei auf Disk."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        original = path.read_text(encoding="utf-8")

        rc = run_migration(str(projects_dir), dry_run=True, verbose=False)
        assert rc == 0
        assert path.read_text(encoding="utf-8") == original

    def test_dry_run_creates_no_backup(self, projects_dir):
        """Dry-run legt kein Backup an."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        run_migration(str(projects_dir), dry_run=True, verbose=False)
        backups = list(projects_dir.glob("*.bak"))
        assert len(backups) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Echte Migration
# ──────────────────────────────────────────────────────────────────────────────

class TestRealMigration:
    def test_migration_writes_schema_v2(self, projects_dir):
        """Nach Migration enthält die Datei schema_version: 2."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        rc = run_migration(str(projects_dir), dry_run=False, verbose=False)
        assert rc == 0
        content = path.read_text(encoding="utf-8")
        assert f"schema_version: {CURRENT_SCHEMA_VERSION}" in content

    def test_migration_writes_project_id(self, projects_dir):
        """Nach Migration enthält die Datei eine project_id."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        run_migration(str(projects_dir), dry_run=False, verbose=False)
        content = path.read_text(encoding="utf-8")
        assert "project_id: url-" in content

    def test_migration_creates_backup(self, projects_dir):
        """Echte Migration legt ein .v1.bak-Backup an."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        original_content = path.read_text(encoding="utf-8")
        run_migration(str(projects_dir), dry_run=False, verbose=False)
        backups = list(projects_dir.glob("*.bak"))
        assert len(backups) == 1
        # Backup enthält den originalen v1-Inhalt
        assert backups[0].read_text(encoding="utf-8") == original_content

    def test_migration_preserves_body(self, projects_dir):
        """Body-Inhalt bleibt nach Migration unverändert."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        run_migration(str(projects_dir), dry_run=False, verbose=False)
        content = path.read_text(encoding="utf-8")
        assert "Testprojekt für Migrationstests." in content

    def test_migration_preserves_state(self, projects_dir):
        """State bleibt nach Migration erhalten."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        run_migration(str(projects_dir), dry_run=False, verbose=False)
        content = path.read_text(encoding="utf-8")
        assert "state: scraped" in content

    def test_migration_multiple_files(self, projects_dir):
        """Mehrere v1-Dateien werden alle migriert."""
        write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        write_project(projects_dir, "projekt_b.md", V1_CONTENT_B)
        rc = run_migration(str(projects_dir), dry_run=False, verbose=False)
        assert rc == 0
        for fname in ["projekt_a.md", "projekt_b.md"]:
            content = (projects_dir / fname).read_text(encoding="utf-8")
            assert f"schema_version: {CURRENT_SCHEMA_VERSION}" in content


# ──────────────────────────────────────────────────────────────────────────────
# Idempotenz
# ──────────────────────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_already_migrated_file_skipped(self, projects_dir):
        """v2-Datei wird bei nochmaligem Durchlauf übersprungen."""
        path = write_project(projects_dir, "already_v2.md", V2_CONTENT)
        original_content = path.read_text(encoding="utf-8")

        rc = run_migration(str(projects_dir), dry_run=False, verbose=False)
        assert rc == 0
        # Datei unverändert
        assert path.read_text(encoding="utf-8") == original_content
        # Kein Backup
        assert len(list(projects_dir.glob("*.bak"))) == 0

    def test_double_migration_is_safe(self, projects_dir):
        """Zweimalige Migration einer v1-Datei ist sicher."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)

        rc1 = run_migration(str(projects_dir), dry_run=False, verbose=False)
        content_after_first = path.read_text(encoding="utf-8")

        rc2 = run_migration(str(projects_dir), dry_run=False, verbose=False)
        content_after_second = path.read_text(encoding="utf-8")

        assert rc1 == 0
        assert rc2 == 0
        # Zweite Migration ändert nichts
        assert content_after_first == content_after_second


# ──────────────────────────────────────────────────────────────────────────────
# Kollisionserkennung
# ──────────────────────────────────────────────────────────────────────────────

class TestCollisionDetection:
    def test_no_collision_with_different_urls(self, projects_dir):
        """Verschiedene URLs → keine Kollision."""
        write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        write_project(projects_dir, "projekt_b.md", V1_CONTENT_B)
        files = list(projects_dir.glob("*.md"))
        collisions = check_id_collisions(files)
        assert len(collisions) == 0

    def test_collision_detected_with_same_url(self, projects_dir):
        """SICHERHEITSTEST: Gleiche URL in zwei Dateien → Kollision erkannt."""
        write_project(projects_dir, "collision_a.md", COLLISION_A)
        write_project(projects_dir, "collision_b.md", COLLISION_B)
        files = list(projects_dir.glob("*.md"))
        collisions = check_id_collisions(files)
        assert len(collisions) >= 1, (
            "Kollision mit gleicher URL wurde nicht erkannt! "
            "Migration würde Datenverlust riskieren."
        )

    def test_collision_aborts_migration(self, projects_dir):
        """Kollision → Migration wird mit Fehler-Exit abgebrochen."""
        write_project(projects_dir, "collision_a.md", COLLISION_A)
        write_project(projects_dir, "collision_b.md", COLLISION_B)

        rc = run_migration(str(projects_dir), dry_run=False, verbose=False)
        assert rc != 0, (
            "Migration hätte bei Kollision abbrechen müssen, lief aber durch!"
        )

    def test_collision_does_not_modify_files(self, projects_dir):
        """Bei Kollision werden KEINE Dateien verändert."""
        path_a = write_project(projects_dir, "collision_a.md", COLLISION_A)
        path_b = write_project(projects_dir, "collision_b.md", COLLISION_B)
        original_a = path_a.read_text(encoding="utf-8")
        original_b = path_b.read_text(encoding="utf-8")

        run_migration(str(projects_dir), dry_run=False, verbose=False)

        assert path_a.read_text(encoding="utf-8") == original_a
        assert path_b.read_text(encoding="utf-8") == original_b


# ──────────────────────────────────────────────────────────────────────────────
# Rollback (Backup-Verifikation)
# ──────────────────────────────────────────────────────────────────────────────

class TestRollback:
    def test_backup_contains_original_v1_content(self, projects_dir):
        """Das Backup enthält den originalen v1-Inhalt für manuellen Rollback."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        run_migration(str(projects_dir), dry_run=False, verbose=False)

        backups = list(projects_dir.glob("*.bak"))
        assert len(backups) == 1
        backup_content = backups[0].read_text(encoding="utf-8")
        assert "title: \"Test Projekt v1\"" in backup_content
        # Backup enthält KEIN schema_version 2
        assert "schema_version: 2" not in backup_content

    def test_backup_is_valid_v1_file(self, projects_dir):
        """Backup kann als gültige v1-Datei zurück-kopiert werden."""
        path = write_project(projects_dir, "projekt_a.md", V1_CONTENT)
        run_migration(str(projects_dir), dry_run=False, verbose=False)

        backups = list(projects_dir.glob("*.bak"))
        backup = backups[0]

        # Backup zurückkopieren
        restore_path = projects_dir / "restored.md"
        shutil.copy2(str(backup), str(restore_path))

        # Als Projekt lesbar
        from migrate_to_v2 import _parse_frontmatter
        content = restore_path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(content)
        assert fm.get("title") == "Test Projekt v1"
        assert int(fm.get("schema_version", 1)) == 1
