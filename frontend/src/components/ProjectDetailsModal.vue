<template>
  <Teleport to="body">
    <div v-if="show" class="modal-overlay" @click="close">
      <div class="modal-content" @click.stop>
        <div class="modal-header">
          <h3>Project Details</h3>
          <button @click="close" class="modal-close">×</button>
        </div>

        <div v-if="loading" class="loading-state">
          <div class="loading-spinner"></div>
          <p>Loading project details...</p>
        </div>

        <div v-else-if="error" class="error-state">
          <div class="error-icon">⚠️</div>
          <h4>Error Loading Project</h4>
          <p>{{ error }}</p>
          <button @click="retry" class="retry-button">Retry</button>
        </div>

        <div v-else-if="project" class="modal-body">
          <!-- Project Header -->
          <div class="project-header">
            <h4>{{ project.title }}</h4>
            <div class="project-meta">
              <span class="company">{{ project.company || 'Unknown Company' }}</span>
              <span :class="`status-badge status-${project.status}`">{{ project.status }}</span>
            </div>
          </div>

          <!-- Project Details Grid -->
          <div class="details-grid">
            <div class="detail-item">
              <label>Retrieval Date:</label>
              <span>{{ formatDate(project.retrieval_date) }}</span>
            </div>
            <div class="detail-item">
              <label>Posted Date:</label>
              <span>{{ project.posted_date || 'N/A' }}</span>
            </div>
            <div class="detail-item">
              <label>Pre-eval Score:</label>
              <span v-if="project.pre_eval_score !== null">{{ formatPreEvalScore(project) }}</span>
              <span v-else>N/A</span>
            </div>
            <div class="detail-item">
              <label>LLM Score:</label>
              <span v-if="project.llm_score !== null">{{ project.llm_score }}%</span>
              <span v-else>N/A</span>
            </div>
          </div>

          <!-- Project URL -->
          <div v-if="project.url" class="url-section">
            <label>Project URL:</label>
            <a :href="project.url" target="_blank" class="project-url">
              {{ project.url }}
            </a>
          </div>

          <!-- State History -->
          <div v-if="project.state_history && project.state_history.length > 0" class="state-history">
            <h5>State History</h5>
            <div class="history-timeline">
              <div
                v-for="(stateChange, index) in project.state_history"
                :key="index"
                class="history-item"
              >
                <div class="history-connector" v-if="index < project.state_history.length - 1"></div>
                <div class="history-content">
                  <div class="history-state">
                    <span :class="`status-badge status-${stateChange.state}`">{{ stateChange.state }}</span>
                  </div>
                  <div class="history-meta">
                    <div class="history-timestamp">{{ formatDate(stateChange.timestamp) }}</div>
                    <div v-if="stateChange.note" class="history-note">{{ stateChange.note }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- LLM-Bewertung (Phase 5) -->
          <div v-if="project.llm_evaluation" class="llm-section">
            <h5>🤖 KI-Bewertung
              <span :class="`eval-status-badge eval-${project.evaluation_status}`">
                {{ evalStatusLabel(project.evaluation_status) }}
              </span>
              <span v-if="project.llm_priority" :class="`prio-badge prio-${project.llm_priority}`">
                {{ project.llm_priority }}
              </span>
            </h5>

            <!-- Profil-Scores -->
            <div v-if="project.llm_evaluation.evaluations?.length" class="profile-evaluations">
              <div
                v-for="ev in project.llm_evaluation.evaluations"
                :key="ev.profile_id"
                class="profile-eval-card"
                :class="{ 'best-profile': ev.profile_id === project.best_profile }"
              >
                <div class="profile-eval-header">
                  <span class="profile-name">{{ ev.profile_id }}</span>
                  <span class="profile-score">{{ ev.score }}%</span>
                  <span v-if="ev.profile_id === project.best_profile" class="best-badge">★ Beste Übereinstimmung</span>
                </div>
                <div class="score-bar">
                  <div class="score-bar-fill" :class="scoreColorClass(ev.score)" :style="{ width: ev.score + '%' }"></div>
                </div>
                <div v-if="ev.matched?.length" class="eval-matched">
                  <span class="eval-tag-label">✓ Matched:</span>
                  <span v-for="m in ev.matched" :key="m" class="eval-tag tag-matched">{{ m }}</span>
                </div>
                <div v-if="ev.missing?.length" class="eval-missing">
                  <span class="eval-tag-label">✗ Fehlend:</span>
                  <span v-for="m in ev.missing" :key="m" class="eval-tag tag-missing">{{ m }}</span>
                </div>
                <div v-if="ev.rationale" class="eval-rationale">{{ ev.rationale }}</div>
              </div>
            </div>

            <!-- Kosten & Token -->
            <div class="llm-cost-info">
              <span v-if="project.llm_evaluation.cost_usd">
                💰 {{ formatCost(project.llm_evaluation.cost_usd) }}
              </span>
              <span v-if="project.llm_evaluation.input_tokens">
                📥 {{ project.llm_evaluation.input_tokens }} Token
              </span>
              <span v-if="project.llm_evaluation.output_tokens">
                📤 {{ project.llm_evaluation.output_tokens }} Token
              </span>
              <span v-if="project.llm_evaluation.cache_hit" class="cache-hit">⚡ Cache-Treffer</span>
              <span v-if="project.llm_evaluation.pii_replacements > 0" class="pii-info">
                🔒 {{ project.llm_evaluation.pii_replacements }} PII-Ersetzungen
              </span>
            </div>
          </div>

          <div v-else-if="project.evaluation_status" class="llm-section llm-section-pending">
            <h5>🤖 KI-Bewertung</h5>
            <span :class="`eval-status-badge eval-${project.evaluation_status}`">
              {{ evalStatusLabel(project.evaluation_status) }}
            </span>
          </div>

          <!-- Manuelle Entscheidungen (Phase 5) -->
          <div class="quick-decisions">
            <button class="decision-btn btn-accept" @click="quickDecision('accepted')" :disabled="transitioning">
              ✅ Interessant
            </button>
            <button class="decision-btn btn-review" @click="quickDecision('scraped')" :disabled="transitioning">
              ⏸ Später prüfen
            </button>
            <button class="decision-btn btn-reject" @click="quickDecision('rejected')" :disabled="transitioning">
              ❌ Ablehnen
            </button>
            <button class="decision-btn btn-undo" @click="undoState" :disabled="transitioning || !canUndo">
              ↩ Rückgängig
            </button>
          </div>

          <!-- Action Buttons -->
          <div class="modal-actions">
            <button @click="close" class="cancel-btn">Close</button>
            <button
              v-if="canGenerateApplication"
              @click="generateApplication"
              class="generate-btn"
              :disabled="generating"
            >
              {{ generating ? 'Generating...' : 'Generate Application' }}
            </button>
            <button
              v-if="canTransition"
              @click="openTransitionModal"
              class="transition-btn"
            >
              Change Status
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useProjectsStore } from '../stores/projects'
import axios from 'axios'
import { formatPreEvalScore } from '../services/scoreLabel'

const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8002'

// Props
const props = defineProps({
  projectId: {
    type: String,
    default: null
  },
  show: {
    type: Boolean,
    default: false
  }
})

// Emits
const emit = defineEmits(['close', 'generate-application', 'transition-project'])

// Store
const projectsStore = useProjectsStore()

// Local state
const project = ref(null)
const loading = ref(false)
const error = ref(null)
const generating = ref(false)
const transitioning = ref(false)

// Computed
const canGenerateApplication = computed(() => {
  return project.value && project.value.status === 'accepted'
})

const canTransition = computed(() => {
  return project.value !== null
})

const canUndo = computed(() => {
  return project.value?.state_history?.length >= 2
})

// Phase 5 helpers
function evalStatusLabel(status) {
  const labels = {
    ok: 'Bewertet',
    pending_retry: 'Ausstehend',
    failed: 'Fehler',
    unsafe_content: 'Unsicherer Inhalt',
    not_evaluated: 'Nicht bewertet',
  }
  return labels[status] || status || '—'
}

function scoreColorClass(score) {
  if (score >= 70) return 'score-high'
  if (score >= 45) return 'score-medium'
  return 'score-low'
}

function formatCost(usd) {
  if (!usd) return '—'
  return `$${Number(usd).toFixed(4)}`
}

// Quick decisions (Phase 5)
const quickDecision = async (targetState) => {
  if (!project.value || transitioning.value) return
  transitioning.value = true
  try {
    await axios.put(
      `${baseURL}/api/v1/projects/${project.value.id}/state`,
      { state: targetState, note: 'Manuelle Entscheidung (Dashboard)', ui_context: true },
      { headers: { 'X-Requested-With': 'XMLHttpRequest' } }
    )
    // Projekt neu laden
    project.value = await projectsStore.fetchProjectById(project.value.id)
    await projectsStore.fetchStats()
  } catch (err) {
    console.error('Quick decision failed:', err)
  } finally {
    transitioning.value = false
  }
}

const undoState = async () => {
  if (!project.value || transitioning.value || !canUndo.value) return
  transitioning.value = true
  try {
    await axios.post(
      `${baseURL}/api/v1/projects/${project.value.id}/undo_state`,
      {},
      { headers: { 'X-Requested-With': 'XMLHttpRequest' } }
    )
    project.value = await projectsStore.fetchProjectById(project.value.id)
    await projectsStore.fetchStats()
  } catch (err) {
    console.error('Undo state failed:', err)
  } finally {
    transitioning.value = false
  }
}

// Methods
const close = () => {
  emit('close')
  resetState()
}

const resetState = () => {
  project.value = null
  loading.value = false
  error.value = null
  generating.value = false
}

const formatDate = (dateString) => {
  if (!dateString) return 'N/A'
  try {
    return new Date(dateString).toLocaleString()
  } catch {
    return dateString
  }
}

const generateApplication = async () => {
  if (!project.value) return

  generating.value = true
  try {
    await emit('generate-application', project.value.id)
    close()
  } catch (error) {
    console.error('Failed to generate application:', error)
  } finally {
    generating.value = false
  }
}

const openTransitionModal = () => {
  if (!project.value) return
  
  console.log('🔄 Opening transition modal for project:', project.value.id, 'status:', project.value.status)
  
  // Emit to parent and close modal
  emit('transition-project', project.value)
  close()
}

const retry = async () => {
  if (!props.projectId) return
  await loadProject()
}

const loadProject = async () => {
  if (!props.projectId) return

  loading.value = true
  error.value = null

  try {
    project.value = await projectsStore.fetchProjectById(props.projectId)
  } catch (err) {
    console.error('Failed to load project:', err)
    error.value = err.response?.data?.message || err.message
  } finally {
    loading.value = false
  }
}

// Watch for projectId changes
watch(() => props.projectId, (newId) => {
  if (newId && props.show) {
    loadProject()
  }
})

// Watch for show changes
watch(() => props.show, (newShow) => {
  if (newShow && props.projectId) {
    loadProject()
  } else if (!newShow) {
    resetState()
  }
})
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: white;
  border-radius: 8px;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
  max-width: 600px;
  width: 90%;
  max-height: 90vh;
  overflow-y: auto;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1.5rem;
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h3 {
  margin: 0;
  color: #374151;
  font-size: 1.25rem;
  font-weight: 600;
}

.modal-close {
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  color: #6b7280;
  padding: 0;
  width: 2rem;
  height: 2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 0.25rem;
  transition: background-color 0.2s;
}

.modal-close:hover {
  background: #f3f4f6;
  color: #374151;
}

.modal-body {
  padding: 1.5rem;
}

.loading-state, .error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem;
  text-align: center;
}

.loading-spinner {
  width: 40px;
  height: 40px;
  border: 4px solid #f3f3f3;
  border-top: 4px solid #4f46e5;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 1rem;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.error-icon {
  font-size: 3rem;
  margin-bottom: 1rem;
}

.error-state h4 {
  margin: 0 0 0.5rem 0;
  color: #374151;
}

.retry-button {
  background: #4f46e5;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-weight: 500;
  transition: background-color 0.2s;
  margin-top: 1rem;
}

.retry-button:hover {
  background: #4338ca;
}

.project-header {
  margin-bottom: 1.5rem;
}

.project-header h4 {
  margin: 0 0 0.5rem 0;
  color: #374151;
  font-size: 1.25rem;
  font-weight: 600;
}

.project-meta {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.company {
  color: #6b7280;
  font-weight: 500;
}

.status-badge {
  display: inline-block;
  padding: 0.25rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: capitalize;
}

.status-accepted { background: #dcfce7; color: #166534; }
.status-rejected { background: #fef2f2; color: #991b1b; }
.status-applied { background: #dbeafe; color: #1e40af; }
.status-sent { background: #f3e8ff; color: #7c3aed; }
.status-open { background: #e0f2fe; color: #0c4a6e; }
.status-archived { background: #f3f4f6; color: #374151; }
.status-scraped { background: #fef3c7; color: #92400e; }
.status-empty { background: #f9fafb; color: #6b7280; }

.details-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.detail-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.detail-item label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #374151;
}

.detail-item span {
  font-size: 0.875rem;
  color: #6b7280;
}

.url-section {
  margin-bottom: 1.5rem;
}

.url-section label {
  display: block;
  font-size: 0.875rem;
  font-weight: 600;
  color: #374151;
  margin-bottom: 0.25rem;
}

.project-url {
  color: #4f46e5;
  text-decoration: none;
  word-break: break-all;
}

.project-url:hover {
  text-decoration: underline;
}

.state-history {
  margin-bottom: 1.5rem;
}

.state-history h5 {
  margin: 0 0 1rem 0;
  color: #374151;
  font-size: 1rem;
  font-weight: 600;
}

.history-timeline {
  position: relative;
}

.history-item {
  position: relative;
  padding-left: 2rem;
  margin-bottom: 1rem;
}

.history-item:last-child {
  margin-bottom: 0;
}

.history-connector {
  position: absolute;
  left: 0.75rem;
  top: 2rem;
  bottom: -1rem;
  width: 2px;
  background: #e5e7eb;
}

.history-content {
  display: flex;
  align-items: flex-start;
  gap: 1rem;
}

.history-state {
  flex-shrink: 0;
}

.history-meta {
  flex: 1;
}

.history-timestamp {
  font-size: 0.75rem;
  color: #9ca3af;
  margin-bottom: 0.25rem;
}

.history-note {
  font-size: 0.875rem;
  color: #6b7280;
  font-style: italic;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  padding-top: 1.5rem;
  border-top: 1px solid #e5e7eb;
}

.cancel-btn {
  background: white;
  color: #6b7280;
  border: 1px solid #d1d5db;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.875rem;
  font-weight: 500;
  transition: all 0.2s;
}

.cancel-btn:hover {
  border-color: #9ca3af;
  background: #f9fafb;
}

.generate-btn {
  background: #059669;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.875rem;
  font-weight: 500;
  transition: background-color 0.2s;
}

.generate-btn:hover:not(:disabled) {
  background: #047857;
}

.generate-btn:disabled {
  background: #9ca3af;
  cursor: not-allowed;
}

.transition-btn {
  background: #7c3aed;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.875rem;
  font-weight: 500;
  transition: background-color 0.2s;
}

.transition-btn:hover {
  background: #6d28d9;
}

/* LLM-Sektion */
.llm-section {
  margin-bottom: 1.5rem;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 1rem;
  background: #f8fafc;
}

.llm-section h5 {
  margin: 0 0 0.75rem 0;
  font-size: 0.95rem;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.llm-section-pending { background: #fffbeb; border-color: #fde68a; }

.eval-status-badge {
  font-size: 0.7rem;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 500;
}
.eval-ok           { background: #dcfce7; color: #166534; }
.eval-pending_retry { background: #fffbeb; color: #92400e; }
.eval-failed       { background: #fef2f2; color: #991b1b; }
.eval-unsafe_content { background: #fef9c3; color: #713f12; }
.eval-not_evaluated { background: #f3f4f6; color: #374151; }

.prio-badge {
  font-size: 0.68rem;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 600;
  text-transform: uppercase;
}
.prio-high   { background: #dcfce7; color: #166534; }
.prio-medium { background: #fef9c3; color: #92400e; }
.prio-low    { background: #f3f4f6; color: #6b7280; }

.profile-evaluations {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.profile-eval-card {
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 0.75rem;
}

.profile-eval-card.best-profile {
  border-color: #4ade80;
  background: #f0fdf4;
}

.profile-eval-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
  flex-wrap: wrap;
}

.profile-name {
  font-weight: 600;
  font-size: 0.85rem;
  color: #374151;
}

.profile-score {
  font-size: 1rem;
  font-weight: 700;
  color: #1a202c;
  margin-left: auto;
}

.best-badge {
  font-size: 0.65rem;
  color: #166534;
  background: #dcfce7;
  padding: 1px 5px;
  border-radius: 4px;
  font-weight: 600;
}

.score-bar {
  height: 5px;
  background: #e2e8f0;
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 0.5rem;
}

.score-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.4s ease;
}

.score-high   { background: #4ade80; }
.score-medium { background: #facc15; }
.score-low    { background: #f87171; }

.eval-matched, .eval-missing {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-bottom: 0.3rem;
  font-size: 0.78rem;
}

.eval-tag-label {
  font-weight: 600;
  color: #374151;
  margin-right: 0.2rem;
}

.eval-tag {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.72rem;
}

.tag-matched { background: #dcfce7; color: #166534; }
.tag-missing { background: #fef2f2; color: #991b1b; }

.eval-rationale {
  font-size: 0.78rem;
  color: #6b7280;
  font-style: italic;
  margin-top: 0.3rem;
  padding-top: 0.3rem;
  border-top: 1px solid #e2e8f0;
}

.llm-cost-info {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  font-size: 0.75rem;
  color: #6b7280;
  padding-top: 0.5rem;
  border-top: 1px solid #e2e8f0;
}

.cache-hit { color: #059669; font-weight: 500; }
.pii-info  { color: #d97706; }

/* Manuelle Entscheidungen */
.quick-decisions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  margin-bottom: 1rem;
}

.decision-btn {
  flex: 1 1 auto;
  padding: 0.4rem 0.75rem;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.82rem;
  font-weight: 500;
  transition: all 0.15s;
}

.decision-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-accept { background: #dcfce7; color: #166534; }
.btn-accept:hover:not(:disabled) { background: #bbf7d0; }

.btn-review { background: #fef9c3; color: #713f12; }
.btn-review:hover:not(:disabled) { background: #fde68a; }

.btn-reject { background: #fef2f2; color: #991b1b; }
.btn-reject:hover:not(:disabled) { background: #fecaca; }

.btn-undo { background: #f3f4f6; color: #374151; }
.btn-undo:hover:not(:disabled) { background: #e5e7eb; }

/* Responsive design */
@media (max-width: 768px) {
  .modal-content {
    width: 95%;
    margin: 1rem;
  }

  .details-grid {
    grid-template-columns: 1fr;
  }

  .history-content {
    flex-direction: column;
    gap: 0.5rem;
  }

  .modal-actions {
    flex-direction: column;
  }

  .cancel-btn, .generate-btn, .transition-btn {
    width: 100%;
  }
}
</style>