import { ref, computed, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { zipvoiceApi } from '../api'

/**
 * 本地语音合成模型（ZipVoice）下载的共享逻辑：状态查询 + 启动下载 + 进度轮询。
 *
 * 与 useAsr（Whisper）**同构**：后端为单例后台下载线程，前端按 1s 轮询
 * /zipvoice/download/status。模型约 195MB（首次下载较久），故卡片上要显示
 * 「已下载 X / 共 Y MB」，让用户知道进度是真的在走。
 */
export function useZipvoice() {
  const models = ref([])
  const loading = ref(false)
  const downloading = ref(false)
  const progress = ref(0)
  const downloadedMb = ref(0)
  const totalMb = ref(0)
  const error = ref('')

  const installed = computed(() => models.value.some((m) => m.installed))
  // 单一模型：下载中不区分 size（与 ASR 的多版本不同）
  const dlSize = ref('')

  let timer = null

  async function load() {
    loading.value = true
    try {
      const { data } = await zipvoiceApi.models()
      models.value = data.models || []
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

  async function download() {
    error.value = ''
    try {
      await zipvoiceApi.download()
      downloading.value = true
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
      const { data } = await zipvoiceApi.downloadStatus()
      downloading.value = !!data.downloading
      progress.value = data.progress || 0
      downloadedMb.value = data.downloaded_mb || 0
      totalMb.value = data.total_mb || 0
      if (data.status === 'error') error.value = data.error || '模型下载失败'
      if (data.downloading) {
        schedulePoll()
        return
      }
      if (data.status === 'done') ElMessage.success('语音合成模型下载完成')
      await load()   // 刷新安装状态
    } catch (e) {
      schedulePoll() // 轮询偶发失败继续重试
    }
  }

  onUnmounted(() => clearTimeout(timer))

  return {
    models, installed, loading, downloading, dlSize, progress,
    downloadedMb, totalMb, error, load, download,
  }
}
