#!/usr/bin/env python3
"""
migrate_to_v2.py — Migrationsskript: Projektdateien von Schema v1 auf v2.

Verwendung:
    python migrate_to_v2.py --dry-run             # Vorschau, keine Änderungen
    python migrate_to_v2.py                       # Echte Migration mit Backup
    python migrate_to_v2.py --projects-dir PATH   # Anderes Verzeichnis
    python migrate_to_v2.py --check               # Nur Statusbericht ausgeben

Sicherheitsgarantien:
- Idempotent: bereits migrierte Dateien (schema_version=2) werden übersprungen.
- Backup: Jede v1-Datei wird vor der Migration als <name>.v1.bak gesichert.
- Rollback: Backup-Dateien bleiben erhalten; kein automatisches Löschen.
- Kollisionserkennung: Wenn zwei verschiedene v1-Dateien dieselbe project_id
  erzeugen würden (z.B. wegen identischer URL), wird die Migration für die
  betroffenen Dateien abgebrochen und ein Fehler gemeldet.
- Kein Merge: Unterschiedliche Projekte werden NIEMALS zusammengeführt.
  Jede Datei bleibt eine eigene Datei mit eigener project_id.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from project_record import ProjectRecord, CURRENT_SCHEMA_VERSION


# ──────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ──────────────────────────────────────────────────────────────────────────────

def _parse_frontmatter(content: str) -> Tuple[Dict, str]:
    """Trennt YAML-Frontmatter vom Body. Gibt ({}, content) zurück wenn kein FM."""
    if not content.startswith("---"):
        return {}, content

    lines = content.split("\n")
    end_idx = -1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx == -1:
        return {}, content

    fm_text = "\n".join(lines[1:end_idx])
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}

    body = "\n".join(lines[end_idx + 1:]) if end_idx + 1 < len(lines) else ""
    return fm, body


def _write_file(path: Path, record: ProjectRecord, body: str) -> None:
    """Schreibt Frontmatter + Body zurück in die Datei."""
    path.write_text(record.to_frontmatter_text() + body, encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# Kollisionscheck
# ──────────────────────────────────────────────────────────────────────────────

def check_id_collisions(
    project_files: List[Path],
) -> Dict[str, List[Path]]:
    """
    Prüft ob zwei verschiedene Dateien dieselbe project_id erzeugen würden.

    Sicherheitsregel: Kollisionen = Datenfehler, kein automatischer Merge.
    Gibt ein Dict zurück: {project_id: [datei1, datei2, ...]} für alle Konflikte.
    Nur Einträge mit mehr als einer Datei sind Kollisionen.
    """
    id_to_files: Dict[str, List[Path]] = defaultdict(list)

    for path in project_files:
        content = path.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(content)
        version = int(fm.get("schema_version", 1))

        if version >= CURRENT_SCHEMA_VERSION:
            # Bereits migriert — project_id ist bereits final
            pid = fm.get("project_id", "")
        else:
            # v1: ID würde aus URL oder Titel abgeleitet
            record = ProjectRecord.from_frontmatter_dict(fm)
            pid = record.project_id

        if pid:
            id_to_files[pid].append(path)

    return {pid: files for pid, files in id_to_files.items() if len(files) > 1}


# ──────────────────────────────────────────────────────────────────────────────
# Hauptlogik
# ──────────────────────────────────────────────────────────────────────────────

def migrate_file(
    path: Path,
    dry_run: bool = True,
    verbose: bool = True,
) -> Optional[str]:
    """
    Migriert eine einzelne Projektdatei von v1 auf v2.

    Gibt None zurück wenn alles OK, sonst eine Fehlermeldung.
    """
    content = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(content)

    version = int(fm.get("schema_version", 1))

    # Bereits migriert → überspringen
    if version >= CURRENT_SCHEMA_VERSION:
        if verbose:
            print(f"  ⏭  {path.name}: bereits v{version}, übersprungen")
        return None

    # ProjectRecord aus v1-Dict erzeugen (konvertiert automatisch)
    record = ProjectRecord.from_frontmatter_dict(fm)

    if verbose:
        print(f"  📄 {path.name}")
        print(f"     project_id : {record.project_id}")
        print(f"     state      : {record.state}")
        if record.search_groups:
            print(f"     groups     : {record.search_groups}")
        if record.sources:
            print(f"     sources    : {len(record.sources)} Quelle(n)")

    if dry_run:
        if verbose:
            print(f"     [dry-run]  keine Änderungen")
        return None

    # Backup
    backup_path = path.with_suffix(f".v1.bak")
    if backup_path.exists():
        # Zweites Backup mit Timestamp
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        backup_path = path.with_suffix(f".v1.{ts}.bak")

    shutil.copy2(str(path), str(backup_path))
    if verbose:
        print(f"     backup     : {backup_path.name}")

    # Datei schreiben
    _write_file(path, record, body)
    if verbose:
        print(f"     ✅  migriert auf Schema v{CURRENT_SCHEMA_VERSION}")

    return None


def run_migration(
    projects_dir: str = "projects",
    dry_run: bool = True,
    check_only: bool = False,
    verbose: bool = True,
) -> int:
    """
    Führt die Migration für alle .md-Dateien im projects_dir durch.

    Gibt 0 zurück bei Erfolg, 1 bei Fehlern/Kollisionen.
    """
    base = Path(projects_dir)
    if not base.exists():
        print(f"❌  Verzeichnis nicht gefunden: {base}")
        return 1

    md_files = sorted(base.glob("*.md"))
    if not md_files:
        print(f"ℹ️  Keine .md-Dateien in {base} gefunden.")
        return 0

    # ── Statusbericht ──────────────────────────────────────────────────────
    v1_files = []
    v2_files = []
    error_files = []

    for path in md_files:
        try:
            content = path.read_text(encoding="utf-8")
            fm, _ = _parse_frontmatter(content)
            version = int(fm.get("schema_version", 1))
            if version >= CURRENT_SCHEMA_VERSION:
                v2_files.append(path)
            else:
                v1_files.append(path)
        except Exception as e:
            error_files.append((path, str(e)))

    print(f"\n📊  Projektdateien in '{base}':")
    print(f"    Schema v{CURRENT_SCHEMA_VERSION} (aktuell) : {len(v2_files)}")
    print(f"    Schema v1 (zu migrieren) : {len(v1_files)}")
    if error_files:
        print(f"    Lesefehler               : {len(error_files)}")
        for p, err in error_files:
            print(f"      ⚠️  {p.name}: {err}")

    if check_only:
        return 0 if not error_files else 1

    if not v1_files:
        print("\n✅  Nichts zu migrieren.")
        return 0

    # ── Kollisionscheck VOR jeder Änderung ────────────────────────────────
    print(f"\n🔍  Kollisionscheck für {len(v1_files)} v1-Datei(en) ...")
    collisions = check_id_collisions(v1_files)

    if collisions:
        print(f"\n❌  KOLLISIONEN GEFUNDEN — Migration abgebrochen!")
        print(f"    Folgende Dateien würden dieselbe project_id erhalten.")
        print(f"    Das deutet auf doppelte Einträge hin und muss manuell")
        print(f"    bereinigt werden. Kein automatischer Merge!")
        print()
        for pid, files in collisions.items():
            print(f"    project_id: {pid}")
            for f in files:
                print(f"      - {f.name}")
        print()
        return 1

    print(f"    ✅  Keine Kollisionen")

    # ── Migration ──────────────────────────────────────────────────────────
    mode = "[DRY-RUN]" if dry_run else "[MIGRATION]"
    print(f"\n{mode}  Migriere {len(v1_files)} Datei(en) ...\n")

    errors = []
    migrated = 0

    for path in v1_files:
        try:
            err = migrate_file(path, dry_run=dry_run, verbose=verbose)
            if err:
                errors.append((path, err))
            elif not dry_run:
                migrated += 1
        except Exception as e:
            errors.append((path, str(e)))
            if verbose:
                print(f"  ❌  {path.name}: {e}")

    # ── Abschlussbericht ───────────────────────────────────────────────────
    print()
    if dry_run:
        print(f"[DRY-RUN] Würde {len(v1_files)} Datei(en) migrieren.")
        print("  Führe ohne --dry-run aus, um die Migration durchzuführen.")
    else:
        print(f"✅  {migrated} Datei(en) migriert.")
        if len(v1_files) - migrated > 0:
            print(f"⚠️  {len(v1_files) - migrated} Datei(en) nicht migriert.")

    if errors:
        print(f"\n❌  {len(errors)} Fehler:")
        for path, err in errors:
            print(f"    {path.name}: {err}")
        return 1

    return 0


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migriert Projektdateien von Schema v1 auf v2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--projects-dir",
        default="projects",
        help="Verzeichnis mit Projektdateien (Standard: projects/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Vorschau ohne Änderungen (Standard: aktiv wenn kein --run angegeben)",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        default=False,
        help="Führt die Migration wirklich durch (ohne --dry-run)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Nur Statusbericht ausgeben, keine Migration",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Weniger Ausgabe",
    )

    args = parser.parse_args()

    # Standardmäßig dry-run, außer --run explizit gesetzt
    dry_run = not args.run

    sys.exit(run_migration(
        projects_dir=args.projects_dir,
        dry_run=dry_run,
        check_only=args.check,
        verbose=not args.quiet,
    ))


if __name__ == "__main__":
    main()
