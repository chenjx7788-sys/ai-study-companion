<template>
  <div class="page-head">
    <div class="page-head-icon" aria-hidden="true">
      <slot name="icon" />
    </div>
    <div class="page-head-title">
      <h2>{{ title }}</h2>
      <p v-if="sub" class="page-head-sub">{{ sub }}</p>
    </div>
    <div v-if="$slots.default" class="page-head-actions">
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  sub: { type: String, default: '' }
})
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 18px;
}
.page-head-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--asc-primary);
  background: linear-gradient(150deg, rgba(124, 92, 252, .16), rgba(124, 92, 252, .06));
  border: 1px solid rgba(124, 92, 252, .22);
}
.page-head-icon :deep(svg) { display: block; }
.page-head-title {
  display: flex;
  flex-direction: column;
  gap: 3px;
  flex: 1;
  min-width: 0;
}
.page-head-title h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: .2px;
  color: var(--asc-text);
}
.page-head-sub {
  margin: 0;
  font-size: 12.5px;
  color: var(--asc-text-3);
  line-height: 1.5;
}
.page-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  margin-left: auto;
}
</style>
