// Kennzeichnung des Vorbewertungs-Scores nach Herkunft.
// Der Server liefert pre_eval_score_kind: "eignung" | "tfidf" | "keyword" | null.
export const SCORE_KIND_LABELS = {
  eignung: 'Eignung',
  tfidf: 'TF-IDF',
  keyword: 'Keyword'
}

// z.B. "40 · Eignung" oder "32 · TF-IDF"; ohne bekannte Herkunft nur die Zahl.
export function formatPreEvalScore(project) {
  if (!project || project.pre_eval_score === null || project.pre_eval_score === undefined) {
    return 'N/A'
  }
  const label = SCORE_KIND_LABELS[project.pre_eval_score_kind]
  return label ? `${project.pre_eval_score} · ${label}` : `${project.pre_eval_score}`
}
