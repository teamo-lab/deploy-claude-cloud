# deploy-claude-cloud

Claude Code API 服务 + OpenClaw Skill（用于在腾讯云硅谷区部署）。

## 本地运行 API

```bash
cd ~/deploy-claude-cloud
python3 -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8900 --reload
```

健康检查：

```bash
curl http://127.0.0.1:8900/health
```

## 与 clawschool 联调

先在 clawschool 服务侧设置：

```bash
export CLAUDE_API_URL=http://127.0.0.1:8900
```

然后调用：

```bash
GET /api/test/diagnose?token=<token>&scope=basic
```

预期返回 `generatedSkills`（非空数组）。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 服务器部署（systemd）

**重要：Claude Code 的 `--dangerously-skip-permissions` 不能以 root 运行。** 必须创建非 root 用户。

### 1. 创建服务用户

```bash
useradd -m -s /bin/bash clawapi
```

### 2. 安装 Claude Code 并登录（以 clawapi 用户）

```bash
su - clawapi
# 安装 claude（需要 Node.js 22+）
npm install -g @anthropic-ai/claude-code
# 登录（OAuth Max 订阅）
claude
# 验证
claude -p "say hi" --model sonnet
```

### 3. 部署代码

```bash
cp -r /path/to/deploy-claude-cloud /opt/clawschool-api
pip3 install -r /opt/clawschool-api/requirements.txt
```

### 4. systemd 服务

```ini
# /etc/systemd/system/clawschool-api.service
[Unit]
Description=ClawSchool Claude API
After=network.target

[Service]
Type=simple
User=clawapi
WorkingDirectory=/opt/clawschool-api
Environment=HOME=/home/clawapi
Environment=PATH=/home/clawapi/.nvm/versions/node/v22.22.0/bin:/usr/local/bin:/usr/bin:/bin
Environment=GH_TOKEN=<your-github-token>
Environment=GITHUB_TOKEN=<your-github-token>
Environment=GITHUB_REPO=teamo-lab/clawschool
ExecStart=/usr/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8900
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now clawschool-api
```

### 5. 注意事项

- Claude Code OAuth 令牌会过期，需定期 `su - clawapi && claude -p "say hi"` 刷新
- 凭证文件位置：`/home/clawapi/.claude/.credentials.json`
- 不要在 subprocess 环境变量中硬编码 `HOME=/root`，否则 clawapi 用户找不到凭证
