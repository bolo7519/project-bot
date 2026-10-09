<template>
  <div class="project-filters">
    <div class="filters-header">
      <h3>Filters & Search</h3>
      <div class="header-actions">
        <button @click="resetFilters" class="reset-btn" :disabled="!hasActiveFilters">
          Reset All
        </button>
      </div>
    </div>

    <div class="filters-grid">
      <!-- Search -->
      <div class="filter-group">
        <label for="search" class="filter-label">Search</label>
        <input
          id="search"
          v-model="localFilters.search"
          type="text"
          placeholder="Search by title or company..."
          class="filter-input"
          @input="debounceApplyFilters"
        />
      </div>

      <!-- Status Filter -->
      <div class="filter-group">
        <label class="filter-label">Status</label>
        <div class="checkbox-grid">
          <label v-for="status in availableStatuses" :key="status" class="checkbox-label">
            <input
              type="checkbox"
              :value="status"
              v-model="localFilters.statuses"
              @change="applyFilters"
            />
            <span class="checkbox-text">{{ status }}</span>
          </label>
        </div>
      </div>

      <!-- Company Filter -->
      <div class="filter-group">
        <label class="filter-label">Company</label>
        <CompanyMultiSelect
          v-model="localFilters.companies"
          :available-companies="availableCompanies"
          @update:modelValue="applyFilters"
        />
      </div>

      <!-- Provider Filter -->
      <div class="filter-group">
        <label class="filter-label">Provider</label>
        <div class="custom-multiselect" :class="{ active: showProviderDropdown }">
          <div class="multiselect-trigger" @click="toggleProviderDropdown">
            <span v-if="localFilters.providers.length === 0" class="placeholder">Select providers...</span>
            <span v-else class="selected-summary">
              {{ localFilters.providers.length }} selected
            </span>
            <span class="dropdown-arrow">▼</span>
          </div>
          <div v-if="showProviderDropdown" class="multiselect-dropdown">
            <label v-for="provider in availableProviders" :key="provider" class="multiselect-option">
              <input
                type="checkbox"
                :value="provider"
                v-model="localFilters.providers"
                @change="applyFilters"
              />
              <span class="option-text">{{ provider }}</span>
            </label>
          </div>
        </div>
      </div>

      <!-- Channel Filter -->
      <div class="filter-group">
        <label class="filter-label">Channel</label>
        <div class="custom-multiselect" :class="{ active: showChannelDropdown }">
          <div class="multiselect-trigger" @click="toggleChannelDropdown">
            <span v-if="localFilters.channels.length === 0" class="placeholder">Select channels...</span>
            <span v-else class="selected-summary">
              {{ localFilters.channels.length }} selected
            </span>
            <span class="dropdown-arrow">▼</span>
          </div>
          <div v-if="showChannelDropdown" class="multiselect-dropdown">
            <label v-for="channel in availableChannels" :key="channel" class="multiselect-option">
              <input
                type="checkbox"
                :value="channel"
                v-model="localFilters.channels"
                @change="applyFilters"
              />
              <span class="option-text">{{ channel }}</span>
            </label>
          </div>
        </div>
      </div>

      <!-- Score Ranges -->
      <div class="filter-group">
        <label class="filter-label">Score Ranges (%)</label>
        <div class="score-ranges-container">
          <!-- Pre-Eval Score Range -->
          <div class="score-range-group">
            <span class="score-type-label">Pre-Eval:</span>
            <div class="score-range">
              <input
                v-model.number="localFilters.pre_eval_score_min"
                type="number"
                placeholder="Min"
                min="0"
                max="100"
                class="filter-input score-input"
                @input="debounceApplyFilters"
              />
              <span class="range-separator">-</span>
              <input
                v-model.number="localFilters.pre_eval_score_max"
                type="number"
                placeholder="Max"
                min="0"
                max="100"
                class="filter-input score-input"
                @input="debounceApplyFilters"
              />
            </div>
          </div>

          <!-- LLM Score Range -->
          <div class="score-range-group">
            <span class="score-type-label">LLM:</span>
            <div class="score-range">
              <input
                v-model.number="localFilters.llm_score_min"
                type="number"
                placeholder="Min"
                min="0"
                max="100"
                class="filter-input score-input"
                @input="debounceApplyFilters"
              />
              <span class="range-separator">-</span>
              <input
                v-model.number="localFilters.llm_score_max"
                type="number"
                placeholder="Max"
                min="0"
                max="100"
                class="filter-input score-input"
                @input="debounceApplyFilters"
              />
            </div>
          </div>
        </div>
      </div>

      <!-- Phase 5: LLM-Filter -->
      <div class="filter-group">
        <label class="filter-label">KI-Bewertung</label>
        <div class="checkbox-grid">
          <label class="checkbox-label">
            <input type="checkbox" value="high" v-model="localFilters.priority_labels" @change="applyFilters" />
            <span class="checkbox-text prio-high-text">🟢 High Priority</span>
          </label>
          <label class="checkbox-label">
            <input type="checkbox" value="medium" v-model="localFilters.priority_labels" @change="applyFilters" />
            <span class="checkbox-text">🟡 Medium</span>
          </label>
          <label class="checkbox-label">
            <input type="checkbox" value="low" v-model="localFilters.priority_labels" @change="applyFilters" />
            <span class="checkbox-text">⚪ Low</span>
          </label>
        </div>
      </div>

      <div class="filter-group">
        <label class="filter-label">Bewertungsstatus</label>
        <div class="checkbox-grid">
          <label class="checkbox-label">
            <input type="checkbox" value="ok" v-model="localFilters.evaluation_statuses" @change="applyFilters" />
            <span class="checkbox-text">✅ Bewertet</span>
          </label>
          <label class="checkbox-label">
            <input type="checkbox" value="pending_retry" v-model="localFilters.evaluation_statuses" @change="applyFilters" />
            <span class="checkbox-text">⏸ Ausstehend</span>
          </label>
          <label class="checkbox-label">
            <input type="checkbox" value="failed" v-model="localFilters.evaluation_statuses" @change="applyFilters" />
            <span class="checkbox-text">❌ Fehler</span>
          </label>
        </div>
      </div>

      <!-- Date Range -->
      <div class="filter-group">
        <label class="filter-label">Date Range</label>
        <div class="date-range-container">
          <!-- Quick Date Range Selector -->
          <select
            v-model="selectedQuickDateRange"
            @change="applyQuickDateRange"
            class="filter-select quick-date-select"
          >
            <option value="">Custom Range</option>
            <option value="today">Today</option>
            <option value="last_2_days">Last 2 days</option>
            <option value="last_3_days">Last 3 days</option>
            <option value="last_10_days">Last 10 days</option>
            <option value="this_week">This week</option>
            <option value="last_week">Last week</option>
            <option value="this_month">This month</option>
            <option value="last_month">Last month</option>
          </select>

          <!-- Manual Date Inputs -->
          <div class="date-range">
            <input
              v-model="localFilters.date_from"
              type="date"
              class="filter-input date-input"
              @change="onManualDateChange"
              :disabled="selectedQuickDateRange !== ''"
            />
            <span class="range-separator">to</span>
            <input
              v-model="localFilters.date_to"
              type="date"
              class="filter-input date-input"
              @change="onManualDateChange"
              :disabled="selectedQuickDateRange !== ''"
            />
          </div>
        </div>
      </div>

      <!-- Page Size -->
      <div class="filter-group">
        <label for="page-size" class="filter-label">Items per page</label>
        <select
          id="page-size"
          v-model.number="localFilters.page_size"
          class="filter-select"
          @change="applyFilters"
        >
          <option :value="10">10</option>
          <option :value="25">25</option>
          <option :value="50">50</option>
          <option :value="100">100</option>
          <option :value="300">300</option>
          <option :value="500">500</option>
          <option :value="0">All</option>
        </select>
      </div>
    </div>

    <!-- Active Filters Summary -->
    <div v-if="hasActiveFilters" class="active-filters">
      <h4>Active Filters:</h4>
      <div class="filter-tags">
        <span v-if="localFilters.search" class="filter-tag">
          Search: "{{ localFilters.search }}"
          <button @click="clearSearch" class="tag-remove">×</button>
        </span>
        <span v-for="status in localFilters.statuses" :key="status" class="filter-tag">
          Status: {{ status }}
          <button @click="removeStatus(status)" class="tag-remove">×</button>
        </span>
        <span v-for="company in localFilters.companies" :key="company" class="filter-tag">
          Company: {{ company }}
          <button @click="removeCompany(company)" class="tag-remove">×</button>
        </span>
        <span v-for="provider in localFilters.providers" :key="provider" class="filter-tag">
          Provider: {{ provider }}
          <button @click="removeProvider(provider)" class="tag-remove">×</button>
        </span>
        <span v-for="channel in localFilters.channels" :key="channel" class="filter-tag">
          Channel: {{ channel }}
          <button @click="removeChannel(channel)" class="tag-remove">×</button>
        </span>
        <span v-if="localFilters.pre_eval_score_min || localFilters.pre_eval_score_max" class="filter-tag">
          Pre-Eval Score: {{ localFilters.pre_eval_score_min || 0 }}% - {{ localFilters.pre_eval_score_max || 100 }}%
          <button @click="clearPreEvalScoreRange" class="tag-remove">×</button>
        </span>
        <span v-if="localFilters.llm_score_min || localFilters.llm_score_max" class="filter-tag">
          LLM Score: {{ localFilters.llm_score_min || 0 }}% - {{ localFilters.llm_score_max || 100 }}%
          <button @click="clearLlmScoreRange" class="tag-remove">×</button>
        </span>
        <span v-if="localFilters.date_from || localFilters.date_to" class="filter-tag">
          <span v-if="selectedQuickDateRange" class="filter-type-indicator">
            {{ selectedQuickDateRange === 'today' ? '📅' : '🔄' }}
          </span>
          Date: {{ formatDate(localFilters.date_from) || '...' }} to {{ formatDate(localFilters.date_to) || '...' }}
          <button @click="clearDateRange" class="tag-remove">×</button>
        </span>
      </div>
    </div>

    <!-- Quick Filters -->
    <div class="quick-filters">
      <div class="quick-filters-header">
        <h4>Quick Filters:</h4>
        <button @click="saveCurrentFilter" class="save-filter-btn">
          Save Current Filter
        </button>
      </div>
      <div v-if="projectsStore.loadingQuickFilters">Loading...</div>
      <div v-if="projectsStore.quickFiltersError">Error: {{ projectsStore.quickFiltersError }}</div>
      <div v-if="!projectsStore.loadingQuickFilters && projectsStore.quickFilters.length > 0" class="quick-filter-buttons">
        <div v-for="filter in projectsStore.quickFilters" :key="filter.id" class="quick-filter-item">
          <button @click="applySavedFilter(filter)" class="quick-filter-btn" :title="getFilterTooltip(filter)">
            <span v-if="filter.isDynamic" class="filter-type-icon">🔄</span>
            <span v-else class="filter-type-icon">📅</span>
            {{ filter.name }}
          </button>
          <button @click="renameFilter(filter)" class="edit-filter-btn">✏️</button>
          <button @click="updateFilter(filter)" class="edit-filter-btn">💾</button>
          <button @click="deleteFilter(filter.id)" class="delete-filter-btn">×</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useProjectsStore } from '../stores/projects'
import CompanyMultiSelect from './CompanyMultiSelect.vue'

// Props
const props = defineProps({
  availableStatuses: {
    type: Array,
    default: () => ['scraped', 'accepted', 'rejected', 'applied', 'sent', 'open', 'archived', 'empty']
  },
  availableCompanies: {
    type: Array,
    default: () => []
  },
  availableProviders: {
    type: Array,
    default: () => []
  },
  availableChannels: {
    type: Array,
    default: () => []
  }
})

// Emits
const emit = defineEmits(['filters-changed'])

// Store
const projectsStore = useProjectsStore()

// Local state
const localFilters = ref({
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
})

const selectedQuickDateRange = ref('')
const showProviderDropdown = ref(false)
const showChannelDropdown = ref(false)

let debounceTimer = null

// Computed
const hasActiveFilters = computed(() => {
  return localFilters.value.search ||
          localFilters.value.statuses.length > 0 ||
          localFilters.value.companies.length > 0 ||
          localFilters.value.providers.length > 0 ||
          localFilters.value.channels.length > 0 ||
          localFilters.value.date_from ||
          localFilters.value.date_to ||
          localFilters.value.pre_eval_score_min !== null ||
          localFilters.value.pre_eval_score_max !== null ||
          localFilters.value.llm_score_min !== null ||
          localFilters.value.llm_score_max !== null ||
          localFilters.value.priority_labels.length > 0 ||
          localFilters.value.evaluation_statuses.length > 0
})

// Watch for store changes
watch(() => projectsStore.filters, (newFilters) => {
  localFilters.value = { ...newFilters }
}, { deep: true, immediate: true })

// Methods
const debounceApplyFilters = () => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    applyFilters()
  }, 500)
}

const applyFilters = () => {
  // Reset to page 1 when filters change
  localFilters.value.page = 1

  // Update store
  projectsStore.setFilters(localFilters.value)

  // Emit to parent
  emit('filters-changed', localFilters.value)
}

const resetFilters = () => {
  localFilters.value = {
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
    // Phase 5
    search_groups: [],
    profile_ids: [],
    priority_labels: [],
    evaluation_statuses: [],
    page: 1,
    page_size: 300
  }
  selectedQuickDateRange.value = ''
  applyFilters()
}

const clearSearch = () => {
  localFilters.value.search = ''
  applyFilters()
}

const removeStatus = (status) => {
  const index = localFilters.value.statuses.indexOf(status)
  if (index > -1) {
    localFilters.value.statuses.splice(index, 1)
    applyFilters()
  }
}

const removeCompany = (company) => {
  const index = localFilters.value.companies.indexOf(company)
  if (index > -1) {
    localFilters.value.companies.splice(index, 1)
    applyFilters()
  }
}

const removeProvider = (provider) => {
  const index = localFilters.value.providers.indexOf(provider)
  if (index > -1) {
    localFilters.value.providers.splice(index, 1)
    applyFilters()
  }
}

const removeChannel = (channel) => {
  const index = localFilters.value.channels.indexOf(channel)
  if (index > -1) {
    localFilters.value.channels.splice(index, 1)
    applyFilters()
  }
}

const clearPreEvalScoreRange = () => {
  localFilters.value.pre_eval_score_min = null
  localFilters.value.pre_eval_score_max = null
  applyFilters()
}

const clearLlmScoreRange = () => {
  localFilters.value.llm_score_min = null
  localFilters.value.llm_score_max = null
  applyFilters()
}

const clearDateRange = () => {
  localFilters.value.date_from = null
  localFilters.value.date_to = null
  selectedQuickDateRange.value = ''
  applyFilters()
}

const formatDate = (dateString) => {
  if (!dateString) return null
  // Check if it's a relative date string
  if (isNaN(new Date(dateString).getTime())) {
    return dateString.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  }
  try {
    return new Date(dateString).toLocaleDateString()
  } catch {
    return dateString
  }
}

const applySavedFilter = (savedFilter) => {
  let newFilters = JSON.parse(JSON.stringify(savedFilter.filters));

  // Handle dynamic date ranges
  if (savedFilter.isDynamic && savedFilter.originalRange) {
    console.log('🔄 Applying dynamic filter:', savedFilter.originalRange);
    const { from, to } = calculateDateRange(savedFilter.originalRange);
    newFilters.date_from = from;
    newFilters.date_to = to;
    console.log('📅 Recalculated dates:', { from, to });
  }

  const defaultFilters = {
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
    page: 1,
  };

  localFilters.value = {
    ...defaultFilters,
    page_size: localFilters.value.page_size, // preserve page size
    ...newFilters
  };

  // Update the quick date range selector based on the filter type
  if (savedFilter.isDynamic && savedFilter.originalRange) {
    selectedQuickDateRange.value = savedFilter.originalRange;
  } else if (newFilters.date_from && isNaN(new Date(newFilters.date_from).getTime())) {
    selectedQuickDateRange.value = newFilters.date_from;
  } else {
    selectedQuickDateRange.value = '';
  }

  applyFilters();
};

const saveCurrentFilter = async () => {
  const name = prompt('Enter a name for this quick filter:');
  if (name) {
    // Check if current filters include date ranges
    const hasDateRange = localFilters.value.date_from || localFilters.value.date_to;

    let isDynamic = false;
    let originalRange = null;

    if (hasDateRange) {
      // Determine the date range type for display
      let rangeDescription = 'custom date range';
      if (selectedQuickDateRange.value) {
        rangeDescription = selectedQuickDateRange.value.replace(/_/g, ' ');
      } else if (localFilters.value.date_from && localFilters.value.date_to) {
        rangeDescription = `${localFilters.value.date_from} to ${localFilters.value.date_to}`;
      }

      // Ask user if they want dynamic dates (only if it's a quick range)
      if (selectedQuickDateRange.value) {
        const makeDynamic = confirm(
          `Save as Dynamic Filter?\n\n` +
          `Filter: "${name}"\n` +
          `Date Range: ${rangeDescription}\n\n` +
          `• OK: Dynamic (dates recalculate relative to today)\n` +
          `• Cancel: Static (dates stay fixed)`
        );
        isDynamic = makeDynamic;
        if (isDynamic) {
          originalRange = selectedQuickDateRange.value;
        }
      } else {
        // For custom date ranges, just save as static
        alert(`Filter "${name}" saved with custom date range.\n\nNote: Custom date ranges are always static.`);
      }
    }

    // Sanitize the filters to remove page number
    const { page, ...filtersToSave } = localFilters.value;
    const newFilter = {
      name: name,
      description: `Saved on ${new Date().toLocaleDateString()}`,
      isDynamic: isDynamic,
      originalRange: originalRange,
      filters: filtersToSave
    };

    console.log('Saving filter:', newFilter);
    await projectsStore.createQuickFilter(newFilter);
  }
};

const deleteFilter = async (id) => {
  if (confirm('Are you sure you want to delete this quick filter?')) {
    await projectsStore.deleteQuickFilter(id);
  }
};

const renameFilter = async (filter) => {
  const newName = prompt('Enter a new name for this quick filter:', filter.name);
  if (newName && newName !== filter.name) {
    await projectsStore.updateQuickFilter(filter.id, { name: newName });
  }
};

const updateFilter = async (filter) => {
  if (confirm(`Are you sure you want to update the "${filter.name}" filter with the current settings?`)) {
    const { page, ...filtersToSave } = localFilters.value;

    // Preserve existing dynamic settings unless user explicitly changes them
    const hasDateRange = localFilters.value.date_from || localFilters.value.date_to;
    let isDynamic = filter.isDynamic;
    let originalRange = filter.originalRange;

    if (hasDateRange && selectedQuickDateRange.value) {
      // Ask user if they want to change the dynamic setting
      const dynamicChoice = confirm(
        `Update dynamic setting?\n\n` +
        `Current: ${filter.isDynamic ? 'Dynamic' : 'Static'}\n\n` +
        `• OK: Keep current setting (${filter.isDynamic ? 'Dynamic' : 'Static'})\n` +
        `• Cancel: Change to ${filter.isDynamic ? 'Static' : 'Dynamic'}`
      );

      if (!dynamicChoice) {
        isDynamic = !filter.isDynamic;
        originalRange = isDynamic ? selectedQuickDateRange.value : null;
      }
    }

    await projectsStore.updateQuickFilter(filter.id, {
      filters: filtersToSave,
      isDynamic: isDynamic,
      originalRange: originalRange
    });
  }
};

const applyQuickDateRange = () => {
  const range = selectedQuickDateRange.value;
  if (range) {
    const { from, to } = calculateDateRange(range);
    localFilters.value.date_from = from;
    localFilters.value.date_to = to;
  } else {
    // Switched back to custom range, clear the dates
    localFilters.value.date_from = null;
    localFilters.value.date_to = null;
  }
  applyFilters();
}

const calculateDateRange = (range) => {
  const today = new Date();
  const from = new Date();
  let to = new Date();

  switch (range) {
    case 'today':
      from.setDate(today.getDate());
      to.setDate(today.getDate());
      break;
    case 'last_2_days':
      from.setDate(today.getDate() - 1); // Yesterday
      to.setDate(today.getDate());
      break;
    case 'last_3_days':
      from.setDate(today.getDate() - 2); // 2 days ago
      to.setDate(today.getDate());
      break;
    case 'last_10_days':
      from.setDate(today.getDate() - 9); // 9 days ago
      to.setDate(today.getDate());
      break;
    case 'this_week':
      const dayOfWeek = today.getDay();
      from.setDate(today.getDate() - dayOfWeek); // Start of week (Sunday)
      to.setDate(today.getDate());
      break;
    case 'last_week':
      const lastWeekStart = new Date(today);
      lastWeekStart.setDate(today.getDate() - today.getDay() - 7); // Last week's Sunday
      const lastWeekEnd = new Date(lastWeekStart);
      lastWeekEnd.setDate(lastWeekStart.getDate() + 6); // Last week's Saturday
      from.setTime(lastWeekStart.getTime());
      to.setTime(lastWeekEnd.getTime());
      break;
    case 'this_month':
      from.setDate(1); // First day of current month
      to.setDate(today.getDate());
      break;
    case 'last_month':
      from.setMonth(today.getMonth() - 1, 1); // First day of last month
      to.setMonth(today.getMonth(), 0); // Last day of last month
      break;
    default:
      return { from: null, to: null };
  }

  return {
    from: from.toISOString().split('T')[0], // YYYY-MM-DD format
    to: to.toISOString().split('T')[0]    // YYYY-MM-DD format
  };
}

const onManualDateChange = () => {
  // When user manually changes dates, reset quick selector
  selectedQuickDateRange.value = ''
  applyFilters()
}

const getFilterTooltip = (filter) => {
  if (filter.isDynamic) {
    return `🔄 Dynamic filter: ${filter.originalRange?.replace(/_/g, ' ') || 'rolling dates'}\nDates recalculate relative to today when applied`;
  } else {
    return `📅 Static filter: Fixed date range\nAlways shows the same time period`;
  }
}

const toggleProviderDropdown = () => {
  showProviderDropdown.value = !showProviderDropdown.value
  showChannelDropdown.value = false // Close other dropdown
}

const toggleChannelDropdown = () => {
  showChannelDropdown.value = !showChannelDropdown.value
  showProviderDropdown.value = false // Close other dropdown
}

// Close dropdowns when clicking outside
const closeDropdowns = () => {
  showProviderDropdown.value = false
  showChannelDropdown.value = false
}

// Add click outside listener
onMounted(() => {
  document.addEventListener('click', (e) => {
    const target = e.target
    const providerDropdown = target.closest('.custom-multiselect')
    if (!providerDropdown) {
      closeDropdowns()
    }
  })

  // Sync with store on mount
  localFilters.value = { ...projectsStore.filters };
  projectsStore.fetchQuickFilters();
})

// Initialize
onMounted(() => {
  // Event listener for closing dropdowns when clicking outside
  document.addEventListener('click', (e) => {
    const target = e.target
    const multiselect = target.closest('.custom-multiselect')
    if (!multiselect) {
      closeDropdowns()
    }
  })

  // Sync with store on mount
  localFilters.value = { ...projectsStore.filters };
  projectsStore.fetchQuickFilters();
})
</script>

<style scoped>
.project-filters {
  background: white;
  border-radius: 8px;
  padding: 1.5rem;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
  margin-bottom: 1.5rem;
}

.filters-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.5rem;
}

.filters-header h3 {
  margin: 0;
  color: #374151;
  font-size: 1.25rem;
  font-weight: 600;
}

.header-actions {
  display: flex;
  gap: 0.75rem;
  align-items: center;
}

.workflow-btn {
  background: linear-gradient(135deg, #4f46e5, #7c3aed);
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.875rem;
  font-weight: 500;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.workflow-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #4338ca, #6d28d9);
  transform: translateY(-1px);
  box-shadow: 0 4px 8px rgba(79, 70, 229, 0.3);
}

.workflow-btn:disabled {
  background: #9ca3af;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.workflow-btn .spinner {
  font-size: 1rem;
}

.email-ingest-btn {
  background: linear-gradient(135deg, #059669, #0d9488);
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.875rem;
  font-weight: 500;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.email-ingest-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #047857, #0f766e);
  transform: translateY(-1px);
  box-shadow: 0 4px 8px rgba(5, 150, 105, 0.3);
}

.email-ingest-btn:disabled {
  background: #9ca3af;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.rss-ingest-btn {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.875rem;
  font-weight: 500;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.rss-ingest-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #059669, #047857);
  transform: translateY(-1px);
  box-shadow: 0 4px 8px rgba(16, 185, 129, 0.3);
}

.rss-ingest-btn:disabled {
  background: #9ca3af;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.full-workflow-btn {
  background: linear-gradient(135deg, #7c3aed, #a855f7);
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.875rem;
  font-weight: 500;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.full-workflow-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #6d28d9, #9333ea);
  transform: translateY(-1px);
  box-shadow: 0 4px 8px rgba(124, 58, 237, 0.3);
}

.full-workflow-btn:disabled {
  background: #9ca3af;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.reset-btn {
  background: #6b7280;
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.875rem;
  font-weight: 500;
  transition: background-color 0.2s;
}

.reset-btn:hover:not(:disabled) {
  background: #4b5563;
}

.reset-btn:disabled {
  background: #d1d5db;
  cursor: not-allowed;
}

.filters-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1.5rem;
  margin-bottom: 1.5rem;
}

/* Large screen optimization for filters */
@media (min-width: 1440px) {
  .filters-grid {
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 2rem;
  }

  .checkbox-grid {
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  }
}

.filter-group {
  display: flex;
  flex-direction: column;
}

.filter-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #374151;
  margin-bottom: 0.5rem;
}

.filter-input, .filter-select {
  padding: 0.5rem;
  border: 1px solid #d1d5db;
  border-radius: 0.375rem;
  font-size: 0.875rem;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.filter-select[multiple] {
  min-height: 2.5rem;
  max-height: 8rem;
  overflow-y: auto;
}

.filter-input:focus, .filter-select:focus {
  outline: none;
  border-color: #4f46e5;
  box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1);
}

.checkbox-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 0.5rem;
}

.checkbox-label {
  display: flex;
  align-items: center;
  cursor: pointer;
  font-size: 0.875rem;
  color: #374151;
}

.checkbox-label input[type="checkbox"] {
  margin-right: 0.5rem;
  accent-color: #4f46e5;
}

.checkbox-text {
  user-select: none;
}

.score-range, .date-range {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.date-range-container {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.quick-date-select {
  width: 100%;
  margin-bottom: 0.5rem;
}

.score-input, .date-input {
  flex: 1;
}

.score-ranges-container {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.score-range-group {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.score-type-label {
  font-size: 0.875rem;
  font-weight: 500;
  color: #374151;
  min-width: 70px;
}

.date-input:disabled {
  background-color: #f9fafb;
  cursor: not-allowed;
  opacity: 0.6;
}

.range-separator {
  color: #6b7280;
  font-size: 0.875rem;
}


.active-filters {
  border-top: 1px solid #e5e7eb;
  padding-top: 1rem;
  margin-bottom: 1.5rem;
}

.active-filters h4 {
  margin: 0 0 0.5rem 0;
  font-size: 0.875rem;
  font-weight: 600;
  color: #374151;
}

.filter-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.filter-tag {
  display: inline-flex;
  align-items: center;
  background: #e0e7ff;
  color: #3730a3;
  padding: 0.25rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  font-weight: 500;
}

.tag-remove {
  background: none;
  border: none;
  color: #3730a3;
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
  margin-left: 0.25rem;
  padding: 0;
  opacity: 0.7;
  transition: opacity 0.2s;
}

.tag-remove:hover {
  opacity: 1;
}

.quick-filters {
  border-top: 1px solid #e5e7eb;
  padding-top: 1rem;
}

.quick-filters h4 {
  margin: 0 0 0.5rem 0;
  font-size: 0.875rem;
  font-weight: 600;
  color: #374151;
}

.quick-filter-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.quick-filter-btn {
  background: #f3f4f6;
  color: #374151;
  border: 1px solid #d1d5db;
  padding: 0.375rem 0.75rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 500;
  transition: all 0.2s;
}

.quick-filter-btn:hover {
  background: #e5e7eb;
  border-color: #9ca3af;
}

.filter-type-icon {
  margin-right: 0.25rem;
  font-size: 0.875rem;
}

.filter-type-indicator {
  margin-right: 0.25rem;
}

.quick-filters-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
}

.save-filter-btn {
  background: #10b981;
  color: white;
  border: none;
  padding: 0.375rem 0.75rem;
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 500;
  transition: background-color 0.2s;
}

.save-filter-btn:hover {
  background: #059669;
}

.quick-filter-item {
  display: flex;
  align-items: center;
}

.delete-filter-btn {
  background: #ef4444;
  color: white;
  border: none;
  border-radius: 50%;
  width: 20px;
  height: 20px;
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
  margin-left: 0.5rem;
  opacity: 0.7;
  transition: opacity 0.2s;
}

.delete-filter-btn:hover {
  opacity: 1;
}

.edit-filter-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1rem;
  opacity: 0.6;
  transition: opacity 0.2s;
  padding: 0 0.25rem;
}

.edit-filter-btn:hover {
  opacity: 1;
}

/* Custom Multi-Select Styles */
.custom-multiselect {
  position: relative;
}

.multiselect-trigger {
  padding: 0.5rem;
  border: 1px solid #d1d5db;
  border-radius: 0.375rem;
  background: white;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.875rem;
  transition: border-color 0.2s, box-shadow 0.2s;
  min-height: 2.5rem;
}

.multiselect-trigger:hover {
  border-color: #9ca3af;
}

.multiselect-trigger:focus {
  outline: none;
  border-color: #4f46e5;
  box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1);
}

.placeholder {
  color: #9ca3af;
}

.selected-summary {
  color: #374151;
  font-weight: 500;
}

.dropdown-arrow {
  color: #6b7280;
  font-size: 0.75rem;
  transition: transform 0.2s;
}

.custom-multiselect.active .dropdown-arrow {
  transform: rotate(180deg);
}

.multiselect-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: white;
  border: 1px solid #d1d5db;
  border-radius: 0.375rem;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
  z-index: 10;
  max-height: 12rem;
  overflow-y: auto;
  margin-top: 0.25rem;
}

.multiselect-option {
  display: flex;
  align-items: center;
  padding: 0.5rem;
  cursor: pointer;
  font-size: 0.875rem;
  color: #374151;
  transition: background-color 0.2s;
}

.multiselect-option:hover {
  background: #f9fafb;
}

.multiselect-option input[type="checkbox"] {
  margin-right: 0.5rem;
  accent-color: #4f46e5;
}

.option-text {
  user-select: none;
}

/* Responsive design */
@media (max-width: 768px) {
  .filters-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 1rem;
  }

  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }

  .filters-grid {
    grid-template-columns: 1fr;
    gap: 1rem;
  }

  .checkbox-grid {
    grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  }

  .score-range, .date-range {
    flex-direction: column;
    align-items: stretch;
  }

  .score-ranges-container {
    gap: 0.5rem;
  }

  .score-range-group {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.25rem;
  }

  .score-type-label {
    min-width: auto;
  }

  .date-range-container {
    gap: 0.75rem;
  }

  .range-separator {
    align-self: center;
  }

  .quick-filter-buttons {
    flex-direction: column;
  }

  .quick-filter-btn {
    width: 100%;
    text-align: center;
  }
}
</style>