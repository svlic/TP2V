# TP2V

监听 Telegram **A 频道的新消息**，提取第一个独立的 **6 或 7 位数字编号**，在 **SOURCE** 中匹配消息，再原生转发到 **B**。支持多组 A → SOURCE → B，每组可独立选择匹配模式；同一 A 可以配置多个 SOURCE / B。

只实现匹配与转发，不包含 T4BY 的媒体下载、Hash 分类、MAN 或合并业务。

## 两种模式

| 配置 | 行为 | 前提 |
| --- | --- | --- |
| `mode = "nearby"` | 与 T4BY 当前 Reader 相同：定位 A 的转发来源，先检查原消息之后最近 `window` 条，再检查之前最近 `window` 条，首个命中即停止；默认各 5 条 | A 必须保留来自本组 SOURCE 的可见转发来源，原消息仍存在 |
| `mode = "search"` | 在 SOURCE 使用带引号的编号搜索，按新到旧检查最多 `search_limit` 条搜索结果，本地再次校验完整数字边界；默认 100 条，首个命中即停止 | 不要求 A 是转发消息，也不要求有来源元数据 |

- 编号规则：`(?<!\d)\d{6,7}(?!\d)`，保留前导零。例如 `编号：2636937（261014）` 提取 `2636937`，不从 8 位及以上数字中截取。
- 候选只需包含该完整编号，不额外要求“编号”字段、视频类型或“验证视频”文案。搜索结果相同编号有多个时取最新的一条，而非转发所有结果。
- 两种模式均排除已识别的 SOURCE 原消息及其整个媒体组；不会将它自己当作匹配结果。
- 媒体组算一条逻辑消息，匹配任一成员的 caption 即转发完整组，按原始 ID 顺序保留 caption 和转发来源。窗口按逻辑消息计数，不是消息 ID ±5；历史每页读取 60 条并支持继续翻页。
- 无编号直接忽略；缺少所需来源、未匹配或转发失败只记录日志，不向 B 发送占位消息。`nearby` 不会隐式回退到 `search`。

参考：T4BY 的 `reader.py`、`gateway.py` 和 `models.py`（参考版本 `d4b512e`）。T4BY 可用 Git 历史中未找到频道搜索实现；`search` 模式参考本机 TPBY 的带引号搜索与本地校验方式，但**不引入**它的“编号”字段限制、最多两组对象或媒体分类规则。

## 部署前提

需要 Python 3.11+，一个 Telegram **用户账号**（不是 Bot Token）。该账号应已加入所有 A / SOURCE，可读取历史和搜索，并具有向各 B 发消息的权限。SOURCE 的内容保护可能禁止原生转发，本程序不会绕过保护。

Docker 部署不需要在宿主机安装 Python，只需 Docker Engine 和 Docker Compose 插件。

## Docker Compose 部署（推荐）

在项目目录准备配置和凭据：

```bash copy Copy Docker Setup
cp config.example.toml config.toml
cp .env.example .env
mkdir -p data
```

编辑 `.env`，填写自己的 `TP2V_API_ID` / `TP2V_API_HASH`；编辑 `config.toml` 中的频道和路由。保留 `session = "data/tp2v"`、`database = "data/state.sqlite3"`，它们在容器内对应 `/app/data/`，持久保存在宿主机 `./data/`。如需自定义路径，必须确保它们仍位于挂载的数据目录内。`.env`、`config.toml` 和 `data/` 均已被 Git 忽略，不要上传凭据或 session。

拉取镜像并校验配置（不连接 Telegram）：

```bash copy Copy Docker Config Check
docker compose pull
docker compose run --rm tp2v --config /app/config.toml --check-config
```

首次登录必须在交互终端运行，依次输入手机号、验证码和可能的两步验证密码：

```bash copy Copy Docker Login
docker compose run --rm tp2v
```

看到 `TP2V started` 后按 Ctrl+C 结束首次运行，session 已保存在 `./data/`。再启动后台服务：

```bash copy Copy Docker Start
docker compose up -d --no-build
docker compose logs -f --tail=100 tp2v
```

退出日志查看用 Ctrl+C，不会停止后台服务。服务配置了 `unless-stopped` 重启策略。不要在后台服务运行时再次执行登录命令，避免多个进程共用 session / SQLite。

更新镜像、重启和停止：

```bash copy Copy Docker Update
docker compose pull
docker compose up -d --no-build
# 修改 config.toml 后重启：
docker compose restart tp2v
# 停止并移除容器，保留宿主机 data/：
docker compose down
```

备份时先停止服务，再备份整个 `data/`（包含登录凭据和去重数据库），以及 `config.toml` / `.env`；请限制备份的访问权限。无需映射任何入站端口。

### 镜像与本地构建

默认镜像为 `ghcr.io/svlic/tp2v:latest`，同一个标签支持 `linux/amd64` 和 `linux/arm64`（64 位 ARM），Docker 自动选择宿主机架构。可在 `.env` 设置 `TP2V_IMAGE=ghcr.io/svlic/tp2v:0.1.0` 固定已发布版本。

GitHub Actions 在推送 `main`、推送 `v*` 版本标签或手动触发时构建并发布到当前仓库的 GHCR 地址；PR 只构建、不发布。主分支生成 `latest`，例如 `v0.1.0` 生成 `0.1.0`，同时生成 `sha-...` 标签。首次发布后，如需匿名拉取，请在 GitHub Packages 中将包可见性设置为 Public；私有包需先使用具有 `read:packages` 权限的令牌执行 `docker login ghcr.io`。Fork 后请通过 `.env` 中的 `TP2V_IMAGE` 指向自己的镜像地址。

镜像尚未发布或希望从本地源码部署时，可替代上述拉取步骤：

```bash copy Copy Docker Build
docker compose build --pull
docker compose run --rm --pull never tp2v --config /app/config.toml --check-config
docker compose run --rm --pull never tp2v
# 首次登录成功后 Ctrl+C，再后台启动：
docker compose up -d --no-build --pull never
```

## Python 直接安装与运行

```bash copy Copy Installation
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp config.example.toml config.toml
```

编辑 `config.toml` 中的频道。每个 `[[routes]]` 是一组，可自由增加，`name` 必须唯一且应保持稳定。频道支持 `-100...` 数字 ID、数字字符串或公开用户名（可带 `@`）。`session` / `database` 相对路径以启动时的工作目录为准。

API 凭据从环境变量读取，请使用自己的 Telegram API 应用；不要将真实 API Hash 或 session 提交到 Git：

```bash copy Copy Startup
export TP2V_API_ID='你的 API ID'
export TP2V_API_HASH='你的 API Hash'
tp2v --config config.toml --check-config
tp2v --config config.toml
```

首次运行在交互终端依次输入手机号、验证码和可能的两步验证密码。看到 `TP2V started` 后开始监听。后续使用相同 `data/tp2v.session` 可免登录；停止使用 Ctrl+C。部署时应持久保存整个 `data/` 目录，并使用进程管理器管理后台运行。

`--check-config` 只校验本地 TOML，不读取凭据、不登录 Telegram；真正启动时再解析频道并检查每组 A / SOURCE / B 是否不同。请勿配置跨组环路（例如第一组 B 是第二组 A，第二组 B 又是第一组 A），否则可能循环转发。

## 运行边界

- 所有组共用一个用户会话，单进程运行；不要让多个实例共享 session 或数据库。
- 仅监听运行期间收到的新消息，不扫描 A 的旧消息，也不保证补齐停机或断线期间的消息。修改配置后需重启。
- SQLite 保存**成功转发**的 `(组名, A频道, 单消息/媒体组身份)`，同一输入重复投递或重启后再次投递不会再次转发；不同输入即使编号相同仍会各自处理。修改组名会改变去重身份，修改 SOURCE / B 不会清除该组已成功的输入记录。
- 未匹配或失败不记为成功；再次收到同一输入时可以重试，但当前没有持久化待办队列、定时重试或手动补发命令。若 SOURCE 消息晚于 A 到达，首次可能未匹配。
- Telegram 常规 FloodWait 交给 Telethon 等待并重试；处理串行化以避免并发重复转发。长时间限流会拖慢其他组；某一组异常不会阻止其他组继续处理同一输入。
- Telegram 转发与本地 SQLite 无法原子提交。若远端已发送、但进程在写入成功记录前崩溃，重新投递可能产生重复；不承诺 exactly-once。
- 搜索受 Telegram 索引及 `search_limit` 限制；窗口模式只查指定窗口。未命中并不代表 SOURCE 全历史中不存在该编号。

## 开发验证

```bash copy Copy Tests
pip install -e '.[test]'
pytest -q
ruff check .
```

测试不连接真实 Telegram，使用真实 Telethon 消息结构及内存频道历史，覆盖编号边界、双向窗口顺序、逻辑消息计数、跨页媒体组、搜索误命中排除、多组隔离、失败隔离和重启去重。
