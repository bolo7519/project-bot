import { defineStore } from 'pinia'
import api, { quickFiltersApi, projectsApi } from '../services/api'

export const useProjectsStore = defineStore('projects', {
  state: () => ({
    projects: [],
    filters: {
      search: '',
      statuses: [],
      companies: [],
      providers: [],
      channels: [],
      date_from: null,
      date_to: null,
      pre_eval_score_min: null,
      pre_eval_score_max: null,
      llm_score_min: null,
      llm_score_max: null,
      // Phase 5: LLM-Filter
      search_groups: [],
      profile_ids: [],
      priority_labels: [],
      evaluation_statuses: [],
      page: 1,
      page_size: 300
    },
    pagination: {
      total: 0,
      page: 1,
      page_size: 300,
      has_next: false,
      has_prev: false
    },
    loading: false,
    error: null,
    stats: {},
    quickFilters: [],
    loadingQuickFilters: false,
    quickFiltersError: null,
    availableProviders: [],
    availableChannels: [],
    loadingConfigFilters: false,
    configFiltersError: null,
  }),

  getters: {
    filteredProjects: (state) => state.projects,
    isLoading: (state) => state.loading,
    hasError: (state) => !!state.error,
    totalProjects: (state) => state.pagination.total
  },

  actions: {
    async fetchProjects() {
      this.loading = true
      this.error = null

      try {
        const params = new URLSearchParams()

        // Add filters to query params
        if (this.filters.search) params.append('search', this.filters.search)
        if (this.filters.statuses.length > 0) {
          this.filters.statuses.forEach(status => params.append('statuses', status))
        }
        if (this.filters.companies.length > 0) {
          this.filters.companies.forEach(company => params.append('companies', company))
        }
        if (this.filters.providers.length > 0) {
          this.filters.providers.forEach(provider => params.append('providers', provider))
        }
        if (this.filters.channels.length > 0) {
          this.filters.channels.forEach(channel => params.append('channels', channel))
        }
        if (this.filters.date_from) params.append('date_from', this.filters.date_from)
        if (this.filters.date_to) params.append('date_to', this.filters.date_to)
        if (this.filters.pre_eval_score_min !== null) params.append('pre_eval_score_min', this.filters.pre_eval_score_min)
        if (this.filters.pre_eval_score_max !== null) params.append('pre_eval_score_max', this.filters.pre_eval_score_max)
        if (this.filters.llm_score_min !== null) params.append('llm_score_min', this.filters.llm_score_min)
        if (this.filters.llm_score_max !== null) params.append('llm_score_max', this.filters.llm_score_max)
        // Phase 5: LLM-Filter
        if (this.filters.search_groups?.length > 0) {
          this.filters.search_groups.forEach(g => params.append('search_groups', g))
        }
        if (this.filters.profile_ids?.length > 0) {
          this.filters.profile_ids.forEach(p => params.append('profile_ids', p))
        }
        if (this.filters.priority_labels?.length > 0) {
          this.filters.priority_labels.forEach(l => params.append('priority_labels', l))
        }
        if (this.filters.evaluation_statuses?.length > 0) {
          this.filters.evaluation_statuses.forEach(s => params.append('evaluation_statuses', s))
        }
        if (this.filters.page !== 1) params.append('page', this.filters.page)
        params.append('page_size', this.filters.page_size)

        const response = await api.get(`/api/v1/projects?${params.toString()}`)
        console.log('📦 Raw API response:', response.data)
        console.log('📊 Projects array:', response.data.projects)
        console.log('📊 Projects count:', response.data.projects?.length || 0)

        this.projects = response.data.projects || []
        this.pagination = {
          total: response.data.total || 0,
          page: response.data.page || 1,
          page_size: response.data.page_size || 300,
          has_next: response.data.has_next || false,
          has_prev: response.data.has_prev || false
        }

        console.log(`✅ Store updated: ${this.projects.length} projects (${this.pagination.total} total)`)
        console.log('🔍 First project:', this.projects[0])
      } catch (error) {
        console.error('Error fetching projects:', error)
        this.error = error.response?.data?.message || error.message
        throw error
      } finally {
        this.loading = false
      }
    },

    async fetchProjectById(id) {
      try {
        const response = await api.get(`/api/v1/projects/${id}`)
        return response.data
      } catch (error) {
        console.error('Error fetching project:', error)
        throw error
      }
    },

    async updateProjectState(id, fromState, toState, note = null) {
      const requestData = {
        from_state: fromState,
        to_state: toState,
        note: note,
        force: true,  // Always force for UI calls
        ui_context: true  // Indicate this is a UI-initiated change
      }
      console.log('Sending UI transition request:', requestData)
      
      try {
        const response = await api.post(`/api/v1/projects/${id}/transition`, requestData)
        
        // Update the project in the local state
        const projectIndex = this.projects.findIndex(p => p.id === id)
        if (projectIndex !== -1) {
          this.projects[projectIndex] = response.data.project
        }
        
        console.log(`Project ${id} state updated: ${fromState} → ${toState} (UI context)`)
        return response.data
      } catch (error) {
        console.error('Error updating project state:', error)
        throw error
      }
    },

    async generateApplication(id) {
      try {
        const response = await api.post(`/api/v1/projects/${id}/generate`, {}, {
          timeout: 300000 // 5 minutes timeout to match backend
        })
        console.log(`Application generated for project ${id}`)
        return response.data
      } catch (error) {
        console.error('Error generating application:', error)
        throw error
      }
    },

    async reevaluateProject(id, force = false) {
      try {
        const response = await api.post(`/api/v1/projects/${id}/evaluate`, { force }, {
          timeout: 60000 // 60 seconds timeout for evaluation
        })
        console.log(`Project ${id} reevaluated successfully${force ? ' (forced)' : ''}`)
        return response.data
      } catch (error) {
        console.error('Error reevaluating project:', error)
        throw error
      }
    },

    async fetchStats() {
      try {
        const response = await api.get('/api/v1/dashboard/stats')
        this.stats = response.data
        return response.data
      } catch (error) {
        console.error('Error fetching stats:', error)
        throw error
      }
    },

    async runWorkflow(workflowName) {
      try {
        const response = await api.post(`/api/v1/workflows/${workflowName}/run`, {})
        console.log(`Workflow ${workflowName} executed successfully`)
        return response.data
      } catch (error) {
        console.error(`Error running workflow ${workflowName}:`, error)
        throw error
      }
    },

    async runRssIngest() {
      try {
        const response = await api.post('/api/v1/workflows/rss_ingest/run', {})
        console.log('RSS ingestion executed successfully')
        return response.data
      } catch (error) {
        console.error('Error running RSS ingestion:', error)
        throw error
      }
    },

    async runFullWorkflow() {
      try {
        const response = await api.post('/api/v1/workflows/full_workflow/run', {})
        console.log('Full workflow executed successfully')
        return response.data
      } catch (error) {
        console.error('Error running full workflow:', error)
        throw error
      }
    },

    async getWorkflowStatus(workflowName) {
      try {
        const response = await api.get(`/api/v1/workflows/${workflowName}/status`)
        return response.data
      } catch (error) {
        console.error(`Error getting workflow status for ${workflowName}:`, error)
        throw error
      }
    },

    setFilters(filters) {
      this.filters = { ...this.filters, ...filters }
    },

    setPage(page) {
      this.filters.page = page
    },

    resetFilters() {
      this.filters = {
        search: '',
        statuses: [],
        companies: [],
        providers: [],
        channels: [],
        date_from: null,
        date_to: null,
        pre_eval_score_min: null,
        pre_eval_score_max: null,
        llm_score_min: null,
        llm_score_max: null,
        // Phase 5: LLM-Filter
        search_groups: [],
        profile_ids: [],
        priority_labels: [],
        evaluation_statuses: [],
        page: 1,
        page_size: 300
      }
    },

    clearError() {
      this.error = null
    },

    async fetchQuickFilters() {
      this.loadingQuickFilters = true;
      this.quickFiltersError = null;
      try {
        const response = await quickFiltersApi.getQuickFilters();
        this.quickFilters = response.filters;
      } catch (error) {
        this.quickFiltersError = error.response?.data?.message || error.message;
      } finally {
        this.loadingQuickFilters = false;
      }
    },

    async createQuickFilter(filter) {
      try {
        await quickFiltersApi.createQuickFilter(filter);
        await this.fetchQuickFilters();
      } catch (error) {
        this.quickFiltersError = error.response?.data?.message || error.message;
        throw error;
      }
    },

    async updateQuickFilter(id, filter) {
      try {
        await quickFiltersApi.updateQuickFilter(id, filter);
        await this.fetchQuickFilters();
      } catch (error) {
        this.quickFiltersError = error.response?.data?.message || error.message;
        throw error;
      }
    },

    async deleteQuickFilter(id) {
      try {
        await quickFiltersApi.deleteQuickFilter(id);
        await this.fetchQuickFilters();
      } catch (error) {
        this.quickFiltersError = error.response?.data?.message || error.message;
        throw error;
      }
    },

    async deleteProject(projectId) {
      try {
        await projectsApi.deleteProject(projectId);
        this.projects = this.projects.filter(p => p.id !== projectId);
        this.pagination.total -= 1;
      } catch (error) {
        console.error('Error deleting project:', error);
        throw error;
      }
    },

    async createManualProject(projectData) {
      try {
        const response = await api.post('/api/v1/projects', projectData);
        console.log('Manual project created:', response.data);

        // Add the new project to the local state
        if (response.data.project) {
          this.projects.unshift(response.data.project);
          this.pagination.total += 1;
        }

        return response.data;
      } catch (error) {
        console.error('Error creating manual project:', error);
        throw error;
      }
    },

    async fetchConfigFilters() {
      this.loadingConfigFilters = true;
      this.configFiltersError = null;
      try {
        const response = await api.get('/api/v1/config/filters');
        this.availableProviders = response.data.providers || [];
        this.availableChannels = response.data.channels || [];
        console.log('Config filters loaded:', { providers: this.availableProviders, channels: this.availableChannels });
      } catch (error) {
        this.configFiltersError = error.response?.data?.message || error.message;
        console.error('Error fetching config filters:', error);
        // Set fallback empty arrays to prevent UI breakage
        this.availableProviders = [];
        this.availableChannels = [];
      } finally {
        this.loadingConfigFilters = false;
      }
    },
  }
})
