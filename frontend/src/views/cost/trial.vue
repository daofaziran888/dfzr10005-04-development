<template>
  <section class="page" data-module="cost-trial">
    <header class="page-head">
      <div>
        <h2>冷数据成本试算</h2>
        <p class="page-desc">按分析范围关联历史热度、分区访问与分层存储策略，演算候选表、建议目标层、预计释放容量与风险项；确认后生成演算快照并同步报告、分层定义与热度记录。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/cost">返回成本报告列表</RouterLink>
      </div>
    </header>

    <section class="panel">
      <header class="panel-head">
        <h3>试算口径（当前版本 {{ caliber?.口径版本 ?? '—' }}）</h3>
        <button class="btn ghost" type="button" @click="showCaliberForm = !showCaliberForm">
          {{ showCaliberForm ? '收起口径调整' : '调整口径' }}
        </button>
      </header>
      <div v-if="caliber" class="caliber-grid">
        <div>
          <span class="stat-label">热度口径</span>
          <p>热分区：近30天查询 ≥ {{ caliber.热度口径.热分区查询阈值 }} 次，或 {{ caliber.热度口径.热分区近访天数 }} 天内有访问</p>
          <p>冷分区：近30天查询 &lt; {{ caliber.热度口径.冷分区查询阈值 }} 次，且 {{ caliber.热度口径.冷分区未访天数 }} 天未访问</p>
        </div>
        <div>
          <span class="stat-label">分层条件（冲突时按管理优先级裁决）</span>
          <p v-for="cond in caliber.分层条件" :key="cond.条件编号">
            P{{ cond.管理优先级 }} · {{ cond.条件名称 }}：{{ cond.依据 }}
          </p>
        </div>
        <div>
          <span class="stat-label">成本口径</span>
          <p>迁移单价 {{ caliber.成本口径.迁移单价元每GB }} 元/GB · 带宽 {{ caliber.成本口径.迁移带宽GB每小时 }} GB/h · 维护窗口 {{ caliber.成本口径.维护窗口小时 }}h</p>
          <p>容量水位阈值 {{ caliber.成本口径.水位阈值百分比 }}% · 试算周期 {{ caliber.成本口径.试算周期月 }} 个月</p>
        </div>
      </div>
      <form v-if="showCaliberForm" class="filter-bar" @submit.prevent="submitCaliber">
        <label class="filter-item">
          <span>热分区查询阈值（次/30天）</span>
          <input v-model="caliberForm.热分区查询阈值" placeholder="留空则不调整" />
        </label>
        <label class="filter-item">
          <span>热分区近访天数</span>
          <input v-model="caliberForm.热分区近访天数" placeholder="留空则不调整" />
        </label>
        <label class="filter-item">
          <span>冷分区查询阈值（次/30天）</span>
          <input v-model="caliberForm.冷分区查询阈值" placeholder="留空则不调整" />
        </label>
        <label class="filter-item">
          <span>冷分区未访天数</span>
          <input v-model="caliberForm.冷分区未访天数" placeholder="留空则不调整" />
        </label>
        <label class="filter-item">
          <span>操作人</span>
          <input v-model="operator" placeholder="操作人" />
        </label>
        <button class="btn primary" type="submit">更新口径版本</button>
        <span class="hint">口径更新后，历史报告仍按原采样口径保留，新试算按新口径核算</span>
      </form>
    </section>

    <section class="panel">
      <header class="panel-head">
        <h3>发起试算</h3>
      </header>
      <form class="filter-bar" @submit.prevent="createTrial">
        <label class="filter-item">
          <span>分析范围</span>
          <select v-model="createForm.scope">
            <option v-for="scope in scopes" :key="scope" :value="scope">{{ scope }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>操作人</span>
          <input v-model="operator" placeholder="操作人" />
        </label>
        <button class="btn primary" type="submit">发起试算</button>
        <span v-if="message" class="hint">{{ message }}</span>
        <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>试算版本</th>
            <th>分析范围</th>
            <th>口径版本</th>
            <th>状态</th>
            <th>创建人</th>
            <th>确认人</th>
            <th>演算快照</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="trial in trials" :key="trial.id" :class="{ 'row-active': selected?.id === trial.id }">
            <td>{{ trial.试算版本 }}</td>
            <td>{{ trial.分析范围 }}</td>
            <td>{{ trial.口径版本 }}</td>
            <td>{{ trial.status }}</td>
            <td>{{ trial.创建人 }}</td>
            <td>{{ trial.确认人 || '—' }}</td>
            <td>{{ trial.演算快照 || '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="selectTrial(trial)">查看</button>
            </td>
          </tr>
          <tr v-if="!trials.length">
            <td colspan="8" class="empty-state">暂无试算版本，请先发起试算</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="selected" class="panel">
      <header class="panel-head">
        <h3>试算详情：{{ selected.试算版本 }}（口径 {{ selected.口径版本 }}）</h3>
        <div class="page-actions">
          <template v-if="selected.status === '待确认'">
            <button class="btn primary" type="button" @click="confirmTrial">确认试算版本</button>
          </template>
          <span v-else class="hint">已由 {{ selected.确认人 }} 于 {{ selected.确认时间 }} 确认，快照 {{ selected.演算快照 }}</span>
        </div>
      </header>

      <div class="stat-row">
        <article v-for="item in summaryCards" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>

      <h4 class="bucket-title">迁移候选（{{ selected.候选.迁移候选.length }} 表，按预计节省降序）</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>候选表</th>
            <th>当前层</th>
            <th>建议目标层</th>
            <th>预计释放容量(GB)</th>
            <th>预计节省(元/月)</th>
            <th>迁移成本(元)</th>
            <th>回本周期(月)</th>
            <th>试算周期净节省(元)</th>
            <th>风险明细</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in selected.候选.迁移候选" :key="item.表名称">
            <td>{{ item.表名称 }}</td>
            <td>{{ item.当前层 }}</td>
            <td>{{ item.建议目标层 }}</td>
            <td>{{ item.预计释放容量GB }}</td>
            <td>{{ item.预计节省元每月 }}</td>
            <td>{{ item.迁移成本元 }}</td>
            <td>{{ item.回本周期月 ?? '—' }}</td>
            <td>{{ item.试算周期净节省元 }}</td>
            <td>{{ item.风险明细.join('；') }}</td>
          </tr>
          <tr v-if="!selected.候选.迁移候选.length">
            <td colspan="9" class="empty-state">本范围内无迁移候选</td>
          </tr>
        </tbody>
      </table>

      <h4 class="bucket-title">冲突裁决（{{ selected.候选.冲突裁决.length }} 表，按管理优先级判定）</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>候选表</th>
            <th>当前层</th>
            <th>建议目标层</th>
            <th>预计节省(元/月)</th>
            <th>冲突条件</th>
            <th>裁决依据</th>
            <th>风险明细</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in selected.候选.冲突裁决" :key="item.表名称">
            <td>{{ item.表名称 }}</td>
            <td>{{ item.当前层 }}</td>
            <td>{{ item.建议目标层 }}</td>
            <td>{{ item.预计节省元每月 }}</td>
            <td>{{ item.冲突条件.join('；') }}</td>
            <td>{{ item.裁决依据 }}</td>
            <td>{{ item.风险明细.join('；') }}</td>
          </tr>
          <tr v-if="!selected.候选.冲突裁决.length">
            <td colspan="7" class="empty-state">本范围内无冲突裁决</td>
          </tr>
        </tbody>
      </table>

      <h4 class="bucket-title">热点保留（{{ selected.候选.热点保留.length }} 表，热点分区不得仅因总量大判冷）</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>候选表</th>
            <th>当前层</th>
            <th>总容量(GB)</th>
            <th>分区统计(热/温/冷)</th>
            <th>裁决依据</th>
            <th>风险明细</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in selected.候选.热点保留" :key="item.表名称">
            <td>{{ item.表名称 }}</td>
            <td>{{ item.当前层 }}</td>
            <td>{{ item.总容量GB }}</td>
            <td>{{ item.分区统计.热 }}/{{ item.分区统计.温 }}/{{ item.分区统计.冷 }}</td>
            <td>{{ item.裁决依据 }}</td>
            <td>{{ item.风险明细.join('；') }}</td>
          </tr>
          <tr v-if="!selected.候选.热点保留.length">
            <td colspan="6" class="empty-state">本范围内无热点保留</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <header class="panel-head">
        <h3>演算快照（{{ snapshots.length }}）</h3>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>快照编号</th>
            <th>试算版本</th>
            <th>口径版本</th>
            <th>分析范围</th>
            <th>成本报告</th>
            <th>确认人</th>
            <th>确认时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="snap in snapshots" :key="snap.id">
            <td>{{ snap.快照编号 }}</td>
            <td>{{ snap.试算版本 }}</td>
            <td>{{ snap.口径版本 }}</td>
            <td>{{ snap.分析范围 }}</td>
            <td>{{ snap.报告编号 }}</td>
            <td>{{ snap.确认人 }}</td>
            <td>{{ snap.确认时间 }}</td>
          </tr>
          <tr v-if="!snapshots.length">
            <td colspan="7" class="empty-state">暂无演算快照，确认试算版本后生成</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>冷数据成本试算 · 报告、分层与热度记录统一引用演算快照</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Dict = Record<string, any>

const ENDPOINT = '/api/cost'

const caliber = ref<Dict | null>(null)
const scopes = ref<string[]>([])
const trials = ref<Dict[]>([])
const snapshots = ref<Dict[]>([])
const selected = ref<Dict | null>(null)
const operator = ref('值班管理员')
const message = ref('')
const errorMessage = ref('')
const showCaliberForm = ref(false)
const createForm = ref({ scope: '全域' })
const caliberForm = ref<Record<string, string>>({
  热分区查询阈值: '',
  热分区近访天数: '',
  冷分区查询阈值: '',
  冷分区未访天数: '',
})

const summaryCards = computed(() => {
  const summary = selected.value?.汇总
  if (!summary) return []
  return [
    { label: '评估表数', value: summary.评估表数 },
    { label: '候选表数', value: summary.候选表数 },
    { label: '预计释放容量(GB)', value: summary.预计释放容量GB },
    { label: '预计节省(元/月)', value: summary.预计节省元每月 },
    { label: '迁移成本(元)', value: summary.迁移成本元 },
    { label: '试算周期净节省(元)', value: summary.试算周期净节省元 },
  ]
})

async function loadCaliber() {
  const response = await request(`${ENDPOINT}/caliber`)
  if (response.ok) caliber.value = await response.json()
}

async function loadScopes() {
  const response = await request(`${ENDPOINT}/scopes`)
  if (response.ok) scopes.value = (await response.json()).scopes ?? []
}

async function loadTrials(keepSelection = true) {
  const response = await request(`${ENDPOINT}/trials`)
  if (!response.ok) return
  trials.value = (await response.json()).items ?? []
  if (keepSelection && selected.value) {
    selected.value = trials.value.find((item) => item.id === selected.value?.id) ?? null
  }
}

async function loadSnapshots() {
  const response = await request(`${ENDPOINT}/snapshots`)
  if (response.ok) snapshots.value = (await response.json()).items ?? []
}

function selectTrial(trial: Dict) {
  selected.value = trial
  message.value = ''
  errorMessage.value = ''
}

async function createTrial() {
  message.value = ''
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/trials`, {
      method: 'POST',
      body: JSON.stringify({ 分析范围: createForm.value.scope, 操作人: operator.value }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    message.value = payload.message
    await loadTrials(false)
    selected.value = payload.entry
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试算生成失败'
  }
}

async function confirmTrial() {
  if (!selected.value) return
  message.value = ''
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/trials/${selected.value.id}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ 操作人: operator.value }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    message.value = payload.message
    await Promise.all([loadTrials(), loadSnapshots()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '试算确认失败'
    await loadTrials()
  }
}

async function submitCaliber() {
  message.value = ''
  errorMessage.value = ''
  const heat: Record<string, string> = {}
  for (const [key, value] of Object.entries(caliberForm.value)) {
    if (value.trim()) heat[key] = value.trim()
  }
  if (!Object.keys(heat).length) {
    errorMessage.value = '请至少填写一项要调整的热度口径'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/caliber`, {
      method: 'POST',
      body: JSON.stringify({ 热度口径: heat, 操作人: operator.value }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    message.value = payload.message
    showCaliberForm.value = false
    await loadCaliber()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '口径更新失败'
  }
}

onMounted(() => {
  void loadCaliber()
  void loadScopes()
  void loadTrials()
  void loadSnapshots()
})
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.panel-head h3 {
  margin: 0;
  font-size: 15px;
}
.caliber-grid {
  display: flex;
  gap: 24px;
  font-size: 13px;
}
.caliber-grid > div {
  flex: 1;
}
.caliber-grid p {
  margin: 4px 0;
  color: #374151;
}
.bucket-title {
  margin: 14px 0 6px;
  font-size: 14px;
}
.hint {
  color: var(--muted);
  font-size: 12px;
}
.row-active td {
  background: #eff6ff;
}
select {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
</style>
