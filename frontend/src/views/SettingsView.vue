<template>
  <div class="page settings-page" :style="{ '--asc-set-head-h': headH + 'px' }">
    <PageHead ref="pageHeadRef" title="管理中心" sub="管理 LLM、向量模型与本地数据配置">
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
        <el-form-item label="知识图谱">
          <el-checkbox v-model="autoExtractEntities">导入材料后自动生成知识图谱</el-checkbox>
          <div class="kg-hint">
            <div class="field-hint">开启后每份材料会调用大模型抽取实体与关系（消耗 token）。</div>
            <div class="field-hint">关闭时仍可在「知识库 → 按材料」对单份材料点「生成图谱」按需生成；</div>
            <div class="field-hint">材料重试解析时若内容未变化，会自动跳过抽取、不重复消耗 token</div>
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

    <!-- 语音合成模型（本地 ZipVoice）：按需下载，不入安装包 -->
    <el-card class="set-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>语音合成模型（本地合成）</span>
          <el-tag :type="zvInstalled ? 'success' : 'info'" size="small">
            {{ zvInstalled ? '已就绪' : '未下载' }}
          </el-tag>
        </div>
      </template>
      <p class="asr-tip">
        播客配音默认用在线语音（edge-tts，音色好但依赖网络，且上游接口无 SLA 保证）。
        下载本地模型后，把下方 <b>合成引擎</b> 切到「本地」即可：纯本机运算、断网可用，更稳定。
      </p>
      <div class="asr-pref">
        <span class="asr-pref-label">合成引擎</span>
        <el-radio-group v-model="ttsProvider" size="small" @change="saveTtsProvider">
          <el-radio-button value="edge">在线（edge-tts）</el-radio-button>
          <el-radio-button value="zipvoice">本地（ZipVoice）</el-radio-button>
        </el-radio-group>
        <span class="field-hint" style="margin-left:0;">
          在线音色更丰富、依赖网络；本地纯离线更稳定（切换后播客需重新合成才会换音色）
        </span>
      </div>
      <div class="asr-list">
        <div v-for="m in zvModels" :key="m.id" class="asr-item">
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
            <template v-if="zvDownloading">
              <el-progress :percentage="zvProgress" :stroke-width="10" style="width: 120px" />
              <span class="asr-size" style="margin-left:8px;">{{ zvDownloadedMb }} / {{ zvTotalMb }} MB</span>
            </template>
            <el-button v-else-if="!m.installed" size="small" type="primary"
              :disabled="zvDownloading" @click="downloadZipvoice()">立即下载</el-button>
            <span v-else class="asr-done">✓ 已安装</span>
          </div>
        </div>
      </div>
      <div v-if="zvError" class="asr-error">
        <span class="asr-error-text">{{ zvError }}</span>
        <el-button size="small" type="danger" plain @click="downloadZipvoice()">重试</el-button>
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
          <div class="card-head-actions">
            <el-radio-group v-model="usagePeriod" size="small" fill="var(--asc-primary)">
              <el-radio-button label="今日" value="today" />
              <el-radio-button label="累计" value="all" />
            </el-radio-group>
            <el-button size="small" text @click="loadUsage">
              <el-icon class="usage-refresh" :class="{ spinning: usageLoading }"><RefreshRight /></el-icon>刷新
            </el-button>
          </div>
        </div>
      </template>
      <div v-if="usage.all._total.calls" class="usage-body">
        <div class="usage-metrics">
          <div class="usage-metric-card">
            <div class="usage-metric-top">
              <span class="usage-metric-num">{{ fmtTokens(usage[usagePeriod]._total.tokens) }}</span>
              <span class="usage-metric-unit">tokens</span>
            </div>
            <div class="usage-metric-label">{{ usagePeriod === 'today' ? '今日消耗' : '累计消耗' }}</div>
            <div class="usage-metric-compare">占累计 {{ usagePercent(usage[usagePeriod]._total.tokens, usage.all._total.tokens) }}</div>
          </div>
          <div class="usage-metric-card">
            <div class="usage-metric-top">
              <span class="usage-metric-num">{{ usage[usagePeriod]._total.calls }}</span>
              <span class="usage-metric-unit">次</span>
            </div>
            <div class="usage-metric-label">{{ usagePeriod === 'today' ? '今日调用' : '累计调用' }}</div>
            <div class="usage-metric-compare">占累计 {{ usagePercent(usage[usagePeriod]._total.calls, usage.all._total.calls) }}</div>
          </div>
        </div>

        <div class="usage-section">
          <div class="usage-section-title">
            <span>业务分布</span>
            <span class="usage-section-sub">按 Token 数排序 · 共 {{ Object.keys(usageKinds).length }} 类</span>
          </div>
          <div class="usage-list">
            <div v-for="([k, v], idx) in usageKindsSorted" :key="k" class="usage-row">
              <div class="usage-rank">{{ idx + 1 }}</div>
              <div class="usage-row-main">
                <div class="usage-row-head">
                  <span class="usage-kind">{{ kindLabel(k) }}</span>
                  <span class="usage-kind-nums">
                    <b>{{ fmtTokens(v.prompt_tokens + v.completion_tokens) }}</b> tokens
                    <span class="usage-kind-split">·</span>
                    {{ v.calls }} 次
                    <span class="usage-kind-share">{{ usageKindShare(v.prompt_tokens + v.completion_tokens) }}</span>
                  </span>
                </div>
                <div class="usage-bar-wrap">
                  <div class="usage-bar" :style="{ width: usageBarWidth(v.prompt_tokens + v.completion_tokens) }" />
                </div>
              </div>
            </div>
          </div>
        </div>

        <p class="storage-tip">
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4">
            <circle cx="8" cy="8" r="6.5" /><path d="M8 5v3.5l2.5 1.3" stroke-linecap="round" />
          </svg>
          流式调用按字符数估算（≈1.6 字符/token），非流式调用优先使用 API 返回的精确值
        </p>
      </div>
      <el-empty v-else description="暂无调用记录（配置 Key 后使用 AI 功能即开始统计）" :image-size="60" />
    </el-card>
      </el-tab-pane>

      <el-tab-pane label="浏览器扩展" name="ext">
      <el-card class="set-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>浏览器扩展</span>
          <span class="card-head-tip">网页划词，随时调用本机 AI</span>
        </div>
      </template>

      <div class="ext-steps">
        <div class="ext-step">
          <span class="ext-step-no">1</span>
          <div class="ext-step-main">
            <div class="ext-step-title">下载并安装扩展</div>
            <div class="ext-step-desc">下载 zip 并解压 → 浏览器打开 <code>chrome://extensions</code>
              （Edge 用 <code>edge://extensions</code>）→ 打开「开发者模式」→「加载已解压的扩展程序」
              → 选中解压出的 ZhiYing-Extension 文件夹</div>
            <div class="ext-step-action">
              <el-button type="primary" @click="downloadExt">下载扩展</el-button>
            </div>
          </div>
        </div>

        <div class="ext-step">
          <span class="ext-step-no">2</span>
          <div class="ext-step-main">
            <div class="ext-step-title">复制配对令牌</div>
            <div class="ext-token-row">
              <el-input v-model="extToken" readonly class="ext-token-input" placeholder="令牌加载中…" />
              <el-button @click="copyExtToken">复制</el-button>
              <el-button text @click="regenExtToken">重新生成</el-button>
            </div>
          </div>
        </div>

        <div class="ext-step">
          <span class="ext-step-no">3</span>
          <div class="ext-step-main">
            <div class="ext-step-title">在扩展里完成配对</div>
            <div class="ext-step-desc">右键浏览器工具栏里的扩展图标 →「选项」→ 粘贴令牌；
              「主应用地址」保持 <code>http://127.0.0.1:8000</code>（改过启动端口才需同步改）
              → 保存 → 点「测试连接」</div>
          </div>
        </div>
      </div>

      <p class="ext-note">主应用需保持运行；扩展不联网，只和本机主应用通信。</p>
    </el-card>
      </el-tab-pane>

      <el-tab-pane label="陪练" name="drill">
      <el-card class="set-card" shadow="never">
      <template #header><span>AI 陪练 · 难度自适应</span></template>
      <p class="storage-tip">陪练出题时按**前序得分**自动升降难度。下列四项决定升降规则，保存后**下一次出题即生效**（已完成的场次不受影响）。</p>
      <el-form label-width="132px" class="drill-form">
        <el-form-item label="答得好判定线">
          <div class="drill-row">
            <el-input-number v-model="adaptUpScore" :min="50" :max="100" :step="5" size="small" />
            <span class="drill-hint">单题得分 ≥ 该分数记为「答得好」（默认 80）</span>
          </div>
        </el-form-item>
        <el-form-item label="答得差判定线">
          <div class="drill-row">
            <el-input-number v-model="adaptDownScore" :min="0" :max="79" :step="5" size="small" />
            <span class="drill-hint">单题得分 ≤ 该分数记为「答得差」（默认 50）</span>
          </div>
        </el-form-item>
        <el-form-item label="连续题数">
          <div class="drill-row">
            <el-input-number v-model="adaptStreak" :min="1" :max="10" :step="1" size="small" />
            <span class="drill-hint">连续 N 题同向即调一档；连续 2N 题调两档（默认 2）</span>
          </div>
        </el-form-item>
        <el-form-item label="最大档差">
          <div class="drill-row">
            <el-input-number v-model="adaptMaxShift" :min="0" :max="2" :step="1" size="small" />
            <span class="drill-hint">相对题纲难度最多升降几档，0 = 关闭自适应（默认 2）</span>
          </div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save()">保存</el-button>
        </el-form-item>
      </el-form>
    </el-card>
      </el-tab-pane>

      <el-tab-pane label="关于" name="about">
      <el-card class="set-card" shadow="never">
      <template #header>
        <div class="card-head">
          <span>关于知萤</span>
          <el-tag v-if="updateStore.hasUpdate" type="warning" size="small">
            可更新到 v{{ updateStore.latest }}
          </el-tag>
          <el-tag v-else-if="updateStore.upstreamHasUpdate" type="info" size="small">
            v{{ updateStore.latest }} 已忽略
          </el-tag>
          <el-tag v-else type="info" size="small">已是最新</el-tag>
        </div>
      </template>
      <div class="about-grid">
        <div class="about-row">
          <span class="about-k">当前版本</span>
          <span class="about-v">v{{ aboutVersion || updateStore.current || '—' }}</span>
        </div>
        <div class="about-row">
          <span class="about-k">运行平台</span>
          <span class="about-v">{{ aboutPlatform }}</span>
        </div>
        <div class="about-row">
          <span class="about-k">安装目录</span>
          <span class="about-v about-mono">{{ updateStore.installDir || '—' }}</span>
        </div>
        <div class="about-row">
          <span class="about-k">下载目录</span>
          <span class="about-v about-mono">{{ updateStore.downloadDir || '—' }}</span>
        </div>
      </div>
      <div class="about-ops">
        <el-button :loading="aboutChecking" @click="doCheckUpdate">检查更新</el-button>
        <el-button text type="primary" @click="router.push({ path: '/help', query: { sec: 'changelog' } })">查看更新日志</el-button>
      </div>
      <p class="storage-tip">
        有新版本时，侧栏底部版本号旁会出现红点；同一版本自动提示最多 3 次，之后只留红点。
        更新只替换程序目录，你的资料、笔记与配置存放在独立的数据目录，不受影响。
      </p>
      </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { Search, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { useRouter, useRoute } from 'vue-router'
import { settingsApi, updateApi } from '../api'
import { useUpdateStore } from '../stores/update'
import http from '../api/http'
import { errMsg } from '../api/http'
import { useAsr } from '../composables/useAsr'
import { useZipvoice } from '../composables/useZipvoice'
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

// 语音合成模型（ZipVoice，本地离线）状态与下载：与 ASR 同构
const {
  models: zvModels, installed: zvInstalled, downloading: zvDownloading,
  progress: zvProgress, downloadedMb: zvDownloadedMb, totalMb: zvTotalMb,
  error: zvError, load: loadZipvoice, download: downloadZipvoice,
} = useZipvoice()

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
    message: '现在去导入第一份材料，开始你的知萤之旅 · 点此前往材料库',
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
// 知识图谱：导入材料后自动抽取实体（默认关——每次抽取调 LLM 耗 token，成本主动权交给用户；
// 关闭时仍可在知识库页对单份材料手动点「生成图谱」）
const autoExtractEntities = ref(false)
const asrModelSize = ref('')   // 语音模型默认转写版本：''=自动，'small'/'base'=强制
// 语音合成引擎：'edge'=在线 / 'zipvoice'=本地（改动即时保存，播客页下次合成生效）
const ttsProvider = ref('edge')

async function saveTtsProvider() {
  try {
    await settingsApi.update({ tts_provider: ttsProvider.value })
    ElMessage.success(ttsProvider.value === 'zipvoice'
      ? '已切到本地合成（ZipVoice）' : '已切到在线合成（edge-tts）')
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  }
}
// 陪练 · 难度自适应参数（管理中心可调；出题时读取，改动即时生效）
const adaptUpScore = ref(80)
const adaptDownScore = ref(50)
const adaptStreak = ref(2)
const adaptMaxShift = ref(2)
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
  { key: 'prompt_learn_note', label: '简单学 · 讲义（叙事类）', desc: '叙事类材料（电子书 / 传记 / 小说）的讲义工艺：必记要点 / 探索历程 / 逐章转译 / ◆三点小结。⚠️ 未判定内容性质的老材料也走这套（默认工艺）', category: 'study' },
  { key: 'prompt_learn_note_conceptual', label: '简单学 · 讲义（认知思维类）', desc: '认知类材料（心理 / 商业 / 理论）的讲义工艺：核心问题 / 底层假设 / 思维框架 / 适用边界；有清晰章节时沿用章回体', category: 'study' },
  { key: 'prompt_learn_note_practical', label: '简单学 · 讲义（工具实用类）', desc: '工具类材料（技术手册 / 操作指南 / 规范）的讲义工艺：操作流程 S{n} / 判断标准 / 禁忌坑点 / 适用边界；不套场景比喻', category: 'study' },
  { key: 'prompt_learn_note_fragment', label: '简单学 · 讲义（碎片内容）', desc: '图片 OCR / 音视频转写的要点清单工艺：逐条信息点 + 标注出处；禁止编造章节与隐喻、禁止补全与拔高', category: 'study' },
  { key: 'prompt_learn_note_chapter', label: '简单学 · 讲义（分章）', desc: '长材料按章生成时的章级工艺：本章必记要点 / 逐节转译 / 本章小结；只讲本章、不提全书', category: 'study' },
  { key: 'prompt_drill_examiner', label: 'AI 陪练 · 出题', desc: '按考点出题 / 追问（出题纲·出题·追问三模式共用，注意保留 JSON 输出格式说明）', category: 'study' },
  { key: 'prompt_drill_judge', label: 'AI 陪练 · 评分', desc: '对作答打分并给出维度评价 / 遗漏点 / 追问建议（注意保留 JSON 输出格式说明）', category: 'study' },
  { key: 'prompt_note_rewrite', label: '文本改写', desc: '改写选中文字或整条内容（笔记 / 材料文本共用，更通顺/专业/简洁）', category: 'note' },
  { key: 'prompt_note_expand', label: '文本扩写', desc: '补充背景/例子/细节，展开要点（笔记 / 材料文本共用）', category: 'note' },
  { key: 'prompt_note_summarize', label: '文本总结', desc: '提炼压缩为精炼要点（笔记 / 材料文本共用）', category: 'note' },
  { key: 'prompt_note_continue', label: '文本续写', desc: '接着已有内容往下续写（笔记 / 材料文本共用）', category: 'note' },
  // ---- AI 播客 / 周报（快速模式 direct 版提示词刻意不开放 UI，走默认模板；需要时再加回）----
  { key: 'prompt_podcast_brief', label: 'AI 播客 · 知识简报', desc: '素材提炼成结构化简报（提炼层，可单独转笔记）。注意保留 {brief_chars} 字数占位符', category: 'podcast' },
  { key: 'prompt_podcast_script', label: 'AI 播客 · 对话脚本', desc: '知识简报改写成双人对话（两步式）。保留 JSON 数组输出说明与 {script_chars} / {max_chars} / {seg_count} / {avg_chars} 占位符', category: 'podcast' },
  { key: 'prompt_podcast_script_solo', label: 'AI 播客 · 单人精讲脚本', desc: '知识简报改写成单人精讲口播（两步式）。保留 JSON 数组输出说明与 {script_chars} / {max_chars} / {seg_count} / {avg_chars} 占位符', category: 'podcast' },
  // ---- AI 视频（模块 K）：三条提示词构成一条链路 —— 讲稿 → 结构图元素 → 定位锚点 ----
  { key: 'prompt_video_script', label: 'AI 视频 · 讲稿', desc: '素材改写成单人讲解口播。保留 JSON 数组输出说明与 {script_chars} / {seg_count} / {max_chars} / {avg_chars} 占位符；每句里要有原文原样存在的多字短语（4-12 字），否则画面定位不到', category: 'video' },
  { key: 'prompt_video_points', label: 'AI 视频 · 结构图', desc: '讲稿拆成结构图元素（三类：概念 / 好处 / 代价）。保留 JSON 输出格式说明与 {point_count} / {per_seg} / {seg_count} / {max_group} 占位符。⚠️ {point_count} 是**密度下限**不是参考值：少给会让画面长时间静止、过不了质量自检', category: 'video' },
  { key: 'prompt_video_anchor', label: 'AI 视频 · 定位锚点', desc: '为每句挑 1-4 个用于在材料原件里定位的锚点短语。系统会把材料的原文片段一并交给模型，锚点必须从那一段里原样摘取（不给原文时模型只能猜，定位命中率上不去）', category: 'video' },
  { key: 'prompt_stats_report', label: '学习周报', desc: '统计页周报：按数据摘要 / 问题诊断 / 行动建议 / 进阶方法四段输出', category: 'other' },
  { key: 'prompt_recall', label: '复述卡', desc: '从笔记生成自我复述提示（保留 JSON 输出格式说明）', category: 'other' },
]
const prompts = reactive({
  prompt_summary: '', prompt_keywords: '', prompt_explain: '',
  prompt_kb_qa: '', prompt_general: '', prompt_review: '', prompt_suggest: '', prompt_quiz: '', prompt_learn_note: '',
  prompt_learn_note_conceptual: '', prompt_learn_note_practical: '', prompt_learn_note_fragment: '',
  prompt_learn_note_chapter: '',
  prompt_note_rewrite: '', prompt_note_expand: '', prompt_note_summarize: '', prompt_note_continue: '',
})
const promptDefaults = reactive({})
const saving = ref(false)

// 智能体提示词页：搜索、分类与状态
const promptSearch = ref('')
const activePromptCategory = ref('all')

// ⚠️ 新增一个提示词分类要**同时**改这里和下面的 promptCategories()：
//    分组标题取自本表（漏了 → 标题渲染成 undefined），而左侧分类导航是显式枚举的
//    （漏了 → 新分类的 chips 压根不存在，用户只能靠搜索框找到那些提示词）。
const CATEGORY_LABELS = {
  study: '学习理解',
  note: '笔记加工',
  podcast: 'AI 播客',
  video: 'AI 视频',
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
    { key: 'video', label: CATEGORY_LABELS.video, count: counts.video },
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
const usagePeriod = ref('today')
const usageLoading = ref(false)

// Token 统计业务展示顺序（按业务流），未收录的新 kind 自动排在最后
const KIND_ORDER = ['summary', 'section_summary', 'keywords', 'explain', 'ask', 'transform',
                    'chat', 'suggest', 'review', 'quiz', 'learn_note', 'drill', 'drill_judge', 'stats_report',
                    'podcast_brief', 'podcast_script', 'video_script',
                    'video_points', 'video_points_graph', 'video_points_graph_paper',
                    'video_points_graph_clean', 'video_points_ppt', 'video_points_board',
                    'video_points_motion',
                    'knowledge_graph', 'embedding_index', 'embedding_query', 'test']
const usageKinds = computed(() => {
  const rows = usage[usagePeriod.value] || { _total: { calls: 0, tokens: 0 } }
  const { _total, ...rest } = rows
  const ordered = {}
  for (const k of KIND_ORDER) {
    if (rest[k]) ordered[k] = rest[k]
  }
  for (const [k, v] of Object.entries(rest)) {
    if (!ordered[k]) ordered[k] = v
  }
  return ordered
})
const usageKindsSorted = computed(() => {
  return Object.entries(usageKinds.value)
    .sort((a, b) => (b[1].prompt_tokens + b[1].completion_tokens) - (a[1].prompt_tokens + a[1].completion_tokens))
})
const usageMaxTokens = computed(() => {
  return usageKindsSorted.value.reduce((m, [, v]) => Math.max(m, v.prompt_tokens + v.completion_tokens), 0)
})
const usageTotalTokensSelected = computed(() => usage[usagePeriod.value]?._total?.tokens || 0)
const usageKindShare = (tokens) => {
  const total = usageTotalTokensSelected.value
  if (!total) return '0%'
  return Math.max(0.1, Math.round((tokens / total) * 1000) / 10).toFixed(1).replace('.0', '') + '%'
}
const usageBarWidth = (tokens) => {
  const max = usageMaxTokens.value || 1
  return Math.max(2, Math.round((tokens / max) * 100)) + '%'
}
const usagePercent = (part, total) => {
  if (!total) return '0%'
  return Math.round((part / total) * 1000) / 10 + '%'
}
const kindLabel = (k) => ({
  summary: '摘要', section_summary: '分章总结', keywords: '知识点',
  explain: '解读/追问', ask: '材料提问', transform: '文本加工',
  chat: '问答', suggest: '追问建议', review: '复习出题', quiz: '测一测',
  learn_note: '简单学',
  drill: 'AI 陪练', drill_judge: '陪练评分',
  stats_report: '学习报告', podcast_brief: '播客简报', podcast_script: '播客脚本',
  video_script: '视频讲稿', video_points: '视频结构图（历史）',
  video_points_graph: '结构图', video_points_graph_paper: '纸感结构图',
  video_points_graph_clean: '清透结构图', video_points_ppt: 'PPT 课件',
  video_points_board: '板书', video_points_motion: '动效图解',
  knowledge_graph: '知识图谱',
  embedding_index: '向量入库', embedding_query: '检索向量', test: '连接测试',
}[k] || k)
const fmtTokens = (n) => n >= 10000 ? (n / 10000).toFixed(1) + 'w' : (n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n))

async function loadUsage() {
  usageLoading.value = true
  try {
    const { data } = await settingsApi.usage()
    Object.assign(usage, data)
  } finally {
    usageLoading.value = false
  }
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

// 浏览器扩展配对令牌（GET /api/ext/token 免 token，仅本页展示用；跨站网页被 Sec-Fetch-Site 防护拦）
const extToken = ref('')
async function loadExtToken() {
  try {
    const { data } = await http.get('/ext/token')
    extToken.value = data.token || ''
  } catch (e) {
    extToken.value = ''
  }
}
async function downloadExt() {
  // blob 下载：window.open 在桌面壳（WebView2）里不一定触发保存；这条路径两处都稳
  try {
    const resp = await http.get('/ext/download', { responseType: 'blob' })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    a.download = 'ZhiYing-Extension.zip'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    ElMessage.success('扩展 zip 已开始下载，解压后按第 1 步说明安装')
  } catch (e) {
    const status = e && e.response && e.response.status
    if (status === 404) {
      ElMessage.error('主应用版本过旧（没有下载接口），请重启主应用后再试')
    } else {
      ElMessage.error('下载失败：' + ((e && e.message) || '请稍后重试'))
    }
  }
}
async function copyExtToken() {
  if (!extToken.value) { ElMessage.warning('令牌还没加载出来，请稍后重试'); return }
  try {
    await navigator.clipboard.writeText(extToken.value)
    ElMessage.success('已复制，去扩展设置页粘贴')
  } catch (e) {
    ElMessageBox.alert(extToken.value, '手动复制令牌', { confirmButtonText: '知道了' })
  }
}

// 重新生成配对令牌（POST /api/ext/token/rotate）：旧令牌立即失效，已配对的扩展须重贴
async function regenExtToken() {
  try {
    await ElMessageBox.confirm(
      '重新生成后旧令牌立即失效，已配对的浏览器扩展需要到「选项」页重新粘贴新令牌。',
      '重新生成配对令牌？',
      { confirmButtonText: '重新生成', cancelButtonText: '取消', type: 'warning' }
    )
  } catch (e) {
    return   // 用户取消
  }
  try {
    const { data } = await http.post('/ext/token/rotate')
    extToken.value = data.token || ''
    ElMessage.success('已生成新令牌，请复制后到扩展里重新粘贴')
  } catch (e) {
    ElMessage.error('生成失败：' + ((e && e.message) || '请稍后重试'))
  }
}

// ---------- 顶部标题区吸顶：把 .page-head 的真实高度写进 CSS 变量 ----------
// ⚠️ 高度是内容驱动的（图标 44px / 标题 20px / 副标题 12.5px，副标题一换行就变）
// → tab 条的 sticky top 必须引用实测值，写死数字迟早静默错位。
const pageHeadRef = ref(null)
const headH = ref(0)
let headRO = null
onMounted(() => {
  const el = pageHeadRef.value && pageHeadRef.value.$el
  if (!el) return
  const sync = () => { headH.value = el.getBoundingClientRect().height }
  sync()
  if (typeof ResizeObserver !== 'undefined') {
    headRO = new ResizeObserver(sync)
    headRO.observe(el)
  }
})
onBeforeUnmount(() => {
  if (headRO) { headRO.disconnect(); headRO = null }
})

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
  autoExtractEntities.value = data.auto_extract_entities ?? false
  asrModelSize.value = data.asr_model_size || ''
  ttsProvider.value = data.tts_provider || 'edge'
  adaptUpScore.value = data.drill_adapt_up_score ?? 80
  adaptDownScore.value = data.drill_adapt_down_score ?? 50
  adaptStreak.value = data.drill_adapt_streak ?? 2
  adaptMaxShift.value = data.drill_adapt_max_shift ?? 2
  for (const p of promptFields) prompts[p.key] = data[p.key] || ''
  Object.assign(promptDefaults, data.prompt_defaults || {})
  loadStorage()
  loadUsage()
  loadAsr()
  loadZipvoice()
  loadExtToken()
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
      auto_extract_entities: autoExtractEntities.value,
      // 陪练 · 难度自适应（后端 adapt_params() 会兜底 + 钳制，这里原样提交）
      drill_adapt_up_score: adaptUpScore.value,
      drill_adapt_down_score: adaptDownScore.value,
      drill_adapt_streak: adaptStreak.value,
      drill_adapt_max_shift: adaptMaxShift.value,
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
// ---------- 关于 / 检查更新 ----------
const updateStore = useUpdateStore()
const aboutChecking = ref(false)
const aboutVersion = ref('')
const aboutPlatform = computed(() => {
  const map = { win: 'Windows', 'macos-arm64': 'macOS（Apple Silicon）', 'macos-x86_64': 'macOS（Intel）' }
  const k = updateStore.info?.platform
  return k ? (map[k] || k) : '—'
})

async function loadAbout() {
  updateStore.hydrate()
  try {
    // 版本号走轻接口（必成功）；更新信息走 check 的 6h 缓存（不额外打网络）
    const { data } = await http.get('/version')
    aboutVersion.value = data.version
  } catch { /* 静默 */ }
  try {
    const { data } = await updateApi.check(false)
    if (!updateStore.info) updateStore.info = data
  } catch { /* 静默：断网时只显示版本号 */ }
}
onMounted(loadAbout)

async function doCheckUpdate() {
  aboutChecking.value = true
  try {
    const r = await updateStore.manualCheck()
    if (!r.ok) {
      ElMessage.warning(r.message)
      return
    }
    if (!r.hasUpdate) {
      ElMessage.success('已是最新版本 v' + updateStore.current)
      return
    }
    updateStore.dialogOpen = true
  } finally {
    aboutChecking.value = false
  }
}
</script>

<style scoped>
/* ===== 顶部标题区 + 七个 tab 双吸顶（2026-10-22） ===== 
   滚动容器是 el-main（.app-main，overflow:auto）；中间祖先都不能有 overflow:hidden，
   否则 sticky 会退化成「原地不动」——el-tabs 本身不设 overflow，
   el-tabs__content 的 overflow:hidden 只包内容区、不包 header，故无需改动。
   负 margin 把底衬铺到 .page 的 32px 内边距之外，滚动时两侧不留透明缝。 */

/* 标题区：padding-top 12 + 下面 .settings-page 的 padding-top 16 = 改前的 28px，
   静止态位置逐像素不变；原 margin-bottom 18px 转成 padding-bottom，
   吸顶时这 18px 由底色盖住，滚动内容不会从缝里露出来。 */
.settings-page { padding-top: 16px; }
.settings-page :deep(.page-head) {
  position: sticky; top: 0; z-index: 21;
  margin: 0 -32px;
  padding: 12px 32px 18px;
  background: var(--asc-bg);
}

/* tab 条：sticky top 取标题区的实测高度（--asc-set-head-h 由 ResizeObserver 写入）；
   height:auto 是为了让 padding 不被 el-tabs 默认的 40px 定高挤掉。 */
.settings-tabs :deep(.el-tabs__header) {
  position: sticky; top: var(--asc-set-head-h, 79px); z-index: 20;
  height: auto;
  margin: 0 -32px 16px;
  padding: 0 32px;
  background: var(--asc-bg);
  box-shadow: 0 1px 0 var(--asc-border);
}
.settings-tabs :deep(.el-tabs__item) { font-size: 14px; }
.set-card {
  max-width: 870px; margin-bottom: 18px;
  border: 1px solid var(--asc-border);
  box-shadow: 0 1px 4px rgba(0, 0, 0, .03);
}
.card-head { display: flex; justify-content: space-between; align-items: center; font-weight: 600; }
.test-result { margin-left: 12px; font-size: 13px; color: var(--asc-text-2); }
.field-hint { margin-left: 10px; font-size: 12.5px; color: var(--asc-text-3); white-space: nowrap; }
/* 知识图谱提示：三条独立成行 + 允许换行（覆盖 .field-hint 的 nowrap，防溢出画面） */
/* flex-basis:100% —— el-form-item 内容区是 flex 横排，强制提示区换到 checkbox 下一行 */
.kg-hint { margin-top: 2px; flex-basis: 100%; max-width: 100%; }
.kg-hint .field-hint { margin-left: 0; white-space: normal; line-height: 1.7; word-break: break-word; }
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

.usage-body { display: flex; flex-direction: column; gap: 20px; }

/* ===== Token 消耗页 redesign ===== */
.card-head-actions { display: flex; align-items: center; gap: 12px; }

.usage-metrics { display: grid; grid-template-columns: repeat(2, minmax(140px, 1fr)); gap: 16px; }
.usage-metric-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: var(--asc-radius); padding: 18px 18px 14px;
  transition: border-color .18s ease, box-shadow .18s ease;
}
.usage-metric-card:hover { border-color: var(--asc-primary); box-shadow: var(--asc-shadow-hover); }
.usage-metric-top { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.usage-metric-num { font-size: 28px; font-weight: 700; color: var(--asc-text); letter-spacing: -.5px; }
.usage-metric-unit { font-size: 13px; color: var(--asc-text-3); font-weight: 500; }
.usage-metric-label { font-size: 13px; color: var(--asc-text-2); font-weight: 500; margin-bottom: 4px; }
.usage-metric-compare { font-size: 12px; color: var(--asc-text-3); }

.usage-section-title {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px;
  font-size: 14px; font-weight: 600; color: var(--asc-text);
}
.usage-section-sub { font-size: 12px; color: var(--asc-text-3); font-weight: 400; }

.usage-list { display: flex; flex-direction: column; gap: 10px; }
.usage-row {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 14px; border-radius: 10px;
  border: 1px solid var(--asc-border); background: var(--asc-card);
  transition: background .15s ease, border-color .15s ease;
}
.usage-row:hover { background: var(--asc-surface-2); border-color: var(--asc-primary); }
.usage-rank {
  flex: none; width: 24px; height: 24px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 600; color: var(--asc-text-2);
  background: var(--asc-surface-2);
}
.usage-row:first-child .usage-rank { background: var(--asc-primary); color: #fff; }
.usage-row-main { flex: 1; min-width: 0; }
.usage-row-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 8px;
}
.usage-kind { font-size: 13.5px; font-weight: 600; color: var(--asc-text); }
.usage-kind-nums { font-size: 12.5px; color: var(--asc-text-2); white-space: nowrap; }
.usage-kind-nums b { color: var(--asc-text); font-weight: 600; }
.usage-kind-split { margin: 0 5px; color: var(--asc-text-3); }
.usage-kind-share { margin-left: 8px; font-size: 11.5px; color: var(--asc-primary); font-weight: 500; }
.usage-bar-wrap { height: 6px; border-radius: 3px; background: var(--asc-surface-2); overflow: hidden; }
.usage-bar {
  height: 100%; border-radius: 3px;
  background: linear-gradient(90deg, var(--asc-primary) 0%, var(--asc-cyan) 100%);
  transition: width .35s ease-out;
}

.usage-refresh { margin-right: 4px; transition: transform .4s ease; }
.usage-refresh.spinning { animation: usage-spin 1s linear infinite; }
@keyframes usage-spin { to { transform: rotate(360deg); } }

.storage-tip { font-size: 13px; color: var(--asc-text-3); margin: 10px 0 0; }

@media (max-width: 620px) {
  .usage-metrics { grid-template-columns: 1fr; }
  .usage-row-head { flex-direction: column; align-items: flex-start; gap: 4px; }
  .usage-kind-nums { white-space: normal; }
  .card-head-actions { flex-wrap: wrap; justify-content: flex-end; }
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


/* 浏览器扩展 tab：编号步骤条（2026-09-24） */
.ext-steps { display: flex; flex-direction: column; }
.ext-step { display: flex; align-items: flex-start; gap: 14px; padding: 16px 4px; }
.ext-step:first-child { padding-top: 4px; }
.ext-step + .ext-step { border-top: 1px dashed var(--asc-border); }
.ext-step-no {
  flex: none; width: 26px; height: 26px; border-radius: 50%; margin-top: 1px;
  background: var(--asc-primary); color: #fff;
  font-size: 13px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.ext-step-main { flex: 1; min-width: 0; }
.ext-step-title { font-size: 14px; font-weight: 600; color: var(--asc-text); margin-bottom: 4px; }
.ext-step-desc { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.7; }
.ext-step-desc code, .ext-note code {
  background: var(--asc-bg); padding: 1px 6px; border-radius: 4px; font-size: 12px;
}
.ext-step-action { margin-top: 10px; }
.ext-token-row { display: flex; gap: 8px; align-items: center; margin-top: 6px; }
.ext-token-input { max-width: 380px; }
.ext-token-input .el-input__inner { font-family: Consolas, 'Courier New', monospace; font-size: 12.5px; }
.ext-note {
  margin: 4px 0 0; padding-top: 12px; border-top: 1px solid var(--asc-border);
  font-size: 12px; color: var(--asc-text-3);
}
/* ===== 陪练 · 难度自适应（优化 7） ===== */
.drill-form { margin-top: 14px; }
.drill-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.drill-hint { font-size: 12.5px; color: var(--asc-text-2); }
/* ===== 关于 / 检查更新 ===== */
.about-grid { display: flex; flex-direction: column; gap: 10px; max-width: 760px; }
.about-row { display: flex; align-items: flex-start; gap: 14px; font-size: 13px; }
.about-k { width: 76px; flex-shrink: 0; color: var(--asc-text-2); }
.about-v { color: var(--asc-text); word-break: break-all; }
.about-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px; color: var(--asc-text-2);
}
.about-ops { margin-top: 16px; display: flex; align-items: center; gap: 10px; }
</style>
