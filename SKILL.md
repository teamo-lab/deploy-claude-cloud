---
name: deploy-claude-cloud
description: |
  一键在腾讯云硅谷区部署 Claude Code 云端实例，并包装为 API 服务。
  Triggers on: "部署Claude", "deploy claude", "云端Claude", "Claude云服务", "claude cloud", "部署云端实例".
  首次使用时需要提供腾讯云 SecretId/SecretKey。
metadata:
  openclaw:
    emoji: "☁️"
---

# 一键部署 Claude Code 云端实例

在腾讯云硅谷区（na-siliconvalley）部署一台 Claude Code 实例，自动配置 OAuth 登录，并包装为可接收 HTTP 请求的 API 服务。

**整个过程需要用户参与 1 次：完成 Claude OAuth 授权。**

## 前置条件

- 腾讯云账号（有 Lighthouse 实例配额）
- Claude Max/Pro 订阅（用于 OAuth 登录）
- GitHub Personal Access Token（repo 权限，用于推送生成的 skills）

## 第零步：获取凭证

检查本地文件 `~/.claude/tencent_cloud_credentials.json` 是否存在。

如果不存在，向用户索要以下信息并保存：

```json
{
  "secretId": "<用户提供的腾讯云 SecretId，格式 AKID...>",
  "secretKey": "<用户提供的腾讯云 SecretKey>"
}
```

另外向用户索要 GitHub Personal Access Token（需要 repo 权限），保存到同一文件：

```json
{
  "secretId": "...",
  "secretKey": "...",
  "githubToken": "<用户提供的 GitHub PAT>"
}
```

后续所有 API 调用从此文件读取凭证。**不要将凭证硬编码或提交到代码仓库。**

---

## 第一步：查找或创建实例

### 1.1 腾讯云 API 签名方法（TC3-HMAC-SHA256）

所有腾讯云 API 调用都需要签名。用 Python 实现签名并发送请求：

```python
import hashlib, hmac, json, time, urllib.request
from datetime import datetime, timezone

def tc_api(secret_id, secret_key, service, action, version, region, payload):
    host = f"{service}.tencentcloudapi.com"
    ts = int(time.time())
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    body = json.dumps(payload, separators=(",", ":"))
    hp = hashlib.sha256(body.encode()).hexdigest()
    canonical = f"POST\n/\n\ncontent-type:application/json; charset=utf-8\nhost:{host}\n\ncontent-type;host\n{hp}"
    hc = hashlib.sha256(canonical.encode()).hexdigest()
    scope = f"{date}/{service}/tc3_request"
    sts = f"TC3-HMAC-SHA256\n{ts}\n{scope}\n{hc}"
    def s(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
    sig = hmac.new(s(s(s(f"TC3{secret_key}".encode(), date), service), "tc3_request"),
                   sts.encode(), hashlib.sha256).hexdigest()
    auth = f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders=content-type;host, Signature={sig}"
    req = urllib.request.Request(f"https://{host}", data=body.encode(), headers={
        "Content-Type": "application/json; charset=utf-8", "Host": host,
        "Authorization": auth, "X-TC-Action": action, "X-TC-Version": version,
        "X-TC-Timestamp": str(ts), "X-TC-Region": region})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())
```

### 1.2 查询现有实例

调用 `lighthouse.DescribeInstances`（Region=na-siliconvalley）。

选择策略：
- 优先复用名称包含 `claude` 的 RUNNING 实例
- 否则选择任意空闲实例
- 记录 **实例 ID** 和 **公网 IP**

### 1.3 创建新实例（如果需要）

调用 `lighthouse.CreateInstances`：
- BundleId: `bundle_rs_nmc_lin_med2_01`（2C2G 40GB $40/mo）
- BlueprintId: `lhbp-1l4ptuvm`（Ubuntu 24.04）
- InstanceChargePrepaid.Period: 1

等待实例 RUNNING（每 10 秒查询，最多 3 分钟）。

---

## 第二步：通过 TAT 安装 Claude Code

腾讯云 TAT 可以远程执行命令（无需 SSH）。

### 2.1 TAT 执行命令

调用 `tat.RunCommand`：
- Content: base64 编码的 shell 命令
- CommandType: SHELL
- InstanceIds: [实例ID]

查询结果用 `tat.DescribeInvocationTasks`（Filter: invocation-id）。

### 2.2 安装 Node.js 22 + Claude Code

通过 TAT 执行：

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm install 22 && nvm use 22 && nvm alias default 22
npm install -g @anthropic-ai/claude-code
claude --version
```

验证输出包含版本号。

---

## 第三步：Claude Code OAuth 登录（需要用户参与）

### 3.1 在服务器上生成 PKCE 和授权 URL

通过 TAT 执行 Python 脚本：

```python
import hashlib, base64, secrets, json, urllib.parse

code_verifier = secrets.token_urlsafe(64)[:128]
challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).rstrip(b"=").decode()
state = secrets.token_urlsafe(32)

with open("/tmp/oauth_pkce.json", "w") as f:
    json.dump({"code_verifier": code_verifier, "state": state}, f)

params = urllib.parse.urlencode({
    "code": "true",
    "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    "response_type": "code",
    "redirect_uri": "https://platform.claude.com/oauth/code/callback",
    "scope": "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers",
    "code_challenge": challenge,
    "code_challenge_method": "S256",
    "state": state,
})
print("https://claude.ai/oauth/authorize?" + params)
```

同时在服务器创建即时交换脚本 `/tmp/oauth_exchange.py`（接受 code 参数，立即交换 token）。

### 3.2 让用户授权

将授权 URL 展示给用户。用户在浏览器中打开、登录 Claude 账号、点击授权。

回调页面会显示 Authentication Code（格式 `<code>#<state>`），让用户把整个字符串发回来。

### 3.3 立即交换 Token

拿到用户的 code 后，**立即**通过 TAT 执行交换脚本。

Token endpoint: `https://platform.claude.com/v1/oauth/token`

请求体（JSON）:
```json
{
  "grant_type": "authorization_code",
  "code": "<去掉#后面state的部分>",
  "redirect_uri": "https://platform.claude.com/oauth/code/callback",
  "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
  "code_verifier": "<PKCE code_verifier>",
  "state": "<PKCE state>"
}
```

**关键：code 有效期很短（约 1 分钟），必须在用户提供后立即交换。**

### 3.4 保存凭证到 Claude Code

将返回的 token 写入 `~/.claude/.credentials.json`：

```json
{
  "claudeAiOauth": {
    "accessToken": "<access_token>",
    "refreshToken": "<refresh_token>",
    "expiresAt": "<当前毫秒时间戳 + expires_in * 1000>",
    "scopes": ["org:create_api_key", "user:profile", "user:inference", "user:sessions:claude_code", "user:mcp_servers"]
  }
}
```

### 3.5 验证登录

```bash
claude auth status    # loggedIn: true
claude -p "Say hi" --model sonnet  # 应返回正常回复
```

---

## 第四步：部署 API 服务

### 4.1 安装 Python 依赖

```bash
dnf install -y python3-pip || apt-get install -y python3-pip
pip3 install fastapi uvicorn
```

### 4.2 安装 GitHub CLI

```bash
curl -sL https://github.com/cli/cli/releases/download/v2.67.0/gh_2.67.0_linux_amd64.tar.gz \
  | tar xz -C /tmp && cp /tmp/gh_2.67.0_linux_amd64/bin/gh /usr/local/bin/
```

### 4.3 部署 API 代码

创建 `/opt/clawschool-api/main.py`，包含：
- `POST /api/generate-skills` — 接收诊断数据，调用 `claude -p` 生成 skills，通过 `gh api` 推到 GitHub
- `GET /health` — 健康检查

API 通过 `asyncio.create_subprocess_exec` 调用 `claude -p`（非交互模式），安全地传递参数。

### 4.4 创建 systemd service

写入 `/etc/systemd/system/clawschool-api.service`，关键环境变量：
- HOME=/root
- PATH 包含 nvm 的 Node.js 路径
- GH_TOKEN=用户提供的 GitHub PAT

```bash
systemctl daemon-reload
systemctl enable clawschool-api
systemctl start clawschool-api
```

### 4.5 开放防火墙

调用 `lighthouse.CreateFirewallRules` 开放端口 8900。

---

## 第五步：验证部署

```bash
curl http://<实例IP>:8900/health
```

向用户展示：

| 项目 | 值 |
|------|-----|
| 实例 IP | `<公网IP>` |
| API 端点 | `http://<IP>:8900/api/generate-skills` |
| Claude Code 版本 | X.Y.Z |
| 认证状态 | 已登录 |
| 服务状态 | active (running) |

---

## 故障排查

| 问题 | 解决方案 |
|------|----------|
| 实例配额不足 | 复用现有实例，或到腾讯云控制台申请配额 |
| TAT 命令超时 | 增加 Timeout，或拆分为多个命令 |
| OAuth code 过期 | 重新生成 PKCE + 授权 URL，让用户重新授权 |
| Token 交换 invalid_grant | code 已过期或已使用，需重新授权 |
| Cloudflare 拦截 claude.ai | 用 `platform.claude.com/v1/oauth/token` 做 token 交换 |
| pip3 找不到 | `dnf install -y python3-pip` |
| claude -p 报错 | 检查 `claude auth status`，确认 credentials.json 格式正确 |
