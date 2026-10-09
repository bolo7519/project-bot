"""
search_group_config.py — Konfigurationsmodell für Suchgruppen (Phase 2).

Datenstruktur im config.yaml:

    search_groups:
      automation_bi:
        display_name: "Automation & BI"
        priority: 1
        keywords: [...]
        providers:
          freelancermap:
            channels:
              rss:
                feed_urls: [...]
                max_age_days: 7
                limit: 20
              email:
                senders: [...]
                subject_patterns: [...]
                body_url_patterns: [...]
        schedule:
          rss_interval_minutes: 60
          email_interval_minutes: 30
      infra_security:
        ...

Designprinzip:
- Jede Suchgruppe hat eigene Provider-Konfiguration (Feed-URLs, Mail-Filter, etc.)
- Gemeinsame globale Provider-Defaults (channels.*) bleiben unverändert
- Erweiterbar: weitere Suchgruppen ohne Codeänderung hinzufügbar
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Versionierte Standardwerte für die fachlichen Einschlusskriterien je Suchgruppe
DEFAULT_TOPIC_FILTERS_PATH = Path(__file__).parent / "search_group_filters.yaml"

# FilterConfig is imported lazily to avoid circular dependencies;
# the TYPE_CHECKING guard keeps mypy happy without a runtime import loop.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from filter_engine import FilterConfig


# ──────────────────────────────────────────────────────────────────────────────
# Sub-Konfigurationen
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RSSChannelConfig:
    """RSS-Kanaleinstellungen für eine Suchgruppe + Provider-Kombination."""
    feed_urls: List[str]
    max_age_days: int = 7
    limit: int = 20
    url_exclude_patterns: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RSSChannelConfig":
        return cls(
            feed_urls=list(d.get("feed_urls", [])),
            max_age_days=int(d.get("max_age_days", 7)),
            limit=int(d.get("limit", 20)),
            url_exclude_patterns=list(d.get("url_exclude_patterns", [])),
        )

    def validate(self, group_id: str, provider_id: str) -> List[str]:
        errors = []
        if not self.feed_urls:
            errors.append(
                f"search_groups.{group_id}.providers.{provider_id}"
                f".channels.rss.feed_urls must not be empty"
            )
        if self.limit < 1:
            errors.append(
                f"search_groups.{group_id}.providers.{provider_id}"
                f".channels.rss.limit must be >= 1"
            )
        return errors


@dataclass
class EmailChannelConfig:
    """E-Mail-Kanaleinstellungen für eine Suchgruppe + Provider-Kombination."""
    senders: List[str]
    subject_patterns: List[str]
    body_url_patterns: List[str]
    url_exclude_patterns: List[str] = field(default_factory=list)
    max_urls_per_email: int = 50

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EmailChannelConfig":
        return cls(
            senders=list(d.get("senders", [])),
            subject_patterns=list(d.get("subject_patterns", [])),
            body_url_patterns=list(d.get("body_url_patterns", [])),
            url_exclude_patterns=list(d.get("url_exclude_patterns", [])),
            max_urls_per_email=int(d.get("max_urls_per_email", 50)),
        )

    def validate(self, group_id: str, provider_id: str) -> List[str]:
        errors = []
        if not self.senders:
            errors.append(
                f"search_groups.{group_id}.providers.{provider_id}"
                f".channels.email.senders must not be empty"
            )
        for pattern in self.subject_patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(
                    f"search_groups.{group_id}.providers.{provider_id}"
                    f".channels.email.subject_patterns: invalid regex '{pattern}': {e}"
                )
        for pattern in self.body_url_patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(
                    f"search_groups.{group_id}.providers.{provider_id}"
                    f".channels.email.body_url_patterns: invalid regex '{pattern}': {e}"
                )
        return errors


@dataclass
class ProviderChannels:
    """Kanaleinstellungen (RSS und/oder E-Mail) für einen Provider innerhalb einer Suchgruppe."""
    rss: Optional[RSSChannelConfig] = None
    email: Optional[EmailChannelConfig] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProviderChannels":
        rss_d = d.get("rss")
        email_d = d.get("email")
        return cls(
            rss=RSSChannelConfig.from_dict(rss_d) if rss_d else None,
            email=EmailChannelConfig.from_dict(email_d) if email_d else None,
        )

    def validate(self, group_id: str, provider_id: str) -> List[str]:
        errors = []
        if self.rss is None and self.email is None:
            errors.append(
                f"search_groups.{group_id}.providers.{provider_id}.channels:"
                f" at least one of 'rss' or 'email' must be configured"
            )
        if self.rss:
            errors.extend(self.rss.validate(group_id, provider_id))
        if self.email:
            errors.extend(self.email.validate(group_id, provider_id))
        return errors


@dataclass
class SearchGroupSchedule:
    """Zeitplan für eine Suchgruppe."""
    rss_interval_minutes: int = 60
    email_interval_minutes: int = 30

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SearchGroupSchedule":
        return cls(
            rss_interval_minutes=int(d.get("rss_interval_minutes", 60)),
            email_interval_minutes=int(d.get("email_interval_minutes", 30)),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Haupt-Datenklasse
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SearchGroupConfig:
    """
    Vollständige Konfiguration einer Suchgruppe.

    Instanzen werden von load_search_groups() aus config.yaml gelesen.
    """
    group_id: str
    display_name: str
    priority: int = 10
    keywords: List[str] = field(default_factory=list)
    # provider_id → ProviderChannels
    providers: Dict[str, ProviderChannels] = field(default_factory=dict)
    schedule: SearchGroupSchedule = field(default_factory=SearchGroupSchedule)
    enabled: bool = True
    # Phase 3: optionale Filterregeln; None = kein Filter (alles durchlassen)
    # Typ ist Dict[str, Any] zur Laufzeit (FilterConfig beim Import vermeiden)
    filters: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, group_id: str, d: Dict[str, Any]) -> "SearchGroupConfig":
        providers: Dict[str, ProviderChannels] = {}
        for pid, pcfg in d.get("providers", {}).items():
            channels_d = pcfg.get("channels", {})
            providers[pid] = ProviderChannels.from_dict(channels_d)

        schedule_d = d.get("schedule", {})
        return cls(
            group_id=group_id,
            display_name=str(d.get("display_name", group_id)),
            priority=int(d.get("priority", 10)),
            keywords=list(d.get("keywords", [])),
            providers=providers,
            schedule=SearchGroupSchedule.from_dict(schedule_d),
            enabled=bool(d.get("enabled", True)),
            filters=d.get("filters"),  # Raw dict — FilterEngine liest es selbst
        )

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.group_id:
            errors.append("search_groups entry is missing a group_id")
        if not self.providers:
            errors.append(
                f"search_groups.{self.group_id}.providers must have at least one provider"
            )
        for pid, pc in self.providers.items():
            errors.extend(pc.validate(self.group_id, pid))
        return errors

    def get_rss_providers(self) -> List[str]:
        """Provider-IDs, die für diese Suchgruppe einen RSS-Kanal haben."""
        return [pid for pid, pc in self.providers.items() if pc.rss is not None]

    def get_email_providers(self) -> List[str]:
        """Provider-IDs, die für diese Suchgruppe einen E-Mail-Kanal haben."""
        return [pid for pid, pc in self.providers.items() if pc.email is not None]


# ──────────────────────────────────────────────────────────────────────────────
# Loader
# ──────────────────────────────────────────────────────────────────────────────

class SearchGroupConfigError(ValueError):
    """Wird geworfen, wenn die search_groups-Konfiguration ungültig ist."""


def load_search_groups(config: Dict[str, Any]) -> Dict[str, SearchGroupConfig]:
    """
    Liest alle Suchgruppen aus dem übergebenen Konfigurations-Dict.

    Args:
        config: Das vollständige config.yaml als Dict (bereits geparst).

    Returns:
        Dict von group_id → SearchGroupConfig, sortiert nach priority (aufsteigend).

    Raises:
        SearchGroupConfigError: Wenn Pflichtfelder fehlen oder Regex-Patterns ungültig sind.
    """
    raw = config.get("search_groups", {})
    if not raw:
        return {}

    groups: Dict[str, SearchGroupConfig] = {}
    all_errors: List[str] = []

    for group_id, group_dict in raw.items():
        if not isinstance(group_dict, dict):
            all_errors.append(f"search_groups.{group_id} must be a mapping, got {type(group_dict)}")
            continue

        grp = SearchGroupConfig.from_dict(group_id, group_dict)
        errors = grp.validate()
        if errors:
            all_errors.extend(errors)
        else:
            groups[group_id] = grp

    if all_errors:
        raise SearchGroupConfigError(
            "Invalid search_groups configuration:\n" + "\n".join(f"  - {e}" for e in all_errors)
        )

    # Sortiert nach priority (niedrigere Zahl = höhere Priorität)
    return dict(sorted(groups.items(), key=lambda kv: kv[1].priority))


# ──────────────────────────────────────────────────────────────────────────────
# Fachliche Einschlusskriterien (Standardwerte aus search_group_filters.yaml)
# ──────────────────────────────────────────────────────────────────────────────

def load_default_topic_filters(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """
    Liest die versionierten fachlichen Einschlusskriterien je Suchgruppe.

    Returns:
        Dict group_id → {"include_terms": [...], "include_min_matches": int}.
        Leeres Dict, wenn die Datei fehlt oder nicht lesbar ist.
    """
    import yaml

    filters_path = Path(path) if path is not None else DEFAULT_TOPIC_FILTERS_PATH
    try:
        with open(filters_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(
            "Fachliche Standardfilter nicht lesbar: %s (%s)", filters_path, exc
        )
        return {}

    raw = data.get("search_group_filters") or {}
    return {gid: dict(v) for gid, v in raw.items() if isinstance(v, dict)}


def apply_default_topic_filters(
    config: Dict[str, Any],
    defaults_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Ergänzt fehlende fachliche Einschlusskriterien der Suchgruppen.

    Für jede Suchgruppe ohne eigenes ``filters.include_terms`` werden die
    Begriffe aus search_group_filters.yaml eingetragen. Eine in config.yaml
    gesetzte Liste (auch eine leere) hat Vorrang; alle übrigen Filter und
    Einstellungen der Gruppe bleiben unverändert.

    Returns:
        Kopie von ``config`` mit ergänzten Filtern (Original bleibt unverändert).
    """
    groups = config.get("search_groups")
    if not isinstance(groups, dict) or not groups:
        return config

    defaults = load_default_topic_filters(defaults_path)
    merged = dict(config)
    merged_groups = copy.deepcopy(groups)

    for group_id, group in merged_groups.items():
        if not isinstance(group, dict):
            continue
        filters = group.get("filters") or {}
        if "include_terms" not in filters:
            group_defaults = defaults.get(group_id) or {}
            if group_defaults.get("include_terms"):
                filters = dict(filters)
                filters["include_terms"] = list(group_defaults["include_terms"])
                filters.setdefault(
                    "include_min_matches", group_defaults.get("include_min_matches", 1)
                )
                group["filters"] = filters
        if not (group.get("filters") or {}).get("include_terms"):
            logger.warning(
                "Suchgruppe '%s' hat keine fachlichen Einschlusskriterien — "
                "jeder Feed-Eintrag gilt als Treffer dieser Gruppe",
                group_id,
            )

    merged["search_groups"] = merged_groups
    return merged
