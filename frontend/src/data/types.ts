/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
  entry?: EntryRow
}

export type ArrivalReceipt = {
  ok: boolean
  message: string
  receipt: {
    订单编号: string
    回单编号: string
    文件名: string
    新增行数: number
    重复行数: number
    跳过行数: number
    跳过明细: { 行号: number; 原因: string }[]
    重复导入: boolean
    回单状态: string
    已核对数量: number
    待核对数量: number
  }
}


export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
