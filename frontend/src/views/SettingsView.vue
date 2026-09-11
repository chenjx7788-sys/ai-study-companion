<template>
  <div class="page settings-page">
    <PageHead title="管理中心" sub="管理 LLM、向量模型与本地数据配置">
      <template #icon>
        <svg viewBox="0 0 16 16" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
          <circle cx="8" cy="8" r="2.2" />
          <path d="M8 1.8v1.7M8 12.5v1.7M1.8 8h1.7M12.5 8h1.7M3.6 3.6l1.2 1.2M11.2 11.2l1.2 1.2M12.4 3.6l-1.2 1.2M4.8 11.2l-1.2 1.2" />
        </svg>
      </template>
    </PageHead>

    <el-tabs v-model="activeTab" class="settings-tabs">
      <el-tab-pane label="大模型配置" name="llm">
      <el-card class="set-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>LLM 模型</span>
          <el-tag :type="form.configured ? 'success' : 'warning'" size="small">
            {{ form.configured ? '已配置' : '未配置模型' }}
          </el-tag>
        </div>
      </template>
      <!-- 模型管理：列表 + 增删改 -->
      <div class="model-section">
        <div class="model-list">
          <div v-for="m in models" :key="m.id" class="model-item">
            <div class="model-info">
              <span class="model-name">{{ m.name }}</span>
              <span class="model-meta">{{ m.model }}<template v-if="m.base_url"> · {{ m.base_url }}</template></span>
              <span v-if="modelTestResults[m.id]" class="model-test-result"
                :class="{ fail: modelTestResults[m.id].startsWith('✗') }">
                {{ modelTestResults[m.id] }}
              </span>
            </div>
            <div class="model-ops">
              <el-button size="small" text type="success" :loading="testingModelId === m.id"
                @click="testModel(m)">测试连接</el-button>
              <el-button size="small" text type="primary" @click="openModelEditor(m)">编辑</el-button>
              <el-button size="small" text type="danger" @click="removeModel(m)">删除</el-button>
            </div>
          </div>
          <el-empty v-if="models.length === 0" description="还没有模型，点击下方按钮添加" :image-size="48" />
          <el-button class="model-add" @click="openModelEditor(null)">+ 添加模型</el-button>
        </div>
      </div>

      <!-- 有模型但未指定用途：引导选择（未指定时对应功能无法调用） -->
      <el-alert v-if="models.length && (!summaryModelId || !chatModelId)" type="warning" :closable="false" class="pick-tip">
        已添加模型，请为下方「总结模型」和「问答模型」分别指定用途 —— 未指定时对应功能将无法调用。
      </el-alert>
      <el-form label-width="120px" style="max-width: 710px">
        <el-form-item label="总结模型" required>
          <el-select v-model="summaryModelId" placeholder="请选择（必填）" style="width: 100%"
            :class="{ 'pick-empty': !summaryModelId }">
            <el-option v-for="m in models" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
          </el-select>
          <div class="field-hint" style="margin-left:0; margin-top:4px;">用于摘要 / 知识点 / 划线解读 / 复习出题</div>
        </el-form-item>
        <el-form-item label="问答模型" required>
          <el-select v-model="chatModelId" placeholder="请选择（必填）" style="width: 100%"
            :class="{ 'pick-empty': !chatModelId }">
            <el-option v-for="m in models" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
          </el-select>
          <div class="field-hint" style="margin-left:0; margin-top:4px;">问答页默认模型，可在输入区切换</div>
        </el-form-item>
        <el-form-item style="margin-top: 12px">
          <el-button type="primary" :loading="saving" @click="save()">保存</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 向量模型（知识库入库与检索） -->
    <el-card class="set-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>向量模型</span>
          <el-tag :type="embeddingMode === 'api' ? 'success' : 'info'" size="small">
            {{ embeddingMode === 'api' ? 'API 模型' : '本地 BGE-small-zh' }}
          </el-tag>
        </div>
      </template>
      <el-form label-width="120px" style="max-width: 870px">
        <el-form-item label="向量模型">
          <el-radio-group v-model="embeddingMode">
            <el-radio-button value="local">本地 BGE-small-zh（免配置）</el-radio-button>
            <el-radio-button value="api">API 模型（效果更好）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="embeddingMode === 'api'">
          <el-form-item label="Embedding 模型">
            <el-select v-model="form.embedding_model" filterable allow-create clearable
              placeholder="如 BAAI/bge-large-zh-v1.5" style="width: 100%">
              <el-option v-for="m in embedOptions" :key="m" :label="m" :value="m" />
            </el-select>
          </el-form-item>
          <el-form-item label="Base URL">
            <el-input v-model="form.embedding_base_url" placeholder="留空则复用 LLM 模型的 Base URL" />
          </el-form-item>
          <el-form-item label="API Key">
            <el-input v-model="form.embedding_api_key" type="password" show-password
              placeholder="留空则复用 LLM 模型的 API Key" />
          </el-form-item>
        </template>
        <el-form-item>
          <el-button :loading="testingEmbed" @click="testEmbedding">测试向量模型</el-button>
          <span v-if="embedTestResult" class="test-result">{{ embedTestResult }}</span>
        </el-form-item>
        <el-alert type="warning" :closable="false" class="embed-tip">
          切换向量模型后必须重建全部索引（不同模型的向量空间不兼容），并建议重新校准检索阈值。
          <el-button size="small" type="warning" plain :loading="rebuildingAll" @click="rebuildAll">
            一键重建全部索引
          </el-button>
        </el-alert>
        <el-form-item label="检索命中阈值">
          <el-input-number v-model="threshold" :min="0" :max="1" :step="0.05" :precision="3" style="width: 150px" />
          <span class="field-hint">低于此分数的检索结果不注入回答（换向量模型后建议点「校准」参考新值）</span>
          <el-button size="small" :loading="calibrating" @click="calibrate">校准阈值</el-button>
          <span v-if="calibResult" class="test-result">{{ calibResult }}</span>
        </el-form-item>
        <el-divider content-position="left">切分与去噪（入库管道）</el-divider>
        <el-form-item label="分块大小">
          <el-input-number v-model="chunkSize" :min="200" :max="2000" :step="50" style="width: 150px" />
          <span class="field-hint">字/块，推荐 500–800。块越大上下文越完整，但检索精度越粗</span>
        </el-form-item>
        <el-form-item label="句级重叠">
          <el-input-number v-model="chunkOverlap" :min="0" :max="400" :step="10" style="width: 150px" />
          <span class="field-hint">相邻块衔接的上下文字数（0=关闭重叠，最大为分块大小的一半）</span>
        </el-form-item>
        <el-form-item label="去噪规则">
          <div class="clean-switches">
            <el-checkbox v-model="cleanSwitches.header_footer">页眉页脚与页码</el-checkbox>
            <el-checkbox v-model="cleanSwitches.watermark">水印（高频短句）</el-checkbox>
            <el-checkbox v-model="cleanSwitches.garbled">乱码行</el-checkbox>
            <el-checkbox v-model="cleanSwitches.dedup">重复内容去重</el-checkbox>
          </div>
          <div class="field-hint" style="margin-left:0; margin-top:4px;">
            修改后对「新上传 / 重试解析」的材料生效；仅调分块参数时点上方「一键重建全部索引」即可生效，无需重新解析
          </div>
        </el-form-item>
        <el-form-item style="margin-top: 12px">
          <el-button type="primary" :loading="saving" @click="save()">保存</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 语音模型（音视频转写） -->
    <el-card class="set-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>语音模型（音视频转写）</span>
          <el-tag :type="asrInstalled ? 'success' : 'info'" size="small">
            {{ asrInstalled ? '已就绪' : '未下载' }}
          </el-tag>
        </div>
      </template>
      <p class="asr-tip">音视频转写依赖本地 Whisper 模型；未下载时首次转写会自动下载，也可在此提前下载。</p>
      <div class="asr-pref">
        <span class="asr-pref-label">默认转写版本</span>
        <el-radio-group v-model="asrModelSize" size="small">
          <el-radio-button value="">自动（small 优先）</el-radio-button>
          <el-radio-button value="small">Small</el-radio-button>
          <el-radio-button value="base">Base</el-radio-button>
        </el-radio-group>
        <span class="field-hint" style="margin-left:0;">强制指定版本时需已下载该版本，否则回退到已安装的最高版本</span>
      </div>
      <div class="asr-list">
        <div v-for="m in asrModels" :key="m.id" class="asr-item">
          <div class="asr-info">
            <div class="asr-name-row">
              <span class="asr-name">{{ m.name }}</span>
              <span class="asr-size">{{ m.size_mb }}MB · {{ m.quality }}质量</span>
              <el-tag v-if="m.installed" size="small" type="success">已就绪</el-tag>
            </div>
            <div class="asr-desc"><span class="asr-ok">✓ {{ m.pros }}</span></div>
            <div class="asr-desc"><span class="asr-warn">△ {{ m.cons }}</span></div>
          </div>
          <div class="asr-op">
            <template v-if="asrDownloading && asrDlSize === m.id">
              <el-progress :percentage="asrProgress" :stroke-width="10" style="width: 120px" />
            </template>
            <el-button v-else-if="!m.installed" size="small" type="primary"
              :disabled="asrDownloading" @click="downloadAsr(m.id)">
              {{ asrDownloading ? '下载中…' : '立即下载' }}
            </el-button>
            <span v-else class="asr-done">✓ 已安装</span>
          </div>
        </div>
      </div>
      <div v-if="asrError" class="asr-error">
        <span class="asr-error-text">{{ asrError }}</span>
        <el-button size="small" type="danger" plain @click="downloadAsr(asrDlSize || 'base')">重试</el-button>
      </div>
    </el-card>

    <!-- 模型编辑弹窗 -->
    <el-dialog v-model="modelDialog.show" :title="modelDialog.id ? '编辑模型' : '添加模型'" width="550px" append-to-body>
      <!-- 快速选择厂商：一键填充 Base URL 与模型名，降低填写门槛 -->
      <div class="vendor-picker">
        <div class="vp-label">快速选择厂商<span class="vp-hint">自动填充下方 Base URL 与模型名，可修改</span></div>
        <div class="vp-chips">
          <button v-for="v in VENDORS" :key="v.key" type="button"
            class="vp-chip" :class="{ on: vendorKey === v.key, 'vp-chip-custom': v.custom }" @click="applyVendor(v)">{{ v.name }}</button>
        </div>
      </div>
      <el-form label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="modelDialog.name" placeholder="如 DeepSeek-V3" />
        </el-form-item>
        <el-form-item label="Base URL">
          <el-input v-model="modelDialog.base_url" placeholder="https://api.deepseek.com" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="modelDialog.api_key" type="password" show-password
            placeholder="sk-...（已保存时显示掩码，不修改请保持原样）" />
          <div v-if="currentVendor && currentVendor.apply" class="field-hint" style="margin-left:0; margin-top:4px;">
            还没有 Key？<a :href="currentVendor.apply" target="_blank" rel="noopener" class="vendor-link">去 {{ currentVendor.name }} {{ currentVendor.applyHint || '领免费额度' }} →</a>
          </div>
          <div v-if="currentVendor && currentVendor.note" class="vendor-note">{{ currentVendor.note }}</div>
        </el-form-item>
        <el-form-item label="模型名" required>
          <div class="model-picker">
            <el-select v-model="modelDialog.model" filterable allow-create default-first-option
              placeholder="选择或输入模型名" style="flex: 1" :loading="fetchingModels">
              <el-option v-for="m in modelOptions" :key="m" :label="m" :value="m" />
            </el-select>
            <el-button :loading="fetchingModels" :disabled="!modelDialog.api_key"
              @click="fetchModelList">获取模型</el-button>
          </div>
          <div class="field-hint" style="margin-left:0; margin-top:4px;">填好 Base URL 与 API Key 后点「获取模型」拉取该服务商可用模型</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="modelDialog.show = false">取消</el-button>
        <el-button type="primary" @click="saveModel()">保存</el-button>
      </template>
    </el-dialog>
      </el-tab-pane>

      <el-tab-pane label="智能体提示词" name="prompts">
        <div class="prompts-page-header">
          <div>
            <p class="prompts-page-subtitle">留空即使用默认模板；修改后即时生效</p>
          </div>
          <el-input
            v-model="promptSearch"
            class="prompts-search"
            placeholder="搜索提示词"
            clearable
            :prefix-icon="Search"
          />
        </div>

        <div class="prompts-layout">
          <nav class="prompts-nav">
            <div class="prompts-nav-label">分类</div>
            <div
              v-for="cat in promptCategories"
              :key="cat.key"
              class="prompts-nav-item"
              :class="{ active: activePromptCategory === cat.key }"
              @click="activePromptCategory = cat.key"
            >
              <span>{{ cat.label }}</span>
              <span class="prompts-nav-count">{{ cat.count }}</span>
            </div>
          </nav>

          <div class="prompts-content">
            <div v-for="group in groupedPromptFields" :key="group.category" class="prompts-section">
              <h4 class="prompts-section-title">{{ group.label }}</h4>
              <div
                v-for="p in group.items"
                :key="p.key"
                class="prompt-card"
                :class="{ customized: prompts[p.key] }"
              >
                <div class="prompt-card-head">
                  <div class="prompt-card-title-row">
                    <span class="prompt-label">{{ p.label }}</span>
                    <span class="prompt-status-badge" :class="{ customized: prompts[p.key] }">
                      {{ prompts[p.key] ? '已自定义' : '默认' }}
                    </span>
                  </div>
                  <div class="prompt-card-desc">{{ p.desc }}</div>
                </div>
                <div class="prompt-card-actions">
                  <el-button
                    v-if="prompts[p.key]"
                    size="small"
                    text
                    type="warning"
                    @click="prompts[p.key] = ''"
                  >恢复默认</el-button>
                  <el-button
                    v-else
                    size="small"
                    text
                    type="primary"
                    @click="fillDefault(p.key)"
                  >基于默认修改</el-button>
                </div>
                <el-input
                  v-model="prompts[p.key]"
                  class="prompt-textarea"
                  type="textarea"
                  :rows="prompts[p.key] ? 6 : 3"
                  :placeholder="promptDefaults[p.key]"
                />
              </div>
            </div>

            <el-empty v-if="groupedPromptFields.length === 0" description="未找到匹配的提示词" :image-size="60" />
          </div>
        </div>

        <div class="sticky-action-bar">
          <span class="sticky-action-hint">
            已自定义 <strong>{{ customizedCount }}</strong> 项
          </span>
          <el-button type="primary" :loading="saving" @click="save()">保存提示词</el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane label="存储管理" name="storage">
      <el-card class="set-card" shadow="never">
      <template #header><span>存储管理</span></template>
      <div class="storage-row">
        <div class="storage-item"><span class="storage-num">{{ storage.files_mb }}</span>MB<span class="storage-label">原文文件</span></div>
        <div class="storage-item"><span class="storage-num">{{ storage.db_mb }}</span>MB<span class="storage-label">数据库</span></div>
        <div class="storage-item"><span class="storage-num">{{ storage.chroma_mb }}</span>MB<span class="storage-label">向量索引</span></div>
        <el-button @click="backup" :loading="backingUp">一键备份（下载 data 目录 zip）</el-button>
        <el-upload :show-file-list="false" :http-request="onRestore" accept=".zip">
          <el-button :loading="restoring">从备份恢复</el-button>
        </el-upload>
      </div>
      <p class="storage-tip">数据目录：{{ storage.data_dir }}（恢复前会自动备份当前数据，可随时回滚）</p>
    </el-card>
      </el-tab-pane>

      <el-tab-pane label="Token 消耗" name="usage">
      <el-card class="set-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>Token 消耗</span>
          <el-button size="small" text @click="loadUsage">刷新</el-button>
        </div>
      </template>
      <div v-if="usage.all._total.calls" class="usage-body">
        <div class="usage-totals">
          <div class="storage-item">
            <span class="storage-num">{{ usage.today._total.calls }}</span>
            <span class="storage-label">今日调用</span>
          </div>
          <div class="storage-item">
            <span class="storage-num">{{ fmtTokens(usage.today._total.tokens) }}</span>
            <span class="storage-label">今日 Token</span>
          </div>
          <div class="storage-item">
            <span class="storage-num">{{ usage.all._total.calls }}</span>
            <span class="storage-label">累计调用</span>
          </div>
          <div class="storage-item">
            <span class="storage-num">{{ fmtTokens(usage.all._total.tokens) }}</span>
            <span class="storage-label">累计 Token</span>
          </div>
        </div>
        <div class="usage-kinds">
          <span v-for="(v, k) in usageKinds" :key="k" class="usage-chip">
            {{ kindLabel(k) }} {{ v.calls }} 次 / {{ fmtTokens(v.prompt_tokens + v.completion_tokens) }}
          </span>
        </div>
        <p class="storage-tip">流式调用按字符数估算（≈1.6 字符/token），非流式调用为 API 返回的精确值</p>
      </div>
      <el-empty v-else description="暂无调用记录（配置 Key 后使用 AI 功能即开始统计）" :image-size="60" />
    </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { useRouter, useRoute } from 'vue-router'
import { settingsApi } from '../api'
import http from '../api/http'
import { useAsr } from '../composables/useAsr'
import PageHead from '../components/PageHead.vue'

const embedOptions = [
  'BAAI/bge-large-zh-v1.5',        // 硅基流动（免费）
  'BAAI/bge-m3',                   // 硅基流动（免费）
  'text-embedding-v3',             // 阿里百炼
  'embedding-3',                   // 智谱（免费额度）
]

const activeTab = ref('llm')

// 语音模型（Whisper）状态与下载
const {
  models: asrModels, installed: asrInstalled, loading: asrLoading,
  downloading: asrDownloading, dlSize: asrDlSize, progress: asrProgress,
  error: asrError, load: loadAsr, download: downloadAsr,
} = useAsr()

const router = useRouter()
const route = useRoute()
const form = reactive({
  llm_base_url: '', llm_api_key: '', summary_model: '', chat_model: '',
  embedding_model: '', embedding_base_url: '', embedding_api_key: '', configured: false,
})

// 首次配置完成：给一次「去导入材料」的正反馈引导（localStorage 去重，仅弹一次）
const SETUP_DONE_KEY = 'asc_setup_guided'
function maybeGuideAfterSetup() {
  if (!form.configured || localStorage.getItem(SETUP_DONE_KEY)) return
  localStorage.setItem(SETUP_DONE_KEY, '1')
  ElNotification({
    title: '模型已就绪 🎉',
    message: '现在去导入第一份材料，开始你的 AI 伴学 · 点此前往材料库',
    type: 'success',
    duration: 9000,
    onClick: () => router.push('/'),
  })
}

// 多模型：模型列表 + 用途引用
const models = ref([])
const summaryModelId = ref('')
const chatModelId = ref('')
const modelDialog = reactive({ show: false, id: null, name: '', base_url: '', api_key: '', model: '' })
const modelOptions = ref([])      // 弹窗内「模型名」下拉选项（由服务商拉取/当前值兜底）
const fetchingModels = ref(false)

// 主流厂商预设：一键填充 Base URL / 模型名，降低配置门槛
// ⚠️ 链接与模型名可能随厂商调整，上线前需逐个实测「获取模型」能否成功
const VENDORS = [
  { key: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com', model: 'deepseek-chat', apply: 'https://platform.deepseek.com/api_keys' },
  { key: 'kimi', name: 'Kimi', base_url: 'https://api.moonshot.cn/v1', model: 'kimi-k2-thinking', apply: 'https://platform.moonshot.cn/console/api-keys' },
  { key: 'kimicode', name: 'Kimi For Coding', base_url: 'https://api.kimi.com/coding/v1', model: 'kimi-for-coding', apply: 'https://www.kimi.com/code/console', applyHint: '获取 API Key', note: '⚠️ 需 Kimi Code 会员（订阅制，非开放平台）；模型 ID 固定填 kimi-for-coding；官方限定编程场景使用，请勿篡改客户端标识' },
  { key: 'zhipu', name: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash', apply: 'https://open.bigmodel.cn/usercenter/apikeys' },
  { key: 'qwen', name: '通义千问', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', apply: 'https://bailian.console.aliyun.com/' },
  { key: 'doubao', name: '豆包', base_url: 'https://ark.cn-beijing.volces.com/api/v3', model: '', apply: 'https://console.volcengine.com/ark', note: '⚠️ 模型名需填「推理接入点 ID」（在火山方舟创建后获得），不是模型名本身' },
  { key: 'hunyuan', name: '腾讯混元', base_url: 'https://api.hunyuan.cloud.tencent.com/v1', model: 'hunyuan-turbos-latest', apply: 'https://console.cloud.tencent.com/hunyuan' },
  { key: 'ernie', name: '百度文心', base_url: 'https://qianfan.baidubce.com/v2', model: 'ernie-4.5-turbo-128k', apply: 'https://console.bce.baidu.com/qianfan/' },
  { key: 'minimax', name: 'MiniMax', base_url: 'https://api.minimax.chat/v1', model: 'MiniMax-Text-01', apply: 'https://platform.minimaxi.com/' },
  { key: 'yi', name: '零一万物', base_url: 'https://api.lingyiwanwu.com/v1', model: 'yi-lightning', apply: 'https://platform.lingyiwanwu.com/' },
  { key: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-5', apply: 'https://platform.openai.com/api-keys', note: '⚠️ 需可访问外网；国内直连通常不可用' },
  { key: 'siliconflow', name: '硅基流动', base_url: 'https://api.siliconflow.cn/v1', model: 'Qwen/Qwen2.5-7B-Instruct', apply: 'https://cloud.siliconflow.cn/account/ak' },
  { key: 'custom', name: '自定义', base_url: '', model: '', apply: '', custom: true, note: 'ℹ️ 自填服务商提供的 Base URL 与模型名，需兼容 OpenAI 协议；多数地址需以 /v1 结尾（如 https://xxx.com/v1）——漏写会返回网页而无法问答' },
]
const vendorKey = ref('')
const currentVendor = computed(() => VENDORS.find(v => v.key === vendorKey.value) || null)

// 选中厂商：自动填充 Base URL 与模型名（名称仅当为空或同为厂商名时覆盖，避免冲掉用户自定义）
function applyVendor(v) {
  vendorKey.value = v.key
  if (v.custom) {
    // 自定义：清空预填，全部交由用户手填
    modelDialog.name = ''
    modelDialog.base_url = ''
    modelDialog.model = ''
    modelOptions.value = []
    return
  }
  if (!modelDialog.name.trim() || VENDORS.some(x => x.name === modelDialog.name.trim())) {
    modelDialog.name = v.name
  }
  modelDialog.base_url = v.base_url
  if (v.model) {
    modelDialog.model = v.model
    modelOptions.value = [v.model]
  }
}

function openModelEditor(m) {
  vendorKey.value = ''   // 每次打开重置厂商选择态
  if (m) {
    modelDialog.id = m.id
    modelDialog.name = m.name
    modelDialog.base_url = m.base_url || ''
    modelDialog.api_key = m.api_key || ''   // 掩码或真实值；掩码原样回传=保留
    modelDialog.model = m.model
    modelOptions.value = m.model ? [m.model] : []   // 至少保留当前模型名，可下拉也可拉取刷新
  } else {
    modelDialog.id = null
    modelDialog.name = ''
    modelDialog.base_url = ''
    modelDialog.api_key = ''
    modelDialog.model = ''
    modelOptions.value = []
  }
  modelDialog.show = true
}

// 从服务商拉取可用模型列表（api_key 传掩码时后端按 id 取已存值）
async function fetchModelList() {
  if (!modelDialog.api_key) {
    ElMessage.warning('请先填写 API Key')
    return
  }
  fetchingModels.value = true
  try {
    const { data } = await settingsApi.listModels({
      id: modelDialog.id || '',
      base_url: modelDialog.base_url || '',
      api_key: modelDialog.api_key || '',
    })
    modelOptions.value = data.models || []
    ElMessage.success(`已获取 ${modelOptions.value.length} 个模型`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '获取失败')
  } finally {
    fetchingModels.value = false
  }
}

async function saveModel() {
  if (!modelDialog.name.trim() || !modelDialog.model.trim()) {
    ElMessage.warning('请填写名称和模型名')
    return
  }
  const payload = {
    name: modelDialog.name.trim(),
    base_url: modelDialog.base_url.trim(),
    api_key: modelDialog.api_key.trim(),
    model: modelDialog.model.trim(),
  }
  if (modelDialog.id) {
    const t = models.value.find(x => x.id === modelDialog.id)
    if (t) Object.assign(t, payload)
  } else {
    payload.id = 'm_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
    models.value.push(payload)
  }
  modelDialog.show = false
  // 保存即持久化：避免「还要点底部保存」的两步困惑，并给出明确的成功提示
  try {
    await save('保存成功', true)
  } catch {
    ElMessage.warning('已加入列表，请点底部「保存」持久化')
  }
}

function removeModel(m) {
  ElMessageBox.confirm(`删除模型「${m.name}」？`, '删除确认', { type: 'warning' })
    .then(async () => {
      models.value = models.value.filter(x => x.id !== m.id)
      delete modelTestResults[m.id]
      if (summaryModelId.value === m.id) summaryModelId.value = ''
      if (chatModelId.value === m.id) chatModelId.value = ''
      // 删除即持久化：否则用户刷新后模型会重新出现（看起来像「删不掉」）
      try {
        await save(`已删除模型「${m.name}」`, true)
      } catch {
        ElMessage.warning('已从列表移除，请点底部「保存」持久化')
      }
    })
    .catch(() => {})
}

// 单模型连接测试：直接按列表里的当前配置测（含未点「保存」的新模型）；掩码 Key 由后端按 id 取已存值
const testingModelId = ref('')
const modelTestResults = reactive({})

async function testModel(m) {
  testingModelId.value = m.id
  modelTestResults[m.id] = ''
  try {
    const { data } = await settingsApi.testModel({
      id: m.id, base_url: m.base_url || '', api_key: m.api_key || '', model: m.model || '',
    })
    modelTestResults[m.id] = `✓ 连接成功（${data.model}，延迟 ${data.latency_ms}ms）`
  } catch (e) {
    modelTestResults[m.id] = `✗ ${e.response?.data?.detail || '连接失败'}`
  } finally {
    testingModelId.value = ''
  }
}
const embeddingMode = ref('local')
const threshold = ref(0.15)
const chunkSize = ref(700)
const chunkOverlap = ref(100)
const cleanSwitches = reactive({ header_footer: true, watermark: true, garbled: true, dedup: true })
const asrModelSize = ref('')   // 语音模型默认转写版本：''=自动，'small'/'base'=强制
const calibrating = ref(false)
const calibResult = ref('')

async function calibrate() {
  calibrating.value = true
  calibResult.value = ''
  try {
    const { data } = await http.post('/kb/calibrate')
    const s = data.samples.map(x => `相关${x.self_score}/无关${x.cross_score ?? '-'}`).join('，')
    calibResult.value = `建议阈值 ${data.suggested_threshold}（样本：${s}）`
  } catch (e) {
    calibResult.value = '校准失败，请确认知识库已有内容'
  } finally {
    calibrating.value = false
  }
}
const testingEmbed = ref(false)
const embedTestResult = ref('')
const rebuildingAll = ref(false)

const promptFields = [
  { key: 'prompt_summary', label: '脉络摘要', desc: '生成全文层级大纲（摘要 Tab / 分章总结）', category: 'study' },
  { key: 'prompt_keywords', label: '知识点提炼', desc: '提炼核心概念（注意保留 JSON 输出格式说明）', category: 'study' },
  { key: 'prompt_explain', label: '划线解读 / 追问', desc: '选中文本后的解释与多轮追问', category: 'study' },
  { key: 'prompt_kb_qa', label: '知识库问答（命中时）', desc: '问答页检索到相关内容时的回答风格', category: 'study' },
  { key: 'prompt_general', label: '通用回答（未命中时）', desc: '知识库未命中时的兜底回答风格', category: 'study' },
  { key: 'prompt_review', label: '复习出题', desc: '从笔记生成自测卡片（保留 JSON 输出格式说明）', category: 'study' },
  { key: 'prompt_suggest', label: '相关问题推荐', desc: '问答回答后生成 3 个追问建议（保留 JSON 数组格式）', category: 'study' },
  { key: 'prompt_quiz', label: '测一测出题', desc: '基于资料核心内容生成测试题（保留 JSON 数组格式）', category: 'study' },
  { key: 'prompt_note_rewrite', label: '文本改写', desc: '改写选中文字或整条内容（笔记 / 材料文本共用，更通顺/专业/简洁）', category: 'note' },
  { key: 'prompt_note_expand', label: '文本扩写', desc: '补充背景/例子/细节，展开要点（笔记 / 材料文本共用）', category: 'note' },
  { key: 'prompt_note_summarize', label: '文本总结', desc: '提炼压缩为精炼要点（笔记 / 材料文本共用）', category: 'note' },
  { key: 'prompt_note_continue', label: '文本续写', desc: '接着已有内容往下续写（笔记 / 材料文本共用）', category: 'note' },
  // ---- AI 播客 / 周报（快速模式 direct 版提示词刻意不开放 UI，走默认模板；需要时再加回）----
  { key: 'prompt_podcast_brief', label: 'AI 播客 · 知识简报', desc: '素材提炼成结构化简报（提炼层，可单独转笔记）。注意保留 {brief_chars} 字数占位符', category: 'podcast' },
  { key: 'prompt_podcast_script', label: 'AI 播客 · 对话脚本', desc: '知识简报改写成双人对话（两步式）。保留 JSON 数组输出说明与 {script_chars} / {max_chars} / {seg_count} / {avg_chars} 占位符', category: 'podcast' },
  { key: 'prompt_podcast_script_solo', label: 'AI 播客 · 单人精讲脚本', desc: '知识简报改写成单人精讲口播（两步式）。保留 JSON 数组输出说明与 {script_chars} / {max_chars} / {seg_count} / {avg_chars} 占位符', category: 'podcast' },
  { key: 'prompt_stats_report', label: '学习周报', desc: '统计页周报：按数据摘要 / 问题诊断 / 行动建议 / 进阶方法四段输出', category: 'other' },
  { key: 'prompt_recall', label: '复述卡', desc: '从笔记生成自我复述提示（保留 JSON 输出格式说明）', category: 'other' },
]
const prompts = reactive({
  prompt_summary: '', prompt_keywords: '', prompt_explain: '',
  prompt_kb_qa: '', prompt_general: '', prompt_review: '', prompt_suggest: '', prompt_quiz: '',
  prompt_note_rewrite: '', prompt_note_expand: '', prompt_note_summarize: '', prompt_note_continue: '',
})
const promptDefaults = reactive({})
const saving = ref(false)

// 智能体提示词页：搜索、分类与状态
const promptSearch = ref('')
const activePromptCategory = ref('all')

const CATEGORY_LABELS = {
  study: '学习理解',
  note: '笔记加工',
  podcast: 'AI 播客',
  other: '其他工具',
}

const promptCategories = computed(() => {
  const counts = { all: promptFields.length }
  for (const p of promptFields) {
    counts[p.category] = (counts[p.category] || 0) + 1
  }
  return [
    { key: 'all', label: '全部', count: counts.all },
    { key: 'study', label: CATEGORY_LABELS.study, count: counts.study },
    { key: 'note', label: CATEGORY_LABELS.note, count: counts.note },
    { key: 'podcast', label: CATEGORY_LABELS.podcast, count: counts.podcast },
    { key: 'other', label: CATEGORY_LABELS.other, count: counts.other },
  ]
})

const filteredPromptFields = computed(() => {
  const q = promptSearch.value.trim().toLowerCase()
  return promptFields.filter(p => {
    const matchCategory = activePromptCategory.value === 'all' || p.category === activePromptCategory.value
    const matchSearch = !q || p.label.toLowerCase().includes(q) || p.desc.toLowerCase().includes(q)
    return matchCategory && matchSearch
  })
})

const groupedPromptFields = computed(() => {
  const groups = {}
  for (const p of filteredPromptFields.value) {
    if (!groups[p.category]) {
      groups[p.category] = { category: p.category, label: CATEGORY_LABELS[p.category], items: [] }
    }
    groups[p.category].items.push(p)
  }
  return Object.values(groups)
})

const customizedCount = computed(() => promptFields.filter(p => prompts[p.key]).length)

// 基于默认模板修改：把默认提示词填入输入框，供在此基础上微调
function fillDefault(key) {
  prompts[key] = promptDefaults[key] || ''
}
const storage = reactive({ files_mb: 0, db_mb: 0, chroma_mb: 0, data_dir: '' })
const backingUp = ref(false)

async function loadStorage() {
  const { data } = await http.get('/settings/storage')
  Object.assign(storage, data)
}

async function backup() {
  backingUp.value = true
  try {
    const resp = await fetch('/api/settings/backup')
    const blob = await resp.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `asc-backup-${new Date().toISOString().slice(0, 10)}.zip`
    a.click()
    ElMessage.success('备份已开始下载')
  } finally {
    backingUp.value = false
  }
}

const restoring = ref(false)
const usage = reactive({ today: { _total: { calls: 0, tokens: 0 } }, all: { _total: { calls: 0, tokens: 0 } } })

// Token 统计业务展示顺序（按业务流），未收录的新 kind 自动排在最后
const KIND_ORDER = ['summary', 'section_summary', 'keywords', 'explain', 'ask', 'transform',
                    'chat', 'suggest', 'review', 'quiz', 'stats_report',
                    'podcast_brief', 'podcast_script']
const usageKinds = computed(() => {
  const { _total, ...rest } = usage.all
  const ordered = {}
  for (const k of KIND_ORDER) {
    if (rest[k]) ordered[k] = rest[k]
  }
  for (const [k, v] of Object.entries(rest)) {
    if (!ordered[k]) ordered[k] = v
  }
  return ordered
})
const kindLabel = (k) => ({
  summary: '摘要', section_summary: '分章总结', keywords: '知识点',
  explain: '解读/追问', ask: '材料提问', transform: '文本加工',
  chat: '问答', suggest: '追问建议', review: '复习出题', quiz: '测一测',
  stats_report: '学习报告', podcast_brief: '播客简报', podcast_script: '播客脚本',
}[k] || k)
const fmtTokens = (n) => n >= 10000 ? (n / 10000).toFixed(1) + 'w' : (n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n))

async function loadUsage() {
  const { data } = await settingsApi.usage()
  Object.assign(usage, data)
}

async function onRestore({ file }) {
  try {
    await ElMessageBox.confirm(
      '将用备份覆盖当前全部数据（原数据会自动备份可回滚），还原后需重启后端。继续？',
      '从备份恢复', { confirmButtonText: '开始还原', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  restoring.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const { data } = await settingsApi.restore(fd)
    ElMessageBox.alert(data.message + `\n回滚备份位于：${data.rollback_dir}`, '还原完成', { type: 'success' })
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '还原失败')
  } finally {
    restoring.value = false
  }
}

onMounted(async () => {
  const { data } = await settingsApi.get()
  Object.assign(form, data)
  models.value = data.llm_models || []
  summaryModelId.value = data.summary_model_id || ''
  chatModelId.value = data.chat_model_id || ''
  embeddingMode.value = data.embedding_model ? 'api' : 'local'
  threshold.value = data.kb_hit_threshold ?? 0.15
  chunkSize.value = data.chunk_size ?? 700
  chunkOverlap.value = data.chunk_overlap ?? 100
  cleanSwitches.header_footer = data.clean_header_footer ?? true
  cleanSwitches.watermark = data.clean_watermark ?? true
  cleanSwitches.garbled = data.clean_garbled ?? true
  cleanSwitches.dedup = data.clean_dedup ?? true
  asrModelSize.value = data.asr_model_size || ''
  for (const p of promptFields) prompts[p.key] = data[p.key] || ''
  Object.assign(promptDefaults, data.prompt_defaults || {})
  loadStorage()
  loadUsage()
  loadAsr()
  // 从问答页「前往配置模型」跳入（/settings?add=1）：直接打开添加模型弹窗
  if (route.query.add) openModelEditor(null)
})

async function save(successMsg = '保存成功', skipGuide = false) {
  saving.value = true
  try {
    const { data } = await settingsApi.update({
      llm_models: models.value,
      summary_model_id: summaryModelId.value,
      chat_model_id: chatModelId.value,
      // 本地模式 = 清空 embedding_model，走内置 BGE
      embedding_model: embeddingMode.value === 'api' ? form.embedding_model : '',
      embedding_base_url: embeddingMode.value === 'api' ? form.embedding_base_url : '',
      embedding_api_key: embeddingMode.value === 'api' ? form.embedding_api_key : '',
      asr_model_size: asrModelSize.value,
      // 提示词（空字符串 = 恢复默认模板）— 遍历 promptFields，确保新增项自动包含
      ...Object.fromEntries(promptFields.map(p => [p.key, prompts[p.key] || ''])),
      kb_hit_threshold: threshold.value,
      chunk_size: chunkSize.value,
      chunk_overlap: chunkOverlap.value,
      clean_header_footer: cleanSwitches.header_footer,
      clean_watermark: cleanSwitches.watermark,
      clean_garbled: cleanSwitches.garbled,
      clean_dedup: cleanSwitches.dedup,
    })
    Object.assign(form, data)
    models.value = data.llm_models || []
    ElMessage.success(successMsg)
    if (!skipGuide) maybeGuideAfterSetup()
  } finally {
    saving.value = false
  }
}

async function testEmbedding() {
  testingEmbed.value = true
  embedTestResult.value = ''
  try {
    await save()
    const { data } = await http.post('/settings/test-embedding')
    embedTestResult.value = data.mode === 'local'
      ? `✓ 本地模型就绪（${data.model}，${data.dim} 维）`
      : `✓ API 调用成功（${data.model}，${data.dim} 维，${data.latency_ms}ms）`
  } catch (e) {
    embedTestResult.value = `✗ ${e.response?.data?.detail || '测试失败'}`
  } finally {
    testingEmbed.value = false
  }
}

async function rebuildAll() {
  rebuildingAll.value = true
  try {
    const { data } = await http.post('/kb/rebuild-all')
    ElMessage.success(`重建完成：${data.materials} 份材料 / ${data.chunks} 原文块 / ${data.notes} 条笔记`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重建失败')
  } finally {
    rebuildingAll.value = false
  }
}
</script>

<style scoped>
.settings-tabs :deep(.el-tabs__header) { margin-bottom: 16px; }
.settings-tabs :deep(.el-tabs__item) { font-size: 14px; }
.set-card {
  max-width: 870px; margin-bottom: 18px;
  border: 1px solid var(--asc-border);
  box-shadow: 0 1px 4px rgba(0, 0, 0, .03);
}
.card-head { display: flex; justify-content: space-between; align-items: center; font-weight: 600; }
.test-result { margin-left: 12px; font-size: 13px; color: var(--asc-text-2); }
.field-hint { margin-left: 10px; font-size: 12.5px; color: var(--asc-text-3); white-space: nowrap; }
.clean-switches { display: flex; flex-wrap: wrap; gap: 2px 20px; }
/* 模型管理列表 */
.model-section { margin-bottom: 8px; }
.model-list { max-width: 710px; }
.model-item {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 12px 14px; margin-bottom: 8px;
  border: 1px solid var(--asc-border); border-radius: 10px;
  background: var(--asc-surface-2); transition: all .16s ease;
}
.model-item:hover { border-color: var(--asc-primary); background: var(--asc-primary-soft); }
.model-info { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.model-name { font-size: 14px; font-weight: 600; color: var(--asc-text); }
.model-meta { font-size: 12px; color: var(--asc-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.model-test-result { font-size: 12px; color: #67c23a; }
.model-test-result.fail { color: #f56c6c; }
.model-ops { flex-shrink: 0; }
.model-picker { display: flex; gap: 8px; align-items: center; width: 100%; }
.model-add { width: 100%; border-style: dashed; }
/* 快速选择厂商（模型编辑弹窗） */
.vendor-picker { margin-bottom: 16px; padding: 12px 14px; background: var(--asc-surface-2); border-radius: 10px; }
.vp-label { font-size: 12.5px; color: var(--asc-text-2); font-weight: 500; margin-bottom: 9px; }
.vp-hint { font-weight: 400; color: var(--asc-text-3); margin-left: 8px; font-size: 11.5px; }
.vp-chips { display: flex; flex-wrap: wrap; gap: 7px; }
.vp-chip { border: 1px solid var(--asc-border); background: #fff; color: var(--asc-text-2); font-size: 12.5px; padding: 5px 12px; border-radius: 20px; cursor: pointer; transition: .16s; font-family: inherit; }
.vp-chip:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
.vp-chip.on { background: var(--asc-primary); border-color: var(--asc-primary); color: #fff; font-weight: 500; }
.vp-chip-custom { border-style: dashed; }
.vp-chip-custom.on { border-style: solid; }
.vendor-link { color: var(--asc-primary); text-decoration: none; font-weight: 500; }
.vendor-link:hover { text-decoration: underline; }
.vendor-note { margin: 6px 0 0; font-size: 11.5px; color: #e6a23c; line-height: 1.5; }
/* 模型用途未选择：引导提示 + 采集态警告 */
.pick-tip { margin: 0 0 14px; }
.pick-empty :deep(.el-select__wrapper) { box-shadow: 0 0 0 1px #f0a020 inset; }
.storage-tip { font-size: 13px; color: var(--asc-text-3); margin: 10px 0 0; }
.embed-tip { margin-bottom: 4px; }
.embed-tip :deep(.el-alert__content) { display: flex; align-items: center; gap: 12px; }
.embed-tip :deep(.el-alert__description) { white-space: nowrap; margin: 0; }
.embed-tip :deep(.el-alert__description .el-button) { flex-shrink: 0; }
.storage-row { display: flex; gap: 28px; align-items: center; }
.storage-item { display: flex; flex-direction: column; align-items: center; }
.storage-num { font-size: 22px; font-weight: 600; color: var(--asc-primary); }
.storage-label { font-size: 12px; color: var(--asc-text-3); }
.card-head-tip { font-size: 12px; color: var(--asc-text-3); font-weight: 400; }
.prompt-label { font-size: 14px; font-weight: 600; }

.usage-body { display: flex; flex-direction: column; gap: 12px; }
.usage-totals { display: flex; gap: 32px; }
.usage-kinds { display: flex; gap: 8px; flex-wrap: wrap; }
.usage-chip {
  font-size: 12px; color: var(--asc-text-2);
  background: var(--asc-surface-2); border-radius: 6px; padding: 3px 10px;
}
.storage-row .el-upload { margin-left: 0; }

/* 语音模型区块 */
.asr-tip { font-size: 13px; color: var(--asc-text-3); margin: 0 0 14px; }
.asr-pref { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.asr-pref-label { font-size: 13px; font-weight: 600; color: var(--asc-text-2); }
.asr-list { display: flex; flex-direction: column; gap: 10px; }
.asr-item {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 12px 14px; border: 1px solid var(--asc-border); border-radius: 10px;
  background: var(--asc-surface-2);
}
.asr-info { min-width: 0; flex: 1; }
.asr-name-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.asr-name { font-size: 14px; font-weight: 600; color: var(--asc-text); }
.asr-size { font-size: 12px; color: var(--asc-text-3); }
.asr-desc { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.5; }
.asr-ok { color: var(--asc-text-2); }
.asr-warn { color: var(--asc-text-3); }
.asr-op { flex-shrink: 0; display: flex; align-items: center; }
.asr-done { font-size: 13px; color: var(--asc-primary); font-weight: 600; }
.asr-error {
  display: flex; align-items: center; gap: 10px; margin-top: 12px;
  padding: 8px 12px; border-radius: 8px;
  background: rgba(245, 108, 108, .08); color: #f56c6c; font-size: 13px;
}
.asr-error-text { flex: 1; }
/* ===== 智能体提示词页 redesign ===== */
.prompts-page-header {
  display: flex; justify-content: space-between; align-items: flex-end;
  gap: 16px; margin-bottom: 22px;
}
.prompts-page-subtitle { margin: 0; font-size: 13px; color: var(--asc-text-2); }
.prompts-search { width: 260px; }
.prompts-search :deep(.el-input__wrapper) { border-radius: 8px; }

.prompts-layout { display: flex; align-items: flex-start; gap: 24px; }
.prompts-nav {
  width: 170px; flex-shrink: 0; position: sticky; top: 16px;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: var(--asc-radius); padding: 12px 10px;
}
.prompts-nav-label {
  font-size: 11px; color: var(--asc-text-3); font-weight: 500;
  margin-bottom: 8px; padding-left: 10px; letter-spacing: .3px;
}
.prompts-nav-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 10px; border-radius: 8px; cursor: pointer;
  font-size: 13px; color: var(--asc-text-2);
  transition: all .15s ease;
}
.prompts-nav-item:hover { background: var(--asc-surface-2); color: var(--asc-text); }
.prompts-nav-item.active { background: var(--asc-primary-soft); color: var(--asc-primary); font-weight: 500; }
.prompts-nav-count { font-size: 12px; color: inherit; opacity: .8; }

.prompts-content { flex: 1; min-width: 0; padding-bottom: 12px; }
.prompts-section { margin-bottom: 28px; }
.prompts-section-title {
  margin: 0 0 14px; font-size: 15px; font-weight: 600; color: var(--asc-text);
}

.prompt-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: var(--asc-radius); padding: 18px 20px; margin-bottom: 14px;
  position: relative; overflow: hidden;
  transition: border-color .18s ease, box-shadow .18s ease;
}
.prompt-card::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: transparent; transition: background .18s ease;
}
.prompt-card:hover { border-color: var(--asc-primary); box-shadow: var(--asc-shadow-hover); }
.prompt-card.customized::before { background: var(--asc-primary); }

.prompt-card-head { margin-bottom: 12px; }
.prompt-card-title-row {
  display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
}
.prompt-card .prompt-label { font-size: 14px; font-weight: 600; color: var(--asc-text); }
.prompt-card-desc { font-size: 12px; color: var(--asc-text-2); line-height: 1.5; }
.prompt-status-badge {
  font-size: 11px; font-weight: 500;
  padding: 2px 7px; border-radius: 4px;
  background: var(--asc-surface-2); color: var(--asc-text-3);
}
.prompt-status-badge.customized { background: var(--asc-primary-soft); color: var(--asc-primary); }

.prompt-card-actions {
  position: absolute; top: 16px; right: 18px;
}
.prompt-card-actions .el-button { padding: 4px 6px; }

.prompt-textarea :deep(.el-textarea__inner) {
  min-height: 90px !important; font-size: 13px; line-height: 1.65;
  color: var(--asc-text);
}

.sticky-action-bar {
  position: sticky; bottom: 0;
  display: flex; align-items: center; justify-content: space-between;
  background: var(--asc-card); border-top: 1px solid var(--asc-border);
  margin: 18px -32px -28px; padding: 14px 32px;
}
.sticky-action-hint { font-size: 13px; color: var(--asc-text-2); }
.sticky-action-hint strong { color: var(--asc-text); font-weight: 600; }

@media (max-width: 860px) {
  .prompts-layout { flex-direction: column; }
  .prompts-nav {
    position: static; width: 100%; display: flex; gap: 6px; overflow-x: auto;
    padding: 10px;
  }
  .prompts-nav-label { display: none; }
  .prompts-nav-item { white-space: nowrap; flex-shrink: 0; }
  .prompts-search { width: 100%; }
  .prompts-page-header { flex-direction: column; align-items: flex-start; }
  .prompt-card-actions { position: static; margin-bottom: 10px; }
}

</style>
