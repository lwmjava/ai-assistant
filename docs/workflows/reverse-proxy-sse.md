# 反向代理与流式长连接

本文说明如何在单实例生产编排外叠加 HTTPS 反向代理，使对话和运行续订的 SSE 流经代理时不被缓冲、不被过早断开，并统一带上安全响应头。本仓库不签发 TLS 证书，也不做多域名路由。本文中的命令不是本次会话的执行授权；未经授权不要启动生产编排，也不要把证书、私钥或环境文件写进仓库、聊天或日志。

## 使用方式

在仓库根目录把代理覆盖文件叠加到生产编排上。必须显式提供隔离的 `--env-file`、由部署方准备的证书和私钥，不要依赖工作区 `.env`。

```sh
docker compose --env-file /secure/assistant.prod.env \
  -p assistant-stage \
  -f docker-compose.prod.yml \
  -f deploy/compose.proxy.yml \
  config --quiet

docker compose --env-file /secure/assistant.prod.env \
  -p assistant-stage \
  -f docker-compose.prod.yml \
  -f deploy/compose.proxy.yml \
  up -d --build --wait --wait-timeout 600
```

额外需要的变量：

| 变量 | 要求 |
|---|---|
| TLS_CERT_FILE | 部署方准备的证书文件绝对路径，挂载为容器内 `/etc/nginx/certs/tls.crt` |
| TLS_KEY_FILE | 部署方准备的私钥文件绝对路径，挂载为容器内 `/etc/nginx/certs/tls.key` |
| PROXY_BIND_HOST | 默认 `127.0.0.1`；对外发布时再改为实际网卡地址 |
| PROXY_HTTPS_PORT | 默认 `443` |
| PROXY_HTTP_PORT | 默认 `80`，只做跳转到 HTTPS |
| CORS_ORIGINS | 必须写成浏览器实际访问的 HTTPS 来源，不能再用应用 loopback 地址 |

应用端口仍默认绑定 `127.0.0.1:8000`，只供本机排障。公开入口走代理的 443 端口。覆盖文件只新增 `proxy` 服务，不改生产编排里的应用、数据库或向量库声明。

## TLS 在代理处终止

客户端与反向代理之间使用 TLS。代理在 443 端口终止 TLS 后，在编排网络内用明文 HTTP 访问 `app:8000`。应用本身不对外提供 HTTPS。80 端口只返回 `301`，把请求转到 `https://$host$request_uri`；默认假定对外 HTTPS 端口是 443。

证书由部署方在仓库外准备并挂载，本仓库和本说明都不签发、不内置、不轮换证书。隔离检查脚本若需要自签证书，只写在一次性临时目录，用完即删，不能用于生产。

## 关闭缓冲、超时与心跳对齐

应用在流式对话期间每 15 秒发送一次心跳（`HEARTBEAT_SECONDS = 15`）。对应配置在 `deploy/nginx/sse_proxy.conf`，并应用到：

- `POST /api/v1/chat/stream`
- `GET /api/v1/runs/{run_id}/events`

这三处必须同时成立：

1. **关闭缓冲**：`proxy_buffering off`、`proxy_cache off`、`gzip off`，避免事件在代理里攒完再发给客户端。
2. **长超时**：流式路径的 `proxy_read_timeout` 与 `proxy_send_timeout` 均为 60 秒，等于 4 个心跳周期，且至少是心跳间隔的两倍。心跳会刷新空闲计时，因此单次对话可以超过 60 秒而不被断开。
3. **与心跳对齐**：超时不能写成 15 秒或更短；否则一次心跳抖动就会被代理断开。普通非流式路径的超时是 3600 秒，不按心跳计算。

断线后续订需要客户端再次请求 `GET /api/v1/runs/{run_id}/events`，并带上 `Last-Event-ID`。代理会把该请求头转给应用，不把订阅断开当作取消运行。

## 安全响应头

所有经 HTTPS 代理的响应都带有：

- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `X-Content-Type-Options: nosniff`
- `Content-Security-Policy`（同源默认策略，允许同源 SSE/`connect-src 'self'`）

这些头由代理统一添加，不依赖上游是否已经设置。

## 验证步骤

先做不启动业务容器的静态检查：

```sh
python scripts/check_reverse_proxy.py static
```

再做隔离演练。该演练使用仓库内的代理配置、一次性自签证书和模拟 SSE 上游，不读取工作区 `.env`，不启动生产编排，也不访问真实模型：

```sh
python scripts/check_reverse_proxy.py
```

演练会确认：

- `curl` 风格的流式读取可持续收到事件，且首个事件与后续事件不是一次性到达（缓冲已关闭）
- 空闲 20 秒后仍能收到心跳（超时大于心跳间隔）
- `Last-Event-ID` 会传到上游
- HTTPS 响应含 HSTS、`X-Content-Type-Options` 和 CSP
- HTTP 会跳转到 HTTPS

在已获授权、并且生产或隔离业务栈已经启动的前提下，经代理发起一次真实流式对话可以用：

```sh
curl -N --http1.1 \
  --cacert /secure/tls.crt \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message":"ping"}' \
  https://<public-host>/api/v1/chat/stream
```

若要验证续订，先记录响应里的 `X-Run-ID` 和事件 `id`，再请求：

```sh
curl -N --http1.1 \
  --cacert /secure/tls.crt \
  -H "Authorization: Bearer <token>" \
  -H "Last-Event-ID: <event-id>" \
  https://<public-host>/api/v1/runs/<run-id>/events
```

不要把令牌或证书内容写进日志。隔离演练通过只证明代理配置本身正确，不能代替一次经授权的真实对话验收。

2026-11-14 在 Docker 28.5.1 上执行 `python scripts/check_reverse_proxy.py drill`，退出码 0。演练使用一次性自签证书和模拟 SSE 上游，确认流式事件经代理持续到达、空闲约 20 秒仍有心跳、HTTPS 响应带有 HSTS、`X-Content-Type-Options` 和 CSP，并且 HTTP 跳转到 HTTPS。没有启动生产编排，也没有读取工作区 `.env`。

## 回滚

去掉覆盖文件即可回到只发布应用 loopback 端口的编排：

```sh
docker compose --env-file /secure/assistant.prod.env \
  -p assistant-stage \
  -f docker-compose.prod.yml \
  up -d --build --wait --wait-timeout 600
```

回滚前先确认没有客户端只依赖 443 入口。不要使用 `down -v`。