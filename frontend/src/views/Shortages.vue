<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
const stats = ref<any>({})
const meta = ref<any>(null)
async function load() {
  // 缺料贴与备料台建议列读的是同一份最新快照，不单独重算。
  const res = await api('/prep/shortages?order_id=1')
  rows.value = res.shortages; stats.value = res.stats
  meta.value = { run_id: res.run_id, status: res.status }
}
onMounted(load)
</script>
<template>
  <h1>缺料便利贴</h1>
  <p class="sub">shortage = need − stock（仅正数）· 与建议列同一份快照</p>
  <div class="kp-shortage-sticky" style="max-width:360px;transform:rotate(-1deg);margin-bottom:1rem">
    <h2>
      ⚠ 缺料 {{ stats.shortage_count }} · 合计 {{ stats.total_shortage_qty }}
      <small v-if="meta">· 快照 #{{ meta.run_id }}（{{ meta.status === 'placed' ? '已落成单' : '建议' }}）</small>
    </h2>
    <div v-for="r in rows" :key="r.ingredient_id" class="kp-shortage-item">
      <span>{{ r.ingredient_name }}</span>
      <span class="kp-qty">−{{ r.shortage }} {{ r.unit }}</span>
    </div>
  </div>
  <div class="card">
    <table>
      <thead><tr><th>原料</th><th>需求</th><th>结存</th><th>缺料</th><th>单位</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.ingredient_id">
          <td>{{ r.ingredient_name }}</td><td>{{ r.need_qty }}</td><td>{{ r.stock_qty }}</td>
          <td><span class="badge badge-bad">{{ r.shortage }}</span></td><td>{{ r.unit }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
