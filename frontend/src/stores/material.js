import { defineStore } from 'pinia'

// 材料全局状态：列表缓存、当前学习材料、解析状态轮询
export const useMaterialStore = defineStore('material', {
  state: () => ({
    materials: [],
    currentId: null,
    loading: false
  }),
  actions: {
    setCurrent(id) { this.currentId = id }
  }
})
