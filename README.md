# PlugHub 插件库

NapCat 生态的插件分发平台：作者提交插件 → 平台审核 → 上架 → 下游插件市场按索引拉取安装。

平台本身不运行机器人、不执行插件代码，只做**收录、审核、版本分发**。

---

## 功能

| 模块 | 说明 |
|---|---|
| 展示首页 | 公开检索入口，卡片展示版本、能力声明、「新版本」角标，支持全部/最近更新/安装最多/名称排序 |
| 插件市场 | 已上架插件列表，可提交新插件（zip 上传或 GitHub tag/commit） |
| 我的插件 | 作者视角的提交记录：最新版本、审核状态、驳回原因、版本历史 |
| 插件审核 | 管理员审核版本与申请的能力，控制上架状态、维护信任作者名单 |
| 版本分发 | `index.json` 索引 + 插件包 zip 下载，全部接口带鉴权 |
| 仓库自动同步 | 定时探测 GitHub 仓库新 tag，自动登记新版本并按策略免审上架 |
| 系统设置 | 发件人、主题字体、插件同步、接入令牌、GitHub 接口 |

---

## 技术栈

- **后端**：FastAPI + SQLAlchemy 2（async）+ SQLite（默认）/ MySQL
- **前端**：Vue 3 + TypeScript + Vite，构建产物由 Nginx 或后端托管
- **依赖**：不需要 Redis，不需要消息队列，一套 SQLite 单文件即可跑起来

---

## 目录结构

```text
backend/
  app/
    routes/          接口路由（plugins / registry / settings / auth ...）
    services/        业务逻辑（提交、审核、分发导出、仓库同步）
    models/          SQLAlchemy 模型
    common/          配置、数据库、日志、安全
  main.py            启动入口
frontend/
  src/pages/         页面（首页、插件市场、我的插件、审核台、系统设置）
  src/styles/        全局样式与页面样式
.github/workflows/
  build-images.yml   构建并推送 Docker 镜像（GitHub Actions）
deploy/linux/
  start.sh           裸机一键启动
  stop.sh            裸机停止
  pluginhub.service  systemd 单元
data/                运行时数据（自动创建，需持久化）
  plugin_registry.db    SQLite 数据库
  plugins/packages/     归一化后的插件包目录
  plugins/archives/     提交时的原始 zip（分发用，校验和与索引一致）
  logs/                 应用日志
```

---

## 快速开始（Docker，推荐）

前置：Docker 24+ 与 Docker Compose v2。

```bash
cp .env.example .env
```

编辑 `.env`，至少填这两项：

```bash
ADMIN_PASSWORD=你的管理员密码
SETTINGS_ENCRYPTION_SECRET=一串足够长的随机字符
```

`SETTINGS_ENCRYPTION_SECRET` 用于加密保存 SMTP 密码与令牌，**运行后不要修改**，否则历史加密配置无法解密。

启动：

```bash
docker compose up -d --build
```

默认地址：

```text
前端入口   http://<服务器IP>:5178
后端接口   http://<服务器IP>:8000
健康检查   http://<服务器IP>:5178/health
```

端口在 `.env` 里用 `FRONTEND_PORT`、`BACKEND_PORT` 调整。

数据库默认 SQLite，文件落在挂载的 `data/` 目录，容器重建不丢数据。需要 MySQL 时：

```bash
docker compose --profile mysql up -d --build
```

并把 `.env` 的 `DATABASE_URL` 换成 `mysql+aiomysql://...`。

---

## 用预构建镜像部署（GitHub Actions 构建）

服务器网络慢、`docker compose up -d --build` 卡在 pip / npm 下载时，改用现成镜像。仓库内置流水线 `.github/workflows/build-images.yml`，在 GitHub 的机器上把两个镜像编好推进镜像库，服务器只负责 `pull`。

镜像名已经写死在 `docker-compose.yml` 的 `image:` 行里，**不用去 `.env` 里配**：

```text
ghcr.io/ovonull/pluginhub-backend:latest
ghcr.io/ovonull/pluginhub-frontend:latest
```

**首次使用要做一次**：打开 `https://github.com/users/ovonull?tab=packages` → 点进 `pluginhub-backend` → 右侧 *Package settings* → Danger Zone → *Change visibility* → Public，`pluginhub-frontend` 同样操作。不做这一步服务器拉取会报 `unauthorized`。（不想公开就改成 `docker login ghcr.io`，用带 `read:packages` 的 PAT 登录。）

服务器上只要两条：

```bash
docker compose pull
docker compose up -d
```

注意**不要带 `--build`**，否则又会在本地编译一遍。

如果 `docker compose up -d` 突然开始编译，说明镜像没拉到（多半是 GHCR 那个包还是私有的）。这时用 `docker compose up -d --no-build`，它会直接报错而不是偷偷跑一次慢编译。

**日常更新**：代码推到 main → 等流水线变绿 → 服务器 `docker compose pull && docker compose up -d`。要回滚就在 `.env` 里把 `IMAGE_TAG` 改成某个 `sha-xxxxxxx`。

**发版本**：`git tag v1.2.0 && git push origin v1.2.0` → 额外产出 `v1.2.0` / `v1.2` / `v1` 三个标签，`latest` 不受影响。

**国内服务器拉不动 ghcr.io**：在仓库 *Settings → Secrets and variables → Actions* 里加四个 Secret —— `ACR_REGISTRY`（如 `registry.cn-hangzhou.aliyuncs.com`）、`ACR_NAMESPACE`、`ACR_USERNAME`、`ACR_PASSWORD`（阿里云容器镜像服务个人版免费），流水线会同时推到 ACR；再在 `.env` 里覆盖 `BACKEND_IMAGE` / `FRONTEND_IMAGE` 指向 ACR 地址即可。

---

## Linux 裸机部署（不用 Docker）

前置：Python 3.11+、Node.js 18+。

```bash
chmod +x deploy/linux/start.sh deploy/linux/stop.sh
./deploy/linux/start.sh
```

首次执行会生成 `.env` 并退出，填好管理员密码与加密密钥后再次执行。脚本会依次：建 `.venv` → 安装后端依赖 → `npm ci && npm run build` → 后台启动后端 → 健康检查。

后端会同时托管前端（`FRONTEND_DIST=frontend/dist`），**只占一个端口**（默认 8120），不需要额外装 Nginx。

```text
访问地址   http://<服务器IP>:8120
停止       ./deploy/linux/stop.sh
日志       data/logs/pluginhub-console.log 与 pluginhub.log
```

生产环境建议改用 systemd，获得开机自启与崩溃重启：

```bash
sudo cp deploy/linux/pluginhub.service /etc/systemd/system/
# 按实际路径修改 User / WorkingDirectory / FRONTEND_DIST
sudo systemctl daemon-reload
sudo systemctl enable --now pluginhub
journalctl -u pluginhub -f
```

---

## 配置项

`.env` 中常用的几项，其余见 `.env.example` 注释。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PORT` | `8120` | 后端监听端口（裸机部署即访问端口） |
| `BACKEND_PORT` | `8120` | Docker 映射到宿主机的后端端口 |
| `FRONTEND_PORT` | `5178` | Docker 映射到宿主机的前端端口 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/plugin_registry.db` | 数据库连接串 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / — | 初始管理员账号，首次启动写入 |
| `SETTINGS_ENCRYPTION_SECRET` | — | 配置加密密钥，必填且不可变更 |
| `PUBLIC_SERVICE_URL` | 空 | 平台对外地址，填了索引里输出绝对下载地址 |
| `REGISTRY_TOKEN` | 空 | 分发接口接入令牌，`.env` 优先于后台设置 |
| `FRONTEND_DIST` | 空 | 裸机部署填 `frontend/dist`，后端顺带托管前端 |
| `PLUGIN_DATA_DIR` | `./data/plugins` | 插件包存放目录 |
| `PLUGIN_PACKAGE_MAX_SIZE` | `10485760` | 单个插件包体积上限（字节） |

---

## 分发接口与鉴权

**展示类接口（公开，不需要登录）** —— 插件库首页用的就是这几个，任何人都能访问：

| 接口 | 说明 |
|---|---|
| `GET /api/registry/plugins` | 分页列表，支持 `keyword`、`sort=all\|latest\|popular\|name` |
| `GET /api/registry/plugins/{plugin_id}` | 插件详情与版本历史 |
| `GET /api/registry/stats` | 插件数、安装数、作者数 |

**分发类接口（需要鉴权）** —— 给下游插件市场同步与安装用，两种身份任选：

- `Authorization: Bearer <接入令牌>` 或 `X-Registry-Token: <接入令牌>` —— 给下游程序用
- 已登录账号的会话令牌 —— 给前端页面用

| 接口 | 说明 |
|---|---|
| `GET /api/registry/index.json` | 已上架插件索引，下游定时同步用 |
| `GET /api/registry/packages/{plugin_id}/{version}.zip` | 下载当前生效版本的插件包 |

令牌有两种来源，鉴权时等价，区别只在「谁能看到和吊销」：

| 来源 | 谁创建 | 用途 |
|---|---|---|
| **系统设置 → 接入令牌** | 仅管理员 | 系统级公共令牌，发给机房里的下游服务共用；也可直接用 `.env` 的 `REGISTRY_TOKEN` |
| **个人设置 → 接入令牌** | 任意已登录账号 | 个人令牌，各自管各自的，账号之间互相看不见、也吊销不了对方的 |

**个人设置 → 接入令牌** 页面上方还会直接给出「API 调用地址」（索引地址与插件包地址），
复制就能填进下游配置。地址默认取当前站点域名；如果外面还挂了域名或反向代理，
在 `.env` 里设置 `PUBLIC_SERVICE_URL` 即可覆盖。每个账号最多同时保留 10 枚有效令牌，用完记得吊销。

两种令牌都留空时，只有已登录账号能下载插件包。

示例：

```bash
curl -H "Authorization: Bearer $REGISTRY_TOKEN" \
     http://<服务器IP>:8120/api/registry/index.json
```

鉴权失败返回业务码 `40100`，路径不存在或已下架返回 `404`。

**校验和约定**：索引里的 `checksum` 就是下载到的 zip 字节的 sha256，下游拿到包后直接比对即可，不需要重新打包或归一化。

---

## 插件提交与审核

提交入口在插件市场右上角，两种来源：

- **上传 zip**：包内根目录需有 `manifest.json` 与声明的入口文件
- **GitHub 仓库**：只接受 tag 或 commit，不接受分支

所有注册用户都可以提交插件。提交后的规则：

1. 插件首次入库需要人工审核
2. 已有审核通过版本后，新版本自动免审上架
3. 信任作者与管理员免审直接上架
4. 被管理员下架过的插件只免审通过，不会自动重新上架
5. 版本提交后不可修改，需要调整请提交新版本

仓库来源的插件还会被定时探测新 tag（系统设置 → 插件同步，默认 6 小时一轮）。

---

## 数据与备份

需要备份的只有 `data/` 目录：

```text
data/plugin_registry.db   数据库（账号、插件、版本、审核记录）
data/plugins/             插件包（packages 归一化目录 + archives 原始 zip）
data/logs/                日志
```

```bash
tar czf pluginhub-backup-$(date +%F).tar.gz data/
```

Docker 部署时 `data/` 已挂载到宿主机，直接备份该目录即可。

---

## 常见问题

**忘记管理员密码**
用 SQLite 工具把 `users` 表对应用户的 `password_hash` 置空后重启，或删库重新初始化（会丢数据）。

**前端打开是空白 / 404**
Docker 部署检查 `frontend` 容器与 Nginx 是否正常；裸机部署确认 `frontend/dist/index.html` 存在，且 systemd 单元里的 `FRONTEND_DIST` 指向正确。

**下游拉取返回 40100**
没带令牌或令牌不匹配。到「系统设置 → 接入令牌」确认已保存，请求头写 `Authorization: Bearer <令牌>`。

**索引里 `package_url` 是相对路径**
`PUBLIC_SERVICE_URL` 没填。填上平台对外地址（如 `https://plugins.example.com`）后，索引会输出绝对地址。

**改了 `SETTINGS_ENCRYPTION_SECRET` 之后 SMTP/GitHub 令牌失效**
该密钥参与加密，改动后历史密文无法解密，需要重新填写这些配置。
