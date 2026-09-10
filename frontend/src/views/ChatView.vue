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
            <img :src="mascot" class="empty-mascot" alt="伴学猫头鹰" />
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
            <p class="greet-sub">你的 AI 学习搭子 · 向知识库提问，我来帮你答疑解惑</p>
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
              <div v-if="m._streaming && !m.content" class="thinking-row">
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
                  v-html="renderMd(m._reasoning || m.reasoning)"></div>
              </div>
              <div v-if="!m.kb_hit && m.content" class="kb-miss-bar">
                知识库中未找到相关内容，以下为通用回答
              </div>
              <div class="md-body" v-html="renderAnswer(m)" @click="onMdClick(m, $event)"></div>
              <span v-if="m._streaming" class="cursor">▍</span>
              <!-- 相关问题推荐（回答后生成，点击直接发问） -->
              <div v-if="!m._streaming && m.suggestions?.length" class="suggestions">
                <div class="suggestions-label">继续问</div>
                <div v-for="(q, i) in m.suggestions" :key="i" class="suggestion-chip"
                  @click="askSuggestion(q)">{{ q }}</div>
              </div>
              <!-- 引用来源（折叠为标识，点击展开） -->
              <div v-if="m.sources?.length" class="sources">
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
                        {{ { note: '笔记', summary: '摘要' }[s.ref_type] || '原文' }}
                      </el-tag>
                      <span class="source-title">{{ s.title }}</span>
                      <span v-if="s.page_no" class="source-page">P{{ s.page_no }}</span>
                    </div>
                    <div class="source-snippet">{{ s.snippet }}…</div>
                  </div>
                </div>
              </div>
              <!-- 回答底部操作：转笔记 + 复制全部内容 -->
              <div v-if="!m._streaming && m.content" class="answer-actions">
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
          <div v-show="!atBottom" class="scroll-btn" title="回到底部" @click="scrollBottom">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
              stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M19 12l-7 7-7-7"/></svg>
          </div>
        </div>
        <div class="input-inner">
        <div class="input-col">
          <!-- 检索范围（置于输入框上方，避免挤压底部） -->
          <div class="scope-row">
            <el-radio-group v-model="scope" size="small" class="scope-picker">
              <el-radio-button value="all">整个知识库</el-radio-button>
              <el-radio-button value="material">资料</el-radio-button>
              <el-radio-button value="note">笔记</el-radio-button>
              <el-radio-button value="folder">文件夹</el-radio-button>
            </el-radio-group>
            <!-- 指定条目：空 = 全部 -->
            <el-select v-if="scope === 'material'" v-model="selectedMaterialIds" multiple
              collapse-tags collapse-tags-tooltip :max-collapse-tags="2"
              placeholder="全部资料（可点选指定）" size="small" class="scope-select">
              <el-option v-for="m in materialOptions" :key="m.id" :label="m.title" :value="m.id" />
            </el-select>
            <el-select v-if="scope === 'note'" v-model="selectedNoteIds" multiple
              collapse-tags collapse-tags-tooltip :max-collapse-tags="2"
              placeholder="全部笔记（可点选指定）" size="small" class="scope-select">
              <el-option v-for="n in noteOptions" :key="n.id" :label="n.title" :value="n.id" />
            </el-select>
            <el-select v-if="scope === 'folder'" v-model="selectedFolderIds" multiple
              collapse-tags collapse-tags-tooltip :max-collapse-tags="2"
              placeholder="选择文件夹（含子文件夹）" size="small" class="scope-select">
              <el-option v-for="f in folderOptions" :key="f.id" :label="f.name" :value="f.id" />
            </el-select>
          </div>
          <div class="composer-row">
            <el-input v-model="question" type="textarea" :rows="2" resize="none"
              :placeholder="scopePlaceholder" @keydown.enter.exact.prevent="send" />
            <el-button type="primary" class="send-btn" circle :loading="asking" :disabled="!question.trim()"
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
            <el-select v-model="reasoningEffort" size="small"
              class="reason-select" title="推理模式（默认关闭）">
              <el-option label="关闭" value="" />
              <el-option label="快速" value="low" />
              <el-option label="标准" value="high" />
              <el-option label="深度" value="max" />
            </el-select>
            <el-select v-if="chatModels.length" v-model="selectedModelId" size="small"
              class="model-select" title="切换模型">
              <el-option v-for="m in chatModels" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
            </el-select>
          </div>
        </div>
        </div>
      </div>
    </main>

    <!-- 问答笔记查看/编辑弹窗（当前页打开，不跳知识库） -->
    <el-dialog v-model="noteDlg.show" width="640px" class="chat-note-dialog"
      :close-on-click-modal="false">
      <template #header>
        <div class="cnd-head">
          <span class="cnd-title">问答笔记</span>
          <span class="cnd-tag">问答笔记</span>
          <span class="cnd-sub">来自 AI 问答<template v-if="noteDlg.createdAt"> · {{ (noteDlg.createdAt || '').slice(0, 10) }}</template></span>
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
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch, h } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import mascot from '../assets/mascot.png'
import { ElMessage, ElNotification } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { chatApi, materialApi, kbApi, settingsApi, foldersApi, noteApi, reviewApi } from '../api'
import http, { errMsg } from '../api/http'

const md = new MarkdownIt({ breaks: true })
const renderMd = (t) => md.render(t || '')

// 渲染回答：把 [n] 引用编号替换成可点击角标（n 对应 sources 序号）
function renderAnswer(m) {
  let html = md.render(m.content || '')
  const sources = m.sources || []
  if (sources.length) {
    html = html.replace(/\[(\d+)\]/g, (full, n) => {
      const idx = parseInt(n, 10)
      if (idx >= 1 && idx <= sources.length) {
        return `<span class="cite" data-idx="${idx}" title="查看引用来源">[${n}]</span>`
      }
      return full
    })
  }
  return html
}

// 引用来源气泡（点击角标弹出，显示来源详情，点击跳转）
const citePop = reactive({ show: false, x: 0, y: 0, source: null })

// 点击引用角标：弹出气泡显示来源详情
function onMdClick(m, e) {
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
const selectedMaterialIds = ref([])
const selectedNoteIds = ref([])
const selectedFolderIds = ref([])
const sessionSearch = ref('')
// 模型切换：从配置的模型列表选，默认用问答模型
const chatModels = ref([])
const selectedModelId = ref('')
const configured = ref(true)   // 是否已配置 LLM 模型：未配置时空态给「配置引导」，默认 true 避免首屏闪烁
const reasoningEffort = ref('low')   // 推理：默认快速（low），''=关闭，high/max=更强

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

// 取消所有引用后自动切回「整个知识库」——否则停留在「资料/笔记」范围，
// 检索会被收窄（资料范围只查原文、笔记范围只查笔记），无法全库回答
watch(selectedMaterialIds, (v) => {
  if (scope.value === 'material' && v.length === 0) scope.value = 'all'
})
watch(selectedNoteIds, (v) => {
  if (scope.value === 'note' && v.length === 0) scope.value = 'all'
})
watch(selectedFolderIds, (v) => {
  if (scope.value === 'folder' && v.length === 0) scope.value = 'all'
})
const materialOptions = ref([])
const noteOptions = ref([])
const folderOptions = ref([])
const msgAreaRef = ref(null)

const scopePlaceholder = computed(() => ({
  all: '基于整个知识库提问（资料原文 + 笔记）…',
  material: selectedMaterialIds.value.length ? `在指定的 ${selectedMaterialIds.value.length} 份资料中检索回答…` : '在全部资料原文中检索回答…',
  note: selectedNoteIds.value.length ? `在指定的 ${selectedNoteIds.value.length} 条笔记中检索回答…` : '在全部笔记中检索回答…',
  folder: selectedFolderIds.value.length ? `在选中的 ${selectedFolderIds.value.length} 个文件夹（含子文件夹）中检索回答…` : '选择文件夹后，在其（含子文件夹）范围内检索回答…',
}[scope.value]))

const examples = ['用户价值公式怎么理解？', '需求管理的流程包含哪些环节？', 'RICE 模型怎么用？']

async function loadSessions() {
  const { data } = await chatApi.sessions()
  sessions.value = data
}

async function openSession(id) {
  currentId.value = id
  const { data } = await chatApi.messages(id)
  messages.value = (data || []).map(m => ({ ...m, _showReasoning: false }))
  scrollBottom()
}

function toggleReasoning(m) {
  m._showReasoning = !m._showReasoning
}

// 新会话：仅前端清空，不立即建库（提问时才由后端自动创建，避免留下空会话）
function newSession() {
  currentId.value = null
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

function scrollBottom() {
  nextTick(() => {
    if (msgAreaRef.value) msgAreaRef.value.scrollTop = msgAreaRef.value.scrollHeight
  })
}

function scrollToTop() {
  if (msgAreaRef.value) msgAreaRef.value.scrollTop = 0
}

// 滚动位置状态（控制回顶/回底按钮显隐）
const atTop = ref(true)
const atBottom = ref(false)
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
      ElMessage.info('该问答此前已转存过')
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '转笔记失败'))
  } finally {
    m._savingNote = false
  }
}

// 查看已转存的问答笔记：当前页弹窗查看/编辑（不跳知识库）
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

// ---------- 问答笔记弹窗：保存 / 删除 / 出复习卡 ----------
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
async function streamSSE(url, body, onToken, onDone, onError) {
  const base = import.meta.env.DEV ? 'http://127.0.0.1:8000' : ''
  const resp = await fetch(base + url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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
      if (ev === 'token') onToken?.(payload.t)
      else if (ev === 'done') onDone?.(payload)
      else if (ev === 'error') onError?.(payload.message)
    }
  }
}

function startEdit(m) {
  if (asking.value) return
  m._editing = true
  m._editText = m.content
}

function cancelEdit(m) {
  m._editing = false
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

async function send() {
  const q = question.value.trim()
  if (!q || asking.value) return
  question.value = ''
  asking.value = true

  const userMsg = { _tmp: 'u' + Date.now(), role: 'user', content: q }
  messages.value.push(userMsg)
  const aiMsg = reactive({ _tmp: 'a' + Date.now(), role: 'assistant', content: '', _reasoning: '', _showReasoning: false, kb_hit: true, sources: [], suggestions: [], _streaming: true })
  messages.value.push(aiMsg)
  scrollBottom()

  try {
    // dev 模式直连后端，绕过 vite 代理（代理会缓冲 SSE 流式响应）
    const base = import.meta.env.DEV ? 'http://127.0.0.1:8000' : ''
    const resp = await fetch(base + '/api/chat/ask/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: q,
        session_id: currentId.value,
        scope: scope.value,
        material_ids: scope.value === 'material' && selectedMaterialIds.value.length ? selectedMaterialIds.value : null,
        note_ids: scope.value === 'note' && selectedNoteIds.value.length ? selectedNoteIds.value : null,
        folder_ids: scope.value === 'folder' && selectedFolderIds.value.length ? selectedFolderIds.value : null,
        model_id: selectedModelId.value || null,
        reasoning_effort: reasoningEffort.value || null,
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
          userMsg.id = payload.user_message_id   // 记录后端消息 id（编辑时截断用）
          aiMsg.kb_hit = payload.kb_hit
          aiMsg.sources = payload.sources
        } else if (ev === 'reasoning') {
          aiMsg._reasoning += payload.t
          scrollBottom()
        } else if (ev === 'suggestions') {
          aiMsg.suggestions = payload.questions
        } else if (ev === 'token') {
          aiMsg.content += payload.t
          scrollBottom()
        } else if (ev === 'error') {
          throw new Error(payload.message)
        }
      }
    }
    aiMsg._streaming = false
    await loadSessions()
  } catch (e) {
    aiMsg._streaming = false
    aiMsg.content = aiMsg.content || ''
    ElMessage.error(e.message || '问答失败')
    if (!aiMsg.content) messages.value = messages.value.filter(m => m !== aiMsg)
  } finally {
    asking.value = false
  }
}

onMounted(async () => {
  // 每次进入默认新会话（不自动打开最近会话；首个问题会自动建会话）
  currentId.value = null
  messages.value = []
  await loadSessions()
  // 范围选择的条目选项
  const [{ data: mats }, { data: kb }, { data: folders }] = await Promise.all([
    materialApi.list(), kbApi.overview(), foldersApi.list(),
  ])
  materialOptions.value = mats.filter(m => m.parsed_status === 'success')
  noteOptions.value = kb.notes
  folderOptions.value = folders
  // 加载模型列表，默认选中问答模型
  try {
    const { data: st } = await settingsApi.get()
    chatModels.value = st.llm_models || []
    selectedModelId.value = st.chat_model_id || (chatModels.value[0]?.id || '')
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
.msg-area { flex: 1; overflow-y: auto; padding: 32px 40px 24px; position: relative; }
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
/* 消息操作（复制/编辑）：hover 显示 */
.msg-ops { display: flex; gap: 10px; justify-content: flex-end; margin-top: 6px; opacity: 0; transition: opacity .15s ease; }
.bubble:hover .msg-ops { opacity: 1; }
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
/* 输入行：textarea + 发送按钮 */
.composer-row { display: flex; align-items: flex-end; gap: 10px; }
.composer-row .el-input { flex: 1; }
/* 范围选择：安静文字 chip（无边框无分割线，弱化层级） */
.scope-row {
  display: flex; gap: 8px; align-items: center;
}
.scope-picker {
  display: inline-flex; flex-shrink: 0; gap: 2px;
}
.scope-picker :deep(.el-radio-button) { margin: 0; }
.scope-picker :deep(.el-radio-button__inner) {
  border: none !important; background: transparent !important; box-shadow: none !important;
  color: var(--asc-text-3); font-size: 12.5px; padding: 4px 11px;
  border-radius: 8px; transition: all .18s ease;
}
.scope-picker :deep(.el-radio-button__inner:hover) {
  background: var(--asc-surface-2) !important; color: var(--asc-text-2) !important;
}
.scope-picker :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: var(--asc-primary-soft) !important; color: var(--asc-primary) !important; font-weight: 600;
  box-shadow: none;
}
.scope-select { flex: 1; }
/* 选中的资料/笔记文件名标签：主题紫高亮 */
.scope-select :deep(.el-tag) {
  color: var(--asc-primary); font-weight: 500;
  background: var(--asc-primary-soft); border-color: rgba(124, 92, 252, .35);
}
.scope-select :deep(.el-tag .el-icon) { color: var(--asc-primary); }
/* 模型工具栏（输入框左下角：推理强度 + 模型切换，靠左，无分割线） */
.model-toolbar {
  display: flex; align-items: center; justify-content: flex-start; gap: 6px;
}
/* 模型切换 / 推理强度下拉：透明底轻量 chip，hover/聚焦才出现底色 */
.model-select { flex-shrink: 0; min-width: 130px; max-width: 210px; }
.reason-select { flex-shrink: 0; min-width: 100px; max-width: 116px; }
.reason-select :deep(.el-select__wrapper),
.model-select :deep(.el-select__wrapper),
.scope-select :deep(.el-select__wrapper) {
  background: transparent;
  border-radius: 9px;
  box-shadow: none;
  min-height: 30px;
  padding: 0 8px;
  transition: background .18s ease, box-shadow .18s ease;
}
.reason-select :deep(.el-select__wrapper:hover),
.model-select :deep(.el-select__wrapper:hover),
.scope-select :deep(.el-select__wrapper:hover) {
  background: var(--asc-surface-2);
}
.reason-select :deep(.el-select__wrapper.is-focused),
.model-select :deep(.el-select__wrapper.is-focused),
.scope-select :deep(.el-select__wrapper.is-focused) {
  background: var(--asc-surface-2);
  box-shadow: 0 0 0 1.5px rgba(124, 92, 252, .45);
}
.reason-select :deep(.el-select__selected-item),
.model-select :deep(.el-select__selected-item) { font-weight: 500; color: var(--asc-text-2); }
.reason-select :deep(.el-select__placeholder),
.model-select :deep(.el-select__placeholder),
.scope-select :deep(.el-select__placeholder) { color: var(--asc-text-3); }
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

/* ===== 问答笔记弹窗（当前页查看/编辑） ===== */
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