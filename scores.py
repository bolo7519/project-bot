"""
scores.py — Score-Hilfsfunktionen für den Project Bot.

Dieses Modul ist absichtlich von main.py getrennt, damit Tests die Funktion
direkt importieren können ohne den kaputten Import-Baum von main.py
(LangChain-Kompatibilität, Phase 1) zu berühren.
"""

import re


def read_llm_score_from_file(project_file: str) -> int:
    """
    Read the LLM fit score from a project markdown file.

    Falls back to 0 if the file cannot be read or contains no score.
    The score is written into the Markdown body by evaluate_projects.py and
    is NOT stored in the YAML frontmatter, so we scan the body text.

    Args:
        project_file: Path to the project markdown file

    Returns:
        Integer score 0-100 (0 when not found)
    """
    try:
        with open(project_file, 'r', encoding='utf-8') as f:
            content = f.read()
        # Pattern used in server_enhanced.py / extract_latest_scores()
        match = re.search(r'\*\*LLM Score:\*\*\s*(\d+)%', content)
        if match:
            return int(match.group(1))
        # Fallback: pre-eval score if no LLM score present
        pre_match = re.search(r'\*\*Pre-Evaluation Score:\*\*\s*(\d+)%', content)
        if pre_match:
            return int(pre_match.group(1))
    except OSError:
        pass
    return 0
