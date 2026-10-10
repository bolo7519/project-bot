<template>
  <div class="project-table-container">
    <!-- Loading State -->
    <div v-if="loading" class="loading-state">
      <div class="loading-spinner"></div>
      <p>Loading projects...</p>
    </div>

    <!-- Error State -->
    <div v-else-if="error" class="error-state">
      <div class="error-icon">⚠️</div>
      <h3>Error Loading Projects</h3>
      <p>{{ error }}</p>
      <button @click="retry" class="retry-button">Retry</button>
    </div>

    <!-- Desktop Table View -->
    <div v-else-if="!isMobile" class="table-wrapper">
      <table class="project-table">
        <thead>
          <tr>
            <th @click="sortBy('retrieval_date')" class="sortable">
              Retrieval Date
              <span v-if="sortField === 'retrieval_date'" class="sort-indicator">
                {{ sortOrder === 'asc' ? '↑' : '↓' }}
              </span>
            </th>
            <th @click="sortBy('posted_date')" class="sortable">
              Posted Date
              <span v-if="sortField === 'posted_date'" class="sort-indicator">
                {{ sortOrder === 'asc' ? '↑' : '↓' }}
              </span>
            </th>
            <th @click="sortBy('title')" class="sortable">
              Project Title
              <span v-if="sortField === 'title'" class="sort-indicator">
                {{ sortOrder === 'asc' ? '↑' : '↓' }}
              </span>
            </th>
            <th @click="sortBy('company')" class="sortable">
              Company
              <span v-if="sortField === 'company'" class="sort-indicator">
                {{ sortOrder === 'asc' ? '↑' : '↓' }}
              </span>
            </th>
            <th @click="sortBy('pre_eval_score')" class="sortable">
              Pre-eval Score
              <span v-if="sortField === 'pre_eval_score'" class="sort-indicator">
                {{ sortOrder === 'asc' ? '↑' : '↓' }}
              </span>
            </th>
            <th @click="sortBy('llm_score')" class="sortable">
              LLM Score
              <span v-if="sortField === 'llm_score'" class="sort-indicator">
                {{ sortOrder === 'asc' ? '↑' : '↓' }}
              </span>
            </th>
            <th @click="sortBy('provider_name')" class="sortable">
              Provider
              <span v-if="sortField === 'provider_name'" class="sort-indicator">
                {{ sortOrder === 'asc' ? '↑' : '↓' }}
              </span>
            </th>
            <th @click="sortBy('collection_channel')" class="sortable">
              Channel
              <span v-if="sortField === 'collection_channel'" class="sort-indicator">
                {{ sortOrder === 'asc' ? '↑' : '↓' }}
              </span>
            </th>
            <th @click="sortBy('status')" class="sortable">
              Status
              <span v-if="sortField === 'status'" class="sort-indicator">
                {{ sortOrder === 'asc' ? '↑' : '↓' }}
              </span>
            </th>
            <th class="actions-header">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="project in sortedProjects" :key="project.id" class="project-row">
            <td>{{ formatDate(project.retrieval_date) }}</td>
            <td>{{ project.posted_date || 'N/A' }}</td>
            <td>
              <div class="project-title">
                <a :href="project.url" target="_blank" class="project-link">
                  {{ project.title }}
                </a>
              </div>
            </td>
            <td>{{ project.company || 'N/A' }}</td>
            <td>
              <span v-if="project.pre_eval_score !== null" class="score">
                {{ formatPreEvalScore(project) }}
              </span>
              <span v-else class="no-score">N/A</span>
            </td>
            <td>
              <span v-if="project.llm_score !== null" class="score">
                {{ project.llm_score }}%
              </span>
              <span v-else class="no-score">N/A</span>
            </td>
            <td>{{ project.metadata?.provider_name || 'N/A' }}</td>
            <td>
              <span :class="`channel-badge channel-${project.metadata?.collection_channel || 'unknown'}`">
                {{ project.metadata?.collection_channel || 'unknown' }}
              </span>
            </td>
            <td>
              <span :class="`status-badge status-${project.status}`">
                {{ project.status }}
              </span>
            </td>
            <td>
              <ProjectActions
                :project="project"
                @view-project="handleViewProject"
                @generate-application="handleGenerateApplication"
                @reevaluate-project="handleReevaluateProject"
                @transition-project="handleTransitionProject"
                @status-changed="handleStatusChanged"
                @delete-project="handleDeleteProject"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Mobile Card View -->
    <div v-else class="mobile-cards">
      <div v-for="project in sortedProjects" :key="project.id" class="project-card">
        <div class="card-header">
          <div class="card-title-section">
            <h3 class="card-title">
              <a :href="project.url" target="_blank" class="project-link">
                {{ project.title }}
              </a>
            </h3>
            <div class="card-company">{{ project.company || 'N/A' }}</div>
          </div>
          <div class="card-status-section">
            <span :class="`status-badge status-${project.status}`">
              {{ project.status }}
            </span>
          </div>
        </div>

        <div class="card-details">
          <div class="detail-row">
            <span class="detail-label">Retrieval:</span>
            <span class="detail-value">{{ formatDate(project.retrieval_date) }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Posted:</span>
            <span class="detail-value">{{ project.posted_date || 'N/A' }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Pre-eval Score:</span>
            <span class="detail-value">
              <span v-if="project.pre_eval_score !== null" class="score">
                {{ formatPreEvalScore(project) }}
              </span>
              <span v-else class="no-score">N/A</span>
            </span>
          </div>
          <div class="detail-row">
            <span class="detail-label">LLM Score:</span>
            <span class="detail-value">
              <span v-if="project.llm_score !== null" class="score">
                {{ project.llm_score }}%
              </span>
              <span v-else class="no-score">N/A</span>
            </span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Provider:</span>
            <span class="detail-value">{{ project.metadata?.provider_name || 'N/A' }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Channel:</span>
            <span class="detail-value">
              <span :class="`channel-badge channel-${project.metadata?.collection_channel || 'unknown'}`">
                {{ project.metadata?.collection_channel || 'unknown' }}
              </span>
            </span>
          </div>
        </div>

        <div class="card-actions">
          <ProjectActions
            :project="project"
            :compact="true"
            @view-project="handleViewProject"
            @generate-application="handleGenerateApplication"
            @reevaluate-project="handleReevaluateProject"
            @transition-project="handleTransitionProject"
            @status-changed="handleStatusChanged"
            @delete-project="handleDeleteProject"
          />
        </div>
      </div>

      <!-- Empty State for Mobile -->
      <div v-if="projects.length === 0" class="empty-state">
        <div class="empty-icon">📋</div>
        <h3>No Projects Found</h3>
        <p>Try adjusting your filters or check if projects are available.</p>
      </div>
    </div>

    <!-- Pagination -->
    <div v-if="!loading && !error && projects.length > 0 && Math.ceil(pagination.total / pagination.page_size) > 1" class="pagination">
      <button
        @click="goToPage(pagination.page - 1)"
        :disabled="!pagination.has_prev"
        class="page-btn"
      >
        ← Previous
      </button>

      <span class="page-info">
        Page {{ pagination.page }} of {{ Math.ceil(pagination.total / pagination.page_size) }}
        ({{ pagination.total }} total projects)
      </span>

      <button
        @click="goToPage(pagination.page + 1)"
        :disabled="!pagination.has_next"
        class="page-btn"
      >
        Next →
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useProjectsStore } from '../stores/projects'
import ProjectActions from './ProjectActions.vue'
import { formatPreEvalScore } from '../services/scoreLabel'

// Props
const props = defineProps({
  projects: {
    type: Array,
    default: () => []
  },
  loading: {
    type: Boolean,
    default: false
  },
  error: {
    type: String,
    default: null
  },
  pagination: {
    type: Object,
    default: () => ({
      total: 0,
      page: 1,
      page_size: 300,
      has_next: false,
      has_prev: false
    })
  }
})

// Emits
const emit = defineEmits(['view-project', 'generate-application', 'reevaluate-project', 'transition-project', 'status-changed', 'retry', 'page-change', 'delete-project'])

// Store
const projectsStore = useProjectsStore()

// Local state
const sortField = ref('retrieval_date')
const sortOrder = ref('desc')
const generatingProjects = ref(new Set())
const isMobile = ref(false)

// Mobile detection
const checkMobile = () => {
  isMobile.value = window.innerWidth < 768
}

onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
})

onUnmounted(() => {
  window.removeEventListener('resize', checkMobile)
})

// Computed
const sortedProjects = computed(() => {
  if (!props.projects.length) return []

  return [...props.projects].sort((a, b) => {
    let aVal, bVal

    // Handle nested metadata fields
    if (sortField.value === 'provider_name' || sortField.value === 'collection_channel') {
      aVal = a.metadata?.[sortField.value] || ''
      bVal = b.metadata?.[sortField.value] || ''
    } else {
      aVal = a[sortField.value] || ''
      bVal = b[sortField.value] || ''
    }

    // Handle null/undefined values
    if (aVal === null || aVal === undefined) aVal = ''
    if (bVal === null || bVal === undefined) bVal = ''

    // Convert to comparable values
    if (typeof aVal === 'string') aVal = aVal.toLowerCase()
    if (typeof bVal === 'string') bVal = bVal.toLowerCase()

    if (aVal < bVal) return sortOrder.value === 'asc' ? -1 : 1
    if (aVal > bVal) return sortOrder.value === 'asc' ? 1 : -1
    return 0
  })
})


// Methods
const sortBy = (field) => {
  if (sortField.value === field) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortField.value = field
    sortOrder.value = 'asc'
  }
}

const formatDate = (dateString) => {
  if (!dateString) return 'N/A'
  try {
    const date = new Date(dateString)
    return date.toLocaleString('de-DE', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })
  } catch {
    return dateString
  }
}

const viewProject = (projectId) => {
  emit('view-project', projectId)
}

const generateApplication = async (projectId) => {
  generatingProjects.value.add(projectId)
  try {
    emit('generate-application', projectId)
  } finally {
    setTimeout(() => {
      generatingProjects.value.delete(projectId)
    }, 2000)
  }
}

const transitionProject = (project) => {
  emit('transition-project', project)
}

const canTransition = (status) => {
  // Allow transitions for most states except archived
  return status !== 'archived'
}

const goToPage = (page) => {
  emit('page-change', page)
}

const handleViewProject = (projectId) => {
  emit('view-project', projectId)
}

const handleGenerateApplication = (projectId) => {
  emit('generate-application', projectId)
}

const handleReevaluateProject = (projectId, force = false) => {
  emit('reevaluate-project', projectId, force)
}

const handleTransitionProject = (project) => {
  emit('transition-project', project)
}

const handleStatusChanged = (data) => {
  emit('status-changed', data)
}

const handleDeleteProject = (projectId) => {
  emit('delete-project', projectId)
}

const retry = () => {
  emit('retry')
}
</script>

<style scoped>
.project-table-container {
  width: 100%;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.loading-state, .error-state, .empty-state {
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

.error-icon, .empty-icon {
  font-size: 3rem;
  margin-bottom: 1rem;
}

.error-state h3, .empty-state h3 {
  color: #374151;
  margin: 0 0 0.5rem 0;
}

.error-state p, .empty-state p {
  color: #6b7280;
  margin: 0 0 1rem 0;
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
}

.retry-button:hover {
  background: #4338ca;
}

.table-wrapper {
  overflow-x: auto;
}

.project-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
}

.project-table th {
  background: #f9fafb;
  padding: 0.5rem 0.75rem;
  text-align: left;
  font-weight: 600;
  color: #374151;
  border-bottom: 1px solid #e5e7eb;
  position: sticky;
  top: 0;
  z-index: 10;
}

.project-table th.sortable {
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s;
}

.project-table th.sortable:hover {
  background: #f3f4f6;
}

.sort-indicator {
  margin-left: 0.25rem;
  opacity: 0.7;
}

.project-table td {
  padding: 0.375rem 0.75rem;
  border-bottom: 1px solid #f3f4f6;
  vertical-align: top;
}

.project-row:hover {
  background: #f9fafb;
}

.project-title {
  max-width: 300px;
}

.actions-header {
  min-width: 320px;
}

/* Large screen optimization */
@media (min-width: 1440px) {
  .project-title {
    max-width: 500px;
  }

  .project-table th,
  .project-table td {
    padding: 1rem 0.75rem;
  }

  .project-table {
    font-size: 0.9rem;
  }

  .actions-header {
    min-width: 320px;
  }
}

.project-link {
  color: #4f46e5;
  text-decoration: none;
  font-weight: 500;
}

.project-link:hover {
  text-decoration: underline;
}

.score {
  font-weight: 600;
  color: #059669;
}

.no-score {
  color: #6b7280;
  font-style: italic;
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

.channel-badge {
  display: inline-block;
  padding: 0.25rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: capitalize;
}

.channel-email { background: #dbeafe; color: #1e40af; }
.channel-rss { background: #dcfce7; color: #166534; }
.channel-unknown { background: #f3f4f6; color: #6b7280; }

.actions {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.action-btn {
  background: none;
  border: 1px solid #d1d5db;
  padding: 0.375rem;
  border-radius: 0.25rem;
  cursor: pointer;
  font-size: 0.875rem;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
}

.action-btn:hover {
  border-color: #9ca3af;
  background: #f9fafb;
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.view-btn:hover { border-color: #4f46e5; color: #4f46e5; }
.generate-btn:hover { border-color: #059669; color: #059669; }
.transition-btn:hover { border-color: #7c3aed; color: #7c3aed; }

.pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem;
  background: #f9fafb;
  border-top: 1px solid #e5e7eb;
}

.page-btn {
  background: #4f46e5;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-weight: 500;
  transition: background-color 0.2s;
}

.page-btn:hover:not(:disabled) {
  background: #4338ca;
}

.page-btn:disabled {
  background: #9ca3af;
  cursor: not-allowed;
}

.page-info {
  color: #6b7280;
  font-size: 0.875rem;
}

/* Responsive design */
@media (max-width: 768px) {
  .project-table {
    font-size: 0.75rem;
  }

  .project-table th,
  .project-table td {
    padding: 0.25rem 0.5rem;
  }

  .project-table th:last-child,
  .project-table td:last-child {
    min-width: 120px; /* Reduce Actions column width on mobile */
    max-width: 150px;
  }

  .project-title {
    max-width: 150px;
  }

  .actions {
    flex-direction: column;
    gap: 0.25rem;
  }

  .action-btn {
    width: 1.5rem;
    height: 1.5rem;
    font-size: 0.75rem;
  }
}

@media (max-width: 480px) {
  .project-table th:last-child,
  .project-table td:last-child {
    min-width: 100px;
    max-width: 120px;
  }

  .project-title {
    max-width: 120px;
  }
}

/* Mobile Card Layout */
.mobile-cards {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.project-card {
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
  padding: 1rem;
  border: 1px solid #e5e7eb;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1rem;
  gap: 1rem;
}

.card-title-section {
  flex: 1;
  min-width: 0;
}

.card-title {
  margin: 0 0 0.5rem 0;
  font-size: 1.1rem;
  font-weight: 600;
  line-height: 1.3;
}

.card-title .project-link {
  color: #4f46e5;
  text-decoration: none;
  word-break: break-word;
}

.card-title .project-link:hover {
  text-decoration: underline;
}

.card-company {
  color: #6b7280;
  font-size: 0.9rem;
  font-weight: 500;
}

.card-status-section {
  flex-shrink: 0;
}

.card-details {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem 1rem;
  margin-bottom: 1rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid #f3f4f6;
}

.detail-row {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.detail-label {
  font-size: 0.8rem;
  color: #6b7280;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.detail-value {
  font-size: 0.9rem;
  color: #374151;
  word-break: break-word;
}

.card-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.25rem;
  flex-wrap: nowrap;
}

/* Mobile card responsive adjustments */
@media (max-width: 480px) {
  .card-details {
    grid-template-columns: 1fr;
    gap: 0.5rem;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.5rem;
  }

  .card-status-section {
    align-self: flex-end;
  }

  .project-card {
    padding: 0.75rem;
  }
}
</style>