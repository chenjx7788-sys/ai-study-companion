<template>
  <el-image-viewer v-if="state.show" :url-list="state.urls" :initial-index="state.index"
    teleported @close="closeImgViewer" />
</template>

<script setup>
import { imgViewerState as state, closeImgViewer } from '../utils/imgViewer'
</script>
