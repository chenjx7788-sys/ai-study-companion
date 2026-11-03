<template>
  <div class="chat-page">
    <!-- 左：会话列表（E5） -->
    <aside class="session-col">
      <el-button type="primary" class="new-btn" @click="newSession">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
          stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
        新会话
      </el-button>
      <el-input v-model="sessionSearch" placeholder="搜索会话" clearable size="small"
        class="session-search" />

      <template v-for="group in groupedSessions" :key="group.label">
        <div class="session-group-label">{{ group.label }}</div>
        <div v-for="s in group.items" :key="s.id"
          class="session-item" :class="{ active: s.id === currentId }"
          @click="openSession(s.id)">
          <span class="pin-icon" :class="{ on: s.pinned }" title="置顶" @click.stop="togglePin(s)">
            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>
            </svg>
          </span>
          <div class="session-body">
            <el-input v-if="renamingId === s.id" v-model="renameText" size="small"
              @click.stop @keyup.enter="saveRename(s)" @blur="saveRename(s)" />
            <template v-else>
              <div class="session-title" :title="s.title + '（双击重命名）'"
                @dblclick.stop="startRename(s)">{{ s.title }}</div>
              <div class="session-meta">{{ s.message_count }} 问 · {{ relTime(s.updated_at) }}</div>
              <div v-if="s.last_message" class="session-preview">{{ s.last_message }}</div>
            </template>
          </div>
          <el-popconfirm title="删除该会话及其消息？" width="200"
            confirm-button-text="删除" confirm-button-type="danger" cancel-button-text="取消"
            @confirm="removeSession(s.id)">
            <template #reference>
              <el-button size="small" text type="danger" class="session-del" title="删除" @click.stop>
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                  stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                  <line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>
                </svg>
              </el-button>
            </template>
          </el-popconfirm>
        </div>
      </template>

      <div v-if="sessions.length === 0" class="session-empty">暂无会话</div>
      <div v-else-if="groupedSessions.length === 0" class="session-empty">无匹配会话</div>
    </aside>

    <!-- 右：对话区 -->
    <main class="chat-main">
      <div ref="msgAreaRef" class="msg-area" @scroll.passive="onMsgScroll">
        <div class="msg-col">
        <div v-if="messages.length === 0" class="chat-empty">
          <div class="mascot-hero">
            <img :src="mascot" class="empty-mascot" alt="知萤猫头鹰" />
          </div>
          <!-- 未配置模型：先引导配置（替代示例问题，避免点击直撞后端「请先配置」报错） -->
          <template v-if="!configured">
            <h1 class="greet-title">你好，我是伴伴</h1>
            <p class="greet-sub">开始前只差一步：配置一个 AI 模型<br>1 分钟搞定，多数厂商新用户送免费额度</p>
            <div class="setup-cta">
              <el-button type="primary" size="large" @click="router.push('/settings?add=1')">前往配置模型</el-button>
            </div>
            <p class="setup-note">配置后即可向知识库提问，回答会引用你的材料</p>
          </template>
          <!-- 已配置：正常空态 -->
          <template v-else>
            <h1 class="greet-title">你好，我是伴伴</h1>
            <p class="greet-sub">你的 AI 学习搭子 · 有问题，随便问，我来帮你答疑解惑</p>
            <div class="example-list">
              <div class="example-q" v-for="q in examples" :key="q" @click="askSuggestion(q)">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                  stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
                </svg>
                {{ q }}
              </div>
            </div>
          </template>
        </div>

        <template v-for="(m, i) in messages" :key="m.id ?? m._tmp">
          <!-- 用户消息 -->
          <div v-if="m.role === 'user'" class="msg-row right">
            <div class="bubble user">
              <template v-if="m._editing">
                <el-input v-model="m._editText" type="textarea" :rows="3" resize="none"
                  :autofocus="true" placeholder="修改问题后重新生成回答" />
                <div class="edit-ops">
                  <el-button size="small" text @click="cancelEdit(m)">取消</el-button>
                  <el-button size="small" type="primary" :disabled="!m._editText.trim()"
                    @click="confirmEdit(m, i)">保存并重新生成</el-button>
                </div>
              </template>
              <template v-else>
                <span class="user-text">{{ m.content }}</span>
                <div class="msg-ops">
                  <span class="msg-op" title="复制" @click="copyUserMsg(m)">复制</span>
                  <span class="msg-op" title="编辑" @click="startEdit(m)">编辑</span>
                </div>
              </template>
            </div>
          </div>
          <!-- AI 消息 -->
          <div v-else class="msg-row">
            <img :src="mascot" class="ai-avatar" :class="{ thinking: m._streaming && !m.content }" alt="" />
            <div class="bubble ai">
              <div v-if="m._streaming && !m.content && !(m._tools && m._tools.length)" class="thinking-row">
                <span class="thinking-dot"></span><span class="thinking-dot"></span><span class="thinking-dot"></span>
                <span class="thinking-text">伴伴正在思考…</span>
              </div>
              <!-- 思维链（可折叠） -->
              <div v-if="m._reasoning || m.reasoning" class="reasoning-box">
                <div class="reasoning-toggle" @click="toggleReasoning(m)">
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 5a3 3 0 0 0-3 3v3a3 3 0 0 0 3 3 3 3 0 0 1 3 3v3a3 3 0 0 1-3 3"/>
                    <path d="M12 5a3 3 0 0 1 3 3v3a3 3 0 0 1-3 3 3 3 0 0 0-3 3v3a3 3 0 0 0 3 3"/>
                  </svg>
                  <span class="reasoning-label">思考过程</span>
                  <span v-if="m._streaming && !m.content" class="reasoning-thinking">思考中…</span>
                  <svg class="reasoning-arrow" :class="{ open: m._showReasoning }" viewBox="0 0 24 24"
                    width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2"
                    stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
                </div>
                <div v-if="m._showReasoning" class="reasoning-body md-body"
                  v-html="renderMd(m._reasoning || m.reasoning, m._streaming)"></div>
              </div>
              <!-- 工具步骤（P4）：模型调用了技能授权的工具。这是**过程**记录，不落库 ——
                   刷新后消失。理由：写进正文会污染回答，加库字段要迁移，两者都不划算。 -->
              <div v-if="m._tools && m._tools.length" class="tool-steps">
                <div v-for="(s, i) in m._tools" :key="i" class="tool-step"
                  :class="{ running: !s.done, failed: s.done && !s.ok }">
                  <span class="tool-step-ic">
                    <svg v-if="!s.done" class="tool-spin" viewBox="0 0 24 24" width="12" height="12"
                      fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
                      <path d="M12 3a9 9 0 1 0 9 9" />
                    </svg>
                    <svg v-else-if="s.ok" viewBox="0 0 24 24" width="12" height="12" fill="none"
                      stroke="currentColor" stroke-width="2.6" stroke-linecap="round"
                      stroke-linejoin="round"><path d="M4 12.5l5 5L20 6" /></svg>
                    <svg v-else viewBox="0 0 24 24" width="12" height="12" fill="none"
                      stroke="currentColor" stroke-width="2.6" stroke-linecap="round">
                      <path d="M6 6l12 12M18 6L6 18" /></svg>
                  </span>
                  <span class="tool-step-lb">{{ s.label }}</span>
                  <!-- 读了哪份 / 检索了什么（P1-①）。原来只有「读材料原文」四个字，
                       用户不知道读的是哪一份，得再问一遍。 -->
                  <span v-if="s.brief" class="tool-step-bf" :title="s.brief">{{ s.brief }}</span>
                  <!-- 耗时 / 复用（P3-②）：完成后才出现；复用的步骤没真跑，不显示耗时 -->
                  <span v-if="s.done && s.reused" class="tool-step-ms">复用</span>
                  <span v-else-if="s.done && s.ms != null" class="tool-step-ms">
                    {{ s.ms >= 1000 ? (s.ms / 1000).toFixed(1) + 's' : s.ms + 'ms' }}</span>
                  <!-- 撤销（仅 save_note 成功且有 note_id）：一键删掉这条刚写的笔记。
                       服务端会校验：非 chat_note / 用户已编辑过 → 409，前端如实提示 -->
                  <button v-if="s.name === 'save_note' && s.done && s.ok && s.note_id && !s.undone"
                    class="tool-undo" @click.stop="undoChatNote(s)">撤销</button>
                  <span v-if="s.undone" class="tool-step-ms">已撤销</span>
                </div>
              </div>
              <!-- 口径提示条（三档问答）：general=中性徽章；general_fallback/kb_miss=灰条；旧数据按 kb_hit 回落 -->
              <div v-if="missBarText(m)" class="kb-miss-bar" :class="{ neutral: m.answer_kind === 'general' }">
                {{ missBarText(m) }}
              </div>
              <!-- 生成中断/失败（含刷新后从库恢复的错误消息）：气泡内错误态 + 重试 -->
              <div v-if="m.error" class="err-bar" :class="{ stopped: m.error === '用户中断生成' }">
                <span>{{ m.error === '用户中断生成' ? '已停止' : ('⚠ 生成中断：' + friendlyErr(m.error)) }}</span>
                <button v-if="!asking" class="err-retry" @click="retryError(m)">重试</button>
              </div>
              <!-- 未配置模型：气泡内错误态（重试 + 去配置） -->
              <div v-if="m._error" class="ai-config-error">
                <div class="ai-config-error-text">{{ m._error }}</div>
                <div class="ai-config-error-ops">
                  <el-button size="small" type="primary" plain @click="retryQuestion(m)">重试</el-button>
                  <el-button size="small" type="primary" @click="router.push('/settings')">去配置</el-button>
                </div>
              </div>
              <div v-else class="md-body" v-html="renderAnswer(m)" @click="onMdClick(m, $event)"></div>
              <span v-if="m._streaming" class="cursor">▍</span>
              <!-- 库内相关资源（找内容）：正文之后单独成区，点开即读 / 看 / 听 -->
              <div v-if="!m._streaming && m.resources?.length" class="res-block">
                <div class="res-label">
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
                    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
                  </svg>
                  资料库里找到 {{ m.resources.length }} 项
                </div>
                <div class="res-list">
                  <div v-for="(r, ri) in m.resources" :key="ri" class="res-card"
                    :class="'k-' + r.kind" @click="openResource(r)">
                    <span class="res-badge">{{ r.badge }}</span>
                    <span class="res-title">{{ r.title }}</span>
                    <span v-if="r.meta" class="res-meta">{{ r.meta }}</span>
                    <!-- 闭环：材料卡直接给动作，省得跳过去再自己找按钮（@click.stop：点按钮不触发整卡跳转） -->
                    <span v-if="r.kind === 'material'" class="res-acts" @click.stop>
                      <button type="button" class="res-act" title="打开讲义并开始生成"
                        @click="openResource(r, 'simplelearn')">讲义</button>
                      <button type="button" class="res-act" title="打开测一测并开始出题"
                        @click="openResource(r, 'quiz')">出题</button>
                    </span>
                    <svg class="res-arrow" viewBox="0 0 24 24" width="13" height="13" fill="none"
                      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M9 18l6-6-6-6"/>
                    </svg>
                  </div>
                </div>
              </div>
              <!-- 库外推荐：正文里只有书名、没有可点的东西 → 给一条真动作，
                   把这个主题变成材料库里的导读（可读 / 出讲义 / 出题），推荐才落到地面上 -->
              <div v-if="!m._streaming && m.answer_kind === 'discover_miss'" class="brief-cta">
                <div class="brief-cta-body">
                  <div class="brief-cta-title">就这个主题，先来一份入门导读</div>
                  <div class="brief-cta-sub">
                    AI 会写一份「大致讲什么 · 怎么入门 · 常见卡点」的导读存进材料库，之后能读、能出讲义、能出题
                    <span class="brief-cta-note">（AI 生成 · 非原书内容）</span>
                  </div>
                </div>
                <el-button size="small" type="primary" plain :loading="!!m._briefBusy"
                  @click="createBrief(m, i)">
                  {{ m._briefBusy ? '正在生成…' : '生成导读' }}
                </el-button>
              </div>
              <!-- 相关问题推荐（回答后生成，点击直接发问） -->
              <div v-if="!m._streaming && m.suggestions?.length" class="suggestions">
                <div class="suggestions-label">继续问</div>
                <div v-for="(q, i) in m.suggestions" :key="i" class="suggestion-chip"
                  @click="askSuggestion(q)">{{ q }}</div>
              </div>
              <!-- 引用来源（折叠为标识，点击展开）。
                   ⚠️ !m._streaming：sources 事件在 token 之前就下发，不加闸的话回答还在吐字、
                   引用区就挂在气泡下面（炼丹炉 2026-10-30 已修同款「显示太提前」）。
                   流式期间点正文 [n] 角标不受影响——弹气泡走 citePop，不依赖这块 DOM。 -->
              <div v-if="!m._streaming && m.sources?.length" class="sources">
                <div class="sources-toggle" @click="toggleSources(m)">
                  <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
                    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
                  </svg>
                  引用 {{ m.sources.length }} 条
                  <svg class="toggle-arrow" :class="{ open: m._showSources }" viewBox="0 0 24 24" width="12" height="12"
                    fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M6 9l6 6 6-6"/>
                  </svg>
                </div>
                <div v-if="m._showSources" class="source-list">
                  <div v-for="(s, i) in m.sources" :key="i" class="source-card"
                    :class="{ focused: i + 1 === m._focusSource }" @click="jumpToSource(s)">
                    <div class="source-head">
                      <el-tag size="small" effect="plain"
                        :type="s.ref_type === 'note' ? 'success' : (s.ref_type === 'summary' ? 'warning' : 'primary')">
                        {{ { note: '笔记', summary: '摘要', tool_read: '已读' }[s.ref_type] || '原文' }}
                      </el-tag>
                      <span class="source-title">{{ s.title }}</span>
                      <span v-if="s.page_no" class="source-page">P{{ s.page_no }}</span>
                    </div>
                    <div class="source-snippet">{{ s.snippet }}…</div>
                  </div>
                </div>
              </div>
              <!-- 回答底部操作：重新生成（仅最后一条）+ 转笔记 + 转文档 + 复制 -->
              <div v-if="!m._streaming && m.content" class="answer-actions">
                <span v-if="!m.error && isLastAssistant(m)" class="msg-op"
                  title="用同一个问题重新生成（旧回答不保留）" @click="regenerate(m)">
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>
                  </svg>
                  重新生成
                </span>
                <span class="msg-op" :class="{ done: m._savedNote }"
                  :title="m._savedNote ? '在知识库中查看 / 编辑该笔记' : '标题 = 你的问题 · 内容 = 本回答'"
                  @click="m._savedNote ? viewChatNote(m) : saveAnswerToNote(m, i)">
                  <svg v-if="!m._savedNote" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>
                  </svg>
                  <svg v-else viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>
                  </svg>
                  {{ m._savedNote ? '✓ 已转存 · 查看' : '转笔记' }}
                </span>
                <span class="msg-op" :class="{ done: m._savedDoc }"
                  :title="m._savedDoc ? '在编辑器里打开这份文档（标题可改）'
                    : '把本回答存成一份文档：进材料库，可阅读 / 出讲义 / 出题 / 做视频，知识库也能检索'"
                  @click="m._savedDoc ? openSavedDoc(m) : saveAnswerToDoc(m, i)">
                  <svg v-if="!m._savedDoc" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/>
                  </svg>
                  <svg v-else viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>
                  </svg>
                  {{ m._savedDoc ? '✓ 已存文档 · 查看' : '转文档' }}
                </span>
                <span class="msg-op" :class="{ done: m._copied }" @click="copyAnswer(m)">
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                  </svg>
                  {{ m._copied ? '✓ 已复制' : '复制回答' }}
                </span>
              </div>
            </div>
          </div>
        </template>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="input-bar">
        <!-- 回到顶部 / 底部（悬浮于输入框上方） -->
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
        <div class="input-inner">
        <div class="input-col">
          <div class="ask-mode-row">
            <!-- 回答口径三档（2026-10-25）：自动=先查库、未命中兜底通用；知识库=只按库答、未命中如实说；通用=不查库 -->
            <el-radio-group v-model="askMode" size="small"
              title="回答口径：自动 = 先查知识库，未命中用通用回答兜底；知识库 = 只按库内内容回答，未找到会如实告知；通用 = 不查知识库，大模型直接回答">
              <el-radio-button value="auto">自动</el-radio-button>
              <el-radio-button value="kb">知识库</el-radio-button>
              <el-radio-button value="general">通用</el-radio-button>
            </el-radio-group>
            <!-- 检索范围（F1 抽出的组件）；通用档不查库 → 禁用 -->
            <div class="scope-wrap" :class="{ off: askMode === 'general' }">
              <ScopePicker
                v-model:scope="scope"
                v-model:material-ids="selectedMaterialIds"
                v-model:note-ids="selectedNoteIds"
                v-model:folder-ids="selectedFolderIds"
                :material-options="materialOptions"
                :note-options="noteOptions"
                :folder-options="folderOptions" />
            </div>
          </div>
          <!-- P1 建议卡 / P3 备选：发送之前给出，不弹模态、不阻塞输入、不重复追问 -->
          <div v-if="suggest" class="sk-sugg">
            <div class="sk-sugg-hd">
              <span class="sk-sugg-ic">{{ iconOf(suggest.id) }}</span>
              <span class="sk-sugg-nm">{{ suggest.name }}</span>
              <!-- 来源如实标注：命中关键词 vs 模型判断，可信度不是一回事 -->
              <span class="sk-sugg-tag" :class="{ ai: suggest.via === 'semantic' }">
                {{ suggest.via === 'semantic' ? 'AI 判断' : '建议使用' }}
              </span>
              <span v-if="suggest.ambiguous" class="sk-sugg-amb">多个技能都沾边</span>
              <span class="sk-sugg-x" title="关闭建议" @click="cancelSuggest">×</span>
            </div>
            <div class="sk-sugg-why">{{ suggest.why }} —— 它会按一套固定方法回答这个问题，比通用回答更贴近这件事该怎么做。</div>
            <!-- 备选：规则一次命中多个时，不假装很确定，让用户直接换 -->
            <div v-if="suggest.alternatives && suggest.alternatives.length" class="sk-sugg-alts">
              <span class="sk-sugg-alts-lb">换个技能：</span>
              <span v-for="a in suggest.alternatives" :key="a.id" class="sk-sugg-alt"
                    :title="a.why" @click="switchSuggest(a)">{{ a.name }}</span>
            </div>
            <div class="sk-sugg-ft">
              <el-button size="small" type="primary" @click="useSkill">用它回答</el-button>
              <el-button size="small" @click="skipSkill">直接回答</el-button>
              <span class="sk-sugg-off" @click="turnOffSuggest">本会话不再提示</span>
            </div>
          </div>
          <!-- 语义判断中：延迟 250ms 才出现（规则命中 <5ms，不该闪一下） -->
          <div v-if="matching" class="sk-matching">正在看这问题适合用哪个技能…</div>

          <!-- P1 已加载技能：让用户知道本轮回答被哪个方法影响，可随时移除 -->
          <div v-if="mountedSkill" class="skill-bar">
            <span class="skill-bar-ic">{{ iconOf(mountedSkill.id) }}</span>
            <span class="skill-bar-tx">已加载技能 <b>{{ mountedSkill.name }}</b></span>
            <span class="skill-bar-hint">· 本轮及后续提问按这个方法回答</span>
            <span class="skill-bar-x" title="移除技能" @click="unmountSkill">移除</span>
          </div>
          <div class="composer-row">
            <el-input ref="inputRef" v-model="question" type="textarea" :rows="2" resize="none"
              :placeholder="scopePlaceholder" @keydown.enter.exact="onEnterSend" />
            <el-button v-if="asking" type="danger" class="send-btn" circle plain
              title="停止生成" @click="stopAsk">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </el-button>
            <el-button v-else type="primary" class="send-btn" circle :disabled="!question.trim()"
              title="发送（Enter）" @click="send">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M22 2 11 13" />
                <path d="M22 2 15 22 11 13 2 9z" />
              </svg>
            </el-button>
          </div>
          <!-- 模型工具栏：推理强度 + 模型切换（左下角） -->
          <div class="model-toolbar">
            <!-- P1 手动挂载：跳过模型判断，用户强制指定一个方法 -->
            <el-popover v-model:visible="skillPickerOpen" placement="top-start" :width="286" trigger="click">
              <template #reference>
                <button type="button" class="skill-add" :class="{ on: !!mountedSkill }">
                  ＋ 技能<template v-if="mountedSkill"> · {{ mountedSkill.name }}</template>
                </button>
              </template>
              <div class="sk-pick">
                <div class="sk-pick-hd">选一个方法，本轮及后续提问都按它回答</div>
                <div v-for="s in chatSkills" :key="s.id" class="sk-pick-row"
                  :class="{ on: mountedSkill && mountedSkill.id === s.id }" @click="mountSkill(s)">
                  <span class="sk-pick-ic">{{ iconOf(s.id) }}</span>
                  <span class="sk-pick-nm">{{ s.name }}</span>
                  <span class="sk-pick-ds">{{ s.description }}</span>
                </div>
                <div v-if="!chatSkills.length" class="sk-pick-empty">暂无可用技能</div>
              </div>
            </el-popover>
            <el-select v-model="reasoningEffort" size="small"
              class="reason-select" title="推理模式（默认快速）">
              <!-- EP isEmptyValue 视 '' 为空 → value="" 的选项永远选不中，显示回落成 placeholder。
                 哨兵 off 在发请求时映射回 null（reasoning_effort 的「关闭」语义）。 -->
              <el-option label="关闭" value="off" />
              <el-option label="快速" value="low" />
              <el-option label="标准" value="high" />
              <el-option label="深度" value="max" />
            </el-select>
            <el-select v-if="chatModels.length" v-model="selectedModelId" size="small"
              class="model-select" title="切换模型">
              <!-- 不选具体模型 = 跟随管理中心的对话模型；哨兵 default 在载荷里映射回 null -->
              <el-option label="跟随对话模型（管理中心）" value="default" />
              <el-option v-for="m in chatModels" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
            </el-select>
          </div>
        </div>
        </div>
      </div>
    </main>

    <!-- 对话笔记查看/编辑弹窗（当前页打开，不跳知识库） -->
    <el-dialog v-model="noteDlg.show" width="640px" class="chat-note-dialog"
      :close-on-click-modal="false">
      <template #header>
        <div class="cnd-head">
          <span class="cnd-title">对话笔记</span>
          <span class="cnd-tag">对话笔记</span>
          <span class="cnd-sub">来自 AI 对话<template v-if="noteDlg.createdAt"> · {{ (noteDlg.createdAt || '').slice(0, 10) }}</template></span>
        </div>
      </template>

      <div v-loading="noteDlg.loading">
        <div class="cnd-field">
          <label class="cnd-label" for="cnd-title">标题（原问题）</label>
          <el-input id="cnd-title" v-model="noteDlg.title" placeholder="给这条笔记起个标题" maxlength="255" />
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
            placeholder="支持 Markdown" :disabled="noteDlg.loading"
            @mouseup="onNoteDlgSelect" @keyup="onNoteDlgSelect" @blur="hideChatSel"></textarea>
          <div v-else class="md-body cnd-preview" v-html="renderMd(noteDlg.content)"></div>
          <!-- 划线加工工具条：编辑态选中文字后浮出 -->
          <div v-if="chatSel.show && noteDlg.mode === 'edit'" class="cnd-sel-bar">
            <span class="cnd-sel-count">已选中 {{ chatSel.text.length }} 字</span>
            <span class="cnd-sel-sep" aria-hidden="true"></span>
            <button type="button" class="cnd-sel-btn" @mousedown.prevent @click="chatTransform('rewrite', chatSel)">改写</button>
            <button type="button" class="cnd-sel-btn" @mousedown.prevent @click="chatTransform('expand', chatSel)">扩写</button>
            <button type="button" class="cnd-sel-btn" @mousedown.prevent @click="chatTransform('continue', chatSel)">续写</button>
            <button type="button" class="cnd-sel-btn" @mousedown.prevent @click="chatTransform('summarize', chatSel)">总结</button>
            <span class="cnd-sel-sep" aria-hidden="true"></span>
            <button type="button" class="cnd-sel-btn plain" @mousedown.prevent @click="hideChatSel">取消</button>
          </div>
        </div>
      </div>

      <!-- AI 加工整条笔记 -->
      <div v-if="noteDlg.content.trim()" class="cnd-ai">
        <span class="cnd-ai-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor">
            <path d="M12 2l1.7 5.3L19 9l-5.3 1.7L12 16l-1.7-5.3L5 9l5.3-1.7L12 2z"/>
            <path d="M19 14l.9 2.6 2.6.9-2.6.9L19 21l-.9-2.6-2.6-.9 2.6-.9L19 14z"/>
          </svg>
        </span>
        <span class="cnd-ai-label">AI 加工</span>
        <span class="cnd-ai-sep" aria-hidden="true"></span>
        <el-tooltip content="换个说法，保持原意与事实" placement="top" :show-after="250">
          <el-button text type="primary" size="small" @click="chatTransform('rewrite')">改写</el-button>
        </el-tooltip>
        <el-tooltip content="补充细节、例子与解释，让笔记更充实" placement="top" :show-after="250">
          <el-button text type="primary" size="small" @click="chatTransform('expand')">扩写</el-button>
        </el-tooltip>
        <el-tooltip content="接着已有内容往下续写，补全或延伸思路" placement="top" :show-after="250">
          <el-button text type="primary" size="small" @click="chatTransform('continue')">续写</el-button>
        </el-tooltip>
        <el-tooltip content="压缩成精炼要点，突出核心结论" placement="top" :show-after="250">
          <el-button text type="primary" size="small" @click="chatTransform('summarize')">总结</el-button>
        </el-tooltip>
      </div>

      <div class="cnd-tools">
        <el-button text type="warning" size="small" :loading="noteDlg.reviewing" @click="addNoteDlgToReview">加入复习</el-button>
        <el-button text type="primary" size="small" :loading="noteDlg.recalling" @click="addNoteDlgToRecall">生成复述卡</el-button>
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

    <!-- 笔记加工结果弹窗（原文 vs 结果，采用/放弃） -->
    <el-dialog v-model="chatTransformD.show" :title="chatTransformTitle" width="560px">
      <div class="cnd-polish-block">
        <div class="cnd-polish-label">原文</div>
        <div class="cnd-polish-text">{{ chatTransformD.original }}</div>
      </div>
      <div class="cnd-polish-block cnd-polish-new">
        <div class="cnd-polish-label">{{ chatTransformD.mode === 'continue' ? '续写内容' : (TRANSFORM_LABELS[chatTransformD.mode] + '后') }}</div>
        <div class="cnd-polish-text">{{ chatTransformD.result }}<span v-if="chatTransformD.streaming" class="cnd-stream-cursor">▍</span></div>
      </div>
      <template #footer>
        <el-button @click="chatTransformD.show = false">放弃</el-button>
        <el-button type="primary" :disabled="chatTransformD.streaming" @click="adoptChatTransform">
          {{ chatTransformD.mode === 'continue' ? '插入' : '采用' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 引用来源气泡（点击角标弹出，点击跳转原文） -->
    <div v-if="citePop.show" class="cite-pop"
      :style="{ left: citePop.x + 'px', top: citePop.y + 'px' }" @click.stop>
      <div class="cite-pop-head">
        <el-tag size="small" effect="plain"
          :type="citePop.source.ref_type === 'note' ? 'success' : (citePop.source.ref_type === 'summary' ? 'warning' : 'primary')">
          {{ { note: '笔记', summary: '摘要' }[citePop.source.ref_type] || '原文' }}
        </el-tag>
        <span class="cite-pop-title">{{ citePop.source.title }}</span>
        <span v-if="citePop.source.page_no" class="cite-pop-page">P{{ citePop.source.page_no }}</span>
      </div>
      <div class="cite-pop-snippet">{{ citePop.source.snippet }}…</div>
      <div class="cite-pop-foot" @click="jumpCite">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7"/><path d="M8 7h9v9"/></svg>
        跳转到原文
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, h } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import mascot from '../assets/mascot.png'
import { ElMessage, ElNotification, ElMessageBox } from 'element-plus'
import { createMd, renderMd as renderMdSafe } from '../utils/md'
import { skillIconText } from '../utils/skillIcon'
import { chatApi, materialApi, kbApi, settingsApi, foldersApi, noteApi, reviewApi, skillApi, resourceApi } from '../api'
import http, { errMsg } from '../api/http'
// SSE 统一走共享 util（此前内联副本硬编码 127.0.0.1:8000，换端口即静默失效）
import { streamSSE } from '../utils/sse'
import { enterToSend } from '../utils/keys'
import ScopePicker from '../components/ScopePicker.vue'

const md = createMd({ breaks: true })
const renderMd = (t, streaming) => renderMdSafe(md, t || '', { streaming: !!streaming })

// 渲染回答：把 [n] 引用编号替换成可点击角标（n 对应 sources 序号）
function renderAnswer(m) {
  // skillImport：对话里出现技能文件草稿（````skill 围栏）时，代码块头部出「导入为技能」按钮
  let html = renderMdSafe(md, m.content || '', { streaming: m._streaming, skillImport: true })
  const sources = m.sources || []
  if (sources.length) {
    // 第三期：角标只在「句末/标点前」才渲染——普通文本里的 [1]（文献编号、数组下标）
    // 在句首或后接文字时保持原样，不再误变成可点角标
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

// 从代码块导入技能（P5）。确认框里先本地粗读 manifest 展示名称/id；读不出来也没关系，
// 后端的 import 校验会给出字段级原因。
async function importSkillFromBlock(btn) {
  const code = b64decodeForBtn(btn)
  let nm = '', sid = ''
  try {
    const m = code.match(/```skill\s*([\s\S]*?)```/)
    const man = JSON.parse(m[1])
    nm = man.name || ''; sid = man.id || ''
  } catch { /* 解析不出就靠后端校验兜底 */ }
  try {
    await ElMessageBox.confirm(
      `将安装技能「${nm || '未命名'}」${sid ? '（' + sid + '）' : ''}。安装后到「技能」页可查看、可挂载。`,
      '导入这个技能？',
      { confirmButtonText: '安装', cancelButtonText: '再想想', type: 'info' })
  } catch { return }   // 用户点了取消
  try {
    const { data } = await skillApi.importText(code)
    ElMessage.success(`已安装技能「${data.skill?.name || nm}」，到「技能」页可挂载使用`)
    btn.textContent = '已安装'
    btn.disabled = true
  } catch (e) {
    ElMessage.error(errMsg(e, '导入失败'))
  }
}

// 代码块按钮上的内容是 base64（含中文直接 atob 会炸），与 md.js 的复制按钮同一套解
function b64decodeForBtn(btn) {
  const bin = atob(btn.getAttribute('data-code') || '')
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new TextDecoder().decode(bytes)
}

// 引用来源气泡（点击角标弹出，显示来源详情，点击跳转）
const citePop = reactive({ show: false, x: 0, y: 0, source: null })

// 点击引用角标：弹出气泡显示来源详情
function onMdClick(m, e) {
  // 「导入为技能」按钮（P5）：技能草稿代码块头部的按钮。点击 ≠ 落盘——
  // 先确认框，确认后走 POST /skills/import（后端有全套字段校验）。
  const impBtn = e.target.closest('.md-code-import-skill')
  if (impBtn) { importSkillFromBlock(impBtn); return }
  const cite = e.target.closest('.cite')
  if (!cite) { citePop.show = false; return }
  const idx = parseInt(cite.dataset.idx, 10)
  const source = (m.sources || [])[idx - 1]
  if (!source) return
  const rect = cite.getBoundingClientRect()
  citePop.source = source
  citePop.x = Math.min(rect.left + rect.width / 2, window.innerWidth - 320)
  // 优先在角标下方弹出；底部空间不足时向上弹出，避免内容超出视口
  const popH = 300   // 气泡高度上限估算（head + snippet 200 + foot）
  const spaceBelow = window.innerHeight - rect.bottom - 12
  citePop.y = spaceBelow > popH ? rect.bottom + 6 : Math.max(10, rect.top - popH - 6)
  citePop.show = true
}

function jumpCite() {
  if (citePop.source) jumpToSource(citePop.source)
  citePop.show = false
}

const router = useRouter()
const route = useRoute()
const sessions = ref([])
const currentId = ref(null)
const messages = ref([])
const question = ref('')
const asking = ref(false)
const scope = ref('all')
// 回答口径（三档问答）：auto 自动 / kb 仅知识库 / general 通用
const askMode = ref('auto')
const selectedMaterialIds = ref([])
const selectedNoteIds = ref([])
const selectedFolderIds = ref([])
const sessionSearch = ref('')
// 模型切换：从配置的模型列表选，默认用对话模型
const chatModels = ref([])
const selectedModelId = ref('default')   // default = 跟随管理中心的对话模型（载荷映射回 null）
const configured = ref(true)   // 是否已配置 LLM 模型：未配置时空态给「配置引导」，默认 true 避免首屏闪烁
const reasoningEffort = ref('low')   // 推理：默认快速（low）；off=关闭（载荷映射回 null），high/max=更强

// 相对时间：无时区 ISO 视为 UTC（后端 datetime.utcnow）
function relTime(iso) {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  const diff = Date.now() - d.getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return min + ' 分钟前'
  const hour = Math.floor(min / 60)
  if (hour < 24) return hour + ' 小时前'
  const day = Math.floor(hour / 24)
  if (day === 1) return '昨天'
  if (day < 7) return day + ' 天前'
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

// 会话分组：置顶 / 今天 / 昨天 / 7 天内 / 更早（含搜索过滤）
const groupedSessions = computed(() => {
  const kw = sessionSearch.value.trim().toLowerCase()
  let list = sessions.value
  if (kw) {
    list = list.filter(s =>
      (s.title || '').toLowerCase().includes(kw) ||
      (s.last_message || '').toLowerCase().includes(kw))
  }
  const groups = [
    { label: '置顶', items: [] },
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '7 天内', items: [] },
    { label: '更早', items: [] },
  ]
  const now = new Date()
  const today0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  for (const s of list) {
    if (s.pinned) { groups[0].items.push(s); continue }
    const d = new Date((s.updated_at || '').endsWith('Z') ? s.updated_at : (s.updated_at || '') + 'Z')
    const day0 = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
    const days = Math.round((today0 - day0) / 86400000)
    if (days <= 0) groups[1].items.push(s)
    else if (days === 1) groups[2].items.push(s)
    else if (days < 7) groups[3].items.push(s)
    else groups[4].items.push(s)
  }
  return groups.filter(g => g.items.length)
})

// ⚠️「取消所有引用后自动切回整个知识库」的 watch 已随范围选择器一起移入
//    components/ScopePicker.vue（F1）——对话 / 陪练共用同一份交互规则。
const materialOptions = ref([])
const noteOptions = ref([])
const folderOptions = ref([])
const msgAreaRef = ref(null)
const inputRef = ref(null)          // 对话框（技能库「去试试」进来时直接聚焦）

const scopePlaceholder = computed(() => {
  if (askMode.value === 'general') return '通用模式：不查知识库，由大模型直接回答…'
  if (askMode.value === 'kb') return '知识库模式：只在库内找答案，找不到会如实告知…'
  return ({
    all: '基于整个知识库提问（资料原文 + 笔记）…',
    material: selectedMaterialIds.value.length ? `在指定的 ${selectedMaterialIds.value.length} 份资料中检索回答…` : '在全部资料原文中检索回答…',
    note: selectedNoteIds.value.length ? `在指定的 ${selectedNoteIds.value.length} 条笔记中检索回答…` : '在全部笔记中检索回答…',
    folder: selectedFolderIds.value.length ? `在选中的 ${selectedFolderIds.value.length} 个文件夹（含子文件夹）中检索回答…` : '选择文件夹后，在其（含子文件夹）范围内检索回答…',
  })[scope.value]
})

// 空态示例问题（2026-11-02 通用化）：原来是产品经理领域题（用户价值公式/RICE），
// 换成不挑领域的能力示范 —— 库内容发现 / 通俗讲解 / 要点整理
const examples = ['我的知识库里有哪些内容？', '帮我把一个陌生概念讲得通俗一点', '整理一下我最近导入资料的重点']

// 口径提示条文案（三档问答）：answer_kind 为空 = 旧数据，按 kb_hit 回落到原灰条逻辑
function missBarText(m) {
  if (!m.content || m.role !== 'assistant') return ''
  if (m.answer_kind === 'kb') return ''
  if (m.answer_kind === 'general') return '通用回答（未查询知识库）'
  if (m.answer_kind === 'kb_miss') return '知识库中未找到相关内容'
  // 找内容（2026-10-26）：有库内资源时不挂提示条；没有才说明，并带「建议核实」
  if (m.answer_kind === 'discover') return ''
  if (m.answer_kind === 'discover_miss') return '资料库里没有相关内容，以下是推荐（建议核实）'
  if (m.answer_kind === 'general_fallback') return '知识库中未找到相关内容，以下为通用回答'
  return m.kb_hit ? '' : '知识库中未找到相关内容，以下为通用回答'
}

async function loadSessions() {
  const { data } = await chatApi.sessions()
  sessions.value = data
}

async function openSession(id) {
  abortCtrl?.abort()          // 切换会话时中断进行中的流（后端检测断连落库半截回答）
  matchCtrl?.abort()          // 预检是可选增强，取消后绝不能降级成「把问题发到新会话」
  asking.value = false
  currentId.value = id
  restoreSkillState(id)        // 每个会话各自记住挂了哪个技能
  const { data } = await chatApi.messages(id)
  messages.value = (data || []).map(m => ({
    ...m, _showReasoning: false,
    // 工具步骤落库回填（P1-②）：历史消息里的步骤都已经跑完，done 恒为 true。
    // 不回填的话，刷新后回答看起来像凭空出现——用户看不出它是读了材料才答的。
    _tools: (m.tool_steps || []).map(s => ({ ...s, done: true, ok: !!s.ok })),
  }))
  scrollBottom(true)
  restoreChatDocs(id)   // 恢复「已存文档」按钮态（不 await：不影响正文显示）
}

// 把本会话已转成文档的回答标回「✓ 已存文档」
async function restoreChatDocs(sid) {
  if (!sid) return
  try {
    const { data } = await materialApi.chatDocs(sid)
    const map = {}
    const list = data.items || []
    list.forEach((it) => { map[it.message_id] = it })
    messages.value.forEach((m) => {
      const hit = map[m.id]
      if (hit) { m._savedDoc = true; m._savedDocId = hit.material_id }
    })
  } catch (e) {
    // 恢复失败不影响主流程（按钮退回「转文档」，再点也只会命中后端幂等）
  }
}

function toggleReasoning(m) {
  m._showReasoning = !m._showReasoning
}

// 新会话：仅前端清空，不立即建库（提问时才由后端自动创建，避免留下空会话）
function newSession() {
  currentId.value = null
  restoreSkillState(null)
  messages.value = []
  renamingId.value = null
}

const renamingId = ref(null)
const renameText = ref('')

function startRename(s) {
  renamingId.value = s.id
  renameText.value = s.title
}

async function saveRename(s) {
  const title = renameText.value.trim()
  renamingId.value = null
  if (!title || title === s.title) return
  await chatApi.renameSession(s.id, title)
  s.title = title
  ElMessage.success('已重命名')
}

// 删除会话：本地先移除 + 4 秒内可撤销（超时才真正落库删除）
function removeSession(id) {
  const idx = sessions.value.findIndex(s => s.id === id)
  if (idx < 0) return
  const removed = sessions.value[idx]
  sessions.value.splice(idx, 1)
  if (currentId.value === id) { currentId.value = null; messages.value = [] }

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
    onClose: () => { if (!restored) chatApi.removeSession(id).catch(() => {}) },
  })
}

async function togglePin(s) {
  try {
    const { data } = await chatApi.pinSession(s.id, !s.pinned)
    s.pinned = data.pinned
  } catch (e) {
    ElMessage.error(errMsg(e, '操作失败'))
  }
}

// O1：仅当用户停留在底部时才跟随滚动（此前每个 token 强制回底，上翻阅读会被拽回）；
// force=true 用于发送新消息/打开会话等必须回底的场景
function scrollBottom(force = false) {
  if (!force && !atBottom.value) return
  if (force) atBottom.value = true
  nextTick(() => {
    if (msgAreaRef.value) msgAreaRef.value.scrollTop = msgAreaRef.value.scrollHeight
  })
}

function scrollToTop() {
  if (msgAreaRef.value) msgAreaRef.value.scrollTop = 0
}

// 滚动位置状态（控制回顶/回底按钮显隐 + 流式跟随闸门）
const atTop = ref(true)
const atBottom = ref(true)   // 初始视为在底部：新会话/未滚动时流式应跟随
function onMsgScroll() {
  const el = msgAreaRef.value
  if (!el) return
  atTop.value = el.scrollTop < 40
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

// 展开/收起引用来源
function toggleSources(m) {
  m._showSources = !m._showSources
}

function jumpToSource(s) {
  if (!s.material_id) return
  const query = {}
  if (s.page_no) query.page = s.page_no
  // 带片段前 30 字用于学习页内高亮定位（摘要类型不高亮）
  if (s.ref_type !== 'summary' && s.snippet) query.hl = s.snippet.slice(0, 30)
  router.push({ path: `/study/${s.material_id}`, query })
}

function askSuggestion(q) {
  if (asking.value) return
  question.value = q
  send()
}

// ---------- 复制 / 编辑 ----------
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text || '')
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text || ''
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
}

function copyUserMsg(m) {
  copyText(m.content)
  ElMessage.success('已复制')
}

async function copyAnswer(m) {
  await copyText(m.content || '')
  m._copied = true
  setTimeout(() => { m._copied = false }, 2000)
}

// AI 回答转笔记：标题=该回答对应的最近一条用户问题，内容=回答正文（引用来源快照一并存档）
// 撤销「对话里 AI 写入」的笔记（P3-②）：点步骤行上的「撤销」。
// 404 = 已经被删过了，直接标记已撤销；409 = 用户编辑过 / 不是 chat_note，如实提示。
async function undoChatNote(s) {
  if (s._undoing) return
  s._undoing = true
  try {
    await http.post(`/notes/${s.note_id}/undo`)
    s.undone = true
    ElMessage.success('已撤销这条笔记')
  } catch (e) {
    if (e?.response?.status === 404) {
      s.undone = true
      ElMessage.info('笔记已不存在，无需撤销')
    } else {
      ElMessage.warning(errMsg(e, '撤销失败'))
    }
  } finally {
    s._undoing = false
  }
}

// AI 回答转文档（2026-10-26）：写成一份 .md 材料 → 进材料库，后台解析后自动建知识库索引。
// 与「转笔记」的分工：笔记只能在知识库里看正文；文档是可打开阅读、能出讲义 / 出题 / 做视频的资产。
async function saveAnswerToDoc(m, i) {
  if (asking.value || m._savedDoc || m._savingDoc) return
  const content = (m.content || '').trim()
  if (!content) return
  m._savingDoc = true
  try {
    const payload = {
      title: docTitleFor(m, i),
      content: content + docSourceAppendix(m),
      origin: 'chat',
      // 幂等键 = 会话 + 消息 id（后端按它认领）。流式中断的回答没有 id → 传空，退化为不幂等但仍可保存。
      origin_ref: (currentId.value && m.id) ? `chat:${currentId.value}:${m.id}` : '',
      origin_meta: {
        saved_from: 'chat',
        session_id: currentId.value || null,
        message_id: m.id || null,
        question: questionOf(m, i).slice(0, 200),
      },
    }
    const { data } = await materialApi.createDocument(payload)
    m._savedDocId = data.id
    m._savedDoc = true
    if (data.duplicated) ElMessage.info('这条回答此前已转过文档')
    else if (data.parsed_status === 'parsing') ElMessage.success('已存到材料库，正在后台解析并建索引')
    else ElMessage.success('已存到材料库，知识库也能检索到了')
  } catch (e) {
    ElMessage.error(errMsg(e, '转文档失败'))
  } finally {
    m._savingDoc = false
  }
}

function openSavedDoc(m) {
  if (m._savedDocId) router.push(`/editor/${m._savedDocId}`)
}

// 该回答对应的最近一条用户提问（转笔记 / 转文档共用口径）
function questionOf(m, i) {
  for (let j = i - 1; j >= 0; j--) {
    const prev = messages.value[j]
    if (prev && prev.role === 'user') return (prev.content || '').trim()
  }
  return ''
}

// 文档标题（会成为文件名与材料库里的名字）：优先回答里的一级/二级标题，其次你的问题，最后截正文
function docTitleFor(m, i) {
  const content = (m.content || '').trim()
  const head = content.split('\n').map(s => s.trim()).find(s => /^#{1,3}\s+\S/.test(s))
  let t = head ? head.replace(/^#{1,3}\s+/, '') : ''
  t = t.replace(/[*`>_]/g, '').trim()
  if (!t) t = questionOf(m, i).replace(/[*`>_#]/g, ' ').trim()
  if (!t) t = content.replace(/[*`>_#\n]/g, ' ').trim()
  return (t.slice(0, 60) || 'AI 回答')
}

// 打开库内资源卡片：route 由后端按类型给（材料 /study/:id、视频 /video/:id、
// 讲书 /podcast、笔记 /knowledge），前端不自己拼 —— 拼错就是点了没反应。
// action 有值 = 材料卡的直达动作（讲义 / 出题）：带 tab 与 auto=1 过去，
// 落地页据此切到目标面板、并在还没有内容时直接开始生成。
// 不带 auto，用户点「出题」还得在目标页再点一次「生成测试题」—— 那这个动作就是装饰。
function openResource(r, action) {
  if (!r || !r.route) return
  if (action && r.kind === 'material') {
    router.push({ path: r.route, query: { tab: action, auto: '1' } })
    return
  }
  // 讲书：早期落库的卡片 route 只有 "/podcast"（没带作品 id）→ 点进去只到列表页，
  // 用户还得自己找。这里按 id 兜底补 ?p=，新数据由后端直接给 "/podcast?p=<id>"。
  if (r.kind === 'podcast' && r.id && r.route.indexOf('?') < 0) {
    router.push({ path: r.route, query: { p: r.id } })
    return
  }
  router.push(r.route)
}

// 库外主题 → 入门导读入库：一次 LLM 调用约 20-40s，幂等键在后端（同主题只生成一份），
// 所以这里只管 loading 与跳转；生成完直接进学习页读它。
async function createBrief(m, i) {
  if (m._briefBusy) return
  m._briefBusy = true
  try {
    const q = questionOf(m, i) || (m.content || '').slice(0, 60) || '学习'
    const { data } = await resourceApi.brief(q)
    ElMessage.success(data.duplicated
      ? '已有一份同主题的导读，直接打开'
      : '导读已存进材料库，可以读 / 出讲义 / 出题')
    router.push('/study/' + data.id)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '生成失败，请稍后重试')
  } finally {
    m._briefBusy = false
  }
}

// 文末附参考来源（有引用才加）：文档是长期资产，出处要跟着走
function docSourceAppendix(m) {
  const list = m.sources || []
  const seen = new Set()
  const rows = []
  list.forEach((s) => {
    if (!s || !s.title) return
    const k = s.title + '|' + (s.page_no || '')
    if (seen.has(k)) return
    seen.add(k)
    rows.push('- ' + s.title + (s.page_no ? '（第 ' + s.page_no + ' 页）' : ''))
  })
  if (!rows.length) return ''
  return '\n\n---\n\n> 参考来源（AI 生成本文时引用）\n>\n' + rows.map(r => '> ' + r).join('\n')
}

async function saveAnswerToNote(m, i) {
  // 防连点：请求发出后置 busy，避免两次并发同时通过幂等检查建出重复笔记
  if (asking.value || m._savedNote || m._savingNote) return
  m._savingNote = true
  try {
    const content = (m.content || '').trim()
    if (!content) return
    let title = ''
    for (let j = i - 1; j >= 0; j--) {
      if (messages.value[j].role === 'user') { title = (messages.value[j].content || '').trim(); break }
    }
    title = title || content.slice(0, 30)
    const sources = (m.sources || []).map(s => ({
      title: s.title, ref_type: s.ref_type, page_no: s.page_no,
      material_id: s.material_id, snippet: s.snippet,
    }))
    const { data } = await noteApi.fromChat({
      title, content,
      sources: sources.length ? sources : undefined,
    })
    m._savedNoteId = data.note?.id
    m._savedNote = true
    if (data.created) {
      if (data.note?.reindex_warning) ElMessage.warning(data.note.reindex_warning)
      else ElMessage.success('已转存到知识库笔记')
    } else {
      ElMessage.info('该对话此前已转存过')
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '转笔记失败'))
  } finally {
    m._savingNote = false
  }
}

// 查看已转存的对话笔记：当前页弹窗查看/编辑（不跳知识库）
async function viewChatNote(m) {
  if (!m._savedNoteId) return
  noteDlg.sourceMsg = m
  noteDlg.id = m._savedNoteId
  noteDlg.show = true
  noteDlg.loading = true
  noteDlg.mode = 'edit'
  try {
    const { data: n } = await http.get(`/notes/${m._savedNoteId}`)
    noteDlg.title = n.title || ''
    noteDlg.content = n.content || ''
    noteDlg.createdAt = n.created_at || null
  } catch (e) {
    ElMessage.error(errMsg(e, '笔记加载失败'))
    noteDlg.show = false
  } finally {
    noteDlg.loading = false
  }
}

// ---------- 对话笔记弹窗：保存 / 删除 / 出复习卡 ----------
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
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    noteDlg.saving = false
  }
}

async function deleteNoteDlg() {
  if (!noteDlg.id) return
  try {
    await noteApi.remove(noteDlg.id)
    const src = noteDlg.sourceMsg
    if (src) { src._savedNote = false; src._savedNoteId = null }   // 按钮复位，可重新转存
    noteDlg.show = false
    ElMessage.success('笔记已删除')
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  }
}

async function addNoteDlgToReview() {
  noteDlg.reviewing = true
  try {
    const { data } = await reviewApi.createCard(noteDlg.id)
    ElMessage.success(data.created ? `已加入复习：「${(data.question || '').slice(0, 24)}…」` : '该笔记已在复习队列中')
  } catch (e) {
    ElMessage.error(errMsg(e, '加入复习失败'))
  } finally {
    noteDlg.reviewing = false
  }
}

async function addNoteDlgToRecall() {
  noteDlg.recalling = true
  try {
    const { data } = await reviewApi.createRecallCard(noteDlg.id)
    ElMessage.success(data.created ? `已生成复述卡：「${(data.question || '').slice(0, 24)}…」` : '该笔记已有复述卡')
  } catch (e) {
    ElMessage.error(errMsg(e, '生成复述卡失败'))
  } finally {
    noteDlg.recalling = false
  }
}

const noteDlg = reactive({
  show: false, id: null, title: '', content: '', mode: 'edit',
  loading: false, saving: false, reviewing: false, recalling: false,
  createdAt: null, sourceMsg: null,
})

// ---------- 笔记 AI 加工（改写/扩写/续写/总结，整条或划线选段） ----------
const TRANSFORM_LABELS = { rewrite: '改写', expand: '扩写', continue: '续写', summarize: '总结' }
const chatTransformD = reactive({ show: false, mode: 'rewrite', original: '', result: '', streaming: false, sel: null })
const chatSel = reactive({ show: false, text: '', start: 0, end: 0 })
const chatTransformTitle = computed(() => {
  const act = TRANSFORM_LABELS[chatTransformD.mode] || '加工'
  return chatTransformD.sel ? `${act}选中文字` : `${act}笔记`
})

// 笔记内容选区：选中文字后浮出加工工具（sel 为 null = 整条笔记）
function onNoteDlgSelect(e) {
  const el = e.target
  if (!el || typeof el.selectionStart !== 'number') return
  const start = el.selectionStart, end = el.selectionEnd
  const text = el.value.substring(start, end)
  if (!text.trim()) { chatSel.show = false; chatSel.text = ''; return }
  chatSel.text = text
  chatSel.start = start
  chatSel.end = end
  chatSel.show = true
}
function hideChatSel() { chatSel.show = false; chatSel.text = '' }

async function chatTransform(mode, sel) {
  const content = sel ? sel.text : noteDlg.content?.trim()
  if (!content) { ElMessage.warning(sel ? '请先选中文字' : '笔记还没有内容'); return }
  chatTransformD.mode = mode
  chatTransformD.original = content
  chatTransformD.result = ''
  chatTransformD.sel = sel ? { start: sel.start, end: sel.end } : null
  chatTransformD.streaming = true
  chatTransformD.show = true
  hideChatSel()
  try {
    await streamSSE('/api/ai/note/transform/stream',
      { content, mode, title: noteDlg.title },
      (t) => { chatTransformD.result += t },
      () => {},
      (msg) => { throw new Error(msg) },
    )
  } catch (e) {
    ElMessage.error(e.message || '加工失败')
    chatTransformD.show = false
  } finally {
    chatTransformD.streaming = false
  }
}

function adoptChatTransform() {
  if (!chatTransformD.result.trim()) { ElMessage.warning('结果为空，无法采用'); return }
  if (chatTransformD.sel) {
    const { start, end } = chatTransformD.sel
    if (chatTransformD.mode === 'continue') {
      // 续写：保留原选段，续写内容追加在选区之后
      noteDlg.content = noteDlg.content.substring(0, end) + '\n\n' + chatTransformD.result + noteDlg.content.substring(end)
    } else {
      noteDlg.content = noteDlg.content.substring(0, start) + chatTransformD.result + noteDlg.content.substring(end)
    }
  } else if (chatTransformD.mode === 'continue') {
    noteDlg.content = noteDlg.content.trimEnd() + '\n\n' + chatTransformD.result
  } else {
    noteDlg.content = chatTransformD.result
  }
  chatTransformD.show = false
  noteDlg.mode = 'edit'
  ElMessage.success(chatTransformD.mode === 'continue' ? '已续写，记得保存' : '已采用，记得保存')
}

// SSE 流式读取（dev 直连后端，与知识库/资料详情页一致）

function startEdit(m) {
  if (asking.value) return
  m._editing = true
  m._editText = m.content
}

function cancelEdit(m) {
  m._editing = false
}

function retryQuestion(m) {
  if (asking.value) return
  const q = m._errorQuestion
  if (!q) return
  // 移除这条错误气泡及其前面的用户消息，然后重新发问
  const idx = messages.value.indexOf(m)
  if (idx >= 0) {
    const userIdx = idx - 1
    if (userIdx >= 0 && messages.value[userIdx]?.role === 'user') {
      messages.value.splice(userIdx, 2)
    } else {
      messages.value.splice(idx, 1)
    }
  }
  question.value = q
  send()
}

async function confirmEdit(m, index) {
  const text = (m._editText || '').trim()
  if (!text || asking.value) return
  m._editing = false
  // 先截断后端历史（删除该消息及之后），确保新回答上下文不混入旧对话
  if (m.id && currentId.value) {
    try { await chatApi.truncateMessages(currentId.value, m.id) } catch { /* 忽略 */ }
  }
  // 本地移除该消息及之后的所有消息
  messages.value.splice(index)
  question.value = text
  send()
}

// ---------------- P1 对话技能（方法层，不是产物） ----------------
// 三件事都在这里：建议卡（模型判断 + 用户点头）、手动挂载（跳过判断）、本会话常驻。
const chatSkills = ref([])
const mountedSkill = ref(null)        // {id, name} 当前会话挂载的技能
const pendingSkill = ref(null)        // 首问时 session_id 还没有 → 先暂存，收到 sources 再回填
const suggest = ref(null)             // {id, name, why, question}
const matching = ref(false)          // 语义判断中（延迟显示，避免规则命中时闪一下）
let matchTimer = null                // 上面那个延迟的定时器
let matchCtrl = null                 // 技能预检请求（切会话/离开页面时取消，且取消后不许继续发问）
const suggestOff = ref(false)         // 本会话不再提示
const suggestOffPending = ref(false)  // 同上：首问的时序兜底
const skillPickerOpen = ref(false)

const SKILL_MAP_KEY = 'asc_chat_skill_map'    // { sessionId: {id,name} }
const SKILL_OFF_KEY = 'asc_chat_skill_off'    // { sessionId: true }

function _lsObj(key) {
  try { return JSON.parse(localStorage.getItem(key) || '{}') || {} } catch { return {} }
}
function _lsPut(key, obj) {
  try { localStorage.setItem(key, JSON.stringify(obj)) } catch { /* 存不下不影响本轮 */ }
}

function iconOf(id) {
  const s = chatSkills.value.find(x => x.id === id)
  return skillIconText(s?.icon)
}

async function loadChatSkills() {
  try {
    const { data } = await skillApi.list()
    // 只列能挂在对话里的（kind=chat）；材料工艺不走这个入口
    chatSkills.value = (data.items || []).filter(s => s.kind === 'chat' && s.enabled !== false)
  } catch { chatSkills.value = [] }
}

// 切会话 / 新会话时，恢复该会话各自的挂载状态与「不再提示」标记
function restoreSkillState(sid) {
  if (sid == null) {
    mountedSkill.value = pendingSkill.value || null
    suggestOff.value = suggestOffPending.value
    return
  }
  mountedSkill.value = _lsObj(SKILL_MAP_KEY)[String(sid)] || null
  suggestOff.value = !!_lsObj(SKILL_OFF_KEY)[String(sid)]
}

function persistMountedSkill() {
  const sid = currentId.value
  if (sid == null) { pendingSkill.value = mountedSkill.value; return }
  const m = _lsObj(SKILL_MAP_KEY)
  if (mountedSkill.value) m[String(sid)] = mountedSkill.value
  else delete m[String(sid)]
  _lsPut(SKILL_MAP_KEY, m)
}

// ⚠️ 首问时 session_id 还不存在：直接按键存会存成 "null"，用户点了「不再提示」第二轮又弹。
//    处置：先记 pending，收到 sources 事件的 session_id 后回填绑定。
function bindPendingToSession(sid) {
  if (pendingSkill.value) {
    const m = _lsObj(SKILL_MAP_KEY)
    m[String(sid)] = pendingSkill.value
    _lsPut(SKILL_MAP_KEY, m)
    pendingSkill.value = null
  }
  if (suggestOffPending.value) {
    const off = _lsObj(SKILL_OFF_KEY)
    off[String(sid)] = true
    _lsPut(SKILL_OFF_KEY, off)
    suggestOffPending.value = false
  }
}

function mountSkill(s) {
  mountedSkill.value = { id: s.id, name: s.name }
  skillPickerOpen.value = false
  suggest.value = null          // 手动挂载后不需要再建议
  persistMountedSkill()
}

function unmountSkill() {
  mountedSkill.value = null
  pendingSkill.value = null
  persistMountedSkill()
}

function useSkill() {
  const s = suggest.value
  if (!s) return
  mountedSkill.value = { id: s.id, name: s.name }
  persistMountedSkill()
  suggest.value = null
  doSend(s.question, s.id)
}

function skipSkill() {
  const s = suggest.value
  if (!s) return
  suggest.value = null
  doSend(s.question, null)
}

function turnOffSuggest() {
  const s = suggest.value
  if (!s) return
  if (currentId.value == null) suggestOffPending.value = true
  else {
    const off = _lsObj(SKILL_OFF_KEY)
    off[String(currentId.value)] = true
    _lsPut(SKILL_OFF_KEY, off)
  }
  suggestOff.value = true
  suggest.value = null
  doSend(s.question, null)
}

// P3：规则一次命中多个技能时，直接在卡片上换（不重新请求后端）
function switchSuggest(alt) {
  const cur = suggest.value
  if (!cur || !alt) return
  const old = { id: cur.id, name: cur.name, why: cur.why }
  const rest = (cur.alternatives || []).filter((x) => x.id !== alt.id)
  suggest.value = {
    ...cur,
    id: alt.id,
    name: alt.name,
    why: alt.why,
    // 备选全部来自规则命中，来源如实改回「规则」，不能继续挂着「AI 判断」
    via: 'rule',
    alternatives: [old, ...rest].slice(0, 2),
  }
}

// 关掉建议卡：把问题还回输入框，不让用户重打一遍
function cancelSuggest() {
  const s = suggest.value
  suggest.value = null
  if (s && !question.value.trim()) question.value = s.question
}

let abortCtrl = null   // 流式请求的中断控制器（停止按钮 / 切会话 / 组件卸载）

// ⚠️ 回车发送统一入口（2026-10-29）：原先 `.enter.exact.prevent` 在中文选词回车时
//    会 preventDefault（候选词上不了屏）并直接发送半句。判定见 utils/keys.js。
const onEnterSend = enterToSend(send)

async function send() {
  const q = question.value.trim()
  if (!q || asking.value) return
  // 建议卡还开着时用户又发了新问题 → 旧建议作废
  if (suggest.value) suggest.value = null
  // 已挂载技能 / 本会话已关提示 → 不需要再问，直接发
  if (mountedSkill.value || suggestOff.value) {
    return doSend(q, mountedSkill.value ? mountedSkill.value.id : null)
  }
  // 先问一次建议：规则命中 <5ms；语义开关开着且规则没命中时，会多等一次模型调用。
  // 「正在判断」提示延迟 250ms 才出 —— 正常路径（规则命中）不该闪一下。
  matchCtrl = new AbortController()
  let matchCancelled = false
  matchTimer = setTimeout(() => { matching.value = true }, 250)
  try {
    const { data } = await skillApi.match(q, scope.value, { signal: matchCtrl.signal })
    if (data && data.suggest) {
      suggest.value = {
        ...data.suggest,
        // P3：备选与并列标记一起带进卡片，切换时不必再请求后端
        alternatives: data.alternatives || [],
        ambiguous: !!data.ambiguous,
        question: q,
      }
      question.value = ''      // 问题已存进卡片，避免用户再按发送重复问
      nextTick(() => scrollBottom())
      return
    }
  } catch {
    // 超时/接口失败按普通对话走；但外部主动取消（切会话/离开页面）必须停住。
    matchCancelled = !!matchCtrl?.signal.aborted
  } finally {
    clearTimeout(matchTimer)
    matching.value = false
    matchCtrl = null
  }
  if (matchCancelled) return
  return doSend(q, null)
}

async function doSend(q, skillId) {
  question.value = ''
  asking.value = true

  const userMsg = { _tmp: 'u' + Date.now(), role: 'user', content: q }
  messages.value.push(userMsg)
  const aiMsg = reactive({ _tmp: 'a' + Date.now(), role: 'assistant', content: '', _reasoning: '', _showReasoning: false, kb_hit: true, answer_kind: '', sources: [], resources: [], suggestions: [], _streaming: true, error: '', _tools: [] })
  messages.value.push(aiMsg)
  scrollBottom(true)

  try {
    // 同源请求：dev 走 vite proxy，生产由后端单端口托管（不要硬编码 127.0.0.1:8000，换端口即失效）
    abortCtrl = new AbortController()
    const resp = await fetch('/api/chat/ask/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: abortCtrl.signal,
      body: JSON.stringify({
        question: q,
        session_id: currentId.value,
        scope: scope.value,
        mode: askMode.value,
        material_ids: scope.value === 'material' && selectedMaterialIds.value.length ? selectedMaterialIds.value : null,
        note_ids: scope.value === 'note' && selectedNoteIds.value.length ? selectedNoteIds.value : null,
        folder_ids: scope.value === 'folder' && selectedFolderIds.value.length ? selectedFolderIds.value : null,
        model_id: selectedModelId.value && selectedModelId.value !== 'default' ? selectedModelId.value : null,
        reasoning_effort: reasoningEffort.value && reasoningEffort.value !== 'off' ? reasoningEffort.value : null,
        skill_id: skillId || null,
      }),
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.detail || `请求失败 ${resp.status}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop()
      for (const evt of events) {
        const lines = evt.split('\n')
        const ev = lines.find(l => l.startsWith('event:'))?.slice(6).trim()
        const dataLine = lines.find(l => l.startsWith('data:'))?.slice(5)
        if (!ev || !dataLine) continue
        const payload = JSON.parse(dataLine)
        if (ev === 'sources') {
          currentId.value = payload.session_id
          bindPendingToSession(payload.session_id)   // 首问的挂载/关闭标记回填到真实会话
          userMsg.id = payload.user_message_id   // 记录后端消息 id（编辑时截断用）
          aiMsg.kb_hit = payload.kb_hit
          aiMsg.answer_kind = payload.answer_kind || ''
          aiMsg.sources = payload.sources
          aiMsg.resources = payload.resources || []
        } else if (ev === 'tool') {
          // 工具步骤（P4）。start/done 成对；⚠️ done 找不到对应 start 时要**补一条**，
          // 否则丢了 start 的那一条会让界面永远停在转圈上（看起来像卡死）。
          if (!aiMsg._tools) aiMsg._tools = []
          if (payload.state === 'start') {
            aiMsg._tools.push({ name: payload.name, label: payload.label, brief: payload.brief || '',
              round: payload.round, done: false, ok: true })
          } else {
            let hit = null
            for (let i = aiMsg._tools.length - 1; i >= 0; i--) {
              const s = aiMsg._tools[i]
              if (s.round === payload.round && s.name === payload.name && !s.done) { hit = s; break }
            }
            if (hit) {
              hit.done = true; hit.ok = !!payload.ok
              if (payload.label) hit.label = payload.label
              // 结果级 brief（含真实标题与页码）覆盖参数级的「材料38」
              if (payload.brief) hit.brief = payload.brief
              hit.ms = payload.ms            // P3-② 耗时
              hit.reused = !!payload.reused  // P2-① 复用标记
              if (payload.note_id) hit.note_id = payload.note_id   // P3-② 撤销按钮定位
            } else aiMsg._tools.push({ name: payload.name, label: payload.label,
              brief: payload.brief || '', round: payload.round, done: true, ok: !!payload.ok,
              ms: payload.ms, reused: !!payload.reused, note_id: payload.note_id })

          }
          scrollBottom()
        } else if (ev === 'source_add') {
          // 工具补充来源（P1-③）：追加在**尾部**，正文里的 [n] 编号不受影响。
          aiMsg.sources = (aiMsg.sources || []).concat(payload.sources || [])
          // 读到过知识库内容就不该再显示「未找到相关内容」灰条
          if (payload.kb_hit) aiMsg.kb_hit = true
          if (payload.answer_kind) aiMsg.answer_kind = payload.answer_kind
        } else if (ev === 'reasoning') {
          aiMsg._reasoning += payload.t
          scrollBottom()
        } else if (ev === 'suggestions') {
          aiMsg.suggestions = payload.questions
        } else if (ev === 'token') {
          aiMsg.content += payload.t
          scrollBottom()
        } else if (ev === 'done') {
          aiMsg.id = payload.message_id
          aiMsg._streaming = false   // 第三期：done 即收光标（suggestions 随后到，不等它）
        } else if (ev === 'error') {
          const emsg = payload.message || '生成失败'
          if (emsg.includes('[NEED_SETUP]')) {
            throw new Error(emsg)   // 走原有配置引导路径
          }
          // P1-3：错误已随半截回答落库 → 气泡内错误态，不弹全局提示、不移除气泡
          aiMsg.error = emsg
          if (payload.message_id) aiMsg.id = payload.message_id
          aiMsg._streaming = false
          await loadSessions()
          return
        }
      }
    }
    aiMsg._streaming = false
    await loadSessions()
  } catch (e) {
    if (e.name === 'AbortError') {
      // 用户主动停止：后端检测断连会把半截回答落库（标记「用户中断生成」）
      aiMsg._streaming = false
      aiMsg.error = '用户中断生成'
      loadSessions()
      return
    }
    aiMsg._streaming = false
    aiMsg.content = aiMsg.content || ''
    // 技能在别处被停用 / 删除：后端会明确报错。这里自动取消挂载并回填问题，
    // 否则用户只看到一句报错，下一次发问还会拿同一个失效 id 再撞一次。
    if (/已停用|技能不存在/.test(e.message || '') && mountedSkill.value) {
      ElMessage.warning((e.message || '') + '（已自动取消挂载，请重新发送）')
      unmountSkill()
      question.value = q
      messages.value = messages.value.filter(m => m !== aiMsg)
      return
    }
    // 未配置模型：气泡内展示错误态（含「重试」「去配置」按钮），不弹全局引导，也不移除气泡
    if ((e.message || '').includes('[NEED_SETUP]')) {
      aiMsg._error = (e.message || '').replace('[NEED_SETUP]', '').replace(/^\d{3}:\s*/, '') || '尚未配置可用的 AI 模型'
      aiMsg._errorQuestion = q
      scrollBottom()
    } else {
      ElMessage.error(e.message || '对话失败')
      // P1-5：失败回填问题文本（此前输入框已清空，问题直接丢失）
      question.value = q
      if (!aiMsg.content) messages.value = messages.value.filter(m => m !== aiMsg)
    }
  } finally {
    asking.value = false
    abortCtrl = null
  }
}

function stopAsk() {
  abortCtrl?.abort()
}

// 重新生成：入口只挂**最后一条**回答。中间回答重生成会把之后的整段对话一起截掉
// （后端 truncate 语义 = 该消息及之后全删），不给确认就丢整段对话太危险，故不放入口；
// 错误态也不放（err-bar 的「重试」已是同一动作，避免两个按钮抢戏）。
function isLastAssistant(m) {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].role === 'assistant') return messages.value[i] === m
  }
  return false
}

// 口径与「重试 / 编辑重发」一致：截断「问题 + 旧回答」后用同一个问题重发，
// 沿用当前挂载的技能与范围/模型设置。直调 doSend 跳过技能建议卡——
// 用户点的是「重新生成」，不该再被问一次「要不要用技能」。
async function regenerate(m) {
  if (asking.value || !m || m.role !== 'assistant') return
  const idx = messages.value.indexOf(m)
  if (idx < 0) return
  let uIdx = -1
  for (let i = idx - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') { uIdx = i; break }
  }
  if (uIdx < 0) return
  const q = messages.value[uIdx].content
  const uid = messages.value[uIdx].id
  if (currentId.value && uid) {
    try { await chatApi.truncateMessages(currentId.value, uid) } catch { /* 忽略 */ }
  }
  messages.value.splice(uIdx)
  doSend(q, mountedSkill.value ? mountedSkill.value.id : null)
}

// 错误消息重试（P1-3）：截断掉「问题 + 错误回答」后重新发问，不制造重复消息
async function retryError(m) {
  if (asking.value) return
  const idx = messages.value.indexOf(m)
  if (idx < 0) return
  let uIdx = -1
  for (let i = idx - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') { uIdx = i; break }
  }
  if (uIdx < 0) return
  const q = messages.value[uIdx].content
  const uid = messages.value[uIdx].id
  if (currentId.value && uid) {
    try { await chatApi.truncateMessages(currentId.value, uid) } catch { /* 忽略 */ }
  }
  messages.value.splice(uIdx)
  question.value = q
  send()
}

function friendlyErr(e) {
  return (e || '').replace('[NEED_SETUP]', '').trim()
}

onMounted(async () => {
  // 每次进入默认新会话（不自动打开最近会话；首个问题会自动建会话）
  currentId.value = null
  messages.value = []
  await loadSessions()
  // P1：可挂载的对话技能（失败则空列表，不影响对话）。
  // 技能库「去试试」带 ?skill=<id> 进来：必须等列表回来再校验 —— 先挂后校验会把
  // 已停用 / 已删除的技能当成可用挂上，用户点发送才在后端被拒。
  await loadChatSkills()
  const wantSkill = String(route.query.skill || '').trim()
  if (wantSkill) {
    const hit = chatSkills.value.find(s => s.id === wantSkill)
    if (hit) {
      mountedSkill.value = { id: hit.id, name: hit.name }
      persistMountedSkill()        // 首问还没有 session_id → 记为 pending，收到 sources 再回填
      nextTick(() => inputRef.value?.focus?.())
      ElMessage.success(`已加载技能「${hit.name}」，这轮提问会按它回答`)
    } else {
      ElMessage.warning('这个技能当前不可用（可能已停用），已进入普通对话')
    }
    router.replace({ path: '/chat' })   // 清掉参数：刷新 / 回退不该再次强行挂载
  }
  // 范围选择的条目选项
  const [{ data: mats }, { data: kb }, { data: folders }] = await Promise.all([
    materialApi.list(), kbApi.overview(), foldersApi.list(),
  ])
  materialOptions.value = mats.filter(m => m.parsed_status === 'success')
  noteOptions.value = kb.notes
  folderOptions.value = folders
  // 加载模型列表，默认选中对话模型
  try {
    const { data: st } = await settingsApi.get()
    chatModels.value = st.llm_models || []
    // ⚠️ 默认模型 = 后端「对话用途」配置；不兜底到列表第一条，否则「对话模型没选」时
    // 会静默用列表第一个模型发请求、绕过 NEED_SETUP 校验（用户实测报过）。
    selectedModelId.value = st.chat_model_id || 'default'
    configured.value = st.configured !== false
  } catch { /* 静默 */ }
  // 从资料详情页进入：自动关联当前资料为检索范围
  const mid = route.query.material_id
  if (mid) {
    scope.value = 'material'
    selectedMaterialIds.value = [Number(mid)]
  }
  // 点击气泡/角标以外区域关闭引用气泡
  document.addEventListener('mousedown', closeCitePop)
})

onUnmounted(() => {
  abortCtrl?.abort()   // 离开对话页时中断进行中的流
  matchCtrl?.abort()   // 技能预检也不能在页面卸载后继续补发问题
  document.removeEventListener('mousedown', closeCitePop)
})

function closeCitePop(e) {
  if (e.target.closest('.cite-pop') || e.target.closest('.cite')) return
  citePop.show = false
}
</script>

<style scoped>
.chat-page { display: flex; height: 100vh; overflow: hidden; }

/* ===== 左：会话列表 ===== */
.session-col {
  width: 264px; flex-shrink: 0;
  /* 比对话区（--asc-bg #f5f5f6）深一档，用明度差建立导航与阅读区的边界 */
  background: #edeef2; padding: 18px 12px; overflow-y: auto;
}
.new-btn {
  width: 100%; margin-bottom: 12px; border-radius: 12px; height: 40px;
  display: flex; align-items: center; justify-content: center; gap: 6px;
  font-weight: 500; box-shadow: 0 2px 8px rgba(124, 92, 252, .18);
}
.new-btn svg { flex-shrink: 0; }
.session-search { margin-bottom: 6px; }
.session-search :deep(.el-input__wrapper) {
  border-radius: 10px; background: var(--asc-card);
  box-shadow: 0 1px 2px rgba(0, 0, 0, .04) !important; transition: all .2s ease;
}
.session-search :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1.5px rgba(124, 92, 252, .45), 0 0 0 4px rgba(124, 92, 252, .08) !important;
}
.session-group-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--asc-text-3); padding: 14px 8px 5px;
  letter-spacing: .5px; font-weight: 500;
}
.session-group-label::before {
  content: ''; width: 4px; height: 4px; border-radius: 50%;
  background: var(--asc-text-3); opacity: .5;
}
.session-item {
  position: relative;
  display: flex; align-items: flex-start; gap: 6px;
  padding: 9px 12px; border-radius: 10px; cursor: pointer;
  color: var(--asc-text-2); transition: background .16s ease;
  margin-bottom: 2px;
}
.session-item:hover { background: var(--asc-card); }
.session-item.active { background: rgba(124, 92, 252, .12); }
.session-body { flex: 1; min-width: 0; }
.session-title {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 13px; color: var(--asc-text); font-weight: 500; line-height: 1.4;
}
.session-item.active .session-title { color: var(--asc-primary); font-weight: 600; }
.session-meta { font-size: 11px; color: var(--asc-text-3); margin-top: 2px; line-height: 1.4; }
.session-preview {
  font-size: 11.5px; color: var(--asc-text-3); margin-top: 3px; line-height: 1.4;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.pin-icon {
  flex-shrink: 0; width: 22px; height: 22px; margin-top: 0;
  display: flex; align-items: center; justify-content: center;
  color: var(--asc-text-3); opacity: 0; border-radius: 6px;
  transition: all .16s ease; cursor: pointer;
}
.session-item:hover .pin-icon, .pin-icon.on { opacity: 1; }
.pin-icon.on { color: var(--asc-primary); }
.pin-icon:hover { background: var(--asc-surface-2); color: var(--asc-text-2); }
.session-del { visibility: hidden; flex-shrink: 0; padding: 4px !important; margin-top: 0; }
.session-item:hover .session-del { visibility: visible; }
.session-empty { font-size: 12px; color: var(--asc-text-3); text-align: center; padding: 28px 0; }

/* ===== 右：对话区 ===== */
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--asc-bg); }
.msg-area { flex: 1; overflow-y: auto; padding: 32px 40px 24px; position: relative;
  /* 长代码块的头部吸附在这一层：sticky 的落点是 padding 内侧，需要头部补上这 32px。
     ⚠️ 改这里的 padding-top 必须同步改这个值（否则又会有代码从头部上方露出来）。 */
  --md-sticky-cover: 32px; }
.msg-col { max-width: 768px; margin: 0 auto; }
/* 回到顶部 / 底部（悬浮于输入框上方，居中偏左 50px） */
.scroll-btns {
  position: absolute; left: calc(50% - 50px); transform: translateX(-50%);
  bottom: calc(100% + 10px); z-index: 10;
  display: flex; gap: 10px;
}
.scroll-btn {
  width: 36px; height: 36px; border-radius: 50%;
  background: var(--asc-card);
  display: flex; align-items: center; justify-content: center;
  color: var(--asc-text-2); cursor: pointer;
  box-shadow: 0 2px 10px rgba(0, 0, 0, .10);
  transition: all .18s ease;
}
.scroll-btn:hover { color: var(--asc-primary); transform: translateY(-2px); box-shadow: var(--asc-glow); }

/* 空状态 hero */
.chat-empty { text-align: center; color: var(--asc-text-2); padding-top: 11vh; }
.mascot-hero { position: relative; width: 120px; height: 120px; margin: 0 auto 22px; }
.empty-mascot {
  width: 120px; height: 120px; animation: mascot-float 3.6s ease-in-out infinite;
  position: relative; z-index: 1;
}
.mascot-hero::after {
  content: ''; position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 108px; height: 108px; border-radius: 50%;
  background: radial-gradient(circle, rgba(124,92,252,.14), transparent 70%);
}
@keyframes mascot-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-7px); }
}
.greet-title { margin: 0 0 8px; font-size: 26px; font-weight: 700; color: var(--asc-text); letter-spacing: .2px; }
.greet-sub { margin: 0 0 36px; font-size: 14px; color: var(--asc-text-3); }
.example-list { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; max-width: 620px; margin: 0 auto; }
.example-q {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 11px 18px;
  border-radius: 12px; font-size: 13.5px; cursor: pointer; color: var(--asc-text-2);
  background: var(--asc-card); box-shadow: 0 1px 3px rgba(0, 0, 0, .05);
  transition: all .2s ease;
}
.example-q svg { color: var(--asc-text-3); transition: color .2s ease; flex-shrink: 0; }
.example-q:hover {
  color: var(--asc-primary);
  transform: translateY(-2px); box-shadow: 0 6px 16px rgba(124, 92, 252, .16);
}
.example-q:hover svg { color: var(--asc-primary); }
/* 未配置模型：空态配置引导 */
.setup-cta { margin: 0 0 16px; }
.setup-cta .el-button { padding: 12px 30px; font-size: 14px; }
.setup-note { font-size: 12.5px; color: var(--asc-text-3); }

/* 消息行 */
.msg-row { display: flex; margin-bottom: 30px; }
.msg-row.right { justify-content: flex-end; }
.bubble { max-width: 72%; padding: 12px 16px; font-size: 14px; line-height: 1.8; user-select: text; }
.bubble.user {
  background: #ece7fe; color: #3c3489;
  border-radius: 18px 18px 4px 18px;
  box-shadow: 0 1px 3px rgba(124, 92, 252, .10);
}
/* AI 消息去卡片化：不套气泡，回答直接排版在页面上（ChatGPT/Claude 风） */
.bubble.ai {
  flex: 1; min-width: 0; max-width: none;
  background: transparent; padding: 2px 0 0;
  color: var(--asc-text);
}
.user-text { white-space: pre-wrap; word-break: break-word; }
/* 消息操作（复制/编辑）：hover 显示。
   2026-11-02：opacity:0 仍占位会在用户气泡底部留白 → 不收不占地（max-height 0），悬停才展开 */
.msg-ops { display: flex; gap: 10px; justify-content: flex-end; max-height: 0; margin-top: 0; opacity: 0; overflow: hidden; transition: opacity .15s ease, max-height .15s ease, margin-top .15s ease; }
.bubble:hover .msg-ops { opacity: 1; max-height: 24px; margin-top: 6px; }
.msg-op {
  font-size: 12px; color: var(--asc-text-3); cursor: pointer; user-select: none;
  transition: color .15s ease;
}
.msg-op:hover { color: var(--asc-primary); }
.msg-op.done { color: #67c23a; }
.edit-ops { display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px; }
.answer-actions {
  display: flex; align-items: center; gap: 18px; margin-top: 12px;
}
.answer-actions .msg-op { display: inline-flex; align-items: center; gap: 4px; }
/* 操作图标统一主题紫；成功态（已转存/已复制）整体转绿 */
.answer-actions .msg-op svg { color: var(--asc-primary); }
.answer-actions .msg-op.done svg { color: #67c23a; }

/* 思考态 */
.thinking-row { display: flex; align-items: center; gap: 5px; padding: 4px 0 6px; }
.thinking-dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--asc-primary);
  animation: thinking-bounce 1.2s infinite ease-in-out;
}
.thinking-dot:nth-child(2) { animation-delay: .15s; }
.thinking-dot:nth-child(3) { animation-delay: .3s; }
.thinking-text { margin-left: 6px; font-size: 12.5px; color: var(--asc-text-3); }
@keyframes thinking-bounce { 0%, 80%, 100% { opacity: .25; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }

.kb-miss-bar {
  font-size: 12px; color: var(--asc-text-2); background: var(--asc-surface-2); border-radius: 8px;
  padding: 6px 12px; margin-bottom: 10px;
}
/* 通用档回答的中性徽章：不是「失败灰」，是用户主动选择的口径 */
.kb-miss-bar.neutral { color: var(--el-color-primary); background: var(--el-color-primary-light-9); }

/* 三档口径开关行（自动/知识库/通用）+ 通用档下禁用范围选择 */
.ask-mode-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.scope-wrap.off { opacity: .45; pointer-events: none; }

/* 工具步骤（P4）：模型调用技能授权工具时的过程记录。
   刻意做得很轻（一行一个胶囊）—— 它是过程，不该比答案更抢眼。
   ⚠️ 转圈用 animation：这是 UI，不是视频模板（模板里禁用 animation 是渲染确定性要求）。 */
.tool-steps { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }
.tool-step {
  display: flex; align-items: center; gap: 6px; align-self: flex-start;
  font-size: 12px; color: var(--asc-text-2);
  padding: 3px 10px 3px 8px; border-radius: 999px; background: var(--asc-surface-2);
}
.tool-step-ic { display: inline-flex; align-items: center; color: var(--asc-text-2); }
/* 动作摘要是**次要**信息：靠字号与位置区分，不靠调浅颜色 ——
   调浅会掉到 4.5:1 以下（白底 #a0a0a0 只有 2.6:1）。 */
.tool-step-bf {
  max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 11.5px; letter-spacing: -.1px;
}
.tool-step-bf::before { content: "·"; margin-right: 4px; }
/* 耗时 / 复用 / 已撤销（P3-②）：比 brief 更弱一级的次要信息 */
.tool-step-ms { font-size: 11px; }
.tool-undo {
  border: none; background: transparent; padding: 0 2px; cursor: pointer;
  font-size: 11.5px; color: var(--asc-primary);
}
.tool-undo:hover { text-decoration: underline; }
.tool-step.failed { color: #c4564f; background: #fdf0ef; }
.tool-step.failed .tool-step-ic { color: #c4564f; }
.tool-spin { animation: tool-spin 1s linear infinite; }
@keyframes tool-spin { to { transform: rotate(360deg); } }
/* 生成中断/失败条（错误落库口径 A：气泡内错误态 + 重试） */
.err-bar {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: #c4564f; background: #fdf0ef; border: 1px solid #f5d5d2;
  border-radius: 8px; padding: 6px 12px; margin-bottom: 10px;
}
.err-bar.stopped {
  color: var(--asc-text-2); background: var(--asc-surface-2); border-color: transparent;
}
.err-retry {
  border: none; background: none; cursor: pointer; font-size: 12px;
  color: #7c5cfc; font-weight: 600; padding: 0;
}
.err-retry:hover { text-decoration: underline; }
/* 未配置模型：「重试 + 去配置」气泡内错误态 */
.ai-config-error {
  font-size: 13px; color: var(--asc-text-2); background: var(--asc-surface-2); border-radius: 8px;
  padding: 10px 12px; margin-bottom: 8px;
}
.ai-config-error-text { margin-bottom: 8px; line-height: 1.5; }
.ai-config-error-ops { display: flex; gap: 8px; }
.md-body :deep(h1) { font-size: 18px; font-weight: 600; margin: 16px 0 8px; line-height: 1.4; }
.md-body :deep(h2) { font-size: 16px; font-weight: 600; margin: 14px 0 8px; padding-left: 10px; border-left: 3px solid var(--asc-primary); line-height: 1.4; }
.md-body :deep(h3) { font-size: 15px; font-weight: 600; margin: 12px 0 6px; line-height: 1.4; }
.md-body :deep(h4) { font-size: 14px; font-weight: 600; margin: 10px 0 6px; line-height: 1.4; }
.md-body :deep(p) { margin: 8px 0; }
.md-body :deep(ul), .md-body :deep(ol) { padding-left: 22px; margin: 8px 0; }
.md-body :deep(li) { margin: 4px 0; }
.md-body :deep(li::marker) { color: var(--asc-primary); }
.md-body :deep(strong) { font-weight: 600; }
.md-body :deep(code) { background: var(--asc-surface-2); padding: 2px 6px; border-radius: 4px; font-size: 13px; }
.md-body :deep(pre) { background: var(--asc-surface-2); padding: 12px 14px; border-radius: 8px; overflow-x: auto; margin: 10px 0; }
.md-body :deep(pre code) { background: transparent; padding: 0; }
.md-body :deep(blockquote) { margin: 10px 0; padding: 8px 14px; border-left: 3px solid var(--asc-primary); background: var(--asc-surface-2); border-radius: 0 8px 8px 0; color: var(--asc-text-2); }
.md-body :deep(hr) { border: none; border-top: 1px solid var(--asc-divider); margin: 16px 0; }
.md-body :deep(a) { color: var(--asc-primary); text-decoration: none; }
.md-body :deep(a:hover) { text-decoration: underline; }
.md-body :deep(table) { border-collapse: collapse; margin: 12px 0; width: 100%; font-size: 13px; }
.md-body :deep(th), .md-body :deep(td) { border: 1px solid var(--asc-border); padding: 6px 10px; text-align: left; }
.md-body :deep(th) { background: var(--asc-surface-2); font-weight: 600; }
.md-body :deep(.cite) {
  display: inline-block; vertical-align: super;
  font-size: 11px; font-weight: 600; color: var(--asc-primary);
  background: var(--asc-primary-soft);
  border-radius: 4px; padding: 0 4px; margin: 0 1px;
  cursor: pointer; line-height: 1.4; transition: all .15s ease;
}
.md-body :deep(.cite:hover) { background: var(--asc-primary); color: #fff; }
/* 引用来源气泡 */
.cite-pop {
  position: fixed; z-index: 300; width: 300px;
  background: var(--asc-card);
  border-radius: 14px; padding: 12px 14px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, .16);
  animation: asc-fade-up .18s ease;
}
.cite-pop-head { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.cite-pop-title { font-size: 13px; font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cite-pop-page { font-size: 11px; color: var(--asc-primary); font-weight: 500; flex-shrink: 0; }
.cite-pop-snippet {
  font-size: 12.5px; color: var(--asc-text-2); line-height: 1.7;
  max-height: 200px; overflow-y: auto; padding-right: 6px;
  scrollbar-width: thin;
}
.cite-pop-foot {
  display: flex; align-items: center; gap: 5px; justify-content: flex-end;
  margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--asc-divider);
  font-size: 12.5px; color: var(--asc-primary); font-weight: 500; cursor: pointer;
  transition: color .15s ease;
}
.cite-pop-foot:hover { color: var(--asc-primary-hover); }
.cursor { color: var(--asc-primary); animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0; } }

/* 引用来源（折叠标识，点击展开） */
/* 库内相关资源卡片（找内容） */
.res-block { margin-top: 14px; }
.res-label {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; color: #6e6e6e; margin-bottom: 8px;
}
.res-list { display: flex; flex-direction: column; gap: 6px; }
.res-card {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border: 1px solid #e6e4ef; border-radius: 8px;
  background: #fbfafe; cursor: pointer;
}
.res-card:hover { border-color: #b9aef0; background: #f6f3ff; }
.res-badge {
  flex: none; font-size: 11px; line-height: 1; padding: 4px 6px; border-radius: 4px;
  background: #ece8fd; color: #534ab7; font-weight: 500; white-space: nowrap;
}
.res-card.k-note .res-badge { background: #e1f5ee; color: #0f6e56; }
.res-card.k-video .res-badge { background: #faeeda; color: #854f0b; }
.res-card.k-podcast .res-badge { background: #fbeaf0; color: #993556; }
.res-title {
  flex: 1; min-width: 0; font-size: 13px; color: #2b2b2b; line-height: 1.45;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.res-meta { flex: none; font-size: 11px; color: #7a7a7a; }
.res-arrow { flex: none; color: #a8a8b3; }
/* 材料卡的直达动作（讲义 / 出题）：小、浅、只在材料卡上出现，
   不让「读」这个主入口（整卡点击）被按钮抢走注意力 */
.res-acts { flex: none; display: inline-flex; gap: 4px; }
.res-act {
  font: inherit; font-size: 11px; line-height: 1; padding: 5px 8px;
  color: #534ab7; background: #fff; border: 1px solid #d9d3f5; border-radius: 6px;
  cursor: pointer; transition: all .15s ease;
}
.res-act:hover { background: #ece8fd; border-color: #b9aef0; }
/* 库外推荐的动作卡：比正文轻，比灰条重一档，明确是「下一步做什么」 */
.brief-cta {
  display: flex; align-items: center; gap: 12px;
  margin-top: 14px; padding: 11px 13px;
  border: 1px dashed #cfc7ee; border-radius: 8px; background: #faf9ff;
}
.brief-cta-body { flex: 1; min-width: 0; }
.brief-cta-title { font-size: 13px; color: #2b2b2b; line-height: 1.5; }
.brief-cta-sub { margin-top: 3px; font-size: 12px; color: #6e6e6e; line-height: 1.6; }
.brief-cta-note { color: #8a8a94; }
.sources { margin-top: 14px; }
.sources-toggle {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--asc-primary); font-weight: 500;
  padding: 6px 12px; border-radius: 999px; cursor: pointer;
  background: rgba(124, 92, 252, .10); transition: all .18s ease;
  user-select: none;
}
.sources-toggle:hover { background: rgba(124, 92, 252, .18); }
.toggle-arrow { transition: transform .2s ease; }
.toggle-arrow.open { transform: rotate(180deg); }
.source-list { margin-top: 12px; }
.source-card {
  background: var(--asc-card); border-radius: 12px; padding: 10px 14px; margin-bottom: 8px;
  cursor: pointer; transition: all .18s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .05);
}
.source-card:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0, 0, 0, .08); }
.source-card.focused { box-shadow: 0 0 0 1.5px var(--asc-primary), 0 4px 12px rgba(124, 92, 252, .15); }
.source-head { display: flex; gap: 6px; align-items: center; margin-bottom: 4px; }
.source-title { font-size: 12px; font-weight: 500; color: var(--asc-primary); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source-page { font-size: 11px; color: var(--asc-primary); font-weight: 500; }
.source-snippet { font-size: 12px; color: var(--asc-text-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 相关问题推荐 */
.suggestions { margin-top: 16px; }
.suggestions-label { font-size: 11px; color: var(--asc-text-3); margin-bottom: 9px; }
.suggestion-chip {
  display: inline-block; margin: 0 8px 8px 0; padding: 8px 15px;
  font-size: 13px; color: var(--asc-text-2);
  background: var(--asc-card); box-shadow: 0 1px 3px rgba(0, 0, 0, .05);
  border-radius: 16px; cursor: pointer; transition: all .18s ease;
}
.suggestion-chip:hover { color: var(--asc-primary); background: rgba(124, 92, 252, .10); transform: translateY(-1px); }

/* 头像 */
.ai-avatar {
  width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
  background: var(--asc-card); padding: 2px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .08);
  margin-right: 12px; margin-top: 4px; transition: all .3s ease;
}
.ai-avatar.thinking { animation: avatar-breathe 1.6s ease-in-out infinite; }
@keyframes avatar-breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.08); } }

/* 输入区：单一悬浮 composer（无边框，柔和投影 + focus 紫色光晕） */
.input-bar {
  position: relative;
  padding: 6px 40px 18px;
  background: transparent;
}
.input-inner { max-width: 768px; margin: 0 auto; }
.input-col {
  display: flex; flex-direction: column; gap: 8px;
  background: var(--asc-card);
  border-radius: 18px; padding: 10px 14px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, .05);
  transition: box-shadow .2s ease;
}
.input-col:focus-within {
  box-shadow: 0 0 0 1.5px rgba(124, 92, 252, .45), 0 4px 18px rgba(124, 92, 252, .10);
}
.input-col :deep(.el-textarea__inner) {
  background: transparent; box-shadow: none !important; padding: 10px 0 10px 10px; font-size: 14.5px; line-height: 1.7;
}
/* ---- P1 对话技能：建议卡 / 已加载条 / ＋技能选择器 ---- */
.sk-sugg {
  border: 1px solid #d6e4ff; background: #f5f9ff; border-radius: 10px;
  padding: 10px 12px; margin-bottom: 8px;
}
.sk-sugg-hd { display: flex; align-items: center; gap: 7px; margin-bottom: 6px; }
.sk-sugg-ic {
  width: 20px; height: 20px; border-radius: 6px; background: #2563eb; color: #fff;
  font-size: 11px; display: flex; align-items: center; justify-content: center; flex: 0 0 20px;
}
.sk-sugg-nm { font-size: 13px; font-weight: 600; color: #1f2328; }
.sk-sugg-tag { font-size: 11px; color: #2563eb; background: #e8f0ff; border-radius: 4px; padding: 1px 6px; }
.sk-sugg-x { margin-left: auto; cursor: pointer; color: #6e6e6e; font-size: 15px; line-height: 1; padding: 0 2px; }
.sk-sugg-x:hover { color: #1f2328; }
.sk-sugg-why { font-size: 12px; color: #4a4a4a; line-height: 1.65; margin-bottom: 9px; }
.sk-sugg-ft { display: flex; align-items: center; gap: 8px; }
.sk-sugg-off { font-size: 12px; color: #6e6e6e; cursor: pointer; }
.sk-sugg-off:hover { color: #2563eb; text-decoration: underline; }

/* P3：模型判断出来的建议与关键词命中的，颜色区分开（来源影响可信度判断） */
.sk-sugg-tag.ai { color: #7c3aed; background: #f2ecff; }
/* 并列提示：命中多个、规则分不出高下时如实说，不假装确定 */
.sk-sugg-amb { font-size: 11px; color: #8a5a00; background: #fff4e0; border-radius: 4px; padding: 1px 6px; }
.sk-sugg-alts { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 9px; }
.sk-sugg-alts-lb { font-size: 12px; color: #6e6e6e; }
.sk-sugg-alt {
  font-size: 12px; color: #2563eb; background: #fff; border: 1px solid #d6e4ff;
  border-radius: 5px; padding: 1px 8px; cursor: pointer;
}
.sk-sugg-alt:hover { background: #e8f0ff; }
/* 语义判断中的轻提示：内联在输入区上方，不遮挡输入 */
.sk-matching { font-size: 12px; color: #6e6e6e; padding: 4px 2px 8px; }

.skill-bar {
  display: flex; align-items: center; gap: 7px; margin-bottom: 8px;
  padding: 6px 10px; border-radius: 8px;
  background: #f2f7ff; border: 1px solid #dce8fb; font-size: 12px; color: #3d4b5c;
}
.skill-bar-ic {
  width: 17px; height: 17px; border-radius: 5px; background: #2563eb; color: #fff;
  font-size: 10px; display: flex; align-items: center; justify-content: center; flex: 0 0 17px;
}
.skill-bar b { color: #1f2328; font-weight: 600; }
.skill-bar-hint { color: #6e6e6e; }
.skill-bar-x { margin-left: auto; cursor: pointer; color: #6e6e6e; }
.skill-bar-x:hover { color: #c45656; }

/* 2026-11-02 实测对齐：按钮原来 25px、旁边下拉 30px → 高度不一。
   定死 30px（box-sizing 含边框），纵向 padding 交给 height 而不是 3px 上下内边距 */
.skill-add {
  font-family: inherit; font-size: 12px; color: #4a4a4a; cursor: pointer;
  background: #fff; border: 1px solid #dcdfe6; border-radius: 6px;
  height: 30px; box-sizing: border-box;
  padding: 0 10px; display: inline-flex; align-items: center; gap: 2px;
  white-space: nowrap; transition: none;
}
.skill-add:hover { border-color: #2563eb; color: #2563eb; }
.skill-add.on { border-color: #2563eb; color: #2563eb; background: #f2f7ff; }

.sk-pick-hd { font-size: 12px; color: #6e6e6e; margin-bottom: 8px; }
.sk-pick-row { display: flex; align-items: center; gap: 8px; padding: 7px 8px; border-radius: 7px; cursor: pointer; }
.sk-pick-row:hover { background: #f5f7fa; }
.sk-pick-row.on { background: #f2f7ff; }
.sk-pick-ic {
  width: 20px; height: 20px; border-radius: 6px; background: #eef2f7; color: #4a5568;
  font-size: 11px; display: flex; align-items: center; justify-content: center; flex: 0 0 20px;
}
.sk-pick-row.on .sk-pick-ic { background: #2563eb; color: #fff; }
.sk-pick-nm { font-size: 13px; color: #1f2328; font-weight: 500; flex: 0 0 auto; }
.sk-pick-ds { font-size: 11px; color: #6e6e6e; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.sk-pick-empty { font-size: 12px; color: #6e6e6e; padding: 6px 8px; }

/* 输入行：textarea + 发送按钮 */
.composer-row { display: flex; align-items: flex-end; gap: 10px; }
.composer-row .el-input { flex: 1; }
/* 范围选择器样式已随组件移入 components/ScopePicker.vue（F1） */
/* 模型工具栏（输入框左下角：推理强度 + 模型切换，靠左，无分割线） */
.model-toolbar {
  display: flex; align-items: center; justify-content: flex-start; gap: 6px;
}
/* 模型切换 / 推理强度下拉：透明底轻量 chip，hover/聚焦才出现底色 */
.model-select { flex-shrink: 0; min-width: 130px; max-width: 210px; }
.reason-select { flex-shrink: 0; min-width: 100px; max-width: 116px; }
.reason-select :deep(.el-select__wrapper),
.model-select :deep(.el-select__wrapper) {
  background: transparent;
  border-radius: 9px;
  box-shadow: none;
  min-height: 30px;
  padding: 0 8px;
  transition: background .18s ease, box-shadow .18s ease;
}
.reason-select :deep(.el-select__wrapper:hover),
.model-select :deep(.el-select__wrapper:hover) {
  background: var(--asc-surface-2);
}
.reason-select :deep(.el-select__wrapper.is-focused),
.model-select :deep(.el-select__wrapper.is-focused) {
  background: var(--asc-surface-2);
  box-shadow: 0 0 0 1.5px rgba(124, 92, 252, .45);
}
.reason-select :deep(.el-select__selected-item),
.model-select :deep(.el-select__selected-item) { font-weight: 500; color: var(--asc-text-2); }
.reason-select :deep(.el-select__placeholder),
.model-select :deep(.el-select__placeholder) { color: var(--asc-text-3); }
/* 思维链折叠块 */
.reasoning-box {
  margin-bottom: 12px;
  border-radius: 12px; background: var(--asc-card);
  box-shadow: 0 1px 3px rgba(0, 0, 0, .05); overflow: hidden;
}
.reasoning-toggle {
  display: flex; align-items: center; gap: 6px; padding: 8px 14px;
  font-size: 12.5px; color: var(--asc-text-2); cursor: pointer; user-select: none;
  transition: background .15s ease;
}
.reasoning-toggle:hover { background: var(--asc-primary-soft); }
.reasoning-label { font-weight: 500; }
.reasoning-thinking { font-size: 12px; color: var(--asc-primary); animation: blink 1.2s infinite; }
.reasoning-arrow { margin-left: auto; transition: transform .2s ease; color: var(--asc-text-3); }
.reasoning-arrow.open { transform: rotate(180deg); }
.reasoning-body {
  padding: 8px 14px 12px; border-top: 1px solid var(--asc-divider);
  font-size: 13px; color: var(--asc-text-3); line-height: 1.7;
  max-height: 240px; overflow-y: auto;
}
/* 发送按钮：圆形图标（主 CTA） */
.send-btn {
  width: 44px; height: 44px; border-radius: 50% !important;
  padding: 0 !important; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 12px rgba(124, 92, 252, .3);
  transition: transform .15s ease, box-shadow .15s ease;
}
.send-btn:hover { transform: translateY(-1px); box-shadow: 0 6px 16px rgba(124, 92, 252, .4); }
.send-btn:active { transform: translateY(0) scale(.96); box-shadow: 0 2px 8px rgba(124, 92, 252, .3); }

/* ===== 对话笔记弹窗（当前页查看/编辑） ===== */
.chat-note-dialog { border-radius: 14px; }
.cnd-head { display: flex; align-items: center; gap: 10px; }
.cnd-title { font-size: 17px; font-weight: 600; color: var(--asc-text); }
.cnd-tag {
  font-size: 11px; font-weight: 500; line-height: 1; padding: 4px 8px; border-radius: 6px;
  background: rgba(28, 145, 138, .12); color: #12837c; letter-spacing: .5px;
}
.cnd-sub { font-size: 12px; color: var(--asc-text-3); }
.cnd-field { margin-bottom: 14px; }
.cnd-label { display: block; font-size: 12.5px; color: var(--asc-text-2); margin-bottom: 6px; font-weight: 500; }
.cnd-field-head { display: flex; align-items: center; justify-content: space-between; }
.cnd-field-head .cnd-label { margin-bottom: 6px; }
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
  display: flex; align-items: center; gap: 4px; padding-top: 4px;
  border-top: 1px solid var(--asc-divider);
}
.cnd-sep { flex: 1; }
.cnd-footer { display: flex; align-items: center; justify-content: space-between; }
.cnd-hint { font-size: 12px; color: var(--asc-text-3); }

/* ===== 笔记 AI 加工（整条 + 划线选段） ===== */
.cnd-sel-bar {
  display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
  padding: 6px 10px; margin-top: 8px;
  background: var(--asc-surface-2); border-radius: 8px;
}
.cnd-sel-count { font-size: 12px; color: var(--asc-text-2); }
.cnd-sel-sep { width: 1px; height: 12px; background: var(--asc-border); margin: 0 4px; }
.cnd-sel-btn {
  border: none; background: transparent; cursor: pointer; font-family: inherit;
  font-size: 12.5px; color: var(--asc-primary); padding: 3px 8px; border-radius: 6px;
  transition: background .15s ease;
}
.cnd-sel-btn:hover { background: rgba(124, 92, 252, .10); }
.cnd-sel-btn.plain { color: var(--asc-text-2); }
.cnd-sel-btn.plain:hover { background: var(--asc-card); }
.cnd-ai {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin-bottom: 14px; padding: 8px 12px;
  background: rgba(124, 92, 252, .07);
  border-radius: 10px;
}
.cnd-ai-icon { display: inline-flex; color: var(--asc-primary); line-height: 0; }
.cnd-ai-label { font-size: 12px; font-weight: 500; color: var(--asc-primary); }
.cnd-ai-sep { width: 1px; height: 12px; background: rgba(124, 92, 252, .22); margin: 0 2px; }
.cnd-polish-block { margin-bottom: 14px; }
.cnd-polish-label {
  font-size: 11px; font-weight: 600; letter-spacing: 1.5px;
  color: var(--asc-text-3); margin-bottom: 6px;
}
.cnd-polish-new .cnd-polish-label { color: #0f6e56; }
.cnd-polish-text {
  font-size: 14px; line-height: 1.75; color: var(--asc-text);
  background: var(--asc-surface-2); border-radius: 8px;
  padding: 12px 14px; white-space: pre-wrap;
  max-height: 240px; overflow-y: auto;
}
.cnd-polish-new .cnd-polish-text { background: rgba(15, 110, 86, .07); }
.cnd-stream-cursor { color: var(--asc-primary); animation: cnd-blink 1s step-end infinite; }
@keyframes cnd-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
</style>