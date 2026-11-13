<template>
  <section class="page" data-module="heatmap">
    <header class="page-head">
      <div>
        <h2>数据热度分析管理</h2>
        <p class="page-desc">维护数据热度的查询频率、访问来源与分区热度分析，为数据热度管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记热度记录</button>
        <button class="btn" type="button" @click="exportRows">导出数据热度分析清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="batch-panel">
      <h3 class="batch-title">采样批次追溯</h3>
      <div class="batch-row">
        <article v-for="batch in batches" :key="batch.采样批次" class="batch-card">
          <strong>{{ batch.采样批次 }}</strong>
          <span>{{ batch.记录数 }} 条记录 · 采样于 {{ batch.采样时间 }}</span>
          <span>覆盖：{{ batch.表清单.join('、') }}</span>
          <span :class="{ 'error-text': !batch.引用快照.length }">
            引用快照：{{ batch.引用快照.length ? batch.引用快照.join('、') : '尚未被试算引用' }}
          </span>
        </article>
        <p v-if="!batches.length" class="empty-state">暂无采样批次</p>
      </div>
    </section>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ formatCell(row[column]) }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据热度分析数据，可先登记热度记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据热度分析记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/heatmap'
const columns = ["记录编号", "表名称", "查询次数", "最近查询", "热度等级", "访问来源", "分区热度", "采样批次", "试算引用", "采样时间"]
const actions = ["开始采样", "分析热度", "归档记录"]
const statuses = ["草稿", "已采样", "已分析", "已归档"]
const stats = [{"label": "热度记录总数", "value": 0}, {"label": "已分析记录", "value": 0}, {"label": "已归档记录", "value": 0}]

type Batch = { 采样批次: string; 记录数: number; 表清单: string[]; 采样时间: string; 引用快照: string[] }

const batches = ref<Batch[]>([])

function formatCell(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join('、') : '—'
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

async function loadBatches() {
  try {
    const response = await request(`${ENDPOINT}/batches`)
    if (response.ok) {
      batches.value = (await response.json()).items ?? []
    }
  } catch {
    batches.value = []
  }
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '热度记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据热度分析动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据热度分析操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('热度记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据热度分析列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadBatches()
})
</script>

<style scoped>
.batch-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.batch-title {
  margin: 0 0 8px;
  font-size: 14px;
}
.batch-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.batch-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  color: var(--muted);
  min-width: 260px;
}
.batch-card strong {
  color: #1f2937;
  font-size: 13px;
}
</style>
