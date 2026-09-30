<template>
  <div class="study-page" v-loading="pageLoading">
    <!-- 材料解析中：整页进度遮罩，解析完成后自动加载内容 -->
    <div v-if="parsing" class="parsing-overlay">
      <div class="parsing-card">
        <div class="parsing-spinner"></div>
        <div class="parsing-title">{{ parsingLabel }}</div>
        <div class="parsing-track">
          <div class="parsing-fill" :class="{ indeterminate: isParsingImage }"
            :style="isParsingImage ? {} : { width: Math.max(parsePct, 2) + '%' }"></div>
        </div>
        <div class="parsing-pct">{{ isParsingImage ? '识别时间取决于图片大小与清晰度' : parsePct + '%' }}</div>
        <div class="parsing-hint">完成后会自动打开，无需停留在本页等待</div>
      </div>
    </div>
    <!-- 左栏：目录 + 笔记 -->
    <aside class="col-left" :style="{ width: leftShown + 'px' }">
      <div class="pane-title">目录</div>
      <div class="toc">
        <div v-for="s in sections" :key="s.path" class="toc-item" :class="{ active: s.key === activeSection }">
          <span class="toc-text" @click="scrollToSection(s)">{{ s.path }}</span>
          <el-button class="toc-sum" size="small" text type="primary"
            :loading="sectionSummarizing === s.path" @click="summarizeSection(s)">总结</el-button>
        </div>
        <div v-if="sections.length === 0 && !isImage" class="toc-pages">
          <span v-for="g in visiblePageChips" :key="g.page" class="page-chip" @click="scrollToPage(g.page)">{{ g.page }}</span>
          <span v-if="!pagesExpanded && pagedChunks.length > PAGES_COLLAPSE_AT" class="page-chip page-chip-more"
            @click="pagesExpanded = true">…{{ pagedChunks.length }}</span>
        </div>
      </div>
      <div v-if="hasHighlights" class="hl-legend">
        <span><i class="dot dot-chain"></i>已解读</span>
        <span><i class="dot dot-note"></i>已转笔记</span>
      </div>
    </aside>
    <div class="col-divider" title="拖拽调整宽度，双击复位" @mousedown="startDrag('left', $event)" @dblclick="resetWidth('left')"></div>

    <!-- 中栏：阅读器 -->
    <main ref="colCenterRef" class="col-center">
      <div class="reader-header">
        <div class="reader-title-wrap">
          <span v-if="material" class="reader-fmt" :class="'rfmt-' + material.format">{{ fmtLabel(material.format) }}</span>
          <span class="reader-title" :title="material?.title">{{ material?.title }}</span>
          <span v-if="material?.page_count" class="reader-pages">{{ pageUnitText }}</span>
        </div>
        <div class="reader-actions">
          <el-radio-group v-if="['pdf', 'docx', 'epub', 'jpg', 'jpeg', 'png', 'webp', 'bmp', 'mp3', 'wav', 'm4a', 'mp4'].includes(material?.format)" v-model="viewMode" size="small">
            <el-radio-button value="text">{{ isMedia ? '转写文本' : '文本视图' }}</el-radio-button>
            <el-radio-button value="origin">{{ isMedia ? '播放' : '原文视图' }}</el-radio-button>
          </el-radio-group>
          <el-button v-if="viewMode === 'text'" size="small" class="ra-ghost"
            @click="isMd ? editMdDocument() : toggleTranscriptEdit()">{{ isMd ? '编辑文本' : (transcriptEditing ? '完成' : (isMedia ? '编辑转写' : '编辑文本')) }}</el-button>
          <el-dropdown trigger="click" placement="bottom-end" popper-class="ra-more-pop">
            <el-button size="small" class="ra-ghost ra-more">更多<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="downloadFile">下载文件</el-dropdown-item>
                <el-dropdown-item v-if="material?.storage_mode === 'reference'" @click="relocateFile">重新定位</el-dropdown-item>
                <el-dropdown-item v-if="viewMode === 'origin' && ['pdf', 'docx', 'epub'].includes(material?.format)"
                  @click="toggleFullscreen">{{ isFullscreen ? '退出全屏' : '全屏' }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>

      <PdfReader v-if="viewMode === 'origin' && material?.format === 'pdf'" :key="'pdf-' + fileTs" :url="fileUrl(material.id)" :footprints="fpByPage" :highlights="highlights" @mouseup="onSelect" @scroll.passive="onReaderScroll" />

      <!-- Word「原文视图」：docx-preview 高保真还原版面（表格 / 图片 / 样式 / 分页） -->
      <DocxReader v-else-if="viewMode === 'origin' && isDocx" :key="'docx-' + fileTs" :url="fileUrl(material.id)" />

      <!-- EPUB「原文视图」：iframe 直出 zip 内章节，原书排版与插图原样生效 -->
      <EpubReader v-else-if="viewMode === 'origin' && isEpub" ref="epubRef" :chapters="epubChapters"
        :index="activePage" :base="epubBase"
        @change="onEpubChapterChange" @select="onEpubSelect" @hidesel="hideToolbar" @loaded="applyEpubHighlights" />

      <!-- 图片「原文视图」：直接展示原图 -->
      <div v-else-if="viewMode === 'origin' && isImage" class="image-origin">
        <img :src="fileUrl(material.id)" :alt="material?.title" />
      </div>

      <!-- 音视频「播放」视图：纯播放器 -->
      <div v-else-if="viewMode === 'origin' && isMedia" class="media-only">
        <video v-if="material?.format === 'mp4'" :src="fileUrl(material.id)" controls class="media-video" />
        <audio v-else :src="fileUrl(material.id)" controls class="media-audio" />
      </div>

      <!-- 文本视图：md 整篇渲染，其余格式按页呈现 -->
      <div v-else ref="readerRef" class="reader" :class="{ 'md-reader': isMd }"
        @mouseup="onSelect" @click="onReaderClick" @scroll.passive="onReaderScroll">
        <div v-if="isMedia" class="media-player">
          <video v-if="material?.format === 'mp4'" :src="fileUrl(material.id)" controls class="media-video" />
          <audio v-else :src="fileUrl(material.id)" controls class="media-audio" />
        </div>
        <div class="reader-inner">
        <!-- Markdown：整篇渲染成连续文档，不分页（md 编辑统一走 EditorView，此分支无编辑态） -->
        <template v-if="isMd">
          <div class="md-body">
            <div v-for="c in chunks" :key="c.id" :id="'page-' + c.page_no" :data-page="c.page_no" class="md-seg"
              :class="{ 'md-seg-flash': flashLocate && flashLocate.page === c.page_no }"
              v-html="renderMd(c.content)"></div>
          </div>
        </template>
        <!-- 其余格式：按页分组呈现 -->
        <template v-else>
          <template v-for="group in pagedChunks" :key="group.page">
            <div v-if="!isImage" :id="'page-' + group.page" class="page-marker" :class="{ 'seekable': isMedia }"
             @click="isMedia && seekToMinute(group.page)">
              <span>{{ blockUnitText(group.page) }}</span>
              <el-button v-if="transcriptEditing" class="page-del-btn edit-del-btn" size="small" text type="danger"
                @click.stop="deletePage(group.page)">删除本页</el-button>
            </div>
            <template v-if="transcriptEditing">
              <div v-for="c in group.items" :key="c.id" class="chunk-edit-row">
                <div class="edit-row-head">
                  <el-button class="edit-del-btn" size="small" text type="danger" @click="deleteChunk(c)">删除此块</el-button>
                </div>
                <textarea v-show="!editPreviewOn" v-model="transcriptDraft[c.id]" class="edit-textarea" :rows="editRows(transcriptDraft[c.id])"
                  @mouseup.stop="onEditSelect($event, c)" @keyup="onEditSelect($event, c)" @blur="hidePolishBar"></textarea>
                <div v-if="editPreviewOn" class="edit-preview md-body" v-html="renderMd(transcriptDraft[c.id] || '')"></div>
              </div>
            </template>
            <component :is="isRichMd ? 'div' : 'p'" v-else v-for="c in group.items" :key="c.id" class="chunk" :class="{ 'chunk-image': isImage, 'rich': isRichMd }" :data-page="group.page" :data-chunk="c.id" :data-section="c.section_path || ''" v-html="renderChunk(c)"></component>
          </template>
          <el-empty v-if="pagedChunks.length === 0" :description="isMedia ? '未识别到语音内容' : (isImage ? '未识别到文字，可在原文视图查看图片' : '无文本内容（扫描件请切换原文视图）')" />
        </template>
        <div v-if="transcriptEditing" class="transcript-save">
          <el-button type="primary" :loading="transcriptSaving" @click="saveTranscript">保存修改</el-button>
          <el-button v-if="isRichMd" text type="primary" class="edit-preview-btn" @click="editPreview = !editPreview">{{ editPreview ? '继续编辑' : '预览排版' }}</el-button>
          <span class="transcript-hint">修改会同步更新知识库检索，不影响已生成的摘要/知识点</span>
        </div>
        <!-- 语音模型下载引导（音视频未装 Whisper / 下载中时显示） -->
        <div v-if="isMedia && (!asrInstalled || asrDownloading)" class="asr-download-card">
          <div class="asr-dl-head">
            <span class="asr-dl-title">{{ asrDownloading ? '语音模型下载中' : '未检测到语音模型' }}</span>
            <span class="asr-dl-sub">{{ asrDownloading ? '请保持网络畅通，下载完成后自动开始转写' : '音视频转写需要本地 Whisper 模型，选择版本后下载' }}</span>
          </div>
          <el-radio-group v-if="!asrDownloading" v-model="asrSelect" class="asr-dl-select">
            <el-radio v-for="m in asrModels" :key="m.id" :value="m.id" class="asr-dl-radio">
              {{ m.name }}（{{ m.size_mb }}MB · {{ m.quality }}质量）
            </el-radio>
          </el-radio-group>
          <div v-for="m in asrModels" :key="'desc-' + m.id" class="asr-dl-desc">
            <span v-if="!asrDownloading && m.id === asrSelect" class="asr-dl-desc-text">✓ {{ m.pros }}　△ {{ m.cons }}</span>
          </div>
          <div class="asr-dl-action">
            <el-button v-if="!asrDownloading" type="primary" :disabled="!asrSelect" @click="downloadAsr(asrSelect)">
              下载文本转写语音模型
            </el-button>
            <template v-else>
              <el-progress :percentage="asrProgress" :stroke-width="12" style="flex: 1" />
              <span class="asr-dl-pct">{{ asrProgress }}%</span>
            </template>
          </div>
          <div v-if="asrError" class="asr-dl-error">
            <span class="asr-dl-error-text">{{ asrError }}</span>
            <el-button size="small" type="danger" plain @click="downloadAsr(asrSelect || 'base')">重试</el-button>
          </div>
        </div>
        </div>
      </div>
    </main>
    <div class="col-divider" title="拖拽调整宽度，双击复位" @mousedown="startDrag('right', $event)" @dblclick="resetWidth('right')"></div>

    <!-- 划线浮动工具条 -->
    <div v-if="toolbar.show" class="sel-toolbar" :style="{ left: toolbar.x + 'px', top: toolbar.y + 'px' }">
      <el-button v-if="toolbar.hasHighlight" size="small" text type="danger"
        @mousedown.prevent @click="doUnhighlight">取消划线</el-button>
      <span v-if="toolbar.hasHighlight" class="toolbar-sep"></span>
      <span v-for="c in HIGHLIGHT_COLORS" :key="c" class="hl-dot" :class="'dot-' + c"
        :title="`划为${colorLabel(c)}`" @mousedown.prevent @click="doHighlight(c)"></span>
      <span class="toolbar-sep"></span>
      <el-button size="small" type="primary" @click="doExplain" :loading="explaining">AI 解读</el-button>
      <el-button size="small" @click="selectionToNote">转笔记</el-button>
      <el-button size="small" @click="copySelection">复制</el-button>
    </div>

    <!-- 编辑模式：选中文字浮动工具条（改写/扩写/续写/总结 + 复制） -->
    <div v-if="polishBar.show" class="sel-toolbar polish-bar" :style="{ left: polishBar.x + 'px', top: polishBar.y + 'px' }">
      <el-button size="small" type="primary" @mousedown.prevent @click="openEditTransform('rewrite')">改写</el-button>
      <el-button size="small" type="primary" @mousedown.prevent @click="openEditTransform('expand')">扩写</el-button>
      <el-button size="small" type="primary" @mousedown.prevent @click="openEditTransform('continue')">续写</el-button>
      <el-button size="small" type="primary" @mousedown.prevent @click="openEditTransform('summarize')">总结</el-button>
      <span class="toolbar-sep"></span>
      <el-button size="small" @mousedown.prevent @click="copyEditSelection">复制</el-button>
    </div>

    <!-- 右栏：AI 面板 -->
    <aside class="col-right" :style="{ width: rightShown + 'px' }">
      <el-tabs v-model="activeTab" class="ai-tabs">
        <!-- 摘要 -->
        <el-tab-pane label="摘要" name="summary">
          <div v-if="summaryError" class="gen-error">
            <span class="gen-error-text">{{ summaryError }}</span>
            <el-button size="small" type="danger" plain @click="genSummary(!!summary)">重试</el-button>
            <el-button v-if="isNeedSetup(summaryError)" size="small" type="primary" plain @click="goLLMConfig">去配置</el-button>
          </div>
          <div v-if="summary || streamingSummary" class="summary-body pane-wrap">
            <div class="pane-scroll">
              <div ref="summaryMdEl" class="md-preview summary-text" v-html="renderSummaryMd(streamingSummary || summary.content)" @click="onSummaryClick"></div>
            </div>
            <div v-if="summaryLoading" class="gen-tip"><span class="asking-dots"><i></i><i></i><i></i></span>{{ summaryProgressTip }}</div>
            <el-progress v-if="summaryLoading && summaryProgress" class="gen-progress"
              :percentage="summaryPercent" :stroke-width="8" :show-text="false" />
            <div v-if="summary" class="summary-ops">
              <el-button v-if="!summaryNote" size="small" text type="primary"
                :disabled="summaryLoading" @click="summaryToNote">转笔记</el-button>
              <template v-else>
                <el-button size="small" text type="success"
                  @click="openNoteEditor(summaryNote)">✓ 已转笔记 · 查看</el-button>
                <el-button v-if="summaryStale" size="small" text type="warning"
                  :disabled="summaryLoading" @click="summaryToNote">摘要已更新 · 更新笔记</el-button>
              </template>
            </div>
            <div class="regen-row">
              <el-input v-model="regenInstruction" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                placeholder="补充指令，如：更精简 / 侧重数据" />
              <el-button size="large" :loading="summaryLoading" @click="genSummary(true)">再生成</el-button>
            </div>
          </div>
          <div v-else class="pane-empty">
            <div class="pe-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20"/><path d="M9 8h7M9 12h5"/>
              </svg>
            </div>
            <p class="pe-title">生成全文脉络摘要</p>
            <p class="pe-sub">层级大纲，每条要点标注出处页码</p>
            <el-button type="primary" :loading="summaryLoading" @click="genSummary(false)">生成脉络摘要</el-button>
            <div v-if="summaryLoading" class="gen-tip"><span class="asking-dots"><i></i><i></i><i></i></span>{{ summaryProgressTip }}</div>
            <el-progress v-if="summaryLoading && summaryProgress" class="gen-progress"
              :percentage="summaryPercent" :stroke-width="8" :show-text="false" />
          </div>
        </el-tab-pane>

        <!-- 核心知识点 -->
        <el-tab-pane label="知识点" name="keywords">
          <div v-if="keywordsError" class="gen-error">
            <span class="gen-error-text">{{ keywordsError }}</span>
            <el-button size="small" type="danger" plain @click="genKeywords(keywords.length > 0)">重试</el-button>
            <el-button v-if="isNeedSetup(keywordsError)" size="small" type="primary" plain @click="goLLMConfig">去配置</el-button>
          </div>
          <div v-if="keywords.length" class="kw-list pane-wrap">
            <div class="pane-scroll">
            <div v-for="(k, i) in keywords" :key="i" class="kw-card">
              <div class="kw-head">
                <span class="kw-concept">{{ k.concept }}</span>
                <span class="kw-page kw-page-link" @click="locateAndFlash(k.page_no)">P{{ k.page_no }}</span>
              </div>
              <div class="kw-exp">{{ k.explanation }}</div>
              <el-button size="small" text type="primary" :loading="kwAsking.has(k.concept)"
                @click="kwAsk(k)">追问</el-button>
              <el-button size="small" text type="primary" @click="showRelated(k)">相关</el-button>
              <el-button v-if="!kwTransferred.has(k.concept)" size="small" text type="primary"
                @click="keywordToNote(k)">转笔记</el-button>
              <el-button v-else size="small" text type="success"
                @click="openTransferredNote(k.concept)">✓ 已转笔记 · 查看</el-button>
            </div>
            </div>
            <div v-if="keywordsLoading" class="gen-tip"><span class="asking-dots"><i></i><i></i><i></i></span>正在重新提炼知识点…</div>
            <div class="regen-row">
              <el-input v-model="kwInstruction" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                placeholder="补充指令，如：侧重公式 / 更精简" />
              <el-button size="large" :loading="keywordsLoading" @click="genKeywords(true)">再生成</el-button>
            </div>
          </div>
          <div v-else class="pane-empty">
            <div class="pe-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.3 11 2.9 2.9 0 0 1 1.3 2.4V17h4v-.6a2.9 2.9 0 0 1 1.3-2.4A6 6 0 0 0 12 3z"/>
              </svg>
            </div>
            <p class="pe-title">提炼核心知识点</p>
            <p class="pe-sub">最值得记忆的概念，附出处页码</p>
            <el-button type="primary" :loading="keywordsLoading" @click="genKeywords(false)">提炼核心知识点</el-button>
            <div v-if="keywordsLoading" class="gen-tip"><span class="asking-dots"><i></i><i></i><i></i></span>正在提炼知识点，长文档约需 1-2 分钟…</div>
          </div>
        </el-tab-pane>

        <!-- 追问记录 -->
        <el-tab-pane label="追问" name="chains">
          <div class="pane-wrap">
          <div v-if="chains.length || streamingChain" class="pane-scroll">
          <div v-if="streamingChain" class="chain chain-streaming">
            <div v-if="streamingChain.type === 'explain'" class="chain-quote">「{{ streamingChain.selected_text }}」</div>
            <div v-else class="chain-q">问：{{ streamingChain.question }}</div>
            <div class="chain-a">{{ streamingChain.content }}<span class="stream-cursor">▍</span></div>
          </div>
          <div v-for="chain in chains" :key="chain.root_id" class="chain" :data-root="chain.root_id">
            <template v-for="item in chain.items" :key="item.id">
              <div v-if="item.type === 'explain'" class="chain-quote">
                「{{ item.anchor?.selected_text }}」
                <span v-if="item.anchor?.page_no" class="chain-page chain-page-link"
                  @click="locateAndFlash(item.anchor.page_no, item.anchor.selected_text)">P{{ item.anchor.page_no }}</span>
              </div>
              <div v-if="item.type === 'qa' || item.type === 'ask'" class="chain-q">问：{{ item.anchor?.question }}</div>
              <div class="chain-a">{{ item.content }}</div>
              <div class="chain-ops">
                <el-button v-if="!noteByAsset[item.id]" size="small" text type="primary"
                  @click="assetToNote(item)">转笔记</el-button>
                <el-button v-else size="small" text type="success"
                  @click="openNoteEditor(noteByAsset[item.id])">✓ 已转笔记 · 查看</el-button>
              </div>
            </template>
            <div v-if="chain.items[0]?.type === 'explain'" class="chain-continue">
              <el-tag v-if="activeChainId === chain.root_id" size="small" type="primary" effect="plain">追问中 ↓</el-tag>
              <el-button v-else size="small" text type="primary" @click="activeChainId = chain.root_id">继续此追问</el-button>
            </div>
          </div>
          </div>
          <div v-else class="pane-empty chains-empty">
            <div class="pe-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 12a8 8 0 0 1-8 8H5l-2 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z"/><path d="M8.5 12h.01M12 12h.01M15.5 12h.01"/>
              </svg>
            </div>
            <p class="pe-title">边读边问</p>
            <p class="pe-sub">划线圈选后「AI 解读」吃透，或直接在下方输入问题</p>
          </div>
          <div v-if="activeChain" class="ask-mode-bar">
            <span class="ask-mode-text">正在追问「{{ (activeChain.items[0]?.anchor?.selected_text || '').slice(0, 15) }}…」</span>
            <el-button size="small" text @click="activeChainId = null">退出，直接提问</el-button>
          </div>
          <div v-if="bottomAsking" class="asking-hint">
            <span class="asking-dots"><i></i><i></i><i></i></span>
            <span class="asking-text">伴伴正在思考，请稍候…</span>
          </div>
          <div class="regen-row">
            <el-input v-model="bottomQuestion" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
              :placeholder="bottomPlaceholder"
              @keyup.enter.exact.prevent="doAskBottom" />
            <el-button size="large" type="primary" :loading="bottomAsking" @click="doAskBottom">{{ bottomAsking ? '思考中…' : (activeChain ? '追问' : '提问') }}</el-button>
          </div>
          </div>
        </el-tab-pane>

        <!-- 测一测 -->
        <el-tab-pane label="测一测" name="quiz">
          <div v-if="quizError" class="gen-error">
            <span class="gen-error-text">{{ quizError }}</span>
            <el-button size="small" type="danger" plain @click="genQuiz">重试</el-button>
            <el-button v-if="isNeedSetup(quizError)" size="small" type="primary" plain @click="goLLMConfig">去配置</el-button>
          </div>
          <div v-if="quizCards.length" class="quiz-list pane-wrap">
            <div class="pane-scroll">
              <div v-for="(q, i) in quizCards" :key="q.id" class="quiz-card">
                <div class="quiz-q">{{ i + 1 }}. {{ q.question }}</div>
                <div class="quiz-opts">
                  <div v-for="(opt, oi) in q.options" :key="oi" class="quiz-opt"
                    :class="optClass(q, oi)" @click="pickQuiz(q, oi)">
                    <span class="quiz-key">{{ 'ABCD'[oi] }}</span>{{ opt }}
                    <span v-if="quizPicked[q.id] === oi && oi === q.correct_index" class="quiz-right-mark">✓</span>
                    <span v-else-if="quizPicked[q.id] === oi" class="quiz-wrong-mark">✗</span>
                  </div>
                </div>
                <div v-if="quizPicked[q.id] !== undefined" class="quiz-exp md-body" v-html="renderMd(q.answer)"></div>
                <div v-else class="quiz-pick-hint">点击选项作答</div>
              </div>
            </div>
            <div v-if="quizLoading" class="gen-tip"><span class="asking-dots"><i></i><i></i><i></i></span>正在重新出题…</div>
            <div class="regen-row">
              <el-button size="large" type="warning" plain :loading="quizLoading" @click="genQuiz">重新出题</el-button>
            </div>
          </div>
          <div v-else class="pane-empty">
            <div class="pe-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="9"/><path d="M9.5 9.3a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.2-2.5 3.6"/><path d="M12 17h.01"/>
              </svg>
            </div>
            <p class="pe-title">测一测掌握程度</p>
            <p class="pe-sub">AI 基于资料核心内容出题，并同步到复习模块</p>
            <el-button type="primary" :loading="quizLoading" @click="genQuiz">生成测试题</el-button>
            <div v-if="quizLoading" class="gen-tip"><span class="asking-dots"><i></i><i></i><i></i></span>正在出题，请稍候…</div>
          </div>
        </el-tab-pane>

        <!-- 简单学习：一键生成结构化讲义 + 出题入复习 -->
        <el-tab-pane label="简单学" name="simplelearn">
          <div v-if="learnError" class="gen-error">
            <span class="gen-error-text">{{ learnError }}</span>
            <el-button size="small" type="danger" plain @click="genLearn">重试</el-button>
            <el-button v-if="isNeedSetup(learnError)" size="small" type="primary" plain @click="goLLMConfig">去配置</el-button>
          </div>
          <!-- 生成中（尚无任何讲义内容）：阶段提示 + 进度 -->
          <div v-if="learnLoading && !learnDisplayContent" class="pane-empty">
            <div class="pe-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 3v3M12 18v3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M3 12h3M18 12h3M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>
              </svg>
            </div>
            <p class="pe-title">{{ learnStageText }}</p>
            <p class="pe-sub">可离开本页，稍后回来继续查看</p>
            <el-progress class="gen-progress" :percentage="learnPercent" :stroke-width="8" :show-text="false" />
          </div>
          <!-- 已有讲义（含流式实时打字 / 已落库） -->
          <div v-else-if="learnNote || learnDisplayContent" class="summary-body pane-wrap">
            <div class="pane-scroll">
              <div ref="learnMdEl" class="md-preview summary-text" v-html="renderLearnMd(learnDisplayContent)" @click="onSummaryClick"></div>
            </div>
            <div v-if="learnLoading" class="gen-tip"><span class="asking-dots"><i></i><i></i><i></i></span>{{ learnStageText }}</div>
            <!-- 讲义生成成功、只是出题失败：只提示这一项，不判整体失败 -->
            <div v-if="learnQuizError && !learnLoading" class="learn-quiz-tip is-error">
              <span>{{ learnQuizError }}</span>
              <el-button size="small" text type="primary" :loading="learnQuizLoading"
                @click="retryLearnQuiz">重试出题</el-button>
            </div>
            <div v-if="learnQuizCount > 0" class="learn-quiz-tip">
              <span>{{ learnQuizCount }} 道题已同步到「测一测」</span>
              <el-button size="small" text type="primary" @click="activeTab = 'quiz'">去查看</el-button>
            </div>
            <div class="summary-ops">
              <el-button v-if="!learnSavedNote" size="small" text type="primary"
                :disabled="learnLoading" @click="learnToNote">转笔记</el-button>
              <template v-else>
                <el-button size="small" text type="success"
                  @click="openNoteEditor(learnSavedNote)">✓ 已转笔记 · 查看</el-button>
                <el-button v-if="learnSavedStale" size="small" text type="warning"
                  :disabled="learnLoading" @click="learnToNote">讲义已更新 · 更新笔记</el-button>
              </template>
            </div>
            <div class="regen-row">
              <el-button size="large" :loading="learnLoading" @click="genLearn">重新生成</el-button>
            </div>
          </div>
          <!-- 空态 -->
          <div v-else class="pane-empty">
            <div class="pe-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20"/><path d="M12 7v5M9.5 9.5h5"/>
              </svg>
            </div>
            <p class="pe-title">一键生成学习讲义</p>
            <p class="pe-sub">结构化笔记 + 记忆钩子，并出题同步到复习</p>
            <el-button type="primary" :loading="learnLoading" @click="genLearn">生成学习讲义</el-button>
          </div>
        </el-tab-pane>

        <!-- 笔记 -->
        <el-tab-pane label="笔记" name="notes">
          <div class="notes-pane pane-wrap">
            <div class="pane-scroll">
              <div class="notes-pane-head">
                <span class="notes-count">共 {{ notes.length }} 条</span>
                <span class="notes-ops">
                  <el-button size="small" text type="primary" @click="exportNotes">导出</el-button>
                  <el-button size="small" text type="primary" @click="openNoteEditor()">新建</el-button>
                </span>
              </div>
              <div v-if="notes.length" class="notes-batch">
                <div class="batch-info">
                  <span class="batch-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                    </svg>
                  </span>
                  <div class="batch-text">
                    <span class="batch-title">批量复习</span>
                    <span class="batch-sub">把 {{ notes.length }} 条笔记出成复习卡</span>
                  </div>
                </div>
                <el-button size="small" type="warning" plain :loading="batchReviewing"
                  @click="addAllToReview">全部加入复习</el-button>
              </div>
              <div class="note-list">
                <div v-for="n in notes" :key="n.id" class="note-item" :class="{ 'note-item-ai': n.source_type === 'ai_asset' }" @click="openNoteEditor(n)">
                  <div class="note-title">
                    <span class="note-tag" :class="n.source_type === 'ai_asset' ? 'note-tag-ai' : 'note-tag-manual'">
                      {{ n.source_type === 'ai_asset' ? 'AI' : '手动' }}
                    </span>
                    <span class="note-title-text">{{ n.title }}</span>
                    <span v-if="n.anchor?.page_no" class="note-page note-page-link"
                      @click.stop="locateAndFlash(n.anchor.page_no, n.anchor.selected_text)">P{{ n.anchor.page_no }}</span>
                  </div>
                  <div v-if="n.content" class="note-snippet">{{ noteSnippet(n) }}</div>
                  <div class="note-meta">
                    <span class="note-time">{{ shortTime(n.updated_at) }}</span>
                    <span v-if="n.content" class="note-len">{{ n.content.length }} 字</span>
                  </div>
                </div>
                <div v-if="notes.length === 0" class="pane-empty note-empty-pane">
                  <div class="pe-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
                    </svg>
                  </div>
                  <p class="pe-title">暂无笔记</p>
                  <p class="pe-sub">划线圈选、转 AI 内容，或点右上角「新建」</p>
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </aside>

    <!-- 相关知识点弹窗：跨材料知识连接 -->
    <el-dialog v-model="relatedDialog.show" :title="`「${relatedDialog.concept}」的相关内容`" width="560px">
      <div v-loading="relatedDialog.loading" class="related-list">
        <el-empty v-if="!relatedDialog.loading && relatedDialog.items.length === 0"
          description="知识库里暂无其他相关内容" :image-size="80" />
        <div v-for="(it, i) in relatedDialog.items" :key="i" class="related-item" @click="openRelated(it)">
          <div class="related-title">{{ it.metadata?.material_title || '未知来源' }}
            <span v-if="it.metadata?.page_no" class="related-page">P{{ it.metadata.page_no }}</span>
            <span class="related-type">{{ { note: '笔记', summary: '摘要' }[it.ref_type] || '原文' }}</span>
          </div>
          <div class="related-snippet">{{ it.text.slice(0, 120) }}</div>
        </div>
      </div>
    </el-dialog>

    <!-- 笔记编辑弹窗（全屏 Vditor，共用组件：与知识库/统计/播客页同一内核） -->
    <NoteEditorDialog v-model="noteDialog.show" :note-id="noteDialog.id" :material-title="material?.title || ''"
      :create-draft="noteDialog.id ? null : { title: noteDialog.title, content: noteDialog.content, anchor: noteDialog.anchor, materialId }"
      :create-handler="onCreateNote" :jump-handler="onJumpAnchor" @changed="refreshNotes" />

    <!-- 编辑视图文本加工结果弹窗（原文 vs 结果，采用/放弃） -->
    <el-dialog v-model="editTransform.show" :title="editTransformTitle" width="560px" class="note-dialog">
      <div class="polish-block">
        <div class="polish-label">原文</div>
        <div class="polish-text">{{ editTransform.original }}</div>
      </div>
      <div class="polish-block polish-block-new">
        <div class="polish-label">{{ editTransform.mode === 'continue' ? '续写内容' : (TRANSFORM_LABELS[editTransform.mode] + '后') }}</div>
        <div class="polish-text">{{ editTransform.result }}<span v-if="editTransform.streaming" class="stream-cursor">▍</span></div>
      </div>
      <template #footer>
        <el-button @click="editTransform.show = false">放弃</el-button>
        <el-button type="primary" :disabled="editTransform.streaming" @click="adoptEditTransform">采用</el-button>
      </template>
    </el-dialog>

    <!-- 分章总结结果弹窗（B7） -->
    <el-dialog v-model="sectionDialog.show" :title="`本章总结：${sectionDialog.section}`" width="600px">
      <div class="md-preview" v-html="renderMd(sectionDialog.content)"></div>
      <template #footer>
        <el-button text type="primary" @click="sectionToNote">转笔记</el-button>
        <el-button @click="sectionDialog.show = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import { createMd } from '../utils/md'
import { materialApi, aiApi, noteApi, reviewApi, kbApi, statsApi, simpleLearnApi } from '../api'
import { errMsg } from '../api/http'
import PdfReader from '../components/PdfReader.vue'
import NoteEditorDialog from '../components/NoteEditorDialog.vue'
import DocxReader from '../components/DocxReader.vue'
import EpubReader from '../components/EpubReader.vue'
import { useAsr } from '../composables/useAsr'
import { streamSSE } from '../utils/sse'

// ⚠️ linkify 必开：剪藏正文首行是 `> 来源：https://…`，
// 不开 linkify 时 markdown-it 把裸 URL 当普通文本渲染（连 <a> 都不生成）→ 点了没反应。
// 实测：同一个 md 实例开 linkify 后该行即渲染成 <a href>。
// ⚠️ 外链新窗口由 createMd 统一负责（桌面端会交给系统浏览器，同标签会把应用导航走）。
const md = createMd({ breaks: true, linkify: true })
const renderMd = (text) => md.render(text || '')

// ---------- 摘要 / 讲义：按结构适配的线性图标（只作用于渲染，不入库） ----------
// ⚠️ 为什么必须另建实例：上面的 `md` 被材料正文（transcript）/ 题目解析 / 片段弹窗共用，
//    在它上面挂 heading 规则会把图标一并带到那些地方去。
// ⚠️ 为什么不做进内容层：摘要/讲义的 content 是**落库**的（AIAsset），且「转笔记」是
//    原文搬运（content: ln.content）→ 图标若进内容层会污染 DB / 笔记 / 知识库检索，
//    换一次图标规则还得全量重生成。
const mdSummary = createMd({ breaks: true, linkify: true })
const mdLearn = createMd({ breaks: true, linkify: true })
headingNumRule(mdSummary)
headingIconRule(mdLearn)
const renderSummaryMd = (text) => mdSummary.render(text || '')
const renderLearnMd = (text) => mdLearn.render(text || '')

// 讲义骨架标签 → 图标名。骨架由 prompt_learn_note 写死，实测 5 篇讲义命中率 100%
// （### C{n} 78 / ## B{n} 24 / ### 引入 24 / ### 本节小结 24 / ## 必记要点 5 / ## 探索历程 5）。
// ⚠️ `引入` 用**整词**匹配：若按前缀匹配，摘要里出现「## 一、引入…」这类原文标题会被误挂图标。
// ⚠️ `B\d+` / `C\d+` 后面必须跟 空格 / 冒号 —— 否则 `## B2B 产品设计`、`## C919 首飞`
//    这类正文标题会被当成章序号（产品语料里 B2B 是高频词，实测必须这么收）。
const LEARN_ICONS = [
  [/^必记要点/, 'target'],
  [/^探索历程/, 'route'],
  [/^记忆钩子/, 'anchor'],
  [/^B\d+[\s:：]/, 'book'],
  [/^C\d+[\s:：]/, 'dot'],
  [/^本节小结/, 'summary'],
  [/^引入$/, 'compass'],
  [/^(总结|全篇回顾|速查|要点回顾)/, 'flag'],
]

// 摘要侧只做「章节序号」标记：## 标题以序号开头时，把序号从正文里剥出来单独做成胶囊。
// ⚠️ 不按标题内容猜图标 —— 实测 380 条要点行里 9 类语义规则只命中 33%、兜底 67% 且含误命中
//    （「避免高估…难度」会被判成风险、「即可…」会被判成定义），
//    结果是「有的有图标有的没有」比「全都没有」更乱。
// ⚠️ 限定 1~2 位数字 + 分隔符，避免把 `## 2025 年是爆发年` 的正文数字当序号。
const HEAD_NUM_RE = /^(?:第[一二三四五六七八九十\d]{1,3}[章节讲篇部分]\s*|[（(]?[一二三四五六七八九十]{1,3}[）)、.．]\s*|\d{1,2}[、.．]\s?|\d{1,2}\s+)/

function headingIconRule(md) {
  const base = md.renderer.rules.heading_open || ((t, i, o, e, s) => s.renderToken(t, i, o))
  md.renderer.rules.heading_open = (tokens, idx, options, env, self) => {
    const open = tokens[idx]
    const inline = tokens[idx + 1]
    const raw = ((inline && inline.content) || '').trim()
    if (raw) {
      for (const [re, ico] of LEARN_ICONS) {
        if (re.test(raw)) { open.attrSet('data-ico', ico); break }
      }
    }
    return base(tokens, idx, options, env, self)
  }
}

function headingNumRule(md) {
  const base = md.renderer.rules.heading_open || ((t, i, o, e, s) => s.renderToken(t, i, o))
  md.renderer.rules.heading_open = (tokens, idx, options, env, self) => {
    const open = tokens[idx]
    const inline = tokens[idx + 1]
    const raw = ((inline && inline.content) || '').trim()
    // 只在 ## 上做；且要求「剥掉序号后还剩标题正文」（>=2 字）——流式打字时
    // 会先出现「## 一、」这种半截标题，不加这条会先闪一个孤零零的胶囊。
    if (raw && open.tag === 'h2') {
      const m = raw.match(HEAD_NUM_RE)
      if (m && raw.length - m[0].length >= 2) {
        const first = inline.children && inline.children[0]
        // ⚠️ 只在首个子节点是纯文本时就地截断；若标题以 **加粗** 开头则整条跳过，
        //    绝不改写 children 结构（宁可这一条没有胶囊）。
        if (first && first.type === 'text' && first.content.startsWith(m[0])) {
          first.content = first.content.slice(m[0].length)
          open.attrSet('data-num', m[0].trim().replace(/[.．、）)]$/, ''))
        }
      }
    }
    return base(tokens, idx, options, env, self)
  }
}

const route = useRoute()
const router = useRouter()
const materialId = Number(route.params.id)

// 「未配置模型」类错误判断：后端 NEED_SETUP 文案剥掉信号前缀后的稳定关键词
function isNeedSetup(msg) {
  if (!msg) return false
  return /指定用途|未配置完整|API Key|配置 LLM 模型/.test(msg)
}
// 跳转 LLM 配置页（中心 → 大模型配置）
function goLLMConfig() {
  router.push('/settings')
}

const pageLoading = ref(true)
const material = ref(null)
const chunks = ref([])
const notes = ref([])
const viewMode = ref('text')
// 材料解析中：整页进度遮罩 + 轮询，完成后自动加载
const parsing = ref(false)
const parsingFormat = ref('')
const parsePct = ref(0)
let parsePollTimer = null
const isParsingImage = computed(() => ['jpg', 'jpeg', 'png', 'webp', 'bmp'].includes(parsingFormat.value))
const parsingLabel = computed(() => {
  if (['mp3', 'wav', 'm4a', 'mp4'].includes(parsingFormat.value)) return '正在转写音视频…'
  if (isParsingImage.value) return '正在 OCR 识别图片…'
  return '正在解析材料…'
})
const isMedia = computed(() => ['mp3', 'wav', 'm4a', 'mp4'].includes(material.value?.format))
const isImage = computed(() => ['jpg', 'jpeg', 'png', 'webp', 'bmp'].includes(material.value?.format))
const isMd = computed(() => ['md', 'markdown'].includes(material.value?.format))
// docx/epub/pptx 的 chunk.content 在解析层已是 Markdown 语法（标题/列表/表格，见 parser.py），
// 文本视图按 md 整段渲染；pdf/图片 OCR/音视频转写是纯文本，维持原渲染（避免偶然字符被误当 md 语法）
const isRichMd = computed(() => ['docx', 'doc', 'epub', 'pptx', 'ppt'].includes(material.value?.format))
// Word「原文视图」仅支持 OOXML（.docx）；旧版 .doc 无法在浏览器端还原版面
const isDocx = computed(() => material.value?.format === 'docx')
// EPUB「原文视图」：章节清单按 spine 解析，index 与 chunk.page_no 一一对应
const isEpub = computed(() => material.value?.format === 'epub')
const epubChapters = ref([])
const epubBase = computed(() => materialApi.epubResBase(materialId))

// 计数单位随格式变化：音视频是「分钟」、EPUB 是「章」、其余是「页」
const pageUnitText = computed(() => {
  const n = material.value?.page_count || 0
  if (isMedia.value) return `约 ${n} 分钟`
  return isEpub.value ? `共 ${n} 章` : `共 ${n} 页`
})
const blockUnitText = (page) => {
  if (isMedia.value) return `▶ 第 ${page} 分钟`
  return isEpub.value ? `第 ${page} 章` : `第 ${page} 页`
}

async function loadEpubChapters() {
  try {
    const { data } = await materialApi.epubChapters(materialId)
    epubChapters.value = data.chapters || []
  } catch {
    epubChapters.value = []   // 清单取不到时原文视图给降级提示，不阻断文本视图
  }
}

// 语音模型（Whisper）状态与下载
const {
  models: asrModels, installed: asrInstalled, loading: asrLoading,
  downloading: asrDownloading, dlSize: asrDlSize, progress: asrProgress,
  error: asrError, load: loadAsr, download: downloadAsr,
} = useAsr()
const asrSelect = ref('small')

// 划线高亮：3 种预设颜色样式
const HIGHLIGHT_COLORS = ['yellow', 'green', 'blue']
const COLOR_LABELS = { yellow: '黄色', green: '绿色', blue: '蓝色' }
const colorLabel = (c) => COLOR_LABELS[c] || c

const activeTab = ref('summary')

const summary = ref(null)
const summaryLoading = ref(false)
const summaryError = ref('')
// 长文档摘要进度：后端逐组回传 progress 事件；短文档没有这个事件，走逐 token 流式
const summaryProgress = ref(null)
const summaryPercent = computed(() => {
  const p = summaryProgress.value
  return p && p.total ? Math.round(p.done / p.total * 100) : 0
})
const summaryProgressTip = computed(() => {
  const p = summaryProgress.value
  if (!p) return '正在生成摘要…'
  if (!p.done) return `长文档需分 ${p.total} 部分并行梳理，正在准备…`
  return `已完成 ${p.done} / ${p.total} 部分`
})
const streamingSummary = ref('')   // 流式生成中的摘要文本（逐字预览）
const regenInstruction = ref('')
const keywords = ref([])
const keywordsAssetId = ref(null)   // 当前知识点产物 id（转笔记溯源用）
const keywordsLoading = ref(false)
const keywordsError = ref('')
const kwInstruction = ref('')
const kwTransferred = reactive(new Set())
const kwNoteMap = reactive({})      // concept → note（已转笔记查看/跳原文）
const noteByAsset = reactive({})    // asset_id → note（追问链转笔记状态持久化）
const chains = ref([])
const explaining = ref(false)

const sectionSummarizing = ref('')
const sectionDialog = reactive({ show: false, section: '', content: '', anchor: null })

const toolbar = reactive({ show: false, x: 0, y: 0, text: '', page: 1, chunkId: null, range: null, hasHighlight: false, epub: false })

// 编辑模式选中文字 → AI 加工（改写/扩写/续写/总结，textarea 选区，非 window.getSelection）
const polishBar = reactive({ show: false, x: 0, y: 0, text: '', chunkId: null, page: 0, start: 0, end: 0 })

// 笔记加工（改写/扩写/总结）
const TRANSFORM_LABELS = { rewrite: '改写', expand: '扩写', continue: '续写', summarize: '总结' }
const readerRef = ref(null)
const colCenterRef = ref(null)

// ⚠️ 这三个 ref 会被 setup 阶段的 watch（见「摘要/讲义出处标注」）getter 直接读取，
//    watch 注册时会立即执行一次 getter → 必须在 ref 声明区就定义好，
//    否则触发 TDZ「Cannot access 'learnNote' before initialization」→ 整页白屏。
const learnNote = ref(null)          // 最新一版学习讲义（AIAsset type=learn_note）
const summaryMdEl = ref(null)        // 摘要 md 容器（供 (P页码) 后处理精确定位）
const learnMdEl = ref(null)          // 讲义 md 容器（同上）
const isFullscreen = ref(false)
const learnLive = ref('')            // 流式生成中的讲义正文累积（仅生成中有效；落库后由 learnNote 接管）

// 三栏拖拽调宽（左目录 / 右 AI 面板，中栏自适应）
const LEFT_MIN = 180, LEFT_MAX = 480, LEFT_DEFAULT = 240
const RIGHT_MIN = 260, RIGHT_MAX = 560, RIGHT_DEFAULT = 340
const WIDTHS_KEY = 'asc_study_widths'
const clampW = (v, min, max) => Math.min(max, Math.max(min, v))
// 用户可拖拽保存的左右栏宽度（响应式缩窄不影响这两个「用户值」，见下方 leftShown/rightShown）
const leftWidth = ref(LEFT_DEFAULT)
const rightWidth = ref(RIGHT_DEFAULT)
// 响应式：小屏下自动压低左/右栏「上限」，让中间阅读区（flex:1）拿到更多宽度。
// 用户拖拽保存的宽度不动（rightWidth/leftWidth 仍是用户值），只在展示层 min() 取响应式上限，
// 因此拖到更大的窗口即恢复用户宽度，且不会把临时缩窄写回 localStorage 污染偏好。
const windowWidth = ref(typeof window !== 'undefined' ? window.innerWidth : 1440)
let resizeTimer = null
function onWinResize() {
  clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => { windowWidth.value = window.innerWidth }, 80)
}
// 右栏展示上限：随窗口变窄逐档收紧（默认 340 也不得超过最大值）
// 2026-09-30：三档各 +20px，缓解小屏下 AI 面板（6 个 Tab）拥挤；中栏仍有充足阅读宽度
const rightCap = computed(() => {
  const w = windowWidth.value
  if (w <= 1080) return 260
  if (w <= 1200) return 280
  if (w <= 1366) return 320
  return RIGHT_MAX
})
// 左栏展示上限：窄屏同样让位，但保留目录基本可读宽度
const leftCap = computed(() => windowWidth.value <= 1200 ? 200 : LEFT_MAX)
const leftShown = computed(() => Math.min(leftWidth.value, leftCap.value))
const rightShown = computed(() => Math.min(rightWidth.value, rightCap.value))

function loadWidths() {
  try {
    const saved = JSON.parse(localStorage.getItem(WIDTHS_KEY) || '{}')
    leftWidth.value = clampW(saved.left || LEFT_DEFAULT, LEFT_MIN, LEFT_MAX)
    rightWidth.value = clampW(saved.right || RIGHT_DEFAULT, RIGHT_MIN, RIGHT_MAX)
  } catch { /* 静默 */ }
}

function startDrag(which, e) {
  e.preventDefault()
  const startX = e.clientX
  const startW = which === 'left' ? leftWidth.value : rightWidth.value
  const move = (ev) => {
    const dx = ev.clientX - startX
    const delta = which === 'left' ? dx : -dx   // 左栏向右拖变宽，右栏向右拖变窄
    const w = Math.round(startW + delta)
    if (which === 'left') leftWidth.value = clampW(w, LEFT_MIN, LEFT_MAX)
    else rightWidth.value = clampW(w, RIGHT_MIN, RIGHT_MAX)
  }
  const up = () => {
    document.removeEventListener('mousemove', move)
    document.removeEventListener('mouseup', up)
    document.body.classList.remove('dragging-col')
    localStorage.setItem(WIDTHS_KEY, JSON.stringify({ left: leftWidth.value, right: rightWidth.value }))
  }
  document.addEventListener('mousemove', move)
  document.addEventListener('mouseup', up)
  document.body.classList.add('dragging-col')
}

function resetWidth(which) {
  if (which === 'left') leftWidth.value = LEFT_DEFAULT
  else rightWidth.value = RIGHT_DEFAULT
  localStorage.setItem(WIDTHS_KEY, JSON.stringify({ left: leftWidth.value, right: rightWidth.value }))
}

loadWidths()
function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen()
  } else {
    colCenterRef.value?.requestFullscreen()
  }
}
function onFsChange() { isFullscreen.value = !!document.fullscreenElement }

const noteDialog = reactive({ show: false, id: null, title: '', content: '', anchor: null, sourceType: 'manual' })

// 按页分组
const pagedChunks = computed(() => {
  const map = new Map()
  for (const c of chunks.value) {
    if (!map.has(c.page_no)) map.set(c.page_no, [])
    map.get(c.page_no).push(c)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0]).map(([page, items]) => ({ page, items }))
})

// 无章节材料的页码导航：超过 24 页折叠，点「…N」展开全部（防页码矩阵像调试面板）
const PAGES_COLLAPSE_AT = 24
const pagesExpanded = ref(false)
const visiblePageChips = computed(() =>
  pagesExpanded.value ? pagedChunks.value : pagedChunks.value.slice(0, PAGES_COLLAPSE_AT)
)

// 目录：章节首现页（key 为归一化标题，用于当前章节「定位选择」高亮）
const normPath = (t) => (t || '').replace(/\s+/g, ' ').trim()
const sections = computed(() => {
  const seen = new Map()
  for (const c of chunks.value) {
    if (c.section_path && !seen.has(c.section_path)) seen.set(c.section_path, c.page_no)
  }
  return [...seen.entries()].map(([path, page]) => ({ path, page, key: normPath(path) }))
})
const activeSection = ref('')

const shortTime = (iso) => iso ? iso.slice(5, 16).replace('T', ' ') : ''

// 笔记卡片摘要：剥离常见 Markdown 标记，拼成单行纯文本
const noteSnippet = (n) => (n.content || '')
  .split('\n')
  .map(l => l.replace(/^#{1,6}\s+/, '').replace(/^[-*+]\s+/, '').replace(/^>\s*/, ''))
  .join(' ')
  .replace(/\*\*/g, '').replace(/`/g, '')
  .replace(/\s+/g, ' ')
  .trim()

// B8 阅读进度：滚动时计算当前页（防抖 1.5s 落库）
let progressTimer = null
// 当前阅读页码：即时更新，供「文本视图 / 原文视图」切换时恢复位置
const activePage = ref(1)
// 计算滚动容器内当前所在页（距容器顶部 120px 内最后一个页元素为准）
// 无页锚点时返回 0（不记录）：编辑态 / 图片原文视图等没有 [data-page]，若默认按第 1 页写入会把进度改坏
function currentPageOf(scroller) {
  const pages = scroller.querySelectorAll('[data-page]')
  if (!pages.length) return 0
  const scrollerTop = scroller.getBoundingClientRect().top
  let current = 1
  for (const el of pages) {
    if (el.getBoundingClientRect().top - scrollerTop < 120) current = Number(el.dataset.page)
    else break
  }
  return current
}
function onReaderScroll(e) {
  const scroller = e.target
  updateActiveSection(scroller)   // 即时更新目录「定位选择」高亮
  const current = currentPageOf(scroller)
  if (!current) return            // 该视图没有页锚点，不参与进度记录
  activePage.value = current
  if (progressTimer) clearTimeout(progressTimer)
  progressTimer = setTimeout(() => {
    if (material.value && current !== material.value.last_read_page) {
      material.value.last_read_page = current
      materialApi.update(materialId, { last_read_page: current })
    }
  }, 1500)
}

// 滚动时计算当前所在章节（md 按标题定位，其余按 chunk 的 section 定位）
function updateActiveSection(scroller) {
  const scrollerTop = scroller.getBoundingClientRect().top
  let current = ''
  if (isMd.value) {
    const headings = scroller.querySelectorAll('.md-body h1, .md-body h2, .md-body h3, .md-body h4, .md-body h5, .md-body h6')
    for (const h of headings) {
      if (h.getBoundingClientRect().top - scrollerTop < 120) {
        current = normPath(h.textContent)
      } else break
    }
  } else {
    for (const el of scroller.querySelectorAll('[data-section]')) {
      if (el.getBoundingClientRect().top - scrollerTop < 120) {
        const sec = el.getAttribute('data-section')
        if (sec) current = normPath(sec)
      } else break
    }
  }
  activeSection.value = current
}

// C6 导出笔记
function exportNotes() {
  const a = document.createElement('a')
  a.href = materialApi.exportNotesUrl(materialId)
  a.download = ''   // 后端已带 Content-Disposition 文件名，此处兜底触发下载行为
  document.body.appendChild(a)   // 挂到 DOM 再点击，规避 WebView 忽略游离节点
  a.click()
  document.body.removeChild(a)
  ElMessage.success('笔记导出中…')
}

// 下载文件：文本视图下载转写文本，原文视图下载原始文件
function downloadFile() {
  if (viewMode.value === 'origin') {
    // 原文视图：下载原件（原始文件）
    const a = document.createElement('a')
    a.href = materialApi.fileUrl(materialId) + '?download=1'
    a.download = ''   // 后端 Content-Disposition 已带文件名
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } else {
    // 文本视图：下载文本内容（统一导出 .md）
    const text = chunks.value.map(c => c.content || '').join('\n\n')
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${material.value?.title || '文本'}.md`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }
  ElMessage.success('开始下载')
}

// 原文视图 URL：带时间戳，重定位后 fileTs 变化触发图片/音视频重载（不丢阅读位置）
const fileTs = ref(0)
function fileUrl(id) {
  // v 参数用于避开浏览器/WebView 的「启发式缓存」：材料删除后 id 会被复用，
  // 若 URL 只含 id，换材料后会命中旧缓存、渲染出上一个材料的内容。
  // fileTs = 重定位后的刷新标识；material.created_at = 本材料的稳定版本标识。
  const tag = fileTs.value || (material.value && material.value.created_at) || ''
  return materialApi.fileUrl(id) + (tag ? '?v=' + encodeURIComponent(tag) : '')
}

// 重新定位：引用材料源文件被移动/改名后，重新指向新路径
async function relocateFile() {
  let path = ''
  if (window.pywebview && window.pywebview.api) {
    try {
      const files = await window.pywebview.api.pick_files()
      path = files?.[0] || ''
    } catch (e) {
      ElMessage.error('调用本地文件选择失败：' + (e?.message || e))
      return
    }
  } else {
    try {
      const { value } = await ElMessageBox.prompt(
        '请粘贴源文件的完整路径', '重新定位源文件',
        { inputType: 'text', inputPlaceholder: 'C:\\Users\\xxx\\资料\\xxx.pdf' })
      path = (value || '').trim()
    } catch { return }   // 用户取消
  }
  if (!path) return
  try {
    await materialApi.relocate(materialId, { new_path: path })
    fileTs.value = Date.now()   // 局部刷新原文视图（图片/音视频走时间戳，PDF 走 key 重建），不丢阅读位置
    ElMessage.success('已重新定位')
    try {
      await ElMessageBox.confirm('源文件内容可能已变化，是否重新解析以更新知识库索引？', '重新解析', {
        confirmButtonText: '重新解析', cancelButtonText: '暂不', type: 'info'
      })
      await materialApi.retryParse(materialId)
      if (material.value) material.value.parsed_status = 'parsing'   // 乐观更新，详情页显示解析中
      ElMessage.success('已开始重新解析，完成后刷新即可看到最新内容')
    } catch { /* 选「暂不」或重解析请求取消 */ }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '重新定位失败')
  }
}
const fmtLabel = (f) => ({ pdf: 'PDF', ppt: 'PPT', pptx: 'PPT', doc: 'WORD', docx: 'WORD', epub: 'EPUB', md: 'MD', markdown: 'MD', mp3: '音频', wav: '音频', m4a: '音频', mp4: '视频', jpg: '图片', jpeg: '图片', png: '图片', webp: '图片', bmp: '图片' }[f] || (f || '').toUpperCase())

// 音视频：点分钟标记跳转播放进度
function seekToMinute(minute) {
  const player = document.querySelector('.media-audio, .media-video')
  if (!player) return
  player.currentTime = (minute - 1) * 60
  player.play()
}

function scrollToPage(page) {
  // EPUB 原文视图：page_no 即章节序号，切章即可（iframe 由 activePage 驱动）
  if (viewMode.value === 'origin' && isEpub.value) {
    onEpubChapterChange(page)
    return
  }
  // PDF 原文视图：直接定位到对应页（页面容器常驻，可滚动）
  const pdfPage = document.querySelector('.pdf-page[data-page="' + page + '"]')
  if (viewMode.value === 'origin' && material.value?.format === 'pdf') {
    if (pdfPage) {
      pdfPage.scrollIntoView({ behavior: 'smooth' })
    } else {
      // 页容器尚未渲染（挂载初期）：轮询等待，不能落入下方「切回文本视图」
      restoreReadingPosition('origin', page)
    }
    return
  }
  viewMode.value = 'text'
  nextTick(() => document.getElementById('page-' + page)?.scrollIntoView({ behavior: 'smooth' }))
}

// EPUB 原文视图切章：与阅读进度共用 activePage，章节号即页码，切完直接落库
function onEpubChapterChange(page) {
  const n = Number(page) || 1
  activePage.value = n
  if (material.value && n !== material.value.last_read_page) {
    material.value.last_read_page = n
    materialApi.update(materialId, { last_read_page: n })
  }
}

// 切换「文本视图 / 原文视图」时保持阅读进度
// （两个视图互斥渲染，切换即重建 DOM，不做处理就会回到顶部）
async function restoreReadingPosition(view, page) {
  if (!page) return
  await nextTick()
  if (view === 'origin' && material.value?.format === 'pdf') {
    // PDF：页容器常驻但 canvas 懒渲染，页高随渲染变化 → 轮询校正，直到偏移连续两次不变
    let lastTop = -1
    let stable = 0
    let tries = 0
    const step = () => {
      const el = document.querySelector('.pdf-page[data-page="' + page + '"]')
      if (el) {
        el.scrollIntoView({ behavior: 'auto', block: 'start' })
        stable = el.offsetTop === lastTop ? stable + 1 : 0
        lastTop = el.offsetTop
        if (stable >= 2) return
      }
      if (++tries < 12) setTimeout(step, 150)
    }
    step()
    return
  }
  // 文本视图：优先页锚点，图片材料没有锚点时回退到带 data-page 的首个块
  const anchor = document.getElementById('page-' + page) ||
    document.querySelector('.reader [data-page="' + page + '"]')
  anchor?.scrollIntoView({ behavior: 'auto', block: 'start' })
}

watch(viewMode, (to) => { restoreReadingPosition(to, activePage.value) })

// 跳转 + 目标文本临时高亮 3 秒（text 为空则整段高亮）
const flashLocate = ref(null)
let flashLocateTimer = null
function locateAndFlash(page, text) {
  scrollToPage(page)
  flashLocate.value = { page, text: text || null }
  if (flashLocateTimer) clearTimeout(flashLocateTimer)
  flashLocateTimer = setTimeout(() => { flashLocate.value = null }, 3000)
}

// md 整篇渲染无分页锚点，目录跳转需滚动到对应章节标题
function scrollToSection(s) {
  activeSection.value = s.key
  if (isMd.value) {
    const body = document.querySelector('.md-body')
    if (body) {
      // 归一化空白（源文件可能含 \xa0 不间断空格），避免精确匹配失败
      const target = normPath(s.path)
      const headings = body.querySelectorAll('h1, h2, h3, h4, h5, h6')
      for (const h of headings) {
        if (normPath(h.textContent) === target) {
          h.scrollIntoView({ behavior: 'smooth', block: 'start' })
          return
        }
      }
    }
  }
  scrollToPage(s.page)
}

// ---------- B7 分章总结 ----------

async function summarizeSection(s) {
  sectionSummarizing.value = s.path
  try {
    const { data } = await aiApi.sectionSummary(materialId, s.path)
    sectionDialog.section = s.path
    sectionDialog.content = data.content
    sectionDialog.anchor = data.anchor
    sectionDialog.show = true
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '总结失败')
  } finally {
    sectionSummarizing.value = ''
  }
}

async function sectionToNote() {
  try {
    await createNote(`本章总结：${sectionDialog.section}`, sectionDialog.content,
      'ai_asset', null, { section_path: sectionDialog.section })
    await refreshNotes()
    sectionDialog.show = false
    ElMessage.success('已转笔记')
  } catch (e) {
    ElMessage.error('转笔记失败')
  }
}

// ---------- 划线高亮持久化（追问链 + 笔记锚点 → 正文标记） ----------

const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// 来源跳转带来的临时高亮（闪烁后常亮）：直接从路由参数派生，避开挂载时序问题
const flashHl = computed(() => {
  const page = Number(route.query.page)
  const hl = route.query.hl
  return page && hl ? { page, text: String(hl) } : null
})

// 跨页来源跳转（如知识库「跳转原文」）→ 3 秒临时高亮后清除 page/hl 参数，避免常亮/动画重播
let flashQueryTimer = null
watch(flashHl, (fl) => {
  if (!fl) return
  if (flashQueryTimer) clearTimeout(flashQueryTimer)
  flashQueryTimer = setTimeout(() => {
    const q = { ...route.query }
    delete q.page
    delete q.hl
    router.replace({ query: q })
  }, 3000)
})

// 按页聚合高亮：chain=已 AI 解读（紫）/ note=已转笔记（琥珀）
const hlByPage = computed(() => {
  const map = new Map()
  const add = (page, text, kind, refId) => {
    if (!page || !text) return
    if (!map.has(page)) map.set(page, [])
    map.get(page).push({ text: text.trim(), kind, refId })
  }
  for (const ch of chains.value) {
    const a = ch.items?.[0]?.anchor
    if (a?.selected_text) add(a.page_no, a.selected_text, 'chain', ch.root_id)
  }
  for (const n of notes.value) {
    if (n.anchor?.selected_text) add(n.anchor.page_no, n.anchor.selected_text, 'note', n.id)
  }
  return map
})
const hasHighlights = computed(() => hlByPage.value.size > 0)

// 用户主动划线高亮（3 色），持久化后按页聚合注入正文
const highlights = ref([])
const userHlByPage = computed(() => {
  const map = new Map()
  for (const h of highlights.value) {
    if (!h.page_no || !h.selected_text) continue
    if (!map.has(h.page_no)) map.set(h.page_no, [])
    map.get(h.page_no).push({ text: h.selected_text.trim(), kind: h.color })
  }
  return map
})

// PDF 原文视图的按页足迹计数（供 PdfReader 显示页内标记）
const fpByPage = computed(() => {
  const out = {}
  for (const [page, marks] of hlByPage.value.entries()) {
    const counts = { chain: 0, note: 0 }
    for (const m of marks) {
      if (m.kind === 'chain') counts.chain++
      else if (m.kind === 'note') counts.note++
    }
    out[page] = counts
  }
  return out
})

// 空白不敏感匹配：返回原文索引区间 [[start, end), ...]
function findRanges(content, needle) {
  const direct = []
  let i = content.indexOf(needle)
  while (i !== -1) { direct.push([i, i + needle.length]); i = content.indexOf(needle, i + 1) }
  if (direct.length) return direct
  const map = []
  let norm = ''
  for (let j = 0; j < content.length; j++) {
    if (!/\s/.test(content[j])) { norm += content[j]; map.push(j) }
  }
  const nn = needle.replace(/\s+/g, '')
  if (!nn) return []
  const ranges = []
  let k = norm.indexOf(nn)
  while (k !== -1) {
    ranges.push([map[k], map[k + nn.length - 1] + 1])
    k = norm.indexOf(nn, k + 1)
  }
  return ranges
}

// Word/PPT 解析出的表格以 Markdown（GFM）表格存放，需单独交给 markdown-it 渲染成真表格；
// 其余文本继续走纯文本 + 高亮的渲染路径（保字符偏移语义）。
const MD_TABLE_ROW = /^\s*\|.*\|\s*$/
const MD_TABLE_SEP = /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/

function splitTableBlocks(text) {
  const lines = (text || '').split('\n')
  const out = []
  let buf = []
  const flush = () => { if (buf.length) { out.push({ text: buf.join('\n'), table: false }); buf = [] } }
  let i = 0
  while (i < lines.length) {
    const cur = lines[i]
    const nxt = lines[i + 1]
    // 表格 = 一行 |...| + 紧随的分隔行 |---|---|；其后连续的 |...| 行都算表体
    if (MD_TABLE_ROW.test(cur) && nxt !== undefined && nxt.includes('-') && nxt.includes('|')
      && MD_TABLE_SEP.test(nxt)) {
      flush()
      const tbl = [cur, nxt]
      i += 2
      while (i < lines.length && MD_TABLE_ROW.test(lines[i])) { tbl.push(lines[i]); i++ }
      out.push({ text: tbl.join('\n'), table: true })
    } else {
      buf.push(cur)
      i++
    }
  }
  flush()
  return out
}

const MD_HEADING = /^(#{1,6})\s+(.+)$/

// 是否「内嵌图 OCR」块：content 以 `[图片]` 前缀开头（后端 parser 的独占约定）
const isImgOcr = (c) => (c.content || '').trimStart().startsWith('[图片]')

function renderChunk(c) {
  let html
  if (isRichMd.value) {
    html = renderChunkRich(c)
  } else {
    const content = c.content || ''
    const blocks = splitTableBlocks(content)
    if (blocks.length === 1 && !blocks[0].table) {
      html = renderBlock(c, blocks[0].text)
    } else {
      html = blocks.map(b => (b.table
        ? `<div class="md-table">${md.render(b.text)}</div>`
        : renderBlock(c, b.text))).join('')
    }
  }
  // 内嵌图 OCR 块：包一层卡片，视觉上与正文区分（span 保证在 <p>/<div> 容器内均合法）
  return isImgOcr(c) ? `<span class="imgocr-card">${html}</span>` : html
}

// 单块渲染：标题 → 标题样式（去掉 # 标记后再定位高亮，保证偏移正确）；其余走纯文本 + 高亮
function renderBlock(c, text) {
  const t = (text || '').trim()
  const m = t.includes('\n') ? null : t.match(MD_HEADING)
  if (m) {
    const lv = Math.min(m[1].length, 3)
    return `<div class="chunk-h lv${lv}">${renderChunkPlain(c, m[2])}</div>`
  }
  return renderChunkPlain(c, text)
}

// ---- 高亮共用件：mark 收集 / 优先级切分 / span 生成（纯文本路径与 rich-md 路径必须同源，
//      否则同一页划线在两种渲染下表现不一致） ----
const HL_KIND_PRIORITY = { 'flash-temp': 4, flash: 3, note: 2, chain: 1, yellow: 0, green: 0, blue: 0 }
const HL_KIND_TIP = { chain: '已 AI 解读，点击查看追问', note: '已转笔记', flash: '来源定位', 'flash-temp': '' }
const HL_USER_COLORS = ['yellow', 'green', 'blue']

function collectChunkMarks(c) {
  const fl = flashLocate.value
  const marks = [...(hlByPage.value.get(c.page_no) || [])]
  marks.push(...(userHlByPage.value.get(c.page_no) || []))
  if (flashHl.value && flashHl.value.page === c.page_no) {
    marks.push({ text: flashHl.value.text, kind: 'flash-temp' })
  }
  if (fl && fl.page === c.page_no && fl.text) {
    marks.push({ text: fl.text, kind: 'flash-temp' })
  }
  return marks
}

// 边界切分：重叠区间按优先级取胜者（flash 来源跳转 > note 笔记 > chain 解读 > 用户划线），
// 返回互不重叠、按起点排序的获胜区间 [{s, e, kind, refId}]；无命中返回 []
function segmentHighlightRanges(content, marks) {
  let ranges = []
  for (const m of marks) {
    for (const [s, e] of findRanges(content, m.text)) ranges.push({ s, e, kind: m.kind, refId: m.refId })
  }
  if (!ranges.length) return []
  const points = new Set([0, content.length])
  for (const r of ranges) { points.add(r.s); points.add(r.e) }
  const sorted = [...points].sort((a, b) => a - b)
  const segs = []
  for (let i = 0; i < sorted.length - 1; i++) {
    const s = sorted[i], e = sorted[i + 1]
    if (s >= e) continue
    const covering = ranges.filter(r => r.s < e && r.e > s)
    if (!covering.length) continue
    covering.sort((a, b) => HL_KIND_PRIORITY[b.kind] - HL_KIND_PRIORITY[a.kind])
    segs.push({ s, e, kind: covering[0].kind, refId: covering[0].refId })
  }
  return segs
}

function hlSpanOpen(kind, refId) {
  const isUser = HL_USER_COLORS.includes(kind)
  const cls = isUser ? `hl-user hl-${kind}` : `hl hl-${kind}`
  const refAttr = (kind === 'note' || kind === 'chain') && refId ? ` data-ref-id="${refId}"` : ''
  return `<span class="${cls}"${refAttr}${isUser ? '' : ` title="${HL_KIND_TIP[kind]}"`}>`
}

function renderChunkPlain(c, content) {
  const fl = flashLocate.value
  // 临时定位闪烁：无具体文本时整段高亮
  if (fl && fl.page === c.page_no && !fl.text) {
    return `<span class="hl-flash-temp">${escapeHtml(content)}</span>`
  }
  const segs = segmentHighlightRanges(content, collectChunkMarks(c))
  if (!segs.length) return escapeHtml(content)
  let html = ''
  let cursor = 0
  for (const seg of segs) {
    html += escapeHtml(content.slice(cursor, seg.s))
    html += hlSpanOpen(seg.kind, seg.refId) + escapeHtml(content.slice(seg.s, seg.e)) + '</span>'
    cursor = seg.e
  }
  html += escapeHtml(content.slice(cursor))
  return html
}

// docx/epub/pptx：chunk 已是 md 语法 → 整段交给 markdown-it。
// 高亮不能渲染后再找（raw 偏移 ≠ HTML 偏移），做法：先把获胜区间用 PUA 令牌包进 md 源码
// （\uE000..\uE003 不会被 markdown-it 转义/改写），渲染后整串替换为 <span>。
// ⚠️ 令牌只对「获胜区间」注入（互不重叠），天然避免了交叉区间的非法嵌套。
function renderChunkRich(c) {
  const content = c.content || ''
  const fl = flashLocate.value
  if (fl && fl.page === c.page_no && !fl.text) {
    return `<div class="hl-flash-temp">${md.render(content)}</div>`
  }
  const segs = segmentHighlightRanges(content, collectChunkMarks(c))
  if (!segs.length) return md.render(content)
  let src = ''
  let cursor = 0
  segs.forEach((seg, i) => {
    src += content.slice(cursor, seg.s)
    src += `\uE000${i}\uE001` + content.slice(seg.s, seg.e) + `\uE002${i}\uE003`
    cursor = seg.e
  })
  src += content.slice(cursor)
  let html = md.render(src)
  segs.forEach((seg, i) => {
    html = html.split(`\uE000${i}\uE001`).join(hlSpanOpen(seg.kind, seg.refId))
    html = html.split(`\uE002${i}\uE003`).join('</span>')
  })
  return html
}

// 点击高亮：解读标记 → 定位追问链；已转笔记标记 → 定位到对应笔记
function onReaderClick(e) {
  const chainEl = e.target.closest?.('.hl-chain')
  if (chainEl?.dataset.refId) {
    locateChain(Number(chainEl.dataset.refId))
    return
  }
  const noteEl = e.target.closest?.('.hl-note')
  if (noteEl?.dataset.refId) {
    const note = notes.value.find(n => n.id === Number(noteEl.dataset.refId))
    if (note) {
      activeTab.value = 'notes'
      openNoteEditor(note)
    }
  }
}

// 定位到指定追问链：切追问 tab、激活并滚动到该链
function locateChain(rootId) {
  activeTab.value = 'chains'
  activeChainId.value = rootId
  nextTick(() => {
    document.querySelector(`.chain[data-root="${rootId}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

// ---------- 划线 ----------

function onSelect(e) {
  const sel = window.getSelection()
  const text = sel?.toString().trim() || ''
  const inReader = e?.target?.closest?.('.reader, .pdf-reader')
  if (!text || !inReader) {
    toolbar.show = false
    return
  }
  const range = sel.getRangeAt(0)
  const rect = range.getBoundingClientRect()
  const startEl = range.startContainer.parentElement
  const chunkEl = startEl?.closest('[data-chunk]')
  const pageEl = startEl?.closest('[data-page]')
  toolbar.text = text
  toolbar.page = pageEl ? Number(pageEl.dataset.page) : 0
  toolbar.chunkId = chunkEl ? Number(chunkEl.dataset.chunk) : null
  toolbar.range = range.cloneRange()   // 克隆保存，点击工具条导致 selection 清除后仍可用
  toolbar.epub = false
  toolbar.hasHighlight = matchingHighlights(text, toolbar.chunkId, toolbar.page).length > 0
  toolbar.x = Math.min(rect.left, window.innerWidth - 320)
  toolbar.y = rect.bottom + 8
  toolbar.show = true
}

function hideToolbar() { toolbar.show = false }

// ---------- EPUB 原文视图选区（划线 / AI 解读 / 转笔记 / 复制共用入口） ----------

const epubRef = ref(null)

// EpubReader 已把选区坐标换算到顶层视口；原文视图没有 chunk 锚点（与 PDF 原文一致）
function onEpubSelect({ text, page, x, y }) {
  toolbar.text = text
  toolbar.page = page
  toolbar.chunkId = null
  toolbar.range = null        // 选区在 iframe 内，划线视觉走「文本匹配重刷」而非 range 操作
  toolbar.epub = true
  toolbar.hasHighlight = matchingHighlights(text, null, page).length > 0
  toolbar.x = x
  toolbar.y = y
  toolbar.show = true
}

// 把当前章的划线（含文本视图同章打的）推给 EpubReader 重刷；章加载完成 / 划线变动时调用
function applyEpubHighlights() {
  if (!isEpub.value || viewMode.value !== 'origin') return
  const list = highlights.value.filter(h => h.page_no === activePage.value)
  epubRef.value?.applyHighlights(list)
}
watch(highlights, applyEpubHighlights, { deep: true })

// 主文档与 iframe 的选区都要清（划线/解读完成后）
function clearAnySelection() {
  window.getSelection()?.removeAllRanges()
  if (toolbar.epub) epubRef.value?.clearSelection()
}

// ---------- 划线高亮（3 色） ----------

// PDF 原文视图划线：给相交的 textLayer span 设置/清除高亮（color 为 null 表示清除）
function setPdfHighlight(range, color) {
  const layer = range.startContainer.parentElement?.closest('.textLayer')
  if (!layer) return
  for (const span of layer.querySelectorAll('span')) {
    if (!range.intersectsNode(span)) continue
    span.classList.remove('hl-user', 'hl-yellow', 'hl-green', 'hl-blue')
    if (color) span.classList.add('hl-user', 'hl-' + color)
  }
}

// 找与选中文本匹配的已有高亮（用于取消 / 换色替换，避免叠加）
function matchingHighlights(text, chunkId, page) {
  if (!text) return []
  return highlights.value.filter(h => {
    if (!h.selected_text) return false
    if (chunkId != null && h.chunk_id != null) {
      if (h.chunk_id !== chunkId) return false
    } else if (page != null && h.page_no !== page) {
      return false
    }
    return h.selected_text.includes(text) || text.includes(h.selected_text)
  })
}

// Markdown 整篇渲染后重刷高亮（取消/换色后调用）
async function refreshMdHighlights() {
  if (!isMd.value) return
  await nextTick()
  const body = document.querySelector('.md-body')
  if (!body) return
  body.querySelectorAll('.hl-user').forEach(el => {
    const text = el.textContent
    el.parentNode.replaceChild(document.createTextNode(text), el)
  })
  for (const h of highlights.value) {
    wrapTextOccurrences(body, h.selected_text, `hl-user hl-${h.color}`)
  }
}

// 恢复划线：在 DOM 内按文本匹配包裹（Markdown 整篇渲染后使用）
function wrapTextOccurrences(root, text, className) {
  if (!text || !root) return
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes = []
  let n
  while ((n = walker.nextNode())) nodes.push(n)
  for (const node of nodes) {
    const val = node.nodeValue || ''
    if (!val.includes(text)) continue
    if (node.parentElement?.closest('.hl-user')) continue
    const parts = val.split(text)
    if (parts.length === 1) continue
    const frag = document.createDocumentFragment()
    parts.forEach((part, i) => {
      if (part) frag.appendChild(document.createTextNode(part))
      if (i < parts.length - 1) {
        const span = document.createElement('span')
        span.className = className
        span.textContent = text
        frag.appendChild(span)
      }
    })
    node.parentNode.replaceChild(frag, node)
  }
}

async function loadHighlights() {
  try {
    const { data } = await materialApi.highlights(materialId)
    highlights.value = data
  } catch { /* 静默 */ }
}

async function applyMdHighlights() {
  if (!isMd.value) return
  await nextTick()
  const body = document.querySelector('.md-body')
  if (!body) return
  for (const h of highlights.value) {
    wrapTextOccurrences(body, h.selected_text, `hl-user hl-${h.color}`)
  }
}

async function doHighlight(color) {
  // 使用 onSelect/onEpubSelect 时保存的选中信息，不依赖实时 selection（点击工具条会清除 selection）
  const text = (toolbar.text || '').trim()
  const range = toolbar.range
  const isEpubFrame = toolbar.epub
  if (!text || (!range && !isEpubFrame)) return
  const startEl = range?.startContainer?.parentElement
  const chunkId = toolbar.chunkId
  const page = toolbar.page
  const isPdf = !!startEl?.closest('.textLayer')

  try {
    const matched = matchingHighlights(text, chunkId, page)
    if (matched.length) {
      // 切换颜色：更新已有高亮的颜色（替换而非叠加）
      for (const h of matched) {
        if (h.color !== color) {
          const { data } = await materialApi.updateHighlight(materialId, h.id, { color })
          Object.assign(h, data)
        }
      }
    } else {
      // 新建高亮
      const { data } = await materialApi.createHighlight(materialId, {
        selected_text: text.slice(0, 500), color, page_no: page, chunk_id: chunkId,
      })
      highlights.value.push(data)
    }
    // 刷新展示：PDF 直接操作 span，EPUB 重刷 iframe，md 重刷，纯文本靠 renderChunk 响应式重渲染
    if (isPdf) {
      setPdfHighlight(range, color)
    } else if (isEpubFrame) {
      applyEpubHighlights()
    } else if (isMd.value) {
      await refreshMdHighlights()
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '划线失败'))
  }
  clearAnySelection()
  hideToolbar()
}

async function doUnhighlight() {
  const text = (toolbar.text || '').trim()
  const range = toolbar.range
  const isEpubFrame = toolbar.epub
  if (!text || (!range && !isEpubFrame)) return
  const chunkId = toolbar.chunkId
  const page = toolbar.page
  const isPdf = !!range?.startContainer?.parentElement?.closest('.textLayer')
  try {
    const matched = matchingHighlights(text, chunkId, page)
    for (const h of matched) {
      await materialApi.deleteHighlight(materialId, h.id)
    }
    if (matched.length) {
      highlights.value = highlights.value.filter(h => !matched.includes(h))
    }
    if (isPdf) {
      setPdfHighlight(range, null)
    } else if (isEpubFrame) {
      applyEpubHighlights()
    } else if (isMd.value) {
      await refreshMdHighlights()
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '取消划线失败'))
  }
  clearAnySelection()
  hideToolbar()
}

// ---------- AI 解读 / 追问 ----------

// 流式中的解读/提问占位（逐字填充）
const streamingChain = ref(null)   // { _tmp, type, question, content, selected_text }

async function doExplain() {
  explaining.value = true
  hideToolbar()
  clearAnySelection()
  activeTab.value = 'chains'
  const sel = toolbar.text
  streamingChain.value = { _tmp: Date.now(), type: 'explain', question: '', content: '', selected_text: sel }
  let newRootId = null
  try {
    await streamSSE('/api/ai/explain/stream',
      { material_id: materialId, selected_text: sel, anchor: { page_no: toolbar.page } },
      (t) => { if (streamingChain.value) streamingChain.value.content += t },
      (payload) => { newRootId = payload.root_id },
      (msg) => { throw new Error(msg) },
    )
  } catch (e) {
    ElMessage.error(e.message || '解读失败')
  } finally {
    explaining.value = false
    streamingChain.value = null
    await loadChains()
    if (newRootId) activeChainId.value = newRootId
  }
}

const activeChainId = ref(null)
const bottomQuestion = ref('')
const bottomAsking = ref(false)

const activeChain = computed(() =>
  activeChainId.value
    ? (chains.value.find(c => c.root_id === activeChainId.value) || null)
    : null)

const bottomPlaceholder = computed(() => {
  if (!activeChain.value) return '直接对当前材料提问…'
  const sel = activeChain.value.items[0]?.anchor?.selected_text || ''
  return `追问「${sel.slice(0, 15)}${sel.length > 15 ? '…' : ''}」`
})

async function doAskBottom() {
  const q = bottomQuestion.value.trim()
  if (!q || bottomAsking.value) return
  bottomAsking.value = true
  bottomQuestion.value = ''
  const chain = activeChain.value
  streamingChain.value = {
    _tmp: Date.now(),
    type: chain ? 'qa' : 'ask',
    question: q,
    content: '',
    selected_text: chain?.items[0]?.anchor?.selected_text || '',
    page: chain?.items[0]?.anchor?.page_no || 0,
    lastId: chain ? chain.items[chain.items.length - 1].id : null,
  }
  let newRootId = null
  try {
    if (chain) {
      // 划线追问：基于当前链继续追问
      await streamSSE('/api/ai/explain/stream', {
        material_id: materialId,
        selected_text: streamingChain.value.selected_text,
        anchor: { page_no: streamingChain.value.page },
        question: q,
        parent_id: streamingChain.value.lastId,
      },
        (t) => { if (streamingChain.value) streamingChain.value.content += t },
        (payload) => { newRootId = payload.root_id },
        (msg) => { throw new Error(msg) },
      )
    } else {
      // 直接对当前材料提问（不依赖划线）
      await streamSSE('/api/ai/ask/stream', { material_id: materialId, question: q },
        (t) => { if (streamingChain.value) streamingChain.value.content += t },
        () => {},
        (msg) => { throw new Error(msg) },
      )
    }
  } catch (e) {
    ElMessage.error(e.message || '提问失败')
  } finally {
    bottomAsking.value = false
    streamingChain.value = null
    await loadChains()
    if (newRootId) activeChainId.value = newRootId
  }
}

async function doAsk(chain) {
  const q = chain._question?.trim()
  if (!q) return
  chain._asking = true
  try {
    const lastId = chain.items[chain.items.length - 1].id
    await aiApi.explain({
      material_id: materialId,
      selected_text: chain.items[0].anchor?.selected_text || '',
      anchor: { page_no: chain.items[0].anchor?.page_no },
      question: q,
      parent_id: lastId,
    })
    await loadChains()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '追问失败')
  } finally {
    chain._asking = false
  }
}

async function loadChains() {
  const { data } = await aiApi.chains(materialId)
  chains.value = data.map(c => ({ ...c, _question: '', _asking: false }))
  // 选中的链已不存在时重置为「直接提问」模式（默认不自动激活链）
  if (activeChainId.value && !chains.value.some(c => c.root_id === activeChainId.value)) {
    activeChainId.value = null
  }
}

// ---------- 摘要 / 知识点 ----------

async function genSummary(regen) {
  summaryLoading.value = true
  streamingSummary.value = ''
  summaryError.value = ''
  summaryProgress.value = null
  try {
    await streamSSE('/api/ai/summary/stream',
      { material_id: materialId, instruction: regen ? regenInstruction.value || undefined : undefined },
      (t) => { streamingSummary.value += t },
      (payload) => { summary.value = payload.asset },
      (msg) => { throw new Error(msg) },
      undefined,   // onMeta：摘要链路不使用
      (p) => { summaryProgress.value = p },   // onProgress：长文档逐组回传
    )
    ElMessage.success(regen ? `已再生成（V${summary.value.version}）` : '摘要生成完成')
  } catch (e) {
    summaryError.value = e.message || '摘要生成失败，请重试'
  } finally {
    summaryLoading.value = false
    streamingSummary.value = ''
    summaryProgress.value = null
    // streamingSummary 清空后 v-html 切换到 summary.content，需重新包裹 (P数字) 为可点击
    nextTick(() => makeSummaryPageClickable(summaryMdEl.value))
  }
}

// 把摘要里的出处标注 (P数字) / (P数字-数字) 包裹成可点击跳转
function makeSummaryPageClickable(root) {
  // 不传（undefined）→ 回退第一个 .summary-text（兼容旧调用）；
  // 传了但为 null（容器未渲染）→ 直接返回，绝不误伤其它 Tab 的容器。
  // ⚠️ 多个 Tab 都有 .summary-text，且 el-tab-pane 渲染后会留在 DOM，
  //    所以必须按 ref 精确指定，否则只会命中 DOM 里第一个（摘要）。
  const el = root === undefined ? document.querySelector('.summary-text') : root
  if (!el) return
  // 幂等：先还原已有包裹，避免重复嵌套
  el.querySelectorAll('.sum-page-link').forEach(s => {
    s.parentNode.replaceChild(document.createTextNode(s.textContent), s)
  })
  const re = /\(P(\d+)(?:-P?(\d+))?\)/g
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const nodes = []
  let n
  while ((n = walker.nextNode())) nodes.push(n)
  for (const node of nodes) {
    const val = node.nodeValue || ''
    re.lastIndex = 0
    const frag = document.createDocumentFragment()
    let last = 0, m, matched = false
    while ((m = re.exec(val))) {
      matched = true
      if (m.index > last) frag.appendChild(document.createTextNode(val.slice(last, m.index)))
      const span = document.createElement('span')
      span.className = 'sum-page-link'
      span.textContent = m[0]
      span.dataset.page = m[1]
      if (m[2]) span.dataset.pageEnd = m[2]
      span.title = m[2] ? `跳转到第 ${m[1]}-${m[2]} 页` : `跳转到第 ${m[1]} 页`
      frag.appendChild(span)
      last = m.index + m[0].length
    }
    if (matched) {
      if (last < val.length) frag.appendChild(document.createTextNode(val.slice(last)))
      node.parentNode.replaceChild(frag, node)
    }
  }
}

function onSummaryClick(e) {
  const link = e.target.closest('.sum-page-link')
  if (!link) return
  locateAndFlash(Number(link.dataset.page))
}

// 摘要/讲义渲染完成或切回对应 tab 时，把 (P数字) 出处标注做成可点击
// ⚠️ watch 注册时会立即跑一次 getter → getter 里读到的 ref 必须声明在本行之前，
//    新增 ref 到 getter 时先确认它已在 ref 声明区（learnNote/summaryMdEl/learnMdEl 即为此前置）。
watch([() => summary.value?.content, () => learnNote.value?.content, activeTab], async () => {
  await nextTick()
  if (activeTab.value === 'summary') makeSummaryPageClickable(summaryMdEl.value)
  else if (activeTab.value === 'simplelearn') makeSummaryPageClickable(learnMdEl.value)
})

// ---------- 材料全文摘要 → 笔记 ----------
// 状态口径：notes 里 anchor.kind === 'summary' 的那条（同一材料只沉淀一条摘要笔记）。
// ⚠️ 不要用 source_asset_id 判断：「再生成」会新建 AIAsset（version+1、id 变化），
// 按 asset id 会让按钮在再生成后重置回「转笔记」，重复沉淀。
const summaryNote = computed(
  () => notes.value.find(n => n.source_type === 'ai_asset' && n.anchor?.kind === 'summary') || null)
// 「摘要已更新」用 version 比对（不用正文比对——用户编辑过笔记会误判为过期）
const summaryStale = computed(() => {
  const n = summaryNote.value, sv = summary.value
  if (!n || !sv) return false
  return Number(n.anchor?.version) !== Number(sv.version)
})

async function summaryToNote() {
  const sv = summary.value
  if (!sv?.content) return
  if (streamingSummary.value) { ElMessage.warning('摘要生成中，请稍候再转笔记'); return }
  try {
    if (summaryNote.value) {
      await ElMessageBox.confirm(
        `已存在该摘要的笔记「${summaryNote.value.title}」，用当前摘要覆盖它的内容？你在笔记里做过的编辑会被覆盖。`,
        '更新摘要笔记', { confirmButtonText: '覆盖更新', cancelButtonText: '取消', type: 'warning' })
      await noteApi.update(summaryNote.value.id, {
        title: summaryNote.value.title,
        content: sv.content,
        // 同步更新 anchor.version，否则版本比对会一直判定「摘要已更新」
        anchor: { ...(summaryNote.value.anchor || {}), kind: 'summary', version: sv.version },
      })
      await refreshNotes()
      ElMessage.success('笔记已更新为当前摘要')
    } else {
      const title = `全文摘要：${(material.value?.title || '材料').slice(0, 40)}`
      await createNote(title, sv.content, 'ai_asset', sv.id, { kind: 'summary', version: sv.version })
      ElMessage.success('已转笔记')
    }
  } catch (e) {
    if (e === 'cancel' || e === 'close') return   // ElMessageBox 取消
    ElMessage.error(errMsg(e, '转笔记失败'))
  }
}

async function genKeywords(regen) {
  keywordsLoading.value = true
  try {
    const { data } = await aiApi.keywords(materialId, regen ? kwInstruction.value || undefined : undefined)
    keywords.value = data.items || []
    keywordsAssetId.value = data.id
    keywordsError.value = ''
    ElMessage.success(`提炼出 ${keywords.value.length} 个知识点`)
  } catch (e) {
    keywordsError.value = errMsg(e, '知识点提炼失败，请重试')
  } finally {
    keywordsLoading.value = false
  }
}

// ---------- 笔记 ----------

async function refreshNotes() {
  const { data } = await noteApi.list(materialId)
  notes.value = data
  // 从笔记恢复「已转笔记」状态（C1 持久化）
  Object.keys(noteByAsset).forEach(k => delete noteByAsset[k])
  for (const n of data) {
    if (n.source_type === 'ai_asset') {
      if (n.source_asset_id) noteByAsset[n.source_asset_id] = n
      if (n.anchor?.concept) {
        kwTransferred.add(n.anchor.concept)
        kwNoteMap[n.anchor.concept] = n
      }
    }
  }
}

function openTransferredNote(concept) {
  const n = kwNoteMap[concept]
  if (n) openNoteEditor(n)
}

async function createNote(title, content, sourceType, assetId, anchor) {
  await noteApi.create({
    material_id: materialId, title, content,
    source_type: sourceType, source_asset_id: assetId, anchor,
  })
  await refreshNotes()
}

async function selectionToNote() {
  try {
    await createNote(toolbar.text.slice(0, 20), toolbar.text, 'manual', null,
      { page_no: toolbar.page, selected_text: toolbar.text.slice(0, 300) })
    ElMessage.success('已存入笔记')
    hideToolbar()
  } catch (e) {
    ElMessage.error(errMsg(e, '转笔记失败'))
  }
}

const kwAsking = reactive(new Set())

// 知识点直接追问：以概念为选段开一条解读链
async function kwAsk(k) {
  kwAsking.add(k.concept)
  try {
    await aiApi.explain({
      material_id: materialId,
      selected_text: `${k.concept}：${k.explanation}`,
      anchor: { page_no: k.page_no },
    })
    await loadChains()
    activeTab.value = 'chains'
  } catch (e) {
    ElMessage.error(errMsg(e, '追问失败'))
  } finally {
    kwAsking.delete(k.concept)
  }
}

// 相关知识点：向量检索该概念，跨材料建立知识连接
const relatedDialog = reactive({ show: false, concept: '', items: [], loading: false })
async function showRelated(k) {
  relatedDialog.concept = k.concept
  relatedDialog.show = true
  relatedDialog.loading = true
  relatedDialog.items = []
  try {
    const { data } = await kbApi.search(k.concept)
    relatedDialog.items = data.filter(h => h.material_id !== materialId).slice(0, 5)
  } catch (e) {
    ElMessage.error(errMsg(e, '检索相关失败'))
  } finally {
    relatedDialog.loading = false
  }
}
function openRelated(it) {
  relatedDialog.show = false
  const q = it.metadata?.page_no ? { page: it.metadata.page_no } : {}
  router.push({ path: `/study/${it.material_id}`, query: q })
}

async function keywordToNote(k) {
  try {
    await createNote(k.concept, `${k.concept}：${k.explanation}（出处 P${k.page_no}）`,
      'ai_asset', keywordsAssetId.value, { page_no: k.page_no, concept: k.concept })
    kwTransferred.add(k.concept)
    ElMessage.success('已转笔记')
  } catch (e) {
    ElMessage.error('转笔记失败')
  }
}

async function assetToNote(item) {
  try {
    // 标题取「问题」（qa/ask 有 question；explain 无问题则回退到划线文字）
    const title = (item.anchor?.question || item.anchor?.selected_text || item.content).slice(0, 20)
    // 内容取 AI 回复内容
    await createNote(title, item.content, 'ai_asset', item.id, item.anchor)
    ElMessage.success('已转笔记')
  } catch (e) {
    ElMessage.error('转笔记失败')
  }
}

// 批量加入复习：本材料全部笔记一键出题
const batchReviewing = ref(false)
async function addAllToReview() {
  batchReviewing.value = true
  try {
    const { data } = await reviewApi.createCardsBatch(materialId)
    ElMessage.success(`已生成 ${data.created} 张复习卡${data.skipped ? `（${data.skipped} 条已在队列）` : ''}`)
  } catch (e) {
    ElMessage.error(errMsg(e, '批量加入失败'))
  } finally {
    batchReviewing.value = false
  }
}

// 转写校对：编辑音视频转写文本（纠同音字），保存后同步向量索引
const transcriptEditing = ref(false)
const transcriptDraft = reactive({})
const transcriptSaving = ref(false)

// 编辑态 md 预览（仅解析层产出 md 语法的 docx/epub/pptx；退出编辑自动复位）
const editPreview = ref(false)
const editPreviewOn = computed(() => editPreview.value && isRichMd.value)
watch(transcriptEditing, (v) => { if (!v) editPreview.value = false })

// 编辑框高度随内容自适应：估行数（min 3 / max 24），让长内容不再挤在小框里
function editRows(content) {
  const n = (content || '').length
  if (!n) return 3
  const lines = (content.match(/\n/g) || []).length + 1
  return Math.min(24, Math.max(3, lines, Math.ceil(n / 55)))
}

function hasTranscriptChanges() {
  for (const c of chunks.value) {
    if ((transcriptDraft[c.id] || '').trim() !== (c.content || '').trim()) return true
  }
  return false
}

// md 材料「编辑文本」：跳转到 Vditor 富文本编辑页（复用 EditorView，带来源标记）
function editMdDocument() {
  router.push(`/editor/${materialId}?from=study`)
}

async function toggleTranscriptEdit() {
  if (!transcriptEditing.value) {
    for (const c of chunks.value) transcriptDraft[c.id] = c.content
    transcriptEditing.value = true
    return
  }
  // 退出编辑：检查未保存修改，防静默丢数据
  if (!hasTranscriptChanges()) {
    transcriptEditing.value = false
    return
  }
  try {
    await ElMessageBox.confirm('有未保存的修改，退出前要保存吗？', '退出编辑', {
      confirmButtonText: '保存并退出',
      cancelButtonText: '放弃修改',
      distinguishCancelAndClose: true,
      type: 'warning',
    })
    await saveTranscript()
  } catch (action) {
    if (action === 'cancel') {
      transcriptEditing.value = false   // 放弃修改并退出
    }
    // action === 'close'（右上角 X / ESC）：保持编辑态，什么都不做
  }
}

async function saveTranscript() {
  transcriptSaving.value = true
  try {
    let changed = 0
    let warnCount = 0
    for (const c of chunks.value) {
      const draft = (transcriptDraft[c.id] || '').trim()
      if (draft && draft !== c.content) {
        const { data } = await materialApi.updateChunk(materialId, c.id, draft)
        if (data.reindex_warning) warnCount++
        c.content = draft
        changed++
      }
    }
    if (warnCount) ElMessage.warning(`已保存 ${changed} 处修改，但 ${warnCount} 处索引更新失败，可能检索不到`)
    else ElMessage.success(changed ? `已保存 ${changed} 处修改` : '无修改')
    transcriptEditing.value = false
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    transcriptSaving.value = false
  }
}

// 重新加载文本块（删除后刷新正文与目录）
async function loadChunks() {
  const { data } = await materialApi.chunks(materialId)
  chunks.value = data
}

async function deleteChunk(c) {
  try {
    await ElMessageBox.confirm('删除这个文本块？删除后不可恢复。', '删除文本块', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  try {
    await materialApi.deleteChunk(materialId, c.id)
    ElMessage.success('已删除')
    await loadChunks()
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  }
}

async function deletePage(page) {
  try {
    await ElMessageBox.confirm('删除整页文本？删除后不可恢复。', '删除整页', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  try {
    const { data } = await materialApi.deletePage(materialId, page)
    material.value.page_count = data.page_count
    ElMessage.success('已删除整页')
    await loadChunks()
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  }
}

// 测一测：基于资料核心内容出题，同步到复习
const quizCards = ref([])
const quizLoading = ref(false)
const quizError = ref('')
const quizPicked = reactive({})   // 卡 id → 已选选项下标

function pickQuiz(q, oi) {
  if (quizPicked[q.id] !== undefined) return   // 已作答
  quizPicked[q.id] = oi
  // 答错立即进复习队列强化、答对跳过首轮（静默上报，失败不阻塞）
  reviewApi.quizAnswer(q.id, oi === q.correct_index).catch(() => {})
}

function optClass(q, oi) {
  const p = quizPicked[q.id]
  if (p === undefined) return ''
  if (oi === q.correct_index) return 'right'
  if (oi === p) return 'wrong'
  return ''
}

async function genQuiz() {
  quizLoading.value = true
  quizError.value = ''
  for (const k of Object.keys(quizPicked)) delete quizPicked[k]   // 清空作答状态
  try {
    const { data } = await reviewApi.quiz(materialId)
    quizCards.value = data.cards
    ElMessage.success(`已生成 ${data.created} 道测试题，已同步到复习模块`)
  } catch (e) {
    quizError.value = errMsg(e, '出题失败')
  } finally {
    quizLoading.value = false
  }
}

async function loadQuiz() {
  try {
    const { data } = await reviewApi.cards()
    quizCards.value = data.filter(c => c.source === 'quiz' && c.material_id === materialId)
  } catch { /* 静默 */ }
}

// ---------- 简单学习：一键生成结构化讲义 + 出题入复习 ----------
//
// 出的题落 source='quiz'，与「测一测」同口径 → 会自动出现在测一测里，
// 故本 Tab 只负责「生成 + 展示讲义」，不重复实现题目展示。
// 生成走服务端后台任务：离开页面任务照跑，回来重连 SSE 即附着看进度。
const learnLoading = ref(false)
const learnError = ref('')
const learnStage = ref('')           // prepare / note / quiz
const learnQuizCount = ref(0)        // 本次生成入队的题目数
const learnQuizError = ref('')       // 讲义成功但**出题失败**时的提示（不判整体失败）
const learnQuizLoading = ref(false)  // 「重试出题」进行中

const LEARN_STAGE_TEXT = {
  prepare: '正在准备材料…',
  note: '正在转译结构化讲义，长材料约需 1-3 分钟…',
  quiz: '正在出题并同步到复习…',
}
const LEARN_STAGE_PCT = { prepare: 12, note: 48, quiz: 82 }
const learnStageText = computed(() => LEARN_STAGE_TEXT[learnStage.value] || '正在生成…')
const learnPercent = computed(() => LEARN_STAGE_PCT[learnStage.value] || (learnLoading.value ? 5 : 0))
// 讲义展示正文：流式生成中优先显示实时累积（逐 token 打字），落库后切回 learnNote
const learnDisplayContent = computed(() => learnLive.value || (learnNote.value?.content || ''))

async function loadLearn() {
  try {
    const { data } = await simpleLearnApi.note(materialId)
    learnNote.value = data || null
  } catch { learnNote.value = null }   // 404「尚未生成」属正常空态
}

// 回到页面：仍在生成 → 附着看进度；否则读回已有讲义
async function resumeLearn() {
  try {
    const { data } = await simpleLearnApi.status(materialId)
    if (data?.note) learnNote.value = data.note
    if (data?.status === 'running') { genLearn(); return }   // 附着（后端会补一份状态快照）
    if (data?.status === 'failed') {
      // ⚠️ 旧实现只判 running → 上次失败的记录回到页面完全不可见，用户只看到「从没生成过」。
      learnError.value = data.error || '上次生成失败，请重试'
      return
    }
    if (data?.quiz?.error) learnQuizError.value = data.quiz.error   // 讲义成功、只是出题失败
    if (data?.quiz?.created) learnQuizCount.value = data.quiz.created
    if (data?.note) return
  } catch { /* 静默 */ }
  await loadLearn()
}

// 「生成中」这条 SSE 的控制器：组件卸载时必须 abort，
// 否则每进出一次页面就多留一条挂到后台任务结束的不死连接。
let learnAbort = null

async function genLearn() {
  if (learnLoading.value) return
  learnLoading.value = true
  learnError.value = ''
  learnStage.value = 'prepare'
  learnQuizCount.value = 0
  learnQuizError.value = ''
  learnLive.value = ''                 // 清空上一轮流式累积，准备接收新一轮 token
  learnAbort = new AbortController()
  try {
    await streamSSE(
      simpleLearnApi.runStreamUrl,
      { material_id: materialId },
      (t) => { learnLive.value += t },  // token：逐字累积，实时渲染讲义
      (payload) => {                    // done
        if (payload.note) learnNote.value = payload.note
        learnLive.value = ''            // 落库完成，切回 learnNote 展示
        learnQuizCount.value = payload.quiz?.created || 0
        // ⚠️ 出题失败是「降级」：后端在 done 里带 quiz.error（status 仍是 done）。
        //    旧实现只读 created → 出题失败被当成成功，用户完全不知情。
        learnQuizError.value = payload.quiz?.error || ''
      },
      (msg) => { learnError.value = msg || '生成失败' },   // error
      undefined,
      (payload) => {                    // progress
        if (payload.stage) learnStage.value = payload.stage
        if (payload.note) learnNote.value = payload.note
        if (payload.quiz?.error) learnQuizError.value = payload.quiz.error
      },
      learnAbort.signal,                // ⚠️ 第 8 参：离开页面时断开这条长连接
    )
    if (!learnError.value) {
      if (learnQuizError.value) {
        ElMessage.warning('学习讲义已生成，但出题失败，可点「重试出题」')
      } else {
        ElMessage.success(learnQuizCount.value > 0
          ? `学习讲义已生成，${learnQuizCount.value} 道题已同步到复习`
          : '学习讲义已生成')
      }
      loadQuiz()                        // 新题落 source=quiz → 刷新「测一测」
    }
  } catch (e) {
    if (e?.name !== 'AbortError') learnError.value = errMsg(e, '生成失败')
  } finally {
    learnAbort = null
    learnLoading.value = false
    learnStage.value = ''
    learnLive.value = ''
  }
}

// 出题失败后的重试：只重跑出题，**不动讲义**
// （旧「重试」走 genLearn 全量重跑：多花一次 token，讲义 version 还会白涨）
async function retryLearnQuiz() {
  if (learnQuizLoading.value) return
  learnQuizLoading.value = true
  learnQuizError.value = ''
  try {
    const { data } = await simpleLearnApi.quiz(materialId)
    learnQuizCount.value = data?.created || 0
    if (data?.error) {
      learnQuizError.value = data.error
    } else {
      ElMessage.success(learnQuizCount.value > 0
        ? `已重新出题，${learnQuizCount.value} 道题已同步到复习`
        : '已重新出题')
      loadQuiz()
    }
  } catch (e) {
    learnQuizError.value = errMsg(e, '出题失败')
  } finally {
    learnQuizLoading.value = false
  }
}

// ---------- 学习讲义 → 笔记 ----------
// 口径与「摘要转笔记」一致：认 notes 里 anchor.kind === 'learn_note' 的那条（同一材料只沉淀一条讲义笔记）。
// ⚠️⚠️ kind **不能**用 'summary'：摘要 Tab 的「覆盖更新」是按 anchor.kind 认领的，
//    共用 summary 会让它误认领讲义笔记 → 弹出错误的「摘要已更新」确认（本项目已踩过这个坑）。
// ⚠️ 也不要用 source_asset_id 判断：「重新生成」会新建 AIAsset（id 变化）→ 按钮会错误地翻回「转笔记」。
const learnSavedNote = computed(
  () => notes.value.find(n => n.source_type === 'ai_asset' && n.anchor?.kind === 'learn_note') || null)
// 「讲义已更新」用 version 比对（绝不比对正文 —— 用户编辑过笔记会被误判为过期）
const learnSavedStale = computed(() => {
  const n = learnSavedNote.value, ln = learnNote.value
  if (!n || !ln) return false
  return Number(n.anchor?.version) !== Number(ln.version)
})

async function learnToNote() {
  const ln = learnNote.value
  if (!ln?.content) return
  if (learnLoading.value) { ElMessage.warning('讲义生成中，请稍候再转笔记'); return }
  try {
    if (learnSavedNote.value) {
      await ElMessageBox.confirm(
        `已存在该讲义的笔记「${learnSavedNote.value.title}」，用当前讲义覆盖它的内容？你在笔记里做过的编辑会被覆盖。`,
        '更新学习讲义笔记', { confirmButtonText: '覆盖更新', cancelButtonText: '取消', type: 'warning' })
      await noteApi.update(learnSavedNote.value.id, {
        title: learnSavedNote.value.title,
        content: ln.content,
        // 同步更新 anchor.version，否则版本比对会一直判定「讲义已更新」
        anchor: { ...(learnSavedNote.value.anchor || {}), kind: 'learn_note', version: ln.version },
      })
      await refreshNotes()
      ElMessage.success('笔记已更新为当前讲义')
    } else {
      const title = `学习讲义：${(material.value?.title || '材料').slice(0, 40)}`
      await createNote(title, ln.content, 'ai_asset', ln.id, { kind: 'learn_note', version: ln.version })
      ElMessage.success('已转笔记')
    }
  } catch (e) {
    if (e === 'cancel' || e === 'close') return   // ElMessageBox 取消
    ElMessage.error(errMsg(e, '转笔记失败'))
  }
}

function openNoteEditor(n) {
  // 笔记回看埋点（模块 H）
  if (n?.id) statsApi.track({ type: 'note_view', ref_id: n.id }).catch(() => {})
  noteDialog.id = n?.id || null
  noteDialog.title = n?.title || ''
  noteDialog.content = n?.content || ''
  noteDialog.anchor = n?.anchor || null
  noteDialog.sourceType = n?.source_type || 'manual'
  noteDialog.show = true
}

// 新建笔记保存（NoteEditorDialog 的 create-handler）：绑定当前材料
async function onCreateNote({ title, content }) {
  await createNote(title || '未命名笔记', content, 'manual', null, null)
}

// 「跳转原文」（NoteEditorDialog 的 jump-handler）：本页内定位 + 闪高亮
function onJumpAnchor(anchor) {
  locateAndFlash(anchor.page_no, anchor.selected_text)
}

function copySelection() {
  navigator.clipboard.writeText(toolbar.text)
  ElMessage.success('已复制')
  hideToolbar()
}

// ---------- 编辑模式选中文字 → AI 加工（改写/扩写/续写/总结） ----------

// 编辑 textarea 内选区（textarea 用 selectionStart/End，不走 window.getSelection）
function onEditSelect(e, c) {
  const el = e.target
  if (!el || typeof el.selectionStart !== 'number') return
  const start = el.selectionStart, end = el.selectionEnd
  const text = el.value.substring(start, end)
  if (!text.trim()) { hidePolishBar(); return }
  const rect = el.getBoundingClientRect()
  polishBar.show = true
  polishBar.chunkId = c.id
  polishBar.page = c.page_no || 0
  polishBar.text = text
  polishBar.start = start
  polishBar.end = end
  polishBar.x = Math.min(rect.right - 260, window.innerWidth - 280)
  polishBar.y = Math.max(rect.top - 44, 60)
}

function hidePolishBar() { polishBar.show = false }

function copyEditSelection() {
  navigator.clipboard.writeText(polishBar.text)
  ElMessage.success('已复制')
  hidePolishBar()
}

// ---------- 编辑视图选中文字 → AI 加工（改写/扩写/总结，流式） ----------
const editTransform = reactive({ show: false, mode: 'rewrite', original: '', result: '', streaming: false, chunkId: null, start: 0, end: 0 })
const editTransformTitle = computed(() => 'AI ' + (TRANSFORM_LABELS[editTransform.mode] || '加工'))

async function openEditTransform(mode) {
  const text = polishBar.text
  editTransform.mode = mode
  editTransform.original = text
  editTransform.result = ''
  editTransform.chunkId = polishBar.chunkId
  editTransform.start = polishBar.start
  editTransform.end = polishBar.end
  editTransform.streaming = true
  editTransform.show = true
  hidePolishBar()
  try {
    await streamSSE('/api/ai/note/transform/stream',
      { content: text, mode },
      (t) => { editTransform.result += t },
      () => {},
      (msg) => { throw new Error(msg) },
    )
  } catch (e) {
    ElMessage.error(e.message || '加工失败')
    editTransform.show = false
  } finally {
    editTransform.streaming = false
  }
}

async function adoptEditTransform() {
  if (!editTransform.result.trim()) { ElMessage.warning('结果为空，无法采用'); return }
  const chunkId = editTransform.chunkId
  if (chunkId != null) {
    const cur = transcriptDraft[chunkId] || ''
    if (editTransform.mode === 'continue') {
      // 续写：保留原选段，续写内容追加在其后（end 为选区结束位置）
      transcriptDraft[chunkId] = cur.substring(0, editTransform.end) + '\n\n' + editTransform.result + cur.substring(editTransform.end)
    } else {
      transcriptDraft[chunkId] = cur.substring(0, editTransform.start) + editTransform.result + cur.substring(editTransform.end)
    }
  }
  editTransform.show = false
  // 采用即保存该块，并同步 chunks 内容（避免「保存修改」重复提交）
  if (chunkId != null) {
    try {
      await materialApi.updateChunk(materialId, chunkId, transcriptDraft[chunkId])
      const c = chunks.value.find(x => x.id === chunkId)
      if (c) c.content = transcriptDraft[chunkId]
      ElMessage.success(editTransform.mode === 'continue' ? '已续写并保存' : '已采用并保存')
    } catch (e) {
      ElMessage.error(errMsg(e, '保存失败'))
    }
  }
}

// ---------- 学习时长埋点（模块 H：心跳 30s，切后台暂停，无操作暂停） ----------
const studyTrack = { startTs: 0, timer: null }
let studyLastActive = Date.now()
const IDLE_MS = 5 * 60 * 1000   // 5 分钟无鼠标键盘操作视为离开，暂停计时
const ACTIVITY_EVENTS = ['mousemove', 'keydown', 'mousedown', 'scroll', 'wheel', 'touchstart']
const onStudyActivity = () => { studyLastActive = Date.now() }
const studyBeat = () => {
  if (Date.now() - studyLastActive > IDLE_MS) return   // 长时间无操作，本次不上报（人已离开）
  statsApi.track({ type: 'duration', ref_id: materialId, duration_seconds: 30 }).catch(() => {})
}
function onStudyVis() {
  if (document.hidden) {
    if (studyTrack.timer) { clearInterval(studyTrack.timer); studyTrack.timer = null }
  } else {
    studyLastActive = Date.now()   // 回到页面刷新活动时间
    if (!studyTrack.timer) studyTrack.timer = setInterval(studyBeat, 30000)
  }
}
function startStudyTrack() {
  studyTrack.startTs = Date.now()
  studyLastActive = Date.now()
  studyTrack.timer = setInterval(studyBeat, 30000)
  ACTIVITY_EVENTS.forEach(ev => document.addEventListener(ev, onStudyActivity, { passive: true }))
  document.addEventListener('visibilitychange', onStudyVis)
}
function stopStudyTrack() {
  if (studyTrack.timer) clearInterval(studyTrack.timer)
  ACTIVITY_EVENTS.forEach(ev => document.removeEventListener(ev, onStudyActivity))
  document.removeEventListener('visibilitychange', onStudyVis)
  const unreported = Math.floor((Date.now() - studyTrack.startTs) / 1000) % 30
  if (unreported >= 5 && Date.now() - studyLastActive <= IDLE_MS) {
    statsApi.track({ type: 'duration', ref_id: materialId, duration_seconds: unreported }).catch(() => {})
  }
}

// ---------- 初始化 ----------

// 材料解析完成后的完整加载（首次进入 / 解析中轮询结束后共用）
async function initAfterParsed() {
  try {
    const [{ data: m }, { data: c }] = await Promise.all([
      materialApi.detail(materialId),
      materialApi.chunks(materialId),
    ])
    material.value = m
    chunks.value = c
    // PDF / 图片材料默认「原文视图」（扫描件/图片的文本视图常为空，原文才是主内容）
    if (m.format === 'pdf' || isImage.value) viewMode.value = 'origin'
    if (isMedia.value) loadAsr()
    if (isEpub.value) loadEpubChapters()
    await Promise.all([refreshNotes(), loadChains(), loadQuiz(), loadHighlights()])
    resumeLearn()   // 简单学习：附着进行中的生成，或读回已有讲义（不阻塞其余加载）
    const { data: assets } = await aiApi.assets(materialId)
    if (assets.summary) summary.value = assets.summary
    if (assets.keywords) {
      keywords.value = assets.keywords.items || []
      keywordsAssetId.value = assets.keywords.id
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载失败')
  } finally {
    pageLoading.value = false
  }
  // E4 来源跳转 ?page=N 优先（显式跳转，scrollToPage 负责落到能展示的视图）；
  // 否则 B8 恢复上次阅读位置：默认原文视图时不走 scrollToPage（它会切回文本视图）
  const queryPage = Number(route.query.page) || 0
  const targetPage = queryPage || material.value?.last_read_page || 0
  if (queryPage) scrollToPage(queryPage)
  else if (targetPage && viewMode.value === 'origin') restoreReadingPosition('origin', targetPage)
  else if (targetPage) scrollToPage(targetPage)
  // Markdown 渲染后恢复划线高亮
  if (isMd.value) await applyMdHighlights()
  // 点击空白处收起工具条
  document.addEventListener('mousedown', (e) => {
    if (!e.target.closest('.sel-toolbar')) hideToolbar()
  })
  document.addEventListener('fullscreenchange', onFsChange)
  // 学习时长埋点启动（模块 H）
  startStudyTrack()
}

// 解析中轮询：拿到最新进度，解析结束后自动进入完整加载
function pollParse() {
  parsePollTimer = setTimeout(async () => {
    let m0 = null
    try {
      const { data } = await materialApi.detail(materialId)
      m0 = data
    } catch {
      pollParse()   // 网络抖动：继续等
      return
    }
    parsePct.value = m0.parse_progress || 0
    if (m0.parsed_status === 'parsing') {
      pollParse()
      return
    }
    parsing.value = false
    if (m0.parsed_status === 'failed') {
      ElMessage.error('解析失败：' + (m0.parse_error || '未知原因'))
    }
    await initAfterParsed()
  }, 1500)
}

onMounted(async () => {
  window.addEventListener('resize', onWinResize)
  // 材料仍在后台解析（从知识库/直链进入）：先展示解析进度，完成后自动加载
  try {
    const { data: m0 } = await materialApi.detail(materialId)
    if (m0.parsed_status === 'parsing') {
      parsing.value = true
      parsingFormat.value = m0.format || ''
      parsePct.value = m0.parse_progress || 0
      pageLoading.value = false
      pollParse()
      return
    }
  } catch { /* 详情探测失败则走正常加载，由 initAfterParsed 统一报错 */ }
  await initAfterParsed()
})

onUnmounted(() => {
  window.removeEventListener('resize', onWinResize)
  if (resizeTimer) clearTimeout(resizeTimer)
  document.removeEventListener('fullscreenchange', onFsChange)
  stopStudyTrack()
  if (parsePollTimer) clearTimeout(parsePollTimer)
  if (flashLocateTimer) clearTimeout(flashLocateTimer)
  if (flashQueryTimer) clearTimeout(flashQueryTimer)
  // ⚠️ 断开简单学的 SSE：后端生成是独立线程、照跑不误（回来还能附着），
  //    但这条读连接不能跟着一直挂着。genLearn 里已捕获 AbortError、不会误报失败。
  learnAbort?.abort()
})
</script>

<style scoped>
.study-page { display: flex; height: 100vh; overflow: hidden; }

/* ===== 材料解析中遮罩 ===== */
.parsing-overlay {
  position: fixed; inset: 0; z-index: 200;
  background: var(--asc-bg);
  display: flex; align-items: center; justify-content: center;
}
.parsing-card { width: 320px; text-align: center; }
.parsing-spinner {
  width: 36px; height: 36px; margin: 0 auto 18px;
  border: 3px solid var(--asc-border); border-top-color: var(--asc-primary);
  border-radius: 50%; animation: parsing-spin .9s linear infinite;
}
@keyframes parsing-spin { to { transform: rotate(360deg); } }
.parsing-title { font-size: 15px; font-weight: 600; color: var(--asc-text); margin-bottom: 16px; }
.parsing-track {
  height: 6px; border-radius: 3px; overflow: hidden;
  background: var(--asc-border);
}
.parsing-fill {
  height: 100%; border-radius: 3px; background: var(--asc-primary);
  transition: width .5s ease;
}
.parsing-fill.indeterminate {
  width: 40%; animation: parsing-slide 1.2s ease-in-out infinite;
}
@keyframes parsing-slide {
  0% { margin-left: -40%; }
  100% { margin-left: 100%; }
}
.parsing-pct { margin-top: 10px; font-size: 12px; color: var(--asc-text-2); }
.parsing-hint { margin-top: 6px; font-size: 12px; color: var(--asc-text-3); }

/* ===== 左栏（Obsidian 侧栏灰） ===== */
.col-left {
  flex-shrink: 0;
  background: var(--asc-bg); overflow-y: auto; padding: 20px 14px;
}
/* 拖拽分隔线（左-中 / 中-右 共用）：默认发丝细线，hover/拖拽时变紫色指示条。
   ⚠️⚠️ 不能把「局部 .col-divider:hover」与「半全局 :global(body.dragging-col) .col-divider」
   塞进同一条逗号分组：scoped 编译时 :global 只包住它自己那一段，后面的 .col-divider::before
   会被丢弃，整条规则退化成 body.dragging-col { background: var(--asc-primary) } →
   拖拽瞬间把 <body> 染成紫色，整页内容被盖住（用户看到的「紫色遮挡」）。必须拆成两条规则。 */
.col-divider {
  width: 7px; flex-shrink: 0; cursor: col-resize;
  position: relative; background: transparent;
}
.col-divider::before {
  content: ""; position: absolute; left: 3px; top: 0; bottom: 0;
  width: 1px; background: var(--asc-divider); transition: background .15s;
}
.col-divider:hover::before {
  left: 2px; width: 3px; border-radius: 2px; background: var(--asc-primary);
}
/* 拖拽态：整个选择器放进 :global()，避免上面说的退化；transition 收窄到 background 防抖动 */
:global(body.dragging-col .col-divider::before) {
  left: 2px; width: 3px; border-radius: 2px; background: var(--asc-primary); transition: none;
}
:global(body.dragging-col) { user-select: none; cursor: col-resize; }
:global(body.dragging-col .col-center) { pointer-events: none; }
.pane-title {
  font-size: 11px; font-weight: 600; color: var(--asc-text-3);
  letter-spacing: 1.5px; margin-bottom: 10px; padding: 0 6px;
}
.notes-head {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 26px; white-space: nowrap; gap: 8px;
}
.notes-ops { display: inline-flex; align-items: center; flex-shrink: 0; }
.notes-ops .el-button { margin-left: 2px; padding: 4px 6px; }

/* 批量复习：带说明的操作卡片（弱化原全宽橙色大按钮） */
.notes-batch {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 10px 12px; margin: 0 0 10px;
  background: var(--asc-surface-2); border: 1px solid var(--asc-border);
  border-radius: 10px;
}
.batch-info { display: flex; align-items: center; gap: 9px; min-width: 0; }
.batch-icon {
  width: 28px; height: 28px; flex-shrink: 0; border-radius: 8px;
  background: rgba(230, 162, 60, .16); color: #ba7517;
  display: inline-flex; align-items: center; justify-content: center;
}
.batch-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.batch-title { font-size: 12.5px; font-weight: 600; color: var(--asc-text); line-height: 1.3; }
.batch-sub { font-size: 11px; color: var(--asc-text-3); line-height: 1.35; }

.notes-pane-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 2px 2px 8px; white-space: nowrap; gap: 8px;
}
.notes-count { font-size: 12px; color: var(--asc-text-3); }
.note-empty-pane { padding-top: 40px; }

/* ===== note-dialog 通用弹窗骨架（编辑视图加工弹窗等仍在用；笔记弹窗已迁入 NoteEditorDialog 组件） ===== */
.note-dialog :deep(.el-dialog__header) { padding: 20px 24px 14px; margin: 0; }
.note-dialog :deep(.el-dialog__body) { padding: 6px 24px 4px; }
.note-dialog :deep(.el-dialog__footer) { padding: 14px 24px 20px; border-top: 1px solid var(--asc-divider); }

.toc-item {
  display: flex; justify-content: space-between; align-items: center;
  border-radius: 6px; padding-right: 4px; margin: 1px 0; position: relative;
}
.toc-text {
  font-size: 13px; padding: 7px 0 7px 12px; cursor: pointer; flex: 1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; transition: color .15s;
}
.toc-text:hover { color: var(--asc-primary); }
.toc-item:hover { background: var(--asc-surface-2); }
.toc-item.active { background: var(--asc-primary-soft); }
.toc-item.active::before {
  content: ""; position: absolute; left: 3px; top: 22%; bottom: 22%;
  width: 3px; border-radius: 2px; background: var(--asc-primary);
}
.toc-item.active .toc-text { color: var(--asc-primary); font-weight: 600; }
.toc-sum { visibility: hidden; flex-shrink: 0; }
.toc-item:hover .toc-sum { visibility: visible; }
/* 无章节时的页码导航 */
.toc-pages { display: flex; flex-wrap: wrap; gap: 5px; padding: 2px 6px; }
.page-chip {
  min-width: 28px; height: 24px; padding: 0 5px; display: inline-flex;
  align-items: center; justify-content: center;
  font-size: 11.5px; color: var(--asc-text-2); font-variant-numeric: tabular-nums;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 6px; cursor: pointer; transition: all .15s;
}
.page-chip:hover { color: var(--asc-primary); border-color: var(--asc-primary); }
.page-chip-more {
  color: var(--asc-text-3); border-style: dashed; letter-spacing: .5px;
}
.toc-empty { font-size: 12px; color: var(--asc-text-3); padding: 8px 10px; }
.note-item {
  position: relative; padding: 12px 12px 12px 16px; border-radius: 10px; cursor: pointer;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  margin-bottom: 8px; transition: all .18s ease; overflow: hidden;
}
.note-item::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--asc-primary); opacity: .85; border-radius: 0 3px 3px 0;
}
.note-item-ai::before { background: #e8a33d; }
.note-item:hover { border-color: var(--asc-primary); box-shadow: var(--asc-shadow-hover); transform: translateY(-1px); }
.note-title { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.note-tag {
  flex-shrink: 0; font-size: 10px; font-weight: 600; line-height: 1;
  border-radius: 4px; padding: 3px 5px; letter-spacing: .5px;
}
.note-tag-ai { background: var(--asc-primary-soft); color: var(--asc-primary); }
.note-tag-manual { background: var(--asc-surface-2); color: var(--asc-text-2); }
.note-title-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.note-page {
  flex-shrink: 0; font-size: 10px; font-weight: 500; color: var(--asc-primary);
  background: var(--asc-primary-soft); border-radius: 4px; padding: 2px 6px;
}
.note-page-link { cursor: pointer; transition: background .15s; }
.note-page-link:hover { background: rgba(124, 92, 252, .2); }
.note-snippet {
  font-size: 12px; color: var(--asc-text-3); line-height: 1.5; margin-top: 6px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.note-meta { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.note-time { font-size: 11px; color: var(--asc-text-3); }
.note-len { font-size: 11px; color: var(--asc-text-3); }
.note-len::before { content: "·"; margin-right: 8px; }

/* ===== 中栏：阅读器（白画布 + 限宽正文） ===== */
.col-center { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--asc-card); }
/* 全屏沉浸阅读（PDF 原文视图） */
.col-center:fullscreen { background: #525659; }
.col-center:fullscreen .reader-header { background: #3d3d3f; border-bottom-color: #2a2a2c; }
.col-center:fullscreen .reader-header .reader-title { color: #ececee; }
.col-center:fullscreen .reader-header .reader-pages { color: #b0b0b4; }
.col-center:fullscreen .pdf-reader { background: #525659; }
.reader-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 28px; border-bottom: 1px solid var(--asc-divider);
  gap: 16px;
}
.reader-title-wrap { display: flex; align-items: center; gap: 10px; min-width: 0; }
.reader-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
/* 视图切换：分段控件（药丸容器 + 白色激活块） */
.reader-actions :deep(.el-radio-group) {
  display: inline-flex; align-items: center; gap: 4px;
  background: var(--asc-surface-2); border-radius: 8px; padding: 2px;
}
.reader-actions :deep(.el-radio-button__inner) {
  border: none; background: transparent; box-shadow: none;
  border-radius: 6px; font-size: 12.5px; line-height: 22px; padding: 0 14px;
  color: var(--asc-text-2);
  transition: color .16s ease, background .16s ease, box-shadow .16s ease;
}
.reader-actions :deep(.el-radio-button:first-child .el-radio-button__inner) { border-left: none; }
/* 未选中项 hover：浅色浮层 + 文字加深（原来没有任何反馈） */
.reader-actions :deep(.el-radio-button:not(.is-active) .el-radio-button__inner:hover) {
  color: var(--asc-text); background: rgba(255, 255, 255, .6);
}
.reader-actions :deep(.el-radio-button.is-active .el-radio-button__inner),
.reader-actions :deep(.el-radio-button.is-active .el-radio-button__inner:hover) {
  background: var(--asc-card); color: var(--asc-primary); font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .08);
}
/* 头部次级按钮：幽灵态，不抢标题与分段控件 */
.ra-ghost.el-button, .ra-ghost.el-button:focus {
  border-color: transparent; background: transparent; color: var(--asc-text-2);
}
.ra-ghost.el-button:hover {
  color: var(--asc-primary); background: var(--asc-primary-soft); border-color: transparent;
}
.ra-more.el-button { padding-left: 12px; padding-right: 8px; }
.reader-fmt {
  font-size: 10px; font-weight: 700; color: #fff; letter-spacing: .5px;
  border-radius: 4px; padding: 3px 7px; flex-shrink: 0;
}
.rfmt-pdf { background: #d85a30; }
.rfmt-ppt, .rfmt-pptx { background: #ba7517; }
.rfmt-doc, .rfmt-docx { background: #185fa5; }
.rfmt-epub { background: #15803d; }
.rfmt-md, .rfmt-markdown { background: #475569; }
.rfmt-mp3, .rfmt-wav, .rfmt-m4a { background: #0f766e; }
.rfmt-mp4 { background: #6d28d9; }
.reader-title {
  font-weight: 600; font-size: 15px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.reader-pages { font-size: 12px; color: var(--asc-text-3); flex-shrink: 0; }
.pdf-frame { flex: 1; border: none; background: #525659; }
.reader { flex: 1; overflow-y: auto; padding: 36px 48px 96px; }
.reader-inner { max-width: 720px; margin: 0 auto; }
.page-marker {
  display: flex; align-items: center; gap: 14px;
  margin: 40px 0 20px;
  color: var(--asc-text-3); font-size: 10px; letter-spacing: 1.5px;
}
.page-marker::before, .page-marker::after {
  content: ""; flex: 1; height: 1px; background: var(--asc-divider);
}
.chunk {
  font-size: 15px; line-height: 1.95; margin: 0 0 16px;
  color: #363632; text-align: left; word-break: break-word;
}
.chunk::selection { background: rgba(124, 92, 252, .22); }
/* 图片材料：OCR 结果为多行文本，保留换行、左对齐（不拉伸短行） */
.chunk-image { white-space: pre-line; text-align: left; }

/* 内嵌图 OCR 块：灰底卡片 + 图片图标标签，视觉上与正文区分（识别出来的图片文字）。
   ⚠️ 这类元素由 renderChunk 的字符串经 v-html 注入，**不带 scoped 的 data-v 属性**，
   必须挂 .chunk（模板元素，有 data-v）再用 :deep() 穿透，否则选择器永不匹配、样式失效。 */
.chunk :deep(.imgocr-card) {
  display: flex; align-items: flex-start; gap: 10px;
  background: var(--asc-surface-2, #f5f5f7);
  border: 1px solid var(--asc-border, #e5e5e8);
  border-left: 3px solid var(--asc-primary, #7c5cfc);
  border-radius: 8px;
  padding: 10px 14px; margin: 4px 0 16px;
  font-size: 14px; line-height: 1.8; color: var(--asc-text-2, #6e6e6e);
  white-space: pre-line; word-break: break-word; text-align: left;
}
.chunk :deep(.imgocr-card)::before {
  content: "🖼"; flex: none; margin-top: 1px;
  font-size: 15px; line-height: 1.6;
}

/* ---- 表格 / 标题：Word/PPT 解析产物由 v-html 注入，**不带 scoped 的 data-v 属性**，
       所以不能以 .md-table / .chunk-h 自身当锚点（那样选择器永不匹配、表格会没有边框），
       必须挂在模板元素 .chunk 上再用 :deep() 穿透 ---- */
.chunk :deep(.md-table) { margin: 14px 0; }
.chunk :deep(.md-table-flash) { animation: hl-flash-temp 3s ease forwards; border-radius: 6px; }
.chunk :deep(table),
.md-body :deep(table) {
  border-collapse: collapse; display: block; overflow-x: auto; max-width: 100%;
  font-size: 13.5px; line-height: 1.7; text-align: left;
}
.chunk :deep(th), .chunk :deep(td),
.md-body :deep(th), .md-body :deep(td) {
  border: 1px solid var(--asc-border); padding: 8px 12px;
  vertical-align: top; word-break: break-word; text-align: left;
}
.chunk :deep(th), .md-body :deep(th) {
  background: var(--asc-surface-2); font-weight: 600;
}
.chunk :deep(tbody tr:nth-child(even) td),
.md-body :deep(tbody tr:nth-child(even) td) { background: rgba(124, 92, 252, .045); }

/* rich-md 块（docx/epub/pptx）：markdown-it 输出的排版样式，与 .md-body 同族但更紧凑 */
.chunk.rich :deep(h1), .chunk.rich :deep(h2), .chunk.rich :deep(h3),
.chunk.rich :deep(h4), .chunk.rich :deep(h5), .chunk.rich :deep(h6) {
  font-weight: 600; color: var(--asc-text); line-height: 1.5; margin: 14px 0 6px;
}
.chunk.rich :deep(h1:first-child), .chunk.rich :deep(h2:first-child), .chunk.rich :deep(h3:first-child),
.chunk.rich :deep(h4:first-child), .chunk.rich :deep(h5:first-child), .chunk.rich :deep(h6:first-child) { margin-top: 0; }
.chunk.rich :deep(h1) { font-size: 19px; }
.chunk.rich :deep(h2) { font-size: 16.5px; padding-left: 10px; border-left: 3px solid var(--asc-primary); }
.chunk.rich :deep(h3) { font-size: 15px; color: var(--asc-text-2); }
.chunk.rich :deep(h4), .chunk.rich :deep(h5), .chunk.rich :deep(h6) { font-size: 14px; color: var(--asc-text-2); }
.chunk.rich :deep(p) { margin: 0; }
.chunk.rich :deep(ul), .chunk.rich :deep(ol) { padding-left: 22px; margin: 4px 0; }
.chunk.rich :deep(li) { margin: 4px 0; }
.chunk.rich :deep(li::marker) { color: var(--asc-primary); }
.chunk.rich :deep(strong) { font-weight: 600; }
.chunk.rich :deep(code) { background: var(--asc-surface-2); padding: 1px 5px; border-radius: 4px; font-size: 13px; }
.chunk.rich :deep(blockquote) {
  margin: 6px 0; padding: 6px 12px; border-left: 3px solid var(--asc-border);
  background: var(--asc-surface-2); border-radius: 0 8px 8px 0; color: var(--asc-text-2);
}
.chunk.rich :deep(blockquote p) { margin: 0; }

/* 编辑态 md 预览（排版效果与正文一致，浅色底与 textarea 区分） */
.edit-preview {
  border: 1px dashed var(--asc-border); border-radius: 8px;
  background: var(--asc-surface-2); padding: 10px 14px; min-height: 40px;
  font-size: 15px; line-height: 1.85; color: var(--asc-text);
}
.edit-preview :deep(p) { margin: 6px 0; }

/* Word 标题（解析时带 # 前缀，渲染为标题样式） */
.chunk :deep(.chunk-h) {
  font-weight: 600; color: var(--asc-text); line-height: 1.55;
  text-align: left; letter-spacing: .2px;
}
.chunk :deep(.chunk-h.lv1) { font-size: 19px; margin: 26px 0 12px; }
.chunk :deep(.chunk-h.lv2) { font-size: 16.5px; margin: 22px 0 10px; padding-left: 10px; border-left: 3px solid var(--asc-primary); }
.chunk :deep(.chunk-h.lv3) { font-size: 15px; margin: 18px 0 8px; color: var(--asc-text-2); }

/* 划线工具条 */
.sel-toolbar {
  position: fixed; z-index: 200; background: #2c2c2a; border-radius: 10px;
  padding: 6px; display: flex; gap: 4px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, .18);
  animation: asc-fade-up .15s ease;
}
.sel-toolbar .el-button { margin: 0; }
.hl-dot {
  width: 22px; height: 22px; border-radius: 50%; cursor: pointer; flex-shrink: 0;
  border: 2px solid rgba(255, 255, 255, .85); transition: transform .15s;
}
.hl-dot:hover { transform: scale(1.18); }
.dot-yellow { background: #fcd34d; }
.dot-green { background: #86efac; }
.dot-blue { background: #93c5fd; }
.toolbar-sep { width: 1px; height: 18px; background: rgba(255, 255, 255, .22); margin: 0 2px; }

/* AI 加工弹窗：原文 vs 加工后 对比块 */
.polish-block { margin-bottom: 14px; }
.polish-label {
  font-size: 11px; font-weight: 600; letter-spacing: 1.5px;
  color: var(--asc-text-3); margin-bottom: 6px;
}
.polish-block-new .polish-label { color: #0f6e56; }
.polish-text {
  font-size: 14px; line-height: 1.75; color: var(--asc-text);
  background: var(--asc-surface-2); border-radius: 8px;
  padding: 12px 14px; white-space: pre-wrap;
  max-height: 240px; overflow-y: auto;
}
.polish-block-new .polish-text { background: rgba(15, 110, 86, .07); }

/* ===== 右栏：AI 面板 ===== */
/* 右栏满高：栏本身不滚，Tab 内容区撑满并自滚 */
.col-right {
  flex-shrink: 0;
  background: var(--asc-bg);
  display: flex; flex-direction: column; overflow: hidden;
}
.ai-tabs { flex: 1; display: flex; flex-direction: column; padding: 12px 12px 0; overflow: hidden; }
.ai-tabs :deep(.el-tabs__header) { margin: 0 0 10px; }
.ai-tabs :deep(.el-tabs__nav-wrap)::after { display: none; }
.ai-tabs :deep(.el-tabs__nav) {
  width: 100%; display: flex; gap: 2px; padding: 3px;
  background: var(--asc-surface-2); border: none; border-radius: 10px;
}
.ai-tabs :deep(.el-tabs__active-bar) { display: none; }
.ai-tabs :deep(.el-tabs__item) {
  flex: 1; text-align: center; height: 30px; line-height: 30px;
  padding: 0 2px !important;   /* 覆盖 EP 默认 0 20px，5 个 tab 在窄栏才放得下 */
  font-size: 12.5px; white-space: nowrap; min-width: 0;
  border-radius: 8px; color: var(--asc-text-2); transition: all .16s ease;
}
.ai-tabs :deep(.el-tabs__item:hover) { color: var(--asc-text); }
.ai-tabs :deep(.el-tabs__item.is-active) {
  background: var(--asc-card); color: var(--asc-primary); font-weight: 600;
  box-shadow: 0 1px 4px rgba(0, 0, 0, .08);
}
.ai-tabs :deep(.el-tabs__content) { flex: 1; overflow: hidden; }
.ai-tabs :deep(.el-tab-pane) { height: 100%; }
.quiz-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 12px; padding: 14px 16px; margin-bottom: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .04); transition: all .18s ease;
}
.quiz-card:hover { border-color: var(--asc-primary); box-shadow: var(--asc-shadow-hover); transform: translateY(-1px); }
.quiz-q { font-size: 14px; font-weight: 600; margin-bottom: 8px; line-height: 1.5; }
.quiz-opts { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.quiz-opt {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; padding: 5px 10px; border-radius: 6px;
  background: var(--asc-surface-2); color: var(--asc-text-2);
  cursor: pointer; transition: all .15s;
}
.quiz-opt:hover { background: var(--asc-surface-3); }
.quiz-opt.right { background: rgba(15, 110, 86, .08); color: #0f6e56; font-weight: 500; }
.quiz-opt.wrong { background: rgba(210, 78, 78, .08); color: #b23c3c; }
.quiz-key {
  width: 18px; height: 18px; border-radius: 4px; flex-shrink: 0;
  background: var(--asc-surface-3); color: var(--asc-text-2);
  font-size: 11px; font-weight: 600; display: inline-flex; align-items: center; justify-content: center;
}
.quiz-opt.right .quiz-key { background: #0f6e56; color: #fff; }
.quiz-opt.wrong .quiz-key { background: #d24e4e; color: #fff; }
.quiz-right-mark { margin-left: auto; color: #0f6e56; font-weight: 700; }
.quiz-wrong-mark { margin-left: auto; color: #d24e4e; font-weight: 700; }
.quiz-pick-hint { font-size: 12px; color: var(--asc-text-3); text-align: center; padding: 4px 0; }
.quiz-exp { font-size: 12.5px; color: var(--asc-text-3); border-top: 1px dashed var(--asc-border); padding-top: 6px; }
.pane-empty { text-align: center; color: var(--asc-text-2); font-size: 13px; padding: 48px 16px; line-height: 1.8; }
.chains-empty { flex: 1; justify-content: flex-start; padding: 24px 16px; }

/* 摘要面板：去边框融入面板（提高优先级压过 .md-preview 的 400px 限高） */
.summary-body .md-preview { border: none; background: transparent; padding: 4px 2px; max-height: none; overflow: visible; }
/* 摘要出处页码 (P数字) 可点击跳转（span 由 JS 动态创建，需 :deep 穿透 scoped） */
.summary-text :deep(.sum-page-link) {
  color: var(--asc-primary); font-weight: 500; cursor: pointer;
  border-bottom: 1px dashed var(--asc-primary); transition: background .15s;
}
.summary-text :deep(.sum-page-link):hover { background: rgba(124, 92, 252, .15); }
/* 摘要操作行：与下方「补充指令 + 再生成」合并为同一块底栏（分隔线只留一条） */
.summary-ops {
  display: flex; align-items: center; gap: 4px; flex-shrink: 0;
  padding: 8px 2px 0; background: var(--asc-bg);
  border-top: 1px solid var(--asc-divider);
}
.summary-body .regen-row { border-top: none; padding-top: 6px; }
/* 固定底栏：Tab 页 = 滚动区 + 底部固定操作行 */
.pane-wrap { display: flex; flex-direction: column; height: 100%; }
.pane-scroll { flex: 1; overflow-y: auto; padding: 4px 2px; }
.regen-row {
  display: flex; gap: 10px; align-items: flex-end; flex-shrink: 0;
  padding: 12px 2px 6px;
  background: var(--asc-bg);
  border-top: 1px solid var(--asc-divider);
}
.regen-row .el-input { flex: 1; }
.regen-row .el-textarea__inner { font-size: 14px; line-height: 1.6; }
.regen-row .el-button { flex-shrink: 0; height: 32px; }
.ask-mode-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 6px 10px; margin: 6px 2px 0; flex-shrink: 0;
  background: var(--asc-primary-soft); border-radius: 8px;
}
.ask-mode-text {
  font-size: 12px; color: var(--asc-text-2);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.asking-hint {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 12px; margin: 6px 2px 0; flex-shrink: 0;
  background: var(--asc-primary-soft); border-radius: 8px;
}
.asking-text { font-size: 13px; color: var(--asc-primary); }
.asking-dots { display: inline-flex; gap: 3px; }
.asking-dots i {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--asc-primary);
  animation: asking-bounce 1.2s infinite ease-in-out;
}
.asking-dots i:nth-child(2) { animation-delay: .2s; }
.asking-dots i:nth-child(3) { animation-delay: .4s; }
@keyframes asking-bounce {
  0%, 80%, 100% { opacity: .3; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-3px); }
}

/* 知识点卡片 */
.kw-card {
  background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 12px;
  padding: 13px 15px; margin-bottom: 10px; transition: all .18s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .04);
}
.kw-card:hover { border-color: var(--asc-primary); box-shadow: var(--asc-shadow-hover); transform: translateY(-1px); }
.kw-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.kw-concept { font-weight: 600; font-size: 13px; }
.kw-page {
  font-size: 11px; color: var(--asc-primary); font-weight: 500;
  background: var(--asc-primary-soft); border-radius: 4px; padding: 2px 7px;
}
.kw-page-link { cursor: pointer; }
.kw-page-link:hover { background: rgba(124, 92, 252, .2); }
.kw-exp { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.75; margin-bottom: 6px; }
.kw-done { font-size: 12px; color: #0f6e56; }

/* 追问链卡片 */
.chain {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 12px; padding: 13px 15px; margin-bottom: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .04);
}
.chain-quote {
  font-size: 12px; color: var(--asc-text-2); background: var(--asc-primary-soft);
  border-left: 3px solid var(--asc-primary);
  padding: 8px 10px; border-radius: 6px; margin-bottom: 10px; line-height: 1.6;
}
.chain-page { color: var(--asc-primary); margin-left: 6px; font-weight: 500; }
.chain-page-link { cursor: pointer; }
.chain-page-link:hover { text-decoration: underline; }
.chain-q { font-size: 13px; font-weight: 600; margin: 10px 0 4px; }
.chain-a { font-size: 13px; line-height: 1.75; white-space: pre-wrap; color: var(--asc-text); }
.chain-streaming .chain-a { color: var(--asc-text-2); }
.stream-cursor { color: var(--asc-primary); animation: cursor-blink 1s step-end infinite; }
@keyframes cursor-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
.chain-ops { text-align: right; }
.follow-row { display: flex; gap: 10px; margin-top: 10px; align-items: center; }
.follow-row .el-input { flex: 1; }
.follow-row .el-button { flex-shrink: 0; height: 32px; }

/* Markdown 预览（笔记弹窗/分章总结） */
.md-preview {
  border: 1px solid var(--asc-border); border-radius: 10px; padding: 14px 18px;
  font-size: 13px; line-height: 1.8; max-height: 400px; overflow-y: auto; background: var(--asc-card);
}

.chain-continue { text-align: right; margin-top: 6px; }

/* 划线高亮（v-html 注入，需 :deep） */
.chunk :deep(.hl) { border-radius: 3px; padding: 0 1px; cursor: pointer; transition: background .15s; }
.chunk :deep(.hl-chain) { background: rgba(124, 92, 252, .15); border-bottom: 2px solid rgba(124, 92, 252, .45); }
.chunk :deep(.hl-chain):hover { background: rgba(124, 92, 252, .28); }
.chunk :deep(.hl-note) { background: rgba(232, 163, 61, .2); border-bottom: 2px solid rgba(232, 163, 61, .5); }
.chunk :deep(.hl-flash) { animation: hl-flash 2.4s ease forwards; border-radius: 3px; padding: 0 1px; }
/* P 徽标点击跳转后的 3 秒临时高亮（整段 / 文本匹配） */
.chunk :deep(.hl-flash-temp) { animation: hl-flash-temp 3s ease forwards; border-radius: 3px; padding: 0 1px; }
.md-seg-flash { animation: hl-flash-temp 3s ease forwards; border-radius: 6px; }
/* 用户主动划线（3 色）：文本视图 chunk + Markdown 整篇渲染共用 */
.chunk :deep(.hl-user), .md-body :deep(.hl-user) { border-radius: 3px; padding: 0 1px; cursor: pointer; transition: background .15s; }
.chunk :deep(.hl-yellow), .md-body :deep(.hl-yellow) { background: rgba(252, 211, 77, .32); border-bottom: 2px solid rgba(234, 179, 8, .6); }
.chunk :deep(.hl-green), .md-body :deep(.hl-green) { background: rgba(134, 239, 172, .42); border-bottom: 2px solid rgba(34, 197, 94, .6); }
.chunk :deep(.hl-blue), .md-body :deep(.hl-blue) { background: rgba(147, 197, 253, .45); border-bottom: 2px solid rgba(59, 130, 246, .6); }
@keyframes hl-flash {
  0%, 60% { background: rgba(124, 92, 252, .45); }
  100% { background: rgba(124, 92, 252, .15); }
}
@keyframes hl-flash-temp {
  0%, 70% { background: rgba(124, 92, 252, .5); }
  100% { background: rgba(124, 92, 252, 0); }
}
.hl-legend {
  display: flex; gap: 14px; padding: 12px 10px 4px; margin-top: 14px;
  border-top: 1px solid var(--asc-divider);
  font-size: 11px; color: var(--asc-text-3);
}
.hl-legend .dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 5px; }
.hl-legend .dot-chain { background: rgba(124, 92, 252, .5); }
.hl-legend .dot-note { background: rgba(232, 163, 61, .6); }
/* AI 生成失败重试条 */
.gen-error {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  margin: 8px 2px; padding: 10px 12px;
  background: rgba(210, 78, 78, .08); border: 1px solid rgba(210, 78, 78, .25);
  border-radius: 8px;
}
.gen-error-text { font-size: 12px; color: #b23c3c; line-height: 1.5; }

.media-player {
  padding: 16px 28px; background: var(--asc-card);
  border-bottom: 1px solid var(--asc-divider);
}
.media-video { width: 100%; max-height: 320px; border-radius: 10px; background: #000; }
.media-audio { width: 100%; }

.media-only { flex: 1; padding: 28px 40px; background: var(--asc-bg); }
.media-only .media-video { width: 100%; max-height: 70vh; border-radius: 12px; background: #000; }
.media-only .media-audio { width: 100%; margin-top: 24px; }

.image-origin { flex: 1; overflow-y: auto; padding: 28px 40px; background: var(--asc-bg); }
/* 原图按自然尺寸居中展示，不放大；仅当宽过阅读栏时才等比缩小 */
.image-origin img { display: block; margin: 0 auto; max-width: 100%; border-radius: 12px; box-shadow: var(--asc-shadow-hover); }

.page-marker.seekable { cursor: pointer; }
.page-marker.seekable:hover span { color: var(--asc-primary); }
.gen-hint { font-size: 12px; color: var(--asc-text-3); margin-top: 10px; }
.gen-tip {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 9px 12px; margin: 10px 0 0; flex-shrink: 0;
  background: var(--asc-primary-soft); border-radius: 8px;
  font-size: 13px; color: var(--asc-primary);
}
/* 长文档分组摘要的进度条：与 gen-tip 同区居中收窄，不撑满整栏 */
.gen-progress { width: 240px; margin: 8px auto 0; flex-shrink: 0; }
.chunk-edit-row { margin-bottom: 10px; }
.edit-row-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 4px;
}
/* 编辑模式原生 textarea（替换 el-input，选区事件更可靠） */
.edit-textarea {
  width: 100%; border: none; outline: none; resize: vertical;
  background: var(--asc-card); box-shadow: 0 0 0 1px var(--asc-border) inset;
  border-radius: 6px; padding: 8px 12px;
  font-family: inherit; font-size: 14px; line-height: 1.6; color: var(--asc-text);
  transition: box-shadow .18s ease;
}
.edit-textarea:focus { box-shadow: 0 0 0 1.5px var(--asc-primary) inset; }
.page-del-btn { margin-left: 8px; flex-shrink: 0; }
/* 删除按钮弱化：默认隐藏，hover 所在行/页时浮现 */
.edit-del-btn { opacity: 0; transition: opacity .15s ease; }
.chunk-edit-row:hover .edit-del-btn,
.page-marker:hover .edit-del-btn { opacity: 1; }
/* Markdown 整篇阅读排版 */
.md-reader .reader-inner { max-width: 760px; }
.md-body {
  font-size: 15px; line-height: 1.9; color: #363632;
  padding: 8px 0 32px; text-align: left;
}
.md-seg { scroll-margin-top: 16px; }
.md-seg:empty { display: none; }
.md-body :deep(h1) { font-size: 22px; font-weight: 600; margin: 24px 0 12px; line-height: 1.4; }
.md-body :deep(h2) { font-size: 18px; font-weight: 600; margin: 22px 0 10px; padding-left: 12px; border-left: 3px solid var(--asc-primary); line-height: 1.4; }
.md-body :deep(h3) { font-size: 16px; font-weight: 600; margin: 18px 0 8px; line-height: 1.4; }
.md-body :deep(p) { margin: 10px 0; }
.md-body :deep(ul), .md-body :deep(ol) { padding-left: 24px; margin: 10px 0; }
.md-body :deep(li) { margin: 5px 0; }
.md-body :deep(li::marker) { color: var(--asc-primary); }
.md-body :deep(strong) { font-weight: 600; }
.md-body :deep(code) { background: var(--asc-surface-2); padding: 2px 6px; border-radius: 4px; font-size: 13px; }
.md-body :deep(pre) { background: var(--asc-surface-2); padding: 14px 16px; border-radius: 8px; overflow-x: auto; }
.md-body :deep(pre code) { background: transparent; padding: 0; }
.md-body :deep(blockquote) { margin: 12px 0; padding: 8px 14px; border-left: 3px solid var(--asc-border); background: var(--asc-surface-2); border-radius: 0 8px 8px 0; color: var(--asc-text-2); }
.md-body :deep(hr) { border: none; border-top: 1px solid var(--asc-divider); margin: 20px 0; }
/* 正文外链（linkify 生成的，含剪藏正文首行「来源：URL」）：与主题一致，可点、看得出是链接。
   ⚠️ 只加在本组件：global.css 的 .md-preview 暂无 a 规则，但那处有多会话并发风险，不在这里动。 */
.md-body :deep(a), .md-preview :deep(a) {
  color: var(--asc-primary); text-decoration: none;
  border-bottom: 1px solid rgba(124, 92, 252, .38);
  word-break: break-all;          /* 长 URL/中文查询串不撑破阅读栏 */
  transition: border-color .15s;
}
.md-body :deep(a:hover), .md-preview :deep(a:hover) { border-bottom-color: var(--asc-primary); }
/* 正文图片自适应：剪藏/公众号原图常达 1000~2200px 宽，而正文容器只有 ~688px，
   不加约束时浏览器按**自然尺寸**渲染（实测最多溢出容器 1512px，必须横向滚动才能看全）。
   ⚠️ v-html 注入的节点不带 scoped 的 data-v 属性 → 只能用 :deep()。 */
.md-body :deep(img), .chunk :deep(img) {
  max-width: 100%;
  height: auto;            /* 只约束宽、不改宽高比，避免拉伸变形 */
  border-radius: 8px;
}
/* 上图（被 <br> 换到独立一行的图，剪藏/公众号正文即此形态）：居中 + 与正文留白。
   ⚠️ 必须用 `br + img` 相邻兄弟选择器，不能用 `img:only-child` ——
   markdown-it 开了 breaks，软换行渲染成 <br>，该 <p> 里除 img 还有一个 <br>，
   :only-child 永远不命中（已用真机 DOM 验证过）。 */
.md-body :deep(br + img), .chunk :deep(br + img) {
  display: block;
  margin: 12px auto;
}
.transcript-save { display: flex; align-items: center; gap: 12px; padding: 10px 0 4px; }
.transcript-hint { font-size: 12px; color: var(--asc-text-3); }

/* 语音模型下载引导卡片 */
.asr-download-card {
  margin-top: 16px; padding: 16px 18px;
  border: 1px solid var(--asc-border); border-radius: 12px;
  background: var(--asc-surface-2);
}
.asr-dl-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: 12px; }
.asr-dl-title { font-size: 15px; font-weight: 600; color: var(--asc-text); }
.asr-dl-sub { font-size: 12.5px; color: var(--asc-text-3); }
.asr-dl-select { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; margin-bottom: 8px; }
.asr-dl-radio { margin-right: 0; height: 28px; }
.asr-dl-radio :deep(.el-radio__label) { font-size: 13px; }
.asr-dl-desc { margin-bottom: 6px; }
.asr-dl-desc-text { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.5; }
.asr-dl-action { display: flex; align-items: center; gap: 12px; margin-top: 4px; }
.asr-dl-pct { font-size: 13px; color: var(--asc-primary); font-weight: 600; flex-shrink: 0; }
.asr-dl-error {
  display: flex; align-items: center; gap: 10px; margin-top: 12px;
  padding: 8px 12px; border-radius: 8px; font-size: 13px;
  background: rgba(245, 108, 108, .08); color: #f56c6c;
}
.asr-dl-error-text { flex: 1; }


/* 相关知识点弹窗 */
.related-list { max-height: 60vh; overflow-y: auto; }
.related-item {
  border: 1px solid var(--asc-border); border-radius: 10px;
  padding: 10px 14px; margin-bottom: 10px; cursor: pointer; transition: all .15s;
}
.related-item:hover { border-color: var(--asc-primary); background: var(--asc-primary-soft); }
.related-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
.related-page { font-size: 11px; color: var(--asc-primary); font-weight: 500; }
.related-type { font-size: 11px; color: var(--asc-text-3); background: var(--asc-surface-2); border-radius: 4px; padding: 1px 6px; }
.related-snippet { font-size: 12px; color: var(--asc-text-3); line-height: 1.6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ===== 学习页美化（2026-09-22 · 方案B 结构重排） ===== */
/* 空态：图标 + 主标题 + 副说明（原为单行灰字） */
.pane-empty { display: flex; flex-direction: column; align-items: center; padding: 56px 24px; }
.chains-empty { flex-direction: column; }
.pe-icon {
  width: 52px; height: 52px; border-radius: 14px; margin-bottom: 14px;
  display: flex; align-items: center; justify-content: center;
  color: var(--asc-primary); background: var(--asc-primary-soft);
}
.pe-title { font-size: 14px; font-weight: 600; color: var(--asc-text); margin: 0 0 4px; line-height: 1.5; }
.pe-sub { font-size: 12.5px; color: var(--asc-text-3); margin: 0 0 16px; line-height: 1.6; }
/* 面板滚动区留白 & 笔记卡容器感与右栏卡片统一 */
.pane-scroll { padding: 6px 4px; }
.note-item { border-radius: 12px; box-shadow: 0 1px 3px rgba(0, 0, 0, .04); }
.notes-batch { border-radius: 12px; }
/* 底部操作行：与分段控件同代语言（圆角 8、留白一致） */
.regen-row { padding: 12px 2px 10px; }
.regen-row .el-button { height: 34px; border-radius: 8px; }
.regen-row :deep(.el-textarea__inner) { border-radius: 8px; }
/* 左栏收紧，与右栏 12px 边距对齐 */
.col-left { padding: 18px 12px; }
.learn-quiz-tip {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  /* ⚠️⚠️ 右侧必须留出 76px：本提示条贴在内容区底部、按钮右对齐，会与「study 页悬浮问答球」
     （App.vue `.float-chat-study`：right 26px + 宽 56px + z-index 90）**重叠**。
     实测（1440×960）：按钮 box x∈[1346,1416] y∈[840,864]，悬浮球 x∈[1358,1414] y∈[796,852]
     → 点按钮右半部分会被悬浮球抢走点击（Playwright 表现为 click 超时）。
     26+56=82 是悬浮球占用的水平宽度；扣掉按钮自身内边距，76px 足够让按钮整体移出重叠区。 */
  padding: 8px 76px 8px 12px; margin: 10px 0 0; flex-shrink: 0;
  background: var(--asc-primary-soft); border-radius: 8px;
  font-size: 12px; color: var(--asc-text-2);
}
/* 出题失败提示：与「摘要」Tab 的错误色一致 */
.learn-quiz-tip.is-error {
  background: rgba(210, 78, 78, .08); color: #b23c3c;
}


/* ===== 摘要 / 讲义：按结构适配的线性图标 ===== 
   由 script 区的 headingIconRule / headingNumRule 在渲染时写入
   data-ico（讲义骨架）/ data-num（摘要章节序号），只作用于渲染结果。
   ⚠️ 不写回 content：这两处内容落库，且「转笔记」为原文搬运，
      图标若进内容层会污染 DB / 笔记 / 知识库检索。 */
.summary-text :deep([data-ico])::before {
  content: '';
  display: inline-block;
  width: 15px; height: 15px;
  margin-right: 6px;
  vertical-align: -2px;
  background-color: var(--asc-primary);
  -webkit-mask-image: var(--ico); mask-image: var(--ico);
  -webkit-mask-repeat: no-repeat; mask-repeat: no-repeat;
  -webkit-mask-position: center; mask-position: center;
  -webkit-mask-size: contain; mask-size: contain;
}
/* 小节级图标（引入 / C{n}）降为次要灰；本节小结保留主色 —— 它是每章的落点 */
.summary-text :deep([data-ico="compass"])::before,
.summary-text :deep([data-ico="dot"])::before { background-color: var(--asc-text-3); }
/* 讲义章节标题改用图标，撤掉 h2 的左侧色条（避免「色条 + 图标」两层竖线堆叠） */
.summary-text :deep(h2[data-ico]) { border-left: none; padding-left: 0; }

/* 摘要侧：章节序号胶囊 */
.summary-text :deep(h2[data-num])::before {
  content: attr(data-num);
  display: inline-block;
  min-width: 18px; height: 18px;
  padding: 0 5px; margin-right: 7px;
  vertical-align: 1px;
  border-radius: 5px;
  background-color: var(--asc-primary-soft);
  color: var(--asc-primary);
  font-size: 11px; font-weight: 600;
  line-height: 18px; text-align: center;
}

/* 图标资源：单色线性，用 mask 承载 → 颜色由上面的 background-color 决定 */
.summary-text :deep([data-ico="target"]) { --ico: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='8.4'/%3E%3Ccircle cx='12' cy='12' r='3.4'/%3E%3C/svg%3E"); }
.summary-text :deep([data-ico="route"]) { --ico: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='5.2' cy='18' r='2'/%3E%3Ccircle cx='18.8' cy='6' r='2'/%3E%3Cpath d='M7.2 18h5.3a3 3 0 0 0 0-6H9.5a3 3 0 0 1 0-6h7.3'/%3E%3C/svg%3E"); }
.summary-text :deep([data-ico="book"]) { --ico: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5V5.5z'/%3E%3Cpath d='M4 20.5A2.5 2.5 0 0 1 6.5 18H20'/%3E%3C/svg%3E"); }
.summary-text :deep([data-ico="compass"]) { --ico: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='8.4'/%3E%3Cpath d='m15.2 8.8-2.1 4.3-4.3 2.1 2.1-4.3z'/%3E%3C/svg%3E"); }
.summary-text :deep([data-ico="dot"]) { --ico: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='3.6' fill='black' stroke='none'/%3E%3C/svg%3E"); }
.summary-text :deep([data-ico="summary"]) { --ico: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 6.5h8.5M4 12h8.5M4 17.5h5'/%3E%3Cpath d='m14.4 16.6 2.2 2.2 4.4-4.8'/%3E%3C/svg%3E"); }
.summary-text :deep([data-ico="anchor"]) { --ico: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='4.8' r='1.9'/%3E%3Cpath d='M12 6.7V21'/%3E%3Cpath d='M4.8 13.2a7.2 7.2 0 0 0 14.4 0'/%3E%3Cpath d='M9 9.6h6'/%3E%3C/svg%3E"); }
.summary-text :deep([data-ico="flag"]) { --ico: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M5.4 21V3.9'/%3E%3Cpath d='M5.4 4.7h11.2l-1.9 3.9 1.9 3.9H5.4'/%3E%3C/svg%3E"); }
</style>
