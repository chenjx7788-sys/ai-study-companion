<template>
  <div class="page podcast-page" v-loading="loading" element-loading-text="加载中...">
    <PageHead title="AI 播客" sub="把材料与笔记提炼成对话式知识简报，随时用耳朵复习">
      <template #icon>
        <svg viewBox="0 0 16 16" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
          <path d="M2.6 9.4V8a5.4 5.4 0 0 1 10.8 0v1.4" />
          <rect x="1.7" y="8.7" width="3.1" height="4.5" rx="1.55" />
          <rect x="11.2" y="8.7" width="3.1" height="4.5" rx="1.55" />
        </svg>
      </template>
      <span class="voice-chip" :class="voiceStatus.ok ? 'ok' : 'bad'" @click="checkVoice"
        :title="voiceStatus.text">
        <span class="dot"></span>{{ voiceStatus.label }}
      </span>
    </PageHead>

    <div class="podcast-grid">
      <!-- ============ 左栏：作品库 + 新建作品 ============ -->
      <div class="podcast-col col-left">
        <div class="panel panel-left">
          <div class="panel-head">
            <span class="panel-title">作品库</span>
            <span class="count-badge">{{ list.length }}</span>
          </div>
          <div class="work-new">
            <el-button type="primary" class="new-work-btn" @click="resetForm">
              <span class="plus">+</span>新建作品
            </el-button>
          </div>
          <div class="work-filter">
            <el-input v-model="workQuery" size="default" placeholder="搜索作品标题" clearable>
              <template #prefix>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
              </template>
            </el-input>
            <div class="seg-ctl mini">
              <span v-for="f in WORK_FILTERS" :key="f.id" class="seg-item"
                :class="{ on: workFilter === f.id }" @click="workFilter = f.id">{{ f.label }}</span>
            </div>
          </div>
          <div class="work-list">
            <p class="work-empty" v-if="!visibleWorks.length">没有匹配的作品</p>
            <div v-for="p in visibleWorks" :key="p.id" class="work-item"
              :class="{ on: current && current.id === p.id }" @click="openWork(p)">
              <span class="work-dot" :class="workState(p)" :title="workStateText(p)"></span>
              <div class="work-main">
                <div class="work-title">{{ p.title }}</div>
                <div class="work-meta">
                  <span>{{ sourceLabel(p.source_type) }}</span>
                  <span class="fail-tag" v-if="p.status === 'failed'">合成失败</span>
                  <span v-if="p.has_audio">{{ fmtDur(p.duration_sec) }}</span>
                  <span v-else class="pending">{{ p.status === 'done' ? '音频丢失' : '待合成' }}</span>
                </div>
              </div>
              <span class="work-edit" @click.stop="renameWork(p)" title="重命名">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              </span>
              <span class="work-del" @click.stop="removeWork(p)" title="删除">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- ============ 中栏：生成台 ============ -->
      <div class="podcast-col col-center">
        <div class="panel panel-left">
          <div class="panel-head">
            <span class="panel-title">生成台</span>
            <el-button v-if="current" text size="small" @click="resetForm">新建</el-button>
          </div>

          <!-- 选中作品时生成台跟随该作品的来源与参数。这里始终显示「来自哪里」，
               否则用户看不出面板里填的是哪条作品的来源，也意识不到参数被回填了 -->
          <div class="follow-bar" v-if="current">
            <div class="fb-main">
              <div class="fb-line">
                <span class="fb-name" :title="current.title">{{ current.title }}</span>
                <span class="fb-tag">当前作品</span>
              </div>
              <div class="fb-line fb-src-line">
                <span class="fb-src" :title="currentSourceText">{{ currentSourceText }}</span>
                <span class="fb-bad" v-if="sourceMissing">来源已失效</span>
              </div>
            </div>
            <p class="fb-tip">改动后点「重新生成脚本」会基于新参数重做</p>
          </div>

          <div class="form-section">
            <div class="section-label">内容来源</div>

            <div class="field">
              <label>来源</label>
              <div class="seg-ctl">
                <span v-for="t in sourceTypes" :key="t.id" class="seg-item"
                  :class="{ on: form.source_type === t.id }" @click="onSourceType(t.id)">{{ t.label }}</span>
              </div>
            </div>

            <div class="field" v-if="form.source_type === 'article'">
              <label>选择文档</label>
              <el-select v-model="form.ref_id" filterable size="small" placeholder="选择一篇文档"
                style="width:100%" :disabled="!materialOptions.length">
                <el-option v-for="m in materialOptions" :key="m.id"
                  :label="m.label" :value="m.id" />
              </el-select>
              <p class="hint" v-if="!(options.materials || []).length">还没有已解析完成的文档</p>
            </div>

            <div class="field" v-else-if="form.source_type === 'notes'">
              <label>选择笔记<span class="hint-inline">可多选，按主题归并</span></label>
              <el-select v-model="form.note_ids" multiple filterable collapse-tags collapse-tags-tooltip
                size="small" placeholder="选择笔记" style="width:100%">
                <el-option v-for="n in noteOptions" :key="n.id" :label="n.label" :value="n.id" />
              </el-select>
            </div>

            <div class="field" v-else-if="form.source_type === 'highlights'">
              <label>选择文档</label>
              <el-select v-model="form.ref_id" filterable size="small" placeholder="选择有划线的文档"
                style="width:100%">
                <el-option v-for="m in highlightOptions" :key="m.id"
                  :label="m.label" :value="m.id" />
              </el-select>
              <label style="margin-top:10px">划线类型</label>
              <div class="seg-ctl">
                <span v-for="(sem, key) in options.highlight_semantics" :key="key" class="seg-item"
                  :class="{ on: form.highlight_colors.includes(key) }" @click="toggleColor(key)">
                  {{ sem.label }}
                </span>
              </div>
            </div>

            <div class="field" v-else>
              <label>今日到期卡片</label>
              <div class="due-box" :class="{ empty: !options.review_due }">
                <b>{{ options.review_due }}</b> 张待复习
                <span v-if="!options.review_due">— 今天没有到期的卡片</span>
              </div>
              <p class="hint">卡片的问题与答案天然构成「主持人提问 + 专家解答」，适合通勤时听</p>
            </div>
          </div>

          <div class="form-section">
            <div class="section-label">输出设置</div>

            <div class="field">
              <label>风格</label>
              <div class="seg-ctl">
                <span v-for="s in options.styles" :key="s.id" class="seg-item"
                  :class="{ on: form.style === s.id }" @click="form.style = s.id">{{ s.label }}</span>
              </div>
              <p class="hint">{{ styleDesc }}</p>
            </div>

            <div class="field">
              <label>时长</label>
              <div class="seg-ctl">
                <span v-for="l in options.lengths" :key="l.minutes" class="seg-item"
                  :class="{ on: form.target_minutes === l.minutes }"
                  @click="form.target_minutes = l.minutes">{{ l.minutes }} 分钟</span>
              </div>
            </div>

            <div class="field">
              <label>音色<span class="hint-inline">点 ▶ 可试听每个音色</span></label>
              <div class="voice-row">
                <span class="voice-role">主持人</span>
                <el-select v-model="form.voice_map.host" size="small" style="flex:1">
                  <el-option v-for="v in options.voices" :key="v.id"
                    :label="v.name" :value="v.id">
                    <div class="vo-row">
                      <span class="vo-txt">{{ v.name }}<i>{{ v.desc }}</i></span>
                      <span class="vo-play" :title="`试听 ${v.name}`" @click.stop.prevent="previewVoice(v.id)">
                        <i v-if="previewId === v.id && previewBusy" class="vo-spin"></i>
                        <i v-else-if="previewId === v.id" class="vo-stop"></i>
                        <i v-else class="vo-tri"></i>
                      </span>
                    </div>
                  </el-option>
                </el-select>
                <button type="button" class="try-btn" title="试听当前主持人音色"
                  @click="previewVoice(form.voice_map.host)">
                  <i v-if="previewId === form.voice_map.host && previewBusy" class="vo-spin"></i>
                  <i v-else-if="previewId === form.voice_map.host" class="vo-stop"></i>
                  <i v-else class="vo-tri"></i>
                </button>
              </div>
              <!-- 单人精讲只有一个说话人 → 不显示专家音色（选了也不会被用上） -->
              <div class="voice-row" v-if="form.style !== 'solo'">
                <span class="voice-role">专家</span>
                <el-select v-model="form.voice_map.expert" size="small" style="flex:1">
                  <el-option v-for="v in options.voices" :key="v.id"
                    :label="v.name" :value="v.id">
                    <div class="vo-row">
                      <span class="vo-txt">{{ v.name }}<i>{{ v.desc }}</i></span>
                      <span class="vo-play" :title="`试听 ${v.name}`" @click.stop.prevent="previewVoice(v.id)">
                        <i v-if="previewId === v.id && previewBusy" class="vo-spin"></i>
                        <i v-else-if="previewId === v.id" class="vo-stop"></i>
                        <i v-else class="vo-tri"></i>
                      </span>
                    </div>
                  </el-option>
                </el-select>
                <button type="button" class="try-btn" title="试听当前专家音色"
                  @click="previewVoice(form.voice_map.expert)">
                  <i v-if="previewId === form.voice_map.expert && previewBusy" class="vo-spin"></i>
                  <i v-else-if="previewId === form.voice_map.expert" class="vo-stop"></i>
                  <i v-else class="vo-tri"></i>
                </button>
              </div>
              <!-- 双人对话里两个角色同音色 → 听不出谁在说谁（像自言自语） -->
              <p class="hint warn" v-if="sameVoice">
                主持人与专家选了同一个音色，对话会分不清谁在说谁。换一个音色区分开。
              </p>
            </div>

            <div class="field">
              <label>背景音乐<span class="hint-inline">点 ▶ 可试听</span></label>
              <div class="bgm-row">
                <el-select v-model="form.bgm_id" size="small" style="flex:1"
                  placeholder="不加背景音乐" @change="persistBgm">
                  <el-option label="不加背景音乐" value="" />
                  <el-option v-for="t in bgmSelectOptions" :key="t.id"
                    :label="t.name" :value="t.id">
                    <div class="vo-row">
                      <span class="vo-txt">{{ t.name }}<i>{{ t.desc }}</i></span>
                      <span class="vo-play" :title="`试听 ${t.name}`"
                        @click.stop.prevent="previewBgm(t.id)">
                        <i v-if="previewId === t.id && previewBusy" class="vo-spin"></i>
                        <i v-else-if="previewId === t.id" class="vo-stop"></i>
                        <i v-else class="vo-tri"></i>
                      </span>
                      <!-- 只有自己上传的能删；内置氛围音后端也会拒绝 -->
                      <span class="vo-del" v-if="t.kind === 'user' && !t._ghost"
                        :title="`删除《${t.name}》`" @click.stop.prevent="removeBgmTrack(t)">×</span>
                    </div>
                  </el-option>
                </el-select>
                <button type="button" class="try-btn" title="试听当前背景音乐"
                  :disabled="!form.bgm_id" @click="previewBgm(form.bgm_id)">
                  <i v-if="previewId === form.bgm_id && previewBusy" class="vo-spin"></i>
                  <i v-else-if="previewId === form.bgm_id" class="vo-stop"></i>
                  <i v-else class="vo-tri"></i>
                </button>
                <button type="button" class="try-btn" title="上传自己的音乐（mp3/wav/m4a/flac）"
                  :disabled="bgmUploading" @click="triggerBgmUpload">
                  <i v-if="bgmUploading" class="vo-spin"></i>
                  <span v-else class="bgm-plus">+</span>
                </button>
                <input ref="bgmFileEl" type="file" accept="audio/*" class="bgm-file"
                  @change="onBgmFile" />
              </div>
              <div class="bgm-vol" v-if="form.bgm_id">
                <span class="bgm-vol-label">音量</span>
                <el-slider v-model="form.bgm_volume" :min="bgmVolumeRange.min"
                  :max="bgmVolumeRange.max" :step="1" size="small"
                  :show-tooltip="false" @change="persistBgm" />
                <span class="bgm-vol-val">{{ form.bgm_volume }} dB</span>
              </div>
              <!-- 作品引用的 BGM 已被删除：不自动改作品（那是静默破坏），
                   给一个明确的自愈入口，否则用户只会看到「重新合成失败」 -->
              <p class="hint warn" v-if="bgmMissing">
                这条作品引用的背景音乐已被删除，重新合成会失败。
                <el-button text size="small" type="primary" @click="clearMissingBgm">改为不加背景音乐</el-button>
              </p>
              <p class="hint">{{ form.bgm_id
                ? '讲解时自动压低、停顿处再浮现，不会抢话；改动后点「重新合成」生效'
                : '可不加；也能上传自己的音乐，服务端会自动统一响度' }}</p>
            </div>
            <audio ref="previewAudioEl" @playing="previewBusy = false"
              @ended="stopPreview" @error="onPreviewError"></audio>
          </div>

          <div class="form-section">
            <div class="field">
              <label>附加要求<span class="hint-inline">可选</span></label>
              <div class="inst-presets">
                <span v-for="t in instructionPresets" :key="t" class="preset-chip"
                  @click="applyPreset(t)">{{ t }}</span>
              </div>
              <el-input v-model="form.instruction" type="textarea" :rows="2" size="small"
                placeholder="例如：多举工作中的例子 / 侧重方法论" />
            </div>

            <div class="field mode-row">
              <el-checkbox v-model="fastMode" size="small">快速模式</el-checkbox>
              <span class="hint-inline">跳过知识简报，直接出脚本（更快，信息密度略降）</span>
            </div>

            <el-button type="primary" class="generate-btn" :loading="generating" @click="generate">
              {{ generating ? '正在生成脚本...' : (current ? '重新生成脚本' : '生成脚本') }}
            </el-button>

            <!-- 生成要过 1~2 次 LLM（每次 20-60s）：只写「正在生成脚本…」用户无法判断
                 是在跑还是卡死。后端的阶段进度通过 SSE 逐步回传（含字数校正轮次）。 -->
            <div class="gen-progress" v-if="generating && genProgress.label">
              <span class="sp-text">{{ genProgress.label }}
                <i v-if="genProgress.total">（第 {{ genProgress.index }} / {{ genProgress.total }} 步）</i>
              </span>
              <div class="sp-bar">
                <div class="sp-fill"
                  :style="{ width: (genProgress.total
                    ? Math.round(genProgress.index / genProgress.total * 100) : 0) + '%' }"></div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ============ 右栏：作品内容 ============ -->
      <div class="podcast-col col-right">
        <div class="empty-guide" v-if="!current">
          <img :src="mascot" class="eg-img" alt="伴学猫头鹰" />
          <h3 class="eg-title">还没有选中的作品</h3>
          <p class="eg-sub">三步做出一段可以听的复习音频</p>
          <div class="eg-steps">
            <div class="eg-step"><b>1</b><span>在左侧选好内容来源与输出参数</span></div>
            <div class="eg-step"><b>2</b><span>点「生成脚本」，得到主持人 × 专家的对话稿</span></div>
            <div class="eg-step"><b>3</b><span>逐句校对后点「合成音频」，成品出现在作品库</span></div>
          </div>
        </div>

        <template v-else>
          <!-- 音频卡片 -->
          <div class="audio-card" :class="{ stale: audioMismatch }" v-if="current.has_audio">
            <div class="ac-stale" v-if="audioMismatch">
              <div class="ac-stale-icon">!</div>
              <div class="ac-stale-body">
                <b>音频对应的是修改前的文案</b>
                <span>脚本已变更，现在听到的是上一版。点「重新合成」会自动保存修改并生成与文案一致的音频。</span>
              </div>
            </div>
            <div class="ac-top">
              <div class="ac-info">
                <div class="ac-title">
                  <span class="ac-title-text">{{ current.title }}</span>
                  <span class="ac-badge" v-if="audioMismatch">上一版</span>
                </div>
                <div class="ac-meta">{{ current.segment_count }} 句 · {{ fmtDur(current.duration_sec) }}
                  · {{ (current.audio_bytes / 1024 / 1024).toFixed(1) }} MB
                  <template v-if="current.bgm_id"> · {{ current.bgm_name || '未知曲目'
                    }}<span class="ac-bgm-gone" v-if="current.bgm_missing">（已失效）</span></template>
                </div>
              </div>
              <div class="ac-ops">
                <el-button size="small" text title="打开位置" @click="reveal">
                  <svg class="ac-op-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                  <span class="ac-op-text">打开位置</span>
                </el-button>
                <el-button size="small" text title="复制路径" @click="copyPath">
                  <svg class="ac-op-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                  <span class="ac-op-text">复制路径</span>
                </el-button>
                <el-button size="small" text title="导出文稿" @click="exportScript">
                  <svg class="ac-op-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><line x1="9" y1="15" x2="15" y2="15"/></svg>
                  <span class="ac-op-text">导出文稿</span>
                </el-button>
                <el-button size="small" text title="导出字幕" :disabled="!hasTimestamps"
                  @click="exportSrt">
                  <svg class="ac-op-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/></svg>
                  <span class="ac-op-text">导出字幕</span>
                </el-button>
              </div>
            </div>
            <div class="ac-player">
              <button class="play-btn" @click="togglePlay">
                <svg v-if="!playing" width="18" height="18" viewBox="0 0 14 14"><path d="M3 1.8v10.4L11.5 7z" fill="currentColor"/></svg>
                <svg v-else width="18" height="18" viewBox="0 0 14 14"><rect x="2.6" y="2" width="3.2" height="10" rx="1" fill="currentColor"/><rect x="8.2" y="2" width="3.2" height="10" rx="1" fill="currentColor"/></svg>
              </button>
              <span class="ptime ptime-current">{{ fmtDur(Math.floor(playerTime)) }}</span>
              <div class="progress" ref="progressRef" @click="seek">
                <div class="bar" :style="{ width: progressPct + '%' }"></div>
              </div>
              <span class="ptime">{{ fmtDur(current.duration_sec) }}</span>
              <div class="player-extras">
                <button class="skip-btn" title="后退 15 秒" @click="skip(-15)">−15s</button>
                <button class="seg-nav" title="上一句" @click="stepSeg(-1)"
                  :disabled="!hasTimestamps">‹</button>
                <button class="seg-nav" title="下一句" @click="stepSeg(1)"
                  :disabled="!hasTimestamps">›</button>
                <el-select v-model="playRate" size="small" style="width:76px" @change="onRateChange">
                  <el-option label="0.9x" value="0.9" />
                  <el-option label="1.0x" value="1" />
                  <el-option label="1.1x" value="1.1" />
                  <el-option label="1.25x" value="1.25" />
                </el-select>
              </div>
            </div>
            <audio ref="audioRef" :src="audioSrc" preload="metadata"
              @timeupdate="onTimeUpdate" @loadedmetadata="onMeta" @ended="playing = false"></audio>
          </div>

          <!-- 脚本工坊 -->
          <div class="panel workshop">
            <div class="ws-head">
              <div class="tabs">
                <span class="tab" :class="{ on: tab === 'script' }" @click="tab = 'script'">
                  对话脚本<span class="tab-num">{{ script.length }}</span>
                </span>
                <span class="tab" :class="{ on: tab === 'brief' }" @click="tab = 'brief'"
                  v-if="current.brief">知识简报</span>
              </div>
              <div class="ws-ops">
                <!-- 统计跟着当前 tab 走：看简报时显示简报字数，不显示脚本的预估时长 -->
                <span class="ws-stat">{{ tab === 'brief'
                  ? `${briefChars} 字`
                  : `${scriptChars} 字 · 预估 ${fmtDur(estimateSec)}` }}</span>
                <span class="ws-sep" aria-hidden="true"></span>
                <!-- 转笔记：产物沉到笔记里（材料无关，落知识库「按笔记」）。
                     简报与脚本是两条独立笔记，所以按钮组跟着 tab 切换。 -->
                <template v-if="tab === 'script'">
                  <el-button v-if="!scriptNote" size="small" text type="primary"
                    :disabled="noteBusy" @click="scriptToNote">转笔记</el-button>
                  <template v-else>
                    <el-button size="small" text type="success"
                      @click="openScriptNote(scriptNote.id)">✓ 已转笔记 · 查看</el-button>
                    <el-button v-if="scriptStale" size="small" text type="warning"
                      :disabled="noteBusy" @click="scriptToNote">脚本已更新 · 更新笔记</el-button>
                  </template>
                </template>
                <template v-else>
                  <el-button v-if="!briefNote" size="small" text type="primary"
                    :disabled="noteBusy" @click="briefToNote">转笔记</el-button>
                  <template v-else>
                    <el-button size="small" text type="success"
                      @click="openBriefNote(briefNote.id)">✓ 已转笔记 · 查看</el-button>
                    <el-button v-if="briefStale" size="small" text type="warning"
                      :disabled="noteBusy" @click="briefToNote">简报已更新 · 更新笔记</el-button>
                  </template>
                </template>
                <el-button size="small" :loading="synthesizing" @click="synthesize">
                  {{ synthesizing
                    ? (synthProgress.total
                      ? `合成中 ${synthProgress.done}/${synthProgress.total}`
                      : '合成中...')
                    : ((current.has_audio || current.status === 'failed') ? '重新合成' : '合成音频') }}
                </el-button>
                <el-button size="small" type="primary" @click="saveScript" :disabled="!dirty">
                  {{ dirty ? '保存修改' : '已保存' }}
                </el-button>
              </div>
            </div>

            <!-- 逐句合成要 30-60s（10 分钟档更久）：只写「合成中…」用户无法判断
                 是在跑还是卡死，而进度本来就有（每合成完一句回调一次） -->
            <div class="synth-progress" v-if="synthesizing && synthProgress.total">
              <div class="sp-bar">
                <div class="sp-fill"
                  :style="{ width: Math.round(synthProgress.done / synthProgress.total * 100) + '%' }"></div>
              </div>
              <span class="sp-text">正在合成 {{ synthProgress.done }} / {{ synthProgress.total }} 句</span>
            </div>

            <p class="ws-tip" v-if="!current.has_audio">
              脚本可逐句修改：点角色名切换主持人/专家，点文字直接编辑。
              确认满意后再点「合成音频」——不点就不会消耗合成时间。
            </p>
            <p class="ws-tip error" v-if="current.status === 'failed' && current.error">
              {{ current.error }}
            </p>
            <p class="ws-tip warn" v-if="dirty">
              {{ current.has_audio
                ? '脚本已修改，上方音频仍是上一版。点「重新合成」会自动保存修改并生成与文案一致的音频。'
                : '脚本有未保存的修改，点「保存修改」即可保存。' }}
            </p>

            <!-- 对话脚本 -->
            <div class="seg-list" v-if="tab === 'script'">
              <div v-for="(seg, i) in script" :key="i" class="seg"
                :class="[seg.speaker, { active: i === activeIndex }]"
                :ref="(el) => setSegRef(el, i)" @click="seekToSeg(i)">
                <div class="seg-bubble">
                  <div class="seg-head">
                    <span class="role" @click.stop="toggleSpeaker(i)"
                      :title="`点击切换为${seg.speaker === 'host' ? '专家' : '主持人'}`">
                      <span class="role-avatar" :class="seg.speaker"></span>
                      {{ seg.speaker === 'host' ? '主持人' : '专家' }}
                      <span class="role-voice" v-if="seg.voice_name">· {{ seg.voice_name }}</span>
                      <span class="seg-len" v-if="(seg.text || '').length > MAX_SEG_CHARS">
                        · {{ (seg.text || '').length }} 字，超出会被截断
                      </span>
                    </span>
                    <span class="seg-ops" @click.stop>
                      <button class="seg-btn" title="编辑" @click="editingIdx = editingIdx === i ? -1 : i">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                      </button>
                      <button class="seg-btn seg-btn-danger" title="删除" @click="removeSeg(i)">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                      </button>
                    </span>
                  </div>
                  <div v-if="editingIdx !== i" class="seg-text">{{ seg.text }}</div>
                  <el-input v-else v-model="seg.text" type="textarea"
                    :autosize="{ minRows: 2, maxRows: 8 }" size="small" @click.stop />
                </div>
              </div>

              <div class="add-seg" @click="addSeg">
                <span class="add-seg-icon">+</span>
                <span>新增一句</span>
              </div>
            </div>

            <!-- 知识简报 -->
            <div class="brief-box" v-else v-html="renderedBrief"></div>
          </div>
        </template>
      </div>
    </div>
  </div>
      <!-- 笔记查看弹窗：转笔记后「查看」在当前页打开，不再跳转知识库 -->
      <NoteEditorDialog v-model="scriptView.show" :note-id="scriptView.id" @changed="loadAiNotes" />
      <NoteEditorDialog v-model="briefView.show" :note-id="briefView.id" @changed="loadAiNotes" />
</template>

<script setup>
import { ref, computed, reactive, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownIt from 'markdown-it'
import mascot from '../assets/mascot.png'
import { podcastApi, noteApi } from '../api'
import { errMsg } from '../api/http'
import { textHash } from '../utils/hash'
import { streamSSE } from '../utils/sse'
import NoteEditorDialog from '../components/NoteEditorDialog.vue'
import PageHead from '../components/PageHead.vue'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const loading = ref(true)
const generating = ref(false)
const synthesizing = ref(false)
// 流式合成的逐句进度（total=0 表示还没收到第一个进度事件）
const synthProgress = ref({ done: 0, total: 0 })
// 流式生成脚本的阶段进度（label 为空表示还没收到第一个阶段事件）
const genProgress = ref({ label: '', index: 0, total: 0 })

const options = ref({
  materials: [], notes: [], highlight_materials: [], voices: [],
  // 与后端 STYLE_PRESETS / LENGTH_PRESETS 同步；加载完成前用它渲染出骨架，避免闪一下
  styles: [
    { id: 'dialogue', label: '知识对谈', desc: '主持人提问 + 专家解答' },
    { id: 'solo', label: '单人精讲', desc: '一个人从头讲到尾，合成时间约减半' },
  ],
  lengths: [{ minutes: 3, label: '3 分钟精华' },
            { minutes: 5, label: '5 分钟轻听' },
            { minutes: 10, label: '10 分钟深度' }],
  highlight_semantics: {}, review_due: 0,
  default_style: 'dialogue',
  // 单句字数上限由后端下发（不再手抄一份，避免两处常量漂移）
  max_seg_chars: 400,
})
const list = ref([])
const current = ref(null)

const form = reactive({
  source_type: 'article',
  // 生成风格：dialogue 知识对谈 / solo 单人精讲。属「生成期参数」——
  // 改了要点「重新生成脚本」才生效（与时长、音色同层）。
  style: 'dialogue',
  // 单选取值（单篇文档 / 有划线的文档）与多选取值分开存：
  // 二者共用同一字段时，单选会把数组替换成数字，导致 .length / .map 失效
  ref_id: null,
  note_ids: [],
  highlight_colors: ['green', 'yellow', 'blue'],
  target_minutes: 3,
  voice_map: { host: '', expert: '' },
  instruction: '',
  // 背景音乐：bgm_id 为空表示不加。改它会立刻写进作品，从而让后端指纹
  // 把已合成的音频判为「上一版」（不需要等下一次合成）。
  bgm_id: '',
  bgm_volume: -20,
})
const fastMode = ref(false)

// ---------- 附加要求预设 ----------
const instructionPresets = [
  '多举生活中的例子',
  '侧重方法论和 actionable 建议',
  '语言更口语化，像朋友聊天',
  '控制在目标时长内，不要超时',
  '适合通勤时听，节奏紧凑'
]
function applyPreset(text) {
  const cur = form.instruction || ''
  form.instruction = cur ? `${cur}；${text}` : text
}

// ---------- 作品库搜索 / 筛选 ----------
// 作品攒到几十条后「找到那一条」比生成还费劲，所以搜索与状态筛选都放在列表上方。
const workQuery = ref('')
const workFilter = ref('all')
const WORK_FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'audio', label: '可播放' },
  { id: 'todo', label: '待合成' },
  { id: 'failed', label: '失败' },
]
const visibleWorks = computed(() => {
  const q = workQuery.value.trim().toLowerCase()
  return (list.value || []).filter((p) => {
    if (q && !(p.title || '').toLowerCase().includes(q)) return false
    if (workFilter.value === 'audio') return !!p.has_audio
    if (workFilter.value === 'todo') return !p.has_audio && p.status !== 'failed'
    if (workFilter.value === 'failed') return p.status === 'failed'
    return true
  })
})

const script = ref([])
const brief = ref('')
const tab = ref('script')
const editingIdx = ref(-1)
const dirty = ref(false)

// 单句文本上限：**由后端 options 下发**（原来是前端手抄一份 400，两处常量迟早漂移）。
// 超长句后端会截断（保存完才发现句尾少了半截），所以这里提前把字数显示出来，
// 并在保存 / 合成前确认一次，把「静默」变成「用户知情」。
const MAX_SEG_CHARS = computed(() => options.value.max_seg_chars || 400)
const overlongSegs = computed(() =>
  script.value.filter((s) => (s.text || '').trim().length > MAX_SEG_CHARS.value))

// 播放器
const audioRef = ref(null)
const progressRef = ref(null)
const playing = ref(false)
const playerTime = ref(0)
const playRate = ref('1')

const sourceTypes = [
  { id: 'article', label: '单篇文档' },
  { id: 'notes', label: '笔记' },
  { id: 'highlights', label: '三色划线' },
  { id: 'review', label: '错题卡片' },
]

const voiceStatus = ref({ ok: true, label: '语音服务', text: '点击检测语音服务' })

// 当前风格的说明（用户不必点开才知道「单人精讲」是什么）
const styleDesc = computed(() => {
  const s = (options.value.styles || []).find((x) => x.id === form.style)
  return s?.desc || ''
})

// 双人对话里两个角色选了同一个音色 → 听起来像自言自语，听不出谁在说谁。
// 单人精讲只有一个说话人，这条提示不适用（专家音色在选择器里也已被隐藏）。
const sameVoice = computed(() =>
  form.style !== 'solo' && !!form.voice_map.host
  && form.voice_map.host === form.voice_map.expert)

// 有没有可用的逐句时间戳（= 合成过、且脚本没被改过）
const hasTimestamps = computed(() =>
  (script.value || []).some((s) => s.start_ms != null))

// ---------- 背景音乐 ----------
const bgmTracks = ref({ builtin: [], mine: [], volume: { min: -40, max: -3, default: -20 } })
const bgmUploading = ref(false)
const bgmFileEl = ref(null)
const bgmVolumeRange = computed(() => bgmTracks.value.volume || { min: -40, max: -3, default: -20 })

const bgmSelectOptions = computed(() => {
  const t = bgmTracks.value || {}
  const list = []
  for (const b of t.builtin || []) {
    list.push({ id: b.id, name: b.name, desc: b.desc, kind: 'builtin' })
  }
  for (const m of t.mine || []) {
    list.push({ id: m.id, name: m.name, desc: `${m.seconds || 0} 秒 · 我上传的`, kind: 'user' })
  }
  // 作品可能引用一首已被删除的 BGM：补占位项，否则下拉会显示裸 id（如 u-3f2a1b）
  const cur = form.bgm_id
  if (cur && !list.some((x) => x.id === cur)) {
    list.push({
      id: cur, name: current.value?.bgm_name || '已失效的背景音乐',
      desc: '已被删除，请重新选择', _ghost: true,
    })
  }
  return list
})

const sourceLabel = (t) => (sourceTypes.find((s) => s.id === t) || { label: t }).label

// 引用的背景音乐是否已被删除。只对用户上传的（u-）判定：内置氛围音在
// exists()/resolve() 里会按需现场生成，拿列表里有没有它来判断会误报。
const bgmMissing = computed(() => {
  const cur = form.bgm_id
  if (!cur || !cur.startsWith('u-')) return false
  return !(bgmTracks.value.mine || []).some((x) => x.id === cur)
})

// 删除自己上传的背景音乐。**先告诉用户哪些作品在用它** —— 删完这些作品的
// bgm_id 会悬空、重新合成必然失败；删完也不自动改作品（那是静默破坏），
// 由用户在作品侧显式点「改为不加背景音乐」。
async function removeBgmTrack(t) {
  const refs = (list.value || []).filter((p) => p.bgm_id === t.id)
  const usage = refs.length
    ? `有 ${refs.length} 条作品正在使用它：`
      + `${refs.slice(0, 3).map((p) => `《${p.title}》`).join('、')}`
      + `${refs.length > 3 ? ' 等' : ''}。删除后这些作品需要重新选择背景音乐，否则重新合成会失败。`
    : '目前没有作品在使用它。'
  try {
    await ElMessageBox.confirm(usage, `删除背景音乐《${t.name}》？`,
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch {
    return
  }
  try {
    await podcastApi.removeBgm(t.id)
    await loadBgm()
    await loadList()        // 引用它的作品会被标成「已失效」，列表需要同步
    ElMessage.success('已删除背景音乐')
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  }
}

// 悬空 BGM 的自愈：改成「不加背景音乐」并立刻写回作品（没有作品时只清表单）
async function clearMissingBgm() {
  form.bgm_id = ''
  if (!current.value) return
  if (await persistBgm()) ElMessage.success('已改为不加背景音乐')
}

// ---------- 生成台 ↔ 当前作品的关联 ----------
// 后端在生成时会把来源快照写进 podcasts.source_refs（{type,id,title[,colors]|count}），
// 这里据此把「这条作品来自哪里」显示出来，并把参数回填进生成台。

const COLOR_LABELS = { green: '绿色', yellow: '黄色', blue: '蓝色' }

function refTitleById(id) {
  const refs = current.value?.source_refs || []
  const hit = refs.find((r) => Number(r.id) === Number(id))
  return hit?.title || ''
}

// 给下拉补一个「已失效」占位项。作品的来源快照可能指向已被删除的材料/笔记，
// 不补的话 el-select 会显示裸 id，用户看不懂那是什么。
function withGhost(list, id, title) {
  if (id == null || list.some((x) => x.id === id)) return list
  return [...list, { id, label: `${title || '已失效的来源'}（已失效）`, _ghost: true }]
}

const materialOptions = computed(() => {
  const list = (options.value.materials || []).map((m) => ({ id: m.id, label: m.title }))
  return withGhost(list, form.ref_id, (current.value?.source_refs || [])[0]?.title)
})

const noteOptions = computed(() => {
  const list = (options.value.notes || []).map((n) => ({ id: n.id, label: n.title }))
  const ghosts = form.note_ids
    .filter((id) => !list.some((x) => x.id === id))
    .map((id) => ({ id, label: `${refTitleById(id) || '已失效的笔记'}（已失效）`, _ghost: true }))
  return [...list, ...ghosts]
})

const highlightOptions = computed(() => {
  const list = (options.value.highlight_materials || [])
    .map((m) => ({ id: m.id, label: `${m.title}（${m.count} 条）` }))
  return withGhost(list, form.ref_id, (current.value?.source_refs || [])[0]?.title)
})

// 一句话说清当前作品的来源
const currentSourceText = computed(() => {
  const p = current.value
  if (!p) return ''
  const refs = Array.isArray(p.source_refs) ? p.source_refs : []
  const label = sourceLabel(p.source_type)
  const titles = refs.map((r) => r.title).filter(Boolean)
  if (p.source_type === 'review') {
    const n = refs[0]?.count || 0
    return n ? `今日到期卡片 ${n} 张` : label
  }
  if (!titles.length) return label
  if (p.source_type === 'notes') {
    const head = titles.slice(0, 2).map((t) => `《${t}》`).join('、')
    return titles.length <= 2 ? `笔记 ${head}` : `笔记 ${head} 等 ${titles.length} 条`
  }
  let s = `《${titles[0]}》`
  if (p.source_type === 'highlights') {
    const colors = refs[0]?.colors || []
    if (colors.length) s += ` · ${colors.map((c) => COLOR_LABELS[c] || c).join('/')}划线`
  }
  return `${label} ${s}`
})

// 作品的来源是否已不在库里（材料 / 笔记被删除）
// 判定用后端按 id 精查的 source_missing：**不能**拿 options 那份列表比对 ——
// 那个接口只回最近 300 条材料 / 笔记，超过 300 条后老来源会被误报「已不在库里」。
const sourceMissing = computed(() => current.value?.source_missing === true)

const scriptChars = computed(() => script.value.reduce((n, s) => n + (s.text || '').length, 0))
const estimateSec = computed(() => Math.round((scriptChars.value / 250) * 60))
const renderedBrief = computed(() => md.render(brief.value || ''))

// 音频与当前脚本是否不一致。两个来源缺一不可：
//   · current.audio_stale —— 后端指纹比对，覆盖「已保存脚本但还没重新合成」
//   · dirty               —— 本地状态，覆盖「刚编辑、尚未保存」
// 只用一个会漏：编辑未保存时后端不知道，已保存未合成时 dirty 又是 false。
const audioMismatch = computed(() => {
  if (!current.value?.has_audio) return false
  return !!current.value.audio_stale || dirty.value
})
const progressPct = computed(() => {
  const total = current.value?.duration_sec || 0
  if (!total) return 0
  return Math.min(100, (playerTime.value / total) * 100)
})

// 音频 URL 带版本号（音频字节数）。不带的话重新合成前后 src 字符串完全相同，
// Vue 认为属性没变 → 不更新 DOM → <audio> 继续放上一次缓冲的音频（用户会以为
// 「重新合成点了没用」）。带上版本号后 URL 变化，播放器必然重新取流。
const audioSrc = computed(() => {
  const c = current.value
  if (!c) return ''
  return podcastApi.audioUrl(c.id, c.audio_bytes)
})

// 播放时逐句高亮：时间戳由后端在合成时回填
const activeIndex = computed(() => {
  if (!playing.value) return -1
  const ms = playerTime.value * 1000
  for (let i = 0; i < script.value.length; i++) {
    const s = script.value[i]
    if (s.start_ms != null && ms >= s.start_ms && ms < s.end_ms) return i
  }
  return -1
})

// 逐句高亮时把当前句滚进可视区：activeIndex 本来就算好了，50 句的脚本却
// 「听到哪句看不到哪句」。正在编辑时不动（别跟用户的输入抢滚动位置）。
const segRefs = ref([])
function setSegRef(el, i) {
  if (el) segRefs.value[i] = el
}
watch(activeIndex, (i) => {
  if (i < 0 || editingIdx.value >= 0) return
  const el = segRefs.value[i]
  if (el && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
})

function fmtDur(sec) {
  const s = Math.max(0, Math.round(sec || 0))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

// 作品状态点：先看「能不能听」，再看过程状态。
// ⚠️ 早先 `status === 'failed'` 排在最前 —— 于是一个有可播放旧音频的作品，
// 红点会把「有音频」这个事实整个盖掉。现在两者并列：底色随音频状态，
// 「合成失败」在 meta 里用红字单独标注（见模板），互不覆盖。
function workState(p) {
  if (p.has_audio) return p.audio_stale ? 'stale' : 'done'
  return p.status === 'failed' ? 'failed' : 'pending'
}

function workStateText(p) {
  if (p.status === 'failed' && p.has_audio) {
    return '上次合成失败；当前可播放的是上一版音频'
  }
  return {
    failed: '合成失败（还没有可听的音频）', stale: '音频是上一版',
    done: '音频就绪', pending: '待合成',
  }[workState(p)]
}

// ---------- 数据加载 ----------

async function loadOptions() {
  // 注意：项目 axios 实例未挂响应拦截器，接口返回完整 response，需取 .data
  const { data } = await podcastApi.options()
  options.value = data
  if (data.defaults?.voice_map) {
    form.voice_map.host = data.defaults.voice_map.host
    form.voice_map.expert = data.defaults.voice_map.expert
  }
}

async function loadList() {
  const { data } = await podcastApi.list()
  list.value = data
}

async function loadBgm() {
  const { data } = await podcastApi.bgmList()
  bgmTracks.value = data
}

// 背景音乐一改就写进作品：这样后端指纹立刻把已合成音频判为「上一版」，
// 界面无需自己做本地比对，也不怕用户改完没点重新合成就关掉页面。
async function persistBgm() {
  if (!current.value) return true     // 还没生成过作品 → 配置留在 form，生成时一并提交
  try {
    const { data } = await podcastApi.setBgm(current.value.id, {
      bgm_id: form.bgm_id || '', bgm_volume: form.bgm_volume,
    })
    current.value = data
    await loadList()
    return true
  } catch (e) {
    ElMessage.error(errMsg(e, '背景音乐设置保存失败'))
    return false
  }
}

function triggerBgmUpload() {
  bgmFileEl.value?.click()
}

async function onBgmFile(e) {
  const f = e.target.files && e.target.files[0]
  e.target.value = ''                 // 清空，允许再次选同一个文件
  if (!f) return
  bgmUploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    const { data } = await podcastApi.uploadBgm(fd)
    await loadBgm()
    form.bgm_id = data.id
    await persistBgm()
    ElMessage.success(`已添加背景音乐：${data.name}`)
  } catch (err) {
    ElMessage.error(errMsg(err, '上传失败'))
  } finally {
    bgmUploading.value = false
  }
}

function loadCurrent(p) {
  current.value = p
  script.value = (p.script || []).map((s) => ({ ...s }))
  brief.value = p.brief || ''
  tab.value = 'script'
  editingIdx.value = -1
  dirty.value = false
  playerTime.value = 0
  playing.value = false
}

// 打开作品：列表接口只回卡片字段（script / brief 体积大，不随列表下发），
// 所以这里按需拉一次详情。seq 用于丢弃「点了 A 又点 B」时先到的那份响应。
let openSeq = 0
async function openWork(p) {
  const seq = ++openSeq
  try {
    const { data } = await podcastApi.detail(p.id)
    if (seq !== openSeq) return
    loadCurrent(data)
    syncFormFromWork(data)
    if (!(data.script || []).length && data.brief) tab.value = 'brief'
  } catch (e) {
    if (seq === openSeq) ElMessage.error(errMsg(e, '打开作品失败'))
  }
}

// 把作品的来源与参数回填进生成台。
// 不做的话，选中作品后生成台仍是上一次手选的来源 —— 用户既看不出这条作品来自哪里，
// 也无法基于同一来源直接重新生成（按钮却已写着「重新生成脚本」，界面自相矛盾）。
function syncFormFromWork(p) {
  const type = sourceTypes.some((s) => s.id === p.source_type) ? p.source_type : 'article'
  const refs = Array.isArray(p.source_refs) ? p.source_refs : []
  form.source_type = type
  form.ref_id = null
  form.note_ids = []
  if (type === 'notes') {
    form.note_ids = refs.map((r) => Number(r.id)).filter((n) => Number.isFinite(n))
  } else if (type === 'article' || type === 'highlights') {
    const id = Number(refs[0]?.id)
    form.ref_id = Number.isFinite(id) ? id : null
    if (type === 'highlights') {
      const colors = refs[0]?.colors
      if (Array.isArray(colors) && colors.length) form.highlight_colors = [...colors]
    }
  }
  const styleIds = (options.value.styles || []).map((s) => s.id)
  form.style = styleIds.includes(p.style) ? p.style : (options.value.default_style || 'dialogue')
  if (p.target_minutes) form.target_minutes = p.target_minutes
  const vm = p.voice_map || {}
  if (vm.host) form.voice_map.host = vm.host
  if (vm.expert) form.voice_map.expert = vm.expert
  form.instruction = p.instruction || ''
  form.bgm_id = p.bgm_id || ''
  form.bgm_volume = p.bgm_volume ?? -20
}

// 「新建」= 回到干净状态。⚠️ 必须把生成台也清掉：只清 current 的话，面板里
// 仍填着上一条作品的来源 / 音色 / BGM，尤其 BGM 会被静默带进新作品 ——
// 用户以为在新建，实际继承了旧配置，却看不出哪里不对。
function resetForm() {
  current.value = null
  script.value = []
  brief.value = ''
  dirty.value = false
  playing.value = false
  editingIdx.value = -1

  form.source_type = 'article'
  form.style = options.value.default_style || 'dialogue'
  form.ref_id = null
  form.note_ids = []
  form.highlight_colors = ['green', 'yellow', 'blue']
  form.target_minutes = 3
  const d = options.value.defaults || {}
  form.voice_map = { host: d.voice_map?.host || '', expert: d.voice_map?.expert || '' }
  form.instruction = ''
  form.bgm_id = ''
  form.bgm_volume = bgmVolumeRange.value.default ?? -20
  fastMode.value = false
}

function onSourceType(t) {
  form.source_type = t
  form.ref_id = null
  form.note_ids = []
}

function toggleColor(key) {
  const i = form.highlight_colors.indexOf(key)
  if (i >= 0) form.highlight_colors.splice(i, 1)
  else form.highlight_colors.push(key)
}

// ---------- 生成 ----------

function payload() {
  // ref_ids 统一归一成数组：单选取值在提交前包一层，避免后端拿到裸数字
  let refIds = []
  if (form.source_type === 'notes') {
    refIds = form.note_ids.map(Number)
  } else if (form.source_type !== 'review' && form.ref_id != null) {
    refIds = [Number(form.ref_id)]
  }
  return {
    source_type: form.source_type,
    ref_ids: refIds,
    // 带上当前标题：后端 regenerate 是「req.title or 来源标题」，不带就等于
    // 「重新生成一次，用户起的名字被重置成来源标题」。
    title: current.value?.title || '',
    highlight_colors: form.highlight_colors,
    // 风格必须跟着走：不带的话「重新生成」会静默退回默认的对谈风格，
    // 而生成台里明明选着「单人精讲」（面板与产物不一致，用户看不出来）
    style: form.style,
    target_minutes: form.target_minutes,
    voice_map: { ...form.voice_map },
    mode: fastMode.value ? 'fast' : 'two_step',
    instruction: form.instruction,
    bgm_id: form.bgm_id || '',
    bgm_volume: form.bgm_volume,
  }
}

async function generate() {
  if (form.source_type === 'notes') {
    if (!form.note_ids.length) return ElMessage.warning('请至少选择一条笔记')
  } else if (form.source_type === 'review') {
    if (!options.value.review_due) return ElMessage.warning('今天没有到期的复习卡片')
  } else if (form.source_type === 'highlights') {
    // 三色一个都没勾时在这里拦住：旧版后端会把空数组静默当成「三色全选」，
    // 生成的内容与用户看到的勾选状态不一致
    if (form.ref_id == null) return ElMessage.warning('请选择一篇有划线的文档')
    if (!form.highlight_colors.length) return ElMessage.warning('请至少选择一种划线颜色')
  } else if (form.ref_id == null) {
    return ElMessage.warning('请先选择文档')
  }

  generating.value = true
  genProgress.value = { label: '', index: 0, total: 0 }
  let ok = false
  try {
    const body = payload()
    // 走 SSE：LLM 一两趟要几十秒，阶段进度按「抽素材 → 提炼简报 → 撰写脚本（含字数校正）」
    // 逐步回传（见后端 /generate/stream、/{pid}/regenerate/stream）
    await streamSSE(
      current.value
        ? podcastApi.regenerateStreamUrl(current.value.id)
        : podcastApi.generateStreamUrl(),
      body,
      undefined,
      ({ podcast }) => {
        ok = true
        if (podcast) loadCurrent(podcast)
      },
      (msg) => ElMessage.error(msg || '生成失败'),
      undefined,
      ({ label, index, total }) => {
        genProgress.value = { label: label || '', index: index || 0, total: total || 0 }
      },
    )
    await loadList()
    if (ok && current.value) {
      ElMessage.success(`脚本已生成：${current.value.segment_count} 句 / `
        + `${current.value.script_chars} 字，预估 ${fmtDur(current.value.estimate_sec)}`)
    }
  } catch (e) {
    // 参数类错误（没选文档 / 划线颜色为空 / 素材为空）由后端在返回流之前用
    // HTTP 4xx 拒绝，streamSSE 会抛出来 → 这里统一提示
    ElMessage.error(errMsg(e, '生成失败'))
  } finally {
    generating.value = false
    genProgress.value = { label: '', index: 0, total: 0 }
  }
}

// ---------- 脚本转笔记 ----------
// 脚本是「材料无关」的产物（一条播客可引用多份材料/笔记，不归属任何单一材料），
// 所以笔记落 material_id=NULL，只能在知识库「按笔记」里看到。
const aiNotes = ref([])
const noteBusy = ref(false)
// 「已转笔记 · 查看」→ 当前页弹窗打开（共用组件）
const scriptView = reactive({ show: false, id: null })
// 简报用它自己的实例：两个弹窗互不干扰（同一页可能先看脚本笔记、再看简报笔记）
const briefView = reactive({ show: false, id: null })

// 这里只用 anchor 判断「这个产物转过笔记没有」，正文一个字都用不上 ——
// 所以带 slim=1 拉**不带正文**的列表（否则会把全部周报 / 播客脚本 / 问答的
// 正文一起下载下来，笔记正文正是这批数据里最大的一块）。
async function loadAiNotes() {
  try {
    const { data } = await noteApi.list(null, { slim: 1 })
    aiNotes.value = data
  } catch { /* 静默：拿不到笔记列表不影响生成播客 */ }
}

// 脚本正文（转笔记的内容）：只取说话人与文本，与界面所见一致
const scriptMd = computed(() => (script.value || [])
  .filter((s) => (s.text || '').trim())
  .map((s) => `**${s.speaker === 'host' ? '主持人' : '专家'}**：${s.text.trim()}`)
  .join('\n\n'))

// 脚本指纹：只取「说话人 + 文本」，**不含音色与时间戳** ——
// 换音色 / 合成回填时间戳都不影响笔记内容，算进去会误报「脚本已更新」。
const scriptSig = computed(() => textHash((script.value || [])
  .filter((s) => (s.text || '').trim())
  .map((s) => `${s.speaker || 'host'}|${s.text.trim()}`)
  .join('\n')))

// 状态口径用 podcast_id 这个稳定业务键，**不是脚本指纹** ——
// 重新生成脚本时 podcast_id 不变，用它才能找到「这条作品的笔记」；
// 指纹只用来判断「笔记还对得上当前脚本吗」。
const scriptNote = computed(() => {
  const pid = current.value?.id
  if (pid == null) return null
  return aiNotes.value.find(
    n => n.anchor?.kind === 'podcast_script' && n.anchor?.podcast_id === pid) || null
})
const scriptStale = computed(() =>
  !!scriptNote.value && scriptNote.value.anchor?.script_sig !== scriptSig.value)

async function scriptToNote() {
  const c = current.value
  if (!c) return
  const content = scriptMd.value
  if (content.trim().length < 2) return ElMessage.warning('脚本为空，无法转笔记')

  const anchor = {
    kind: 'podcast_script',
    podcast_id: c.id,
    script_sig: scriptSig.value,
    key: `podcast_script:${c.id}`,
  }
  noteBusy.value = true
  try {
    if (scriptNote.value) {
      // 覆盖更新：同一条笔记推进到最新脚本，不新建（避免重复沉淀）
      await ElMessageBox.confirm(
        '脚本已经改过了。更新后，你在笔记里做过的编辑会被最新脚本覆盖。',
        '更新笔记', { type: 'warning', confirmButtonText: '覆盖更新', cancelButtonText: '取消' })
      const { data } = await noteApi.update(scriptNote.value.id, { content, anchor })
      if (data?.reindex_warning) ElMessage.warning(data.reindex_warning)
      else ElMessage.success('笔记已更新')
    } else {
      const { data } = await noteApi.fromAi({
        title: `${c.title || 'AI 播客'} · 对话脚本`,
        content, source_type: 'podcast_script', anchor,
      })
      if (data?.note?.reindex_warning) ElMessage.warning(data.note.reindex_warning)
      else ElMessage.success('已转成笔记')
    }
    await loadAiNotes()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(errMsg(e, '转笔记失败'))
  } finally {
    noteBusy.value = false
  }
}

// 转笔记后「查看」：在当前页弹窗打开该笔记（与知识库同一份编辑器组件）
function openScriptNote(id) {
  scriptView.id = Number(id)
  scriptView.show = true
}

// ---------- 知识简报（提炼层）转笔记 ----------
// ⚠️ 简报与脚本是**同一播客下的两条独立产物** → 必须各自建档：
// anchor 的 kind 与 key 都带产物类型前缀，否则两者的笔记会互相判重、互相覆盖。
const briefChars = computed(() => (brief.value || '').replace(/\s+/g, '').length)
// 简报指纹：只取正文。简报是只读产物（页面不能编辑），所以正文即唯一输入。
const briefSig = computed(() => textHash((brief.value || '').trim()))

const briefNote = computed(() => {
  const pid = current.value?.id
  if (pid == null) return null
  return aiNotes.value.find(
    n => n.anchor?.kind === 'podcast_brief' && n.anchor?.podcast_id === pid) || null
})
const briefStale = computed(() =>
  !!briefNote.value && briefNote.value.anchor?.brief_sig !== briefSig.value)

async function briefToNote() {
  const c = current.value
  if (!c) return
  const content = (brief.value || '').trim()
  if (content.length < 2) return ElMessage.warning('知识简报为空，无法转笔记')

  const anchor = {
    kind: 'podcast_brief',
    podcast_id: c.id,
    brief_sig: briefSig.value,
    key: `podcast_brief:${c.id}`,
  }
  noteBusy.value = true
  try {
    if (briefNote.value) {
      await ElMessageBox.confirm(
        '知识简报已经更新过了。更新后，你在笔记里做过的编辑会被最新简报覆盖。',
        '更新笔记', { type: 'warning', confirmButtonText: '覆盖更新', cancelButtonText: '取消' })
      const { data } = await noteApi.update(briefNote.value.id, { content, anchor })
      if (data?.reindex_warning) ElMessage.warning(data.reindex_warning)
      else ElMessage.success('笔记已更新')
    } else {
      const { data } = await noteApi.fromAi({
        title: `${c.title || 'AI 播客'} · 知识简报`,
        content, source_type: 'podcast_brief', anchor,
      })
      if (data?.note?.reindex_warning) ElMessage.warning(data.note.reindex_warning)
      else ElMessage.success('已转成笔记')
    }
    await loadAiNotes()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(errMsg(e, '转笔记失败'))
  } finally {
    noteBusy.value = false
  }
}

function openBriefNote(id) {
  briefView.id = Number(id)
  briefView.show = true
}

// ---------- 脚本编辑 ----------

function toggleSpeaker(i) {
  // 单人精讲没有第二个说话人：允许切换会让作品变成「声明 solo、实际有 expert」，
  // 界面也无法自洽。想改成对谈请重新生成（风格是生成期参数）。
  if (form.style === 'solo') {
    return ElMessage.info('单人精讲只有一个说话人；想改风格请重新生成脚本')
  }
  script.value[i].speaker = script.value[i].speaker === 'host' ? 'expert' : 'host'
  dirty.value = true
}

function removeSeg(i) {
  script.value.splice(i, 1)
  dirty.value = true
}

function addSeg() {
  const last = script.value[script.value.length - 1]
  script.value.push({ speaker: last?.speaker === 'host' ? 'expert' : 'host', text: '' })
  editingIdx.value = script.value.length - 1
  dirty.value = true
}

async function saveScript() {
  if (!current.value) return
  const clean = script.value.filter((s) => (s.text || '').trim())
  if (!clean.length) return ElMessage.warning('脚本不能为空')
  if (!(await confirmOverlong())) return
  try {
    const res = await podcastApi.saveScript(current.value.id, {
      script: clean,
      voice_map: { ...form.voice_map },
    })
    loadCurrent(res.data)
    await loadList()
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  }
}

// 超长句会被后端按 MAX_SEG_CHARS 截断。用一次确认把这件事说清楚，
// 而不是等保存完才发现句尾少了半截（旧行为是纯静默）。
async function confirmOverlong() {
  const n = overlongSegs.value.length
  if (!n) return true
  try {
    await ElMessageBox.confirm(
      `有 ${n} 句超过 ${MAX_SEG_CHARS.value} 字，保存后超出部分会被截断`
      + '（逐句跳转需要较短的句子）。建议先拆成几句。仍要继续吗？',
      '有超长句子',
      { type: 'warning', confirmButtonText: '继续保存', cancelButtonText: '返回修改' })
    return true
  } catch {
    return false
  }
}

// 导出文稿：用 <a download> 触发下载，而不是在新标签页里打开 ——
// 后端已带 Content-Disposition: attachment，配合 download 才是真的「下载」。
function exportScript() {
  const c = current.value
  if (!c) return
  const a = document.createElement('a')
  a.href = podcastApi.exportUrl(c.id)
  a.download = ''            // 文件名由服务端的 Content-Disposition 决定
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// 导出 SRT 字幕。时间轴来自合成回填的时间戳，所以没合成过就没有字幕 ——
// 这种情况下给明确提示，而不是让用户下载到一份空文件。
function exportSrt() {
  const c = current.value
  if (!c) return
  if (!hasTimestamps.value) {
    return ElMessage.warning('请先合成一次音频：字幕时间轴来自音频')
  }
  const a = document.createElement('a')
  a.href = podcastApi.srtUrl(c.id)
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// 脚本一改动，音频就不再对应当前文案 —— 立即暂停播放。
// 否则用户会「边听旧音频、边看新文案」，听起来就是声音和文字对不上。
watch(dirty, (v) => {
  if (v && playing.value) {
    audioRef.value?.pause()
    playing.value = false
  }
})

// ---------- 合成 ----------

// 合成前预检：空句会被丢弃、超长句会被截断。⚠️ 这两件事在旧版是**纯静默**的，
// 用户只能在成品里发现「少了一句 / 句子被砍了一截」。
// 进度条回答「还要多久」，这一段回答「有没有东西会被悄悄改掉」。
function applyPrecheck(pre) {
  if (!pre) return
  const parts = []
  if (pre.empty) parts.push(`${pre.empty} 句空白会被丢弃`)
  if (pre.overlong) {
    parts.push(`${pre.overlong} 句超长会被截断（约 ${pre.dropped_chars} 字）`)
  }
  if (parts.length) ElMessage.warning(`合成已开始：${parts.join('；')}`)
}

async function refreshCurrent() {
  if (!current.value) return
  try {
    const { data } = await podcastApi.detail(current.value.id)
    loadCurrent(data)
  } catch { /* 忽略刷新失败 */ }
}

async function synthesize() {
  if (!current.value) return
  const clean = script.value.filter((s) => (s.text || '').trim())
  if (!clean.length) return ElMessage.warning('脚本为空，无法合成')
  if (!(await confirmOverlong())) return

  synthesizing.value = true
  synthProgress.value = { done: 0, total: 0 }
  let ok = false
  try {
    if (dirty.value) {
      const saved = await podcastApi.saveScript(current.value.id, {
        script: clean, voice_map: { ...form.voice_map },
      })
      dirty.value = false
      current.value = saved.data
    }
    // 走 SSE：逐句合成要 30-60s，进度按「已完成句数」回传（见后端 /synthesize/stream）
    await streamSSE(
      podcastApi.synthesizeStreamUrl(current.value.id),
      {
        voice_map: { ...form.voice_map }, gap_ms: options.value.defaults?.gap_ms,
        bgm_id: form.bgm_id || '', bgm_volume: form.bgm_volume,
      },
      undefined,
      ({ podcast }) => {
        ok = true
        if (podcast) loadCurrent(podcast)
        else refreshCurrent()
      },
      (msg) => { ElMessage.error(msg || '合成失败'); refreshCurrent() },
      // precheck 事件走 meta 通道（utils/sse.js 已有的元信息回调）
      (pre) => applyPrecheck(pre),
      ({ done, total }) => { synthProgress.value = { done, total } },
    )
    await loadList()
    if (ok && current.value) {
      ElMessage.success(`音频已生成：${fmtDur(current.value.duration_sec)}，可在下方播放`)
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '合成失败'))
    // 失败详情（含可续传说明）要留在页面上，不能只靠一闪而过的提示
    await refreshCurrent()
  } finally {
    synthesizing.value = false
    synthProgress.value = { done: 0, total: 0 }
  }
}

// ---------- 播放器 ----------

function togglePlay() {
  const el = audioRef.value
  if (!el) return
  if (el.paused) {
    el.play().then(() => { playing.value = true }).catch(() => ElMessage.error('播放失败'))
  } else {
    el.pause()
    playing.value = false
  }
}

function onTimeUpdate() {
  if (audioRef.value) playerTime.value = audioRef.value.currentTime
}

function onMeta() {
  if (audioRef.value) playRate.value = String(audioRef.value.playbackRate)
}

function onRateChange(v) {
  if (audioRef.value) audioRef.value.playbackRate = Number(v)
}

function seek(e) {
  const el = audioRef.value
  const bar = progressRef.value
  if (!el || !bar || !el.duration) return
  const rect = bar.getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
  el.currentTime = ratio * el.duration
  playerTime.value = el.currentTime
}

// 快进 / 后退：通勤时「这句没听清」是高频动作，拖进度条找那几秒很费劲
function skip(sec) {
  const el = audioRef.value
  if (!el || !el.duration) return
  el.currentTime = Math.min(el.duration, Math.max(0, el.currentTime + sec))
  playerTime.value = el.currentTime
}

// 上一句 / 下一句：句子边界本来就在脚本里（start_ms），不必让用户自己找
async function stepSeg(dir) {
  const el = audioRef.value
  if (!el) return
  if (audioMismatch.value) {
    return ElMessage.warning('音频还是上一版，请先点「重新合成」再按句子跳转')
  }
  const segs = (script.value || []).filter((s) => s.start_ms != null)
  if (!segs.length) return
  const ms = el.currentTime * 1000
  let target = null
  if (dir > 0) {
    target = segs.find((s) => s.start_ms > ms + 50) || segs[segs.length - 1]
  } else {
    // 已经过了本句开头 1.2s 以上 → 先回到本句开头；否则再退到上一句
    const before = segs.filter((s) => s.start_ms <= ms + 1)
    const cur = before.length ? before[before.length - 1] : null
    target = (cur && ms - cur.start_ms > 1200)
      ? cur
      : (before.length > 1 ? before[before.length - 2] : segs[0])
  }
  el.currentTime = target.start_ms / 1000
  playerTime.value = el.currentTime
  if (el.paused) {
    try { await el.play(); playing.value = true } catch { /* 忽略自动播放拦截 */ }
  }
}

async function seekToSeg(i) {
  const seg = script.value[i]
  if (!current.value?.has_audio || seg.start_ms == null) return
  // 音频是「上一版」时不能跳：时间戳属于旧文案，跳过去必然落在不相干的句子中间，
  // 高亮也会跟着错。此处应引导用户先重新合成，而不是给一个错的位置。
  if (audioMismatch.value) {
    return ElMessage.warning('音频还是上一版，请先点「重新合成」再按句子跳转')
  }
  const el = audioRef.value
  if (!el) return
  el.currentTime = seg.start_ms / 1000
  playerTime.value = el.currentTime
  if (el.paused) {
    try { await el.play(); playing.value = true } catch { /* 忽略自动播放拦截 */ }
  }
}

// ---------- 文件操作 ----------

async function reveal() {
  try {
    const { data } = await podcastApi.reveal(current.value.id)
    ElMessage.success('已在文件管理器中定位')
    return data
  } catch (e) {
    ElMessage.error(errMsg(e, '无法打开文件位置'))
  }
}

async function copyPath() {
  try {
    const { data } = await podcastApi.audioPath(current.value.id)
    const path = data.path
    try {
      await navigator.clipboard.writeText(path)
    } catch {
      // 非安全上下文 / WebView 可能不支持 clipboard API，退回 execCommand
      const ta = document.createElement('textarea')
      ta.value = path
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    ElMessage.success('已复制文件路径')
  } catch (e) {
    ElMessage.error(errMsg(e, '复制失败'))
  }
}

// 重命名作品：后端 PUT /podcasts/{id} 一直支持改标题，但界面上没有入口，
// 而「重新生成」又会用来源标题回填 —— 用户于是完全没有办法给作品起个好认的名字。
async function renameWork(p) {
  let title = ''
  try {
    const r = await ElMessageBox.prompt('给这条作品起个名字', '重命名', {
      inputValue: p.title, confirmButtonText: '保存', cancelButtonText: '取消',
      inputValidator: (v) => ((v || '').trim() ? true : '标题不能为空'),
    })
    title = (r.value || '').trim()
  } catch {
    return
  }
  try {
    await podcastApi.rename(p.id, title)
    // 只改标题：不能走 loadCurrent()，否则会把本地未保存的脚本编辑冲掉
    if (current.value?.id === p.id) current.value = { ...current.value, title }
    await loadList()
    ElMessage.success('已重命名')
  } catch (e) {
    ElMessage.error(errMsg(e, '重命名失败'))
  }
}

async function removeWork(p) {
  try {
    await ElMessageBox.confirm(`删除播客「${p.title}」？音频文件会一并删除。`, '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch {
    return
  }
  try {
    await podcastApi.remove(p.id)
    if (current.value?.id === p.id) resetForm()
    await loadList()
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  }
}

async function checkVoice() {
  voiceStatus.value = { ok: true, label: '检测中...', text: '' }
  try {
    const { data } = await podcastApi.testVoice()
    voiceStatus.value = { ok: data.ok, label: data.ok ? '语音服务正常' : '语音服务异常', text: data.detail }
    data.ok ? ElMessage.success(data.detail) : ElMessage.error(data.detail)
  } catch (e) {
    voiceStatus.value = { ok: false, label: '语音服务异常', text: errMsg(e, '检测失败') }
    ElMessage.error(errMsg(e, '检测失败'))
  }
}

// ---------- 音色试听 ----------

const previewAudioEl = ref(null)
const previewId = ref('')          // 正在试听的音色 id（空 = 未在试听）
const previewBusy = ref(false)     // 正在等待音频到达（首次要现场合成，需数秒）
let previewObjUrl = ''

function stopPreview() {
  const el = previewAudioEl.value
  if (el) {
    el.pause()
    el.removeAttribute('src')
    try { el.load() } catch (_) { /* 忽略 */ }
  }
  if (previewObjUrl) {
    URL.revokeObjectURL(previewObjUrl)
    previewObjUrl = ''
  }
  previewId.value = ''
  previewBusy.value = false
}

function onPreviewError() {
  stopPreview()
  ElMessage.error('试听失败，请稍后重试')
}

// 音色与背景音乐共用同一套试听机制（同一个 audio 元素 + 同一个预览状态）
// 点同一个 = 停止（试听按钮天然是开关）
async function playPreview(url, id, what) {
  if (!id) return ElMessage.info(`请先选择${what}`)
  if (previewId.value === id) return stopPreview()

  stopPreview()
  // 主播放器正在放播客时先停掉，否则两个声音会叠在一起
  if (playing.value) {
    audioRef.value?.pause()
    playing.value = false
  }
  previewId.value = id
  previewBusy.value = true

  try {
    // 用 fetch 取 blob，而不是把 URL 直接交给 <audio>：
    // 首次要现场处理（音色需合成、BGM 需截取，都要数秒），
    // fetch 能拿到明确的失败原因，也能自己控制加载态
    const res = await fetch(url)
    if (!res.ok) {
      let msg = `HTTP ${res.status}`
      try {
        const j = await res.json()
        if (j && j.detail) msg = j.detail
      } catch (_) { /* 非 JSON 响应 */ }
      throw new Error(msg)
    }
    const blob = await res.blob()
    if (previewId.value !== id) return      // 期间用户又点了别的，丢弃本次结果

    previewObjUrl = URL.createObjectURL(blob)
    const el = previewAudioEl.value
    el.src = previewObjUrl
    await el.play()                          // 真正播出来后由 @playing 关掉加载态
  } catch (e) {
    if (previewId.value === id) {
      stopPreview()
      ElMessage.error('试听失败：' + (e?.message || '服务暂时不可用'))
    }
  }
}

function previewVoice(id) {
  return playPreview(podcastApi.voicePreviewUrl(id), id, '音色')
}

function previewBgm(id) {
  return playPreview(podcastApi.bgmPreviewUrl(id), id, '背景音乐')
}

onBeforeUnmount(stopPreview)

onMounted(async () => {
  try {
    await Promise.all([loadOptions(), loadList(), loadAiNotes(), loadBgm()])
  } catch (e) {
    ElMessage.error(errMsg(e, '加载失败'))
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
/* =========================================================
   AI 播客 · 视觉重构 v2
   原则：用留白与字重建立层级，弱化边框，强化语义
   节奏：4 / 8 / 12 / 16 / 20 / 24 / 32 / 40（8 的倍数）
   ========================================================= */
.podcast-page { padding-bottom: 32px; }

/* ---------- 页头（PageHead 组件负责标题/图标/副标题，保留右侧操作样式） ---------- */
.voice-chip {
  display: inline-flex; align-items: center; gap: 7px; cursor: pointer;
  font-size: 12px; color: var(--asc-text-2); padding: 5px 12px;
  border: 1px solid var(--asc-border); border-radius: 20px; background: var(--asc-card);
  transition: border-color .18s ease, color .18s ease;
}
.voice-chip:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
.voice-chip .dot {
  width: 6px; height: 6px; border-radius: 50%; background: #37c07a;
  box-shadow: 0 0 0 3px rgba(55, 192, 122, .16);
}
.voice-chip.bad .dot { background: #e5484d; box-shadow: 0 0 0 3px rgba(229, 72, 77, .16); }

/* ---------- 栅格 ---------- */
.podcast-grid { display: grid; grid-template-columns: 260px 380px minmax(0, 1fr); gap: 24px; align-items: stretch; height: calc(100vh - 140px); }
.podcast-col { display: flex; flex-direction: column; gap: 24px; min-width: 0; }
.col-left { position: sticky; top: 24px; align-self: start; max-height: 100%; overflow-y: auto; }
.col-center, .col-right { overflow-y: auto; }

/* ---------- 通用面板 ---------- */
.panel {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: var(--asc-radius); padding: 20px 22px 22px;
  box-shadow: 0 1px 2px rgba(0,0,0,.03);
}
.panel-left { padding: 18px 20px 22px; }
.panel-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 18px;
}
.panel-title { font-size: 14px; font-weight: 600; color: var(--asc-text); letter-spacing: .2px; }
.count-badge {
  font-size: 11px; color: var(--asc-text-2); background: var(--asc-surface-2);
  padding: 2px 8px; border-radius: 10px; font-variant-numeric: tabular-nums;
}

/* ---------- 表单分节 ---------- */
.form-section { padding-top: 18px; }
.form-section + .form-section { border-top: 1px solid var(--asc-divider); margin-top: 18px; }
.form-section:first-of-type { padding-top: 0; }
.section-label {
  font-size: 12px; font-weight: 600; color: var(--asc-text);
  margin-bottom: 16px; letter-spacing: .02em;
}

/* ---------- 跟随条（当前作品 / 来源） ---------- */
.follow-bar {
  background: var(--asc-primary-soft);
  border-radius: 10px;
  padding: 14px 16px; margin-bottom: 18px;
}
.fb-main { display: flex; flex-direction: column; gap: 6px; }
.fb-line { display: flex; align-items: center; gap: 8px; font-size: 13px; line-height: 1.5; }
.fb-line.fb-src-line { margin-top: 2px; }
.fb-tag {
  flex: none; font-size: 10px; font-weight: 600; color: var(--asc-primary);
  background: rgba(124, 92, 252, .12); padding: 2px 7px; border-radius: 4px;
}
.fb-name {
  font-weight: 600; color: var(--asc-text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.fb-src {
  color: var(--asc-text-2);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.fb-bad { flex: none; color: #b03030; font-size: 11px; font-weight: 500; }
.fb-tip { margin: 10px 0 0; font-size: 11.5px; color: var(--asc-text-2); line-height: 1.65; }

/* ---------- 表单 ---------- */
.field { margin-bottom: 18px; }
.field:last-child { margin-bottom: 0; }
.field label {
  display: block; font-size: 12px; font-weight: 500;
  color: var(--asc-text-2); margin-bottom: 8px;
}
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }
.hint { font-size: 12px; color: var(--asc-text-2); margin: 8px 0 0; line-height: 1.65; }
/* 需要用户处理的提示（如引用的背景音乐已失效）：用深红，不要用浅灰蒙混过去 */
.hint.warn { color: #a72d2d; }
.hint.warn .el-button { margin-left: 2px; vertical-align: baseline; }
.hint-inline { font-size: 11.5px; color: var(--asc-text-2); margin-left: 6px; font-weight: 400; }

.inst-presets { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.preset-chip {
  font-size: 11.5px; line-height: 1.4; color: var(--asc-text-2);
  background: var(--asc-surface-2); border: 1px solid var(--asc-divider);
  border-radius: 12px; padding: 4px 10px; cursor: pointer;
  transition: background .18s ease, color .18s ease, border-color .18s ease;
}
.preset-chip:hover {
  background: var(--asc-primary-soft); color: var(--asc-primary); border-color: var(--asc-primary);
}

/* ---------- 分段控件（来源 / 时长 / 划线类型） ---------- */
.seg-ctl {
  display: flex; gap: 2px; padding: 3px;
  background: var(--asc-surface-2); border-radius: 9px;
}
.seg-ctl .seg-item {
  flex: 1; min-width: 0; text-align: center; white-space: nowrap;
  font-size: 12px; color: var(--asc-text-2); cursor: pointer;
  padding: 7px 2px; border-radius: 7px;
  transition: background .18s ease, color .18s ease, box-shadow .18s ease;
}
.seg-ctl .seg-item:hover { color: var(--asc-text); }
.seg-ctl .seg-item.on {
  background: var(--asc-card); color: var(--asc-primary); font-weight: 600;
  box-shadow: 0 1px 2px rgba(0, 0, 0, .06), 0 0 0 1px rgba(124, 92, 252, .16);
}

/* ---------- 主生成按钮 ---------- */
.generate-btn { width: 100%; margin-top: 2px; }

/* ---------- 复习卡片数量 ---------- */
.due-box {
  display: flex; align-items: baseline; gap: 8px;
  font-size: 13px; color: var(--asc-text);
  background: var(--asc-surface-2); border-radius: 9px; padding: 14px 16px;
}
.due-box b {
  font-size: 24px; font-weight: 600; line-height: 1;
  color: var(--asc-primary); font-variant-numeric: tabular-nums;
}
.due-box.empty { color: var(--asc-text-2); font-size: 12.5px; }
.due-box.empty b { display: none; }

.mode-row { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }

/* ---------- 生成进度 ---------- */
.gen-progress { margin: 12px 0 0; }
.gen-progress .sp-text {
  display: block; font-size: 11.5px; color: var(--asc-text-2);
  margin-bottom: 6px; line-height: 1.5;
}
.gen-progress .sp-text i { font-style: normal; color: var(--asc-text-2); opacity: .85; }
.gen-progress .sp-bar {
  height: 4px; background: var(--asc-divider); border-radius: 2px; overflow: hidden;
}
.gen-progress .sp-fill {
  height: 100%; background: var(--asc-primary); border-radius: 2px;
  transition: width .3s ease;
}

/* ---------- 音色试听 ---------- */
.voice-row {
  display: grid; grid-template-columns: 42px minmax(0, 1fr) 30px;
  align-items: center; gap: 8px; margin-bottom: 8px;
}
.voice-role { font-size: 12px; color: var(--asc-text-2); }
.try-btn {
  width: 30px; height: 30px; border-radius: 50%; flex: none; cursor: pointer;
  border: 1px solid var(--asc-border); background: var(--asc-card); color: var(--asc-primary);
  display: flex; align-items: center; justify-content: center;
  transition: border-color .18s ease, background .18s ease;
}
.try-btn:hover { border-color: var(--asc-primary); background: var(--asc-primary-soft); }
/* 下拉里每一项：音色名靠左、试听按钮靠右 */
.vo-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.vo-txt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vo-txt i { font-style: normal; color: var(--asc-text-2); font-size: 11px; margin-left: 4px; }
.vo-txt i::before { content: '·'; margin-right: 5px; opacity: .7; }
.vo-play {
  flex: none; width: 20px; height: 20px; border-radius: 50%; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  color: var(--asc-primary); background: var(--asc-primary-soft);
  transition: background .18s ease, color .18s ease;
}
.vo-play:hover { background: var(--asc-primary); color: #fff; }
/* 删除自己上传的背景音乐：与试听按钮同形，用红调表示破坏性操作 */
.vo-del {
  flex: none; width: 20px; height: 20px; border-radius: 50%; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  color: #b03030; background: #fdeceb; font-size: 14px; line-height: 1;
  transition: background .18s ease, color .18s ease;
}
.vo-del:hover { background: #e5484d; color: #fff; }
.vo-tri {
  width: 0; height: 0; margin-left: 1px;
  border-left: 6px solid currentColor;
  border-top: 4px solid transparent; border-bottom: 4px solid transparent;
}
.vo-stop { width: 7px; height: 7px; background: currentColor; border-radius: 1px; }
.vo-spin {
  width: 9px; height: 9px; border-radius: 50%;
  border: 1.6px solid currentColor; border-top-color: transparent;
  animation: vo-spin .7s linear infinite;
}
@keyframes vo-spin { to { transform: rotate(360deg); } }
.try-btn:disabled { opacity: .4; cursor: not-allowed; }
.try-btn:disabled:hover { border-color: var(--asc-border); background: var(--asc-card); }

/* ---------- 背景音乐 ---------- */
.bgm-row { display: flex; align-items: center; gap: 8px; }
.bgm-file { display: none; }
.bgm-plus { font-size: 17px; line-height: 1; margin-top: -2px; }
.bgm-vol { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.bgm-vol-label { flex: none; font-size: 12px; color: var(--asc-text-2); }
.bgm-vol .el-slider { flex: 1; }
.bgm-vol-val {
  flex: none; min-width: 46px; text-align: right;
  font-size: 11.5px; color: var(--asc-text-2); font-variant-numeric: tabular-nums;
}

/* ---------- 作品库 ---------- */
.work-new { margin-bottom: 12px; }
.work-new .new-work-btn { width: 100%; }
.work-new .plus { margin-right: 4px; font-size: 16px; line-height: 1; }
.work-list { display: flex; flex-direction: column; gap: 4px; max-height: calc(100vh - 360px); min-height: 120px; overflow-y: auto; }
.work-filter { display: flex; flex-direction: column; gap: 10px; margin-bottom: 12px; }
.work-filter .el-input { width: 100%; }
.work-filter .el-input :deep(.el-input__wrapper) {
  background: var(--asc-surface-2);
  box-shadow: 0 0 0 1px var(--asc-divider) inset;
  padding-left: 11px;
}
.work-filter .el-input :deep(.el-input__prefix-inner) { color: var(--asc-text-2); }
.work-filter .el-input :deep(.el-input__inner) { color: var(--asc-text); }
.work-filter .el-input :deep(.el-input__inner::placeholder) { color: var(--asc-text-3); }
.seg-ctl.mini { padding: 2px; border-radius: 8px; display: flex; width: 100%; }
.seg-ctl.mini .seg-item {
  flex: 1; min-width: 0; text-align: center;
  font-size: 11px; padding: 5px 4px; border-radius: 6px;
}
.work-empty { font-size: 12px; color: var(--asc-text-2); text-align: center; padding: 14px 0; margin: 0; }
.work-item {
  position: relative; display: flex; align-items: center; gap: 10px;
  padding: 10px 10px 10px 14px;
  border-radius: 9px; cursor: pointer; transition: background .18s ease;
}
.work-item::before {
  content: ''; position: absolute; left: 0; top: 50%; transform: translateY(-50%);
  width: 3px; height: 0; border-radius: 2px; background: var(--asc-primary);
  transition: height .2s ease;
}
.work-item:hover { background: var(--asc-surface-2); }
.work-item.on { background: var(--asc-primary-soft); }
.work-item.on::before { height: 22px; }

/* 状态点：绿=就绪 / 橙=上一版 / 灰=待合成 / 红=失败 */
.work-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: #c9c9cf; }
.work-dot.done { background: #37c07a; }
.work-dot.stale { background: #e8a33d; }
.work-dot.pending { background: #c9c9cf; }
.work-dot.failed { background: #e5484d; }

.work-main { flex: 1; min-width: 0; }
.work-title {
  font-size: 13px; color: var(--asc-text); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.work-meta { font-size: 11.5px; color: var(--asc-text-2); margin-top: 4px; display: flex; gap: 10px; }
.work-meta .pending { color: #a06a12; }
.work-meta .fail-tag { color: #b03030; font-weight: 500; }
.work-del, .work-edit {
  flex: none; width: 22px; height: 22px; line-height: 1;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%; color: var(--asc-text-2);
  opacity: 0; transition: opacity .18s ease, background .18s ease, color .18s ease;
}
.work-item:hover .work-del, .work-item:hover .work-edit { opacity: 1; }
.work-del:hover { background: #fde8e8; color: #e5484d; }
/* 重命名：与删除同形。用主色的深变体做小字（主色 #7c5cfc 在白底做小字不足 4.5:1） */
.work-edit:hover { background: #eee9ff; color: #634aca; }

/* ---------- 右栏空态：三步引导 ---------- */
.empty-guide {
  display: flex; flex-direction: column; align-items: center;
  padding: 64px 32px; text-align: center;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: var(--asc-radius);
}
.eg-img { width: 120px; height: 120px; object-fit: contain; margin-bottom: 22px; }
.eg-title { margin: 0; font-size: 17px; font-weight: 600; color: var(--asc-text); letter-spacing: .2px; }
.eg-sub { margin: 8px 0 0; font-size: 13px; color: var(--asc-text-2); }
.eg-steps { display: flex; flex-direction: column; gap: 10px; margin-top: 28px; width: 100%; max-width: 430px; }
.eg-step {
  display: flex; align-items: center; gap: 12px; text-align: left;
  font-size: 13px; line-height: 1.6; color: var(--asc-text-2);
  background: var(--asc-surface-2); border-radius: 10px; padding: 12px 14px;
}
.eg-step b {
  flex: none; width: 22px; height: 22px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 600;
  background: var(--asc-card); color: var(--asc-primary);
  box-shadow: 0 0 0 1px rgba(124, 92, 252, .2);
}

/* ---------- 音频卡片 ---------- */
.audio-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: var(--asc-radius); padding: 18px 22px 20px;
  box-shadow: 0 1px 2px rgba(0,0,0,.03);
}
.audio-card.stale { border-color: #e8a33d; }
.ac-stale {
  display: flex; align-items: flex-start; gap: 12px; margin-bottom: 16px;
  padding: 12px 14px; border-radius: 8px;
  background: #fdf8ec; line-height: 1.65;
}
.ac-stale-icon {
  flex: none; width: 20px; height: 20px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: #e8a33d; color: #fff; font-size: 12px; font-weight: 700;
}
.ac-stale-body { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.ac-stale b { font-size: 12.5px; font-weight: 600; color: #8a5a00; }
.ac-stale span { font-size: 12px; color: #96660f; }
.ac-badge {
  display: inline-block; vertical-align: middle; margin-left: 8px;
  font-size: 11px; font-weight: 500; color: #8a5a00; background: #fdf8ec;
  border-radius: 4px; padding: 2px 7px;
}
.ac-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.ac-info { min-width: 0; flex: 1; }
.ac-title {
  font-size: 15px; font-weight: 600; color: var(--asc-text);
  display: flex; align-items: center; gap: 8px;
}
.ac-title-text {
  flex: 1; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ac-title .ac-badge { flex: none; }
.ac-meta {
  font-size: 12px; color: var(--asc-text-2); margin-top: 6px; font-variant-numeric: tabular-nums;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 背景音乐素材已被删除：曲名还在（后端记着 bgm_label），但要标出「已失效」。
   用本页既有的琥珀色（#8a5a00 白底 7.0:1），不要用 --asc-text-3（2.6:1 不可读）。 */
.ac-bgm-gone { color: #8a5a00; }
.ac-ops { display: flex; align-items: center; gap: 2px; flex: none; flex-wrap: wrap; }
.ac-ops .el-button { display: inline-flex; align-items: center; gap: 5px; }
.ac-op-icon { flex: none; }

.ac-player { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
/* 通勤场景的高频动作：±15 秒、上一句 / 下一句 */
.play-btn {
  width: 44px; height: 44px; border-radius: 50%; border: none; flex: none;
  background: var(--asc-primary); color: #fff; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background .18s ease, transform .12s ease, box-shadow .18s ease;
  box-shadow: 0 3px 10px rgba(124, 92, 252, .25);
}
.play-btn:hover { background: var(--asc-primary-hover); box-shadow: 0 4px 14px rgba(124, 92, 252, .35); }
.play-btn:active { transform: scale(.94); }
.ptime {
  flex: none; font-size: 12px; color: var(--asc-text-2);
  font-variant-numeric: tabular-nums; min-width: 38px;
}
.ptime-current { color: var(--asc-text); font-weight: 500; }
.progress {
  flex: 1; height: 7px; background: var(--asc-divider); border-radius: 4px;
  cursor: pointer; position: relative; min-width: 100px;
}
.progress .bar {
  position: relative; height: 100%; background: var(--asc-primary); border-radius: 4px;
  transition: width .1s linear;
}
.progress .bar::after {
  content: ''; position: absolute; right: -6px; top: 50%;
  width: 12px; height: 12px; border-radius: 50%; background: #fff;
  box-shadow: 0 0 0 2px var(--asc-primary), 0 1px 4px rgba(0, 0, 0, .18);
  transform: translateY(-50%) scale(0); transition: transform .16s ease;
}
.progress:hover .bar::after { transform: translateY(-50%) scale(1); }

.player-extras {
  display: flex; align-items: center; gap: 8px; flex: none; flex-wrap: wrap;
}
.skip-btn {
  flex: none; min-width: 42px; height: 26px; padding: 0 8px;
  border: 1px solid var(--asc-divider); border-radius: 13px; background: transparent;
  font-size: 11px; color: var(--asc-text-2); cursor: pointer;
  font-variant-numeric: tabular-nums;
  transition: border-color .18s ease, color .18s ease;
}
.skip-btn:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
.seg-nav {
  flex: none; width: 24px; height: 24px; border: none; border-radius: 50%;
  background: var(--asc-surface-2); color: var(--asc-text-2); cursor: pointer;
  font-size: 13px; line-height: 1; display: flex; align-items: center;
  justify-content: center; transition: background .18s ease, color .18s ease;
}
.seg-nav:hover:not(:disabled) { background: var(--asc-primary); color: #fff; }
.seg-nav:disabled { opacity: .4; cursor: not-allowed; }

/* ---------- 脚本工坊 ---------- */
.workshop { padding: 20px 22px 24px; }
.ws-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; flex-wrap: wrap;
  padding-bottom: 16px; margin-bottom: 18px; border-bottom: 1px solid var(--asc-divider);
}
.tabs { display: flex; gap: 24px; }
.tab {
  font-size: 14px; color: var(--asc-text-2); cursor: pointer;
  padding-bottom: 8px; border-bottom: 2px solid transparent;
  transition: color .18s ease, border-color .18s ease;
}
.tab:hover { color: var(--asc-text); }
.tab.on { color: var(--asc-primary); border-bottom-color: var(--asc-primary); font-weight: 600; }
.tab-num {
  display: inline-block; font-size: 11px; color: var(--asc-text-2);
  background: var(--asc-surface-2); padding: 1px 7px; border-radius: 9px;
  margin-left: 7px; font-variant-numeric: tabular-nums;
}
.tab.on .tab-num { background: var(--asc-primary-soft); color: var(--asc-primary); }
.ws-ops { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ws-sep { width: 1px; height: 16px; background: var(--asc-divider); }
.ws-stat { font-size: 12px; color: var(--asc-text-2); font-variant-numeric: tabular-nums; }

.ws-tip {
  font-size: 12.5px; color: var(--asc-text-2); background: var(--asc-surface-2);
  border-left: 3px solid var(--asc-border); border-radius: 0 8px 8px 0;
  padding: 11px 14px; margin: 0 0 16px; line-height: 1.7;
}
.ws-tip.warn { color: #8a5a00; background: #fdf8ec; border-left-color: #e8a33d; }
.ws-tip.error { color: #a72d2d; background: #fdeceb; border-left-color: #e5484d; }

/* 合成进度 */
.synth-progress { display: flex; align-items: center; gap: 10px; margin: 0 0 16px; }
.synth-progress .sp-bar { flex: 1; height: 4px; background: var(--asc-divider); border-radius: 2px; overflow: hidden; }
.synth-progress .sp-fill { height: 100%; background: var(--asc-primary); border-radius: 2px; transition: width .2s ease; }
.synth-progress .sp-text { flex: none; font-size: 11.5px; color: var(--asc-text-2); font-variant-numeric: tabular-nums; }

/* 对话脚本：气泡式对话 */
.seg-list { display: flex; flex-direction: column; gap: 16px; }
.seg {
  position: relative; cursor: pointer;
  transition: transform .12s ease;
}
.seg:hover { transform: translateX(2px); }
.seg-bubble {
  background: var(--asc-surface-2); border-radius: 12px;
  padding: 12px 14px; position: relative;
  transition: background .18s ease, box-shadow .18s ease;
  overflow: hidden;
}
.seg.host .seg-bubble {
  background: #f7f5ff;
  border: 1px solid rgba(124, 92, 252, .12);
}
.seg.expert .seg-bubble {
  background: #f2f6ff;
  border: 1px solid rgba(74, 114, 212, .12);
}
.seg.active .seg-bubble {
  box-shadow: 0 0 0 2px var(--asc-primary);
}
.seg.host.active .seg-bubble { background: #efeafb; }
.seg.expert.active .seg-bubble { background: #e8efff; }

.seg-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.role {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 600; letter-spacing: .02em;
  cursor: pointer; user-select: none;
}
.role-avatar {
  width: 18px; height: 18px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  flex: none;
}
.role-avatar.host { background: var(--asc-primary); }
.role-avatar.expert { background: #4a72d4; }
.role-avatar::after {
  content: ''; width: 7px; height: 7px; background: #fff; border-radius: 50%;
}
.seg.host .role { color: #634aca; }
.seg.expert .role { color: #3f63c4; }
.role-voice { font-weight: 400; font-size: 11px; color: var(--asc-text-2); }
/* 超长句提示：后端按 MAX_SEG_CHARS 截断，先在这里说清楚 */
.seg-len { font-weight: 400; font-size: 11px; color: #b03030; }

.seg-ops { display: flex; align-items: center; gap: 6px; opacity: 0; transition: opacity .18s ease; }
.seg:hover .seg-ops, .seg.active .seg-ops { opacity: 1; }
.seg-btn {
  width: 26px; height: 26px; border-radius: 6px; border: none; cursor: pointer;
  background: transparent; color: var(--asc-text-2);
  display: flex; align-items: center; justify-content: center;
  transition: background .18s ease, color .18s ease;
}
.seg-btn:hover { background: rgba(0,0,0,.06); color: var(--asc-text); }
.seg-btn.seg-btn-danger:hover { background: #fde8e8; color: #e5484d; }

.seg-text {
  font-size: 15px; line-height: 1.85; color: var(--asc-text);
  white-space: pre-wrap; word-break: break-word;
}

.add-seg {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  text-align: center; font-size: 12.5px; color: var(--asc-text-2); cursor: pointer;
  padding: 12px; margin-top: 4px;
  border: 1px dashed var(--asc-border); border-radius: 10px;
  transition: border-color .18s ease, color .18s ease, background .18s ease;
}
.add-seg-icon {
  width: 16px; height: 16px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: var(--asc-surface-2); font-size: 14px; line-height: 1;
}
.add-seg:hover { border-color: var(--asc-primary); color: var(--asc-primary); background: var(--asc-primary-soft); }
.add-seg:hover .add-seg-icon { background: var(--asc-primary-soft); }

/* ---------- 知识简报 ---------- */
.brief-box { font-size: 14px; line-height: 1.9; color: var(--asc-text); overflow-wrap: break-word; word-break: break-word; }
.brief-box :deep(h2) {
  font-size: 15px; font-weight: 600; color: var(--asc-text);
  margin: 24px 0 10px; padding-left: 11px;
  border-left: 3px solid var(--asc-primary); line-height: 1.5;
}
.brief-box :deep(h2:first-child) { margin-top: 0; }
.brief-box :deep(p) { margin: 10px 0; }
.brief-box :deep(ul) { padding-left: 22px; margin: 8px 0; }
.brief-box :deep(li) { margin-bottom: 6px; line-height: 1.85; }
.brief-box :deep(li::marker) { color: var(--asc-primary); }

/* ---------- 响应式 ---------- */
@media (max-width: 1280px) {
  .podcast-grid { grid-template-columns: 220px 340px minmax(0, 1fr); gap: 18px; }
  .seg-text { font-size: 14.5px; }
}
@media (max-width: 1100px) {
  .podcast-grid { grid-template-columns: minmax(0, 1fr); height: auto; }
  .col-left, .col-center, .col-right { overflow-y: visible; max-height: none; }
  .col-left { position: static; }
  .seg-text { font-size: 15px; }
  .ac-top { flex-direction: column; gap: 10px; }
  .ac-ops { align-self: flex-start; }
}
@media (max-width: 520px) {
  .ac-player { gap: 8px; }
  .player-extras { width: 100%; justify-content: flex-end; margin-top: 2px; }
  .ac-op-text { display: none; }
}
/* 无 hover 能力（触屏）：常显操作按钮，否则永远点不到 */
@media (hover: none) {
  .seg-ops { opacity: 1; }
  .work-del, .work-edit { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .podcast-page *, .podcast-page *::before, .podcast-page *::after {
    animation-duration: .01ms !important;
    transition-duration: .01ms !important;
  }
}
</style>
