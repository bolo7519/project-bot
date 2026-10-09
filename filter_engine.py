"""
filter_engine.py — Konfigurierbare Hard-Filter für Freelance-Projekte (Phase 3).

Filterentscheidungen:
- Alle konfigurierten Kriterien werden einzeln geprüft.
- Unbekannte Werte (fehlende Felder, nicht auswertbarer Text) führen NICHT zur
  Ablehnung — „im Zweifel für das Projekt".
- Jede Entscheidung und ihr Grund werden transparent gespeichert.

YAML-Konfiguration je Suchgruppe (search_groups.<id>.filters):

    filters:
      # Arbeitsmodell: Remote / Hybrid / Vor Ort / Beliebig
      work_mode:
        allowed: [remote, hybrid]      # Nur diese Werte zulassen
        reject_if_unknown: false       # Standard: false → unknown → pass

      # Vertragstyp: freelance / anue / festanstellung / beliebig
      contract_type:
        allowed: [freelance]
        reject_if_unknown: false

      # Stundensatz / Tagessatz in EUR
      rate:
        min_hourly: 80                 # Mindest-Stundensatz (optional)
        max_hourly: 200                # Maximal-Stundensatz (optional)
        min_daily: 600                 # Mindest-Tagessatz (optional)
        max_daily: 1600                # Maximal-Tagessatz (optional)
        reject_if_unknown: false       # Standard: false

      # Projektsprachen (de / en / nl)
      language:
        allowed: [de, en]
        reject_if_unknown: false

      # Aktualität: nicht älter als N Tage
      max_age_days: 30

      # Ausschlussbegriffe (Regex oder Plaintext, case-insensitive)
      exclude_terms:
        - "Festanstellung"
        - "ANÜ"
        - "Arbeitnehmerüberlassung"
        - "Vollzeitstelle"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Ergebnis einer einzelnen Filterprüfung
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FilterCheckResult:
    """Ergebnis eines einzelnen Filter-Checks."""
    criterion: str          # z.B. "work_mode", "contract_type", "rate_hourly"
    passed: bool            # True = Projekt passiert diesen Check
    reason: str             # Menschlich lesbarer Grund
    value_found: Any = None # Tatsächlicher Wert im Projekt (None = nicht gefunden)
    was_unknown: bool = False  # True = Wert war nicht bestimmbar


# ──────────────────────────────────────────────────────────────────────────────
# Gesamtergebnis der Filterprüfung
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FilterResult:
    """Gesamtergebnis der Filterprüfung für ein Projekt in einer Suchgruppe."""
    search_group_id: str
    passed: bool                            # True = Projekt passiert alle Filter
    checks: List[FilterCheckResult] = field(default_factory=list)

    @property
    def failed_checks(self) -> List[FilterCheckResult]:
        return [c for c in self.checks if not c.passed]

    @property
    def unknown_checks(self) -> List[FilterCheckResult]:
        return [c for c in self.checks if c.was_unknown]

    def to_dict(self) -> Dict[str, Any]:
        """Serialisierung für Frontmatter-Speicherung."""
        return {
            "search_group_id": self.search_group_id,
            "passed": self.passed,
            "failed_criteria": [c.criterion for c in self.failed_checks],
            "unknown_criteria": [c.criterion for c in self.unknown_checks],
            "checks": [
                {
                    "criterion": c.criterion,
                    "passed": c.passed,
                    "reason": c.reason,
                    "value_found": c.value_found,
                    "was_unknown": c.was_unknown,
                }
                for c in self.checks
            ],
        }


# ──────────────────────────────────────────────────────────────────────────────
# Filterkonfiguration
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkModeFilterConfig:
    allowed: List[str] = field(default_factory=list)   # leer = kein Filter
    reject_if_unknown: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkModeFilterConfig":
        return cls(
            allowed=[str(v).lower() for v in d.get("allowed", [])],
            reject_if_unknown=bool(d.get("reject_if_unknown", False)),
        )


@dataclass
class ContractTypeFilterConfig:
    allowed: List[str] = field(default_factory=list)
    reject_if_unknown: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ContractTypeFilterConfig":
        return cls(
            allowed=[str(v).lower() for v in d.get("allowed", [])],
            reject_if_unknown=bool(d.get("reject_if_unknown", False)),
        )


@dataclass
class RateFilterConfig:
    min_hourly: Optional[float] = None
    max_hourly: Optional[float] = None
    min_daily: Optional[float] = None
    max_daily: Optional[float] = None
    reject_if_unknown: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RateFilterConfig":
        def _float(key: str) -> Optional[float]:
            v = d.get(key)
            return float(v) if v is not None else None

        return cls(
            min_hourly=_float("min_hourly"),
            max_hourly=_float("max_hourly"),
            min_daily=_float("min_daily"),
            max_daily=_float("max_daily"),
            reject_if_unknown=bool(d.get("reject_if_unknown", False)),
        )

    @property
    def is_active(self) -> bool:
        return any(
            v is not None
            for v in (self.min_hourly, self.max_hourly, self.min_daily, self.max_daily)
        )


@dataclass
class LanguageFilterConfig:
    allowed: List[str] = field(default_factory=list)   # ["de", "en", "nl"]
    reject_if_unknown: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LanguageFilterConfig":
        return cls(
            allowed=[str(v).lower() for v in d.get("allowed", [])],
            reject_if_unknown=bool(d.get("reject_if_unknown", False)),
        )


@dataclass
class FilterConfig:
    """
    Vollständige Filterkonfiguration für eine Suchgruppe.
    Leere / fehlende Konfiguration = kein Filter aktiv.
    """
    work_mode: Optional[WorkModeFilterConfig] = None
    contract_type: Optional[ContractTypeFilterConfig] = None
    rate: Optional[RateFilterConfig] = None
    language: Optional[LanguageFilterConfig] = None
    max_age_days: Optional[int] = None
    exclude_terms: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FilterConfig":
        wm_d = d.get("work_mode")
        ct_d = d.get("contract_type")
        rate_d = d.get("rate")
        lang_d = d.get("language")
        return cls(
            work_mode=WorkModeFilterConfig.from_dict(wm_d) if wm_d else None,
            contract_type=ContractTypeFilterConfig.from_dict(ct_d) if ct_d else None,
            rate=RateFilterConfig.from_dict(rate_d) if rate_d else None,
            language=LanguageFilterConfig.from_dict(lang_d) if lang_d else None,
            max_age_days=int(d["max_age_days"]) if d.get("max_age_days") is not None else None,
            exclude_terms=list(d.get("exclude_terms", [])),
        )

    @classmethod
    def empty(cls) -> "FilterConfig":
        """Leere Konfiguration — alle Filter inaktiv."""
        return cls()


# ──────────────────────────────────────────────────────────────────────────────
# Textnormalisierung
# ──────────────────────────────────────────────────────────────────────────────

# Mapping bekannter Synonyme → kanonische Werte
_WORK_MODE_SYNONYMS: Dict[str, str] = {
    # Remote
    "remote": "remote", "100% remote": "remote", "vollständig remote": "remote",
    "home office": "remote", "homeoffice": "remote", "voll remote": "remote",
    "fully remote": "remote",
    # Hybrid
    "hybrid": "hybrid", "teilweise vor ort": "hybrid", "partially onsite": "hybrid",
    "hybrid (remote / vor ort)": "hybrid", "remote/hybrid": "hybrid",
    # Vor Ort
    "vor ort": "onsite", "onsite": "onsite", "on-site": "onsite",
    "präsenz": "onsite", "in house": "onsite",
}

_CONTRACT_TYPE_SYNONYMS: Dict[str, str] = {
    # Freelance
    "freelance": "freelance", "freiberuflich": "freelance",
    "selbständig": "freelance", "projektbasis": "freelance",
    "projekt": "freelance",
    # ANÜ / Arbeitnehmerüberlassung
    "anü": "anue", "arbeitnehmerüberlassung": "anue",
    "zeitarbeit": "anue", "leiharbeit": "anue", "arbeitnehmerüberlassung (anü)": "anue",
    # Festanstellung
    "festanstellung": "permanent", "fest": "permanent",
    "vollzeit": "permanent", "vollzeitstelle": "permanent",
    "unbefristet": "permanent", "angestelltenverhältnis": "permanent",
    "full-time employee": "permanent", "fte": "permanent",
}

_LANGUAGE_PATTERNS: Dict[str, re.Pattern] = {
    "de": re.compile(
        r"\b(deutsch|german|de\b|auf deutsch|deutschkenntnisse|deutschsprachig|"
        r"german\s+language|sprache:\s*deutsch)",
        re.IGNORECASE,
    ),
    "en": re.compile(
        r"\b(english|englisch|en\b|englischkenntnisse|englischsprachig|"
        r"english\s+language|sprache:\s*englisch)",
        re.IGNORECASE,
    ),
    "nl": re.compile(
        r"\b(dutch|niederländisch|nl\b|vlaams|dutch\s+language|sprache:\s*niederländisch)",
        re.IGNORECASE,
    ),
}

# Stundensatz / Tagessatz — Erkennung aus Text
_RATE_HOURLY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:€|EUR|eur)?\s*/?\s*(?:std\.?|stunde|h\b|hour)",
    re.IGNORECASE,
)
_RATE_DAILY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:€|EUR|eur)?\s*/?\s*(?:tag|day|pt\b|pd\b|pro tag|per day)",
    re.IGNORECASE,
)

# Tagessatz aus "xxx €/Tag" oder "Tag: xxx €"
_RATE_DAILY_RE2 = re.compile(
    r"(?:tagessatz|daily rate|rate)\D{0,10}(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

_HOURLY_RE2 = re.compile(
    r"(?:stundensatz|hourly rate|stundenlohn)\D{0,10}(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)


def _normalize_float(s: str) -> float:
    """'1.200,50' oder '1200.50' → 1200.5"""
    s = s.replace(".", "").replace(",", ".")
    return float(s)


def _extract_rates(text: str) -> Dict[str, Optional[float]]:
    """Versucht Stunden- und Tagessatz aus Freitext zu extrahieren."""
    hourly: Optional[float] = None
    daily: Optional[float] = None

    for m in _HOURLY_RE2.finditer(text):
        try:
            hourly = _normalize_float(m.group(1))
            break
        except ValueError:
            pass

    if hourly is None:
        for m in _RATE_HOURLY_RE.finditer(text):
            try:
                hourly = _normalize_float(m.group(1))
                break
            except ValueError:
                pass

    for m in _RATE_DAILY_RE2.finditer(text):
        try:
            daily = _normalize_float(m.group(1))
            break
        except ValueError:
            pass

    if daily is None:
        for m in _RATE_DAILY_RE.finditer(text):
            try:
                daily = _normalize_float(m.group(1))
                break
            except ValueError:
                pass

    return {"hourly": hourly, "daily": daily}


def _normalize_work_mode(text: str) -> Optional[str]:
    """Versucht den Arbeitsort-Typ aus Text zu bestimmen."""
    lower = text.lower()
    # Exakt-Match
    if lower in _WORK_MODE_SYNONYMS:
        return _WORK_MODE_SYNONYMS[lower]
    # Teilstring-Match
    for synonym, canonical in _WORK_MODE_SYNONYMS.items():
        if synonym in lower:
            return canonical
    return None


def _normalize_contract_type(text: str) -> Optional[str]:
    """Versucht den Vertragstyp aus Text zu bestimmen."""
    lower = text.lower()
    if lower in _CONTRACT_TYPE_SYNONYMS:
        return _CONTRACT_TYPE_SYNONYMS[lower]
    for synonym, canonical in _CONTRACT_TYPE_SYNONYMS.items():
        if synonym in lower:
            return canonical
    return None


def _detect_languages(text: str) -> List[str]:
    """Erkennt explizit genannte Sprachen im Text."""
    found = []
    for lang, pattern in _LANGUAGE_PATTERNS.items():
        if pattern.search(text):
            found.append(lang)
    return found


# ──────────────────────────────────────────────────────────────────────────────
# Filter-Engine
# ──────────────────────────────────────────────────────────────────────────────

class FilterEngine:
    """
    Wendet konfigurierbare Hard-Filter auf ein Projekt an.

    Die Engine arbeitet rein auf Text/Metadaten; sie ruft keine externen APIs auf
    und ist vollständig kostenlos.

    Designprinzip: Unbekannte Werte → pass (kein Verlust von Projekten durch
    fehlende Daten). Das Verhalten kann per reject_if_unknown pro Filter
    überschrieben werden.
    """

    def __init__(self, config: FilterConfig) -> None:
        self._cfg = config

    @classmethod
    def from_search_group_config(cls, group_cfg: Any) -> "FilterEngine":
        """
        Erzeugt eine FilterEngine aus einer SearchGroupConfig-Instanz.

        Erwartet optionales Attribut 'filters' (FilterConfig) oder
        liest es aus group_cfg.filters_dict.
        """
        if hasattr(group_cfg, "filters") and isinstance(group_cfg.filters, FilterConfig):
            return cls(group_cfg.filters)
        if hasattr(group_cfg, "filters_dict") and group_cfg.filters_dict:
            return cls(FilterConfig.from_dict(group_cfg.filters_dict))
        return cls(FilterConfig.empty())

    def apply(self, project_data: Dict[str, Any], search_group_id: str) -> FilterResult:
        """
        Wendet alle konfigurierten Filter auf die Projektdaten an.

        Args:
            project_data: Dict mit Feldern des Projekts. Erwartete Keys:
                - title (str)
                - description / summary / body (str)  — werden zu 'text' vereint
                - work_mode (str, optional)
                - contract_type (str, optional)
                - hourly_rate / daily_rate (float, optional)
                - language (str, optional)
                - published_date / discovered_at (str ISO-8601, optional)
            search_group_id: ID der prüfenden Suchgruppe (für Logging/Ergebnis).

        Returns:
            FilterResult mit passed=True wenn alle aktiven Filter bestehen.
        """
        cfg = self._cfg
        checks: List[FilterCheckResult] = []

        # Volltext aus verfügbaren Feldern zusammenstellen
        full_text = " ".join(filter(None, [
            str(project_data.get("title") or ""),
            str(project_data.get("description") or ""),
            str(project_data.get("summary") or ""),
            str(project_data.get("body") or ""),
            str(project_data.get("content") or ""),
        ]))

        # ── 1. Ausschlussbegriffe ───────────────────────────────────────────
        if cfg.exclude_terms:
            checks.append(self._check_exclude_terms(full_text))

        # ── 2. Vertragstyp ──────────────────────────────────────────────────
        if cfg.contract_type and cfg.contract_type.allowed:
            checks.append(self._check_contract_type(project_data, full_text, cfg.contract_type))

        # ── 3. Arbeitsmodell ────────────────────────────────────────────────
        if cfg.work_mode and cfg.work_mode.allowed:
            checks.append(self._check_work_mode(project_data, full_text, cfg.work_mode))

        # ── 4. Stundensatz / Tagessatz ──────────────────────────────────────
        if cfg.rate and cfg.rate.is_active:
            checks.append(self._check_rate(project_data, full_text, cfg.rate))

        # ── 5. Projektsprache ───────────────────────────────────────────────
        if cfg.language and cfg.language.allowed:
            checks.append(self._check_language(project_data, full_text, cfg.language))

        # ── 6. Aktualität ───────────────────────────────────────────────────
        if cfg.max_age_days is not None:
            checks.append(self._check_age(project_data, cfg.max_age_days))

        # Gesamtergebnis: alle Checks müssen bestehen
        overall_passed = all(c.passed for c in checks)

        result = FilterResult(
            search_group_id=search_group_id,
            passed=overall_passed,
            checks=checks,
        )

        logger.debug(
            "Filter result",
            extra={
                "search_group_id": search_group_id,
                "passed": overall_passed,
                "failed": [c.criterion for c in result.failed_checks],
                "unknown": [c.criterion for c in result.unknown_checks],
            },
        )
        return result

    # ── Einzelne Filter-Checks ─────────────────────────────────────────────────

    def _check_exclude_terms(self, text: str) -> FilterCheckResult:
        for term in self._cfg.exclude_terms:
            try:
                pattern = re.compile(term, re.IGNORECASE)
            except re.error:
                # Kein gültiges Regex → Plaintext-Suche
                pattern = re.compile(re.escape(term), re.IGNORECASE)

            if pattern.search(text):
                return FilterCheckResult(
                    criterion="exclude_terms",
                    passed=False,
                    reason=f"Ausschlussbegriff gefunden: '{term}'",
                    value_found=term,
                    was_unknown=False,
                )
        return FilterCheckResult(
            criterion="exclude_terms",
            passed=True,
            reason="Kein Ausschlussbegriff gefunden",
        )

    def _check_work_mode(
        self, data: Dict, text: str, cfg: WorkModeFilterConfig
    ) -> FilterCheckResult:
        # Explizites Feld bevorzugen, sonst Text-Erkennung
        raw = str(data.get("work_mode") or "").strip()
        normalized = _normalize_work_mode(raw) if raw else _normalize_work_mode(text)

        if normalized is None:
            return FilterCheckResult(
                criterion="work_mode",
                passed=not cfg.reject_if_unknown,
                reason=(
                    "Arbeitsmodell nicht bestimmbar — "
                    + ("abgelehnt (reject_if_unknown=true)" if cfg.reject_if_unknown
                       else "akzeptiert (Standardverhalten: unbekannt → pass)")
                ),
                was_unknown=True,
            )

        allowed = cfg.allowed
        passed = normalized in allowed
        return FilterCheckResult(
            criterion="work_mode",
            passed=passed,
            reason=(
                f"Arbeitsmodell '{normalized}' ist"
                + (" erlaubt" if passed else f" nicht erlaubt (erlaubt: {allowed})")
            ),
            value_found=normalized,
        )

    def _check_contract_type(
        self, data: Dict, text: str, cfg: ContractTypeFilterConfig
    ) -> FilterCheckResult:
        raw = str(data.get("contract_type") or "").strip()
        normalized = _normalize_contract_type(raw) if raw else _normalize_contract_type(text)

        if normalized is None:
            return FilterCheckResult(
                criterion="contract_type",
                passed=not cfg.reject_if_unknown,
                reason=(
                    "Vertragstyp nicht bestimmbar — "
                    + ("abgelehnt" if cfg.reject_if_unknown else "akzeptiert (unbekannt → pass)")
                ),
                was_unknown=True,
            )

        passed = normalized in cfg.allowed
        return FilterCheckResult(
            criterion="contract_type",
            passed=passed,
            reason=(
                f"Vertragstyp '{normalized}' ist"
                + (" erlaubt" if passed else f" nicht erlaubt (erlaubt: {cfg.allowed})")
            ),
            value_found=normalized,
        )

    def _check_rate(
        self, data: Dict, text: str, cfg: RateFilterConfig
    ) -> FilterCheckResult:
        # Explizite Felder bevorzugen
        hourly: Optional[float] = None
        daily: Optional[float] = None

        if data.get("hourly_rate") is not None:
            try:
                hourly = float(data["hourly_rate"])
            except (ValueError, TypeError):
                pass

        if data.get("daily_rate") is not None:
            try:
                daily = float(data["daily_rate"])
            except (ValueError, TypeError):
                pass

        # Text-Extraktion als Fallback
        if hourly is None and daily is None:
            extracted = _extract_rates(text)
            hourly = extracted["hourly"]
            daily = extracted["daily"]

        if hourly is None and daily is None:
            return FilterCheckResult(
                criterion="rate",
                passed=not cfg.reject_if_unknown,
                reason=(
                    "Stunden-/Tagessatz nicht angegeben — "
                    + ("abgelehnt" if cfg.reject_if_unknown else "akzeptiert (unbekannt → pass)")
                ),
                was_unknown=True,
            )

        # Prüfung anhand verfügbarer Werte
        failures = []

        if hourly is not None:
            if cfg.min_hourly is not None and hourly < cfg.min_hourly:
                failures.append(f"Stundensatz {hourly}€ < Minimum {cfg.min_hourly}€")
            if cfg.max_hourly is not None and hourly > cfg.max_hourly:
                failures.append(f"Stundensatz {hourly}€ > Maximum {cfg.max_hourly}€")

        if daily is not None:
            if cfg.min_daily is not None and daily < cfg.min_daily:
                failures.append(f"Tagessatz {daily}€ < Minimum {cfg.min_daily}€")
            if cfg.max_daily is not None and daily > cfg.max_daily:
                failures.append(f"Tagessatz {daily}€ > Maximum {cfg.max_daily}€")

        passed = len(failures) == 0
        return FilterCheckResult(
            criterion="rate",
            passed=passed,
            reason=(
                "; ".join(failures) if failures
                else f"Satz akzeptiert (Std: {hourly}€, Tag: {daily}€)"
            ),
            value_found={"hourly": hourly, "daily": daily},
        )

    def _check_language(
        self, data: Dict, text: str, cfg: LanguageFilterConfig
    ) -> FilterCheckResult:
        # Explizites Feld bevorzugen
        explicit = str(data.get("language") or "").strip().lower()
        if explicit:
            detected = [explicit]
        else:
            detected = _detect_languages(text)

        if not detected:
            return FilterCheckResult(
                criterion="language",
                passed=not cfg.reject_if_unknown,
                reason=(
                    "Projektsprache nicht erkennbar — "
                    + ("abgelehnt" if cfg.reject_if_unknown else "akzeptiert (unbekannt → pass)")
                ),
                was_unknown=True,
            )

        allowed = set(cfg.allowed)
        overlap = [lang for lang in detected if lang in allowed]
        passed = len(overlap) > 0
        return FilterCheckResult(
            criterion="language",
            passed=passed,
            reason=(
                f"Sprache(n) {detected} "
                + (f"enthält erlaubte: {overlap}" if passed
                   else f"nicht in erlaubten Sprachen: {list(allowed)}")
            ),
            value_found=detected,
        )

    def _check_age(self, data: Dict, max_age_days: int) -> FilterCheckResult:
        # Verschiedene Datumsfelder probieren
        for date_field in ("published_date", "discovered_at", "scraped_date", "created_at"):
            raw = data.get(date_field)
            if not raw:
                continue
            try:
                # ISO-8601 mit oder ohne Zeitzone
                ts = str(raw).rstrip("Z")
                if "T" in ts:
                    dt = datetime.fromisoformat(ts)
                else:
                    dt = datetime.fromisoformat(ts + "T00:00:00")

                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                age = datetime.now(timezone.utc) - dt
                cutoff = timedelta(days=max_age_days)

                passed = age <= cutoff
                return FilterCheckResult(
                    criterion="max_age_days",
                    passed=passed,
                    reason=(
                        f"Alter {age.days} Tage"
                        + (" ≤ " if passed else " > ")
                        + f"Limit {max_age_days} Tage (Quelle: {date_field})"
                    ),
                    value_found=age.days,
                )
            except (ValueError, TypeError):
                continue

        # Kein Datum gefunden → pass
        return FilterCheckResult(
            criterion="max_age_days",
            passed=True,
            reason="Veröffentlichungsdatum nicht bestimmbar — akzeptiert (unbekannt → pass)",
            was_unknown=True,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Hilfsfunktion: FilterConfig aus SearchGroupConfig laden
# ──────────────────────────────────────────────────────────────────────────────

def load_filter_config_for_group(config: Dict[str, Any], group_id: str) -> FilterConfig:
    """
    Liest die Filter-Konfiguration für eine Suchgruppe aus dem Haupt-Config-Dict.

    Args:
        config: Vollständiges config.yaml als Dict.
        group_id: ID der Suchgruppe.

    Returns:
        FilterConfig (leer wenn keine Filter konfiguriert).
    """
    groups = config.get("search_groups", {})
    group = groups.get(group_id, {})
    filters_d = group.get("filters", {})
    if not filters_d:
        return FilterConfig.empty()
    return FilterConfig.from_dict(filters_d)


def filter_config_from_search_group(search_group_config: Any) -> FilterConfig:
    """
    Liest die Filter-Konfiguration direkt aus einem SearchGroupConfig-Objekt.

    Args:
        search_group_config: SearchGroupConfig-Instanz (Phase 2/3).
            Das `.filters`-Attribut ist ein rohes Dict oder None.

    Returns:
        FilterConfig (leer wenn keine Filter konfiguriert).
    """
    filters_d = getattr(search_group_config, "filters", None)
    if not filters_d:
        return FilterConfig.empty()
    return FilterConfig.from_dict(filters_d)
