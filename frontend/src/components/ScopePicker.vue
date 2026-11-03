<template>
  <div class="scope-row">
    <el-radio-group v-model="scope" size="small" class="scope-picker">
      <el-radio-button value="all">整个知识库</el-radio-button>
      <el-radio-button value="material">资料</el-radio-button>
      <el-radio-button value="note">笔记</el-radio-button>
      <el-radio-button value="folder">文件夹</el-radio-button>
    </el-radio-group>
    <!-- 指定条目：空 = 全部（对话）／空 = 无效（陪练，见 allow-empty-all） -->
    <el-select v-if="scope === 'material'" v-model="materialIds" multiple filterable
      :placeholder="allowEmptyAll ? '全部资料（可点选指定）' : '选择资料（至少 1 项）'"
      size="small" class="scope-select">
      <el-option v-for="m in materialOptions" :key="m.id" :label="m.title" :value="m.id" />
    </el-select>
    <el-select v-if="scope === 'note'" v-model="noteIds" multiple filterable
      :placeholder="allowEmptyAll ? '全部笔记（可点选指定）' : '选择笔记（至少 1 项）'"
      size="small" class="scope-select">
      <el-option v-for="n in noteOptions" :key="n.id" :label="n.title" :value="n.id" />
    </el-select>
    <el-select v-if="scope === 'folder'" v-model="folderIds" multiple filterable
      placeholder="选择文件夹（含子文件夹）" size="small" class="scope-select">
      <el-option v-for="f in folderOptions" :key="f.id" :label="f.name" :value="f.id" />
    </el-select>
  </div>
</template>

<script setup>
// 检索范围选择器（AI 对话 / AI 陪练共用）
// 研发方案 F1：从 ChatView.vue 抽取；模板与样式按原样搬移，只增属性、不改交互语义。
//
// ⚠️ 契约（两个调用方都必须遵守）：
//   - v-model:scope        —— 'all' | 'material' | 'note' | 'folder'
//   - v-model:material-ids —— 资料范围下点选的 id 数组
//   - v-model:note-ids     —— 笔记范围下点选的 id 数组
//   - v-model:folder-ids   —— 文件夹范围下点选的 id 数组
//   - props.materialOptions / noteOptions / folderOptions 只读，由父组件按需加载。
//
// ⚠️ allow-empty-all 是两个调用方的**语义分水岭**（默认 true = 对话的既有行为）：
//   - true （AI 对话）：空 = 「全部」。取消勾选后自动切回「整个知识库」——否则停留在
//     「资料/笔记」范围，检索被收窄（资料范围只查原文、笔记范围只查笔记），无法全库回答。
//   - false（AI 陪练）：空 = **范围无效**。PRD 3.1.7 明确规定「未勾选任何条目 → 禁用开始
//     陪练 + 提示『请先选择要陪练的内容』」，**严禁退化为全库**（静默把范围放大到整个
//     知识库正是 PRD 反复警告的坑）。所以这里既不自动回退，也不改默认值。
import { watch } from 'vue'

const scope = defineModel('scope', { type: String, default: 'all' })
const materialIds = defineModel('materialIds', { type: Array, default: () => [] })
const noteIds = defineModel('noteIds', { type: Array, default: () => [] })
const folderIds = defineModel('folderIds', { type: Array, default: () => [] })

const props = defineProps({
  materialOptions: { type: Array, default: () => [] },
  noteOptions: { type: Array, default: () => [] },
  folderOptions: { type: Array, default: () => [] },
  allowEmptyAll: { type: Boolean, default: true },
})

// 仅 allowEmptyAll 时启用「取消所有引用 → 切回整个知识库」
watch(materialIds, (v) => {
  if (props.allowEmptyAll && scope.value === 'material' && v.length === 0) scope.value = 'all'
})
watch(noteIds, (v) => {
  if (props.allowEmptyAll && scope.value === 'note' && v.length === 0) scope.value = 'all'
})
watch(folderIds, (v) => {
  if (props.allowEmptyAll && scope.value === 'folder' && v.length === 0) scope.value = 'all'
})
</script>

<style scoped>
/* 范围选择：安静文字 chip（无边框无分割线，弱化层级）
   ⚠️ 样式随组件一起抽取（scoped 样式不会跨组件边界，留在 ChatView 里会失效） */
.scope-row {
  display: flex; gap: 8px; align-items: center;
}
.scope-picker {
  display: inline-flex; flex-shrink: 0; gap: 2px;
}
.scope-picker :deep(.el-radio-button) { margin: 0; }
.scope-picker :deep(.el-radio-button__inner) {
  border: none !important; background: transparent !important; box-shadow: none !important;
  color: var(--asc-text-3); font-size: 12.5px; padding: 4px 11px;
  border-radius: 8px; transition: all .18s ease;
}
.scope-picker :deep(.el-radio-button__inner:hover) {
  background: var(--asc-surface-2) !important; color: var(--asc-text-2) !important;
}
.scope-picker :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: var(--asc-primary-soft) !important; color: var(--asc-primary) !important; font-weight: 600;
  box-shadow: none;
}
/* ⚠️ min-width 必须给足（2026-11-02 实测）：父链是 shrink-to-fit 的 flex，
   只写 flex:1 时「选 1 项 + 点空白收起下拉」会把选择器压到 38px、tag 只剩 10px
   （条目名完全看不见，用户截图反馈）。220px 保底能放下一行条目名。 */
.scope-select { flex: 1; min-width: 220px; }
/* 选中的资料/笔记文件名标签：主题紫高亮 */
.scope-select :deep(.el-tag) {
  color: var(--asc-primary); font-weight: 500;
  background: var(--asc-primary-soft); border-color: rgba(124, 92, 252, .35);
}
.scope-select :deep(.el-tag .el-icon) { color: var(--asc-primary); }
.scope-select :deep(.el-select__wrapper) {
  background: transparent;
  border-radius: 9px;
  box-shadow: none;
  min-height: 30px;
  padding: 0 8px;
  transition: background .18s ease, box-shadow .18s ease;
}
.scope-select :deep(.el-select__wrapper:hover) {
  background: var(--asc-surface-2);
}
.scope-select :deep(.el-select__wrapper.is-focused) {
  background: var(--asc-surface-2);
  box-shadow: 0 0 0 1.5px rgba(124, 92, 252, .45);
}
.scope-select :deep(.el-select__placeholder) { color: var(--asc-text-3); }
/* 多选 tag 区限高（2026-11-02 实测：选 8 项时工具栏被撑到 4 行 ~110px，右侧大片空白）。
   限两行半（56px）内部纵滚 —— 露出第三行一截当「还有更多」的线索；
   滚动条常显细条（overlay 滚动条不 hover 不可见，会被误认成截断） */
.scope-select :deep(.el-select__selection) {
  max-height: 56px; overflow-y: auto;
  scrollbar-width: thin; scrollbar-color: #c9c9d4 transparent;
}
.scope-select :deep(.el-select__selection)::-webkit-scrollbar { width: 6px; }
.scope-select :deep(.el-select__selection)::-webkit-scrollbar-thumb { background: #c9c9d4; border-radius: 3px; }
</style>
