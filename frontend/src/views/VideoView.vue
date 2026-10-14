<template>
  <div class="video-page">
    <PageHead title="AI 视频" sub="把一份资料变成讲解视频：讲稿与分镜 → 视觉步点 → 质量自检 → 渲染出片">
      <template #icon>
        <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
          <rect x="2.5" y="4.5" width="17" height="13" rx="3" stroke="currentColor" stroke-width="1.6" />
          <path d="M9.4 8.6l4.6 2.4-4.6 2.4V8.6z" fill="currentColor" />
        </svg>
      </template>
      <el-button :disabled="loading" @click="reloadAll">刷新</el-button>
    </PageHead>

    <div class="video-body">
      <!-- ============ 左：生成台（新建 / 编辑同一个面板） ============ -->
      <section class="panel form-panel">
        <div class="panel-head">
          <h3>{{ cur ? '作品参数' : '新建作品' }}</h3>
          <span v-if="cur" class="panel-head-note">#{{ cur.id }} · {{ STATUS_LABEL[cur.status] || cur.status }}</span>
        </div>

        <div class="field">
          <label class="field-label">标题</label>
          <el-input v-model="form.title" maxlength="50" show-word-limit
                    :placeholder="cur ? '留空则保持原标题' : '留空则按来源自动命名'" />
        </div>

        <!-- 画面署名（电台浅色/深色画面底部那行字）。
             ⚠️ 只在电台皮肤下显示：其余皮肤的画面里**没有这个位置**，
                给一个填了却永远不出现在成片里的输入框，比不显示更糟。
             ⚠️ maxlength 与后端 svc.BRAND_TEXT_MAX 必须一致（20）。 -->
        <div v-if="brandSkin" class="field">
          <label class="field-label">画面署名</label>
          <el-input v-model="form.brand_text" maxlength="20" show-word-limit
                    placeholder="留空则用作品标题" />
          <p class="field-hint">
            电台画面底部那行字。标题太长放不下时，在这里填一个短一些的；
            改完需<b>重新生成</b>才会出现在成片里。
          </p>
        </div>

        <div class="field">
          <label class="field-label">音轨来源</label>
          <AudioSourcePicker
            v-model="form.audio_source"
            v-model:podcast-id="form.podcast_id"
            :podcasts="opts.podcasts"
          >
            <template #self>
              <div class="field">
                <label class="field-sub-label">素材类型</label>
                <el-select v-model="form.source_type" style="width: 100%">
                  <el-option v-for="s in opts.source_types" :key="s.key" :value="s.key" :label="s.label" />
                </el-select>
              </div>

              <div v-if="form.source_type === 'article'" class="field">
                <label class="field-sub-label">选择文档</label>
                <el-select v-model="form.ref_id" filterable placeholder="选一份材料"
                           style="width: 100%">
                  <el-option v-for="m in opts.materials" :key="m.id" :value="m.id"
                             :label="`${m.title}（${m.format} · ${m.page_count || '—'} 页）`" />
                </el-select>
              </div>

              <div v-else-if="form.source_type === 'notes'" class="field">
                <label class="field-sub-label">选择笔记（可多选）</label>
                <el-select v-model="form.note_ids" multiple filterable placeholder="选笔记"
                           style="width: 100%">
                  <el-option v-for="n in opts.notes" :key="n.id" :value="n.id"
                             :label="`${n.title}（${n.chars} 字）`" />
                </el-select>
              </div>

              <div v-else-if="form.source_type === 'highlights'" class="field">
                <label class="field-sub-label">选择文档</label>
                <el-select v-model="form.ref_id" filterable placeholder="选一份有划线的材料"
                           style="width: 100%">
                  <el-option v-for="m in opts.materials" :key="m.id" :value="m.id" :label="m.title" />
                </el-select>
                <div class="color-row">
                  <el-checkbox v-for="c in highlightChoices" :key="c.key"
                               :model-value="form.highlight_colors.includes(c.key)"
                               @update:model-value="(v) => toggleColor(c.key, v)">
                    {{ c.label }}
                  </el-checkbox>
                </div>
              </div>

              <div v-else-if="form.source_type === 'text'" class="field">
                <label class="field-sub-label">粘贴文本</label>
                <el-input v-model="form.pasted_text" type="textarea" :rows="6"
                          placeholder="把要讲的原文粘进来（不进材料库，随作品保存快照）" />
              </div>

              <div v-else class="field">
                <p class="field-hint">「错题卡片」会按复习队列里已到期的错题自动取材，无需再选。</p>
              </div>

              <div class="field">
                <label class="field-sub-label">音色<span class="hint-inline">点 ▶ 可试听每个音色</span></label>
                <div class="voice-row">
                  <el-select v-model="form.voice_map.host" size="small" style="flex: 1">
                    <el-option label="跟随设置页的语音设置" value="" />
                    <el-option v-for="v in opts.voices" :key="v.id" :value="v.id"
                               :label="`${v.name}（${v.desc}）`">
                      <div class="vo-row">
                        <span class="vo-txt">{{ v.name }}<i>{{ v.desc }}</i></span>
                        <span class="vo-play" :title="`试听 ${v.name}`"
                              @click.stop.prevent="previewVoice(v.id)">
                          <i v-if="previewId === v.id && previewBusy" class="vo-spin"></i>
                          <i v-else-if="previewId === v.id" class="vo-stop"></i>
                          <i v-else class="vo-tri"></i>
                        </span>
                      </div>
                    </el-option>
                  </el-select>
                  <!-- 三态与播客一致：▶ 待试听 / 转圈 等待音频 / ■ 播放中（再点即停）。
                       ⚠️ 空值守卫：`previewId === '' 且未选音色` 时 '' === '' 会误亮停止态。 -->
                  <button type="button" class="try-btn" :disabled="!form.voice_map.host"
                          :title="form.voice_map.host ? '试听当前音色' : '先选一个具体音色'"
                          @click="previewVoice(form.voice_map.host)">
                    <i v-if="form.voice_map.host && previewId === form.voice_map.host && previewBusy"
                       class="vo-spin"></i>
                    <i v-else-if="form.voice_map.host && previewId === form.voice_map.host"
                       class="vo-stop"></i>
                    <i v-else class="vo-tri"></i>
                  </button>
                </div>
                <p class="field-hint">
                  本片音轨用哪个音色说话。选「跟随设置页」即用「管理中心 → 语音合成」里的值。
                  换音色后需要<b>重新生成</b>才会合成新音色的音轨。
                </p>
              </div>

              <!-- 背景音乐：曲库是全局的（与播客共用 /api/podcasts/bgm）。
                   本行在「复用播客音轨」时随 self 插槽整体不渲染 —— 复用的音轨里
                   已烘了播客自己的 BGM，再叠一层会双音乐打架（2026-10-12 产品决策）。 -->
              <div class="field">
                <label class="field-sub-label">背景音乐<span class="hint-inline">点 ▶ 可试听</span></label>
                <div class="voice-row">
                  <el-select v-model="form.bgm_id" size="small" style="flex: 1"
                             placeholder="不加背景音乐">
                    <el-option label="不加背景音乐" value="" />
                    <el-option v-for="t in bgmSelectOptions" :key="t.id" :value="t.id"
                               :label="t.name">
                      <div class="vo-row">
                        <span class="vo-txt">{{ t.name }}<i>{{ t.desc }}</i></span>
                        <!-- 已失效（_ghost）的素材不给试听：文件已不在，点了必然 404 -->
                        <span v-if="!t._ghost" class="vo-play" :title="`试听 ${t.name}`"
                              @click.stop.prevent="previewBgm(t.id)">
                          <i v-if="previewId === t.id && previewBusy" class="vo-spin"></i>
                          <i v-else-if="previewId === t.id" class="vo-stop"></i>
                          <i v-else class="vo-tri"></i>
                        </span>
                      </div>
                    </el-option>
                  </el-select>
                  <button type="button" class="try-btn" :disabled="!form.bgm_id"
                          :title="form.bgm_id ? '试听当前背景音乐' : '先选一首背景音乐'"
                          @click="previewBgm(form.bgm_id)">
                    <i v-if="form.bgm_id && previewId === form.bgm_id && previewBusy"
                       class="vo-spin"></i>
                    <i v-else-if="form.bgm_id && previewId === form.bgm_id" class="vo-stop"></i>
                    <i v-else class="vo-tri"></i>
                  </button>
                  <button type="button" class="try-btn" :disabled="bgmUploading"
                          title="上传自己的音乐（mp3/wav/m4a/flac）"
                          @click="bgmFileEl && bgmFileEl.click()">
                    <i v-if="bgmUploading" class="vo-spin"></i>
                    <span v-else class="bgm-plus">+</span>
                  </button>
                  <input ref="bgmFileEl" type="file" accept="audio/*" class="bgm-file"
                         @change="onBgmPicked" />
                </div>
                <div v-if="form.bgm_id" class="bgm-vol-row">
                  <span class="bgm-vol-label">音量</span>
                  <el-slider v-model="form.bgm_volume" :min="bgmVolumeRange.min"
                             :max="bgmVolumeRange.max" :step="1" size="small" style="flex: 1" />
                  <span class="bgm-vol-val">{{ form.bgm_volume }} dB</span>
                </div>
                <p class="field-hint">
                  混进本片音轨（说话时自动压低，不妨碍听清讲解）。
                  换曲子或调音量后需要<b>重新生成</b>音轨才会生效。
                </p>
                <!-- ⚠️ 只说「已失效」，**不自动改作品**：bgm_id 保持原值，改不改由用户决定 -->
                <p v-if="bgmMissing" class="field-hint bad">
                  这个作品原来用的背景音乐已被删除，重新生成会失败；请重新选一首，或改回「不加背景音乐」。
                </p>
              </div>

              <div class="field">
                <label class="field-sub-label">生成方式</label>
                <el-select v-model="form.gen_mode" style="width: 100%">
                  <el-option v-for="g in opts.gen_modes" :key="g.key" :value="g.key" :label="g.label" />
                </el-select>
                <p v-if="form.gen_mode === 'verbatim'" class="field-hint">
                  原文朗读：不改写、不丢字，逐句照读原文；此时「时长档」不适用（照读完整份原文），已收起。
                </p>
              </div>

              <!-- ⚠️ 时长档只对 AI 改写有意义：verbatim 照读完整份原文，选了也不会生效，
                   留着只会让用户以为「能调时长」→ 整个字段不出现（2026-10-12 产品决策）。
                   target_minutes 保持原值不静默清：切回改写模式时选择还在。 -->
              <div v-if="form.gen_mode !== 'verbatim'" class="field">
                <label class="field-sub-label">时长档</label>
                <div class="seg">
                  <button v-for="l in opts.length_presets" :key="l.minutes" type="button"
                          class="seg-btn" :class="{ active: form.target_minutes === l.minutes }"
                          @click="form.target_minutes = l.minutes">{{ l.label }}</button>
                </div>
              </div>
            </template>
          </AudioSourcePicker>
        </div>

        <div class="field field-group-start">
          <label class="field-label">视觉方案</label>
          <SkinPicker
            v-model="form.skin"
            v-model:inner-skin="form.inner_skin"
            v-model:skin-variant="form.skin_variant"
            :skins="opts.skins"
            :inner-skins="opts.inner_skins"
            :ambient-inner-skins="opts.ambient_inner_skins"
            :variants="opts.skin_variants"
            :precheck="precheckSkins"
            :previews="opts.previews"
          />
          <!-- ⚠️⚠️ 【数字人口播】形象行整块**暂停入口**（2026-10-14，产品决定：方案重想好之后
               再启动研发与规划）。后端 /options 的 skins 列表已同步注释「数字人口播」，
               用户暂时选不到该视觉风格 → 本行 v-if 条件永不为真；渲染链路与已有作品的
               播放、重新生成全部保留（不静默改作品）。
               恢复方法：① 去掉本外层注释；② 把下方 3 处「普通文字」行还原成 HTML 注释
               （原注释的尖括号已去掉，避免嵌套注释提前终止外层注释）；③ 后端两处 skins
               （/options 列表 + /precheck 预检）一起加回。

               [以下为暂停的原始内容]
               数字人口播的形象：只允许预置素材或用户本人上传（PRD 3.7.6 合规）
          <div v-if="form.skin === 'talking_head'" class="avatar-row">
            <div class="avatar-list">
              <button v-for="a in opts.avatars" :key="a.id" type="button" class="avatar-item"
                      :class="{ active: form.avatar_id === a.id }" @click="form.avatar_id = a.id">
                预置形象与上传形象**都**由后端下发 url（自 2026-10-12 起预置也是真图片）
                → 统一显示缩略图；加载失败时清空 url 自动回落中性圆点（不留破图框）。
                <img v-if="a.url" class="avatar-thumb" :src="a.url" :alt="a.label"
                     @error="a.url = ''" />
                <span v-else class="avatar-dot"
                      :style="a.tint != null ? { background: `hsl(${a.tint} 40% 52%)` } : {}" />
                {{ a.label }}
              </button>
            </div>
            <div class="avatar-actions">
              <div class="seg">
                <button v-for="p in opts.avatar_positions" :key="p.key" type="button"
                        class="seg-btn" :class="{ active: form.avatar_pos === p.key }"
                        @click="form.avatar_pos = p.key">{{ p.label }}</button>
              </div>
              <el-button size="small" :loading="uploading" @click="pickAvatar">上传本人形象</el-button>
              删除入口只对「上传形象」出现：预置形象不是文件、后端会拒。
              不静默改引用它的作品 —— 确认框里用 used_by 如实说明后果。
              <el-button v-if="curAvatarUpload" size="small" type="danger" plain
                         :loading="deletingAvatar" @click="removeAvatar">删除该形象</el-button>
              <input ref="avatarInput" type="file" accept="image/*" hidden @change="onAvatarPicked" />
            </div>
            <p class="field-hint">
              ⚠️ 形象为预置素材或本人照片；说话状态是<b>形象示意</b>（动作 + 光环），
              不是真实唇形同步。成片会带「AI 生成」标识。
              请勿上传他人肖像。{{ opts.hints.avatar_upload }}
            </p>
          </div>
               [暂停的原始内容结束]
          -->
          <!-- 自定义背景：解压型三皮肤（电台浅色 / 电台深色 / 氛围）共用一份全局背景库。
               ⚠️ 信息型四皮肤**不出现**这一行 —— 它们的每一寸画面都在承载可读内容，
                  换一张底图会破坏信息层级（名单以后端 `bg_skins` 为准，前端不写死）。 -->
          <div v-if="bgSkinOn" class="avatar-row">
            <div class="avatar-list">
              <!-- 「自带画面」是**默认态**，必须留一个入口能选回来：没有它，用户换过图之后
                   就再也回不到皮肤原样（只能删掉整张图或重建作品）。 -->
              <button type="button" class="avatar-item" :class="{ active: !form.bg_id }"
                      @click="form.bg_id = ''">
                <span class="bg-dot" />
                皮肤自带画面
              </button>
              <button v-for="b in opts.backgrounds" :key="b.id" type="button" class="avatar-item"
                      :class="{ active: form.bg_id === b.id }" @click="form.bg_id = b.id">
                <img v-if="b.url" class="bg-thumb" :src="b.url" :alt="b.label"
                     @error="b.url = ''" />
                <span v-else class="bg-dot" />
                {{ b.label }}
              </button>
            </div>
            <div class="avatar-actions">
              <el-button size="small" :loading="uploadingBg" @click="pickBg">上传背景图</el-button>
              <!-- 删除入口只对「上传的背景」出现：内置背景不是文件、后端会拒 -->
              <el-button v-if="curBgUpload" size="small" type="danger" plain
                         :loading="deletingBg" @click="removeBg">删除该背景</el-button>
              <input ref="bgInput" type="file" accept="image/*" hidden @change="onBgPicked" />
            </div>
            <p class="field-hint">
              {{ opts.hints.bg_upload || '请上传横版或竖版图片（短边 ≥ 720px）。' }}
            </p>
            <!-- ⚠️ 只说「会回落」，**不自动改作品**：作品表里的 bg_id 保持原值，
                 改不改由用户自己决定（静默改作品的画面是本项目明令禁止的）。 -->
            <p v-if="bgMissing" class="field-hint bad">
              这个作品原来用的那张背景已从背景库删除，成片会回落到「皮肤自带画面」。你可以重新选一张。
            </p>
          </div>
        </div>

        <div class="field">
          <label class="field-label">附加要求（可选）</label>
          <el-input v-model="form.instruction" type="textarea" :rows="2"
                    :maxlength="opts.hints.instruction_max || 500" show-word-limit
                    placeholder="例如：面向新同事，术语要解释一下；多举业务里的例子" />
        </div>

        <!-- 试听用的唯一 <audio>（音色与 BGM 共用，与播客同一状态机）：@playing 才关加载态。
             ⚠️ 必须放在**无条件**区域（不能进 self 插槽）：插槽卸载会带走元素，
                试听状态就会卡在「转圈」。 -->
        <audio ref="previewAudioEl" @playing="previewBusy = false"
               @ended="stopPreview" @error="onPreviewError"></audio>

        <p v-if="!opts.llm_ready" class="notice warn">
          还没有配置可用的大模型，生成讲稿与步点会失败。请先到「管理中心 → 模型配置」完成配置。
        </p>
        <p v-else-if="!opts.render_ready" class="notice info">
          质量自检通过后即可出片，但当前环境缺少渲染组件：<b>{{ opts.render_reason || '渲染器不可用' }}</b>
        </p>

        <div class="actions">
          <template v-if="!cur">
            <el-button type="primary" :loading="busy" :disabled="!opts.llm_ready" @click="createWork">
              创建作品
            </el-button>
            <span class="actions-hint">创建后即可生成讲稿与分镜</span>
          </template>
          <template v-else>
            <el-button :loading="busy" @click="saveParams">保存参数</el-button>
            <el-button type="primary" :loading="planning" :disabled="!canPlan" @click="runPlan(cur.id)">
              {{ planning ? '生成中…' : (hasScript ? '重新生成讲稿与步点' : '生成讲稿与分镜') }}
            </el-button>
          </template>
        </div>
        <p v-if="cur && planning" class="stage-line">
          <span class="stage-dot" />{{ stageText }}
        </p>
      </section>

      <!-- ============ 右：作品库 / 作品详情 ============ -->
      <section class="panel main-panel">
        <!-- —— 作品库 —— -->
        <template v-if="!cur">
          <div class="panel-head">
            <h3>作品库</h3>
            <span class="panel-head-note">{{ list.length }} 个作品</span>
          </div>
          <p v-if="!loading && !list.length" class="empty-line">
            还没有视频作品。在左边选好音轨来源与视觉方案，点「创建作品」开始。
          </p>
          <div class="work-grid">
            <button v-for="w in list" :key="w.id" type="button" class="work-card"
                    @click="openWork(w.id)">
              <div v-if="w.has_video && w.poster_name" class="work-thumb">
                <img :src="videoApi.posterUrl(w.id, w.video_bytes)" alt="" loading="lazy" />
              </div>
              <div class="work-card-top">
                <span class="work-title">{{ w.title }}</span>
                <span class="badge" :class="statusClass(w.status)">{{ cardStatusText(w) }}</span>
              </div>
              <div class="work-meta">
                <span>{{ w.skin_label }}</span>
                <span>{{ fmtMs(w.duration_ms) }}</span>
                <span v-if="w.has_video && w.video_bytes">{{ fmtBytes(w.video_bytes) }}</span>
                <span>{{ w.shot_count }} 分镜 / {{ w.step_count }} 步点</span>
              </div>
              <div class="work-meta">
                <span v-if="w.gate_skipped" class="bad-text">未通过质量自检</span>
                <span v-else-if="w.gate_passed === true" class="ok-text">质量自检通过</span>
                <span v-else-if="w.gate_passed === false" class="bad-text">质量自检未通过</span>
                <span v-else>未自检</span>
                <span>{{ w.audio_source === 'podcast' ? '复用播客音轨' : '本片音轨' }}</span>
                <span v-if="w.has_video">已成片</span>
              </div>
              <p v-if="w.error" class="work-error">{{ w.error }}</p>
            </button>
          </div>
        </template>

        <!-- —— 作品详情 —— -->
        <template v-else>
          <div class="panel-head">
            <el-button text @click="backToList">← 作品库</el-button>
            <h3 class="detail-title">{{ cur.title }}</h3>
            <span class="badge" :class="statusClass(cur.status)">{{ STATUS_LABEL[cur.status] || cur.status }}</span>
          </div>

          <!-- 提醒区：派生资源的同步状态（PRD 3.11.4 —— 不删旧成片，只标注对应哪一版配置） -->
          <div v-if="alerts.length" class="alerts">
            <p v-for="(a, i) in alerts" :key="i" class="notice" :class="a.level">{{ a.text }}</p>
          </div>

          <div class="stats-row">
            <div class="stat"><span class="stat-k">时长</span><span class="stat-v">{{ fmtMs(cur.duration_ms) }}</span></div>
            <div class="stat"><span class="stat-k">讲稿</span><span class="stat-v">{{ cur.segment_count }} 句</span></div>
            <div class="stat"><span class="stat-k">分镜</span><span class="stat-v">{{ cur.shot_count }}</span></div>
            <div class="stat"><span class="stat-k">视觉步点</span><span class="stat-v">{{ cur.step_count }}</span></div>
            <div class="stat"><span class="stat-k">最长静止</span><span class="stat-v">{{ fmtMs(cur.gate_max_still_ms) }}</span></div>
            <div class="stat">
              <span class="stat-k">定位命中率</span>
              <span class="stat-v">{{ fmtPct(cur.gate_hit_rate) }}</span>
            </div>
            <div class="stat"><span class="stat-k">视觉方案</span><span class="stat-v stat-v--text">{{ cur.skin_label }}</span></div>
            <div class="stat"><span class="stat-k">音轨</span>
              <span class="stat-v stat-v--text">{{ cur.has_audio ? (cur.audio_source === 'podcast' ? '复用播客' : '本片生成') : '缺失' }}</span>
            </div>
            <div v-if="cur.render_seconds > 0" class="stat">
              <span class="stat-k">上次渲染</span>
              <span class="stat-v">{{ Number(cur.render_seconds).toFixed(0) }} 秒</span>
            </div>
          </div>

          <div class="actions wrap">
            <el-button :loading="busy" @click="rerunGate">重跑质量自检</el-button>
            <el-button :disabled="!cur.has_timeline" @click="openSrt">导出字幕</el-button>
            <el-button :loading="busy" @click="toNote">转笔记</el-button>
            <el-button v-if="cur.has_video" @click="openExport">导出成片</el-button>
            <el-button @click="revealInFolder">在文件夹中定位</el-button>
            <el-button type="danger" plain @click="removeWork">删除作品</el-button>
          </div>
          <!-- 渲染（阶段 2）：闸门通过 → 入队 → 单线程串行出片。
               并发上限 1，超限不是失败而是排队并显示位次（PRD 验收 #34）。 -->
          <div class="render-box">
            <div class="actions wrap render-actions">
              <el-button type="primary" :loading="rendering" :disabled="!canRender"
                         @click="runRender(cur.id)">
                {{ rendering ? '渲染中…' : renderBtnText }}
              </el-button>
              <span v-if="rendering" class="actions-hint">
                可离开页面，渲染在后台继续；回来会自动接上进度
              </span>
              <span v-else-if="!opts.render_ready" class="actions-hint">{{ opts.render_reason }}</span>
            </div>
            <!-- 跳过质量闸门（PRD 3.9.7 / 验收 #33）：连续 3 次未通过才出现；
                 跳过 = 知情放弃质量保障，成片标注「未通过质量自检」并记录在案 -->
            <div v-if="canSkipGate" class="actions wrap skip-gate-row">
              <el-button type="danger" plain :loading="rendering" @click="skipGateRender">
                跳过质量自检直接渲染
              </el-button>
              <span class="actions-hint">
                已连续 {{ cur.gate_fail_count }} 次未通过；跳过表示知情放弃质量保障，成片将标注「未通过质量自检」
              </span>
            </div>
            <div v-if="rendering" class="render-bar">
              <el-progress :percentage="renderPct" :stroke-width="8" :show-text="false" />
              <span class="render-stage">{{ renderStageText }}</span>
            </div>
            <p v-if="renderError" class="notice warn">{{ renderError }}</p>
            <p v-if="showQuality" class="render-quality">
              上次渲染：{{ renderQualityText }}
            </p>
            <p v-else-if="!cur.has_video && !rendering" class="field-hint">
              还没有成片。质量自检通过后点「{{ renderBtnText }}」即可出片。
            </p>
          </div>

          <!-- 质量自检（8 条判据） -->
          <h4 class="sec-title">质量自检<template v-if="cur.gate_passed === true"> · 全部通过</template></h4>
          <p v-if="!gateItems.length" class="empty-line">
            还没有自检结果。点上面的「生成讲稿与分镜」，或在改完讲稿后点「重跑质量自检」。
          </p>
          <table v-else class="gate-table">
            <thead>
              <tr><th>判据</th><th class="num">实测</th><th class="num">阈值</th><th>结果</th><th>建议</th></tr>
            </thead>
            <tbody>
              <tr v-for="it in gateItems" :key="it.key" :class="{ bad: !it.ok && !it.na }">
                <td>{{ it.label }}</td>
                <td class="num">{{ fmtGateValue(it) }}</td>
                <td class="num">{{ it.na ? '—' : fmtGateValue({ ...it, value: it.threshold }) }}</td>
                <td>
                  <span v-if="it.na" class="muted">不适用</span>
                  <span v-else-if="it.ok" class="ok-text">通过</span>
                  <span v-else class="bad-text">未通过</span>
                </td>
                <td class="fix">
                  <template v-if="!it.ok && !it.na">
                    <el-button v-if="it.fix === 'fill_steps'" size="small" type="primary" plain
                               :loading="refilling" @click="refillSteps">一键补步</el-button>
                    <el-button v-else-if="it.fix === 'manual_locate' && unlocatedRows.length"
                               size="small" type="primary" plain
                               @click="showUnlocated = !showUnlocated">
                      {{ showUnlocated ? '收起未命中句' : `未命中句（${unlocatedRows.length}）` }}
                    </el-button>
                    <div class="fix-hint">{{ FIX_HINT[it.fix] || '' }}</div>
                  </template>
                </td>
              </tr>
            </tbody>
          </table>

          <!-- 未命中句手动定位（判据 4 修复入口，PRD 3.9.5/3.9.6） -->
          <div v-if="showUnlocated && unlocatedRows.length" class="unlocated-box">
            <p class="field-hint">
              下面这些句子没能在材料原件里定位到原文。从材料里挑一段<b>原样存在</b>的连续短语，
              点「指定」把它钉到该句的时间点上：
            </p>
            <div v-for="u in unlocatedRows" :key="u.seg" class="unlocated-row">
              <span class="unlocated-seg">#{{ u.seg }}</span>
              <span class="unlocated-text" :title="u.text">{{ u.text }}</span>
              <el-input v-model="u.anchor" size="small"
                        placeholder="材料原文中的一段连续短语（≥2 字）" />
              <el-button size="small" type="primary" plain :loading="u.busy"
                         @click="manualLocate(u)">指定</el-button>
            </div>
          </div>

          <!-- 讲稿（可逐句编辑；改一句丢该句时间戳，不触发重新合成） -->
          <h4 class="sec-title">
            讲稿
            <span class="sec-note">改过 {{ dirtyCount }} 句 · 时间轴来自音轨，改动后请重跑自检</span>
            <el-button v-if="scriptDirty" size="small" type="primary" :loading="busy" @click="saveScript">
              保存讲稿
            </el-button>
          </h4>
          <p v-if="!scriptDraft.length" class="empty-line">还没有讲稿。点「生成讲稿与分镜」。</p>
          <div v-else class="script-list">
            <div v-for="(s, i) in scriptDraft" :key="i" class="script-row">
              <span class="script-time">{{ fmtMs(s.start_ms) }}</span>
              <span class="script-spk">{{ s.speaker === 'expert' ? '专家' : '主讲' }}</span>
              <el-input v-model="s.text" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                        @input="scriptDirty = true" />
            </div>
          </div>

          <!-- 分镜 -->
          <h4 class="sec-title">分镜<span class="sec-note">按句间留白切分，是节奏单位<template v-if="cur.has_video">；点击行跳到该分镜起点</template></span></h4>
          <p v-if="!cur.shots || !cur.shots.length" class="empty-line">还没有分镜。</p>
          <table v-else class="mini-table">
            <thead><tr><th class="num">#</th><th class="num">起</th><th class="num">止</th><th class="num">句数</th><th>首个高亮框</th></tr></thead>
            <tbody>
              <tr v-for="(sh, i) in cur.shots" :key="i"
                  :class="{ 'shot-clickable': cur.has_video }" @click="seekShot(sh)">
                <td class="num">{{ i + 1 }}</td>
                <td class="num">{{ fmtMs(sh.at) }}</td>
                <td class="num">{{ fmtMs(sh.end) }}</td>
                <td class="num">{{ sh.count }}</td>
                <td class="muted">{{ sh.hit || '—' }}</td>
              </tr>
            </tbody>
          </table>

          <!-- 视觉步点 -->
          <h4 class="sec-title">
            视觉步点
            <span class="sec-note">{{ cur.step_count }} 个（画面变化的唯一单位）</span>
            <el-button v-if="cur.step_count > 40" size="small" text @click="showSteps = !showSteps">
              {{ showSteps ? '只看前 40 条' : `展开全部 ${cur.step_count} 条` }}
            </el-button>
          </h4>
          <p v-if="!cur.steps || !cur.steps.length" class="empty-line">还没有步点。</p>
          <table v-else class="mini-table">
            <thead><tr><th class="num">时刻</th><th class="num">页</th><th>锚点</th><th>命中原文</th><th class="num">位置</th></tr></thead>
            <tbody>
              <tr v-for="(st, i) in visibleSteps" :key="i">
                <td class="num">{{ fmtMs(st.at) }}</td>
                <td class="num">{{ st.page || '—' }}</td>
                <td class="anchor">{{ st.anchor || st.t || '—' }}</td>
                <td class="muted">{{ st.hit || (st.group !== undefined ? `元素组 ${st.group}` : '—') }}</td>
                <td class="num muted">{{ st.x !== undefined ? `${st.x}% / ${st.y}%` : '—' }}</td>
              </tr>
            </tbody>
          </table>

          <!-- 音轨 / 成片 -->
          <h4 class="sec-title">音轨与成片</h4>
          <audio v-if="cur.has_audio" class="player" :src="videoApi.audioUrl(cur.id, cur.audio_bytes)" controls />
          <p v-else class="empty-line">还没有音轨。</p>
          <template v-if="cur.has_video">
            <!-- V6：浏览器缺 H.264 解码 → 提示导出后用本地播放器（PRD 3.10.7） -->
            <p v-if="!canPlayMp4 || videoPlayError" class="notice warn">
              当前环境无法内嵌播放（缺少 H.264 解码支持）。请用「导出成片」下载后，换本地播放器观看。
            </p>
            <template v-else>
              <video ref="videoEl" class="player" :src="videoApi.fileUrl(cur.id, cur.video_bytes)"
                     :poster="cur.poster_name ? videoApi.posterUrl(cur.id, cur.video_bytes) : ''"
                     controls @loadedmetadata="applySubMode" @play="onVideoPlay"
                     @pause="onVideoPause" @ended="onVideoEnded" @error="onVideoError">
                <!-- V3：播放器内字幕（WebVTT 轨；与 SRT 导出同一条时间轴，PRD 3.10.5） -->
                <track v-if="cur.has_timeline" kind="subtitles" srclang="zh" label="中文字幕"
                       :src="videoApi.vttUrl(cur.id, cur.audio_bytes)" default />
              </video>
              <div class="player-bar">
                <el-button v-if="cur.has_timeline" size="small" text @click="toggleSub">
                  {{ subOn ? '关闭字幕' : '开启字幕' }}
                </el-button>
              </div>
            </template>
          </template>
        </template>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHead from '../components/PageHead.vue'
import SkinPicker from '../components/SkinPicker.vue'
import AudioSourcePicker from '../components/AudioSourcePicker.vue'
import { statsApi, videoApi, podcastApi } from '../api'
import { errMsg } from '../api/http'
import { streamSSE } from '../utils/sse'

// ============================================================================
// ⚠️ 声明顺序 = 执行顺序：setup 里的 computed / watch 会**立即求值一次**，
//    它们读到的 ref 必须已经声明（否则 TDZ → 整页白屏，构建与文本断言都全绿）。
//    所以：ref/reactive → computed → watch → 函数。
// ============================================================================

const route = useRoute()
const router = useRouter()

const STATUS_LABEL = {
  draft: '草稿', scripting: '生成中', gate_check: '待自检通过',
  queued: '排队渲染', rendering: '渲染中', finished: '已完成', failed: '失败',
}
// 闸门给的是机器可执行的 fix 键；按钮动作在模板里，这里是照着做的说明文字。
const FIX_HINT = {
  fill_steps: '只重跑定位与步点生成（不动讲稿与音轨），补完自动重跑自检',
  manual_locate: '从材料原文里挑一段原样存在的短语手动指定；或换一份材料 / 改用结构图方案',
  resynth_audio: '音轨对应的是上一版文案：复用播客 → 去「AI 播客」页重新合成；本片音轨 → 目前需新建作品重新生成',
  free_space: '清理磁盘空间后重试',
}
const STAGE_LABEL = {
  audio: '准备音轨', script: '生成讲稿与分镜', anchor: '挑选定位锚点',
  locate: '在材料原件上定位', plan: '编排视觉步点', gate: '质量自检',
}
// ⚠️ 这个常量必须声明在任何引用它的 computed **之前**：
//    setup 里的 computed 注册即同步求值一次，读到未声明的 const 会 TDZ → 整页白屏
//    （构建与文本断言都全绿，只有真机打开才发现）。
const RENDER_STAGE_LABEL = {
  queued: '排队中', stage: '准备舞台页', capture: '录制画面',
  mux: '合成音视频', finalize: '写入成片', done: '完成',
}
// 后端 /videos/options 目前不下发 highlight_semantics（播客页有）。
// 这里给一份最小兜底，避免「三色划线」来源在视频页无法选择颜色。
const HIGHLIGHT_FALLBACK = [
  { key: 'green', label: '绿色' }, { key: 'yellow', label: '黄色' }, { key: 'blue', label: '蓝色' },
]

const loading = ref(true)
const busy = ref(false)
const planning = ref(false)
const uploading = ref(false)
const deletingAvatar = ref(false)
const showSteps = ref(false)
const scriptDirty = ref(false)
// 渲染（阶段 2）：与生成同构的「启动或附着」—— 离开页面任务照跑，回来重新订阅。
const rendering = ref(false)
const renderPct = ref(0)
const renderStage = ref('')
const renderPos = ref(0)
const renderError = ref('')
// 渲染报告快照（对齐 99 分位 / 平均偏差 / 重复帧占比 / 抓帧数 / 耗时）。
// ⚠️ 这是 PRD 3.8.6 对齐自检的**唯一可观测出口**：不出这个，界面只能看到「出了个片」，
//    看不到「对得准不准」，自检哪天被改坏也没人发现。
const renderQuality = ref(null)
let renderAbort = null
// 作品库轮询（2026-10-12 修「状态更新不及时」）：
// 只在列表里还有「排队中 / 渲染中」的作品时挂定时器，全跑完立刻停（见 loadList/armListPoll）。
const LIST_POLL_MS = 2500
let listPollTimer = 0
// 闸门修复（V5 / PRD 3.9.6）：一键补步 + 未命中句手动指定
const refilling = ref(false)
const showUnlocated = ref(false)
const unlocatedRows = ref([])   // [{seg, text, anchor, busy}]
// 播放器（V3 字幕开关 / V4 分镜跳转 / V6 降级 / V2 播放埋点）
const videoEl = ref(null)
const subOn = ref(true)
const canPlayMp4 = ref(true)
const videoPlayError = ref(false)
let playStartAt = 0
let playAccum = 0

const opts = ref({
  source_types: [], materials: [], notes: [], podcasts: [], voices: [],
  skins: [], inner_skins: [], avatars: [], avatar_positions: [],
  // 电台浅色/深色的预设变体元数据（{radio:[...], night:[...]}，后端下发）
  skin_variants: {},
  // 全局背景库（解压型三皮肤共用）+「支持自定义背景的皮肤」名单（以后端下发为准）
  backgrounds: [], bg_skins: [], previews: [],
  length_presets: [], gen_modes: [], hints: {}, output: {},
  render_ready: false, render_reason: '', llm_ready: false,
})
const list = ref([])
const cur = ref(null)
const precheck = ref(null)
/** 讲稿草稿：**独立于 cur.script**，编辑期间不污染详情对象（取消/保存语义清晰） */
const scriptDraft = ref([])
const stage = reactive({ key: '', detail: {} })
const avatarInput = ref(null)
const bgInput = ref(null)
const uploadingBg = ref(false)
const deletingBg = ref(false)
let planAbort = null

// ---------- 试听（音色与背景音乐共用，与播客同一状态机） ----------
// 三态：previewId 空 = 待试听（▶）；previewBusy = 等待音频到达（转圈）；
// 真正出声后 = 播放中（■，再点即停）。
// ⚠️ previewBusy 由 <audio> 的 @playing 事件关闭（出声才关），不是 fetch 完就关 ——
//    首次试听要现场合成/截取，fetch 完成到出声之间还有解码时间。
const previewAudioEl = ref(null)
const previewId = ref('')          // 正在试听的对象 id（空 = 未在试听）
const previewBusy = ref(false)     // 正在等待音频到达（首次要现场合成，需数秒）
let previewObjUrl = ''
function stopPreview() {
  const el = previewAudioEl.value
  if (el) {
    el.pause()
    el.removeAttribute('src')
    try { el.load() } catch { /* 已销毁，忽略 */ }
  }
  if (previewObjUrl) { URL.revokeObjectURL(previewObjUrl); previewObjUrl = '' }
  previewId.value = ''
  previewBusy.value = false
}
function onPreviewError() {
  stopPreview()
  ElMessage.error('试听失败，请稍后重试')
}
// 点同一个 = 停止（试听按钮天然是开关）
async function playPreview(url, id, what) {
  if (!id) return ElMessage.info(`请先选择${what}`)
  if (previewId.value === id) return stopPreview()
  stopPreview()
  // 成片正在播时先暂停，否则两个声音叠在一起（与播客暂停主播放器同款）
  try { videoEl.value && videoEl.value.pause() } catch { /* 忽略 */ }
  previewId.value = id
  previewBusy.value = true
  try {
    // 用 fetch 取 blob，而不是把 URL 直接交给 <audio>：首次要现场处理（音色需合成、
    // BGM 需截取，都要数秒），fetch 能拿到明确的失败原因，也能自己控制加载态
    const res = await fetch(url)
    if (!res.ok) {
      let msg = `HTTP ${res.status}`
      try {
        const j = await res.json()
        if (j && j.detail) msg = j.detail
      } catch { /* 非 JSON 响应 */ }
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
async function previewVoice(id) {
  return playPreview(podcastApi.voicePreviewUrl(id), id, '一个具体音色')
}

const form = reactive({
  title: '',
  audio_source: 'podcast',
  podcast_id: null,
  source_type: 'article',
  ref_id: null,
  note_ids: [],
  highlight_colors: ['green', 'yellow', 'blue'],
  pasted_text: '',
  gen_mode: 'fast',
  target_minutes: 3,
  skin: 'source',
  inner_skin: '',
  // 皮肤预设变体（电台浅色/深色各 3 套）。'' = 默认套。
  skin_variant: '',
  avatar_id: 'preset_01',
  avatar_pos: 'right',
  // 自定义背景图（解压型三皮肤）。空 = 用皮肤自带画面。
  bg_id: '',
  instruction: '',
  // 本片音色（作品级，与播客 voice_map 同构）。host 为空 = 跟随设置页的语音设置。
  // ⚠️ 选中作品时必须能还原它当时用的音色（回填不完整比不回填更危险）。
  voice_map: { host: '' },
  // 背景音乐（作品级，与播客 bgm_* 同构）。bgm_id 空 = 不加；volume 单位 dB。
  // 仅「本片单独生成」消费；复用播客时此行不显示、提交也不带（音轨里已含播客自己的 BGM）。
  bgm_id: '',
  bgm_volume: -20,
  // 画面署名（电台浅色/深色画面底部那行字）。空 = 回落作品标题 ——
  // ⚠️ 与 title 解耦不是冗余：标题管列表与导出文件名、可以长；画面那行字空间有限。
  brand_text: '',
})

// 画面署名只对电台浅色/深色有意义 —— 只有这两个皮肤的画面底部有「署名」这个位置。
// ⚠️ 与后端 svc.BRAND_SKINS 同口径（皮肤 key 是硬编码常量，两边必须一致）。
// ⚠️ 必须声明在 form **之后**：computed 注册即同步求值，放前面会 TDZ → 整页白屏。
const brandSkin = computed(() => form.skin === 'radio' || form.skin === 'night')

// ---------- 背景音乐（曲库全局，与播客共用 /api/podcasts/bgm 系列接口） ----------
// ⚠️ 整块必须声明在 form / cur **之后**：computed 注册即同步求值（TDZ 白屏教训），
//    bgmSelectOptions / bgmMissing 都读 form 与 cur。
const bgmTracks = ref({ builtin: [], mine: [], volume: { min: -40, max: -3, default: -20 } })
const bgmUploading = ref(false)
const bgmFileEl = ref(null)
const bgmVolumeRange = computed(() => bgmTracks.value.volume || { min: -40, max: -3, default: -20 })
const bgmSelectOptions = computed(() => {
  const t = bgmTracks.value || {}
  const out = []
  for (const x of t.builtin || []) {
    out.push({ id: x.id, name: x.name, desc: x.desc || x.scene || '', kind: 'builtin' })
  }
  for (const x of t.mine || []) {
    out.push({ id: x.id, name: x.name, desc: '我上传的', kind: 'user' })
  }
  // 当前绑定的素材已被删除：留一个如实显示的选项（_ghost），否则下拉会显示成空白 id。
  // ghost 不给试听按钮（文件已不在，点了必然 404）。
  const curId = form.bgm_id
  if (curId && !out.some((o) => o.id === curId)) {
    out.push({ id: curId, name: (cur.value && cur.value.bgm_name) || '已失效的背景音乐',
               desc: '已失效', kind: 'user', _ghost: true })
  }
  return out
})
// 引用的背景音乐是否已被删除。只判定用户上传的（u-）：内置氛围音是程序生成，不会丢
const bgmMissing = computed(() => {
  const id = form.bgm_id
  if (!id || !id.startsWith('u-')) return false
  return !(bgmTracks.value.mine || []).some((x) => x.id === id)
})
async function loadBgm() {
  try {
    const { data } = await podcastApi.bgmList()
    bgmTracks.value = data
  } catch { /* 曲库加载失败不挡主流程：下拉只剩「不加背景音乐」 */ }
}
// 试听与音色共用同一个 <audio> 与同一套三态（见上方 playPreview）——任何时刻只响一路，
// 与播客完全同构（音色 id 与曲目 id 命名空间天然不撞，不需要前缀区分）。
function previewBgm(id) {
  return playPreview(podcastApi.bgmPreviewUrl(id), id, '一首背景音乐')
}
async function onBgmPicked(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!file) return
  bgmUploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const { data } = await podcastApi.uploadBgm(fd)
    await loadBgm()
    form.bgm_id = data.id
    ElMessage.success(`已添加背景音乐：${data.name}`)
  } catch (err) {
    ElMessage.error(errMsg(err, '上传背景音乐失败'))
  } finally {
    bgmUploading.value = false
  }
}

const isPodcastSource = computed(() => form.audio_source === 'podcast')
const precheckSkins = computed(() => (precheck.value?.skins || null))
const gateItems = computed(() => cur.value?.gate_report?.items || [])
const hasScript = computed(() => (cur.value?.segment_count || 0) > 0)
const canPlan = computed(() => {
  if (!cur.value) return false
  if (isPodcastSource.value) return !!cur.value.podcast_id
  if (form.source_type === 'text') return !!form.pasted_text.trim()
  if (form.source_type === 'review') return true
  if (form.source_type === 'notes') return form.note_ids.length > 0
  return !!form.ref_id
})
const stageText = computed(() => {
  const base = STAGE_LABEL[stage.key] || '正在准备'
  const d = stage.detail || {}
  if (stage.key === 'audio' && d.done !== undefined) return `${base}（${d.done}/${d.total} 句）`
  if (stage.key === 'anchor' && d.source_chars !== undefined) {
    return `${base}（已把 ${d.source_chars} 字原文交给模型）`
  }
  if (stage.key === 'locate' && d.pages !== undefined) return `${base}（${d.pages} 页）`
  if (stage.key === 'plan' && d.events_target !== undefined) return `${base}（目标 ${d.events_target} 个视觉事件）`
  return base
})
const highlightChoices = computed(() => {
  const s = opts.value.highlight_semantics
  if (s && typeof s === 'object' && Object.keys(s).length) {
    return Object.entries(s).map(([k, v]) => ({ key: k, label: (v && v.label) || k }))
  }
  return HIGHLIGHT_FALLBACK
})
const dirtyCount = computed(() => (scriptDirty.value ? '有' : '0'))
// 渲染前置：闸门通过 + 有音轨 + 渲染器可用 + （没有成片 或 成片已过时）
const canRender = computed(() => {
  const v = cur.value
  if (!v || rendering.value) return false
  if (!opts.value.render_ready) return false
  if (v.gate_passed !== true || !v.has_audio) return false
  if (!v.has_video) return true
  return (v.stale_kinds || []).length > 0
})
// 渲染按钮的文案要能回答「点了会发生什么」：新建 / 重建 / 失败后重试 / 已是最新
const renderBtnText = computed(() => {
  const v = cur.value
  if (!v) return '开始渲染'
  // ⚠️ 失败态必须能被读成「重试」：PRD 5.1 #35/#44/#46 要求「重试按钮」
  if (v.status === 'failed') return '重试渲染'
  if ((v.stale_kinds || []).length > 0) return '重新渲染（成片对应上一版）'
  return v.has_video ? '重新渲染' : '开始渲染'
})
// 跳过质量闸门（PRD 3.9.7 / 验收 #33）：连续 3 次未通过才允许；跳过 = 知情放弃质量保障
const canSkipGate = computed(() => {
  const v = cur.value
  if (!v || rendering.value) return false
  if (!opts.value.render_ready) return false
  return v.gate_passed === false && (v.gate_fail_count || 0) >= 3
})
const renderStageText = computed(() => {
  if (renderStage.value === 'queued' || (!renderStage.value && renderPos.value > 0)) {
    return renderPos.value > 1 ? `排队中：前面还有 ${renderPos.value - 1} 部` : '排队中'
  }
  const base = RENDER_STAGE_LABEL[renderStage.value] || '渲染中'
  if (renderStage.value === 'capture') return `${base} ${renderPct.value}%`
  return base
})
// ⚠️ 必须声明在 renderQuality 之后：setup 里 computed 注册即同步求值一次（TDZ 会整页白屏）
const renderQualityText = computed(() => {
  const r = renderQuality.value
  if (!r) return ''
  const out = []
  if (r.skew_ms !== undefined && r.skew_ms !== null) out.push(`对齐 99 分位 ${r.skew_ms} ms`)
  if (r.skew_mean_ms !== undefined && r.skew_mean_ms !== null) out.push(`平均 ${r.skew_mean_ms} ms`)
  if (r.dup_ratio !== undefined && r.dup_ratio !== null) {
    out.push(`重复帧 ${(Number(r.dup_ratio) * 100).toFixed(2)}%`)
  }
  if (r.captured !== undefined) out.push(`抓帧 ${r.captured}`)
  if (r.elapsed_s !== undefined) out.push(`耗时 ${Number(r.elapsed_s).toFixed(1)} 秒`)
  return out.join(' · ')
})
const showQuality = computed(() => !!renderQualityText.value && !rendering.value)
const visibleSteps = computed(() => {
  const all = cur.value?.steps || []
  return showSteps.value || all.length <= 40 ? all : all.slice(0, 40)
})
const alerts = computed(() => {
  const v = cur.value
  if (!v) return []
  const out = []
  if (v.error) out.push({ level: 'warn', text: `质量自检未通过：${v.error}` })
  if (v.gate_skipped && v.has_video) out.push({ level: 'warn', text: '当前成片是「跳过质量自检」渲染的（未通过质量自检）：风险已由你确认承担；自检重新通过并渲染后，这个标注会自动去掉。' })
  if (v.audio_stale) out.push({ level: 'warn', text: '音轨与当前讲稿不一致（音轨是上一版文案的产物）：改完文案请重新合成或重新选择来源。' })
  for (const label of v.stale_labels || []) out.push({ level: 'info', text: `成片对应的是上一版：${label}（旧成片不会自动删除，可一键重建）` })
  if (v.source_missing) out.push({ level: 'warn', text: '来源材料已被删除或移动，画面定位可能失效。' })
  if (v.has_video && !v.stale_labels?.length) out.push({ level: 'info', text: '成片与当前配置一致。' })
  return out
})

// ============================================================================
// 数据加载
// ============================================================================
async function loadOptions() {
  const { data } = await videoApi.options()
  opts.value = { ...opts.value, ...data }
}
/** 列表里是否还有「排队中 / 渲染中」的作品 —— 决定轮询要不要继续。 */
function hasTransient() {
  return (list.value || []).some((w) => w && (w.status === 'queued' || w.status === 'rendering'))
}
function stopListPoll() {
  if (listPollTimer) { clearTimeout(listPollTimer); listPollTimer = 0 }
}
/**
 * 作品库轮询（2026-10-12 修：作品状态更新不及时）。
 *
 * ⚠️ 为什么必须轮询：列表接口里的 `render_stage / render_pct / render_position` 是后端
 *    **每次现算的内存态**，但前端此前只在渲染**收尾**时才拉一次列表 → 从「排队渲染」切到
 *    「渲染中」这一整段（分钟级）界面完全不动，用户以为卡在排队（其实早在录屏了）。
 * ⚠️ 只在有 transient 作品时挂定时器、跑完立刻停 —— 不做没人看的常驻请求。
 * ⚠️ 拉列表失败就停掉轮询（不在 catch 里重排）：接口连着报错时别把它变成死循环刷屏，
 *    状态自然会在用户下次操作触发的 loadList 里恢复。
 */
function armListPoll() {
  if (listPollTimer) return
  listPollTimer = setTimeout(async () => {
    listPollTimer = 0
    try {
      await loadList()
    } catch {
      stopListPoll()
    }
  }, LIST_POLL_MS)
}
async function loadList() {
  const { data } = await videoApi.list()
  list.value = data || []
  // 每次拉完都按最新状态决定要不要续下一拍（跑完自动停）
  if (hasTransient()) armListPoll()
  else stopListPoll()
}
/** 把渲染的实时态写回列表里对应那张卡片（不等下一拍轮询，点完立刻有反馈）。 */
function patchRenderLive(id, stage, pct, position) {
  const it = (list.value || []).find((w) => w && String(w.id) === String(id))
  if (!it) return
  const s = stage || ''
  it.status = (!s || s === 'queued') ? 'queued' : 'rendering'
  it.render_stage = s
  if (pct !== undefined && pct !== null) it.render_pct = pct
  if (position !== undefined && position !== null) it.render_position = position
}
async function loadDetail(id, { quiet = false } = {}) {
  try {
    const { data } = await videoApi.detail(id)
    applyVideo(data)
  } catch (e) {
    if (!quiet) ElMessage.error(errMsg(e, '作品详情加载失败'))
    if (route.params.id) router.replace('/video')
  }
}
async function loadPrecheck(id) {
  try {
    const { data } = await videoApi.precheck(id)
    precheck.value = data
  } catch {
    // 预检失败 = 没有预检结果 → SkinPicker 一律放行（「查不到」不等于「不可用」）
    precheck.value = null
  }
}

/** 把后端返回的作品灌进界面状态（详情 ↔ 表单 ↔ 讲稿草稿三处一起同步） */
function applyVideo(v) {
  cur.value = v
  scriptDraft.value = (v.script || []).map((s) => ({ ...s }))
  scriptDirty.value = false
  // 未命中句面板（V5）：保留仍在清单里的句子已填的短语，移除已解决的
  const unl = (v.gate_report && v.gate_report.unlocated) || []
  const prevAnchors = new Map(unlocatedRows.value.map((r) => [r.seg, r.anchor]))
  unlocatedRows.value = unl.map((seg) => ({
    seg,
    text: ((v.script || [])[seg - 1] || {}).text || '',
    anchor: prevAnchors.get(seg) || '',
    busy: false,
  }))
  if (!unlocatedRows.value.length) showUnlocated.value = false
  form.title = v.title || ''
  form.audio_source = v.audio_source || 'podcast'
  form.podcast_id = v.podcast_id ?? null
  form.source_type = v.source_type || 'article'
  form.gen_mode = v.gen_mode || 'fast'
  form.target_minutes = v.target_minutes || 3
  form.skin = v.skin || 'source'
  // ⚠️ 口播的内容层**必须有值**：空着会让用户以为「还没选」（旧数据里存在这种空值），
  //    而后端生成/渲染实际按结构图走 → 界面与真实绑定不一致（回填不完整比不回填更危险）。
  //    ambient 同理，但默认是 none（纯氛围）。
  form.inner_skin = v.inner_skin
    || (v.skin === 'talking_head' ? 'graph' : (v.skin === 'ambient' ? 'none' : ''))
  form.skin_variant = v.skin_variant || ''
  // 画面署名照原值回填（空 = 跟随标题，界面上要如实显示成空、不能替用户填上标题，
  // 否则用户一保存就把「跟随标题」这个状态固化成当时的标题了）
  form.brand_text = v.brand_text || ''
  form.avatar_id = v.avatar_id || 'preset_01'
  form.avatar_pos = v.avatar_pos || 'right'
  // ⚠️ 照原值回填，**不做「文件已不在就清空」的兜底**：清空等于静默改作品
  //    （用户什么都没做，画面却回落到自带背景）。缺失只做提示，见 bgMissing。
  form.bg_id = v.bg_id || ''
  form.instruction = v.instruction || ''
  form.voice_map.host = (v.voice_map || {}).host || ''
  // BGM 同音色纪律：照原值回填（已失效的素材也回填，由 bgmMissing 如实提示，不静默清空）
  form.bgm_id = v.bgm_id || ''
  form.bgm_volume = v.bgm_volume ?? -20
  // 来源回填：只在来源是材料/笔记时能精确还原，其余（划线/错题/粘贴）保留原值不回填
  const refs = v.source_refs || []
  const idOf = (t) => {
    const r = refs.find((x) => x && x.type === t && x.id !== undefined)
    return r ? r.id : null
  }
  if (v.source_type === 'article' || v.source_type === 'highlights') form.ref_id = idOf('material')
  if (v.source_type === 'notes') {
    form.note_ids = refs.filter((x) => x && x.type === 'note').map((x) => x.id)
  }
  const pasted = refs.find((x) => x && x.type === 'text')
  form.pasted_text = pasted ? (pasted.content || '') : ''
}

async function reloadAll() {
  loading.value = true
  try {
    await Promise.all([loadOptions(), loadList(), loadBgm()])
    const id = route.params.id ? Number(route.params.id) : null
    if (id) {
      // ⚠️ 直接打开 /video/:id（或刷新页面）时 watch 不会触发 → 这里必须自己接
      //    进度，否则界面是「没人在跑」的假状态（任务其实在后台跑着）。
      await loadDetail(id)
      await loadPrecheck(id)
      await resumeIfRunning(id)
      await resumeRenderIfRunning(id)
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '加载失败'))
  } finally {
    loading.value = false
  }
}

// ============================================================================
// 表单 → 请求体
// ============================================================================
function refIds() {
  if (form.source_type === 'notes') return form.note_ids.slice()
  if (form.source_type === 'review' || form.source_type === 'text') return []
  return form.ref_id ? [form.ref_id] : []
}
function createPayload() {
  const base = {
    title: form.title || '',
    skin: form.skin,
    inner_skin: form.inner_skin,
    skin_variant: form.skin_variant || '',
    brand_text: form.brand_text || '',
    avatar_id: form.avatar_id,
    avatar_pos: form.avatar_pos,
    bg_id: form.bg_id || '',
    audio_source: form.audio_source,
    voice_map: { host: form.voice_map.host || '' },
    instruction: form.instruction || '',
  }
  if (form.audio_source === 'podcast') return { ...base, podcast_id: form.podcast_id }
  return {
    ...base,
    source_type: form.source_type,
    ref_ids: refIds(),
    highlight_colors: form.source_type === 'highlights' ? form.highlight_colors.slice() : null,
    pasted_text: form.pasted_text || '',
    gen_mode: form.gen_mode,
    target_minutes: form.target_minutes,
    // 背景音乐只随「本片单独生成」提交（复用播客的音轨已含播客自己的 BGM）
    bgm_id: form.bgm_id || '',
    bgm_volume: form.bgm_volume ?? -20,
    style: 'solo',            // 视频是讲解型，固定单人
  }
}
function updatePayload() {
  // ⚠️ 只在必要时带来源字段：`_apply_source` 一旦被触发就会**重跑素材抽取**并重置音轨来源，
  //    仅改标题/方案时不该发生这种事。
  const p = {
    title: form.title || undefined,
    skin: form.skin,
    inner_skin: form.inner_skin,
    skin_variant: form.skin_variant || '',
    // ⚠️ 必须**总是带**（不能像 title 那样用 undefined 表示「不动」）：
    //    '' 是有意义的值 —— 表示「改回跟随作品标题」。
    brand_text: form.brand_text || '',
    avatar_id: form.avatar_id,
    avatar_pos: form.avatar_pos,
    bg_id: form.bg_id || '',
    instruction: form.instruction || '',
  }
  if ((form.voice_map.host || '') !== (((cur.value || {}).voice_map || {}).host || '')) {
    p.voice_map = { host: form.voice_map.host || '' }
  }
  // BGM 与音色同款纪律：变了才带；音量只在有曲子时有意义（没曲子带音量是脏数据）
  const bgmDirty = (form.bgm_id || '') !== ((cur.value || {}).bgm_id || '')
    || ((form.bgm_id || '') && (form.bgm_volume ?? -20) !== ((cur.value || {}).bgm_volume ?? -20))
  if (bgmDirty) {
    p.bgm_id = form.bgm_id || ''
    p.bgm_volume = form.bgm_volume ?? -20
  }
  if (form.audio_source === 'podcast') {
    if (form.podcast_id !== (cur.value?.podcast_id ?? null)) p.podcast_id = form.podcast_id
  } else {
    if (form.source_type !== (cur.value?.source_type || 'article')
      || form.gen_mode !== cur.value?.gen_mode
      || form.target_minutes !== cur.value?.target_minutes
      || JSON.stringify(refIds()) !== JSON.stringify(
        (cur.value?.source_refs || []).filter((r) => r && r.type === 'material').map((r) => r.id))) {
      p.source_type = form.source_type
      p.ref_ids = refIds()
      p.highlight_colors = form.source_type === 'highlights' ? form.highlight_colors.slice() : null
      p.pasted_text = form.pasted_text || ''
      p.gen_mode = form.gen_mode
      p.target_minutes = form.target_minutes
    }
  }
  return p
}

// ============================================================================
// 动作
// ============================================================================
async function createWork() {
  busy.value = true
  try {
    const { data } = await videoApi.create(createPayload())
    await loadList()
    ElMessage.success('作品已创建')
    router.push(`/video/${data.id}`)
  } catch (e) {
    ElMessage.error(errMsg(e, '创建失败'))
  } finally {
    busy.value = false
  }
}

async function saveParams() {
  if (!cur.value) return
  busy.value = true
  try {
    const { data } = await videoApi.update(cur.value.id, updatePayload())
    applyVideo(data)
    await loadList()
    await loadPrecheck(data.id)
    ElMessage.success('参数已保存（讲稿与步点需要重新生成才会跟上）')
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    busy.value = false
  }
}

async function removeWork() {
  if (!cur.value) return
  try {
    await ElMessageBox.confirm(
      `删除《${cur.value.title}》？讲稿、步点与成片会一并删除，无法恢复。`,
      '删除作品', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  busy.value = true
  try {
    await videoApi.remove(cur.value.id)
    cur.value = null
    scriptDraft.value = []
    await loadList()
    ElMessage.success('已删除')
    router.push('/video')
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  } finally {
    busy.value = false
  }
}

/**
 * 生成前把「只在 PUT 生效」的方案类字段补一次保存。
 *
 * ⚠️ 为什么必须有这一步：`POST /videos/{id}/plan/stream` 的请求体是空的
 *    （OpenAPI 里 `requestBody: NO`），后端只按 DB 里已存的 skin / inner_skin / ...
 *    生成。用户在左栏改完视觉方案直接点「生成讲稿与分镜」，新方案**从未上传** →
 *    成片按旧方案出；生成收尾 `applyVideo` 又用服务端值回填表单 →
 *    用户视角就是「我刚选的方案被清掉了」。
 * ⚠️ 绝不复用 `updatePayload()`：它可能带上 `source_type / ref_ids / podcast_id`，
 *    一旦触发后端 `_apply_source` 会**重跑素材抽取并清掉音轨元信息**。
 *    这里只带纯方案/文案字段，对音轨零影响。
 * ⚠️ `gen_mode` / `target_minutes` **故意不在这里补**：它们要生效必须先重做音轨，
 *    走「保存参数」那条显式路径；静默自动化会让音轨和生成模式对不上。
 * ⚠️ 必须是 `function` 声明（不是 const 箭头）：setup 顶层「注册即执行」，
 *    箭头函数会因 TDZ 让整页白屏（本项目踩过）。
 */
async function flushPlanParams(id) {
  const c = cur.value
  if (!c || String(c.id) !== String(id)) return
  // 与服务端回填口径（applyVideo）保持同源，避免把「默认值差异」误判成改动而反复 PUT
  const curInner = c.inner_skin
    || (c.skin === 'talking_head' ? 'graph' : (c.skin === 'ambient' ? 'none' : ''))
  const p = {}
  if ((form.skin || '') !== (c.skin || '')) p.skin = form.skin
  if ((form.inner_skin || '') !== (curInner || '')) p.inner_skin = form.inner_skin || ''
  // 变体与皮肤同类：换肤/换套都影响渲染画面，必须先落库再生成
  if ((form.skin_variant || '') !== (c.skin_variant || '')) p.skin_variant = form.skin_variant || ''
  // ⚠️ 画面署名是**烧进画面**的方案字段，与换肤同一类：不先落库就生成，署名进不了成片；
  //    更糟的是生成收尾 `applyVideo` 会用服务端值回填表单，用户刚填的字**当场被清掉**
  //    （实测复现：填署名 → 直接点「重新生成讲稿与步点」→ 输入框变空）。
  //    比对用 `c.brand_text`（原值），**不能**用 brand_text_effective：后者是「空则回落标题」，
  //    拿它比会把「跟随标题」与「填了同名」两种情况搞混。
  if ((form.brand_text || '') !== (c.brand_text || '')) p.brand_text = form.brand_text || ''
  // ⚠️ 标题同属本函数开头的「文案」，漏掉同样会被回填清掉。
  //    只在**非空**时带：空标题的语义是「保持原标题」（后端会忽略空值），带了等于白跑一次 PUT。
  if ((form.title || '').trim() && form.title.trim() !== (c.title || '')) p.title = form.title.trim()
  if ((form.avatar_id || '') !== (c.avatar_id || 'preset_01')) p.avatar_id = form.avatar_id
  if ((form.avatar_pos || '') !== (c.avatar_pos || 'right')) p.avatar_pos = form.avatar_pos
  if ((form.bg_id || '') !== (c.bg_id || '')) p.bg_id = form.bg_id || ''
  if ((form.instruction || '') !== (c.instruction || '')) p.instruction = form.instruction || ''
  // 音色与视觉方案同类：生成会重新合成音轨，所以必须**先落库再生成**。
  if ((form.voice_map.host || '') !== (((c.voice_map || {}).host) || '')) {
    p.voice_map = { host: form.voice_map.host || '' }
  }
  // BGM 同理：混音发生在音轨生成环节，必须先落库（音量只在有曲子时才参与比对）
  const bgmDirty = (form.bgm_id || '') !== (c.bgm_id || '')
    || ((form.bgm_id || '') && (form.bgm_volume ?? -20) !== (c.bgm_volume ?? -20))
  if (bgmDirty) {
    p.bgm_id = form.bgm_id || ''
    p.bgm_volume = form.bgm_volume ?? -20
  }
  if (!Object.keys(p).length) return
  try {
    const { data } = await videoApi.update(id, p)
    applyVideo(data)
  } catch (e) {
    // 保存失败就**中止生成**：继续下去只会产出用户没要的那套方案
    ElMessage.error(errMsg(e, '视觉方案保存失败，已停止生成'))
    throw e
  }
}

/**
 * 生成讲稿与分镜（PRD 2.2.2 子流程一）。
 *
 * ⚠️ 「启动或附着」：后端已有任务时这里只是**附着**，不会起第二个任务 ——
 *    所以重复点击、离开页面再回来都是安全的。
 * ⚠️ SSE 只是进度订阅者：断开连接**不会**中断生成（任务线程自己持锁跑到底）。
 */
async function runPlan(id) {
  if (planning.value) return
  if (!opts.value.llm_ready) {
    ElMessage.warning('还没有配置可用的大模型，请先到「管理中心」完成配置')
    return
  }
  // ⚠️ 生成接口不收参数（requestBody=NO），左栏方案只有 PUT 能落库 →
  //    先把未保存的视觉/文案改动补一次保存，否则生成仍按 DB 里的旧方案跑。
  try {
    await flushPlanParams(id)
  } catch {
    return
  }
  planning.value = true
  stage.key = ''
  stage.detail = {}
  planAbort = new AbortController()
  let ok = false
  try {
    await streamSSE(
      videoApi.planStreamUrl(id), {},
      undefined,
      (payload) => { ok = true; if (payload && payload.video) applyVideo(payload.video) },
      (msg) => ElMessage.error(msg || '生成失败'),
      (meta) => { stage.key = (meta && meta.stage) || ''; stage.detail = (meta && meta.detail) || {} },
      undefined,
      planAbort.signal,
      ({ stage: s, detail }) => { stage.key = s || ''; stage.detail = detail || {} },
    )
  } catch (e) {
    // 主动中断不是故障（离开页面 / 取消订阅）
    if (e && e.name === 'AbortError') return
    ElMessage.error(errMsg(e, '生成失败'))
  } finally {
    planning.value = false
    planAbort = null
    // 收尾统一以服务端为准：即使流断了，任务也可能已经落库。
    // ⚠️ 但**已经切走**时要跳过：否则旧任务的 loadDetail(id) 会在新作品的
    //    loadDetail 之后再写一次 cur，界面显示成上一个作品（静默串台）。
    if (String(route.params.id) === String(id)) {
      await loadDetail(id, { quiet: true })
      await loadList()
      await loadPrecheck(id)
    }
    if (ok) ElMessage.success('讲稿与视觉步点已生成')
  }
}

/** 回到页面 / 切换作品时：先问一句「还在跑吗」，在跑就重新附着（否则界面是「没人在跑」的假状态） */
async function resumeIfRunning(id) {
  let st = null
  try {
    const { data } = await videoApi.planStatus(id)
    st = data
  } catch { return }
  if (!st || !st.running) return
  stage.key = st.stage || ''
  stage.detail = st.detail || {}
  runPlan(id)
}

/**
 * 渲染（阶段 2）：与 `runPlan` 同构的「启动或附着」。
 *
 * ⚠️ 先 POST 再订阅流：`/render` 的前置校验（闸门没过 / 没有音轨 / 磁盘不足）
 *    会以真正的 **HTTP 400 + detail** 返回，`errMsg` 能取到可读原因；
 *    一旦直接进 SSE，错误就只能作为 `error` 事件回传，拿不到状态码了。
 * ⚠️ POST 是**幂等**的：已在队列 / 正在渲染时返回同一个 job 与它的位次，
 *    所以重复点击、离开页面再回来都安全，不会起第二个渲染进程。
 * ⚠️ SSE 只是进度订阅者：断开连接**不会**中断渲染（调度线程自己跑到底）。
 */
async function runRender(id, { skipGate = false } = {}) {
  if (rendering.value) return
  if (!opts.value.render_ready) {
    ElMessage.warning(opts.value.render_reason || '渲染器不可用')
    return
  }
  rendering.value = true
  renderError.value = ''
  renderQuality.value = null
  renderStage.value = ''
  renderPct.value = 0
  renderPos.value = 0
  const myAbort = new AbortController()
  renderAbort = myAbort
  let ok = false
  try {
    const { data } = await videoApi.render(id, skipGate)
    renderStage.value = (data && data.stage) || 'queued'
    renderPct.value = (data && data.pct) || 0
    renderPos.value = (data && data.position) || 0
    // 入队成功后立刻刷新一次详情：让状态徽标马上变「排队中 / 渲染中」
    loadDetail(id, { quiet: true })
    // 作品库那张卡片也要马上动（否则要等下一拍轮询，用户以为没反应）
    patchRenderLive(id, renderStage.value, renderPct.value, renderPos.value)
    armListPoll()
    await streamSSE(
      videoApi.renderStreamUrl(id), {},
      undefined,
      (payload) => {
        ok = true
        if (payload && payload.video) applyVideo(payload.video)
        if (payload && payload.report) renderQuality.value = payload.report
      },
      (msg) => { renderError.value = msg || '渲染失败'; ElMessage.error(msg || '渲染失败') },
      (meta) => {
        renderStage.value = (meta && meta.stage) || ''
        renderPct.value = (meta && meta.pct) || 0
        renderPos.value = (meta && meta.position) || 0
        patchRenderLive(id, renderStage.value, renderPct.value, renderPos.value)
      },
      undefined,
      myAbort.signal,
      ({ stage: s, pct, position }) => {
        renderStage.value = s || ''
        if (pct !== undefined && pct !== null) renderPct.value = pct
        if (position !== undefined && position !== null) renderPos.value = position
        patchRenderLive(id, renderStage.value, renderPct.value, renderPos.value)
      },
    )
  } catch (e) {
    // 主动中断不是故障（离开页面 / 切换作品）
    if (e && e.name === 'AbortError') return
    const m = errMsg(e, '渲染失败')
    renderError.value = m
    if (!ok) ElMessage.error(m)
  } finally {
    // ⚠️ 只清理「自己这一轮」的状态：切换作品时路由 watch 会 abort 并立刻启动新一轮，
    //    若无条件写 rendering=false 会把刚起来的新一轮标记成「没在跑」。
    if (renderAbort === myAbort) { rendering.value = false; renderAbort = null }
    // 同 runPlan：切走之后不要再用旧 id 覆盖界面
    if (String(route.params.id) === String(id)) {
      await loadDetail(id, { quiet: true })
      await loadList()
      await loadPrecheck(id)
    }
    if (ok) ElMessage.success('成片已生成')
  }
}

/** 回到页面 / 切换作品时：先问一句「还在跑吗」，在跑就重新附着进度 */
async function resumeRenderIfRunning(id) {
  let st = null
  try {
    const { data } = await videoApi.renderStatus(id)
    st = data
  } catch { return }
  if (!st) return
  // ⚠️ 报告要在**这里**就取走，不能等 `running` 分支：渲染跑完之后再回到页面，
  //    `running` 已是 false，若跟着一起 return 就把上次的对齐质量丢掉了 ——
  //    用户看到的是一个「出过片但不知道对不对得准」的界面。
  if (st.report) renderQuality.value = st.report
  if (!st.running) return
  renderStage.value = st.stage || 'queued'
  renderPct.value = st.pct || 0
  renderPos.value = st.position || 0
  runRender(id)
}

async function rerunGate() {
  if (!cur.value) return
  busy.value = true
  try {
    const { data } = await videoApi.gate(cur.value.id)
    applyVideo(data)
    await loadList()
    if (data.gate_passed) ElMessage.success('质量自检全部通过')
    else ElMessage.warning('质量自检仍有未通过项，见下方表格')
    await resumeRenderIfRunning(data.id)
  } catch (e) {
    ElMessage.error(errMsg(e, '重跑失败'))
  } finally {
    busy.value = false
  }
}

/** 一键补步（V5 / PRD 3.9.6）：只重跑定位与步点生成，不动讲稿与音轨 */
async function refillSteps() {
  if (!cur.value) return
  refilling.value = true
  try {
    const { data } = await videoApi.refillSteps(cur.value.id)
    applyVideo(data)
    await loadList()
    if (data.gate_passed) ElMessage.success('补步完成，质量自检全部通过')
    else ElMessage.warning('已补步，自检仍有未通过项，见下方表格')
    await resumeRenderIfRunning(data.id)
  } catch (e) {
    ElMessage.error(errMsg(e, '补步失败'))
  } finally {
    refilling.value = false
  }
}

/** 手动指定未命中句的原文位置（V5 / PRD 3.9.5 判据 4 修复入口） */
async function manualLocate(u) {
  if (!cur.value) return
  if (!(u.anchor || '').trim()) {
    ElMessage.warning('请先填一段材料原文里原样存在的短语')
    return
  }
  u.busy = true
  try {
    const { data } = await videoApi.manualStep(cur.value.id, u.seg, u.anchor.trim())
    applyVideo(data)
    await loadList()
    ElMessage.success(`第 ${u.seg} 句已定位`)
    await resumeRenderIfRunning(data.id)
  } catch (e) {
    ElMessage.error(errMsg(e, '手动指定失败'))
  } finally {
    u.busy = false
  }
}

/** 跳过质量闸门直接渲染（V1 / PRD 3.9.7）：连续 3 次未通过时才有此按钮 */
async function skipGateRender() {
  if (!cur.value) return
  try {
    await ElMessageBox.confirm(
      '质量自检连续多次未通过。跳过即表示你知情地放弃质量保障，成片会标注「未通过质量自检」。确定跳过自检直接渲染？',
      '跳过质量自检',
      { confirmButtonText: '跳过并渲染', cancelButtonText: '再改改', type: 'warning' })
  } catch { return }
  runRender(cur.value.id, { skipGate: true })
}

/** V3：字幕开关（WebVTT 轨的 showing/hidden） */
function applySubMode() {
  const t = videoEl.value && videoEl.value.textTracks && videoEl.value.textTracks[0]
  if (t) t.mode = subOn.value ? 'showing' : 'hidden'
}
function toggleSub() {
  subOn.value = !subOn.value
  applySubMode()
}

/** V4：点击分镜跳到起点 */
function seekShot(sh) {
  if (!cur.value || !cur.value.has_video || !videoEl.value) return
  videoEl.value.currentTime = (Number(sh.at) || 0) / 1000
  videoEl.value.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

/** V6：播放失败（解码不支持）→ 降级为导出提示 */
function onVideoError() { videoPlayError.value = true }

/** V2：播放埋点（PRD 4.2 video_play：watch_seconds / completed）。
 *  暂停只累计不上报；结束 / 切作品 / 离开页面才上报一次（播放次数口径）。 */
function onVideoPlay() { playStartAt = Date.now() }
function onVideoPause() {
  if (playStartAt) { playAccum += (Date.now() - playStartAt) / 1000; playStartAt = 0 }
}
function reportPlay() {
  onVideoPause()
  const v = cur.value
  const watched = Math.round(playAccum)
  playAccum = 0
  if (!v || !v.id || watched < 3) return   // 误触 / 扫一眼不报
  const dur = (v.duration_ms || 0) / 1000
  const completed = dur > 0 && watched >= dur * 0.9
  statsApi.track({
    type: 'video_play', ref_id: v.id, duration_seconds: watched,
    detail: JSON.stringify({ completed }),
  }).catch(() => {})
}
function onVideoEnded() { reportPlay() }

async function saveScript() {
  if (!cur.value) return
  busy.value = true
  try {
    const rows = scriptDraft.value
      .filter((s) => (s.text || '').trim())
      .map((s) => ({ speaker: s.speaker || 'host', text: s.text, start_ms: s.start_ms, end_ms: s.end_ms }))
    const { data } = await videoApi.saveScript(cur.value.id, rows)
    applyVideo(data)
    await loadList()
    if (data.text_changed) {
      ElMessage.success('已保存。文案变了 → 需要重新定位并重跑质量自检')
    } else {
      ElMessage.success('已保存（文案没有实际变化）')
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '保存讲稿失败'))
  } finally {
    busy.value = false
  }
}

async function toNote() {
  if (!cur.value) return
  busy.value = true
  try {
    const { data } = await videoApi.toNote(cur.value.id)
    ElMessage.success(data.created ? '已生成笔记' : '这个版本的讲稿已经有一篇笔记了（同一版本只生成一篇）')
  } catch (e) {
    ElMessage.error(errMsg(e, '转笔记失败'))
  } finally {
    busy.value = false
  }
}

function openSrt() {
  if (!cur.value) return
  window.open(videoApi.srtUrl(cur.value.id), '_blank')
}
function openExport() {
  if (!cur.value) return
  window.open(videoApi.exportUrl(cur.value.id), '_blank')
}
async function revealInFolder() {
  if (!cur.value) return
  try {
    const { data } = await videoApi.reveal(cur.value.id)
    ElMessage.success(`已在文件管理器中定位：${data.path}`)
  } catch (e) {
    ElMessage.error(errMsg(e, '定位失败'))
  }
}

function pickAvatar() { avatarInput.value && avatarInput.value.click() }
async function onAvatarPicked(ev) {
  const f = ev.target.files && ev.target.files[0]
  ev.target.value = ''
  if (!f) return
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    const { data } = await videoApi.uploadAvatar(fd)
    if (!data || !data.avatar_id) throw new Error('上传接口没有返回形象 id')
    form.avatar_id = data.avatar_id
    // ⚠️ 回读服务端清单，而不是在前端凭凑一条：预置项的 label/kind/url 只有后端知道，
    //    前端自己造一条会让「刚上传的形象」和「刷新后的同一形象」长得不一样。
    await loadOptions()
    ElMessage.success(data.note || '形象已上传（只用于你自己的作品）')
  } catch (e) {
    ElMessage.error(errMsg(e, '上传失败'))
  } finally {
    uploading.value = false
  }
}

// 当前选中的是不是「上传形象」→ 决定「删除该形象」按钮出不出现。
// ⚠️ 用服务端给的 kind 判（预置 'lecturer' / 上传 'upload'），不要猜 id 前缀：
//    猜法一旦与后端生成式脱节，按钮就会出现在不该出现的地方（或该出现时不出现）。
const curAvatarUpload = computed(() => {
  const id = form.avatar_id
  return (opts.value.avatars || []).some(a => a.id === id && a.kind === 'upload')
})

async function removeAvatar() {
  const id = form.avatar_id
  if (!id) return
  const item = (opts.value.avatars || []).find(a => a.id === id) || {}
  const n = item.used_by || 0
  try {
    await ElMessageBox.confirm(
      n > 0
        ? `删除形象「${item.label || id}」？有 ${n} 个作品正在使用它，删除后这些作品将回落到预置形象。此操作无法恢复。`
        : `删除形象「${item.label || id}」？此操作无法恢复。`,
      '删除形象', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  deletingAvatar.value = true
  try {
    const { data } = await videoApi.deleteAvatar(id)
    // ⚠️ 删掉的正是当前选中项 → 必须回落：否则表单持有一个列表里已不存在的 id，
    //    界面上没有任何一项是选中态，保存后作品还引用着死 id。
    if (form.avatar_id === id) form.avatar_id = 'preset_01'
    await loadOptions()
    const used = (data && data.used_by) || 0
    ElMessage.success(used > 0 ? `已删除形象（${used} 个作品将回落到预置形象）` : '已删除形象')
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  } finally {
    deletingAvatar.value = false
  }
}

// ---------------------------------------------------------------------------
// 自定义背景（解压型三皮肤 radio / night / ambient）
// ---------------------------------------------------------------------------

// 这一行出不出现，**以后端下发的 `bg_skins` 为准**，前端不写死三个皮肤名：
// 后端将来放开或收紧某个皮肤时，前端不必跟着改（口径只有一处）。
const bgSkinOn = computed(() => (opts.value.bg_skins || []).includes(form.skin))

// 当前选中的是不是「上传的背景」→ 决定「删除该背景」按钮出不出现。
// ⚠️ 用服务端给的 kind 判（内置 'builtin' / 上传 'user'），不猜 id 前缀 —— 猜法一旦
//    与后端生成式脱节，按钮就会出现在不该出现的地方（与 curAvatarUpload 同一纪律）。
const curBgUpload = computed(() => {
  const id = form.bg_id
  if (!id) return false
  return (opts.value.backgrounds || []).some(b => b.id === id && b.kind === 'user')
})

// 「这张背景已被删除」的提示条件。
// ⚠️⚠️ 必须同时满足三条，少一条就会误报：
//   ① 表单确实选了背景 —— 后端 `bg_info` 在**没选背景**时返回的占位也是
//      `exists: false`（`valid_id('')` 为假），只判 `exists` 的话，每个没选背景的
//      作品都会显示「已被删除」；
//   ② 服务端那条 `bg_info.id` 与表单当前选中的**是同一个** —— 用户刚改了选择、还没保存时，
//      提示应该继续跟着「已保存的那张」，而不是对新选择报错；
//   ③ `exists` 为假。
const bgMissing = computed(() => {
  const id = form.bg_id || ''
  if (!id) return false
  const inf = (cur.value || {}).bg_info
  return !!(inf && inf.id === id && inf.exists === false)
})

function pickBg() { bgInput.value && bgInput.value.click() }
async function onBgPicked(ev) {
  const f = ev.target.files && ev.target.files[0]
  ev.target.value = ''
  if (!f) return
  uploadingBg.value = true
  try {
    const fd = new FormData()
    fd.append('file', f)
    const { data } = await videoApi.uploadBackground(fd)
    if (!data || !data.bg_id) throw new Error('上传接口没有返回背景 id')
    form.bg_id = data.bg_id
    // ⚠️ 回读服务端清单，而不是前端凭凑一条：label（上传时间）与缩略图 url 只有后端知道，
    //    自己造一条会让「刚上传的这张」和「刷新后的同一张」长得不一样（与形象同一理由）。
    await loadOptions()
    ElMessage.success(data.note || '背景已上传（只用于你自己的作品）')
  } catch (e) {
    ElMessage.error(errMsg(e, '上传失败'))
  } finally {
    uploadingBg.value = false
  }
}

async function removeBg() {
  const id = form.bg_id
  if (!id) return
  const item = (opts.value.backgrounds || []).find(b => b.id === id) || {}
  const n = item.used_by || 0
  try {
    await ElMessageBox.confirm(
      n > 0
        ? `删除背景「${item.label || id}」？有 ${n} 个作品正在使用它，删除后这些作品会回落到皮肤自带画面。此操作无法恢复。`
        : `删除背景「${item.label || id}」？此操作无法恢复。`,
      '删除背景', { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  deletingBg.value = true
  try {
    const { data } = await videoApi.deleteBackground(id)
    // ⚠️ 删掉的正是当前选中项 → 必须回落成「自带画面」：否则表单持有一个列表里已不存在的 id，
    //    界面上没有任何一项是选中态，保存后作品还引用着死 id（与删形象同一教训）。
    if (form.bg_id === id) form.bg_id = ''
    await loadOptions()
    const used = (data && data.used_by) || 0
    ElMessage.success(used > 0
      ? `已删除背景（${used} 个作品将回落到皮肤自带画面）` : '已删除背景')
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  } finally {
    deletingBg.value = false
  }
}

function toggleColor(key, on) {
  const set = new Set(form.highlight_colors)
  if (on) set.add(key)
  else set.delete(key)
  form.highlight_colors = Array.from(set)
}

function openWork(id) { router.push(`/video/${id}`) }
function backToList() { router.push('/video') }

// ============================================================================
// 展示辅助
// ============================================================================
function fmtMs(ms) {
  const n = Number(ms)
  if (!n || n < 0 || Number.isNaN(n)) return '—'
  const s = Math.round(n / 1000)
  const m = Math.floor(s / 60)
  return m ? `${m}:${String(s % 60).padStart(2, '0')}` : `${s} 秒`
}
function fmtBytes(n) {
  const b = Number(n)
  if (!b || b < 0 || Number.isNaN(b)) return '—'
  if (b >= 1048576) return `${(b / 1048576).toFixed(1)} MB`
  if (b >= 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${b} B`
}
function fmtPct(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return `${(n * 100).toFixed(1)}%`
}
function fmtGateValue(it) {
  if (!it || it.value === undefined || it.value === null || it.na) return '—'
  const n = Number(it.value)
  if (Number.isNaN(n)) return '—'
  if (it.unit === 'ms') return fmtMs(n)
  if (it.unit === 'x' || it.key === 'hit_rate') return fmtPct(n)
  return `${n}${it.unit || ''}`
}
function statusClass(s) {
  if (s === 'finished') return 'ok'
  if (s === 'failed') return 'bad'
  if (s === 'rendering' || s === 'queued' || s === 'scripting') return 'busy'
  return ''
}
/**
 * 作品库卡片的状态文案：**优先取渲染队列的实时态**，再退回 DB 状态。
 *
 * ⚠️ 只读 DB 的 `w.status` 会卡死在「排队渲染」：DB 只有调度器**真正开工那一刻**才把
 *    status 从 queued 改成 rendering（`_run_render_job` 第①段），而列表此前只在渲染收尾
 *    时才刷新一次 → 排队→渲染中的切换、以及录制百分比，界面全程看不到
 *    （用户报的「一直显示排队渲染」就是这个）。
 *    这里改看后端每次 list 都现算的 `render_stage / render_pct / render_position`。
 */
function cardStatusText(w) {
  if (!w) return ''
  const st = w.status || ''
  if (st !== 'queued' && st !== 'rendering') return STATUS_LABEL[st] || st
  const s = w.render_stage || ''
  const pos = Number(w.render_position) || 0
  // 队列里还没轮到（stage 空或 'queued'）：给出位次，和详情页 renderStageText 同口径
  if (!s || s === 'queued') return pos > 1 ? `排队中：前面还有 ${pos - 1} 部` : '排队中'
  if (s === 'capture') return `${RENDER_STAGE_LABEL[s] || '渲染中'} ${Number(w.render_pct) || 0}%`
  return RENDER_STAGE_LABEL[s] || '渲染中'
}

// ============================================================================
// 路由驱动：/video ↔ /video/:id
// ============================================================================
watch(() => route.params.id, async (id) => {
  reportPlay()           // V2：切走前把上一部片子的观看时长报掉
  if (planAbort) { planAbort.abort(); planAbort = null }
  if (renderAbort) { renderAbort.abort(); renderAbort = null }
  planning.value = false
  rendering.value = false
  renderError.value = ''
  renderQuality.value = null
  subOn.value = true
  videoPlayError.value = false
  showUnlocated.value = false
  unlocatedRows.value = []
  if (!id) {
    cur.value = null
    // 新建时清掉上一部作品的画面署名：署名的默认语义就是「空 = 跟随作品标题」，
    // 带着别的作品的署名去新建，会得到「标题是新的、画面署名还是旧的」这种错配。
    // ⚠️ 只清这一项，其余字段沿用既有行为（避免扩大改动面）。
    form.brand_text = ''
    scriptDraft.value = []
    precheck.value = null
    stage.key = ''
    stage.detail = {}
    renderStage.value = ''
    renderPct.value = 0
    renderPos.value = 0
    // 回到作品库：拉一次最新列表（在详情页期间状态可能已变，比如渲染跑完了）
    loadList().catch(() => {})
    return
  }
  await loadDetail(Number(id))
  await loadPrecheck(Number(id))
  await resumeIfRunning(Number(id))
  await resumeRenderIfRunning(Number(id))
})

onMounted(() => {
  reloadAll()
  // V6：缺 H.264 解码的环境只能导出后本地播放（PRD 3.10.7）
  try {
    canPlayMp4.value = !!document.createElement('video').canPlayType('video/mp4; codecs="avc1.42E01E"')
  } catch { canPlayMp4.value = true }
})
onBeforeUnmount(() => {
  stopListPoll()         // 离开页面停掉作品库轮询
  stopPreview()          // 离开页面把试听停掉，避免后台还在响
  reportPlay()           // V2：离开页面把观看时长报掉
  if (planAbort) planAbort.abort()
  // 订阅断开不会中断服务端渲染（调度线程自己跑到底），只是不再收进度
  if (renderAbort) renderAbort.abort()
})
</script>

<style scoped>
.video-page { padding: 22px 26px 40px; }
.video-body {
  display: grid;
  grid-template-columns: minmax(360px, 420px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
@media (max-width: 1180px) { .video-body { grid-template-columns: minmax(0, 1fr); } }
.panel {
  background: var(--asc-card);
  border: 1px solid transparent;
  border-radius: var(--asc-radius);
  padding: 16px 18px;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .05), 0 12px 32px -16px rgba(16, 24, 40, .12);
}
.form-panel { position: sticky; top: 18px; }
.panel-head {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 14px; flex-wrap: wrap;
}
.panel-head h3 { margin: 0; font-size: 15px; font-weight: 600; color: var(--asc-text); }
.panel-head-note { font-size: 12.5px; color: #6e6e6e; }
.detail-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.field { display: flex; flex-direction: column; gap: 7px; margin-bottom: 18px; }
.field-group-start { border-top: 1px solid var(--asc-divider); padding-top: 18px; }
.field-label { font-size: 13px; font-weight: 600; color: var(--asc-text); }
.field-sub-label { font-size: 12.5px; color: var(--asc-text-2); }
.field-hint { margin: 0; font-size: 12px; line-height: 1.6; color: #6e6e6e; }
.field-hint b { color: var(--asc-text); }
.field-hint.bad { color: #b6342c; }
/* 音色选择行（与播客生成台同形：下拉 + 试听） */
.voice-row { display: flex; align-items: center; gap: 8px; }
/* 背景音乐音量行：选中曲目后才出现 */
.bgm-vol-row { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.bgm-vol-label { font-size: 12.5px; color: var(--asc-text-2); flex: none; }
.bgm-vol-val { font-size: 12px; color: var(--asc-text-2); flex: none; min-width: 44px; text-align: right; font-variant-numeric: tabular-nums; }
/* 试听按钮与下拉里逐项试听：与 AI 播客**同形同状态机**（2026-10-12 对齐）——
   圆形图标按钮，三态 ▶/转圈/■；下拉里每个音色、每首曲子右侧都有独立的 ▶。 */
.try-btn {
  width: 30px; height: 30px; border-radius: 50%; flex: none; cursor: pointer;
  border: 1px solid var(--asc-border); background: var(--asc-card); color: var(--asc-primary);
  display: flex; align-items: center; justify-content: center;
  transition: border-color .18s ease, background .18s ease;
}
.try-btn:hover:not(:disabled) { border-color: var(--asc-primary); background: var(--asc-primary-soft); }
.try-btn:disabled { opacity: .4; cursor: not-allowed; }
.try-btn:disabled:hover { border-color: var(--asc-border); background: var(--asc-card); }
.hint-inline { font-size: 11.5px; color: var(--asc-text-2); margin-left: 6px; font-weight: 400; }
/* 下拉里每一项：名称靠左、试听按钮靠右 */
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
.bgm-plus { font-size: 17px; line-height: 1; margin-top: -2px; }
.bgm-file { display: none; }
.seg { display: inline-flex; padding: 3px; gap: 2px; background: var(--asc-surface-2); border-radius: 9px; flex-wrap: wrap; }
.seg-btn {
  border: 0; background: transparent; cursor: pointer; padding: 6px 12px; border-radius: 7px;
  font-size: 12.5px; color: #5f5f66; transition: all .15s ease;
}
.seg-btn:hover { color: var(--asc-text); }
.seg-btn.active { background: var(--asc-card); color: #6a48ee; font-weight: 600; box-shadow: 0 1px 3px rgba(16, 24, 40, .1); }
.color-row { display: flex; gap: 12px; flex-wrap: wrap; }
.avatar-row { display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
.avatar-list { display: flex; gap: 8px; flex-wrap: wrap; }
.avatar-item {
  display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 5px 10px; border-radius: 999px; font-size: 12.5px;
  background: var(--asc-surface-2); border: 1px solid transparent; color: var(--asc-text-2);
}
.avatar-item.active { border-color: var(--asc-primary); background: var(--asc-primary-soft); color: #6a48ee; }
/* url 加载失败的项可能没有 tint（undefined）→ 不给兜底底色的话圆点会是无效值 */
.avatar-dot { width: 12px; height: 12px; border-radius: 50%; display: inline-block; background: var(--asc-border); }
.avatar-thumb {
  width: 22px; height: 22px; border-radius: 6px; flex: none;
  object-fit: cover; object-position: center 22%; background: var(--asc-surface-2);
}
.avatar-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
/* 背景缩略图比形象略宽：背景是横图、形象是竖图，同宽会让横图看不出是什么 */
.bg-thumb {
  width: 34px; height: 22px; border-radius: 5px; flex: none;
  object-fit: cover; background: var(--asc-surface-2);
}
.bg-dot { width: 22px; height: 14px; border-radius: 4px; display: inline-block; background: var(--asc-border); }
.notice {
  margin: 0 0 10px; padding: 6px 10px; border-radius: 6px;
  font-size: 12.5px; line-height: 1.65; border: none; border-left: 3px solid transparent;
}
.notice.warn { color: #8a5a00; background: #fffaf0; border-left-color: #e6b45a; }
.notice.info { color: #2f5c8a; background: #f4f8fd; border-left-color: #7aa7d9; }
.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 4px; }
.actions.wrap { margin: 14px 0 4px; }
.actions-hint { font-size: 12px; color: #6e6e6e; }
/* 详情操作行分层：次操作降为文字型，主按钮集中在「出片台」（V2 美化 20261012） */
.main-panel > .actions.wrap { border-bottom: 1px solid var(--asc-divider); padding-bottom: 14px; }
.main-panel > .actions.wrap .el-button:not(.el-button--primary) {
  border-color: transparent; background: transparent; color: #6e6e6e; padding: 6px 8px;
}
.main-panel > .actions.wrap .el-button:not(.el-button--primary):hover { color: var(--asc-text); background: var(--asc-surface-2); }
.main-panel > .actions.wrap .el-button--danger.is-plain { color: #b6342c; background: transparent; }
.main-panel > .actions.wrap .el-button--danger.is-plain:hover { background: #fdf4f3; }
/* danger 按钮全局加深（实测 #f56c6c on #fef0f0 仅 2.61:1，未过 AA） */
.video-page .el-button--danger.is-plain { color: #b6342c; border-color: #ecc6c3; background: #fdf4f3; }
.video-page .el-button--danger.is-plain:hover { color: #a02a24; background: #fbe9e7; border-color: #e2b3af; }
/* 渲染（阶段 2）：按钮 + 进度条 + 位次，收成一张「出片台」子卡片 */
.render-box {
  margin: 16px 0 8px; padding: 14px 16px;
  background: var(--asc-surface-2); border-radius: 12px;
}
.render-box .actions.wrap { margin: 0; }
.render-box .skip-gate-row { margin-top: 8px; }
.render-box .render-bar { background: var(--asc-card); }
.render-bar {
  display: flex; align-items: center; gap: 10px;
  margin-top: 10px; padding: 10px 12px;
  background: var(--asc-surface-2);
  border: 1px solid var(--asc-border);
  border-radius: 10px;
}
.render-bar :deep(.el-progress) { flex: 1; min-width: 0; }
.render-stage { flex: none; font-size: 12.5px; color: #6a48ee; }
.render-quality { margin: 8px 0 0; font-size: 12px; color: #6e6e6e; }
.stage-line { display: flex; align-items: center; gap: 8px; margin: 10px 0 0; font-size: 12.5px; color: #6a48ee; }
.stage-dot {
  width: 8px; height: 8px; border-radius: 50%; background: var(--asc-primary);
  animation: pulse 1.1s ease-in-out infinite;
}
@keyframes pulse { 0%, 100% { opacity: .3; transform: scale(.85); } 50% { opacity: 1; transform: scale(1.1); } }
.empty-line { margin: 6px 0 14px; font-size: 13px; color: #6e6e6e; }
.work-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.work-card {
  text-align: left; cursor: pointer; padding: 12px 13px; border-radius: 10px;
  background: var(--asc-card); border: 1px solid transparent;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .05);
  display: flex; flex-direction: column; gap: 7px; transition: all .15s ease;
}
.work-card:hover { box-shadow: 0 8px 24px -10px rgba(16, 24, 40, .18); }
.work-card-top { display: flex; align-items: center; gap: 8px; }
.work-title {
  flex: 1; min-width: 0; font-size: 13.5px; font-weight: 600; color: var(--asc-text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.work-meta { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; color: #6e6e6e; }
.work-card .work-meta + .work-meta { font-size: 11.5px; }
.work-error {
  margin: 0; font-size: 12px; color: #b6342c; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.badge {
  font-size: 11px; font-weight: 500; padding: 2px 8px; border-radius: 999px; flex-shrink: 0;
  background: var(--asc-surface-2); color: var(--asc-text-2);
}
.badge.ok { background: #eaf7ee; color: #2c7a45; }
.badge.bad { background: #fdecec; color: #b6342c; }
.badge.busy { background: var(--asc-primary-soft); color: #6a48ee; }
.stats-row {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 6px 0; margin: 8px 0 6px;
}
.stat {
  display: flex; flex-direction: column; gap: 3px;
  padding: 4px 16px; border-left: 1px solid var(--asc-divider);
}
.stat:first-child { border-left: none; padding-left: 0; }
.stat-k { font-size: 12px; color: #6e6e6e; }
.stat-v {
  font-size: 19px; font-weight: 600; color: var(--asc-text);
  letter-spacing: -.01em; font-variant-numeric: tabular-nums;
}
/* 长文本值（视觉方案 / 音轨）降级：19px 在 120px 格里会折 3 行 */
.stat-v--text {
  font-size: 15px; line-height: 1.35; letter-spacing: 0;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.sec-title {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin: 30px 0 12px; font-size: 15px; font-weight: 600; color: var(--asc-text);
}
.sec-note { font-size: 12px; font-weight: 400; color: #6e6e6e; }
.gate-table, .mini-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.gate-table th, .gate-table td, .mini-table th, .mini-table td {
  padding: 9px; border-bottom: 1px solid var(--asc-divider); text-align: left; vertical-align: top;
}
.gate-table th, .mini-table th {
  font-size: 12px; font-weight: 500; color: #6e6e6e;
  background: transparent; border-bottom: 1px solid var(--asc-border);
}
.gate-table tr.bad td { background: #fff7f6; }
.gate-table .num, .mini-table .num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.gate-table .fix { color: #6e6e6e; }
.gate-table .muted, .mini-table .muted { color: #6e6e6e; }
.ok-text { color: #2c7a45; }
.bad-text { color: #b6342c; }
.anchor { color: #6a48ee; }
.script-list { display: flex; flex-direction: column; gap: 6px; max-height: 420px; overflow: auto; }
.script-row { display: grid; grid-template-columns: 52px 44px minmax(0, 1fr); gap: 8px; align-items: start; }
.script-time { font-size: 11.5px; color: #6e6e6e; padding-top: 6px; font-variant-numeric: tabular-nums; }
.script-spk { font-size: 11.5px; color: var(--asc-text-2); padding-top: 6px; }
.player { width: 100%; margin-bottom: 10px; border-radius: 10px; background: #000; }
/* 作品卡片封面缩略图（V7 / PRD 3.11.3） */
.work-thumb { margin: -2px -2px 2px; }
.work-thumb img {
  width: 100%; height: 118px; object-fit: cover; border-radius: 6px;
  display: block; background: #000;
}
/* 闸门操作列的说明文字（V5） */
.fix-hint { margin-top: 4px; font-size: 12px; color: #6e6e6e; line-height: 1.5; }
/* 未命中句手动定位面板（V5 / PRD 3.9.5 判据 4） */
.unlocated-box {
  margin: 8px 0 16px; padding: 10px 12px;
  border: 1px dashed var(--asc-border); border-radius: 8px; background: var(--asc-surface-2);
}
.unlocated-row {
  display: grid; grid-template-columns: 34px minmax(0, 1fr) 220px auto;
  gap: 8px; align-items: center; padding: 6px 0;
}
.unlocated-row + .unlocated-row { border-top: 1px solid var(--asc-border); }
.unlocated-seg { font-size: 12px; color: #6e6e6e; font-variant-numeric: tabular-nums; }
.unlocated-text {
  font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
@media (max-width: 1180px) {
  .unlocated-row { grid-template-columns: 34px minmax(0, 1fr); }
  .unlocated-row .el-input { grid-column: 2; }
  .unlocated-row .el-button { grid-column: 2; justify-self: start; }
}
/* 分镜行可点（V4 / PRD 3.10.5） */
.mini-table tr.shot-clickable { cursor: pointer; }
.mini-table tr.shot-clickable:hover td { background: var(--asc-primary-soft); }
/* 播放器工具行（V3 字幕开关） */
.player-bar { display: flex; justify-content: flex-end; margin: -4px 0 10px; }
/* 跳过闸门（V1 / PRD 3.9.7） */
.skip-gate-row { margin-top: 6px; }
</style>
