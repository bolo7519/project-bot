<template>
  <div class="dashboard">
    <header class="header">
      <div class="header-left">
        <img :src="logoImage" alt="Project Bot Logo" class="logo" />
      </div>
      <div class="header-center">
        <div class="header-text">
          <h1>Project Bot</h1>
          <p class="subtitle">The Complete AI Career Workflow Solution</p>
        </div>
      </div>
      <nav class="header-nav">
        <button @click="createManualProject" class="nav-link create-btn" :disabled="loading">
          ➕ Create Manual
        </button>
        <button @click="refreshData" class="nav-link refresh-btn" :disabled="loading">
          🔄 Refresh
        </button>
        <router-link to="/schedules" class="nav-link">
          ⏰ Schedule Manager
        </router-link>
        <router-link to="/logs" class="nav-link">
          📋 Log Viewer
        </router-link>
      </nav>
    </header>

    <main class="main-content">
      <!-- Workflow Messages -->
      <div v-if="workflowMessage" :class="['workflow-message', `message-${workflowMessageType}`]">
        <span class="message-icon">{{ workflowMessageType === 'success' ? '✅' : workflowMessageType === 'error' ? '❌' : '⚠️' }}</span>
        <span class="message-text">{{ workflowMessage }}</span>
        <button @click="clearWorkflowMessage" class="message-close">×</button>
      </div>

      <!-- Dashboard Stats -->
      <div class="stats-section">
        <div class="stat-card stat-total">
          <div class="stat-number">{{ totalProjects }}</div>
          <div class="stat-label">Total Projects</div>
        </div>
        <div v-for="(count, status) in statsByStatus" :key="status" class="stat-card clickable" :class="`stat-${status}`" @click="filterByStatus(status)">
          <div class="stat-number">{{ count }}</div>
          <div class="stat-label">{{ status }}</div>
        </div>
      </div>

      <!-- LLM Stats -->
      <div v-if="hasLLMStats" class="llm-stats-section">
        <div class="llm-stats-header">🤖 KI-Bewertung</div>
        <div class="llm-stats-grid">
          <div class="llm-stat-card llm-stat-evaluated clickable" @click="filterByEvalStatus('ok')">
            <div class="llm-stat-number">{{ llmStats.llm_evaluated }}</div>
            <div class="llm-stat-label">KI-bewertet</div>
          </div>
          <div class="llm-stat-card llm-stat-high clickable" @click="filterByPriority('high')">
            <div class="llm-stat-number">{{ llmStats.llm_high_priority }}</div>
            <div class="llm-stat-label">High Priority</div>
          </div>
          <div class="llm-stat-card llm-stat-pending clickable" @click="filterByEvalStatus('pending_retry')">
            <div class="llm-stat-number">{{ llmStats.llm_pending }}</div>
            <div class="llm-stat-label">Ausstehend</div>
          </div>
          <div class="llm-stat-card llm-stat-failed clickable" @click="filterByEvalStatus('failed')">
            <div class="llm-stat-number">{{ llmStats.llm_failed }}</div>
            <div class="llm-stat-label">Fehler</div>
          </div>
        </div>
      </div>

      <!-- KI-Kostenübersicht -->
      <LLMCostSummary />

      <!-- Named Workflows -->
      <WorkflowButtons
        @workflow-started="handleWorkflowStarted"
        @workflow-completed="handleWorkflowCompleted"
        @workflow-error="handleWorkflowError"
      />

      <!-- Filters -->
      <ProjectFilters
        :available-companies="availableCompanies"
        :available-providers="projectsStore.availableProviders"
        :available-channels="projectsStore.availableChannels"
        @filters-changed="handleFiltersChanged"
      />

      <!-- Project Table -->
      <ProjectTable
        :projects="projects"
        :loading="loading"
        :error="error"
        :pagination="pagination"
        @view-project="handleViewProject"
        @generate-application="handleGenerateApplication"
        @reevaluate-project="handleReevaluateProject"
        @transition-project="handleTransitionProject"
        @status-changed="handleStatusChanged"
        @retry="handleRetry"
        @page-change="handlePageChange"
        @delete-project="handleDeleteProject"
      />

      <!-- Recent Activity -->
      <div v-if="recentActivity.length > 0" class="activity-section">
        <h3>Recent Activity</h3>
        <div class="activity-list">
          <div v-for="activity in recentActivity" :key="activity.timestamp" class="activity-item">
            <div class="activity-content">
              <strong>{{ activity.project_title }}</strong>
              changed to <span :class="`status-${activity.state}`">{{ activity.state }}</span>
              <span v-if="activity.note" class="activity-note">({{ activity.note }})</span>
            </div>
            <div class="activity-time">{{ formatTime(activity.timestamp) }}</div>
          </div>
        </div>
      </div>

      <!-- Project Details Modal -->
      <ProjectDetailsModal
        :project-id="selectedProjectId"
        :show="showProjectModal"
        @close="closeProjectModal"
        @generate-application="handleGenerateApplication"
        @transition-project="handleTransitionProject"
      />

      <!-- Project Actions Modal -->
      <ProjectActions
        v-if="selectedProjectForTransition"
        :project="selectedProjectForTransition"
        @status-changed="handleStatusChanged"
        @transition-project="handleProjectActionsTransition"
      />
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectsStore } from '../stores/projects'
import WorkflowButtons from '../components/WorkflowButtons.vue'
import ProjectFilters from '../components/ProjectFilters.vue'
import ProjectTable from '../components/ProjectTable.vue'
import ProjectDetailsModal from '../components/ProjectDetailsModal.vue'
import ProjectActions from '../components/ProjectActions.vue'
import LLMCostSummary from '../components/LLMCostSummary.vue'
import logoImage from '../assets/project-bot.png'

// Router
const router = useRouter()

// Store
const projectsStore = useProjectsStore()

// Local state
const availableCompanies = ref([])
const recentActivity = ref([])
const selectedProjectId = ref(null)
const showProjectModal = ref(false)
const selectedProjectForTransition = ref(null)
const workflowMessage = ref(null)
const workflowMessageType = ref(null)

// Computed
const projects = computed(() => {
  const projs = projectsStore.projects
  console.log('📋 Dashboard computed projects:', projs?.length || 0, 'items')
  console.log('📋 First project in dashboard:', projs?.[0])
  return projs
})
const loading = computed(() => {
  const isLoading = projectsStore.loading
  console.log('⏳ Dashboard loading state:', isLoading)
  return isLoading
})
const error = computed(() => {
  const err = projectsStore.error
  if (err) console.log('❌ Dashboard error:', err)
  return err
})
const pagination = computed(() => projectsStore.pagination)
const totalProjects = computed(() => {
  const total = projectsStore.totalProjects
  console.log('📊 Dashboard total projects:', total)
  return total
})
const statsByStatus = computed(() => projectsStore.stats.by_status || {})
const llmStats = computed(() => ({
  llm_evaluated: projectsStore.stats.llm_evaluated || 0,
  llm_high_priority: projectsStore.stats.llm_high_priority || 0,
  llm_pending: projectsStore.stats.llm_pending || 0,
  llm_failed: projectsStore.stats.llm_failed || 0,
}))
const hasLLMStats = computed(() => {
  const s = llmStats.value
  return (s.llm_evaluated + s.llm_high_priority + s.llm_pending + s.llm_failed) > 0
})

// Methods
const handleFiltersChanged = async (filters) => {
  console.log('Filters changed:', filters)
  try {
    await projectsStore.fetchProjects()
  } catch (error) {
    console.error('Failed to fetch projects with new filters:', error)
  }
}

const handleViewProject = (projectId) => {
  selectedProjectId.value = projectId
  showProjectModal.value = true
}

const closeProjectModal = () => {
  showProjectModal.value = false
  selectedProjectId.value = null
}

const handleGenerateApplication = async (projectId) => {
  console.log('Generate application for project:', projectId)
  try {
    await projectsStore.generateApplication(projectId)
    // Refresh data after generation
    await projectsStore.fetchProjects()
    await projectsStore.fetchStats()
  } catch (error) {
    console.error('Failed to generate application:', error)
  }
}

const handleReevaluateProject = async (projectId, force = false) => {
  try {
    await projectsStore.reevaluateProject(projectId, force)
    // Refresh data after reevaluation
    await projectsStore.fetchProjects()
    await projectsStore.fetchStats()
  } catch (error) {
    console.error('Failed to reevaluate project:', error)
  }
}

const handleTransitionProject = (project) => {
  console.log('Transition project from details:', project)
  selectedProjectForTransition.value = project
}

const handleProjectActionsTransition = () => {
  console.log('Transition requested from ProjectActions component')
  // The ProjectActions component manages its own transition modal
  // This handler can be used for additional coordination if needed
}

const handleStatusChanged = async (data) => {
  console.log('Status changed:', data)
  // Close the transition modal
  selectedProjectForTransition.value = null
  // Refresh data
  await projectsStore.fetchProjects()
  await projectsStore.fetchStats()
}

const handleRetry = async () => {
  console.log('Retrying data fetch...')
  try {
    await projectsStore.fetchProjects()
  } catch (error) {
    console.error('Retry failed:', error)
  }
}

const handlePageChange = async (page) => {
  console.log('Page changed to:', page)
  projectsStore.setPage(page)
  try {
    await projectsStore.fetchProjects()
  } catch (error) {
    console.error('Failed to fetch page:', error)
  }
}

const handleDeleteProject = async (projectId) => {
  try {
    await projectsStore.deleteProject(projectId);
    // The store will automatically remove the project from the list,
    // so no need to refetch projects here.
  } catch (error) {
    console.error('Failed to delete project:', error);
  }
};

const filterByEvalStatus = async (evalStatus) => {
  try {
    projectsStore.resetFilters()
    projectsStore.setFilters({ evaluation_statuses: [evalStatus], page: 1 })
    await projectsStore.fetchProjects()
  } catch (error) {
    console.error('Failed to filter by eval status:', error)
  }
}

const filterByPriority = async (priority) => {
  try {
    projectsStore.resetFilters()
    projectsStore.setFilters({ priority_labels: [priority], page: 1 })
    await projectsStore.fetchProjects()
  } catch (error) {
    console.error('Failed to filter by priority:', error)
  }
}

const filterByStatus = async (status) => {
  console.log('Filtering by status:', status);
  try {
    // First reset all existing filters
    projectsStore.resetFilters();
    // Then set filter to only show projects with this status
    projectsStore.setFilters({
      statuses: [status],
      page: 1 // Reset to first page when filtering
    });
    // Fetch projects with the new filter
    await projectsStore.fetchProjects();
  } catch (error) {
    console.error('Failed to filter by status:', error);
  }
};

const createManualProject = async () => {
  console.log('Creating manual project...');

  // Simple prompt for project details
  const title = prompt('Enter project title:');
  if (!title || !title.trim()) {
    alert('Project title is required');
    return;
  }

  const company = prompt('Enter company name (optional):') || '';
  const description = prompt('Enter project description (optional):') || '';

  try {
    const result = await projectsStore.createManualProject({
      title: title.trim(),
      company: company.trim() || null,
      description: description.trim() || null
    });

    console.log('Manual project created successfully:', result);

    // Navigate to the editor for the new project
    if (result.project_id) {
      router.push(`/editor/${result.project_id}`);
    }

    // Refresh stats to show the new project
    await projectsStore.fetchStats();

  } catch (error) {
    console.error('Failed to create manual project:', error);
    alert('Failed to create manual project: ' + (error.response?.data?.message || error.message));
  }
};

const refreshData = async () => {
  console.log('Refreshing dashboard data...')
  try {
    await projectsStore.fetchProjects()
    await projectsStore.fetchStats()
    // Update recent activity
    recentActivity.value = projectsStore.stats.recent_activity || []
    console.log('Dashboard data refreshed successfully')
  } catch (error) {
    console.error('Failed to refresh dashboard data:', error)
  }
}

const handleWorkflowStarted = (workflow) => {
  console.log('Workflow started:', workflow.name)
  workflowMessage.value = `Starting workflow: ${workflow.name}...`
  workflowMessageType.value = 'info'
}

const handleWorkflowCompleted = async (data) => {
  console.log('Workflow completed:', data.workflow.name)
  workflowMessage.value = `Workflow '${data.workflow.name}' completed successfully`
  workflowMessageType.value = 'success'

  // Refresh data after workflow completion
  try {
    await projectsStore.fetchProjects()
    await projectsStore.fetchStats()
    
    // Update recent activity
    recentActivity.value = projectsStore.stats.recent_activity || []
  } catch (error) {
    console.error('Failed to refresh data after workflow:', error)
  }

  // Clear message after 5 seconds
  setTimeout(() => {
    workflowMessage.value = null
    workflowMessageType.value = null
  }, 5000)
}

const handleWorkflowError = (data) => {
  console.error('Workflow error:', data.workflow.name, data.error)
  workflowMessage.value = `Workflow '${data.workflow.name}' failed: ${data.error.response?.data?.message || data.error.message}`
  workflowMessageType.value = 'error'

  // Clear message after 7 seconds for errors
  setTimeout(() => {
    workflowMessage.value = null
    workflowMessageType.value = null
  }, 7000)
}

const clearWorkflowMessage = () => {
  workflowMessage.value = null
  workflowMessageType.value = null
}

const formatTime = (timestamp) => {
  if (!timestamp) return ''
  try {
    const date = new Date(timestamp)
    const now = new Date()
    const diffMs = now - date
    const diffMins = Math.floor(diffMs / (1000 * 60))
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60))
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

    if (diffMins < 1) return 'Just now'
    if (diffMins < 60) return `${diffMins}m ago`
    if (diffHours < 24) return `${diffHours}h ago`
    if (diffDays < 7) return `${diffDays}d ago`

    return date.toLocaleString('de-DE', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })
  } catch {
    return timestamp
  }
}


// Initialize data
const initializeData = async () => {
  try {
    console.log('Initializing dashboard data...')

    // Fetch config filters first
    await projectsStore.fetchConfigFilters()

    // Fetch initial projects
    await projectsStore.fetchProjects()

    // Fetch stats
    await projectsStore.fetchStats()

    // Extract available companies from current projects
    const companies = new Set()
    projectsStore.projects.forEach(project => {
      if (project.company) {
        companies.add(project.company)
      }
    })
    availableCompanies.value = Array.from(companies).sort()

    // Get recent activity from stats
    recentActivity.value = projectsStore.stats.recent_activity || []

    console.log('Dashboard initialized successfully')
  } catch (error) {
    console.error('Failed to initialize dashboard:', error)
  }
}

onMounted(() => {
  console.log('Dashboard component mounted - Phase 3 setup complete')
  initializeData()
})
</script>

<style scoped>
.dashboard {
  min-height: 100vh;
  background-color: #f5f5f5;
}

.header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 2rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-left {
  display: flex;
  align-items: center;
}

.logo {
  height: 120px;
  width: auto;
  flex-shrink: 0;
  object-fit: contain;
}

.header-center {
  flex: 1;
  display: flex;
  justify-content: center;
  align-items: center;
}

.header-text {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  text-align: center;
}

.header-text h1 {
  margin: 0;
  font-size: 2.5rem;
  font-weight: 300;
}

.header-content p {
  margin: 0.5rem 0 0 0;
  opacity: 0.9;
  font-size: 1.1rem;
}

.header-content .subtitle {
  font-weight: bold;
}

.header-nav {
  display: flex;
  gap: 1rem;
}

.nav-link {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1.5rem;
  background: rgba(255, 255, 255, 0.1);
  color: white;
  text-decoration: none;
  border-radius: 6px;
  font-weight: 500;
  transition: all 0.2s;
}

.nav-link:hover {
  background: rgba(255, 255, 255, 0.2);
  transform: translateY(-1px);
}

.refresh-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}

.refresh-btn:disabled:hover {
  background: rgba(255, 255, 255, 0.1);
  transform: none;
}

.create-btn {
  background: #10b981;
  color: white;
}

.create-btn:hover:not(:disabled) {
  background: #059669;
}

.create-btn:disabled {
  background: #6b7280;
  cursor: not-allowed;
}

.main-content {
  width: 100%;
  max-width: 1800px;
  margin: 0 auto;
  padding: 2rem;
  display: flex;
  flex-direction: column;
  gap: 2rem;
}

.workflow-message {
  padding: 1rem 1.5rem;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-weight: 500;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
  animation: slideIn 0.3s ease-out;
}

.message-success {
  background: #dcfce7;
  color: #166534;
  border: 1px solid #bbf7d0;
}

.message-error {
  background: #fef2f2;
  color: #991b1b;
  border: 1px solid #fecaca;
}

.message-warning {
  background: #fffbeb;
  color: #92400e;
  border: 1px solid #fde68a;
}

.message-icon {
  font-size: 1.25rem;
  flex-shrink: 0;
}

.message-text {
  flex: 1;
  font-size: 0.95rem;
}

.message-close {
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  opacity: 0.7;
  padding: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  transition: all 0.2s;
}

.message-close:hover {
  opacity: 1;
  background: rgba(0, 0, 0, 0.1);
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(-10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.stats-section {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  gap: 0.75rem;
}

.stat-card {
  background: white;
  border-radius: 6px;
  padding: 0.75rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  text-align: center;
  transition: transform 0.2s;
  border: 2px solid transparent;
}

.stat-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.stat-card.clickable {
  cursor: pointer;
  transition: all 0.2s ease;
}

.stat-card.clickable:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.stat-card.clickable:active {
  transform: translateY(0);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.stat-number {
  font-size: 1.25rem;
  font-weight: 700;
  color: #374151;
  margin-bottom: 0.25rem;
}

.stat-label {
  font-size: 0.75rem;
  color: #6b7280;
  text-transform: capitalize;
  font-weight: 500;
}

/* Status-specific colors matching the table indicators */
.stat-total {
  background: linear-gradient(135deg, #4f46e5, #7c3aed);
  color: white;
}

.stat-total .stat-number,
.stat-total .stat-label {
  color: white;
}

.stat-scraped {
  background: #fef3c7;
  border-color: #f59e0b;
}

.stat-accepted {
  background: #dcfce7;
  border-color: #10b981;
}

.stat-rejected {
  background: #fef2f2;
  border-color: #ef4444;
}

.stat-applied {
  background: #dbeafe;
  border-color: #3b82f6;
}

.stat-sent {
  background: #f3e8ff;
  border-color: #8b5cf6;
}

.stat-open {
  background: #e0f2fe;
  border-color: #06b6d4;
}

.stat-archived {
  background: #f3f4f6;
  border-color: #6b7280;
}

.stat-empty {
  background: #f9fafb;
  border-color: #d1d5db;
}

.activity-section {
  background: white;
  border-radius: 8px;
  padding: 1.5rem;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
}

.activity-section h3 {
  margin: 0 0 1rem 0;
  color: #374151;
  font-size: 1.25rem;
  font-weight: 600;
}

.activity-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.activity-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem;
  background: #f9fafb;
  border-radius: 0.375rem;
  border-left: 4px solid #4f46e5;
}

.activity-content {
  font-size: 0.875rem;
  color: #374151;
}

.activity-note {
  color: #6b7280;
  font-style: italic;
}

.status-accepted { color: #166534; font-weight: 600; }
.status-rejected { color: #991b1b; font-weight: 600; }
.status-applied { color: #1e40af; font-weight: 600; }
.status-sent { color: #7c3aed; font-weight: 600; }
.status-open { color: #0c4a6e; font-weight: 600; }
.status-archived { color: #374151; font-weight: 600; }
.status-scraped { color: #92400e; font-weight: 600; }
.status-empty { color: #6b7280; font-weight: 600; }

.activity-time {
  font-size: 0.75rem;
  color: #9ca3af;
  white-space: nowrap;
}

/* LLM Stats */
.llm-stats-section {
  background: white;
  border-radius: 8px;
  padding: 0.75rem 1rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.llm-stats-header {
  font-size: 0.75rem;
  font-weight: 600;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 0.5rem;
}

.llm-stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.5rem;
}

.llm-stat-card {
  text-align: center;
  padding: 0.5rem 0.25rem;
  border-radius: 6px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.15s;
}

.llm-stat-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0,0,0,0.12);
}

.llm-stat-number {
  font-size: 1.3rem;
  font-weight: 700;
  color: #1a202c;
}

.llm-stat-label {
  font-size: 0.68rem;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.llm-stat-evaluated  { background: #e0f2fe; border-color: #38bdf8; }
.llm-stat-high       { background: #dcfce7; border-color: #4ade80; }
.llm-stat-pending    { background: #fffbeb; border-color: #facc15; }
.llm-stat-failed     { background: #fef2f2; border-color: #f87171; }

/* Large screen optimization */
@media (min-width: 1440px) {
  .main-content {
    max-width: 2200px;
    padding: 3rem;
  }

  .stats-section {
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 1rem;
  }

  .stat-card {
    padding: 1rem;
  }

  .stat-number {
    font-size: 1.5rem;
  }

  .stat-label {
    font-size: 0.875rem;
  }
}

/* 4K Monitor optimization */
@media (min-width: 2560px) {
  .main-content {
    max-width: 3200px;
    padding: 4rem;
  }

  .stats-section {
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 1.5rem;
  }

  .stat-card {
    padding: 1.25rem;
  }

  .stat-number {
    font-size: 1.75rem;
  }

  .stat-label {
    font-size: 1rem;
  }
}


/* Responsive design */
@media (max-width: 768px) {
  .header {
    padding: 1.5rem;
    flex-direction: column;
    gap: 1rem;
  }

  .header-left {
    order: 1;
  }

  .header-center {
    order: 2;
  }

  .header-nav {
    order: 3;
    width: 100%;
    justify-content: center;
  }

  .header-text h1 {
    font-size: 2rem;
  }

  .logo {
    height: 90px;
  }

  .main-content {
    padding: 1rem;
    gap: 1.5rem;
  }

  .stats-section {
    grid-template-columns: repeat(auto-fit, minmax(80px, 1fr));
    gap: 0.5rem;
  }

  .stat-card {
    padding: 0.5rem;
  }

  .stat-number {
    font-size: 1rem;
  }

  .stat-label {
    font-size: 0.7rem;
  }

  .activity-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.5rem;
  }

  .activity-time {
    align-self: flex-end;
  }
}

@media (max-width: 480px) {
  .stats-section {
    grid-template-columns: repeat(3, 1fr);
    gap: 0.25rem;
  }

  .stat-card {
    padding: 0.375rem;
  }

  .stat-number {
    font-size: 0.875rem;
  }

  .stat-label {
    font-size: 0.625rem;
  }

  .activity-item {
    padding: 0.5rem;
  }

  .logo {
    height: 75px;
  }
}
</style>