<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

// 唯一事实源：snapshot 这一个响应对象。
// 建议列读 snapshot.prep_lines，缺料贴读 snapshot.shortages，落单回执整体替换它。
// 三处永远是同一快照 id、同一瞬间算出来的同一套数。
const snapshot = ref<any>(null)
const tree = ref<any[]>([])
const orders = ref<any[]>([])
const loading = ref(false)
const placing = ref(false)
const refreshError = ref('')   // 建议列刷新失败时非空 → 禁止落单
const stale = ref(false)        // 服务端判定建议列已过期 → 必须先刷新
const notice = ref('')

const shortages = computed(() => snapshot.value?.shortages ?? [])
const canPlace = computed(() =>
  !!snapshot.value && snapshot.value.status === 'suggested'
  && !loading.value && !placing.value && !refreshError.value && !stale.value,
)

function errText(e: any): string {
  try {
    const d = JSON.parse(e?.message || '{}')
    return d?.detail?.message || d?.detail || e?.message || '请求失败'
  } catch { return e?.message || '请求失败' }
}

async function refresh() {
  // 已落单的订单不再生成新建议列：旧单冻结，要改只能开新订单。
  if (snapshot.value?.status === 'placed') {
    notice.value = '该订单已落成单，旧单不可改；如需调整请开新订单。'
    return
  }
  loading.value = true
  refreshError.value = ''
  stale.value = false
  notice.value = ''
  try {
    // 一次请求拿回同一快照的建议列 + 缺料 + 统计，不另发 shortages 请求。
    snapshot.value = await api('/prep/suggest?order_id=1', { method: 'POST' })
  } catch (e: any) {
    // 可能订单此前已在别处落单：回退拉取落成单展示，而不是留一张空白台。
    await loadLatest()
    if (snapshot.value?.status === 'placed') {
      notice.value = '该订单已有落成的备料单（快照 #' + snapshot.value.id + '），旧单不可改。'
    } else {
      refreshError.value = '建议列刷新失败：' + errText(e) + '。刷新成功前禁止落单。'
    }
  } finally {
    loading.value = false
  }
}

async function place() {
  if (!canPlace.value || !snapshot.value) return
  placing.value = true
  notice.value = ''
  try {
    // 落单只递交快照令牌；服务端在锁内重算校验，绝不使用页面上的任何数字。
    const placed = await api('/prep/place', {
      method: 'POST',
      body: JSON.stringify({ run_id: snapshot.value.id }),
    })
    snapshot.value = placed   // 同一快照被置为 placed：数字一行不变，只改状态
    notice.value = '已落成单（快照 #' + placed.id + '）。旧单不再改动，如需新单请重新生成建议列。'
  } catch (e: any) {
    const code = (() => { try { return JSON.parse(e?.message).detail?.code } catch { return '' } })()
    if (code === 'STALE_SUGGESTION') {
      stale.value = true
      refreshError.value = errText(e) + '。已禁止用这份过期建议列落单。'
    } else if (code === 'ALREADY_PLACED' || code === 'ORDER_PLACED') {
      notice.value = errText(e)
      await loadLatest()
    } else {
      refreshError.value = '落单失败：' + errText(e)
    }
  } finally {
    placing.value = false
  }
}

async function loadLatest() {
  try { snapshot.value = await api('/prep/latest?order_id=1') } catch { /* 保留现状 */ }
}

onMounted(async () => {
  // 先进台就先列建议缺料：挂载即刷一份全新快照。
  const [t, o] = await Promise.all([api('/bom/tree'), api('/orders')])
  tree.value = t; orders.value = o
  await refresh()
})
</script>
<template>
  <h1>备料工作台</h1>
  <p class="sub">左 BOM 树 · 中备料表 · 右缺料便利贴 · 顶栏订单芯片</p>
  <div class="kp-chips" style="margin-bottom:0.75rem" v-if="orders.length">
    <span v-for="o in orders" :key="o.id" class="kp-chip" style="cursor:default">
      {{ o.code }} · {{ o.outlet }}
    </span>
  </div>

  <div class="kp-actions">
    <button class="btn" @click="refresh" :disabled="loading">
      {{ loading ? '刷新中…' : (snapshot ? '重新生成建议列' : '生成建议列') }}
    </button>
    <button class="btn kp-place" @click="place" :disabled="!canPlace">
      {{ placing ? '落单中…' : '按建议列落单' }}
    </button>
    <span v-if="snapshot" class="kp-snapmeta">
      快照 #{{ snapshot.id }} ·
      <span :class="snapshot.status === 'placed' ? 'badge badge-ok' : 'badge badge-warn'">
        {{ snapshot.status === 'placed' ? '已落成单' : '建议（待落单）' }}
      </span>
      · {{ snapshot.created_at }}
    </span>
  </div>
  <p v-if="refreshError" class="kp-error">⚠ {{ refreshError }}</p>
  <p v-if="notice" class="kp-oknote">✓ {{ notice }}</p>
  <p v-if="snapshot && snapshot.status === 'suggested'" class="kp-hint">
    建议列、缺料贴、落单结果同属快照 #{{ snapshot.id }}。改了结存请先重新生成建议列，否则落单会被拒绝。
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

    <section class="kp-worksheet" v-if="snapshot">
      <h2>备料单 · {{ snapshot.order?.code }} · {{ snapshot.order?.outlet }}</h2>
      <table>
        <thead><tr><th>原料</th><th>需求</th><th>结存</th><th>单位</th></tr></thead>
        <tbody>
          <tr v-for="l in snapshot.prep_lines" :key="l.ingredient_id">
            <td>{{ l.ingredient_name }}</td><td>{{ l.need_qty }}</td><td>{{ l.stock_qty }}</td><td>{{ l.unit }}</td>
          </tr>
        </tbody>
      </table>
    </section>
    <section v-else-if="!loading" class="kp-worksheet kp-empty">
      <h2>备料单</h2>
      <p class="muted">建议列尚未生成，点击「生成建议列」。</p>
    </section>

    <aside class="kp-shortage-sticky">
      <h2>⚠ 缺料便利贴 <small v-if="snapshot">· #{{ snapshot.id }}</small></h2>
      <div v-for="r in shortages" :key="r.ingredient_id" class="kp-shortage-item">
        <span>{{ r.ingredient_name }}</span>
        <span class="kp-qty">−{{ r.shortage }} {{ r.unit }}</span>
      </div>
      <p v-if="!shortages.length" style="font-size:0.8rem;margin:0.5rem 0 0">暂无缺料</p>
    </aside>
  </div>
</template>

<style scoped>
.kp-actions { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.kp-place { background: var(--kp-ok, #2f6b3a); color: #f4efe8; }
.kp-snapmeta { font-size: 0.75rem; color: var(--kp-muted, #8a8078); }
.kp-error { color: var(--kp-bad, #b33a2b); font-weight: 700; font-size: 0.85rem; margin: 0.5rem 0; }
.kp-oknote { color: var(--kp-ok, #2f6b3a); font-weight: 700; font-size: 0.85rem; margin: 0.5rem 0; }
.kp-hint { font-size: 0.78rem; color: var(--kp-muted, #8a8078); margin: 0.35rem 0; }
.kp-empty .muted { font-size: 0.85rem; }
</style>
