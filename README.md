# NapCatQQ Service

NapCatQQ 的配套管理服务，当前首版包含：

- 极验 GT3 行为验证，支持账号密码、邮箱密码和邮箱验证码三种登录方式。
- 邮箱验证码注册，个人资料、邮箱绑定和密码安全设置。
- 管理员系统设置，按 Tab 配置 SMTP 发件人、默认主题与系统介绍。
- 仪表盘真实统计：自动回复规则、机器人和连接状态。
- 自动回复规则的分页查询、新增、编辑、启停和软删除。
- 进群欢迎词按机器人和群号配置，支持默认规则、指定群优先、机器人开关和 AI 回退。
- 好友列表按机器人从 NapCat 同步入库，支持分组名称补齐、失效标记和多用户数据隔离。
- 机器人维度 AI 回复，支持 OpenAI 兼容、Anthropic、Gemini 和 DashScope 应用。
- NapCat HTTP POST 事件上报服务端，支持 `x-signature` HMAC-SHA1 验签和快速回复。
- NapCat 反向 WebSocket 服务端，支持事件广播、自动回复和动作响应关联。
- 业务侧 OneBot HTTP 动作代理：`/onebot/api/{action}`；业务 WebSocket：`/ws`。
- 插件系统：清单严格校验、能力白名单、插件包归一化、注册表查询、审核流转、信任名单、安装启停、事件钩子、子进程执行器与运行期能力闸门，默认开启。
- 配套示例插件 `video-parser`（源码本地维护，不入库）：群聊贴抖音、B站、小红书链接，先回标题/作者/互动数据/简介的描述卡片，再把视频发出来（默认不限时长），仅 QQ 拒收时补无水印直链，并带解析结果缓存。

## 本地运行

Python 3.11+、Node.js 22+ 和 pnpm/npm 可用时：

```powershell
cd backend
python -m pip install -e .
python main.py
```

另开终端启动前端开发服务器，`VITE_API_TARGET` 指向后端地址：

```powershell
cd frontend
$env:VITE_API_TARGET = 'http://127.0.0.1:8000'
npm install
npm run dev
```

开发端固定访问 `http://127.0.0.1:5178`；如果端口已被占用，Vite 会直接退出，不会自动切换端口。默认管理员账号为 `admin`，密码由根目录 `.env` 的 `ADMIN_PASSWORD` 设置。

首次使用邮箱登录或注册前，管理员需要进入“系统设置 -> 发件人设置”填写 SMTP 服务器、端口、账号和邮箱授权码。SMTP 密码在数据库中加密保存，管理接口只返回掩码。

认证相关接口：

```text
GET  /api/geetest/register
POST /api/geetest/validate
POST /api/auth/email-code
POST /api/auth/register
POST /api/auth/login
PUT  /api/auth/email
```

极验使用 GT3，Captcha ID 和 Private Key 集中定义在 `backend/app/common/config.py`。系统设置公开读取地址为 `/api/settings/public`，管理员分组设置地址为 `/api/settings/{group}`。

## NapCat 配置

`.env` 中的 `NAPCAT_TOKEN` 要与 NapCat 配置使用同一个值。推荐使用反向 WebSocket：

```text
WebSocket Client URL: ws://<服务地址>:8000/onebot/v11/ws
Token: <NAPCAT_TOKEN>
```

NapCat 的 HTTP Client 可以将事件上报地址配置为：

```text
http://<服务地址>:8000/onebot/v11/events
```

HTTP 上报命中规则时，服务会返回 OneBot 快速操作 `{ "reply": "..." }`。反向 WebSocket 命中规则时，服务会通过同一连接调用 `send_group_msg` 或 `send_private_msg`。

机器人管理中为每个机器人填写 NapCat HTTP 地址；如果 NapCat HTTP Server 启用了独立 Token，再填写对应 Token。本服务即可代理调用 NapCat 的正向 HTTP Server。业务程序调用动作：

```http
POST /onebot/api/get_login_info?access_token=<SERVICE_TOKEN>
Content-Type: application/json

{"echo":"request-1"}
```

业务 WebSocket 地址为 `ws://<服务地址>:8000/ws?access_token=<SERVICE_TOKEN>`，收发 OneBot 11 标准动作和事件对象。

仪表盘默认按当前主机名和后端 `PORT` 展示上述对外接口。如果后端使用了不同的公网域名、端口映射或 HTTPS 反向代理，请在 `.env` 中设置 `PUBLIC_SERVICE_URL`，例如 `https://api.example.com`。

## 机器人与群范围

登录管理界面后，先在“机器人管理”中添加机器人 QQ。每个机器人可独立选择：

- `auto`：优先使用该 QQ 的反向 WebSocket，失败后使用该机器人配置的 HTTP 地址。
- `websocket`：仅使用 `X-Self-ID` 对应的反向 WebSocket 连接。
- `http`：仅使用该机器人配置的 NapCat HTTP 地址和 Token。

群策略选择“仅白名单群开启”后，可以手动添加群，或通过 `get_group_list` 从 NapCat 同步。新同步的群默认关闭，需要逐个启用。HTTP、业务 WebSocket、事件广播和自动回复使用同一套群范围校验。

“接口中心”包含当前 NapCat 文档的全部动作，并额外保留兼容动作。动作执行必须选择机器人；群动作还必须选择已启用群。原动作代理调用群动作时也需要提供机器人和群号：

```http
POST /onebot/api/send_group_msg?access_token=<SERVICE_TOKEN>
X-Self-ID: 123456
Content-Type: application/json

{"group_id":987654,"message":"hello"}
```

## AI 回复

在“机器人管理”的操作列点击“AI 配置”，每个机器人可独立配置平台、API 地址、API Key、模型、系统提示词、Temperature 和最大回复 Token。API Key 加密保存，管理接口不返回明文。配置接口为：

```text
GET  /api/bots/ai/providers
GET  /api/bots/{bot_qq}/ai
PUT  /api/bots/{bot_qq}/ai
POST /api/bots/{bot_qq}/ai/test
POST /api/bots/{bot_qq}/ai/models
```

消息处理固定按“群范围校验 -> 关键字自动回复 -> AI 回复”执行。关键字命中后不会调用 AI；AI 开启时，私聊消息可直接触发，群聊处理 `@当前机器人`、引用当前机器人消息以及戳一戳当前机器人的事件。引用消息会通过 OneBot `get_msg` 校验发送者，并将被引用的机器人文本作为上一轮回复传给 AI。

传给 AI 的当前消息、携带的历史记录、定时主动回复和消息意图判断均包含发送者 QQ、QQ 昵称和群名片，以 QQ 区分同名用户。发送者资料以独立 JSON 行放在正文之前，缺失字段为空字符串；机器人历史消息继续保留机器人角色标记。该信息直接读取 OneBot 事件或已有聊天记录，不额外查询 QQ 资料，也不修改历史数据。

群聊和私聊的引用正文保留原发送者资料；合并转发另附各节点标注的发送者资料，不将其当作转发者本人的发言，也不把昵称混进关键词匹配正文。私聊历史按发送人或机器人回复的接收人限定会话；历史查询以触发消息的入库记录为边界，即使消息 ID 缺失，也不会把当前消息及其后的消息当作之前的对话。

## 进群欢迎词

在“机器人管理”表格中打开“进群欢迎”，再进入“进群欢迎词”菜单配置内容。每条规则都绑定一个机器人：群号留空表示该机器人的所有群默认欢迎词，填写群号则只匹配该群，指定群规则优先于默认规则。机器人开关关闭时不会处理进群事件；规则未配置或已停用且机器人 AI 已开启时，会调用该机器人的 AI 生成简短欢迎内容。HTTP 快速回复和 WebSocket `send_group_msg` 都会自动在正文前 `@` 新成员。

欢迎词接口：

```text
GET  /api/welcome_messages
POST /api/welcome_messages
PUT  /api/welcome_messages/{rule_id}
POST /api/welcome_messages/{rule_id}/delete
```

## 好友列表

进入“好友列表”菜单，在页头选择机器人后点“同步好友”，服务端通过 NapCat 的 `get_friend_list` 拉取该机器人的全部好友并入库，同时读取 `get_friends_with_category` 补齐好友分组名称（NapCat 不支持该动作时分组名留空，不影响同步）。同步是幂等的：新好友新增，已存在的好友更新昵称、备注、等级等资料，本次未返回的好友只做软删除标记为失效，不会物理删除历史数据。好友按机器人归属写入 `owner_id`，普通用户只能同步和查询自己机器人的好友，管理员可查看全部。性别取值来自 `friend_sex` 数据字典，前端展示中文名称。

好友接口：

```text
GET  /api/friends
POST /api/friends/{bot_qq}/sync
POST /api/friends/{bot_qq}/{friend_qq}/messages/sync
```

好友表格每行有“聊天记录”按钮，点开弹窗后可查看该好友与机器人的私聊记录（按事件时间倒序、支持分页），并可点“同步聊天记录”从 NapCat 的 `get_friend_msg_history` 拉取历史。该动作是分页接口，服务端按本页最早一条的 `message_seq` 作为游标持续往前翻，单次最多 40 页、每页 50 条；连续 3 页都没有新增时提前结束。历史消息写入 `chat_messages` 表并以 `source=history` 标记，按机器人 QQ + 消息 ID 去重，因此重复同步只会补齐新消息，不会产生重复数据。同步下来的私聊历史也会成为该好友后续 AI 私聊对话的上下文来源。

聊天记录查询接口新增 `user_qq` 和 `private_only` 两个参数：前者同时匹配 `user_id`、`sender_user_id` 和 `target_id`，因此机器人发出的消息也能查到；后者只返回群号为空的私聊记录。

“聊天记录”菜单页支持导出和勾选删除（AI 回复记录视图不含这两个功能）：

```text
GET  /api/chat_records/messages/export
POST /api/chat_records/messages/batch-delete
```

导出按当前筛选条件导出**全部**匹配记录（不只当前页），范围与列表查询完全一致（含用户数据范围），CSV 带 UTF-8 BOM，Excel 打开中文不乱码；以等号、加号、减号、@ 开头的内容会加单引号前缀，避免 Excel 当公式执行。单次导出上限 5 万行，超出会提示缩小筛选范围而不是静默截断。勾选删除单次最多 100 条，只把 `is_deleted` 置为 true，数据仍在库中，删除后不再出现在列表和导出结果里。

## 插件系统

插件系统默认**开启**，无需任何额外配置。需要整体停用时，在 `.env` 中设置 `PLUGIN_ENABLED=false` 后重启后端，此时所有插件接口都会返回「插件功能已关闭」的中文提示，不会影响任何既有功能。

### 插件包结构

一个插件包就是一个 zip 压缩包，**根目录必须包含 `manifest.json`**：

```json
{
  "manifest_version": 1,
  "id": "com.example.weather",
  "name": "天气查询",
  "version": "1.0.0",
  "description": "查询城市天气并发送到群聊",
  "author": { "name": "作者名", "url": "https://example.com" },
  "license": "MIT",
  "homepage": "https://example.com/weather",
  "entry": "plugin.py",
  "icon": "icon.png",
  "capabilities": ["msg.send", "http.outbound"],
  "allowed_domains": ["api.weather.com"],
  "min_host_version": "1.0.0",
  "max_host_version": "2.0.0",
  "dependencies": ["httpx>=0.27"],
  "config_schema": [
    { "key": "city", "label": "默认城市", "type": "text", "required": true, "sensitive": false }
  ]
}
```

清单校验是**严格模式**：出现任何未知字段一律拒收；`id` 必须包含点或连字符且入库后不可修改；`version` 必须是三段式 `x.y.z`；`entry` 必须是包内的 `.py` 文件；`license` 必填；压缩包内路径不允许绝对路径、目录穿越和盘符。

### 能力白名单

插件能做什么由清单里的 `capabilities` 声明，取值必须落在系统白名单内，**不存在「可调任意动作」的万能能力**：

| 风险 | 能力 |
| --- | --- |
| 低 | `plugin.storage`、`cache.kv`、`bot.info`、`group.info` |
| 中 | `friend.info`、`msg.send`、`msg.recall`、`msg.history`、`msg.media`、`llm.generate`、`scheduler.register` |
| 高 | `group.mute`、`group.kick`、`group.settings`、`friend.handle`、`http.outbound` |

风险等级用于审核：低风险与中风险能力随审核通过生效，**高风险能力必须由管理员在审核时显式勾选**才会写入「已批准能力」。声明 `http.outbound` 时必须同时给出 `allowed_domains` 出网域名白名单，否则清单一律拒收；未声明该能力时不允许填写白名单。

系统不提供数据库读写能力，插件自己的状态只能走 `plugin.storage` 或 `cache.kv`。能力中文名称保存在数据字典 `plugin_capability`，前端从字典接口读取，不在前端硬编码。

### 接口

```text
GET  /api/plugins                      插件列表（后端分页，普通用户只看已上架插件与自己提交的）
GET  /api/plugins/capabilities         能力目录（含风险等级，供审核面板展示）
GET  /api/plugins/{plugin_id}          插件详情与全部版本
POST /api/plugins/repo                 从代码仓库提交版本（source_ref 留空即自动探测最新版本）
POST /api/plugins/repo/probe           探测仓库最新版本，只读清单不落库（需上传资格）
POST /api/plugins/package              上传插件压缩包提交版本
GET  /api/plugins/installations        安装记录（后端分页，按机器人 / 插件 / 启用状态筛选，mine=1 只看自己）
POST /api/plugins/installations        为当前账号安装插件并分配机器人（安装后默认停用）
PUT  /api/plugins/installations/{id}   修改启用状态、插件配置、生效能力与生效机器人
POST /api/plugins/installations/{id}/upgrade 升级到新版本（body 可带 version 指定版本/回滚）
POST /api/plugins/installations/{id}/delete  卸载插件（记录保留）
POST /api/plugins/{plugin_id}/delete   删除插件（管理员可删任意，作者只能删自己提交的）
POST /api/plugins/{plugin_id}/check-update  检查仓库来源插件的仓库里是否有新版本（只探测不提交）
GET  /api/plugin_admin/versions        审核台版本列表（仅管理员）
POST /api/plugin_admin/versions/approve      审核通过并批准能力（仅管理员）
POST /api/plugin_admin/versions/reject       驳回版本，必须填写原因（仅管理员）
POST /api/plugin_admin/publish         上架 / 切换当前生效版本（仅管理员）
POST /api/plugin_admin/unpublish       下架插件（仅管理员）
GET  /api/plugin_admin/review-logs     审核流水（仅管理员）
GET  /api/plugin_admin/action-logs     插件动作审计（仅管理员）
GET  /api/plugin_admin/trusted-authors 信任名单（仅管理员）
POST /api/plugin_admin/trusted-authors 新增信任授权（仅管理员）
POST /api/plugin_admin/trusted-authors/{id}/revoke  撤销信任授权（仅管理员）
```

## 登录日志

用户登录**无论成功还是失败都会写入登录日志表**（`login_logs`），记录登录账号、登录方式（数据字典 `login_type`）、是否成功、客户端 IP（优先取 `X-Forwarded-For`）、结果说明和发生时间（北京时间）。

- 日志**只追加**，系统不提供修改和删除入口；
- 左侧菜单「登录日志」支持按关键字、登录方式、时间区间筛选，后端分页返回；
- 普通用户只能查看自己的登录记录，管理员可以查看全部用户的登录记录；
- 查询接口：`GET /api/login-logs`。

## 邮件日志

凡是会触发邮件发送的入口（注册验证码、邮箱验证码登录、绑定邮箱验证码），**无论发送成功、失败，还是被限流或配置缺失等原因拒绝，都会写入邮件日志表**（`email_logs`），记录收件邮箱、触发操作（数据字典 `email_log_action`）、是否成功、客户端 IP（优先取 `X-Forwarded-For`）、结果说明和发生时间（北京时间）。失败的记录会在业务异常抛出之前先落库，保证每一次触发尝试都可审计。

- 日志**只追加**，系统不提供修改和删除入口；
- 触发操作取值入库数据字典 `email_log_action`：`register_code`（注册验证码）、`login_code`（邮箱验证码登录）、`bind_email`（绑定邮箱验证码）；新增任何发信功能时必须同步扩展该字典取值；
- 左侧菜单「邮件日志」**仅管理员可见**，支持按邮箱关键字、触发操作、发送结果、时间区间筛选，后端分页返回；
- 查询接口：`GET /api/email-logs`（仅管理员）。

## 后端日志

后端会将应用、Uvicorn、OneBot、AI、数据库和异常日志同时输出到控制台及文件。默认文件为 `data/logs/napcat_service.log`，按北京时间每天轮转并保留 30 个历史文件；可通过 `.env` 中的 `LOG_FILE`、`LOG_LEVEL`、`LOG_BACKUP_COUNT` 调整。日志包含时间、级别、logger、进程、线程、源码文件和行号，并保留完整原始参数。Docker 部署时 `data` 目录已挂载到宿主机，容器重建不会丢失日志。

## Docker 部署

Docker 部署使用两个容器：FastAPI 后端和 Nginx 前端。MySQL、Redis 使用外部服务；如果运行在 Docker 宿主机上，地址使用 `host.docker.internal`，Compose 已为 Linux 配置 `host-gateway` 映射。`data` 目录挂载到宿主机，容器和镜像重建不会删除日志或 SQLite 数据。

### 本机构建部署

首次执行会从 `.env.example` 生成 `.env` 并停止，填写数据库、管理员密码、Redis 和 NapCat 配置后再次执行。极验凭据已固定在 `backend/app/common/config.py`，无需配置环境变量：

```powershell
.\deploy.ps1
```

Linux/macOS：

```bash
chmod +x deploy.sh
./deploy.sh
```

部署脚本会依次校验 Docker 与 Compose、检查必要环境变量、停止并删除本项目旧容器、按 `.env` 中的镜像名和 Tag 删除本项目旧镜像、重新构建、启动并等待健康检查。脚本不会删除数据目录，也不会清理其他项目镜像或容器。

默认访问地址：

```text
前端入口：http://<服务器地址>:5178
后端接口：http://<服务器地址>:8000
健康检查：http://<服务器地址>:5178/health
```

端口可通过 `.env` 中的 `FRONTEND_PORT`、`BACKEND_PORT` 修改。

### 自动化构建镜像

Windows 本地构建 `linux/amd64` 镜像：

```powershell
.\build_images.ps1
```

构建并推送到已登录的镜像仓库：

```powershell
.\build_images.ps1 -Registry registry.cn-shanghai.aliyuncs.com -Namespace zhinian-software -Tag latest -Push
```

Linux/macOS 使用 `build_images.sh`，通过 `IMAGE_REGISTRY`、`IMAGE_NAMESPACE`、`IMAGE_TAG` 和 `PUSH_IMAGES=1` 控制镜像名称及是否推送。Jenkins 可直接使用 `Pipeline脚本-NapCatQQ_Service-amd64.groovy`，流水线同时推送构建编号和 `latest` 标签。

### 远程镜像部署

将 `deploy_remote.sh` 放到部署服务器目录即可。脚本会自动生成缺失的配置文件：

```text
deploy_remote.sh
```

首次运行会自动生成 `.env.remote` 和 `docker-compose.remote.yml`。脚本优先使用同目录的模板文件，没有模板时使用脚本内置模板。填写 `.env.remote` 完成后再次执行：

```bash
chmod +x deploy_remote.sh
./deploy_remote.sh
```

镜像仓库为私有仓库时，请先在部署服务器执行 `docker login <镜像仓库地址>`。

远程脚本会先拉取新镜像，再更新本项目容器并等待健康检查；新容器健康后只清理被替换的本项目旧镜像 ID。

常用命令：

```bash
docker compose --env-file .env.remote -f docker-compose.remote.yml -p napcatqq-service ps
docker compose --env-file .env.remote -f docker-compose.remote.yml -p napcatqq-service logs -f
docker compose --env-file .env.remote -f docker-compose.remote.yml -p napcatqq-service down
```
