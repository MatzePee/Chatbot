// API-Client. Reines fetch, keine Abhängigkeiten.
const BASE = '/api/v1'

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

async function request(path, options = {}) {
  const isForm = options.body instanceof FormData
  const res = await fetch(BASE + path, {
    credentials: 'include',
    ...options,
    headers: isForm ? (options.headers || {}) : { 'Content-Type': 'application/json', ...(options.headers || {}) },
  })
  if (res.status === 204) return undefined
  const text = await res.text()
  let payload = null
  let isJson = true
  try { payload = text ? JSON.parse(text) : null } catch { payload = text; isJson = false }

  // Antwortet der Server mit HTML, ist der Aufruf nicht bei der API gelandet –
  // meist, weil der Server noch mit einem älteren Stand läuft. Ohne diese
  // Prüfung käme später irgendein unverständlicher Folgefehler.
  if (res.ok && !isJson && typeof payload === 'string' && payload.trimStart().startsWith('<')) {
    throw new ApiError(
      `Der Server kennt ${path} nicht (es kam die Website statt Daten zurück). ` +
      'Läuft er noch mit einem älteren Stand? Ein Neustart über ./run.sh start hilft.',
      404,
    )
  }
  if (!res.ok) {
    let detail = (payload && payload.detail) || (typeof payload === 'string' ? payload : '')
    if (!detail) detail = `HTTP ${res.status} bei ${path}`
    if (typeof detail !== 'string') detail = JSON.stringify(detail)
    // Der Server liefert bei 500 den Fehlertyp mit – der gehört sichtbar in die Oberfläche.
    if (payload && payload.path && !detail.includes(payload.path)) detail += ` (${payload.path})`
    if (payload && Array.isArray(payload.traceback)) {
      console.error('Server-Traceback:', payload.traceback.join('\n'))
    }
    throw new ApiError(detail, res.status)
  }
  return payload
}

const get = (p) => request(p)
const post = (p, body) => request(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const patch = (p, body) => request(p, { method: 'PATCH', body: JSON.stringify(body) })
const del = (p) => request(p, { method: 'DELETE' })

const qs = (params) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') s.append(k, v)
  }
  const out = s.toString()
  return out ? '?' + out : ''
}

export const api = {
  imageComment: () => get('/settings/image-comment'),
  saveImageComment: d => post('/settings/image-comment', d),
  // --- Auth ---
  login: (email, password, totp) => post('/auth/login', { email, password, totp: totp || null }),
  logout: () => post('/auth/logout'),
  me: () => get('/auth/me'),
  authMode: () => get('/auth/mode'),
  users: () => get('/auth/users'),
  createUser: (d) => post('/auth/users', d),
  deleteUser: (id) => del('/auth/users/' + id),

  // --- Dashboard / Betrieb ---
  dashboard: () => get('/dashboard'),
  inventory: () => get('/inventory'),
  settings: () => get('/settings'),
  setRuntime: (d) => post('/settings/runtime', d),
  notifications: (unreadOnly = true) => get('/notifications?unread_only=' + unreadOnly),
  markRead: (ids) => post('/notifications/read', ids),
  llmUsage: () => get('/llm/usage'),
  integrations: () => get('/settings/integrations'),
  saveIntegrations: (d) => post('/settings/integrations', d),
  clearSecret: (field) => post('/settings/integrations/clear', { field }),
  openrouterModels: (refresh = false) => get('/settings/openrouter/models?refresh=' + refresh),
  openrouterTest: (model) => post('/settings/openrouter/test', { model }),
  jobs: () => get('/jobs'),
  runJob: (name) => post(`/jobs/${name}/run`),
  bestTimes: (channelId) => get('/analytics/best-times?channel_id=' + channelId),
  topPosts: () => get('/analytics/top-posts'),

  // --- Kanäle & Personas ---
  channels: () => get('/channels'),
  createChannel: (d) => post('/channels', d),
  updateChannel: (id, d) => patch('/channels/' + id, d),
  deleteChannel: (id) => del('/channels/' + id),
  channelDeletionPreview: (id) => get(`/channels/${id}/deletion-preview`),
  restartService: () => post('/system/restart'),
  pauseChannel: (id, paused) => post(`/channels/${id}/pause?paused=${paused}`),
  disconnect: (id) => post(`/channels/${id}/disconnect`),
  uploadWatermark: (id, file) => {
    const form = new FormData()
    form.append('file', file)
    return request(`/channels/${id}/watermark`, { method: 'POST', body: form })
  },
  deleteWatermark: (id) => del(`/channels/${id}/watermark`),
  watermarkPreviewUrl: (id, p = {}) =>
    `${BASE}/channels/${id}/watermark/preview` + qs({
      anchor: p.anchor, scale_pct: p.scale_pct,
      margin_x_pct: p.margin_x_pct, margin_y_pct: p.margin_y_pct,
      opacity: p.opacity, asset_id: p.asset_id, t: Date.now(),
    }),
  testChannel: (id) => post(`/channels/${id}/test`),
  testAllChannels: () => post('/channels/test-all'),
  oauthStart: (platform, channelId) => get(`/oauth/${platform}/start?channel_id=${channelId}`),
  personas: () => get('/personas'),
  createPersona: (d) => post('/personas', d),
  updatePersona: (id, d) => patch('/personas/' + id, d),
  examples: (personaId, platform) => get(`/personas/${personaId}/examples` + qs({ platform })),
  addExamples: (personaId, d) => post(`/personas/${personaId}/examples`, d),
  deleteExample: (id) => del('/personas/examples/' + id),
  checkRhythm: (text) => post('/personas/rhythm/check', { text }),
  rhythmTemplate: () => get('/personas/rhythm/template'),

  // --- System: Version, Update, Veröffentlichen ---
  version: () => get('/system/version'),
  updateCheck: () => post('/system/update-check'),
  updateInstall: () => post('/system/update'),
  publishStatus: () => get('/system/publish/status'),
  saveGitSettings: (d) => post('/system/publish/settings', d),
  publishRelease: (d) => post('/system/publish', d),

  // --- Medien ---
  searchMedia: (q) => post('/media/search', q),
  counts: (fresh = false) => get('/media/counts?fresh=' + fresh),
  tags: () => get('/media/tags'),
  mediaDetail: (id) => get('/media/' + id),
  updateMedia: (id, d) => patch('/media/' + id, d),
  bulkMedia: (d) => post('/media/bulk', d),
  archiveMedia: (id, archived) => post(`/media/${id}/archive?archived=${archived}`),
  deleteMedia: (id) => del('/media/' + id),
  similar: (id) => get(`/media/${id}/similar`),
  matrix: (limit = 150, cursor) => get('/media/matrix' + qs({ limit, cursor })),
  timeline: (year, month, channelId) => get('/media/timeline' + qs({ year, month, channel_id: channelId })),
  upload: (form) => request('/media/upload', { method: 'POST', body: form }),

  // --- Sets ---
  sets: () => get('/sets'),
  createSet: (d) => post('/sets', d),
  updateSet: (id, d) => patch('/sets/' + id, d),
  deleteSet: (id) => del('/sets/' + id),

  // --- Ansichten & Wartung ---
  views: () => get('/views'),
  createView: (d) => post('/views', d),
  deleteView: (id) => del('/views/' + id),
  maintenanceReport: () => get('/maintenance/report'),
  archiveUsed: (days) => post('/maintenance/archive-used?older_than_days=' + days),
  rebuildCounters: () => post('/maintenance/rebuild-counters'),
  deletePlanned: (opts = {}) => post('/maintenance/delete-planned' + qs({
    dry_run: opts.dryRun === undefined ? true : opts.dryRun,
    channel_id: opts.channelId,
    only_future: opts.onlyFuture,
  })),

  // --- Zuordnung ---
  board: () => get('/assignments/board'),
  assign: (d) => post('/assignments/batch', d),
  unassign: (d) => post('/assignments/remove', d),
  checkRemove: (d) => post('/assignments/check-remove', d),
  reorder: (channel_id, ordered_asset_ids) => post('/assignments/reorder', { channel_id, ordered_asset_ids }),
  channelPool: (id, includeUsed = true) => get(`/assignments/channel/${id}?include_used=${includeUsed}`),
  rules: () => get('/assignments/rules'),
  createRule: (d) => post('/assignments/rules', d),
  deleteRule: (id) => del('/assignments/rules/' + id),
  applyRules: (channelId) => post('/assignments/rules/apply' + qs({ channel_id: channelId })),

  // --- Posts / Kalender ---
  translateText: (text) => post('/posts/translate', { text }),
  posts: (params) => get('/posts' + qs(params)),
  post: (id) => get('/posts/' + id),
  createPost: (d) => post('/posts', d),
  updatePost: (id, d) => patch('/posts/' + id, d),
  deletePost: (id) => del('/posts/' + id),
  bulkMove: (post_ids, shift_minutes, new_status) => post('/posts/bulk-move', { post_ids, shift_minutes, new_status }),
  preflight: (id) => get(`/posts/${id}/preflight`),
  generate: (d) => post('/posts/generate', d),
  publish: (id, force = false) => post(`/posts/${id}/publish?force=${force}`),
  approve: (id) => post(`/posts/${id}/approve`),
  attempts: (id) => get(`/posts/${id}/attempts`),
  fillCalendar: (channel_ids, days, dry_run) => post('/calendar/fill', { channel_ids, days, dry_run }),
  planRange: (d) => post('/calendar/plan', d),
  planRangeStart: (d) => post('/calendar/plan/start', d),
  bulkGenerateStart: (post_ids, instruction = '') =>
    post('/posts/bulk-generate/start', { post_ids, instruction }),
  run: (id) => get('/runs/' + id),
  cancelRun: (id) => post(`/runs/${id}/cancel`),
  activeRuns: () => get('/runs'),
  month: (year, m) => get(`/calendar/month?year=${year}&month=${m}`),
  bulkDelete: (post_ids) => post('/posts/bulk-delete', { post_ids }),
  bulkGenerate: (post_ids, instruction = '') => post('/posts/bulk-generate', { post_ids, instruction }),
  duplicatePost: (id, d) => post(`/posts/${id}/duplicate`, d),
  blackouts: () => get('/calendar/blackouts'),
  createBlackout: (d) => post('/calendar/blackouts', d),
  deleteBlackout: (id) => del('/calendar/blackouts/' + id),
}
