import { apiUrl, fetchJson, request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, ArrivalReceipt, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
  values: Record<string, string> = {},
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action, values }),
  })
  return await response.json()
}

/** 上传到货文件：CSV 文件 + 订单/回单等表单字段一并提交。 */
export async function uploadArrivalFile(
  file: File,
  fields: Record<string, string> = {},
): Promise<ArrivalReceipt> {
  const form = new FormData()
  form.append('file', file)
  Object.entries(fields).forEach(([key, value]) => form.append(key, value))
  const response = await fetch(apiUrl('/api/textbook_arrival/upload'), {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail ?? '到货文件上传失败')
  }
  return await response.json()
}

/** 粘贴回单多行文本导入（与上传文件等价，便于批量录入）。 */
export async function importArrivalReceipt(
  payload: Record<string, unknown>,
): Promise<ArrivalReceipt> {
  const response = await request('/api/textbook_arrival/import', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error('回单导入失败')
  return await response.json()
}

export async function listArrivalReceipts(): Promise<Record<string, unknown>[]> {
  return (await fetchJson('/api/textbook_arrival/receipts')) as Record<string, unknown>[]
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}
