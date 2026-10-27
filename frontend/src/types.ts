export interface ApiResult<T = null> {
  success: boolean
  code: number
  message: string
  data: T
}

export interface UserInfo {
  id: number
  username: string
  email: string | null
  email_verified: boolean
  nickname: string
  role_code: string
  role_name: string
  last_login_at: string | null
  created_at: string
}

export interface ManagedUser {
  id: number
  username: string
  email: string | null
  nickname: string
  role_code: string
  role_name: string
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

export interface CreateUserPayload {
  username: string
  nickname: string
  email: string | null
  password: string
}

/** 管理员修改用户时提交的字段，未填写的字段不会提交。 */
export interface UpdateUserPayload {
  nickname?: string
  email?: string | null
  role_code?: 'admin' | 'user'
  is_active?: boolean
}

export interface LoginLogRecord {
  id: number
  owner_id: number | null
  owner_username: string
  login_account: string | null
  login_type: string
  login_type_name: string
  is_success: boolean
  client_ip: string | null
  result_message: string | null
  created_at: string
}

export interface EmailLogRecord {
  id: number
  owner_id: number | null
  owner_username: string
  email: string
  action: string
  action_name: string
  is_success: boolean
  client_ip: string | null
  result_message: string | null
  created_at: string
}

/** 插件动作审计记录，由插件网关写入，只追加不可修改。 */
export interface PluginActionLogRecord {
  id: number
  plugin_id: string
  version_id: number | null
  owner_id: number | null
  bot_qq: string
  group_id: string
  user_id: string
  action_name: string
  capability: string
  params: string
  result_code: string
  result_message: string
  duration_ms: number
  created_at: string
}

export interface GeetestResult {
  challenge: string
  validate: string
  seccode: string
}

export type LoginType = 'username_password' | 'email_password' | 'email_code'

export interface LoginPayload {
  login_type: LoginType
  username?: string
  email?: string
  password?: string
  email_code?: string
  geetest_challenge: string
}

export interface RegisterPayload {
  username: string
  nickname?: string
  email: string
  password: string
  email_code: string
  geetest_challenge: string
}

export interface PublicSettings {
  appearance: { color: string; font: string; effect: string }
  introduction: { name: string; description: string }
}

export interface FeatureItem {
  id: number
  code: string
  name: string
  description: string
  trigger_keyword: string
  category: string
  sort_order: number
  is_enabled: boolean
}

export interface DictionaryOption {
  value: string
  label: string
}

export interface EmailSettings {
  host: string
  port: number
  username: string
  password: string
  sender_name: string
  use_ssl: boolean
}

export interface AppearanceSettings {
  color: string
  font: string
  effect: string
}

export interface IntroductionSettings {
  name: string
  description: string
}

export interface AnimatedEmojiSettings {
  classification_prompt: string
}

export interface GithubSettings {
  plugin_repo_token: string
}

/** 仓库插件自动同步设置。 */
export interface PluginSyncSettings {
  enabled: boolean
  interval_seconds: number
  batch: number
}

/** 插件库分发接口的接入令牌设置。 */
export interface RegistrySettings {
  token: string
}

/** 一枚接入令牌的展示信息，明文只在创建时返回。 */
export interface RegistryTokenItem {
  id: number
  name: string
  prefix: string
  created_at: string
  last_used_at: string
}

export interface AutoReplyRule {
  id: number
  owner_id: number
  owner_username: string
  keyword: string
  bot_qq: string
  reply_content: string
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface RulePayload {
  keyword: string
  bot_qq: string
  reply_content: string
  is_enabled: boolean
}

export interface ForbiddenWordRule {
  id: number
  owner_id: number
  owner_username: string
  bot_qq: string
  group_id: string
  keyword: string
  mute_prompt: string
  mute_duration: number
  mute_enabled: boolean
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface ForbiddenWordCreatePayload {
  bot_qq: string
  group_id: string
  keywords: string
  mute_prompt: string
  mute_duration: number
  mute_enabled: boolean
  is_enabled: boolean
}

export interface ForbiddenWordUpdatePayload {
  bot_qq: string
  group_id: string
  keyword: string
  mute_prompt: string
  mute_duration: number
  mute_enabled: boolean
  is_enabled: boolean
}

export interface AiTriggerKeywordRule {
  id: number
  owner_id: number
  owner_username: string
  bot_qq: string
  keyword: string
  is_enabled: boolean
  reply_all: boolean
  random_reply: boolean
  random_reply_probability: number
  created_at: string
  updated_at: string
}

export interface AiTriggerKeywordCreatePayload {
  bot_qq: string
  keywords: string
  is_enabled: boolean
  reply_all: boolean
  random_reply: boolean
  random_reply_probability: number
}

export interface AiTriggerKeywordUpdatePayload {
  bot_qq: string
  keyword: string
  is_enabled: boolean
  reply_all: boolean
  random_reply: boolean
  random_reply_probability: number
}

export interface Pagination<T> {
  items: T[]
  page: number
  page_size: number
  total: number
  pages: number
}

export interface ChatMessageRecord {
  id: number
  owner_id: number | null
  owner_username: string
  bot_qq: string
  group_id: string
  message_id: string
  sender_user_id: string
  sender_nickname: string
  message_text: string
  post_type: string
  source: string
  direction: string
  ai_triggered: boolean
  event_time: string
  created_at: string
}

export interface ChatRecordExport {
  file_name: string
  media_type: string
  total: number
  content: string
}

export interface AiReplyRecord {
  id: number
  owner_id: number
  owner_username: string
  rule_id: number
  bot_qq: string
  group_id: string
  message_id: string
  message_key: string
  source_message: string
  source_sender: string
  processed_at: string
  updated_at: string
}

export interface DashboardSummary {
  total_rules: number
  enabled_rules: number
  disabled_rules: number
  bot_count: number
  websocket_connections: number
  connected_bot_count: number
  connected_bots: string[]
  event_subscribers: number
  http_upstream_configured: boolean
  public_service_url: string
  recent_rules: AutoReplyRule[]
}

export interface BotAccount {
  id: number
  owner_id: number
  owner_username: string
  bot_qq: string
  nickname: string
  admin_qq: string
  connection_mode: 'auto' | 'websocket' | 'http'
  http_url: string
  has_access_token: boolean
  group_mode: 'all' | 'allowlist'
  is_enabled: boolean
  welcome_enabled: boolean
  auto_approve_friend: boolean
  auto_approve_group_request: boolean
  connected: boolean
  created_at: string
  updated_at: string
}

export interface BotPayload {
  bot_qq: string
  nickname: string
  admin_qq: string
  connection_mode: 'auto' | 'websocket' | 'http'
  http_url: string
  access_token: string
  group_mode: 'all' | 'allowlist'
  is_enabled: boolean
  welcome_enabled: boolean
  auto_approve_friend: boolean
  auto_approve_group_request: boolean
}

export interface BotGroup {
  id: number
  bot_qq: string
  group_id: string
  group_name: string
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface BotGroupPayload {
  group_id: string
  group_name: string
  is_enabled: boolean
}

export interface BotFriend {
  id: number
  owner_id: number
  owner_username: string
  bot_qq: string
  friend_qq: string
  nickname: string
  remark: string
  sex: string
  age: number
  level: number
  qid: string
  long_nick: string
  category_id: string
  category_name: string
  birthday: string
  synced_at: string | null
  created_at: string
  updated_at: string
  is_blocked: boolean
}

export interface FriendSyncResult {
  bot_qq: string
  total_count: number
  created_count: number
  updated_count: number
  invalid_count: number
}

export interface FriendMessageSyncResult {
  bot_qq: string
  friend_qq: string
  page_count: number
  fetched_count: number
  created_count: number
  existing_count: number
  ignored_count: number
}

export interface WelcomeMessage {
  id: number
  owner_id: number
  owner_username: string
  bot_qq: string
  group_id: string
  welcome_content: string
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface WelcomeMessagePayload {
  bot_qq: string
  group_id: string
  welcome_content: string
  is_enabled: boolean
}

export type AiProviderType =
  | 'openai_compatible'
  | 'anthropic'
  | 'gemini'
  | 'dashscope_app'

export interface AiProviderOption {
  value: AiProviderType
  label: string
  default_base_url: string
  supports_models: boolean
  requires_model: boolean
}

export interface BotAiConfigPayload {
  is_enabled: boolean
  provider_type: AiProviderType
  base_url: string
  api_key: string
  model_name: string
  image_model_name: string
  text_provider_type: AiProviderType
  text_base_url: string
  text_api_key: string
  text_model_name: string
  image_provider_type: AiProviderType | ''
  image_base_url: string
  image_api_key: string
  image_generation_provider_type: AiProviderType | ''
  image_generation_base_url: string
  image_generation_api_key: string
  image_generation_model_name: string
  system_prompt: string
  message_intent_prompt: string
  temperature: number
  max_tokens: number
  followup_count: number
  history_limit: number
  voice_reply_enabled: boolean
  voice_reply_probability: number
  image_reply_probability: number
  voice_character: string
}

export interface AiTestImage {
  media_type: string
  data: string
}

export interface AiModelTestResult {
  reply: string
  image: AiTestImage | null
}

export interface BotAiConfig extends BotAiConfigPayload {
  bot_qq: string
  has_api_key: boolean
  has_text_api_key: boolean
  has_image_api_key: boolean
  has_image_generation_api_key: boolean
  created_at: string | null
  updated_at: string | null
}

export interface AiProactiveReplyRule {
  id: number
  owner_id: number
  owner_username: string
  bot_qq: string
  group_id: string
  is_enabled: boolean
  min_interval_seconds: number
  max_interval_seconds: number
  next_run_at: string | null
  last_run_at: string | null
  last_message_id: string
  created_at: string
  updated_at: string
}

export interface AiProactiveReplyPayload {
  bot_qq: string
  group_id: string
  is_enabled: boolean
  min_interval_seconds: number
  max_interval_seconds: number
}

export interface AiVoiceCharacterRule {
  id: number
  owner_id: number
  owner_username: string
  bot_qq: string
  group_id: string
  character_id: string
  character_name: string
  preview_url: string
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface AiVoiceCharacterPayload {
  bot_qq: string
  group_id: string
  character_id: string
  character_name: string
  preview_url: string
  is_enabled: boolean
}

export interface AnimatedEmojiRecord {
  id: number
  owner_id: number | null
  owner_username: string
  bot_qq: string
  group_id: string
  group_name: string
  user_id: string
  sender_nickname: string
  message_id: string
  file_name: string
  summary: string
  media_type: string
  source_url: string
  category: string
  classification_error: string
  classification_attempts: number
  classified_at: string | null
  received_at: string
  created_at: string | null
  updated_at: string | null
}

export interface AnimatedEmojiBatchCategoryPayload {
  ids: number[]
  category: string
}

export interface AiVoiceCharacterOption {
  character_id: string
  character_name: string
  preview_url: string
}

export interface ScheduledTask {
  id: number
  task_key: string
  task_name: string
  task_description: string
  interval_seconds: number
  is_enabled: boolean
  next_run_at: string | null
  last_run_at: string | null
  last_status: 'pending' | 'running' | 'success' | 'partial' | 'failed' | string
  last_error: string
  last_result: string
  created_at: string | null
  updated_at: string | null
}

export interface ScheduledTaskPayload {
  interval_seconds: number
  is_enabled: boolean
}

export interface AiModelOption {
  value: string
  label: string
}

export interface ActionCatalogItem {
  name: string
  action: string
  execute_action?: string
  category: string
  requires_group: boolean
  dangerous: boolean
  compatibility: boolean
  example: Record<string, unknown>
}

export interface PluginVersionInfo {
  id: number
  plugin_id: string
  version: string
  entry: string
  package_path: string
  checksum: string
  source_ref: string
  review_status: string
  reviewed_by: number | null
  reviewed_at: string | null
  review_comment: string
  min_host_version: string
  max_host_version: string
  capabilities: string[]
  granted_capabilities: string[]
  created_at: string | null
  updated_at: string | null
}

export interface PluginItem {
  plugin_id: string
  name: string
  summary: string
  description: string
  author_name: string
  author_url: string
  homepage: string
  license: string
  source_type: string
  repo_url: string
  icon_path: string
  owner_id: number
  owner_username: string
  is_published: boolean
  current_version: PluginVersionInfo | null
  created_at: string | null
  updated_at: string | null
}

export interface PluginDetail {
  plugin: PluginItem
  versions: PluginVersionInfo[]
}

/** 「我的插件」页：自己提交的插件 + 最新版本审核进度。 */
export interface MyPluginItem extends PluginItem {
  latest_version: PluginVersionInfo | null
  version_count: number
  pending_count: number
}

/** 插件库公开首页的插件条目，字段来自 /api/registry/plugins。 */
export interface RegistryPlugin {
  plugin_id: string
  name: string
  summary: string
  description: string
  author: string
  author_url: string
  homepage: string
  license: string
  source_type: string
  repo_url: string
  source_ref: string
  icon_url: string
  version: string
  capabilities: string[]
  granted_capabilities?: string[]
  requested_capabilities?: string[]
  entry?: string
  install_count: number
  min_host_version: string
  max_host_version: string
  changelog: string
  published_at: string
  created_at: string
  updated_at: string
}

/** 公开首页顶部统计。 */
export interface RegistryStats {
  plugin_count: number
  install_count: number
  author_count: number
  updated_at: string
}

export interface PluginConfigField {
  key: string
  label: string
  type: 'text' | 'password' | 'number' | 'switch' | 'select' | string
  required: boolean
  sensitive: boolean
  options: string[]
}

export interface PluginInstallation {
  id: number
  owner_id: number
  plugin_id: string
  plugin_name: string
  plugin_summary: string
  plugin_published: boolean
  // 插件来源：repo 表示关联了代码仓库，可检查仓库更新；zip 只能手动上传新包。
  plugin_source_type: string
  plugin_repo_url: string
  // 该安装已分配到哪些机器人，同一账号下多台机器人共用这份安装与配置。
  bot_qqs: string[]
  version_id: number | null
  version: string
  is_enabled: boolean
  capabilities: string[]
  approved_capabilities: string[]
  granted_capabilities: string[]
  config: Record<string, string | number | boolean>
  config_schema: PluginConfigField[]
  // 插件有已上架的新版本时 has_update 为真，由用户手动点「升级」才会切换。
  has_update: boolean
  latest_version: string
  latest_version_id: number | null
  latest_capabilities: string[]
  latest_approved_capabilities: string[]
  created_at: string | null
  updated_at: string | null
}

// 升级接口在安装信息之外，额外回传本次切换的变化明细。
export interface PluginUpgradeResult extends PluginInstallation {
  from_version: string
  to_version: string
  dropped_config: string[]
  // 新版本新增、但没有自动授予的能力，需要用户到配置里自行勾选。
  new_capabilities: string[]
  missing_config: string[]
  // 新版本加了必填项且当前留空时，会被自动停用。
  auto_disabled: boolean
  // 被替换掉的旧版本号，已连同包目录删除；为空表示按规则保留或无需清理。
  purged_version: string
  purged_package_dir: string
  // 没有清理旧版本时的原因，例如仍被其他安装引用。
  purge_skipped_reason: string
}

export interface PluginDeleteResult {
  plugin_id: string
  name: string
  // 一并物理删除的版本记录数。
  deleted_versions: number
  // 受牵连而被卸载的安装记录数。
  affected_installations: number
  removed_package_dir: string
}

export interface PluginCapabilityItem {
  code: string
  name: string
  risk_level: string
  description: string
  actions: string[]
}

export interface PluginReviewVersion {
  id: number
  plugin_id: string
  plugin_name: string
  plugin_summary: string
  // 清单声明的作者，与提交人账号 owner_username 是两个不同来源。
  author_name: string
  is_published: boolean
  is_current: boolean
  // 该插件当前生效的版本号，列表按插件收敛后用它说明在线的是哪一版。
  current_version: string
  version: string
  entry: string
  checksum: string
  source_ref: string
  review_status: string
  reviewed_by: number | null
  reviewed_username: string
  reviewed_at: string | null
  review_comment: string
  min_host_version: string
  max_host_version: string
  capabilities: string[]
  granted_capabilities: string[]
  owner_id: number
  owner_username: string
  created_at: string | null
  updated_at: string | null
}

export interface PluginReviewLog {
  id: number
  plugin_id: string
  plugin_name: string
  version: string
  action: string
  from_status: string
  to_status: string
  reviewer_id: number | null
  reviewer_username: string
  comment: string
  created_at: string | null
}

export interface PluginTrustedAuthor {
  id: number
  user_id: number
  username: string
  granted_by: number | null
  granted_by_username: string
  reason: string
  is_active: boolean
  created_at: string | null
  updated_at: string | null
}

export interface PluginRepoSubmitPayload {
  repo_url: string
  // 留空表示自动探测仓库最新的 release / tag / commit。
  source_ref?: string
}

/** 仓库最新版本探测结果：上传前确认用，不落库。 */
export interface PluginRepoProbeResult {
  repo_url: string
  // 实际用于下载压缩包的引用：版本号 tag 或提交哈希。
  ref: string
  // 探测来源：release / tag / commit / ref（显式指定）。
  kind: string
  // 人类可读的 tag 名，来自 commit 时为空串。
  tag: string
  published_at: string
  plugin_id: string
  version: string
  name: string
  // 仅「检查更新」返回：仓库版本是否比已登记版本更新。
  has_new_version?: boolean
  // 仅「检查更新」返回：插件当前生效版本。
  current_version?: string
}

export interface PluginInstallPayload {
  plugin_id: string
  bot_qqs: string[]
}

export interface PluginInstallationUpdatePayload {
  is_enabled?: boolean
  config?: Record<string, string | number | boolean>
  granted_capabilities?: string[]
  bot_qqs?: string[]
}
