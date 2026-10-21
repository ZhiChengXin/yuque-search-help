# 孚盟MX 智能检索助手 · Docker 部署手册

面向运维同学。业务侧将代码推送到 Git 后，按本文在服务器上用 **Docker Compose** 部署即可。

---

## 1. 服务说明

| 项 | 说明 |
| --- | --- |
| 服务名称 | 孚盟MX 智能检索助手 |
| 默认端口 | `8787` |
| 访问地址 | `http://<服务器IP>:8787` |
| 运行方式 | Docker Compose（单容器） |
| 依赖网络 | 容器需能访问外网（语雀同步 + OpenRouter 大模型/向量） |

服务启动后会用 **语雀 Token** 同步知识库到内存索引，并写入本地向量缓存（`data/embeddings`）；之后按 `SYNC_INTERVAL_SECONDS`（默认 600 秒）定时刷新。也可配置语雀 **Webhook**，文档发布/更新后自动同步。

---

## 2. 服务器前置条件

1. 已安装 Docker、Docker Compose（Compose V2 即可）
2. 服务器能访问：
   - Git 仓库
   - Docker 基础镜像源
   - `https://www.yuque.com`（知识库同步）
   - `https://openrouter.ai`（大模型重排与向量召回，若启用）
3. 开放端口 `8787`（或按实际映射端口配置防火墙 / 安全组）

### 2.1 服务器配置要求

#### 当前阶段（基础搜索功能）

| 项目 | 最低配置 | 推荐配置 |
| --- | --- | --- |
| **CPU** | 1 核 | 1-2 核 |
| **内存** | 1 GB | 1.5 - 2 GB |
| **磁盘** | 5 GB | 10 GB+ SSD |
| **带宽** | 出站 1 Mbps | 按实际用户量 |

#### 未来阶段（含扩展功能）

后续计划开发功能：**文档量增加**、**在线表格**、**问题记录**、**搜索排行榜分析**。

| 项目 | 预估需求 |
| --- | --- |
| **CPU** | 2-4 核 |
| **内存** | 2-4 GB |
| **磁盘** | 30-50 GB+ SSD |
| **带宽** | 出入 5-10 Mbps（视并发量） |

> **说明**：当前阶段内存主要消耗在 Python 进程启动 + jieba 分词词典；未来加入在线表格和问题记录统计后，数据量和计算量会明显增长，建议一步到位。

#### 建议一步到位配置

> **推荐：2 核 4G 40GB SSD**

| 项目 | 配置 |
| --- | --- |
| CPU | 2 核 |
| 内存 | 4 GB |
| 系统盘 | 40 GB SSD |
| 数据盘 | 建议单独挂载或配置大磁盘，用于存放 `data/` 索引缓存 |

#### 功能扩展注意事项

| 扩展功能 | 资源影响 | 说明 |
| --- | --- | --- |
| 文档量增加 | 磁盘 + 内存 ↑ | 向量/BM25 索引文件变大 |
| 在线表格 | CPU + 内存 ↑ | 大量并发编辑有计算压力；纯前端方案（SheetJS）则几乎无影响 |
| 问题记录 | 磁盘 + 数据库 | 持续写入，建议搭配 MySQL/SQLite |
| 搜索排行分析 | CPU + 内存 ↑ | 聚合统计，数据量大时查询变重 |

### 2.2 建议配置国内 Docker 镜像加速

编辑或创建 `/etc/docker/daemon.json`（示例，按公司现有加速源替换）：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://mirror.ccs.tencentyun.com"
  ]
}
```

然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

> 说明：本项目 `Dockerfile` 内已将 **apt / pip** 指向国内镜像；Docker Hub 拉基础镜像仍建议配 registry-mirrors。

---

## 3. 获取代码

```bash
# 按实际仓库地址替换
git clone <你们的 Git 仓库地址> yuque-help-search
cd yuque-help-search

# 如已有仓库，更新代码：
# git pull
```

---

## 4. 配置环境变量

```bash
cp .env.example .env
vi .env   # 或使用其他编辑器
```

**必填：**

```env
YUQUE_TOKEN=语雀个人 Token
```

> Token 获取：语雀 → 个人设置 → Token。Token 需对目标知识库有**读**权限；若使用「我要反馈」写入功能，还需具备对应反馈文档的写权限。

**常用可选：**

```env
YUQUE_LOGIN=wangyanling-xsdvq
YUQUE_REPO=vf1lt7
YUQUE_HOST=https://www.yuque.com

# 反馈文档（不进入检索结果）
YUQUE_FEEDBACK_LOGIN=wangyanling-xsdvq
YUQUE_FEEDBACK_REPO=ezw77w
YUQUE_FEEDBACK_SLUG=qr67p7rx343mvp9g

# 大模型 / 向量（OpenRouter）
LLM_API_KEY=你的 OpenRouter Key
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=qwen/qwen-2.5-72b-instruct
EMBEDDING_ENABLED=true
EMBEDDING_MODEL=qwen/qwen3-embedding-4b
DATA_DIR=data
YUQUE_WEBHOOK_SECRET=请改成随机字符串

PORT=8787
SYNC_INTERVAL_SECONDS=600
APP_TITLE=孚盟MX 智能检索助手
```

注意：

- `.env` **不要提交到 Git**
- **无需**再配置 `YUQUE_USERNAME` / `YUQUE_PASSWORD`（已统一为 Token）
- Token、LLM Key 仅保存在服务器本地 / 密钥系统，勿写入镜像
- 建议挂载或持久化 `data/` 目录，避免容器重建后重复全量计算向量

---

## 5. 构建与启动（Docker）

在项目根目录执行：

```bash
# 构建并后台启动
docker compose up -d --build

# 查看状态
docker compose ps

# 查看日志（首次启动会同步语雀并可能计算向量，可能需要一两分钟）
docker compose logs -f --tail=200
```

首次启动日志中出现类似「同步完成」且 `/api/status` 中 `ready=true` 即表示可用。若启用向量，状态里还会有 `vector_count`。

---

## 6. 验证

```bash
# 健康检查
curl -s http://127.0.0.1:8787/api/health

# 状态（文档数 / 章节数 / 联系人数 / 向量数）
curl -s http://127.0.0.1:8787/api/status

# 搜索接口冒烟
curl -s "http://127.0.0.1:8787/api/search?q=%E9%82%AE%E4%BB%B6&limit=3&mode=all"
```

浏览器访问：

```text
http://<服务器IP>:8787
```

---

## 7. 日常运维

### 更新发布（代码已 push 到 Git）

```bash
cd yuque-help-search
git pull
docker compose up -d --build
docker compose logs -f --tail=100
```

### 重启 / 停止

```bash
docker compose restart
docker compose stop
docker compose down
```

### 语雀 Webhook 自动同步（推荐）

1. 语雀知识库 → 设置 → **消息推送** → 其他渠道  
2. Webhook 地址填（公网可访问）：
   `http://<服务器IP>:8787/api/yuque/webhook?token=<YUQUE_WEBHOOK_SECRET>`
3. 勾选：**发布文档**、**更新文档**（可按需加删除类事件）  
4. 点「测试」；服务日志应出现 `webhook 触发同步`

未配置公网或 Webhook 时，仍会按 `SYNC_INTERVAL_SECONDS` 定时同步。运维也可手动：

```bash
curl -X POST http://127.0.0.1:8787/api/sync
```

可选：在 `data/synonyms.json` 维护同义词组（无需改代码），格式见仓库内示例。

### 查看容器日志

```bash
docker compose logs -f yuque-help-search
```

---

## 8. 依赖安装说明（镜像内已处理）

运维 **一般不需要** 在宿主机单独 `pip install`。镜像构建时已使用国内源：

| 类型 | 国内源 |
| --- | --- |
| apt（Debian） | `mirrors.aliyun.com` |
| pip | `https://pypi.tuna.tsinghua.edu.cn/simple` |

如需在宿主机临时安装（排查用，非部署主路径）：

```bash
python3 -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --trusted-host pypi.tuna.tsinghua.edu.cn \
  -r requirements.txt
```

---

## 9. 反向代理（可选）

若通过 Nginx 对外提供 HTTPS，示例：

```nginx
server {
    listen 443 ssl;
    server_name help-search.example.com;

    # ssl_certificate /path/to/fullchain.pem;
    # ssl_certificate_key /path/to/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 10. 常见问题

| 现象 | 排查 |
| --- | --- |
| 页面能开，但一直「同步中 / 未就绪」 | 看日志：`YUQUE_TOKEN` 是否有效、是否有知识库读权限、服务器能否访问语雀 |
| 构建很慢 / 拉基础镜像失败 | 配置 Docker `registry-mirrors` 后重试 |
| pip 安装超时 | 确认 Dockerfile 仍使用清华源；检查出网代理 |
| 端口冲突 | 修改 `docker-compose.yml` 中 `ports`，如 `"8080:8787"` |
| 搜索结果为空 | 确认语雀知识库文档已发布；等待定时同步或触发 Webhook / `POST /api/sync` 后再试 |
| 检索一直转圈 / 很慢 | 确认 OpenRouter 可访问；可临时设 `EMBEDDING_ENABLED=false` 对比；查看容器日志是否超时 |
| 向量数为 0 | 确认已配置 `LLM_API_KEY` 且 `EMBEDDING_ENABLED=true`，再执行一次同步 |

---

## 11. 目录关键文件说明

```text
yuque-help-search/
├── Dockerfile              # 镜像构建（含国内 apt/pip 源）
├── docker-compose.yml      # 一键部署
├── .env.example            # 环境变量模板（Token，无账号密码）
├── .env                    # 真实配置（服务器本地创建，勿提交）
├── requirements.txt        # Python 依赖
├── DEPLOY.md               # 本手册
├── data/                   # 本地数据（向量缓存等，建议持久化）
└── app/                    # 应用代码
```

---

## 12. 给运维的最短路径（复制即用）

```bash
git clone <仓库地址> yuque-help-search
cd yuque-help-search
cp .env.example .env
# 编辑 .env，至少填入 YUQUE_TOKEN；启用大模型/向量时再填 LLM_API_KEY
docker compose up -d --build
curl -s http://127.0.0.1:8787/api/status
```

部署完成后把访问地址反馈给业务侧即可。
