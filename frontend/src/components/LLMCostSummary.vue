<template>
  <div class="llm-cost-summary">
    <div class="cost-header">
      <h3 class="cost-title">🤖 KI-Kostenübersicht</h3>
      <button @click="refresh" class="refresh-btn" :disabled="loading" title="Aktualisieren">
        <span :class="{ spinning: loading }">🔄</span>
      </button>
    </div>

    <div v-if="error" class="cost-error">
      ⚠️ Kostendaten nicht verfügbar
    </div>

    <div v-else class="cost-grid">
      <!-- Heute -->
      <div class="cost-card">
        <div class="cost-value">{{ formatCost(data.cost_today_usd) }}</div>
        <div class="cost-label">Heute</div>
        <div v-if="dailyBudget" class="cost-bar-wrap">
          <div class="cost-bar">
            <div
              class="cost-bar-fill"
              :class="budgetColorClass(data.cost_today_usd, dailyBudget)"
              :style="{ width: budgetPercent(data.cost_today_usd, dailyBudget) + '%' }"
            ></div>
          </div>
          <span class="cost-bar-label">{{ budgetPercent(data.cost_today_usd, dailyBudget) }}% von {{ formatCost(dailyBudget) }}</span>
        </div>
      </div>

      <!-- Monat -->
      <div class="cost-card">
        <div class="cost-value">{{ formatCost(data.cost_month_usd) }}</div>
        <div class="cost-label">Monat</div>
        <div v-if="monthlyBudget" class="cost-bar-wrap">
          <div class="cost-bar">
            <div
              class="cost-bar-fill"
              :class="budgetColorClass(data.cost_month_usd, monthlyBudget)"
              :style="{ width: budgetPercent(data.cost_month_usd, monthlyBudget) + '%' }"
            ></div>
          </div>
          <span class="cost-bar-label">{{ budgetPercent(data.cost_month_usd, monthlyBudget) }}% von {{ formatCost(monthlyBudget) }}</span>
        </div>
      </div>

      <!-- API-Aufrufe Heute -->
      <div class="cost-card cost-card-small">
        <div class="cost-value">{{ data.calls_today }}</div>
        <div class="cost-label">Aufrufe heute</div>
      </div>

      <!-- Cache-Treffer -->
      <div class="cost-card cost-card-small">
        <div class="cost-value">{{ data.cache_hits }}</div>
        <div class="cost-label">Cache-Treffer</div>
      </div>
    </div>

    <div v-if="data.last_updated" class="cost-footer">
      Letzter Aufruf: {{ formatTime(data.last_updated) }}
    </div>
  </div>
</template>

<script>
import { ref, onMounted } from 'vue'
import axios from 'axios'

const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8002'

export default {
  name: 'LLMCostSummary',
  props: {
    dailyBudget: {
      type: Number,
      default: null,  // USD — aus Konfiguration
    },
    monthlyBudget: {
      type: Number,
      default: null,
    },
  },
  setup(props) {
    const loading = ref(false)
    const error = ref(null)
    const data = ref({
      cost_today_usd: 0,
      cost_month_usd: 0,
      calls_total: 0,
      calls_today: 0,
      cache_hits: 0,
      last_updated: null,
    })

    async function refresh() {
      loading.value = true
      error.value = null
      try {
        const res = await axios.get(`${baseURL}/api/v1/llm/costs`)
        data.value = res.data
      } catch (err) {
        error.value = err.message
      } finally {
        loading.value = false
      }
    }

    function formatCost(usd) {
      if (usd === null || usd === undefined) return '—'
      if (usd < 0.001) return `$${(usd * 1000).toFixed(3)}m`
      return `$${Number(usd).toFixed(4)}`
    }

    function budgetPercent(spent, budget) {
      if (!budget || budget <= 0) return 0
      return Math.min(100, Math.round((spent / budget) * 100))
    }

    function budgetColorClass(spent, budget) {
      const pct = budgetPercent(spent, budget)
      if (pct >= 90) return 'bar-danger'
      if (pct >= 70) return 'bar-warning'
      return 'bar-ok'
    }

    function formatTime(ts) {
      if (!ts) return '—'
      try {
        return new Date(ts).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
      } catch {
        return ts
      }
    }

    onMounted(refresh)

    return { loading, error, data, refresh, formatCost, budgetPercent, budgetColorClass, formatTime }
  }
}
</script>

<style scoped>
.llm-cost-summary {
  background: var(--surface, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}

.cost-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.cost-title {
  font-size: 0.9rem;
  font-weight: 600;
  margin: 0;
  color: var(--text-primary, #1a202c);
}

.refresh-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.85rem;
  opacity: 0.7;
}
.refresh-btn:hover { opacity: 1; background: var(--hover, #f7fafc); }
.refresh-btn:disabled { cursor: not-allowed; opacity: 0.4; }

@keyframes spin { to { transform: rotate(360deg); } }
.spinning { display: inline-block; animation: spin 1s linear infinite; }

.cost-error {
  font-size: 0.8rem;
  color: var(--error, #e53e3e);
  padding: 0.25rem 0;
}

.cost-grid {
  display: grid;
  grid-template-columns: 1fr 1fr auto auto;
  gap: 0.75rem;
  align-items: start;
}

@media (max-width: 600px) {
  .cost-grid { grid-template-columns: 1fr 1fr; }
}

.cost-card {
  min-width: 0;
}

.cost-card-small {
  text-align: center;
}

.cost-value {
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--text-primary, #1a202c);
  font-variant-numeric: tabular-nums;
}

.cost-card-small .cost-value {
  font-size: 1.1rem;
}

.cost-label {
  font-size: 0.72rem;
  color: var(--text-muted, #718096);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-top: 1px;
}

.cost-bar-wrap {
  margin-top: 0.3rem;
}

.cost-bar {
  height: 4px;
  background: var(--border, #e2e8f0);
  border-radius: 2px;
  overflow: hidden;
}

.cost-bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.4s ease;
}

.bar-ok    { background: #48bb78; }
.bar-warning { background: #ed8936; }
.bar-danger  { background: #e53e3e; }

.cost-bar-label {
  font-size: 0.68rem;
  color: var(--text-muted, #718096);
  margin-top: 2px;
  display: block;
}

.cost-footer {
  margin-top: 0.6rem;
  font-size: 0.68rem;
  color: var(--text-muted, #718096);
  border-top: 1px solid var(--border, #e2e8f0);
  padding-top: 0.4rem;
}
</style>
