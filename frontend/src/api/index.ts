import { request } from './client'
import type {
  AutoReplyRule,
  AiTriggerKeywordCreatePayload,
  AiTriggerKeywordRule,
  AiTriggerKeywordUpdatePayload,
  ActionCatalogItem,
  AnimatedEmojiBatchCategoryPayload,
  AnimatedEmojiRecord,
  AiProactiveReplyPayload,
  AiProactiveReplyRule,
  AiVoiceCharacterOption,
  AiVoiceCharacterPayload,
  AiVoiceCharacterRule,
  BotAccount,
  BotAiConfig,
  BotAiConfigPayload,
  BotFriend,
  BotGroup,
  BotGroupPayload,
  BotPayload,
  ForbiddenWordCreatePayload,
  ForbiddenWordRule,
  ForbiddenWordUpdatePayload,
  FriendMessageSyncResult,
  FriendSyncResult,
  AiModelOption,
  AiModelTestResult,
  AiProviderOption,
  DashboardSummary,
  DictionaryOption,
  EmailLogRecord,
  EmailSettings,
  FeatureItem,
  AppearanceSettings,
  AnimatedEmojiSettings,
  AiReplyRecord,
  ChatMessageRecord,
  ChatRecordExport,
  IntroductionSettings,
  GithubSettings,
  ManagedUser,
  CreateUserPayload,
  GeetestResult,
  LoginPayload,
  LoginLogRecord,
  Pagination,
  PublicSettings,
  RegisterPayload,
  RulePayload,
  UserInfo,
  WelcomeMessage,
  WelcomeMessagePayload,
  ScheduledTask,
  ScheduledTaskPayload,
  PluginCapabilityItem,
  PluginDetail,
  PluginInstallPayload,
  PluginDeleteResult,
  PluginInstallation,
  PluginInstallationUpdatePayload,
  PluginItem,
  MyPluginItem,
  PluginRepoProbeResult,
  PluginRepoSubmitPayload,
  PluginReviewLog,
  PluginReviewVersion,
  PluginTrustedAuthor,
  PluginUpgradeResult,
  PluginVersionInfo,
  PluginSyncSettings,
  RegistryPlugin,
  RegistrySettings,
  RegistryTokenItem,
  RegistryStats,
} from '../types'

export const authApi = {
  login: (payload: LoginPayload) =>
    request<{ token: string; expires_in: number; user: UserInfo }>({
      url: '/auth/login', method: 'POST', data: payload,
    }),
  register: (payload: RegisterPayload) =>
    request<{ token: string; expires_in: number; user: UserInfo }>({ url: '/auth/register', method: 'POST', data: payload }),
  sendEmailCode: (email: string, purpose: 'login' | 'register' | 'bind', challenge: string) =>
    request({
      url: '/auth/email-code', method: 'POST',
      data: { email, purpose, geetest_challenge: challenge },
    }),
  me: () => request<UserInfo>({ url: '/auth/me' }),
  logout: () => request({ url: '/auth/logout', method: 'POST' }),
  updateProfile: (nickname: string) =>
    request<UserInfo>({ url: '/auth/profile', method: 'PUT', data: { nickname } }),
  updatePassword: (currentPassword: string, newPassword: string) =>
    request({
      url: '/auth/password', method: 'PUT',
      data: { current_password: currentPassword, new_password: newPassword },
    }),
  bindEmail: (email: string, emailCode: string, challenge: string) =>
    request<UserInfo>({
      url: '/auth/email', method: 'PUT',
      data: { email, email_code: emailCode, geetest_challenge: challenge },
    }),
}

/**
 * 个人中心接口：普通账号管理自己的插件库接入令牌。
 *
 * 与 settingsApi 上那组管理员接口的区别是「只看得到自己创建的令牌」，
 * 列表里额外返回下游应使用的服务地址，供页面展示成「API 调用地址」。
 */
export const profileApi = {
  listRegistryTokens: () =>
    request<{ items: RegistryTokenItem[]; endpoint: string }>({
      url: '/profile/registry-tokens',
    }),
  createRegistryToken: (payload: { name: string }) =>
    request<{ token: string; item: RegistryTokenItem }>({
      url: '/profile/registry-tokens', method: 'POST', data: payload,
    }),
  revokeRegistryToken: (id: number) =>
    request<{ id: number }>({ url: `/profile/registry-tokens/${id}`, method: 'DELETE' }),
}

export const userApi = {
  list: (params: { page: number; page_size: number }) =>
    request<Pagination<ManagedUser>>({ url: '/users', params }),
  create: (payload: CreateUserPayload) =>
    request<ManagedUser>({ url: '/users', method: 'POST', data: payload }),
}

export interface LoginLogQuery {
  page: number
  page_size: number
  keyword?: string
  login_type?: string
  start_time?: string
  end_time?: string
}

export const loginLogApi = {
  list: (params: LoginLogQuery) =>
    request<Pagination<LoginLogRecord>>({ url: '/login-logs', params }),
}

export interface EmailLogQuery {
  page: number
  page_size: number
  keyword?: string
  action?: string
  result?: string
  start_time?: string
  end_time?: string
}

export const emailLogApi = {
  list: (params: EmailLogQuery) =>
    request<Pagination<EmailLogRecord>>({ url: '/email-logs', params }),
}

export const publicApi = {
  geetestRegister: () => request<Record<string, unknown>>({ url: '/geetest/register' }),
  geetestValidate: (payload: GeetestResult) =>
    request({ url: '/geetest/validate', method: 'POST', data: payload }),
  settings: () => request<PublicSettings>({ url: '/settings/public' }),
  dictionaries: (types: string[]) =>
    request<Record<string, DictionaryOption[]>>({
      url: '/dictionaries', params: { types: types.join(',') },
    }),
}

export const featuresApi = {
  list: () => request<FeatureItem[]>({ url: '/features' }),
}

export const settingsApi = {
  group: <T>(group: string) => request<T>({ url: `/settings/${group}` }),
  saveEmail: (payload: EmailSettings) =>
    request<EmailSettings>({ url: '/settings/email/save', method: 'PUT', data: payload }),
  saveAppearance: (payload: AppearanceSettings) =>
    request<AppearanceSettings>({ url: '/settings/appearance/save', method: 'PUT', data: payload }),
  saveIntroduction: (payload: IntroductionSettings) =>
    request<IntroductionSettings>({ url: '/settings/introduction/save', method: 'PUT', data: payload }),
  saveAnimatedEmoji: (payload: AnimatedEmojiSettings) =>
    request<AnimatedEmojiSettings>({ url: '/settings/animated-emoji/save', method: 'PUT', data: payload }),
  saveGithub: (payload: GithubSettings) =>
    request<GithubSettings>({ url: '/settings/github/save', method: 'PUT', data: payload }),
  savePluginSync: (payload: PluginSyncSettings) =>
    request<PluginSyncSettings>({ url: '/settings/plugin-sync/save', method: 'PUT', data: payload }),
  saveRegistry: (payload: RegistrySettings) =>
    request<RegistrySettings>({ url: '/settings/registry/save', method: 'PUT', data: payload }),
  listRegistryTokens: () =>
    request<{ items: RegistryTokenItem[] }>({ url: '/settings/registry/tokens' }),
  createRegistryToken: (payload: { name: string }) =>
    request<{ token: string; item: RegistryTokenItem }>({
      url: '/settings/registry/tokens',
      method: 'POST',
      data: payload,
    }),
  revokeRegistryToken: (id: number) =>
    request<{ id: number }>({ url: `/settings/registry/tokens/${id}`, method: 'DELETE' }),
}

export interface RuleQuery {
  page: number
  page_size: number
  keyword?: string
  bot_qq?: string
  enabled?: boolean
}

export const autoReplyApi = {
  list: (params: RuleQuery) =>
    request<Pagination<AutoReplyRule>>({ url: '/auto_replies', params }),
  create: (payload: RulePayload) =>
    request<AutoReplyRule>({ url: '/auto_replies', method: 'POST', data: payload }),
  update: (id: number, payload: RulePayload) =>
    request<AutoReplyRule>({ url: `/auto_replies/${id}`, method: 'PUT', data: payload }),
  remove: (id: number) =>
    request({ url: `/auto_replies/${id}/delete`, method: 'POST' }),
}

export interface ForbiddenWordQuery {
  page: number
  page_size: number
  keyword?: string
  bot_qq?: string
  group_id?: string
  enabled?: boolean
}

export const forbiddenWordApi = {
  list: (params: ForbiddenWordQuery) =>
    request<Pagination<ForbiddenWordRule>>({ url: '/forbidden_words', params }),
  create: (payload: ForbiddenWordCreatePayload) =>
    request<ForbiddenWordRule>({ url: '/forbidden_words', method: 'POST', data: payload }),
  update: (id: number, payload: ForbiddenWordUpdatePayload) =>
    request<ForbiddenWordRule>({ url: `/forbidden_words/${id}`, method: 'PUT', data: payload }),
  remove: (id: number) =>
    request({ url: `/forbidden_words/${id}/delete`, method: 'POST' }),
  batchRemove: (ids: number[]) =>
    request<{ deleted_count: number }>({ url: '/forbidden_words/batch-delete', method: 'POST', data: { ids } }),
}

export interface AiTriggerKeywordQuery {
  page: number
  page_size: number
  keyword?: string
  bot_qq?: string
  enabled?: boolean
}

export const aiTriggerKeywordApi = {
  list: (params: AiTriggerKeywordQuery) =>
    request<Pagination<AiTriggerKeywordRule>>({ url: '/ai_trigger_keywords', params }),
  create: (payload: AiTriggerKeywordCreatePayload) =>
    request<AiTriggerKeywordRule>({ url: '/ai_trigger_keywords', method: 'POST', data: payload }),
  update: (id: number, payload: AiTriggerKeywordUpdatePayload) =>
    request<AiTriggerKeywordRule>({ url: `/ai_trigger_keywords/${id}`, method: 'PUT', data: payload }),
  remove: (id: number) =>
    request({ url: `/ai_trigger_keywords/${id}/delete`, method: 'POST' }),
  batchRemove: (ids: number[]) =>
    request<{ deleted_count: number }>({ url: '/ai_trigger_keywords/batch-delete', method: 'POST', data: { ids } }),
}

export interface WelcomeMessageQuery {
  page: number
  page_size: number
  bot_qq?: string
  group_id?: string
  enabled?: boolean
}

export const welcomeMessageApi = {
  list: (params: WelcomeMessageQuery) =>
    request<Pagination<WelcomeMessage>>({ url: '/welcome_messages', params }),
  create: (payload: WelcomeMessagePayload) =>
    request<WelcomeMessage>({ url: '/welcome_messages', method: 'POST', data: payload }),
  update: (id: number, payload: WelcomeMessagePayload) =>
    request<WelcomeMessage>({ url: `/welcome_messages/${id}`, method: 'PUT', data: payload }),
  remove: (id: number) =>
    request({ url: `/welcome_messages/${id}/delete`, method: 'POST' }),
}

export interface AiProactiveReplyQuery {
  page: number
  page_size: number
  bot_qq?: string
  group_id?: string
  enabled?: boolean
}

export const aiProactiveReplyApi = {
  list: (params: AiProactiveReplyQuery) =>
    request<Pagination<AiProactiveReplyRule>>({ url: '/ai_proactive_replies', params }),
  create: (payload: AiProactiveReplyPayload) =>
    request<AiProactiveReplyRule>({ url: '/ai_proactive_replies', method: 'POST', data: payload }),
  update: (id: number, payload: AiProactiveReplyPayload) =>
    request<AiProactiveReplyRule>({ url: `/ai_proactive_replies/${id}`, method: 'PUT', data: payload }),
  remove: (id: number) =>
    request({ url: `/ai_proactive_replies/${id}/delete`, method: 'POST' }),
}

export interface AiVoiceCharacterQuery {
  page: number
  page_size: number
  bot_qq?: string
  group_id?: string
  enabled?: boolean
}

export const aiVoiceCharacterApi = {
  list: (params: AiVoiceCharacterQuery) =>
    request<Pagination<AiVoiceCharacterRule>>({ url: '/ai_voice_characters', params }),
  create: (payload: AiVoiceCharacterPayload) =>
    request<AiVoiceCharacterRule>({ url: '/ai_voice_characters', method: 'POST', data: payload }),
  update: (id: number, payload: AiVoiceCharacterPayload) =>
    request<AiVoiceCharacterRule>({ url: `/ai_voice_characters/${id}`, method: 'PUT', data: payload }),
  remove: (id: number) =>
    request({ url: `/ai_voice_characters/${id}/delete`, method: 'POST' }),
  options: (botQq: string, groupId = '') =>
    request<AiVoiceCharacterOption[]>({ url: '/ai_voice_characters/options', params: { bot_qq: botQq, group_id: groupId } }),
}

export interface ChatRecordQuery {
  page: number
  page_size: number
  bot_qq?: string
  group_id?: string
  keyword?: string
  post_type?: string
  ai_triggered?: boolean
  user_qq?: string
  private_only?: boolean
}

export interface AiReplyRecordQuery {
  page: number
  page_size: number
  bot_qq?: string
  group_id?: string
}

export type ChatRecordExportQuery = Omit<ChatRecordQuery, 'page' | 'page_size'>

export const chatRecordApi = {
  messages: (params: ChatRecordQuery) =>
    request<Pagination<ChatMessageRecord>>({ url: '/chat_records/messages', params }),
  exportMessages: (params: ChatRecordExportQuery) =>
    request<ChatRecordExport>({ url: '/chat_records/messages/export', params }),
  batchRemoveMessages: (ids: number[]) =>
    request<{ deleted_count: number }>({
      url: '/chat_records/messages/batch-delete', method: 'POST', data: { ids },
    }),
  aiReplies: (params: AiReplyRecordQuery) =>
    request<Pagination<AiReplyRecord>>({ url: '/chat_records/ai_replies', params }),
}

export interface FriendQuery {
  page: number
  page_size: number
  bot_qq?: string
  keyword?: string
  category_name?: string
  sex?: string
}

export const friendApi = {
  list: (params: FriendQuery) =>
    request<Pagination<BotFriend>>({ url: '/friends', params }),
  sync: (botQq: string) =>
    request<FriendSyncResult>({ url: `/friends/${botQq}/sync`, method: 'POST' }),
  block: (friendId: number) =>
    request<BotFriend>({ url: `/friends/${friendId}/block`, method: 'POST' }),
  unblock: (friendId: number) =>
    request<BotFriend>({ url: `/friends/${friendId}/unblock`, method: 'POST' }),
  syncMessages: (botQq: string, friendQq: string) =>
    request<FriendMessageSyncResult>({
      url: `/friends/${botQq}/${friendQq}/messages/sync`, method: 'POST',
    }),
}

export const dashboardApi = {
  summary: () => request<DashboardSummary>({ url: '/dashboard', params: { days: 7 } }),
}

export const scheduledTaskApi = {
  list: (params: { page: number; page_size: number }) =>
    request<Pagination<ScheduledTask>>({ url: '/scheduled_tasks', params }),
  update: (id: number, payload: ScheduledTaskPayload) =>
    request<ScheduledTask>({ url: `/scheduled_tasks/${id}`, method: 'PUT', data: payload }),
  run: (id: number) =>
    request<ScheduledTask>({ url: `/scheduled_tasks/${id}/run`, method: 'POST' }),
}

export const animatedEmojiApi = {
  list: (params: { page: number; page_size: number; bot_qq?: string; category?: string }) =>
    request<Pagination<AnimatedEmojiRecord>>({ url: '/animated_emojis', params }),
  preview: (id: number) =>
    request<{ media_type: string; data: string }>({ url: `/animated_emojis/${id}/preview` }),
  remove: (id: number) =>
    request({ url: `/animated_emojis/${id}/delete`, method: 'POST' }),
  batchRemove: (ids: number[]) =>
    request<{ deleted_count: number }>({ url: '/animated_emojis/batch-delete', method: 'POST', data: { ids } }),
  batchUpdateCategory: (payload: AnimatedEmojiBatchCategoryPayload) =>
    request<{ updated_count: number; category: string }>({
      url: '/animated_emojis/batch-category', method: 'POST', data: payload,
    }),
}

export const botApi = {
  list: () => request<BotAccount[]>({ url: '/bots' }),
  create: (payload: BotPayload) =>
    request<BotAccount>({ url: '/bots', method: 'POST', data: payload }),
  update: (botQq: string, payload: BotPayload) =>
    request<BotAccount>({ url: `/bots/${botQq}`, method: 'PUT', data: payload }),
  remove: (botQq: string) =>
    request({ url: `/bots/${botQq}/delete`, method: 'POST' }),
  groups: (botQq: string) =>
    request<BotGroup[]>({ url: `/bots/${botQq}/groups` }),
  saveGroup: (botQq: string, payload: BotGroupPayload) =>
    request<BotGroup>({ url: `/bots/${botQq}/groups`, method: 'POST', data: payload }),
  removeGroup: (botQq: string, groupId: string) =>
    request({ url: `/bots/${botQq}/groups/${groupId}/delete`, method: 'POST' }),
  syncGroups: (botQq: string) =>
    request<BotGroup[]>({ url: `/bots/${botQq}/groups/sync`, method: 'POST' }),
  aiProviders: () => request<AiProviderOption[]>({ url: '/bots/ai/providers' }),
  aiConfig: (botQq: string) =>
    request<BotAiConfig>({ url: `/bots/${botQq}/ai` }),
  saveAiConfig: (botQq: string, payload: BotAiConfigPayload) =>
    request<BotAiConfig>({ url: `/bots/${botQq}/ai`, method: 'PUT', data: payload }),
  testAiConfig: (botQq: string, payload: BotAiConfigPayload, modelType: 'text' | 'image' | 'generation' = 'text') =>
    request<AiModelTestResult>({ url: `/bots/${botQq}/ai/test`, method: 'POST', params: { model_type: modelType }, data: payload }),
  aiModels: (botQq: string, payload: BotAiConfigPayload, modelType: 'text' | 'image' | 'generation' = 'text') =>
    request<AiModelOption[]>({ url: `/bots/${botQq}/ai/models`, method: 'POST', params: { model_type: modelType }, data: payload }),
  catalog: () => request<ActionCatalogItem[]>({ url: '/bots/catalog' }),
  execute: (botQq: string, action: string, params: Record<string, unknown>) =>
    request<Record<string, unknown>>({
      url: `/bots/${botQq}/actions`,
      method: 'POST',
      data: { bot_qq: botQq, action, params },
    }),
}

export interface PluginQuery {
  page: number
  page_size: number
  keyword?: string
  source_type?: string
  published?: boolean
}

/** 公开插件首页查询参数，无需登录。 */
export interface RegistryPluginQuery {
  page: number
  page_size: number
  keyword?: string
  sort?: 'all' | 'latest' | 'popular' | 'name'
}

export const registryApi = {
  plugins: (params: RegistryPluginQuery) =>
    request<Pagination<RegistryPlugin>>({ url: '/registry/plugins', params }),
  detail: (pluginId: string) =>
    request<RegistryPlugin | null>({ url: `/registry/plugins/${pluginId}` }),
  stats: () => request<RegistryStats>({ url: '/registry/stats' }),
}

export interface PluginInstallationQuery {
  page: number
  page_size: number
  bot_qq?: string
  plugin_id?: string
  enabled?: boolean
  // 只看当前账号自己的安装，管理员在插件市场页需要它排除全站记录。
  mine?: boolean
}

export interface PluginSubmitResult {
  plugin: PluginItem
  version: PluginVersionInfo
}

export const pluginApi = {
  list: (params: PluginQuery) =>
    request<Pagination<PluginItem>>({ url: '/plugins', params }),
  // 我提交过的插件及其最新审核进度，与「已安装」是两回事。
  mine: (params: { page: number; page_size: number; keyword?: string; review_status?: string }) =>
    request<Pagination<MyPluginItem>>({ url: '/plugins/mine', params }),
  detail: (pluginId: string) =>
    request<PluginDetail>({ url: `/plugins/${pluginId}` }),
  capabilities: () =>
    request<PluginCapabilityItem[]>({ url: '/plugins/capabilities' }),
  submitFromRepo: (payload: PluginRepoSubmitPayload) =>
    request<PluginSubmitResult>({ url: '/plugins/repo', method: 'POST', data: payload }),
  // 探测仓库最新版本：只读清单，不登记、不落库，用于上传前确认。
  probeRepoLatest: (payload: PluginRepoSubmitPayload) =>
    request<PluginRepoProbeResult>({ url: '/plugins/repo/probe', method: 'POST', data: payload }),
  // 检查已登记插件的仓库里是否有新版本，只探测、不提交。
  checkPluginUpdate: (pluginId: string) =>
    request<PluginRepoProbeResult>({ url: `/plugins/${pluginId}/check-update`, method: 'POST' }),
  // 压缩包以原始字节上传，后端按 content-type 识别，不使用表单编码。
  submitPackage: (file: File) =>
    request<PluginSubmitResult>({
      url: '/plugins/package',
      method: 'POST',
      data: file,
      headers: { 'Content-Type': 'application/zip' },
    }),
  installations: (params: PluginInstallationQuery) =>
    request<Pagination<PluginInstallation>>({ url: '/plugins/installations', params }),
  install: (payload: PluginInstallPayload) =>
    request<PluginInstallation>({
      url: '/plugins/installations', method: 'POST', data: payload,
    }),
  updateInstallation: (id: number, payload: PluginInstallationUpdatePayload) =>
    request<PluginInstallation>({
      url: `/plugins/installations/${id}`, method: 'PUT', data: payload,
    }),
  uninstall: (id: number) =>
    request({ url: `/plugins/installations/${id}/delete`, method: 'POST' }),
  // 手动升级：不传版本号表示升到插件当前生效版本，传旧版本号即为回滚。
  upgradeInstallation: (id: number, version = '') =>
    request<PluginUpgradeResult>({
      url: `/plugins/installations/${id}/upgrade`,
      method: 'POST',
      data: { version },
    }),
  // 删除插件：管理员可删任意插件，作者只能删自己提交的。
  deletePlugin: (pluginId: string) =>
    request<PluginDeleteResult>({ url: `/plugins/${pluginId}/delete`, method: 'POST' }),
}

export interface PluginReviewQuery {
  page: number
  page_size: number
  plugin_id?: string
  review_status?: string
  keyword?: string
}

export interface PluginReviewLogQuery {
  page: number
  page_size: number
  plugin_id?: string
  action?: string
}

export const pluginAdminApi = {
  versions: (params: PluginReviewQuery) =>
    request<Pagination<PluginReviewVersion>>({ url: '/plugin_admin/versions', params }),
  approve: (payload: {
    plugin_id: string
    version: string
    granted_capabilities?: string[]
    comment?: string
  }) =>
    request<{ version: PluginReviewVersion; plugin: Record<string, unknown> }>({
      url: '/plugin_admin/versions/approve', method: 'POST', data: payload,
    }),
  reject: (payload: { plugin_id: string; version: string; comment: string }) =>
    request<{ version: PluginReviewVersion; plugin: Record<string, unknown> }>({
      url: '/plugin_admin/versions/reject', method: 'POST', data: payload,
    }),
  publish: (payload: { plugin_id: string; version?: string }) =>
    request<Record<string, unknown>>({
      url: '/plugin_admin/publish', method: 'POST', data: payload,
    }),
  unpublish: (pluginId: string) =>
    request<Record<string, unknown>>({
      url: '/plugin_admin/unpublish', method: 'POST', data: { plugin_id: pluginId },
    }),
  reviewLogs: (params: PluginReviewLogQuery) =>
    request<Pagination<PluginReviewLog>>({ url: '/plugin_admin/review-logs', params }),
  trustedAuthors: (params: {
    page: number
    page_size: number
    keyword?: string
    active?: boolean
  }) =>
    request<Pagination<PluginTrustedAuthor>>({
      url: '/plugin_admin/trusted-authors', params,
    }),
  grantTrust: (payload: { user_id: number; reason?: string }) =>
    request<PluginTrustedAuthor>({
      url: '/plugin_admin/trusted-authors', method: 'POST', data: payload,
    }),
  revokeTrust: (id: number) =>
    request({ url: `/plugin_admin/trusted-authors/${id}/revoke`, method: 'POST' }),
  // 仓库来源插件自动同步：定时任务之外的手动触发入口。
  syncRepos: () =>
    request<{
      scanned: number
      submitted: number
      auto_published: number
      up_to_date: number
      failed: string[]
      checked_at: string
    }>({ url: '/plugin_admin/sync-repos', method: 'POST' }),
  syncStatus: () =>
    request<{
      enabled: boolean
      interval_seconds: number
      batch: number
      last_run_at: string
      result: Record<string, unknown>
    }>({ url: '/plugin_admin/sync-status' }),
}
