#!/usr/bin/env python3
"""
run_search_groups_rss.py — RSS-Ingest über die Search-Group-Pipeline (Phase 2/3).

Verwendet run_rss_ingestion_for_search_groups() aus email_agent.py:
  - Deduplizierung via DedupeService
  - TF-IDF-Vorbewertung via PreScorer
  - Kein LLM, keine Bewerbungen, keine SMTP

Aufruf:
    python run_search_groups_rss.py [--config config.yaml] [--dry-run] [--groups automation_bi infra_security]

Sicherheitsnote:
  - Nur RSS, kein E-Mail-Abruf
  - Keine LLM-Aufrufe
  - Keine Bewerbungsgenerierung
  - Bindet ausschließlich lokal (kein Netzwerk-Binding)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from main import load_application_config
from email_agent import run_rss_ingestion_for_search_groups
from pre_scorer import PreScorer

# Regex: prüft ob eine Score-Zeile bereits im Body steht
_SCORE_LINE_RE = re.compile(r"- \*\*Score:\*\*\s*\d+/100", re.MULTILINE)


def _backfill_scores_for_new_files(output_dir: str, new_count: int) -> int:
    """
    Schreibt - **Score:** N/100 in neu angelegte Projektdateien, die
    noch keinen Score-Eintrag im Body haben.
    Gibt die Anzahl der beschriebenen Dateien zurück.
    """
    if new_count == 0:
        return 0

    projects_dir = Path(output_dir)
    md_files = sorted(projects_dir.glob("*.md"))
    if not md_files:
        return 0

    try:
        scorer = PreScorer()
    except Exception as exc:
        print(f"  ⚠️  PreScorer nicht verfügbar, Score-Backfill übersprungen: {exc}")
        return 0

    scored = 0
    for md_path in md_files:
        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception:
            continue

        if _SCORE_LINE_RE.search(content):
            continue  # Bereits gescoret

        title_match = re.search(r"^#\s+(.*)", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else md_path.stem

        try:
            result = scorer.score_project({"title": title, "description": content})
        except Exception:
            continue

        score_100 = round((result.best_score or 0.0) * 100)
        score_line = f"- **Score:** {score_100}/100\n"

        if not content.endswith("\n"):
            content += "\n"
        content += f"\n## Vorbewertung\n\n{score_line}"
        md_path.write_text(content, encoding="utf-8")
        scored += 1

    return scored


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RSS-Ingest für Search Groups (mit PreScoring)"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Pfad zur config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulation ohne Dateiänderungen",
    )
    parser.add_argument(
        "--groups",
        nargs="*",
        default=None,
        help="Nur diese Suchgruppen ausführen, z.B. --groups automation_bi infra_security",
    )
    parser.add_argument(
        "--output-dir",
        default="projects",
        help="Projektverzeichnis (default: projects)",
    )
    args = parser.parse_args()

    config = load_application_config(args.config)

    if "search_groups" not in config or not config["search_groups"]:
        print(
            "ERROR: Keine search_groups in config.yaml gefunden.\n"
            "Bitte search_groups_patch.yaml in config.yaml einfügen:\n"
            "  cat search_groups_patch.yaml >> config.yaml",
            file=sys.stderr,
        )
        sys.exit(1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    groups_hint = ", ".join(args.groups) if args.groups else "alle aktivierten"
    print(f"📰 RSS-Ingest (Search Groups) [{mode}] — Gruppen: {groups_hint}")

    summary = run_rss_ingestion_for_search_groups(
        config=config,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        group_ids=args.groups,
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    # Score-Backfill für neu angelegte Dateien (Body-Regex für server_enhanced.py)
    new_projects = summary['total_projects_saved']
    scored_count = 0
    if not args.dry_run and new_projects > 0:
        print(f"\n⚙️  Score-Backfill für {new_projects} neue Projekte …")
        scored_count = _backfill_scores_for_new_files(args.output_dir, new_projects)
        print(f"   → {scored_count} Dateien mit Score versehen")

    print()
    print("─" * 60)
    print(f"  Gruppen verarbeitet      : {summary['groups_processed']}")
    print(f"  RSS-Einträge gefunden    : {summary['total_entries_found']}")
    print(f"  Neue Projekte gespeichert: {new_projects}")
    print(f"  Duplikate übersprungen   : {summary['total_urls_skipped_dedupe']}")
    print(f"  Scores geschrieben       : {scored_count}")
    print(f"  Fehler                   : {summary['total_errors']}")
    if args.dry_run:
        print("  (DRY-RUN — keine Dateien geändert)")


if __name__ == "__main__":
    main()
