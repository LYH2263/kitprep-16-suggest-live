<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
const editing = ref<Record<number, string>>({})
const saving = ref<number | null>(null)
const error = ref('')
onMounted(load)
async function load() { rows.value = await api('/inventory') }
function cancelEdit(r: any) { delete editing.value[r.id]; error.value = '' }

async function save(r: any) {
  const qty = parseFloat(editing.value[r.id])
  if (!Number.isFinite(qty) || qty < 0) { error.value = '结存必须是非负数字'; return }
  error.value = ''
  saving.value = r.id
  try {
    const updated = await api('/inventory/' + r.id, {
      method: 'PATCH', body: JSON.stringify({ stock_qty: qty }),
    })
    Object.assign(r, updated)
    // 结存一变，备料台任何未刷新的建议列即过期，落单会被服务端拒绝，须重新生成建议列。
    delete editing.value[r.id]
  } catch (e: any) {
    error.value = '保存失败：' + (e?.message || '未知错误')
  } finally {
    saving.value = null
  }
}
</script>
<template>
  <h1>库存</h1>
  <p class="sub">中央厨房原料结存 · 改了结存后，备料台需重新生成建议列才能落单</p>
  <p v-if="error" class="kp-error">{{ error }}</p>
  <div class="card">
    <table>
      <thead><tr><th>编码</th><th>名称</th><th>结存</th><th>单位</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.code }}</td><td>{{ r.name }}</td>
          <td>
            <template v-if="r.id in editing">
              <input v-model="editing[r.id]" type="number" min="0" step="0.001" class="kp-input"
                     @keyup.enter="save(r)" />
            </template>
            <template v-else>{{ r.stock_qty }} <small class="muted">v{{ r.version }}</small></template>
          </td>
          <td>{{ r.unit }}</td>
          <td>
            <template v-if="r.id in editing">
              <button class="btn" :disabled="saving === r.id" @click="save(r)">保存</button>
              <button class="btn kp-ghost" @click="cancelEdit(r)">取消</button>
            </template>
            <button v-else class="btn kp-ghost" @click="editing[r.id] = String(r.stock_qty)">改结存</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.kp-input {
  width: 6rem; padding: 0.25rem 0.4rem;
  background: #fff8ef; border: 1px solid #8a8078; border-radius: 2px;
  font: inherit; color: #2a211a;
}
.kp-ghost { background: transparent; color: var(--kp-accent, #c47a2c); border: 1px solid currentColor; box-shadow: none; }
.kp-error { color: var(--kp-bad, #b33a2b); font-weight: 700; font-size: 0.85rem; }
.muted { color: #8a8078; }
</style>
