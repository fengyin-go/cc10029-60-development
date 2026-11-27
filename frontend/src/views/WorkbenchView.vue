<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button
          v-if="meta.key === 'textbook_arrival'"
          class="btn primary"
          data-testid="open-upload"
          type="button"
          @click="openUpload"
        >
          上传到货文件
        </button>
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div v-if="importReceipt" class="receipt-banner" data-testid="import-receipt">
      <div class="receipt-head">
        <strong>{{ importReceipt.message }}</strong>
        <button class="link" type="button" @click="importReceipt = null">关闭</button>
      </div>
      <div class="receipt-grid">
        <span>订单：{{ importReceipt.receipt.订单编号 || '—' }}</span>
        <span>回单：{{ importReceipt.receipt.回单编号 || '—' }}</span>
        <span>回单状态：{{ importReceipt.receipt.回单状态 }}</span>
        <span>新增：{{ importReceipt.receipt.新增行数 }}</span>
        <span>重复：{{ importReceipt.receipt.重复行数 }}</span>
        <span>跳过：{{ importReceipt.receipt.跳过行数 }}</span>
        <span>已核对：{{ importReceipt.receipt.已核对数量 }} 册</span>
        <span v-if="importReceipt.receipt.重复导入" class="warn-text">同一回单重复导入，未重复计数</span>
      </div>
      <ul v-if="importReceipt.receipt.跳过明细.length" class="receipt-skip">
        <li v-for="item in importReceipt.receipt.跳过明细" :key="item.行号">
          第 {{ item.行号 }} 行：{{ item.原因 }}
        </li>
      </ul>
    </div>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :data-testid="`row-${row.id}`"
          :data-status="String(row.status)"
          :class="{ 'row-abnormal': Boolean(row.abnormal) }"
        >
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showUpload" class="modal-backdrop" data-testid="upload-modal">
      <form class="modal" @submit.prevent="submitUpload">
        <h3>上传到货文件并核对导入回执</h3>
        <p class="page-desc">
          支持 CSV 表头：订单编号、回单编号、到货批次、教材名称、适用课程、到货数量。
        </p>
        <label class="create-field">
          <span>到货文件</span>
          <input data-testid="upload-file" type="file" accept=".csv,text/csv" @change="onFileChange" />
        </label>
        <label v-for="field in uploadFields" :key="field.key" class="create-field">
          <span>{{ field.label }}</span>
          <input v-model="uploadForm[field.key]" :data-testid="`upload-${field.key}`" :placeholder="field.placeholder" />
        </label>
        <label class="create-field">
          <span>或直接粘贴回单行（每行一条，字段用逗号分隔）</span>
          <textarea v-model="pastedRows" data-testid="upload-paste" rows="4" class="paste-area" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showUpload = false">取消</button>
          <button class="btn primary" data-testid="submit-upload" type="submit" :disabled="uploading">
            {{ uploading ? '导入中…' : '导入并核对回执' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="actionPrompt" class="modal-backdrop" data-testid="action-modal">
      <form class="modal" @submit.prevent="submitActionPrompt">
        <h3>{{ actionPrompt.action }}：{{ actionPrompt.row.教材名称 ?? actionPrompt.row.id }}</h3>
        <p class="page-desc">{{ actionPrompt.hint }}</p>
        <label v-for="field in actionPrompt.fields" :key="field.key" class="create-field">
          <span>{{ field.label }}</span>
          <input
            v-model="actionPrompt.values[field.key]"
            :data-testid="`action-field-${field.key}`"
            :placeholder="field.placeholder"
          />
        </label>
        <p v-if="actionPrompt.row.采用资格" class="page-desc">
          当前采用资格：{{ actionPrompt.row.采用资格 }}
        </p>
        <div class="page-actions">
          <button class="btn" type="button" @click="actionPrompt = null">取消</button>
          <button class="btn primary" data-testid="submit-action" type="submit" :disabled="creating">
            确认{{ actionPrompt.action }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  importArrivalReceipt,
  listEntries,
  moduleMeta,
  runAction as applyAction,
  uploadArrivalFile,
} from '@/api/backend-service'
import type { ArrivalReceipt, EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value:
      meta.key === 'textbook_arrival'
        ? rows.value.filter((row) => row.status === '缺货').length
        : rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

// ---- 到货文件上传 ----
const showUpload = ref(false)
const uploading = ref(false)
const uploadFile = ref<File | null>(null)
const uploadForm = ref<Record<string, string>>({ order_no: '', receipt_no: '', course: '', textbook: '' })
const pastedRows = ref('')
const uploadFields = [
  { key: 'order_no', label: '订单编号', placeholder: '如 H-31' },
  { key: 'receipt_no', label: '回单编号', placeholder: '如 RCPT-H31-02' },
  { key: 'course', label: '适用课程', placeholder: '如 数据结构' },
  { key: 'textbook', label: '教材名称', placeholder: '如 数据结构（第2版）' },
]
const importReceipt = ref<ArrivalReceipt | null>(null)

function openUpload() {
  showUpload.value = true
  uploadFile.value = null
  uploadForm.value = { order_no: '', receipt_no: '', course: '', textbook: '' }
  pastedRows.value = ''
  errorMessage.value = ''
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  uploadFile.value = input.files?.[0] ?? null
}

function parsePastedRows(): Record<string, string>[] {
  const header = ['订单编号', '回单编号', '到货批次', '教材名称', '适用课程', '到货数量']
  return pastedRows.value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const cells = line.split(',').map((cell) => cell.trim())
      return Object.fromEntries(header.map((key, index) => [key, cells[index] ?? '']))
    })
}

async function submitUpload() {
  uploading.value = true
  errorMessage.value = ''
  try {
    let result: ArrivalReceipt
    if (uploadFile.value) {
      result = await uploadArrivalFile(uploadFile.value, uploadForm.value)
    } else {
      result = await importArrivalReceipt({
        ...uploadForm.value,
        rows: parsePastedRows(),
      })
    }
    importReceipt.value = result
    showUpload.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回单导入失败'
  } finally {
    uploading.value = false
  }
}

// ---- 动作：替换教材需要补填字段 ----
type ActionPrompt = {
  action: string
  row: EntryRow
  hint: string
  fields: { key: string; label: string; placeholder: string }[]
  values: Record<string, string>
}
const actionPrompt = ref<ActionPrompt | null>(null)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  if (action === '确认替换教材') {
    actionPrompt.value = {
      action,
      row,
      hint: '缺货导致已核对数量不足时，请先确认替换教材，再提交审批。',
      fields: [
        { key: '替换教材', label: '替换教材', placeholder: '填写替代教材名称' },
        { key: '替换依据', label: '替换依据', placeholder: '如教务会议纪要编号' },
      ],
      values: { 替换教材: '', 替换依据: '' },
    }
    return
  }
  if (action === '更正缺货') {
    actionPrompt.value = {
      action,
      row,
      hint: '更正后该批次不再计入已核对数量，并同步刷新同课程大纲审批待办。',
      fields: [
        { key: '缺货证明说明', label: '缺货证明', placeholder: '如出版社延期通知编号/证明说明' },
      ],
      values: { 缺货证明说明: '' },
    }
    return
  }
  await performAction(action, row)
}

async function performAction(action: string, row: EntryRow, values: Record<string, string> = {}) {
  try {
    const result = await applyAction(meta.key, Number(row.id), action, values)
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function submitActionPrompt() {
  if (!actionPrompt.value) return
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await applyAction(
      meta.key,
      Number(actionPrompt.value.row.id),
      actionPrompt.value.action,
      actionPrompt.value.values,
    )
    if (!result.ok) throw new Error(result.message)
    actionPrompt.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  } finally {
    creating.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
