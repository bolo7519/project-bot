"""
pre_scorer.py — Kostenlose TF-IDF-basierte Vorbewertung für Freelance-Projekte (Phase 3).

Die Vorbewertung:
- Ist vollständig lokal und kostenlos (kein LLM-Aufruf, keine API).
- Arbeitet auf Deutsch und Englisch.
- Verarbeitet deutsche Umlaute, Ligatur ß und IT-Abkürzungen korrekt.
- Bewertet jedes Projekt gegen alle vier Kompetenzprofile.
- Gibt je Profil einen Score (0.0–1.0) und die wichtigsten Treffer zurück.
- Nimmt der späteren KI-Bewertung (Phase 4) NICHT vorweg — keine Empfehlung,
  kein Accept/Reject, keine Bewerbungstexte.

Architektur:
  PreScorer
  ├── _load_profile_keywords()  — liest Markdown-Profile aus competency_profiles/
  ├── _tokenize()               — Normalisierung, Umlaut-Expansion, Abkürzungen
  ├── _build_tfidf()            — TF-IDF-Vektoren der Profile (Corpus = Profile)
  └── score_project()           — Cosine-Ähnlichkeit Projekt ↔ alle Profile
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Pfad zu den Kompetenzprofilen ────────────────────────────────────────────

_DEFAULT_PROFILES_DIR = Path(__file__).parent / "competency_profiles"

# Profil-ID → Dateiname (ohne .md)
PROFILE_IDS = [
    "crm_sales_automation",
    "ai_business_process_integration",
    "it_infrastructure_security",
    "power_bi_sharepoint",
]


# ──────────────────────────────────────────────────────────────────────────────
# Textnormalisierung
# ──────────────────────────────────────────────────────────────────────────────

# Umlaut-Normalisierungen (beide Richtungen für Suche)
_UMLAUT_MAP = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "ae", "Ö": "oe", "Ü": "ue",
}

# Bekannte IT-Abkürzungen, die NICHT gesplittet werden sollen
_PROTECTED_TOKENS = frozenset({
    # Microsoft / Cloud
    "m365", "o365", "azure", "aws", "gcp", "ad", "aad", "entra",
    # BI / Data
    "bi", "etl", "elt", "sql", "dwh", "kpi", "erp", "crm",
    # Security
    "iam", "siem", "soc", "vpn", "isms", "gdpr", "dsgvo", "nis2",
    "bsi",
    # DevOps / Infra
    "ci", "cd", "cicd", "iac", "k8s", "api", "rest", "graphql",
    # Automation
    "rpa", "bpa", "bpm", "llm", "ai", "ml",
    # M365
    "spfx", "spfx",
    # Raten-Einheiten (sollen nicht als Keywords landen)
    # (werden beim Tokenizing herausgefiltert)
})

_STOPWORDS_DE = frozenset({
    "und", "oder", "aber", "für", "mit", "von", "auf", "in", "an",
    "zu", "bei", "aus", "nach", "über", "unter", "vor", "auch",
    "ist", "sind", "wird", "werden", "haben", "hat", "sein",
    "nicht", "wie", "als", "das", "die", "der", "den", "dem",
    "ein", "eine", "einen", "einem", "einer", "eines",
    "des", "im", "am", "wir", "sie", "er", "es", "ich",
    "kann", "kann", "dieser", "diese", "dieses",
    "suchen", "sucht", "gesucht", "project", "projekt",
    "freelance", "freelancer", "freiberuflich",
    "placeholder", "eintragen", "ergänzen", "nutzer",
})

_STOPWORDS_EN = frozenset({
    "the", "a", "an", "and", "or", "but", "for", "with", "of",
    "on", "in", "at", "to", "by", "from", "as", "is", "are",
    "was", "were", "be", "been", "have", "has", "had",
    "not", "this", "that", "these", "those", "it", "its",
    "we", "they", "you", "he", "she", "our", "your",
    "will", "would", "can", "could", "should", "may", "might",
})

_STOPWORDS = _STOPWORDS_DE | _STOPWORDS_EN

# Minimale Token-Länge
_MIN_TOKEN_LEN = 2


def _expand_umlauts(text: str) -> str:
    """Expandiert Umlaute zu ASCII-Äquivalenten für einheitlichen Vergleich."""
    for umlaut, replacement in _UMLAUT_MAP.items():
        text = text.replace(umlaut, replacement)
    return text


def _tokenize(text: str) -> List[str]:
    """
    Normalisiert und tokenisiert einen Text für TF-IDF.

    Schritte:
    1. Lowercase
    2. Umlaut-Expansion (ä→ae, ö→oe, ü→ue, ß→ss)
    3. Tokenisierung an Wortgrenzen (Bindestrich als Separator oder Teil)
    4. Stopword-Entfernung
    5. Mindestlänge

    Geschützte Tokens (IT-Abkürzungen) werden nicht gesplittet.
    """
    lower = text.lower()
    # Erst expandieren, dann tokenisieren
    expanded = _expand_umlauts(lower)

    # Tokens: Wörter mit optionalen Bindestrich-Verbindungen
    # Trennzeichen: Leerzeichen, Schrägstrich, Klammern, Komma, Semikolon,
    # Punkt (außer in Abkürzungen), Zeilenumbrüche
    raw_tokens = re.split(r"[\s/\\()\[\]{},;:\n\r\t]+", expanded)

    tokens = []
    for tok in raw_tokens:
        # Führende/nachfolgende Sonderzeichen entfernen
        tok = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", tok)
        if not tok:
            continue

        # Geschützte Tokens direkt übernehmen
        if tok in _PROTECTED_TOKENS:
            tokens.append(tok)
            continue

        # Bindestrich-Verbindungen aufspalten (z.B. "iso-27001" → ["iso", "27001"])
        sub = re.split(r"-+", tok)
        for s in sub:
            s = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", s)
            if not s or len(s) < _MIN_TOKEN_LEN:
                continue
            if s in _STOPWORDS:
                continue
            tokens.append(s)

    return tokens


# ──────────────────────────────────────────────────────────────────────────────
# TF-IDF
# ──────────────────────────────────────────────────────────────────────────────

def _term_freq(tokens: List[str]) -> Dict[str, float]:
    """Berechnet Term-Frequency (normalisiert auf Dokumentlänge)."""
    if not tokens:
        return {}
    count: Dict[str, int] = {}
    for t in tokens:
        count[t] = count.get(t, 0) + 1
    n = len(tokens)
    return {t: c / n for t, c in count.items()}


def _build_idf(documents: List[List[str]]) -> Dict[str, float]:
    """
    Berechnet Inverse Document Frequency über den Corpus (= Kompetenzprofile).

    IDF(t) = log((N + 1) / (df(t) + 1)) + 1   [smoothed]
    """
    n = len(documents)
    df: Dict[str, int] = {}
    for doc in documents:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1

    idf: Dict[str, float] = {}
    for term, freq in df.items():
        idf[term] = math.log((n + 1) / (freq + 1)) + 1
    return idf


def _tfidf_vector(tokens: List[str], idf: Dict[str, float]) -> Dict[str, float]:
    """Berechnet den TF-IDF-Vektor für ein Dokument."""
    tf = _term_freq(tokens)
    return {t: tf_val * idf.get(t, 1.0) for t, tf_val in tf.items()}


def _cosine_similarity(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    """Berechnet Cosine-Ähnlichkeit zweier TF-IDF-Vektoren."""
    if not v1 or not v2:
        return 0.0

    dot = sum(v1.get(t, 0.0) * v2.get(t, 0.0) for t in v2)
    norm1 = math.sqrt(sum(x * x for x in v1.values()))
    norm2 = math.sqrt(sum(x * x for x in v2.values()))

    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


# ──────────────────────────────────────────────────────────────────────────────
# Ergebnis-Typen
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ProfileScore:
    """Bewertung eines Projekts gegen ein einzelnes Kompetenzprofil."""
    profile_id: str
    score: float                           # 0.0 – 1.0 (Cosine-Ähnlichkeit)
    top_matches: List[str] = field(default_factory=list)   # wichtigste Übereinstimmungen

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "score": round(self.score, 4),
            "top_matches": self.top_matches,
        }


@dataclass
class PreScoreResult:
    """Vorbewertung eines Projekts gegen alle vier Kompetenzprofile."""
    profiles: List[ProfileScore] = field(default_factory=list)
    best_profile: Optional[str] = None     # Profile-ID mit höchstem Score
    best_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_profile": self.best_profile,
            "best_score": round(self.best_score, 4),
            "profiles": [p.to_dict() for p in self.profiles],
        }

    def score_for(self, profile_id: str) -> float:
        for p in self.profiles:
            if p.profile_id == profile_id:
                return p.score
        return 0.0


# ──────────────────────────────────────────────────────────────────────────────
# PreScorer
# ──────────────────────────────────────────────────────────────────────────────

class PreScorer:
    """
    Kostenlose TF-IDF-Vorbewertung.

    Usage:
        scorer = PreScorer()                         # lädt Profile aus competency_profiles/
        result = scorer.score_project(project_data)  # Dict mit title, description, …
    """

    def __init__(
        self,
        profiles_dir: Optional[Path] = None,
        profile_ids: Optional[List[str]] = None,
    ) -> None:
        """
        Args:
            profiles_dir: Verzeichnis mit Kompetenzprofilen (*.md).
                          Default: <Modulverzeichnis>/competency_profiles/
            profile_ids: Liste der zu ladenden Profil-IDs.
                         Default: alle vier Standard-Profile.
        """
        self._profiles_dir = profiles_dir or _DEFAULT_PROFILES_DIR
        self._profile_ids = profile_ids or PROFILE_IDS

        # Profile laden und TF-IDF aufbauen
        self._profile_tokens: Dict[str, List[str]] = {}
        self._idf: Dict[str, float] = {}
        self._profile_vectors: Dict[str, Dict[str, float]] = {}

        self._load_and_build()

    # ── Initialisierung ────────────────────────────────────────────────────────

    def _load_and_build(self) -> None:
        """Lädt alle Profile und baut TF-IDF-Corpus auf."""
        docs: List[List[str]] = []
        loaded_ids: List[str] = []

        for pid in self._profile_ids:
            path = self._profiles_dir / f"{pid}.md"
            if not path.exists():
                logger.warning("Kompetenzprofil nicht gefunden: %s", path)
                continue

            text = path.read_text(encoding="utf-8")
            # Kommentare und Platzhalter-Marker aus dem Markdown entfernen
            text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
            text = re.sub(r"\[PLACEHOLDER[^\]]*\]", " ", text)
            text = re.sub(r"^#{1,6}\s+", " ", text, flags=re.MULTILINE)

            tokens = _tokenize(text)
            self._profile_tokens[pid] = tokens
            docs.append(tokens)
            loaded_ids.append(pid)

        if not docs:
            logger.warning("Keine Kompetenzprofile geladen — Pre-Scoring deaktiviert")
            return

        # IDF über Profil-Corpus aufbauen
        self._idf = _build_idf(docs)

        # TF-IDF-Vektoren für alle Profile
        for pid, tokens in self._profile_tokens.items():
            self._profile_vectors[pid] = _tfidf_vector(tokens, self._idf)

        logger.info("PreScorer: %d Profile geladen (%s)", len(loaded_ids), ", ".join(loaded_ids))

    # ── Öffentliche API ────────────────────────────────────────────────────────

    def score_project(
        self,
        project_data: Dict[str, Any],
        top_n: int = 10,
    ) -> PreScoreResult:
        """
        Bewertet ein Projekt gegen alle geladenen Kompetenzprofile.

        Args:
            project_data: Dict mit Projektfeldern. Genutzete Keys:
                - title (str)
                - description / summary / body / content (str)
            top_n: Anzahl der wichtigsten Keyword-Matches pro Profil.

        Returns:
            PreScoreResult mit Score (0.0–1.0) und Top-Matches pro Profil.
            Score 0.0 bei leerem Text oder fehlenden Profilen.
        """
        if not self._profile_vectors:
            # Keine Profile → Ergebnis mit Null-Scores
            return PreScoreResult(
                profiles=[ProfileScore(pid, 0.0) for pid in self._profile_ids],
                best_profile=None,
                best_score=0.0,
            )

        # Projekttext zusammenstellen
        full_text = " ".join(filter(None, [
            str(project_data.get("title") or ""),
            str(project_data.get("description") or ""),
            str(project_data.get("summary") or ""),
            str(project_data.get("body") or ""),
            str(project_data.get("content") or ""),
        ]))

        project_tokens = _tokenize(full_text)
        if not project_tokens:
            return PreScoreResult(
                profiles=[ProfileScore(pid, 0.0) for pid in self._profile_vectors],
                best_profile=None,
                best_score=0.0,
            )

        project_vector = _tfidf_vector(project_tokens, self._idf)

        profile_scores: List[ProfileScore] = []
        for pid, profile_vector in self._profile_vectors.items():
            sim = _cosine_similarity(project_vector, profile_vector)

            # Top-N gemeinsame Terme (nach Produkt der TF-IDF-Gewichte)
            shared = {
                t: project_vector[t] * profile_vector[t]
                for t in project_vector
                if t in profile_vector
            }
            top_matches = sorted(shared, key=shared.get, reverse=True)[:top_n]

            profile_scores.append(ProfileScore(
                profile_id=pid,
                score=round(sim, 4),
                top_matches=top_matches,
            ))

        # Bestes Profil
        best = max(profile_scores, key=lambda p: p.score)

        return PreScoreResult(
            profiles=profile_scores,
            best_profile=best.profile_id if best.score > 0 else None,
            best_score=best.score,
        )

    def score_text(self, text: str) -> PreScoreResult:
        """Bewertet einen Freitext direkt (Hilfs-API für Tests)."""
        return self.score_project({"description": text})

    @property
    def loaded_profile_ids(self) -> List[str]:
        """Gibt die IDs der tatsächlich geladenen Profile zurück."""
        return list(self._profile_vectors.keys())
