<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const tree = ref<any[]>([])
const data = ref<any>(null)
const orders = ref<any[]>([])
const refreshing = ref(false)
const issuing = ref(false)
const refreshError = ref('')
const issueError = ref('')
const issueOk = ref('')

function fmtTime(iso?: string) {
  return iso ? iso.replace('T', ' ').slice(0, 19) : '—'
}
function errText(e: any): string {
  // FastAPI 409: {"detail":{"reason":...,"message":...}}
  try {
    const j = JSON.parse(e?.message || '')
    if (j?.detail?.message) return j.detail.message
    if (j?.detail) return String(j.detail)
  } catch { /* 非 JSON */ }
  return e?.message || '请求失败'
}

async function refresh() {
  refreshing.value = true
  refreshError.value = ''
  issueError.value = ''
  try {
    // 建议列与缺料贴来自同一响应，绝不分两次取数
    data.value = await api('/prep/run?order_id=1', { method: 'POST' })
  } catch (e: any) {
    // 刷新失败：保留旧数但明确标记，禁止落单
    refreshError.value = '建议列刷新失败，已禁止落单：' + errText(e)
  } finally {
    refreshing.value = false
  }
}

async function issue() {
  if (!data.value) return
  issuing.value = true
  issueError.value = ''
  issueOk.value = ''
  try {
    const res = await api('/prep/' + data.value.id + '/issue?order_id=1', {
      method: 'POST',
      body: JSON.stringify({ version: data.value.version }),
    })
    issueOk.value = `已落单 #${res.id}（v${res.version}）· 已按需求全额扣减结存；落单结果与建议列逐项一致`
    // 落单成功后旧单冻结，立刻按新结存刷出下一轮建议
    await refresh()
  } catch (e: any) {
    // 409：建议列过期 或 落单当时重算不一致；必须重新刷新后才能再落
    issueError.value = errText(e)
  } finally {
    issuing.value = false
  }
}

onMounted(async () => {
  tree.value = await api('/bom/tree')
  orders.value = await api('/orders')
  await refresh()
})
</script>
<template>
  <h1>备料工作台</h1>
  <p class="sub">左 BOM 树 · 中建议备料表 · 右缺料便利贴 · 顶栏订单芯片（三处同源同一快照）</p>
  <div class="kp-chips" style="margin-bottom:0.75rem" v-if="orders.length">
    <span v-for="o in orders" :key="o.id" class="kp-chip" style="cursor:default">
      {{ o.code }} · {{ o.outlet }}
    </span>
  </div>
  <div class="kp-chips">
    <button class="btn" :disabled="refreshing" @click="refresh">
      {{ refreshing ? '刷新中…' : '刷新建议缺料' }}
    </button>
    <button class="btn" :disabled="issuing || !data || data.status !== 'draft' || !!refreshError"
            @click="issue">
      落单
    </button>
    <span v-if="data" class="kp-chip" style="cursor:default">
      建议列 v{{ data.version }} · {{ data.status === 'draft' ? '待落单' : '已落单' }} ·
      数据截至 {{ fmtTime(data.refreshed_at) }}
    </span>
  </div>
  <p v-if="refreshError" class="badge badge-bad" style="display:block;margin-top:0.6rem;padding:0.5rem 0.7rem">
    ⚠ {{ refreshError }}
  </p>
  <p v-if="issueError" class="badge badge-warn" style="display:block;margin-top:0.6rem;padding:0.5rem 0.7rem">
    ⚠ 落单被拒绝：{{ issueError }}。请重新「刷新建议缺料」后再落。
  </p>
  <p v-if="issueOk" class="badge badge-ok" style="display:block;margin-top:0.6rem;padding:0.5rem 0.7rem">
    ✓ {{ issueOk }}
  </p>
  <div class="kp-workbench" style="margin-top:0.85rem">
    <aside class="kp-bom-tree">
      <h2>菜品 / BOM</h2>
      <div v-for="d in tree" :key="d.code" class="kp-dish-node">
        <strong>{{ d.dish }}</strong>
        <span style="font-size:0.7rem;color:#8a8078">{{ d.code }}</span>
        <ul>
          <li v-for="(c,i) in d.children" :key="i">{{ c.ingredient }} · {{ c.qty }} {{ c.unit }}</li>
        </ul>
      </div>
    </aside>
    <section class="kp-worksheet" v-if="data">
      <h2>建议备料 · {{ data.order?.code }} · {{ data.order?.outlet }}</h2>
      <table>
        <thead><tr><th>原料</th><th>需求</th><th>库存</th><th>缺料</th><th>单位</th></tr></thead>
        <tbody>
          <tr v-for="l in data.prep_lines" :key="l.ingredient_id">
            <td>{{ l.ingredient_name }}</td>
            <td>{{ l.need_qty }}</td>
            <td>{{ l.stock_qty }}</td>
            <td><span v-if="l.shortage > 0" class="badge badge-bad">{{ l.shortage }}</span><span v-else>0</span></td>
            <td>{{ l.unit }}</td>
          </tr>
        </tbody>
      </table>
    </section>
    <aside class="kp-shortage-sticky">
      <h2>⚠ 缺料便利贴</h2>
      <template v-if="data">
        <div v-for="r in data.shortages" :key="r.ingredient_id" class="kp-shortage-item">
          <span>{{ r.ingredient_name }}</span>
          <span class="kp-qty">−{{ r.shortage }} {{ r.unit }}</span>
        </div>
        <p v-if="!data.shortages.length" style="font-size:0.8rem;margin:0.5rem 0 0">暂无缺料</p>
      </template>
      <p v-else style="font-size:0.8rem;margin:0.5rem 0 0">建议列尚未就绪</p>
    </aside>
  </div>
</template>
