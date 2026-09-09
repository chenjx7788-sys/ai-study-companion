import { ref, computed, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { asrApi } from '../api'

/**
 * 语音模型（Whisper）下载的共享逻辑：状态查询 + 启动下载 + 进度轮询。
 * 后端为单例后台下载线程，前端按 1s 轮询 /asr/download/status。
 */
export function useAsr() {
  const models = ref([])
  const loading = ref(false)
  const downloading = ref(false)
  const dlSize = ref('')
  const progress = ref(0)
  const error = ref('')

  const installed = computed(() => models.value.some((m) => m.installed))

  let timer = null

  async function load() {
    loading.value = true
    try {
      const { data } = await asrApi.models()
      models.value = data.models || []
      // 后端正在下载时恢复轮询（例如切页面后再回来）
      if (data.downloading) {
        downloading.value = true
        dlSize.value = data.downloading_size || ''
        progress.value = data.download_progress || 0
        schedulePoll()
      }
    } catch (e) {
      /* 静默失败，不打断主流程 */
    } finally {
      loading.value = false
    }
  }

  async function download(size) {
    error.value = ''
    try {
      await asrApi.download(size)
      downloading.value = true
      dlSize.value = size
      progress.value = 0
      schedulePoll()
    } catch (e) {
      error.value = e.response?.data?.detail || '下载启动失败，请重试'
    }
  }

  function schedulePoll() {
    clearTimeout(timer)
    timer = setTimeout(poll, 1000)
  }

  async function poll() {
    try {
      const { data } = await asrApi.downloadStatus()
      downloading.value = !!data.downloading
      dlSize.value = data.size || dlSize.value
      progress.value = data.progress || 0
      if (data.status === 'error') error.value = data.error || '模型下载失败'
      if (data.downloading) {
        schedulePoll()
        return
      }
      if (data.status === 'done') ElMessage.success('语音模型下载完成')
      await load()   // 刷新安装状态
    } catch (e) {
      schedulePoll() // 轮询偶发失败继续重试
    }
  }

  onUnmounted(() => clearTimeout(timer))

  return { models, installed, loading, downloading, dlSize, progress, error, load, download }
}
