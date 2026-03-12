# Claude Code API 测试说明

## 覆盖范围

- `GET /health`
  - 服务存活
  - Claude 登录状态探针
- `POST /api/generate-skills`
  - Claude 成功返回结构化 skills
  - Claude 异常时 fallback 仍返回非空 skills
  - 生成的 skill 文件落盘到 `GENERATED_SKILLS_DIR/<token>/`

## 运行方式

```bash
python3 -m unittest discover -s tests -v
```

## 集成联调建议

1. 先启动本服务（默认 `127.0.0.1:8900`）。
2. 启动 clawschool 时设置：

```bash
export CLAUDE_API_URL=http://127.0.0.1:8900
```

3. 在 clawschool 侧调用：

```bash
GET /api/test/diagnose?token=<token>&scope=basic
```

预期：

- 响应中包含 `generatedSkills`
- `generatedSkills` 至少 1 个元素
- 每个元素包含 `name` 和 `url`

