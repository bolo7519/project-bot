# Phase 5: Dashboard-Architekturplan

## Ziel

Erweiterung des bestehenden Flask/Vue-Dashboards um LLM-Bewertungsdaten aus
Phase 4. Keine neue Architektur — maximale Wiederverwendung bestehender
Komponenten.

## Analyseergebnis der bestehenden Architektur

### Backend (`server_enhanced.py`)

| Bestandteil | Stand | Änderungsbedarf |
|---|---|---|
| `parse_project_file()` | Liest nur `pre_eval_score`, `llm_score` (aus Markdown-Text) | +LLM-Daten aus `extra`-Frontmatter-Feldern |
| `ProjectFilters` (Pydantic) | `search`, `statuses`, `companies`, `providers`, `channels`, Scores, Datum | +`search_groups`, `profile_id`, `priority_label`, `evaluation_status` |
| `get_projects_with_filters()` | Filtert nach obigen Feldern | +neue Filter anwenden |
| `get_dashboard_stats()` | Zählt Projekte nach Status | +LLM-Stats (bewertet, high-prio, ausstehend, Fehler) |
| CORS | `localhost:8002` only | unveränderlich |
| Authentifizierung | Keiner (TODO Phase 9) | unveränderlich; schreibende Endpunkte CSRF-Header |

**Neu benötigte Endpunkte:**

```
GET  /api/v1/llm/costs          # Budget-Verbrauch heute/Monat, Aufrufzähler
GET  /api/v1/llm/stats          # Bewertet/High-Prio/Ausstehend/Fehler
```

### Datenfluss Phase-4-Felder

LLM-Ergebnisse werden von `evaluate_projects.py` in Frontmatter-Feldern
gespeichert:

```yaml
extra:
  llm_evaluation:          # LLMEvalResult.to_dict()
    best_profile: crm_sales_automation
    best_score: 78
    evaluations:
      - profile_id: crm_sales_automation
        score: 78
        matched: ["Salesforce", "CRM", "Python"]
        missing: ["SAP", "Power BI"]
        rationale: "Starke CRM-Ausrichtung …"
      - …
    cost_usd: 0.0042
    evaluated_at: "2026-10-09T14:00:00+00:00"
    cache_hit: false
    evaluation_status: ok
    input_tokens: 1200
    output_tokens: 350
    pii_replacements: 2
  llm_priority: high          # "high" | "medium" | "low"
  evaluation_status: ok       # redundant zu llm_evaluation.evaluation_status
```

Kosten-Log: `llm_cost_log.jsonl` im `output_dir` (Standard: Projekt-Root).
Wird von `BudgetTracker` geführt; für Dashboard-Kostenübersicht direkt
eingelesen.

### Frontend (Vue 3 + Pinia + Tailwind)

| Komponente | Stand | Änderungsbedarf |
|---|---|---|
| `Dashboard.vue` | Stat-Cards nach Status, WorkflowButtons, Filter, Tabelle | +LLM-Stat-Cards (bewertet, high-prio, ausstehend, Fehler) + `LLMCostSummary` |
| `ProjectFilters.vue` | Such/Status/Firma/Provider-Filter | +Suchgruppe, Profil, Priorität, eval-Status |
| `ProjectTable.vue` | Spalten: Titel/Firma/Score/Status/Aktionen | +Priorität-Badge, best_profile, eval_status-Indikator |
| `ProjectDetailsModal.vue` | Scores, URL, State-History, Aktionen | +4 Profil-Scores, matched/missing/rationale, API-Kosten |
| `stores/projects.js` | Pinia-Store mit filters-Objekt | +neue Filter-Felder |
| `services/api.js` | Axios-Wrapper | +neue Endpunkte |

**Neue Komponente:** `LLMCostSummary.vue`  
→ Heute/Monat-Budget, API-Aufrufe, Cache-Treffer, Budgetbalken

## Sicherheitsarchitektur

- **Keine E-Mail-Inhalte** im Dashboard: `parse_project_file()` gibt Body-Text
  **nicht** zurück; nur Frontmatter-Felder + vorab extrahierte Metadaten
- **Kein API-Schlüssel im Frontend**: Nur Backend kommuniziert mit LLM-API;
  Kosten-Log ist eine lokale Datei, nie im Frontend exponiert
- **CORS**: weiterhin `localhost:8002` only
- **Schreibende Aktionen** (Statusübergang, manuelle Entscheidungen):
  bestehende Flask-Endpunkte mit `X-Requested-With: XMLHttpRequest`-Header-Check
  als einfacher CSRF-Schutz (reicht für Localhost-only)
- **Keine öffentliche Oberfläche** ohne Auth: Server bindet nur an `127.0.0.1`
  (Dockerfile-Konfiguration bleibt unverändert)

## Komponenten-Map Phase 5

```
server_enhanced.py
  ├── parse_project_file()           +llm_evaluation aus extra-Feldern
  ├── ProjectFilters                 +search_groups, profile_id, priority_label, evaluation_status
  ├── get_projects_with_filters()    +neue Filter
  ├── get_dashboard_stats()          +llm_stats
  ├── GET /api/v1/llm/costs          NEU
  └── GET /api/v1/llm/stats          NEU (alternativ in dashboard_stats)

frontend/src/
  ├── views/Dashboard.vue            +LLM-Stat-Cards + LLMCostSummary einbinden
  ├── components/
  │   ├── LLMCostSummary.vue         NEU
  │   ├── ProjectFilters.vue         +Suchgruppe, Profil, Priorität, eval-Status
  │   ├── ProjectTable.vue           +Priorität-Badge, eval_status-Indikator
  │   └── ProjectDetailsModal.vue    +LLM-Detailansicht (Profile-Scores, matched/missing)
  ├── stores/projects.js             +neue Filter-Felder
  └── services/api.js                +llmApi (costs, stats)

tests/
  └── test_phase5_dashboard.py       NEU
```

## Neue API-Felder pro Projekt

```json
{
  "id": "…",
  "title": "…",
  "llm_score": 78,
  "llm_priority": "high",
  "evaluation_status": "ok",
  "best_profile": "crm_sales_automation",
  "search_groups": ["crm_sales_automation"],
  "llm_evaluation": {
    "evaluations": [
      {"profile_id": "crm_sales_automation", "score": 78,
       "matched": ["Salesforce"], "missing": ["SAP"], "rationale": "…"},
      …
    ],
    "cost_usd": 0.0042,
    "evaluated_at": "…",
    "cache_hit": false,
    "input_tokens": 1200,
    "output_tokens": 350,
    "pii_replacements": 2
  }
}
```

Felder `llm_evaluation.evaluations`, `matched`, `missing`, `rationale` sind
**nur in der Detailansicht** aktiv (nicht in der Tabellen-Liste, um Payload
klein zu halten). Implementierung: `GET /api/v1/projects/{id}` gibt bereits
vollständiges Objekt zurück — dort einfach ergänzen.

## Manuelle Entscheidungen

Bestehender Endpunkt `PUT /api/v1/projects/{id}/state` übernimmt bereits
Statusübergänge. Ergänzung: vier neue Schnellzustand-Buttons im Modal:

| Aktion | Zielzustand | Erlaubte Von-Zustände |
|---|---|---|
| Interessant | `accepted` | `evaluated`, `scraped` |
| Später prüfen | `scraped` | alle (force=False) → `ui_context=True` |
| Ablehnen | `rejected` | alle |
| Rückgängig | vorheriger Zustand aus `state_history` | letzter Eintrag |

"Rückgängig" liest letzten Eintrag aus `state_history` und setzt Zustand zurück.
Neuer Backend-Endpunkt: `POST /api/v1/projects/{id}/undo_state`.

## Offene Phase-4-Abnahmepunkte (festzuhalten im PR)

1. **Echte API-Integration**: Tests laufen mit Mock-API; echter Anthropic-API-Aufruf
   noch nicht durchgeführt (absichtlich — Kostenkontrolle)
2. **Docker-Start**: `docker-compose up` mit Phase-4-Konfiguration nicht verifiziert
3. **Mehrprozess-Budgettest (OS-Prozesse)**: T59 verwendet Threads statt separate OS-Prozesse;
   `fcntl.LOCK_EX` gilt pro Prozess; ein echter Multiprocess-Test (via `multiprocessing`)
   für produktive Absicherung noch ausstehend
4. **Reservierungsabgleich**: Nach Prozessabbruch verbleiben `type="reserved"`-Einträge
   dauerhaft im Log; kein Aufräum-Mechanismus implementiert (konservative Buchführung
   gewollt, aber kein TTL/Cleanup)

## Umsetzungsreihenfolge

1. Backend: `parse_project_file()` + neue Filter + neue Endpunkte
2. Tests: `test_phase5_dashboard.py`
3. Frontend: Store + API-Service + neue Komponente + Modal-Erweiterung
4. Commit + PR
