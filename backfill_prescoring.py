#!/usr/bin/env python3
"""
backfill_prescoring.py — Einmaliges Pre-Scoring für vorhandene Projektdateien.

Liest alle *.md-Dateien in projects/, berechnet per TF-IDF (PreScorer) den
besten Score, und schreibt ihn als

    - **Score:** 73/100

in das Markdown, sodass server_enhanced.py ihn via Regex ausliest.

Sicherheit:
  - Keine LLM-Aufrufe, keine API-Schlüssel, keine Bewerbungen.
  - Trocken-Lauf (--dry-run) zeigt nur, was geschrieben würde.
  - Bestehende Score-Zeilen werden ersetzt (kein Duplikat).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Sicherstellen, dass das Projekt-Root im Pfad ist
sys.path.insert(0, str(Path(__file__).parent))

from pre_scorer import PreScorer


# ── Regex: bestehende Score-Zeile (alt oder schon gesetzt) ───────────────────
_SCORE_LINE_RE = re.compile(r"- \*\*Score:\*\*\s*\d+/100\n?", re.MULTILINE)

# ── Marker, nach dem die Score-Zeile eingefügt wird (falls noch keine da ist) ─
# Wir suchen nach der ersten Markdown-Überschrift oder fügen am Anfang an.
_SCORE_SECTION_RE = re.compile(r"(##\s+Vorbewertung.*?\n)", re.IGNORECASE)


def _write_score(content: str, score_100: int) -> str:
    """Gibt den Markdown-Inhalt zurück, in dem Score gesetzt/ersetzt ist."""
    score_line = f"- **Score:** {score_100}/100\n"

    if _SCORE_LINE_RE.search(content):
        # Ersetzen
        return _SCORE_LINE_RE.sub(score_line, content, count=1)

    # Kein Abschnitt "Vorbewertung" vorhanden → ans Ende anhängen
    if not content.endswith("\n"):
        content += "\n"
    content += f"\n## Vorbewertung\n\n{score_line}"
    return content


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Pre-Scoring für vorhandene Projekte")
    parser.add_argument(
        "--projects-dir",
        default="projects",
        help="Verzeichnis mit Projekt-Markdown-Dateien (default: projects)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur Scores berechnen, nichts schreiben",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Nur Projekte mit best_score >= X eintragen (0.0–1.0, default: 0.0)",
    )
    args = parser.parse_args()

    projects_dir = Path(args.projects_dir)
    if not projects_dir.exists():
        print(f"ERROR: Verzeichnis nicht gefunden: {projects_dir}", file=sys.stderr)
        sys.exit(1)

    md_files = sorted(projects_dir.glob("*.md"))
    if not md_files:
        print(f"Keine *.md-Dateien in {projects_dir} gefunden.")
        sys.exit(0)

    print(f"PreScorer wird initialisiert …")
    try:
        scorer = PreScorer()
    except Exception as exc:
        print(f"ERROR: PreScorer konnte nicht geladen werden: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Scoring {len(md_files)} Projektdateien …")

    stats = {"scored": 0, "skipped_low": 0, "skipped_existing": 0, "errors": 0}

    for md_path in md_files:
        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"  FEHLER beim Lesen {md_path.name}: {exc}")
            stats["errors"] += 1
            continue

        # Bereits gescoret → überspringen (--force würde das ändern)
        if _SCORE_LINE_RE.search(content):
            stats["skipped_existing"] += 1
            continue

        # Text für PreScorer extrahieren (title + body)
        title_match = re.search(r"^#\s+(.*)", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else md_path.stem

        project_data = {
            "title": title,
            "description": content,
        }

        try:
            result = scorer.score_project(project_data)
        except Exception as exc:
            print(f"  FEHLER beim Scoring {md_path.name}: {exc}")
            stats["errors"] += 1
            continue

        best_score = result.best_score or 0.0
        score_100 = round(best_score * 100)

        if best_score < args.min_score:
            stats["skipped_low"] += 1
            print(f"  SKIP  {md_path.name}  score={score_100}/100 (unter min-score {args.min_score*100:.0f})")
            continue

        status = "DRY-RUN" if args.dry_run else "OK"

        if not args.dry_run:
            new_content = _write_score(content, score_100)
            md_path.write_text(new_content, encoding="utf-8")

        best_profile = result.best_profile or "(keins)"
        top = result.profiles[0].top_matches[:3] if result.profiles else []
        print(
            f"  [{status}]  {md_path.name}  "
            f"score={score_100}/100  "
            f"best_profile={best_profile}  "
            f"top_matches={top}"
        )
        stats["scored"] += 1

    print()
    print("─" * 60)
    print(f"  Neu gescored      : {stats['scored']}")
    print(f"  Bereits vorhanden : {stats['skipped_existing']}")
    print(f"  Score < min-score : {stats['skipped_low']}")
    print(f"  Fehler            : {stats['errors']}")
    if args.dry_run:
        print("  (DRY-RUN — keine Dateien geändert)")


if __name__ == "__main__":
    main()
