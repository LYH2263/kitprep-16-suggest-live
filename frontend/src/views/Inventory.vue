<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
const drafts = ref<Record<number, string>>({})
const saving = ref<Record<number, boolean>>({})
const msg = ref<{ id?: number; ok: boolean; text: string } | null>(null)
function errText(e: any): string {
  try {
    const j = JSON.parse(e?.message || '')
    if (j?.detail?.[0]?.msg) return String(j.detail[0].msg)
    if (j?.detail) return String(j.detail)
  } catch { /* 非 JSON */ }
  return e?.message || '保存失败'
}
async function load() {
  rows.value = await api('/inventory')
  for (const r of rows.value) drafts.value[r.id] = String(r.stock_qty)
}
async function save(r: any) {
  const v = Number(drafts.value[r.id])
  if (!Number.isFinite(v) || v < 0) {
    msg.value = { id: r.id, ok: false, text: '结存必须是 ≥0 的数字' }
    drafts.value[r.id] = String(r.stock_qty)
    return
  }
  saving.value[r.id] = true
  msg.value = null
  try {
    const updated = await api('/inventory/' + r.id, {
      method: 'PATCH', body: JSON.stringify({ stock_qty: v }),
    })
    r.stock_qty = updated.stock_qty
    drafts.value[r.id] = String(updated.stock_qty)
    msg.value = { id: r.id, ok: true, text: '已保存；备料台需重新刷新建议列后才能落单' }
  } catch (e: any) {
    // 失败回滚输入，不假装成功
    drafts.value[r.id] = String(r.stock_qty)
    msg.value = { id: r.id, ok: false, text: errText(e) }
  } finally {
    saving.value[r.id] = false
  }
}
onMounted(load)
</script>
<template>
  <h1>库存</h1>
  <p class="sub">中央厨房原料库存 · 修改结存后，备料台旧建议列立即作废、需刷新才能落单</p>
  <p v-if="msg"
     :class="['badge', msg.ok ? 'badge-ok' : 'badge-bad']"
     style="display:block;margin-bottom:0.75rem;padding:0.5rem 0.7rem">
    {{ msg.ok ? '✓ ' : '⚠ ' }}{{ msg.text }}
  </p>
  <div class="card">
    <table>
      <thead><tr><th>编码</th><th>名称</th><th>结存</th><th>单位</th><th></th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.code }}</td><td>{{ r.name }}</td>
          <td><input type="number" min="0" step="0.001" v-model="drafts[r.id]"
                     style="width:7rem" @keyup.enter="save(r)"></td>
          <td>{{ r.unit }}</td>
          <td><button class="btn" :disabled="saving[r.id] || drafts[r.id] === String(r.stock_qty)"
                      @click="save(r)">{{ saving[r.id] ? '保存中…' : '保存' }}</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
