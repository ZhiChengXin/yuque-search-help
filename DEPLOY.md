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
| 依赖网络 | 容器需能访问外网（拉取语雀知识库） |

服务启动后会自动登录语雀并同步知识库到内存索引；之后按 `SYNC_INTERVAL_SECONDS`（默认 600 秒）定时刷新。

---

## 2. 服务器前置条件

1. 已安装 Docker、Docker Compose（Compose V2 即可）
2. 服务器能访问：
   - Git 仓库
   - Docker 基础镜像源
   - `https://www.yuque.com`（业务同步依赖）
3. 开放端口 `8787`（或按实际映射端口配置防火墙 / 安全组）

### 2.1 建议配置国内 Docker 镜像加速

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
YUQUE_USERNAME=语雀账号（手机号/邮箱）
YUQUE_PASSWORD=语雀密码
```

**常用可选：**

```env
YUQUE_LOGIN=fumenggonggongzhanghao
YUQUE_REPO=eggylu
YUQUE_HOST=https://www.yuque.com
PORT=8787
SYNC_INTERVAL_SECONDS=600
APP_TITLE=孚盟MX 智能检索助手
```

注意：

- `.env` **不要提交到 Git**
- 语雀账号需对目标知识库有可读权限
- 账号密码仅保存在服务器本地 / 密钥系统，勿写入镜像

---

## 5. 构建与启动（Docker）

在项目根目录执行：

```bash
# 构建并后台启动
docker compose up -d --build

# 查看状态
docker compose ps

# 查看日志（首次启动会同步语雀，可能需要几十秒）
docker compose logs -f --tail=200
```

首次启动日志中出现类似「同步完成」且 `/api/status` 中 `ready=true` 即表示可用。

---

## 6. 验证

```bash
# 健康检查
curl -s http://127.0.0.1:8787/api/health

# 状态（文档数 / 章节数 / 联系人数）
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

### 手动触发知识库同步

页面左侧有「刷新知识库」按钮；也可：

```bash
curl -X POST http://127.0.0.1:8787/api/sync
```

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
| 页面能开，但一直「同步中 / 未就绪」 | 看日志：账号密码是否正确、服务器能否访问语雀 |
| 构建很慢 / 拉基础镜像失败 | 配置 Docker `registry-mirrors` 后重试 |
| pip 安装超时 | 确认 Dockerfile 仍使用清华源；检查出网代理 |
| 端口冲突 | 修改 `docker-compose.yml` 中 `ports`，如 `"8080:8787"` |
| 搜索结果为空 | 确认语雀知识库文档已发布；点「刷新知识库」后再试 |

---

## 11. 目录关键文件说明

```text
yuque-help-search/
├── Dockerfile              # 镜像构建（含国内 apt/pip 源）
├── docker-compose.yml      # 一键部署
├── .env.example            # 环境变量模板
├── .env                    # 真实配置（服务器本地创建，勿提交）
├── requirements.txt        # Python 依赖
├── DEPLOY.md               # 本手册
└── app/                    # 应用代码
```

---

## 12. 给运维的最短路径（复制即用）

```bash
git clone <仓库地址> yuque-help-search
cd yuque-help-search
cp .env.example .env
# 编辑 .env，填入 YUQUE_USERNAME / YUQUE_PASSWORD
docker compose up -d --build
curl -s http://127.0.0.1:8787/api/status
```

部署完成后把访问地址反馈给业务侧即可。
