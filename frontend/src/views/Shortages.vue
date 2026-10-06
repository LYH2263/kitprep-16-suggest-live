<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
const stats = ref<any>({})
const meta = ref<any>(null)
const loading = ref(false)
const error = ref('')
function fmtTime(iso?: string) {
  return iso ? iso.replace('T', ' ').slice(0, 19) : '—'
}
async function refresh() {
  loading.value = true
  error.value = ''
  try {
    // 与备料台展示同一份 draft 快照（同一接口同源，不另行重算）
    const res = await api('/prep/latest?order_id=1')
    rows.value = res.shortages || []
    stats.value = res.stats || {}
    meta.value = res
  } catch (e: any) {
    error.value = e?.message || '取数失败'
  } finally {
    loading.value = false
  }
}
onMounted(refresh)
</script>
<template>
  <h1>缺料便利贴</h1>
  <p class="sub">shortage = need − stock（仅正数）· 与备料台同一快照</p>
  <div class="kp-chips" style="margin-bottom:0.75rem">
    <button class="btn" :disabled="loading" @click="refresh">{{ loading ? '刷新中…' : '刷新' }}</button>
    <span v-if="meta" class="kp-chip" style="cursor:default">
      建议列 v{{ meta.version }} · 数据截至 {{ fmtTime(meta.refreshed_at) }}
    </span>
  </div>
  <p v-if="error" class="badge badge-bad" style="display:block;margin-bottom:0.75rem;padding:0.5rem 0.7rem">
    ⚠ 快照取数失败：{{ error }}
  </p>
  <div class="kp-shortage-sticky" style="max-width:360px;transform:rotate(-1deg);margin-bottom:1rem">
    <h2>⚠ 缺料 {{ stats.shortage_count }} · 合计 {{ stats.total_shortage_qty }}</h2>
    <div v-for="r in rows" :key="r.ingredient_id" class="kp-shortage-item">
      <span>{{ r.ingredient_name }}</span>
      <span class="kp-qty">−{{ r.shortage }} {{ r.unit }}</span>
    </div>
    <p v-if="!rows.length" style="font-size:0.8rem;margin:0.5rem 0 0">暂无缺料</p>
  </div>
  <div class="card">
    <table>
      <thead><tr><th>原料</th><th>需求</th><th>库存</th><th>缺料</th><th>单位</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.ingredient_id">
          <td>{{ r.ingredient_name }}</td><td>{{ r.need_qty }}</td><td>{{ r.stock_qty }}</td>
          <td><span class="badge badge-bad">{{ r.shortage }}</span></td><td>{{ r.unit }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
