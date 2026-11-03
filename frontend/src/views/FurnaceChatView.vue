<template>
  <div class="fc-page">
    <!-- 左：舞台卡（形象常驻陪伴，气质色随丹灵） -->
    <aside class="fc-stage" :style="stageTheme">
      <div class="stage-glow"></div>
      <div class="stage-spirit pokeable" title="点它一下" @click="pokeSpirit">
        <img class="stage-img spirit-breathe" :src="stageImg" alt="" />
        <transition name="poke-fade"><div v-if="pokeText" class="poke-bubble">{{ pokeText }}</div></transition>
      </div>
      <div class="stage-name">{{ app?.name || '…' }}</div>
      <div class="stage-persona">{{ (app?.persona || '').slice(0, 30) }}…</div>
      <!-- 默契度成长（B1）：阶段徽章 + 进度条 + 下一级缺口（数据全部来自后端 app.bond） -->
      <div class="bond-chip" :title="bondTip">{{ bond.label }} · 已聊 {{ app?.session_count || 0 }} 场</div>
      <div v-if="bond.next" class="bond-bar" :title="bondTip"><i :style="{ width: bondBarWidth }"></i></div>
      <div v-if="bond.next" class="bond-next">{{ bondNextText }}</div>
      <!-- 2026-11-02：左侧只给数量口径，不展示资料/笔记名（清单去配置页看；可展开明细已删） -->
      <div class="stage-mats">
        <span class="mat-chip mat-sum">{{ (app?.materials || []).length }} 份资料<template v-if="(app?.notes || []).length"> · {{ (app?.notes || []).length }} 条笔记</template></span>
      </div>
      <el-button size="small" @click="$router.push(`/furnace/app/${appId}/edit`)">配置</el-button>
      <el-button size="small" text @click="$router.push('/furnace')">返回丹房</el-button>
    </aside>

    <!-- 窄屏抽屉遮罩（A1）：桌面端 sessOpen 恒 false，这层永不出现 -->
    <div v-if="sessOpen" class="sess-mask" @click="sessOpen = false"></div>

    <!-- 中：会话列（按应用隔离；窄屏收为抽屉，支持左滑收起） -->
    <aside class="fc-sess" :class="{ open: sessOpen }"
      @touchstart.passive="onDrawerTouchStart" @touchend.passive="onDrawerTouchEnd">
      <div class="sess-drawer-head">
        <span>会话</span>
        <button type="button" class="sess-drawer-x" title="收起" @click="sessOpen = false">×</button>
      </div>
      <el-button type="primary" class="new-btn" @click="newSession">＋ 新会话</el-button>
      <el-input v-model="sessSearch" placeholder="搜索会话" clearable size="small" class="sess-search" />
      <template v-for="g in groupedSessions" :key="g.label">
        <div class="sess-group-label">{{ g.label }}</div>
        <div v-for="s in g.items" :key="s.id" class="sess-item" :class="{ active: s.id === currentId }" @click="openSession(s.id)">
          <span class="pin-icon" :class="{ on: s.pinned }" :title="s.pinned ? '取消置顶' : '置顶'"
            @click.stop="togglePin(s)">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
            </svg>
          </span>
          <div class="sess-body">
            <el-input v-if="renamingId === s.id" v-model="renameText" size="small"
              @click.stop @keyup.enter="saveRename(s)" @blur="saveRename(s)" />
            <template v-else>
              <div class="sess-title" :title="s.title + '（双击重命名）'" @dblclick.stop="startRename(s)">{{ s.title }}</div>
              <div class="sess-meta">{{ s.message_count }} 问 · {{ relTime(s.updated_at) }}</div>
              <div v-if="s.last_message" class="sess-preview">{{ s.last_message }}</div>
            </template>
          </div>
          <el-button size="small" text type="danger" class="sess-del" title="删除（4 秒内可撤销）"
            @click.stop="removeSession(s.id)">×</el-button>
        </div>
      </template>
      <div v-if="!sessions.length" class="sess-empty">暂无会话</div>
      <div v-else-if="!groupedSessions.length" class="sess-empty">无匹配会话</div>
    </aside>

    <!-- 右：消息流 -->
    <main class="fc-main">
      <!-- 窄屏顶栏（A1）：<1100px 时舞台卡/会话列收走，会话抽屉、配置、返回从这里进 -->
      <div class="fc-topbar">
        <button type="button" class="tb-btn" title="会话列表" @click="sessOpen = true">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round"><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/></svg>
        </button>
        <span class="tb-name">{{ app?.name || '…' }}</span>
        <span class="tb-bond">{{ bond.label }}</span>
        <el-button size="small" text class="tb-cfg" @click="$router.push(`/furnace/app/${appId}/edit`)">配置</el-button>
        <el-button size="small" text @click="$router.push('/furnace')">丹房</el-button>
      </div>
      <div class="fc-msgarea" ref="msgArea" @scroll="onMsgScroll">
        <div class="fc-col">
          <!-- 空会话：欢迎消息 + 建议问题（开场白由配置来，不落库） -->
          <div v-if="!messages.length" class="msg ai">
            <div class="avatar"><img class="avatar-img" :src="spiritImg(app?.spirit || 'book')" alt="" /></div>
            <div class="bubble">
              {{ greetByHour() }}，{{ app?.welcome || '有什么想聊的？' }}
              <!-- 未配置模型：进页就给引导（与主问答同口径），而不是等发问才撞 [NEED_SETUP]。
                   此时建议问题藏起来 —— 点了必然失败，等于诱导用户撞错。 -->
              <div v-if="!configured" class="setup-card">
                <div class="setup-t">开始前只差一步：配置一个 AI 模型</div>
                <div class="setup-s">1 分钟搞定，多数厂商新用户送免费额度</div>
                <el-button type="primary" size="small" @click="router.push('/settings?add=1')">前往配置模型</el-button>
              </div>
              <div class="q-chips" v-else-if="(app?.questions || []).length">
                <span v-for="(q, i) in app.questions" :key="i" class="q-chip" @click="ask(q)">{{ q }}</span>
              </div>
            </div>
          </div>

          <div v-for="m in messages" :key="m._tmp || m.id" class="msg" :class="[m.role === 'user' ? 'user' : 'ai', { miss: m.answer_kind === 'kb_miss' }]">
            <div class="avatar" v-if="m.role !== 'user'"><img class="avatar-img" :src="spiritImg(app?.spirit || 'book')" alt="" /></div>
            <div class="avatar user-av" v-else>我</div>
            <div class="bubble" :class="{ editing: m._editing }">
              <!-- 用户消息（P1 三件套）：复制 / 编辑后重发。编辑走 truncate ——
                   旧回答会被删掉重生成，与主问答同口径，不制造重复消息。 -->
              <template v-if="m.role === 'user'">
                <template v-if="m._editing">
                  <el-input v-model="m._editText" type="textarea" :rows="3" resize="none"
                    placeholder="修改问题后重新生成回答" />
                  <div class="edit-ops">
                    <button type="button" class="edit-btn ghost" @click="cancelEdit(m)">取消</button>
                    <button type="button" class="edit-btn solid" :disabled="!m._editText.trim()"
                      @click="confirmEdit(m)">保存并重新生成</button>
                  </div>
                </template>
                <template v-else>
                  <div class="u-text">{{ m.content }}</div>
                  <div class="msg-ops">
                    <span class="msg-op" :class="{ done: m._copied }" @click="copyUserMsg(m)">
                      {{ m._copied ? '✓ 已复制' : '复制' }}</span>
                    <span class="msg-op" title="改完这个提问，它之后的对话会重新生成" @click="startEdit(m)">编辑</span>
                  </div>
                </template>
              </template>
              <!-- 思维链（P0 对齐主问答）：后端照发 reasoning 事件，不消费=开了「标准/深度」
                   只看到长时间空等。流式读 _reasoning，刷新后读落库的 reasoning。 -->
              <div v-if="m.role !== 'user' && (m._reasoning || m.reasoning)" class="reasoning-box">
                <div class="reasoning-toggle" @click="m._showReasoning = !m._showReasoning">
                  <span class="reasoning-label">思考过程</span>
                  <span v-if="m._streaming && !m.content" class="reasoning-thinking">思考中…</span>
                  <span class="reasoning-arrow" :class="{ open: m._showReasoning }">▾</span>
                </div>
                <div v-show="m._showReasoning" class="reasoning-body md-body" v-html="renderReasoning(m)"></div>
              </div>
              <!-- 未命中口径提示条（P1 对齐主问答）：应用是「知识边界锁定」的专属助手，
                   答不到时必须把它写在界面上 —— 只有米色气泡的话，用户看不出这是「没找到」
                   还是「角色心情」。 -->
              <div v-if="missBarText(m)" class="kb-miss-bar">{{ missBarText(m) }}</div>
              <div v-if="m.role !== 'user' && m._streaming && !m.content && !(m._reasoning || m.reasoning)"
                class="thinking-line"><span class="thinking-dot"></span><span class="thinking-dot"></span><span class="thinking-dot"></span>正在想…</div>
              <div v-else-if="m.role !== 'user'" class="md-body" v-html="renderContent(m)"
                @click="onMdClick(m, $event)"></div>
              <!-- 流式光标（P3）：正文停顿那几秒没有「还在写」的信号，看着像卡住了。
                   只在已出字时显示 —— 一个字都没有时「正在想…」三个点已经是同一个意思，
                   两个一起出现只是噪音。 -->
              <span v-if="m.role !== 'user' && m._streaming && m.content" class="cursor">▍</span>
              <!-- 引用来源（P1 对齐主问答）：正文 [n] 变可点角标，底部折叠成「引用 N 条」。
                   原来是一排 chip、snippet 塞在 title 属性里 —— 等于引用不可读。
                   ⚠️ !m._streaming：sources 事件在 token 之前就下发，不加这道闸的话
                   回答还在吐字、引用区就挂在气泡下面（2026-10-30 用户反馈「显示太提前」）。
                   流式期间点正文角标不受影响 —— 弹气泡走的是 citePop，不依赖这块 DOM。 -->
              <div v-if="m.role !== 'user' && !m._streaming && m.sources && m.sources.length" class="sources">
                <div class="sources-toggle" @click="toggleSources(m)">
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
                    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
                  </svg>
                  引用 {{ m.sources.length }} 条
                  <span class="toggle-arrow" :class="{ open: m._showSources }">▾</span>
                </div>
                <div v-if="m._showSources" class="source-list">
                  <div v-for="(s, i) in m.sources" :key="i" class="source-card"
                    :class="{ focused: i + 1 === m._focusSource }" @click="goSource(s)">
                    <div class="source-head">
                      <span class="ref-tag" :class="'rt-' + (s.ref_type || 'chunk')">{{ refLabel(s.ref_type) }}</span>
                      <span class="source-title">{{ s.title }}</span>
                      <span v-if="s.page_no" class="source-page">P{{ s.page_no }}</span>
                    </div>
                    <div class="source-snippet">{{ s.snippet || '（无片段）' }}…</div>
                  </div>
                </div>
              </div>
              <div v-if="m.error" class="err-line">
                <span>{{ errText(m) }}</span>
                <span v-if="!asking" class="retry" @click="retryLast(m)">重试</span>
                <span v-if="m._needSetup" class="retry" @click="$router.push('/settings?add=1')">去配置</span>
              </div>
              <div v-if="m.role !== 'user' && m.suggestions && m.suggestions.length && !m._streaming" class="q-chips">
                <!-- 不标一句就和开场推荐（气泡里那组）长得一样，分不清「推荐话题」还是「接着问」 -->
                <span class="q-chips-label">继续问</span>
                <span v-for="(q, i) in m.suggestions" :key="i" class="q-chip" @click="ask(q)">{{ q }}</span>
              </div>
              <!-- 回答底部操作（P1 三件套 + 转存）：重新生成只挂最后一条 ——
                   中间回答重生成会把之后的整段对话一起截掉（后端 truncate 语义），
                   不给确认就丢整段对话太危险；错误态也不放（err-line 的「重试」已是同一动作）。 -->
              <div v-if="m.role !== 'user' && !m._streaming && m.content" class="answer-actions">
                <!-- 闲置搭话（_idle）不落库、也没有对应提问：给它「重新生成/转存」会错位
                     （标题会取到上一条提问）。只留复制。 -->
                <template v-if="!m._idle">
                  <span v-if="!m.error && isLastAssistant(m)" class="msg-op"
                    title="用同一个问题重新生成（旧回答不保留）" @click="regenerate(m)">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                    </svg>重新生成</span>
                  <span class="msg-op" :class="{ done: m._savedNote }"
                    :title="m._savedNote ? '在知识库里查看这条笔记' : '标题 = 你的提问 · 内容 = 本回答'"
                    @click="m._savedNote ? viewNote(m) : saveNote(m)">
                    <svg v-if="!m._savedNote" viewBox="0 0 24 24" width="12" height="12" fill="none"
                      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
                    </svg>
                    <svg v-else viewBox="0 0 24 24" width="12" height="12" fill="none"
                      stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M20 6L9 17l-5-5"/>
                    </svg>
                    {{ m._savedNote ? '已转存 · 查看' : '转笔记' }}</span>
                  <span class="msg-op" :class="{ done: m._savedDoc }"
                    :title="m._savedDoc ? '在编辑器里打开这份文档（标题可改）'
                      : '存成一份文档：进材料库，可阅读 / 出讲义 / 出题 / 做视频，知识库也能检索'"
                    @click="m._savedDoc ? openSavedDoc(m) : saveDoc(m)">
                    <svg v-if="!m._savedDoc" viewBox="0 0 24 24" width="12" height="12" fill="none"
                      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                      <path d="M14 2v6h6"/>
                    </svg>
                    <svg v-else viewBox="0 0 24 24" width="12" height="12" fill="none"
                      stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M20 6L9 17l-5-5"/>
                    </svg>
                    {{ m._savedDoc ? '已存文档 · 查看' : '转文档' }}</span>
                </template>
                <span class="msg-op" :class="{ done: m._copied }" @click="copyAnswer(m)">
                  <svg v-if="!m._copied" viewBox="0 0 24 24" width="12" height="12" fill="none"
                    stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                  </svg>
                  <svg v-else viewBox="0 0 24 24" width="12" height="12" fill="none"
                    stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M20 6L9 17l-5-5"/>
                  </svg>
                  {{ m._copied ? '已复制' : '复制回答' }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="fc-input-area">
        <!-- 回到顶部 / 底部：上翻读旧回答时，唯一能一步回到底的入口 -->
        <div v-if="messages.length" class="scroll-btns">
          <div v-show="!atTop" class="scroll-btn" title="回到顶部" @click="scrollToTop">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
              stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>
          </div>
          <div v-show="!atBottom" class="scroll-btn" title="回到底部" @click="scrollBottom(true)">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
              stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M19 12l-7 7-7-7"/></svg>
          </div>
        </div>
        <div class="fc-input">
          <!-- 多行（P2）：贴题干/代码时单行会被压成一条，也没法预留换行。
               Enter 发送、Shift+Enter 换行由 enterToSend 判定（它已排除组词回车与修饰键）。 -->
          <el-input v-model="question" type="textarea" :rows="2" resize="none"
            placeholder="问它点什么……"
            @keydown.enter.exact="onEnterSend" @input="armIdle" />
          <el-button v-if="!asking" type="primary" @click="send" :disabled="!question.trim()">发送</el-button>
          <el-button v-else @click="stopAsk">停止</el-button>
        </div>
        <div class="model-toolbar" v-if="models.length">
          <el-select v-model="chatModel" size="small" class="chip-sel" title="本会话临时切换模型（不写回应用配置）">
            <el-option :label="`应用默认（${appModelLabel}）`" value="default" />
            <el-option v-for="m in models" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
          </el-select>
          <el-select v-model="effort" size="small" class="chip-sel" title="推理强度">
            <el-option label="快速" value="low" /><el-option label="标准" value="high" />
            <el-option label="深度" value="max" /><el-option label="关闭推理" value="off" />
          </el-select>
        </div>
      </div>
    </main>

    <!-- 默契升级仪式（B1）：跨阶段时短播一次（复用揭炉的光柱语言），点遮罩/ESC 即收、3s 自收 -->
    <div v-if="bondUp" class="bondup-mask" @click="closeBondUp">
      <div class="bondup-box" @click.stop>
        <div class="bondup-beam"></div>
        <img class="bondup-img" :src="spiritImg(app?.spirit || 'book', 'happy')" alt="" />
        <div class="bondup-text">默契升级</div>
        <div class="bondup-label">{{ bondUp.label }}</div>
        <div class="bondup-sub">{{ bondUp.sub }}</div>
      </div>
    </div>

    <!-- 引用来源气泡（点正文角标弹出；点别处关闭） -->
    <div v-if="citePop.show" class="cite-pop" :style="{ left: citePop.x + 'px', top: citePop.y + 'px' }"
      @click.stop>
      <div class="cite-pop-head">
        <span class="ref-tag" :class="'rt-' + (citePop.source.ref_type || 'chunk')">{{ refLabel(citePop.source.ref_type) }}</span>
        <span class="cite-pop-title">{{ citePop.source.title }}</span>
        <span v-if="citePop.source.page_no" class="cite-pop-page">P{{ citePop.source.page_no }}</span>
      </div>
      <div class="cite-pop-snippet">{{ citePop.source.snippet || '（无片段）' }}…</div>
      <div class="cite-pop-foot" @click="jumpCite">去原文看 →</div>
    </div>

    <!-- 对话笔记查看/编辑（P1 对齐主问答）：转存后就地改，不必跳知识库。
         只搬「编辑/预览 + 保存 + 删除 + 复习」，AI 加工（改写等）没搬 —— 它要再套一层
         「原文 vs 结果」对比弹窗，与本条无关。 -->
    <el-dialog v-model="noteDlg.show" width="640px" class="chat-note-dialog" :close-on-click-modal="false">
      <template #header>
        <div class="cnd-head">
          <span class="cnd-title">对话笔记</span>
          <span class="cnd-tag">对话笔记</span>
          <span class="cnd-sub">来自与「{{ app?.name || '应用' }}」的对话<template v-if="noteDlg.createdAt"> · {{ String(noteDlg.createdAt).slice(0, 10) }}</template></span>
        </div>
      </template>

      <div v-loading="noteDlg.loading">
        <div class="cnd-field">
          <label class="cnd-label" for="fc-cnd-title">标题（原问题）</label>
          <el-input id="fc-cnd-title" v-model="noteDlg.title" placeholder="给这条笔记起个标题" maxlength="255" />
        </div>
        <div class="cnd-field">
          <div class="cnd-field-head">
            <label class="cnd-label">内容（AI 回答）</label>
            <div class="cnd-seg" role="tablist" aria-label="笔记内容编辑方式">
              <button type="button" class="cnd-seg-btn" :class="{ active: noteDlg.mode === 'edit' }"
                role="tab" :aria-selected="noteDlg.mode === 'edit'" @click="noteDlg.mode = 'edit'">编辑</button>
              <button type="button" class="cnd-seg-btn" :class="{ active: noteDlg.mode === 'preview' }"
                role="tab" :aria-selected="noteDlg.mode === 'preview'" @click="noteDlg.mode = 'preview'">预览</button>
            </div>
          </div>
          <textarea v-if="noteDlg.mode === 'edit'" v-model="noteDlg.content" :rows="10" class="cnd-textarea"
            placeholder="支持 Markdown" :disabled="noteDlg.loading"></textarea>
          <div v-else class="md-body cnd-preview" v-html="notePreviewHtml"></div>
        </div>
      </div>

      <div class="cnd-tools">
        <el-button text type="warning" size="small" :loading="noteDlg.reviewing" @click="addNoteToReview">加入复习</el-button>
        <el-button text type="primary" size="small" :loading="noteDlg.recalling" @click="addNoteToRecall">生成复述卡</el-button>
        <span class="cnd-sep" aria-hidden="true"></span>
        <el-popconfirm title="删除这条笔记？不可恢复"
          confirm-button-text="删除" confirm-button-type="danger" cancel-button-text="取消"
          @confirm="deleteNoteDlg">
          <template #reference>
            <el-button type="danger" text size="small">删除笔记</el-button>
          </template>
        </el-popconfirm>
      </div>

      <template #footer>
        <div class="cnd-footer">
          <span class="cnd-hint">保存后自动同步到知识库「按笔记」</span>
          <div class="cnd-actions">
            <el-button @click="noteDlg.show = false">关闭</el-button>
            <el-button type="primary" :loading="noteDlg.saving" @click="saveNoteDlg">保存修改</el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, h, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElNotification } from 'element-plus'
import { appsApi, chatApi, materialApi, noteApi, reviewApi, settingsApi } from '../api'
import http from '../api/http'
import { createMd, renderMd } from '../utils/md'
import { streamSSE } from '../utils/sse'
import { spiritImg, greetByHour, spiritTheme, parseSpirit, SPIRIT_QUIPS } from '../utils/spirit'
import { enterToSend } from '../utils/keys'

const route = useRoute()
const router = useRouter()
const appId = Number(route.params.id)
const app = ref(null)
const sessions = ref([])
const messages = ref([])
const currentId = ref(null)
const question = ref('')
const asking = ref(false)
// 是否已配置 LLM 模型：未配置时空态给「配置引导」。默认 true 避免首屏闪烁（与主问答同口径）
const configured = ref(true)
const sessSearch = ref('')
const renamingId = ref(null)
const renameText = ref('')
const models = ref([])
const globalChatModelId = ref('')   // 管理中心「对话模型」引用：用于把「跟随全局」解析成当前生效的具体模型名
// ⚠️ 哨兵值，不能用 ''：Element Plus 选中的 isEmptyValue() 把 '' / null / undefined 都判成「无值」
//    → 选项不高亮、显示回落成 placeholder「请选择」（2.14.5 实测，useSelect.mjs:80）。
//    发请求时再映射回 null（后端语义：null = 应用默认 / 服务商默认）。
const chatModel = ref('default')   // 'default'=应用默认（后端解析：app.model_id → 全局）
const effort = ref('low')          // 'off'=关闭推理（同样不能用 ''）
const msgArea = ref(null)
const mood = ref('normal')         // normal / think / happy / miss
let abortCtrl = null
let moodTimer = null
const md = createMd({ breaks: true })   // 与主问答同配置：否则同样一段单换行回答在这里会被并成一段

// 舞台全身丹灵（位图）：容器 .stage-spirit 高 128px，img 按高撑满、底对齐
// 待机眨眼（P1）：常态下每 4.6s 闪切闭眼帧 140ms；情绪非 normal 时不眨；reduced-motion 不眨
const blinkOn = ref(false)
let blinkIv = null, blinkOff = null
const _reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
const stageImg = computed(() => {
  const sp = app.value?.spirit || 'book'
  return spiritImg(sp, blinkOn.value && mood.value === 'normal' ? 'blink' : mood.value)
})
function startBlink() {
  blinkIv = setInterval(() => {
    if (_reducedMotion.matches || mood.value !== 'normal') return
    blinkOn.value = true
    clearTimeout(blinkOff)
    blinkOff = setTimeout(() => { blinkOn.value = false }, 140)
  }, 4600)
}
startBlink()
const stageTheme = computed(() => {
  const t = spiritTheme(app.value?.spirit || 'book')
  return { '--app-body': t.body, '--app-edge': t.edge }
})
// 资料/笔记名不展示了（2026-11-02）：matsOpen 展开态随之删除
// 窄屏会话抽屉（A1）：桌面端恒 false（顶栏只在 <1100px 显示，没人能把它打开）
const sessOpen = ref(false)

// ---- 默契度成长（B1）：阶段/进度/缺口全部来自后端 app.bond（app_to_dict 装配） ----
// ⚠️ 声明必须在 app 之后（setup 的 computed getter 注册即同步跑一次，读前声明的 ref 才不 TDZ）。
const bond = computed(() => app.value?.bond
  || { stage: 0, label: '初识', sessions: 0, questions: 0, days: 0, progress: 0, next: null })
const bondBarWidth = computed(() => `${Math.round((bond.value.progress || 0) * 100)}%`)
const bondTip = computed(() =>
  `已聊 ${bond.value.sessions} 场 · ${bond.value.questions} 问 · ${bond.value.days} 个活跃日`)
const bondNextText = computed(() => {
  const n = bond.value.next
  if (!n) return ''
  const lack = []
  if (bond.value.sessions < n.sessions) lack.push(`${n.sessions - bond.value.sessions} 场`)
  if (bond.value.questions < n.questions) lack.push(`${n.questions - bond.value.questions} 问`)
  if (bond.value.days < n.days) lack.push(`${n.days - bond.value.days} 天`)
  return lack.length ? `再 ${lack.join(' · ')} 升级「${n.label}」` : ''
})

// 升级仪式：localStorage 记**历史最高档**，只有突破最高纪录才播 ——
// 原来记「上次见过的档」：删会话掉档后再升回来会重播一次（降级又升回不是「突破」）。
// 首次进页（无记录）只记不播：老用户第一次打开就弹仪式会莫名其妙。
// 收起路径与揭炉仪式同口径：点遮罩 / ESC / 3s 自收。
const bondUp = ref(null)          // {label, sub}
let bondUpTimer = null
function onBondupKey(e) { if (e.key === 'Escape') closeBondUp() }
function closeBondUp() {
  bondUp.value = null
  clearTimeout(bondUpTimer)
  window.removeEventListener('keydown', onBondupKey)
}
function checkBondUp(data) {
  const b = data?.bond
  if (!b) return
  const key = `furnace-bond-max-${appId}`
  const raw = localStorage.getItem(key)
  const prevMax = raw === null ? -1 : Number(raw)
  if (prevMax >= 0 && b.stage > prevMax) {
    bondUp.value = {
      label: b.label,
      sub: `${b.sessions} 场 · ${b.questions} 问 · ${b.days} 天，越来越有默契了`,
    }
    clearTimeout(bondUpTimer)
    bondUpTimer = setTimeout(closeBondUp, 3000)
    window.addEventListener('keydown', onBondupKey)
  }
  if (b.stage > prevMax) localStorage.setItem(key, String(b.stage))
  else if (prevMax < 0) localStorage.setItem(key, String(b.stage))
}

// 点击丹灵彩蛋（B2）：本地文案池随机一句，零请求零等待；600ms 节流防连点刷屏。
// 随机避开上一句（池小，连点有概率出同一句，看起来像没点上）。
// 默契/灵犀（stage≥2）并入 close 池：成长感不止在徽章上，说话口气也跟着变。
const pokeText = ref('')
let pokeTimer = null
let lastPokeAt = 0
let lastQuip = ''
function pokeSpirit() {
  const now = Date.now()
  if (now - lastPokeAt < 600) return
  lastPokeAt = now
  const q = SPIRIT_QUIPS[parseSpirit(app.value?.spirit || 'book').deco] || SPIRIT_QUIPS.book
  const pool = bond.value.stage >= 2 ? [...q.base, ...q.close] : q.base
  const fresh = pool.length > 1 ? pool.filter(x => x !== lastQuip) : pool
  lastQuip = fresh[Math.floor(Math.random() * fresh.length)]
  pokeText.value = lastQuip
  setMood('happy', 2000)
  clearTimeout(pokeTimer)
  pokeTimer = setTimeout(() => { pokeText.value = '' }, 2400)
}
// 全局对话模型名：把「跟随全局」解析成当前生效的具体模型（每次进页重拉设置，管理中心改了就变）
const globalModelLabel = computed(() => {
  const m = models.value.find(x => x.id === globalChatModelId.value)
  return m ? `${m.name}（${m.model}）` : ''
})
const appModelLabel = computed(() => {
  // 跟随全局：显示当前生效的全局对话模型 —— 只写「跟随全局」时改配置后界面毫无变化，像没生效
  if (!app.value?.model_id) return globalModelLabel.value ? `跟随全局 · ${globalModelLabel.value}` : '跟随全局'
  const m = models.value.find(x => x.id === app.value.model_id)
  // 钉住的模型在管理中心被删 → 后端严格解析报 NEED_SETUP，标签必须如实报失效（原来谎报「跟随全局」）
  return m ? m.name : '已失效 · 请到配置页重选'
})

// 后端 updated_at = datetime.utcnow().isoformat()：**不带时区**的 UTC 串。
// 直接 new Date() 会按本地时间解析 → 比真实时间早 8 小时（会话被分到「昨天」、
// 列表显示的是 UTC 钟点）。与主问答同口径：没有时区标记时补 Z 再解析。
function parseTs(ts) {
  if (!ts) return null
  const s = String(ts).replace(' ', 'T')
  const d = new Date(/([Zz]|[+-]\d{2}:?\d{2})$/.test(s) ? s : s + 'Z')
  return isNaN(d.getTime()) ? null : d
}

// 会话分组：置顶 / 今天 / 昨天 / 7 天内 / 更早（沿用主问答视觉）
// 搜索覆盖标题 + 最后一条消息 —— 只搜标题时，记得内容记不住标题就搜不到。
const groupedSessions = computed(() => {
  const q = sessSearch.value.trim().toLowerCase()
  let list = sessions.value
  if (q) {
    list = list.filter(s =>
      (s.title || '').toLowerCase().includes(q) ||
      (s.last_message || '').toLowerCase().includes(q))
  }
  const now = new Date()
  const today0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const groups = [['置顶', []], ['今天', []], ['昨天', []], ['7 天内', []], ['更早', []]]
  for (const s of list) {
    if (s.pinned) { groups[0][1].push(s); continue }
    const d = parseTs(s.updated_at)
    const day0 = d ? new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() : 0
    const days = Math.round((today0 - day0) / 86400000)
    if (days <= 0) groups[1][1].push(s)
    else if (days === 1) groups[2][1].push(s)
    else if (days < 7) groups[3][1].push(s)
    else groups[4][1].push(s)
  }
  return groups.filter(g => g[1].length).map(([label, items]) => ({ label, items }))
})

function relTime(ts) {
  const d = parseTs(ts)
  if (!d) return ''
  const now = new Date()
  if (d.toDateString() === now.toDateString()) {
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0')
  }
  if (d.toDateString() === new Date(now.getTime() - 864e5).toDateString()) return '昨天'
  return String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0')
}

// 渲染回答：正文里的 [n] 换成可点角标（n 对应 sources 序号）。
// 只认「句末 / 标点前」的位置 —— 句首或后接文字的 [1] 多半是编号、数组下标，乱点就是坑。
function renderContent(m) {
  let html = renderMd(md, m.content || '', { streaming: !!m._streaming })
  const sources = m.sources || []
  if (sources.length) {
    html = html.replace(/\[(1?\d)\](?=[，。；、：！？,.;:!?\s<]|$)/g, (full, n) => {
      const idx = parseInt(n, 10)
      if (idx >= 1 && idx <= sources.length) {
        return `<span class="cite" data-idx="${idx}" title="查看引用来源">[${n}]</span>`
      }
      return full
    })
  }
  return html
}

// 未命中口径文案（与主问答 missBarText 同口径）。
// app 分支后端强制 mode='kb'，正常只会出现 kb / kb_miss 两支；
// general_fallback 留着是因为历史数据与将来的口径变化都会走到这里。
function missBarText(m) {
  if (m.role !== 'assistant' || !m.content) return ''
  if (m.answer_kind === 'kb') return ''
  if (m.answer_kind === 'kb_miss') return '知识库中未找到相关内容'
  if (m.answer_kind === 'general_fallback') return '知识库中未找到相关内容，以下为通用回答'
  // 旧数据（answer_kind 空串）：按 kb_hit 回落。
  // ⚠️ 用 === false 而不是 !m.kb_hit：老消息可能根本没这个字段，
  //    !undefined 会给全部历史回答挂上提示条。
  return m.kb_hit === false ? '知识库中未找到相关内容，以下为通用回答' : ''
}

function refLabel(t) {
  return { note: '笔记', summary: '摘要', tool_read: '已读' }[t] || '原文'
}

// 展开/收起引用来源
function toggleSources(m) {
  m._showSources = !m._showSources
}

// 引用来源气泡（点正文角标弹出，显示 snippet，可跳原文）
const citePop = reactive({ show: false, x: 0, y: 0, source: null })

function onMdClick(m, e) {
  const cite = e.target.closest('.cite')
  if (!cite) { citePop.show = false; return }
  const idx = parseInt(cite.dataset.idx, 10)
  const source = (m.sources || [])[idx - 1]
  if (!source) return
  // 展开引用列表并高亮这一条：气泡是临时的，关掉后还要能在列表里找回它
  m._focusSource = idx
  m._showSources = true
  const rect = cite.getBoundingClientRect()
  citePop.source = source
  citePop.x = Math.min(rect.left + rect.width / 2, window.innerWidth - 320)
  // 优先在角标下方弹出；底部空间不足时向上，避免超出视口
  const popH = 300
  const spaceBelow = window.innerHeight - rect.bottom - 12
  citePop.y = spaceBelow > popH ? rect.bottom + 6 : Math.max(10, rect.top - popH - 6)
  citePop.show = true
}

function jumpCite() {
  if (citePop.source) goSource(citePop.source)
  citePop.show = false
}

function closeCitePop(e) {
  if (e.target.closest('.cite-pop') || e.target.closest('.cite')) return
  citePop.show = false
}

// ESC 收会话抽屉（A1）：遮罩式抽屉的常规预期，与升级仪式/揭炉仪式的 ESC 口径一致
function onSessKey(e) { if (e.key === 'Escape') sessOpen.value = false }

// 抽屉左滑收起（触屏）：横向位移 < -60px 判定为「收」，竖向滚动不误触
let touchX = null
let touchY = null
function onDrawerTouchStart(e) {
  touchX = e.touches[0].clientX
  touchY = e.touches[0].clientY
}
function onDrawerTouchEnd(e) {
  if (touchX === null) return
  const dx = e.changedTouches[0].clientX - touchX
  const dy = e.changedTouches[0].clientY - touchY
  touchX = null; touchY = null
  if (dx < -60 && Math.abs(dx) > Math.abs(dy) * 1.5) sessOpen.value = false
}

function renderReasoning(m) {
  return renderMd(md, m._reasoning || m.reasoning || '')
}

// 错误文案：剥掉后端「未配置模型」信号前缀（前端自己给「去配置」入口，不再露信号串）；
// 用户主动停止单独说成「已停止」，否则会被读成生成失败。
function errText(m) {
  if (m.error === '用户中断生成') return '已停止'
  const e = (m.error || '').replace('[NEED_SETUP]', '').replace(/^\d{3}:\s*/, '').trim()
  return e || '生成失败'
}

// 滚动位置状态：控制回顶/回底按钮显隐，同时是流式跟随的闸门
const atTop = ref(true)
const atBottom = ref(true)   // 初始视为在底部：新会话/未滚动时流式应跟随

// 仅当用户停在底部时才跟随（原来每个 token 强制回底 → 上翻读旧回答会被不断拽回）；
// force=true 用于发送、打开会话这类必须回底的场景
function scrollBottom(force = false) {
  if (!force && !atBottom.value) return
  if (force) atBottom.value = true
  nextTick(() => {
    const el = msgArea.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function scrollToTop() {
  const el = msgArea.value
  if (el) el.scrollTop = 0
}

function onMsgScroll() {
  const el = msgArea.value
  if (!el) return
  atTop.value = el.scrollTop < 40
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

function setMood(m, backMs = 0) {
  mood.value = m
  clearTimeout(moodTimer)
  if (backMs) moodTimer = setTimeout(() => { mood.value = 'normal' }, backMs)
}

// ---- 闲置主动搭话（P1）：页面可见 + 已有对话 + 空闲 45s + 每会话页内最多 1 次 ----
// 打扰度红线：生成中不搭话、页面隐藏不搭话、输入即重置、空会话不搭话（欢迎块已承担开场）。
// 搭话消息 _idle 标记、不落库——刷新即消失，不污染历史。
// ?idle_ms= 仅供探针/调试覆写倒计时（生产不带此参数，恒 45s）。
const IDLE_MS = Number(new URLSearchParams(location.search).get('idle_ms')) || 45000
let idleTimer = null
const nudgedSessions = new Set()

function clearIdle() { clearTimeout(idleTimer); idleTimer = null }

function armIdle() {
  clearIdle()
  if (!app.value || asking.value) return
  const key = currentId.value || '__new__'
  if (nudgedSessions.has(key)) return
  idleTimer = setTimeout(fireNudge, IDLE_MS)
}

async function fireNudge() {
  idleTimer = null
  if (asking.value || document.hidden || !messages.value.length) return
  const key = currentId.value || '__new__'
  if (nudgedSessions.has(key)) return
  nudgedSessions.add(key)
  try {
    const { data } = await appsApi.nudge(appId, currentId.value)
    if (!data?.text || asking.value || document.hidden) return
    messages.value.push(reactive({
      _tmp: 'n' + Date.now(), role: 'assistant', content: data.text,
      kb_hit: true, answer_kind: '', sources: [],
      suggestions: data.questions || [], _streaming: false, error: '', _idle: true,
    }))
    setMood('happy', 2600)
    scrollBottom(true)
  } catch { /* 静默：搭话是锦上添花，失败当没发生 */ }
}

async function loadApp() {
  const { data } = await appsApi.get(appId)
  app.value = data
  checkBondUp(data)            // B1：跨阶段则播升级仪式（首次进页只记录档位）
}
async function loadSessions() {
  const { data } = await chatApi.sessions(appId)
  sessions.value = data
}
async function openSession(id) {
  sessOpen.value = false       // A1：点任何会话行（含当前会话）都先收抽屉——
  // 原来放在提前 return 之后：点当前会话行什么都不发生、抽屉还开着（2026-10-31 实测抓到）
  if (id === currentId.value) return
  stopAsk()
  currentId.value = id
  const { data } = await chatApi.messages(id)
  // 打开会话时补前端态字段：思维链默认收起；库里若有「未配置模型」原文，恢复「去配置」入口
  messages.value = data.map(m => ({
    ...m, _streaming: false, _reasoning: '', _showReasoning: false,
    _needSetup: String(m.error || '').includes('[NEED_SETUP]'),
    _editing: false, _editText: '', _copied: false,
    _savedNote: false, _savedDoc: false, _showSources: false, _focusSource: 0,
  }))
  scrollBottom(true)
  restoreChatDocs(id)   // 恢复「✓ 已存文档」按钮态（不 await：不影响正文显示）
  armIdle()
}
function newSession() {
  stopAsk()
  sessOpen.value = false       // A1：窄屏抽屉里点「新会话」，抽屉该收了
  currentId.value = null      // 不落库：首个提问才由后端建会话（沿用主问答契约）
  messages.value = []
  clearIdle()
  atTop.value = true
  atBottom.value = true
}

function startRename(s) { renamingId.value = s.id; renameText.value = s.title }
async function saveRename(s) {
  const title = renameText.value.trim()
  renamingId.value = null
  if (!title || title === s.title) return
  await chatApi.renameSession(s.id, title)
  s.title = title
  ElMessage.success('已重命名')
}
// 删除会话：本地先移除 + 4 秒内可撤销（超时才真正落库删除）
// 撤销只恢复列表，当前视图不回到那个会话 —— 与主问答同口径，避免「撤销」变成隐式跳转。
function removeSession(id) {
  const idx = sessions.value.findIndex(s => s.id === id)
  if (idx < 0) return
  const removed = sessions.value[idx]
  sessions.value.splice(idx, 1)
  if (currentId.value === id) newSession()

  let restored = false
  let notif = null
  const undo = () => {
    restored = true
    notif?.close()
    loadSessions()
    ElMessage.success('已恢复')
  }
  notif = ElNotification({
    title: '会话已删除',
    message: h('div', [
      h('span', { style: 'font-size:13px;color:#6e6e6e' }, `「${removed.title}」`),
      h('span', { onClick: undo, style: 'margin-left:8px;color:#7c5cfc;font-weight:600;cursor:pointer' }, '撤销'),
    ]),
    duration: 4000,
    onClose: () => {
      if (restored) return
      // 真删了才刷默契度（提前刷会显示删之前的数字）
      chatApi.removeSession(id).then(() => loadApp()).catch(() => {})
    },
  })
}

async function togglePin(s) {
  try {
    const { data } = await chatApi.pinSession(s.id, !s.pinned)
    s.pinned = data.pinned
    ElMessage.success(data.pinned ? '已置顶' : '已取消置顶')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  }
}

function ask(q) { question.value = q; send() }

const onEnterSend = enterToSend(send)

function send() {
  const q = question.value.trim()
  if (!q || asking.value) return
  question.value = ''
  doSend(q)
}

// 真正发问：重新生成 / 编辑重发 / 错误重试 全部走这里 ——
// 这四条入口如果各写一份，迟早有一份漏掉「停止时回读会话」「finally 里重新进入闲置」。
async function doSend(q) {
  if (!q || asking.value) return      // 兜底：连点两个入口时只发一次
  clearIdle()                  // 开口即取消搭话倒计时
  asking.value = true
  setMood('think')

  // 本问是否会新建会话：中止路径也要据此回读（原来声明在 try 内部，catch 里拿不到）
  const wasNewSession = !currentId.value
  const userMsg = { _tmp: 'u' + Date.now(), role: 'user', content: q }
  messages.value.push(userMsg)
  const aiMsg = reactive({ _tmp: 'a' + Date.now(), role: 'assistant', content: '', _reasoning: '', _showReasoning: false, _needSetup: false, kb_hit: true, answer_kind: '', sources: [], suggestions: [], _streaming: true, error: '' })
  messages.value.push(aiMsg)
  scrollBottom(true)

  try {
    abortCtrl = new AbortController()
    // 走共享 utils/sse.js：这段原来是手写的 fetch + getReader 逐事件解析，与共享实现并存
    // = 同一份协议两处维护（加一个事件要改两遍，漏改的那边静默丢事件）。
    // 内置事件走回调，sources / reasoning / suggestions 走兜底 onEvent。
    await streamSSE('/api/chat/ask/stream', {
      question: q,
      session_id: currentId.value,
      app_id: appId,
      mode: 'kb',
      // 'default' / 'off' 是哨兵值，请求时映射回 null（见 chatModel 声明处的说明）
      model_id: chatModel.value && chatModel.value !== 'default' ? chatModel.value : null,
      reasoning_effort: effort.value && effort.value !== 'off' ? effort.value : null,
    }, (t) => {
      // token
      aiMsg.content += t
      scrollBottom()
    }, (payload) => {
      // done
      aiMsg.id = payload.message_id
      aiMsg._streaming = false
      // 情绪闭环：kb_miss → 挠头；命中 → 开心（2.6s 回常态）
      setMood(aiMsg.answer_kind === 'kb_miss' ? 'miss' : 'happy', 2600)
      // B1（2026-10-31 二轮）：每次回答后刷新默契度——原来只有「新会话首问/删会话」才刷，
      // 老会话内发问进度条不动、跨阶段仪式要等到下次进页。成长感的核心就是进度条在动。
      loadApp().catch(() => { /* 刷新失败只影响成长块显示，不挡对话 */ })
    }, (msg, payload) => {
      // error
      // 未配置模型：保留原始文案（含 [NEED_SETUP]），由 errText 剥前缀、模板按 _needSetup 给入口。
      // ⚠️ 必须传 inlineSetupGuide: true，否则共享实现会先弹全局「前往配置」提示并把信号串剥掉
      //    → 气泡里的入口消失、只剩一个全局 toast。
      aiMsg._needSetup = String(msg || '').includes('[NEED_SETUP]')
      aiMsg.error = msg || '生成失败'
      // 错误事件也可能带 message_id（半截回答已落库）：丢了它，重试/重新生成就截断不了
      if (payload && payload.message_id) aiMsg.id = payload.message_id
      aiMsg._streaming = false
      setMood('normal')
    }, null, null, abortCtrl.signal, null, {
      inlineSetupGuide: true,
      onEvent: (ev, payload) => {
        if (ev === 'sources') {
          // 会话是首问时后端才建的：currentId / 消息 id 全靠这个事件回填
          currentId.value = payload.session_id
          userMsg.id = payload.user_message_id
          aiMsg.kb_hit = payload.kb_hit
          aiMsg.answer_kind = payload.answer_kind || ''
          aiMsg.sources = payload.sources || []
        } else if (ev === 'reasoning') {
          // 思维链（P0）：后端照发，不消费 = 选「标准/深度」推理时只看到长时间空等
          aiMsg._reasoning += payload.t
          scrollBottom()
        } else if (ev === 'suggestions') {
          aiMsg.suggestions = payload.questions || []
          scrollBottom()
        }
      },
    })
    loadSessions()
    if (wasNewSession) loadApp()   // 新会话落库后刷新默契度
  } catch (e) {
    if (e && e.name === 'AbortError') {
      // 用户主动停止：后端已落半截回答 → 必须回读会话列与默契度。
      // ⚠️ try 尾部的 loadSessions/loadApp 在异常路径到不了（2026-10-29 修）：
      //    表现为点「停止」后侧栏少一条刚建的会话、「已聊 N 场」停在旧值。
      // P0：同时给气泡打上「已停止」标记 —— 否则半截回答看起来像彻底答完了。
      aiMsg._streaming = false
      aiMsg.error = '用户中断生成'
      loadSessions()
      if (wasNewSession) loadApp()
    }
    else {
      const msg = (e && e.message) || '请求失败'
      aiMsg._streaming = false
      aiMsg.error = msg
      aiMsg._needSetup = msg.includes('[NEED_SETUP]')
      // 失败回填问题（P0）：输入框在发送时已清空，不回填用户就得重打一遍。
      // 一个字都没出的空气泡直接移除，原因用全局提示说（与主问答同口径）。
      question.value = q
      if (!aiMsg.content) {
        messages.value = messages.value.filter(x => x !== aiMsg)
        ElMessage.error(errText(aiMsg))
      }
    }
    setMood('normal')
  } finally {
    asking.value = false
    abortCtrl = null
    armIdle()                  // 一轮结束后重新进入闲置观察
  }
}

function stopAsk() { abortCtrl && abortCtrl.abort() }

// ---------------- 消息三件套（P1 对齐主问答） ----------------
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text || '')
  } catch {
    // 非 https / 无剪贴板权限时的兜底（桌面外壳里 clipboard API 可能被拒）
    const ta = document.createElement('textarea')
    ta.value = text || ''
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
}
function flashCopied(m) {
  m._copied = true
  setTimeout(() => { m._copied = false }, 2000)
}
async function copyUserMsg(m) {
  await copyText(m.content || '')
  flashCopied(m)
}
async function copyAnswer(m) {
  await copyText(m.content || '')
  flashCopied(m)
}

function isLastAssistant(m) {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].role === 'assistant') return messages.value[i] === m
  }
  return false
}
function prevUserIndex(from) {
  for (let i = from - 1; i >= 0; i--) if (messages.value[i]?.role === 'user') return i
  return -1
}

// 截断「该用户消息及其之后」：后端删库 + 本地 splice 同一段。
// 两边少做一边 → 刷新后旧回答又回来了（或本地留着已经被删的消息 id）。
async function truncateAt(idx) {
  const m = messages.value[idx]
  if (!m) return false
  if (currentId.value && m.id) {
    try { await chatApi.truncateMessages(currentId.value, m.id) } catch { /* 忽略：本地照样重发 */ }
  }
  messages.value.splice(idx)
  return true
}

async function regenerate(m) {
  if (asking.value || !m || m.role !== 'assistant') return
  const idx = messages.value.indexOf(m)
  if (idx < 0) return
  const uIdx = prevUserIndex(idx)
  if (uIdx < 0) return
  const q = messages.value[uIdx].content
  if (!(await truncateAt(uIdx))) return
  doSend(q)
}

function startEdit(m) {
  if (asking.value) return
  m._editing = true
  m._editText = m.content
}
function cancelEdit(m) { m._editing = false }
async function confirmEdit(m) {
  const text = (m._editText || '').trim()
  if (!text || asking.value) return
  const idx = messages.value.indexOf(m)
  if (idx < 0) return
  m._editing = false
  if (!(await truncateAt(idx))) return
  doSend(text)
}

// 错误重试（P1 语义修正）：原来是「把最后一条用户问题再发一遍」——
// 旧错误回答留在历史里，重试几次就多几条空气泡。改成截断后重发，与主问答同口径。
async function retryLast(m) {
  if (asking.value) return
  const idx = m ? messages.value.indexOf(m) : messages.value.length
  const uIdx = prevUserIndex(idx < 0 ? messages.value.length : idx)
  if (uIdx < 0) return
  const q = messages.value[uIdx].content
  if (!(await truncateAt(uIdx))) return
  doSend(q)
}

// ---------------- 转存（P1） ----------------
// 转笔记：标题 = 该回答对应的最近一条提问，内容 = 回答正文（引用来源快照一并存档）。
// 后端按「标题 + 内容」幂等，重复点不会多出一条；回答被重新生成过内容变了则会新建。
async function saveNote(m) {
  if (asking.value || m._savedNote || m._savingNote) return
  const content = (m.content || '').trim()
  if (!content) return
  m._savingNote = true
  try {
    const sources = (m.sources || []).map(s => ({
      title: s.title, ref_type: s.ref_type, page_no: s.page_no,
      material_id: s.material_id, snippet: s.snippet,
    }))
    const { data } = await noteApi.fromChat({
      title: questionOf(m) || content.slice(0, 30),
      content,
      sources: sources.length ? sources : undefined,
    })
    m._savedNoteId = data.note?.id
    m._savedNote = true
    if (data.note?.reindex_warning) ElMessage.warning(data.note.reindex_warning)
    else if (data.created) ElMessage.success('已转存到知识库笔记')
    else ElMessage.info('这条回答此前已转过笔记')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '转笔记失败')
  } finally {
    m._savingNote = false
  }
}
// ---------------- 对话笔记弹窗（P1 对齐主问答） ----------------
// 原来直接 router.push('/knowledge?note=')：改一个字都要跳页，回来还得重新找会话。
const noteDlg = reactive({
  show: false, id: null, title: '', content: '', mode: 'edit',
  loading: false, saving: false, reviewing: false, recalling: false,
  createdAt: null, sourceMsg: null,
})
const notePreviewHtml = computed(() => renderMd(md, noteDlg.content || '', {}))

async function viewNote(m) {
  if (!m._savedNoteId) return
  noteDlg.sourceMsg = m
  noteDlg.id = m._savedNoteId
  noteDlg.show = true
  noteDlg.loading = true
  noteDlg.mode = 'edit'
  // 先清空再拉：上一份笔记的正文不能在加载期间残留（会看起来「打开错了」）
  noteDlg.title = ''
  noteDlg.content = ''
  noteDlg.createdAt = null
  try {
    const { data: n } = await http.get(`/notes/${m._savedNoteId}`)
    noteDlg.title = n.title || ''
    noteDlg.content = n.content || ''
    noteDlg.createdAt = n.created_at || null
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '笔记加载失败')
    noteDlg.show = false
  } finally {
    noteDlg.loading = false
  }
}

async function saveNoteDlg() {
  if (!noteDlg.id) return
  noteDlg.saving = true
  try {
    const { data } = await noteApi.update(noteDlg.id, {
      title: noteDlg.title.trim(), content: noteDlg.content.trim(),
    })
    noteDlg.show = false
    if (data.reindex_warning) ElMessage.warning(data.reindex_warning)
    else ElMessage.success('笔记已保存')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    noteDlg.saving = false
  }
}

async function deleteNoteDlg() {
  if (!noteDlg.id) return
  try {
    await noteApi.remove(noteDlg.id)
    const src = noteDlg.sourceMsg
    // 按钮必须复位，否则这条回答永远显示「✓ 已转存」，点开却是一个已删的 id
    if (src) { src._savedNote = false; src._savedNoteId = null }
    noteDlg.show = false
    ElMessage.success('笔记已删除')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  }
}

async function addNoteToReview() {
  if (!noteDlg.id) return
  noteDlg.reviewing = true
  try {
    const { data } = await reviewApi.createCard(noteDlg.id)
    ElMessage.success(data.created
      ? `已加入复习：「${(data.question || '').slice(0, 24)}…」`
      : '该笔记已在复习队列中')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '加入复习失败')
  } finally {
    noteDlg.reviewing = false
  }
}

async function addNoteToRecall() {
  if (!noteDlg.id) return
  noteDlg.recalling = true
  try {
    const { data } = await reviewApi.createRecallCard(noteDlg.id)
    ElMessage.success(data.created
      ? `已生成复述卡：「${(data.question || '').slice(0, 24)}…」`
      : '该笔记已有复述卡')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '生成复述卡失败')
  } finally {
    noteDlg.recalling = false
  }
}

// 转文档：写成一份 .md 材料 → 进材料库，后台解析后自动建知识库索引。
// 与「转笔记」的分工：笔记只能在知识库里看正文；文档能阅读 / 出讲义 / 出题 / 做视频。
async function saveDoc(m) {
  if (asking.value || m._savedDoc || m._savingDoc) return
  const content = (m.content || '').trim()
  if (!content) return
  m._savingDoc = true
  try {
    const { data } = await materialApi.createDocument({
      title: docTitleFor(m),
      content: content + docSourceAppendix(m),
      origin: 'chat',
      // 幂等键 = 会话 + 消息 id（后端按它认领）。中断的半截回答没有 id → 传空，
      // 退化为不幂等，但仍允许保存。
      origin_ref: (currentId.value && m.id) ? `chat:${currentId.value}:${m.id}` : '',
      origin_meta: {
        saved_from: 'chat',
        session_id: currentId.value || null,
        message_id: m.id || null,
        app_id: appId,                 // 可追溯到「从哪个丹灵嘴里聊出来的」
        question: questionOf(m).slice(0, 200),
      },
    })
    m._savedDocId = data.id
    m._savedDoc = true
    if (data.duplicated) ElMessage.info('这条回答此前已转过文档')
    else if (data.parsed_status === 'parsing') ElMessage.success('已存到材料库，正在后台解析并建索引')
    else ElMessage.success('已存到材料库，知识库也能检索到了')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '转文档失败')
  } finally {
    m._savingDoc = false
  }
}
function openSavedDoc(m) {
  if (m._savedDocId) router.push(`/editor/${m._savedDocId}`)
}

// 该回答对应的最近一条用户提问（转笔记 / 转文档共用口径）
function questionOf(m) {
  const idx = messages.value.indexOf(m)
  for (let j = idx - 1; j >= 0; j--) {
    const prev = messages.value[j]
    if (prev && prev.role === 'user') return (prev.content || '').trim()
  }
  return ''
}

// 文档标题（= 文件名 + 材料库里的名字）：优先回答里的一级/二级标题，其次你的提问，最后截正文
function docTitleFor(m) {
  const content = (m.content || '').trim()
  const head = content.split('\n').map(x => x.trim()).find(x => /^#{1,3}\s+\S/.test(x))
  let t = head ? head.replace(/^#{1,3}\s+/, '') : ''
  t = t.replace(/[*`>_]/g, '').trim()
  if (!t) t = questionOf(m).replace(/[*`>_#]/g, ' ').trim()
  if (!t) t = content.replace(/[*`>_#\n]/g, ' ').trim()
  return (t.slice(0, 60) || 'AI 回答')
}

// 文末附参考来源（有引用才加）：文档是长期资产，出处要跟着走
function docSourceAppendix(m) {
  const seen = new Set()
  const rows = []
  ;(m.sources || []).forEach(s => {
    if (!s || !s.title) return
    const k = s.title + '|' + (s.page_no || '')
    if (seen.has(k)) return
    seen.add(k)
    rows.push('- ' + s.title + (s.page_no ? '（第 ' + s.page_no + ' 页）' : ''))
  })
  if (!rows.length) return ''
  return '\n\n---\n\n> 参考来源（AI 生成本文时引用）\n>\n' + rows.map(r => '> ' + r).join('\n')
}

// 打开会话时恢复「✓ 已存文档」按钮态（不 await：不影响正文显示）
async function restoreChatDocs(sid) {
  if (!sid) return
  try {
    const { data } = await materialApi.chatDocs(sid)
    const map = {}
    ;(data.items || []).forEach(it => { map[it.message_id] = it })
    messages.value.forEach(m => {
      const hit = map[m.id]
      if (hit) { m._savedDoc = true; m._savedDocId = hit.material_id }
    })
  } catch { /* 恢复失败只影响按钮文案；再点也只会命中后端幂等 */ }
}

function goSource(s) {
  // ⚠️ 站内链接一律走 router（2026-10-29）：window.open 在桌面外壳里会跳出应用，
  //    与 md.js「只有外链才 target=_blank」的既有约定相反。
  if (!s.material_id) return
  const query = {}
  if (s.page_no) query.page = s.page_no
  // 带片段前 30 字给学习页做高亮定位（摘要类型不高亮，与主问答同口径）
  if (s.ref_type !== 'summary' && s.snippet) query.hl = s.snippet.slice(0, 30)
  router.push({ path: `/study/${s.material_id}`, query })
}

onMounted(async () => {
  try {
    await loadApp()
    await loadSessions()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '应用加载失败')
  }
  try {
    const { data } = await settingsApi.get()
    models.value = data.llm_models || []
    globalChatModelId.value = data.chat_model_id || ''
    configured.value = data.configured !== false
  } catch (e) { /* 模型清单拉取失败 → 隐藏工具行；configured 维持 true（发问时后端会拦） */ }
  // 点击角标 / 气泡以外区域关闭引用气泡
  document.addEventListener('mousedown', closeCitePop)
  window.addEventListener('keydown', onSessKey)
  // C3+（2026-10-31）：看板 miss 行「再问一次」直达 —— ?ask=问题 自动发送。
  // 发出后立刻清掉 query：刷新页面不能再发一次（与「推荐→动作已有内容不重复生成」同契约）。
  const askQ = String(route.query.ask || '').trim()
  if (askQ && configured.value && !messages.value.length) {
    question.value = askQ.slice(0, 2000)
    router.replace({ query: {} })
    send()
  }
})
onUnmounted(() => {
  stopAsk(); clearTimeout(moodTimer); clearIdle()
  clearInterval(blinkIv); clearTimeout(blinkOff)
  clearTimeout(bondUpTimer); clearTimeout(pokeTimer)
  window.removeEventListener('keydown', onBondupKey)
  window.removeEventListener('keydown', onSessKey)
  document.removeEventListener('mousedown', closeCitePop)
})
</script>

<style scoped>
.fc-page { display: grid; grid-template-columns: 260px 230px 1fr; height: 100%; min-height: 0; }
/* 舞台卡 */
.fc-stage { position: relative; overflow-y: auto; text-align: center; padding: 30px 16px; border-right: 1px solid var(--asc-border);
  background: linear-gradient(180deg, color-mix(in srgb, var(--app-body) 52%, #fff) 0%, #fff 46%); }
.stage-glow { width: 170px; height: 170px; border-radius: 50%; margin: 0 auto -140px;
  background: radial-gradient(circle, var(--app-body) 0%, transparent 68%); opacity: .8; }
.stage-spirit { height: 128px; display: flex; align-items: flex-end; justify-content: center; }
/* position:relative 不可省：.stage-glow 的 opacity<1 建层叠上下文，非定位 img 会被光晕压在底下（泛白） */
.stage-img { height: 128px; display: block; position: relative; }
.stage-name { font-size: 16px; font-weight: 600; margin-top: 10px; }
.stage-persona { font-size: 12px; color: var(--asc-text-2); margin: 6px 0 10px; line-height: 1.6; }
.bond-chip { display: inline-block; background: var(--asc-primary-soft); color: var(--asc-primary); font-size: 12px; border-radius: 999px; padding: 3px 12px; margin-bottom: 12px; }
.stage-mats { margin-bottom: 14px; }
.mat-sum { color: var(--app-edge, var(--asc-primary)); font-weight: 500; }
.mat-chip { display: inline-block; background: var(--asc-surface-2); border-radius: 6px; padding: 3px 10px; font-size: 12px; color: var(--asc-text-2); margin: 0 4px 6px; }
.spirit-breathe { animation: breathe 3.2s ease-in-out infinite; transform-origin: 50% 88%; }
@keyframes breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.035); } }
/* 会话列 */
.fc-sess { background: #edeef2; padding: 18px 12px; overflow-y: auto; }
.new-btn { width: 100%; margin-bottom: 12px; border-radius: 12px; height: 40px; font-weight: 500; }
.sess-search { margin-bottom: 6px; }
.sess-group-label { font-size: 11px; color: #666; padding: 14px 8px 5px; letter-spacing: .5px; font-weight: 500; }
.sess-item { position: relative; display: flex; align-items: flex-start; gap: 6px; padding: 9px 12px; border-radius: 10px; cursor: pointer; color: var(--asc-text-2); transition: background .16s ease; margin-bottom: 2px; }
.sess-item:hover { background: var(--asc-card); }
.sess-item.active { background: rgba(124, 92, 252, .12); }
.sess-item.active .sess-title { color: var(--asc-primary); font-weight: 600; }
.pin-icon { flex-shrink: 0; width: 18px; height: 18px; margin-top: 1px; border-radius: 5px; display: flex; align-items: center; justify-content: center; color: var(--asc-text-3); cursor: pointer; opacity: 0; transition: all .15s ease; }
.sess-item:hover .pin-icon, .pin-icon.on { opacity: 1; }
.pin-icon.on { color: var(--asc-primary); }
.pin-icon:hover { background: var(--asc-card); color: var(--asc-text-2); }
.sess-body { flex: 1; min-width: 0; }
.sess-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; color: var(--asc-text); font-weight: 500; line-height: 1.4; }
.sess-meta { font-size: 11px; color: #666; margin-top: 2px; }
.sess-preview { font-size: 11.5px; color: #666; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sess-del { visibility: hidden; flex-shrink: 0; }
.sess-item:hover .sess-del { visibility: visible; }
.sess-empty { font-size: 12px; color: #666; text-align: center; padding: 28px 0; }
/* 空态配置引导卡（未配置模型时替代建议问题） */
.setup-card { margin-top: 10px; padding: 12px 14px; border: 1px solid var(--asc-border);
  border-radius: 10px; background: var(--asc-surface-2, #f7f7f9); }
.setup-t { font-size: 13px; font-weight: 600; color: var(--asc-text, #262626); }
.setup-s { font-size: 12px; color: var(--asc-text-2, #6e6e6e); margin: 4px 0 10px; }
/* 消息流 */
/* min-height: 0 不能省：grid 项默认 min-height:auto，长对话会把整页撑到内容高度，
   结果页面整体滚动、消息区永远没有滚动条（回顶/回底按钮也就永远不出现）。 */
.fc-main { display: flex; flex-direction: column; min-width: 0; min-height: 0; background: var(--asc-bg); }
.fc-msgarea { flex: 1; min-height: 0; overflow-y: auto; padding: 26px 30px 18px; }
.fc-col { max-width: 760px; margin: 0 auto; }
/* 流式光标：贴在正文末尾闪烁 */
.cursor { color: var(--asc-primary); animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0; } }
.msg { display: flex; gap: 10px; margin: 14px 0; animation: msgIn .3s ease; }
.msg.user { flex-direction: row-reverse; }
@keyframes msgIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.avatar { width: 32px; height: 32px; border-radius: 50%; background: var(--asc-primary-soft); display: flex; align-items: center; justify-content: center; font-size: 13px; flex-shrink: 0; overflow: hidden; }
/* 位图全身像裁头部：顶对齐露脸，身体自然裁掉（头像位不需要全身） */
.avatar-img { width: 30px; display: block; align-self: flex-start; margin-top: 2px; }
.user-av { background: var(--asc-primary); color: #fff; font-size: 12px; }
.bubble { max-width: 78%; padding: 10px 14px; border-radius: 12px; line-height: 1.8; font-size: 14px; }
.msg.user .bubble { background: var(--asc-primary); color: #fff; border-top-right-radius: 4px; }
/* AI 回答去卡片化（对齐主问答 ChatView）：不套底色/边框，正文直接排版在页面上，
   未命中信号由气泡内 kb-miss-bar 承担（与主问答同一出口），不再染整泡米色 */
.msg.ai .bubble { flex: 1; min-width: 0; max-width: none; background: transparent; border: none; padding: 2px 0 0; }
/* ===== 对话笔记弹窗（与主问答同款视觉） ===== */
.cnd-head { display: flex; align-items: center; gap: 10px; }
.cnd-title { font-size: 17px; font-weight: 600; color: var(--asc-text); }
.cnd-tag {
  font-size: 11px; font-weight: 500; line-height: 1; padding: 4px 8px; border-radius: 6px;
  background: rgba(28, 145, 138, .12); color: #12837c; letter-spacing: .5px;
}
.cnd-sub { font-size: 12px; color: var(--asc-text-2); }
.cnd-field { margin-bottom: 14px; }
.cnd-label { display: block; font-size: 12.5px; color: var(--asc-text-2); margin-bottom: 6px; font-weight: 500; }
.cnd-field-head { display: flex; align-items: center; justify-content: space-between; }
.cnd-seg { display: inline-flex; border-radius: 8px; background: var(--asc-surface-2); padding: 2px; }
.cnd-seg-btn {
  border: none; background: transparent; cursor: pointer;
  font-size: 12px; color: var(--asc-text-2); padding: 3px 12px; border-radius: 6px;
  transition: all .15s ease; font-family: inherit;
}
.cnd-seg-btn.active { background: var(--asc-card); color: var(--asc-primary); font-weight: 600; box-shadow: 0 1px 3px rgba(0, 0, 0, .06); }
.cnd-textarea {
  width: 100%; border: 1px solid var(--asc-border); border-radius: 8px;
  padding: 10px 12px; font-size: 13.5px; line-height: 1.75; resize: vertical;
  font-family: inherit; color: var(--asc-text); background: var(--asc-card);
  transition: border-color .18s ease, box-shadow .18s ease; box-sizing: border-box;
}
.cnd-textarea:focus { outline: none; border-color: var(--asc-primary); box-shadow: 0 0 0 3px rgba(124, 92, 252, .10); }
.cnd-preview {
  border: 1px solid var(--asc-divider); border-radius: 8px; background: var(--asc-surface-2);
  padding: 10px 14px; max-height: 320px; overflow-y: auto;
}
.cnd-tools {
  display: flex; align-items: center; gap: 4px; padding-top: 8px;
  border-top: 1px solid var(--asc-divider);
}
.cnd-sep { flex: 1; }
.cnd-footer { display: flex; align-items: center; justify-content: space-between; }
.cnd-hint { font-size: 12px; color: var(--asc-text-2); }

/* 未命中提示条：与主问答同款（次要文字 + 浅底），在米色气泡里也不抢戏 */
.kb-miss-bar {
  font-size: 12px; color: var(--asc-text-2); background: var(--asc-surface-2);
  border-radius: 8px; padding: 6px 12px; margin-bottom: 10px;
}
.u-text { white-space: pre-wrap; word-break: break-word; }
/* 消息操作（P1 三件套）：hover 才出现，避免每条消息底部挂一排常驻按钮。
   2026-11-02：opacity:0 仍占位会在用户气泡底部留一块空白 → 不收不占地（max-height 0），
   悬停时才展开留白 */
.msg-ops { display: flex; gap: 10px; justify-content: flex-end; max-height: 0; margin-top: 0; opacity: 0; overflow: hidden; transition: opacity .15s ease, max-height .15s ease, margin-top .15s ease; }
.bubble:hover .msg-ops { opacity: 1; max-height: 24px; margin-top: 6px; }
.msg-op { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--asc-text-2); cursor: pointer; user-select: none; transition: color .15s ease; white-space: nowrap; }
.msg-op svg { flex: none; }
.msg-op:hover { color: var(--asc-primary); }
.msg-op.done { color: #67c23a; }
/* 用户气泡是主题紫底：默认灰字在上面读不出来，单独给近白 */
.msg.user .bubble .msg-op { color: rgba(255, 255, 255, .68); }
.msg.user .bubble .msg-op:hover { color: #fff; }
.edit-ops { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
.edit-btn { border: none; border-radius: 7px; padding: 5px 13px; font-size: 12.5px; font-family: inherit; cursor: pointer; transition: background .15s ease, color .15s ease; }
.edit-btn.ghost { background: transparent; color: rgba(255, 255, 255, .82); }
.edit-btn.ghost:hover { background: rgba(255, 255, 255, .16); color: #fff; }
.edit-btn.solid { background: #fff; color: var(--asc-primary); font-weight: 600; }
.edit-btn.solid:hover { background: rgba(255, 255, 255, .88); }
.edit-btn.solid:disabled { background: rgba(255, 255, 255, .3); color: rgba(124, 92, 252, .75); cursor: not-allowed; }
/* 编辑态给足宽度，否则 textarea 被气泡的 max-width 挤成一条 */
.bubble.editing { min-width: 340px; }
/* 回答底部操作行：虚线分隔，避免和下方的引用 chip / 推荐问题糊在一起 */
.answer-actions { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-top: 12px; }
/* 思维链（P0）：收起时只留一行标题 —— 用户能看出「它想过」，不必展开长文 */
.reasoning-box { margin: 0 0 8px; border: 1px solid var(--asc-border); border-radius: 8px; background: var(--asc-surface-2); }
.reasoning-toggle { display: flex; align-items: center; gap: 6px; padding: 6px 10px; font-size: 12px; color: var(--asc-text-2); cursor: pointer; user-select: none; }
.reasoning-toggle:hover { color: var(--asc-primary); }
.reasoning-label { font-weight: 500; }
.reasoning-thinking { color: var(--asc-text-2); opacity: .75; }
.reasoning-arrow { margin-left: auto; display: inline-block; transition: transform .16s ease; }
.reasoning-arrow.open { transform: rotate(180deg); }
.reasoning-body { padding: 8px 12px; font-size: 13px; line-height: 1.7; color: var(--asc-text-2); border-top: 1px solid var(--asc-border); max-height: 320px; overflow-y: auto; }
/* 生成中占位（P0）：没有它时，开「深度」推理只看到一个空气泡，像卡死 */
.thinking-line { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--asc-text-2); padding: 2px 0; }
.thinking-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--asc-text-3); animation: dotPulse 1.2s ease-in-out infinite; }
.thinking-dot:nth-child(2) { animation-delay: .16s; }
.thinking-dot:nth-child(3) { animation-delay: .32s; }
@keyframes dotPulse { 0%, 100% { opacity: .3; } 50% { opacity: 1; } }
/* 引用来源（P1）：折叠列表 —— snippet 直接摊开，不用悬停猜 */
.sources { margin-top: 12px; }
.sources-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--asc-text-2); cursor: pointer; user-select: none; padding: 3px 10px; border-radius: 999px; background: var(--asc-surface-2); transition: all .15s ease; }
.sources-toggle:hover { background: var(--asc-primary-soft); color: var(--asc-primary); }
.toggle-arrow { display: inline-block; font-size: 10px; transition: transform .16s ease; }
.toggle-arrow.open { transform: rotate(180deg); }
.source-list { margin-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.source-card { background: var(--asc-surface-2); border-radius: 8px; padding: 8px 10px; cursor: pointer; transition: box-shadow .15s ease; }
.source-card:hover { box-shadow: 0 3px 10px rgba(0, 0, 0, .07); }
/* 聚焦态：正文点 [2] 时对应的卡片描边，气泡关掉后仍能对上号 */
.source-card.focused { box-shadow: 0 0 0 1.5px var(--asc-primary), 0 4px 12px rgba(124, 92, 252, .15); }
.source-head { display: flex; gap: 6px; align-items: center; margin-bottom: 4px; }
.ref-tag { font-size: 11px; line-height: 17px; padding: 0 6px; border-radius: 4px; flex-shrink: 0; color: var(--asc-primary); background: var(--asc-primary-soft); }
.ref-tag.rt-note { color: #4e8a2a; background: #eef8e6; }
.ref-tag.rt-summary { color: #96662a; background: #fdf6ec; }
.source-title { font-size: 12px; font-weight: 500; color: var(--asc-text); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-page { font-size: 11px; color: var(--asc-primary); font-weight: 500; flex-shrink: 0; }
.source-snippet { font-size: 12px; color: var(--asc-text-2); line-height: 1.6; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
/* 正文角标（v-html 内容，必须 :deep） */
.md-body :deep(.cite) { display: inline-block; font-size: 11px; line-height: 15px; padding: 0 4px; margin: 0 1px; border-radius: 4px; color: var(--asc-primary); background: var(--asc-primary-soft); cursor: pointer; transition: all .15s ease; }
.md-body :deep(.cite:hover) { background: var(--asc-primary); color: #fff; }
/* 引用气泡 */
.cite-pop { position: fixed; z-index: 300; width: 300px; background: var(--asc-card); border-radius: 14px; padding: 12px 14px; box-shadow: 0 12px 32px rgba(0, 0, 0, .16); }
.cite-pop-head { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.cite-pop-title { font-size: 13px; font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cite-pop-page { font-size: 11px; color: var(--asc-primary); font-weight: 500; flex-shrink: 0; }
.cite-pop-snippet { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.7; max-height: 200px; overflow-y: auto; padding-right: 6px; scrollbar-width: thin; }
.cite-pop-foot { display: flex; align-items: center; gap: 5px; justify-content: flex-end; margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--asc-border); font-size: 12.5px; color: var(--asc-primary); font-weight: 500; cursor: pointer; }
.cite-pop-foot:hover { opacity: .8; }
.err-line { margin-top: 8px; font-size: 12px; color: #b91c1c; }
.retry { margin-left: 8px; color: var(--asc-primary); cursor: pointer; }
.q-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.q-chip { border: 1px solid var(--asc-border); background: var(--asc-surface-2); border-radius: 999px; padding: 5px 12px; font-size: 12.5px; color: var(--asc-text-2); cursor: pointer; transition: all .15s ease; }
.q-chip:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
/* 标签独占一行（flex-basis:100%），否则会挤成第一个 chip 的位置 */
.q-chips-label { flex-basis: 100%; font-size: 11px; color: var(--asc-text-2); margin-bottom: -2px; }
/* 输入区 */
.fc-input-area { position: relative; padding: 10px 30px 16px; }
/* 回到顶部 / 底部（悬浮于输入框上方） */
.scroll-btns { position: absolute; left: 50%; transform: translateX(-50%); bottom: calc(100% + 4px); z-index: 10; display: flex; gap: 10px; }
.scroll-btn { width: 32px; height: 32px; border-radius: 50%; background: var(--asc-card); display: flex; align-items: center; justify-content: center; color: var(--asc-text-2); cursor: pointer; box-shadow: 0 2px 10px rgba(0, 0, 0, .10); transition: all .18s ease; }
.scroll-btn:hover { color: var(--asc-primary); transform: translateY(-2px); }
.fc-input { max-width: 760px; margin: 0 auto; display: flex; gap: 10px; background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 12px; padding: 8px 10px; align-items: flex-end; }
.fc-input:focus-within { box-shadow: 0 0 0 1.5px var(--asc-primary) inset; }
.fc-input :deep(.el-input__wrapper) { box-shadow: none !important; background: transparent !important; }
/* 多行输入框内层：清掉 Element 自带边框/底色，字号与正文一致 */
.fc-input :deep(.el-textarea__inner) {
  box-shadow: none !important; background: transparent !important; border: none;
  padding: 6px 4px; font-size: 14px; line-height: 1.65; color: var(--asc-text);
  font-family: inherit; resize: none;
}
.fc-input :deep(.el-textarea__inner)::placeholder { color: var(--asc-text-2); }
.model-toolbar { max-width: 760px; margin: 6px auto 0; display: flex; gap: 4px; }
.chip-sel { width: 200px; }
/* ===== 默契度成长（B1）===== */
.bond-bar { width: 150px; height: 5px; border-radius: 999px; background: var(--asc-surface-2);
  margin: -6px auto 6px; overflow: hidden; }
.bond-bar i { display: block; height: 100%; border-radius: 999px;
  background: linear-gradient(90deg, var(--app-body), var(--app-edge)); transition: width .5s ease; }
.bond-next { font-size: 11px; color: var(--asc-text-2); margin-bottom: 10px; }
/* ===== 点击彩蛋（B2）：气泡浮在丹灵头顶 ===== */
.stage-spirit.pokeable { cursor: pointer; position: relative; }
.poke-bubble { position: absolute; left: 50%; top: -16px; transform: translateX(-50%); z-index: 5;
  white-space: nowrap; background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 999px; padding: 4px 12px; font-size: 12px; color: var(--asc-text);
  box-shadow: 0 4px 14px rgba(0, 0, 0, .1); }
.poke-fade-enter-active, .poke-fade-leave-active { transition: opacity .18s ease, transform .18s ease; }
.poke-fade-enter-from, .poke-fade-leave-to { opacity: 0; transform: translateX(-50%) translateY(4px); }
/* ===== 默契升级仪式（B1）：复用揭炉的光柱语言，点击即收 ===== */
.bondup-mask { position: fixed; inset: 0; z-index: 320; background: rgba(30, 27, 46, .42);
  display: flex; align-items: center; justify-content: center; cursor: pointer; animation: maskIn .25s ease; }
.bondup-box { position: relative; text-align: center; padding: 30px 44px 26px; cursor: default;
  background: radial-gradient(circle at 50% 30%, rgba(124, 92, 252, .14), transparent 62%), var(--asc-card);
  border: 1px solid rgba(124, 92, 252, .25); border-radius: 16px;
  box-shadow: 0 18px 50px rgba(30, 27, 46, .2); animation: ritualIn .35s cubic-bezier(.2, .8, .3, 1.1); }
.bondup-beam { position: absolute; left: 50%; bottom: 112px; width: 18px; height: 130px; margin-left: -9px;
  border-radius: 999px; transform-origin: 50% 100%; pointer-events: none;
  background: linear-gradient(180deg, transparent, rgba(255, 233, 168, .95) 45%, rgba(239, 159, 39, .9));
  animation: bondBeam 1s ease-out forwards; }
@keyframes bondBeam { 0% { transform: scaleY(.1); opacity: 0; } 25% { opacity: 1; }
  70% { transform: scaleY(1); opacity: .85; } 100% { transform: scaleY(1.04); opacity: 0; } }
.bondup-img { width: 84px; display: block; margin: 0 auto;
  animation: riseUpMini .7s .25s cubic-bezier(.2, .8, .3, 1.25) backwards; }
@keyframes riseUpMini { from { opacity: 0; transform: translateY(34px) scale(.7); } to { opacity: 1; transform: translateY(0) scale(1); } }
.bondup-text { font-size: 13px; letter-spacing: 4px; color: var(--asc-text-2); margin-top: 12px; }
.bondup-label { font-size: 24px; font-weight: 600; color: var(--asc-primary); letter-spacing: 3px;
  margin-top: 4px; animation: bondTextPop .5s .5s backwards; }
.bondup-sub { font-size: 12px; color: var(--asc-text-2); margin-top: 8px; }
@keyframes bondTextPop { 0% { opacity: 0; transform: scale(.7); } 70% { opacity: 1; transform: scale(1.08); } 100% { opacity: 1; transform: scale(1); } }
@keyframes maskIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes ritualIn { from { opacity: 0; transform: scale(.93) translateY(12px); } to { opacity: 1; transform: scale(1) translateY(0); } }
/* ===== 窄屏（A1）：舞台卡收走 → 顶栏接管；会话列 → 抽屉 ===== */
.fc-topbar { display: none; }
.sess-drawer-head { display: none; }
.sess-mask { display: none; }
@media (max-width: 1100px) {
  .fc-page { grid-template-columns: 1fr; }
  .fc-stage { display: none; }
  .fc-topbar { display: flex; align-items: center; gap: 8px; padding: 8px 14px;
    border-bottom: 1px solid var(--asc-border); background: var(--asc-card); }
  .tb-btn { border: 1px solid var(--asc-border); background: var(--asc-card); border-radius: 8px;
    width: 30px; height: 30px; display: inline-flex; align-items: center; justify-content: center;
    color: var(--asc-text-2); cursor: pointer; flex-shrink: 0; }
  .tb-btn:hover { color: var(--asc-primary); border-color: var(--asc-primary); }
  .tb-name { font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tb-bond { font-size: 11px; color: var(--asc-primary); background: var(--asc-primary-soft);
    border-radius: 999px; padding: 1px 8px; flex-shrink: 0; }
  /* 只有「配置」右推（auto 写在通用选择器上会让两个按钮平分剩余空间，配置悬在中间） */
  .fc-topbar .tb-cfg { margin-left: auto; }
  .fc-topbar .el-button + .el-button { margin-left: 0; }
  .fc-sess { position: fixed; left: 0; top: 0; bottom: 0; width: 282px; z-index: 332;
    transform: translateX(-104%); transition: transform .22s ease;
    box-shadow: 12px 0 32px rgba(30, 27, 46, .18); }
  .fc-sess.open { transform: none; }
  .sess-mask { display: block; position: fixed; inset: 0; z-index: 330; background: rgba(30, 27, 46, .4); }
  .sess-drawer-head { display: flex; align-items: center; justify-content: space-between;
    font-size: 13px; font-weight: 600; color: var(--asc-text-2); padding: 0 4px 10px; }
  .sess-drawer-x { border: none; background: transparent; font-size: 18px; color: var(--asc-text-2);
    cursor: pointer; padding: 0 6px; line-height: 1; }
  .sess-drawer-x:hover { color: var(--asc-text); }
  .fc-msgarea { padding: 18px 14px 12px; }
  .fc-input-area { padding: 10px 14px 12px; }
}
@media (prefers-reduced-motion: reduce) { .spirit-breathe, .msg, .cursor,
  .bondup-beam, .bondup-img, .bondup-label, .bondup-mask, .bondup-box { animation: none; }
  .poke-fade-enter-active, .poke-fade-leave-active { transition: none; } }
</style>
