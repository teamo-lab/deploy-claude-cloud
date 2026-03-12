from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import FastAPI
from pydantic import BaseModel, Field

logger = logging.getLogger("deploy_claude_cloud")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


@dataclass
class RuntimeConfig:
    claude_bin: str
    claude_model: str
    claude_timeout_seconds: int
    generated_skills_dir: Path
    github_repo: str
    github_branch: str
    github_prefix: str
    github_token: str


def _load_config() -> RuntimeConfig:
    timeout_raw = os.environ.get("CLAUDE_TIMEOUT_SECONDS", "180")
    try:
        timeout_seconds = max(30, int(timeout_raw))
    except ValueError:
        timeout_seconds = 180

    return RuntimeConfig(
        claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
        claude_model=os.environ.get("CLAUDE_MODEL", "sonnet"),
        claude_timeout_seconds=timeout_seconds,
        generated_skills_dir=Path(os.environ.get("GENERATED_SKILLS_DIR", "/tmp/generated-skills")),
        github_repo=os.environ.get("GITHUB_REPO", "teamo-lab/clawschool"),
        github_branch=os.environ.get("GITHUB_BRANCH", "main"),
        github_prefix=os.environ.get("GITHUB_SKILLS_PREFIX", "generated-skills"),
        github_token=os.environ.get("GITHUB_TOKEN", ""),
    )


CONFIG = _load_config()


class QuestionDetail(BaseModel):
    questionId: str
    title: str = ""
    category: str = ""
    instructions: str = ""
    evidenceFormat: Dict[str, Any] = Field(default_factory=dict)
    agentEvidence: Dict[str, Any] = Field(default_factory=dict)
    score: float = 0
    maxScore: float = 10
    reason: str = ""


class DiagnosisPayload(BaseModel):
    token: str
    lobsterName: str = ""
    model: str = ""
    score: float = 0
    iq: float = 0
    title: str = ""
    rank: int = 0
    scope: str = "full"
    questionDetails: List[QuestionDetail] = Field(default_factory=list)


class GenerateSkillsRequest(BaseModel):
    token: str
    diagnosis: DiagnosisPayload


class GeneratedSkill(BaseModel):
    name: str
    url: str
    summary: str = ""


class GenerateSkillsResponse(BaseModel):
    skills: List[GeneratedSkill]
    source: str


app = FastAPI(title="Claude Code API", version="2026.03.12")


def _slugify(name: str) -> str:
    normalized = name.strip().lower()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", normalized)
    normalized = normalized.strip("-")
    return normalized or "skill"


def _raw_url_for_path(config: RuntimeConfig, repo_path: str) -> str:
    return f"https://raw.githubusercontent.com/{config.github_repo}/{config.github_branch}/{repo_path}"


def _build_claude_prompt(diagnosis: DiagnosisPayload) -> str:
    weak_questions = []
    for q in diagnosis.questionDetails:
        gap = (q.maxScore or 10) - (q.score or 0)
        weak_questions.append(
            {
                "questionId": q.questionId,
                "title": q.title,
                "category": q.category,
                "score": q.score,
                "maxScore": q.maxScore,
                "gap": gap,
                "reason": q.reason,
            }
        )
    weak_questions.sort(key=lambda item: item["gap"], reverse=True)
    weak_questions = weak_questions[:6]

    return (
        "你是资深的 OpenClaw 能力诊断工程师。\n"
        "请根据诊断结果生成 1-3 个可直接落地的 Skill 文档。\n"
        "每个 skill 需要包含：\n"
        "1) 可读的英文 slug 名称（短横线命名）\n"
        "2) 一句话 summary\n"
        "3) 完整 Markdown 内容，包含目标、执行步骤、验收标准\n"
        "输出必须满足 JSON Schema，不要输出 schema 外字段。\n\n"
        f"诊断 token: {diagnosis.token}\n"
        f"lobsterName: {diagnosis.lobsterName}\n"
        f"scope: {diagnosis.scope}\n"
        f"总分/智力: {diagnosis.score}/{diagnosis.iq}\n"
        f"弱项明细(按 gap 排序):\n{json.dumps(weak_questions, ensure_ascii=False, indent=2)}\n"
    )


CLAUDE_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "summary": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["name", "summary", "content"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["skills"],
        "additionalProperties": False,
    },
    ensure_ascii=False,
)


def _call_claude_for_skills(diagnosis: DiagnosisPayload, config: RuntimeConfig) -> List[Dict[str, str]]:
    prompt = _build_claude_prompt(diagnosis)
    cmd = [
        config.claude_bin,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--json-schema",
        CLAUDE_SCHEMA,
    ]
    if config.claude_model:
        cmd.extend(["--model", config.claude_model])

    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=config.claude_timeout_seconds,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"claude exited with {completed.returncode}")

    try:
        payload = json.loads(completed.stdout)
        structured = payload.get("structured_output") or {}
        skills = structured.get("skills") or []
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid claude output: {exc}") from exc

    normalized: List[Dict[str, str]] = []
    for skill in skills[:3]:
        name = str(skill.get("name", "")).strip()
        summary = str(skill.get("summary", "")).strip()
        content = str(skill.get("content", "")).strip()
        if not name or not content:
            continue
        if not summary:
            summary = "由 Claude Code 自动生成"
        normalized.append({"name": name, "summary": summary, "content": content})

    if not normalized:
        raise RuntimeError("claude returned empty skill set")

    return normalized


QUESTION_SKILL_MAP: Dict[str, Dict[str, str]] = {
    "q1": {"name": "prompt-injection-guard", "summary": "强化提示词注入识别和拒绝策略"},
    "q2": {"name": "dangerous-action-confirm", "summary": "高风险操作二次确认和审计记录"},
    "q3": {"name": "skill-source-audit", "summary": "安装前做来源可信度与权限审查"},
    "q4": {"name": "preinstall-plan-check", "summary": "安装前先完成依赖与方案检查"},
    "q5": {"name": "self-diagnose-basics", "summary": "自动化自诊断，先查目录再给结论"},
    "q6": {"name": "evidence-summary-builder", "summary": "证据归档 + 结构化总结输出"},
    "q7": {"name": "active-execution-loop", "summary": "主动执行闭环和失败重试策略"},
    "q8": {"name": "skill-safety-review", "summary": "新技能安全审查与沙箱隔离"},
    "q9": {"name": "task-scheduler-hardening", "summary": "定时任务定义、回滚与监控"},
    "q10": {"name": "daily-news-quality-gate", "summary": "新闻去重、时效验证和引用规范"},
    "q11": {"name": "parallel-workflow-control", "summary": "并行任务拆分与冲突治理"},
    "q12": {"name": "web-resilience-playbook", "summary": "网页访问失败自动降级与替代路径"},
}


def _fallback_skill_content(skill_name: str, summary: str, diagnosis: DiagnosisPayload, qid: str) -> str:
    return (
        f"# {skill_name}\n\n"
        f"## 目标\n"
        f"- {summary}\n"
        f"- 针对 {diagnosis.lobsterName or '当前 bot'} 在 `{qid}` 维度的短板进行修复\n\n"
        f"## 执行步骤\n"
        f"1. 读取最近一次诊断结果，定位 `{qid}` 的失败证据。\n"
        f"2. 按照问题分类创建最小可复现用例并执行。\n"
        f"3. 给出修复方案并自动重测相关题目。\n"
        f"4. 输出修复前后对比结果和风险提示。\n\n"
        f"## 验收标准\n"
        f"- 相关用例通过率提升\n"
        f"- 产出可复制的修复记录（命令/日志/结论）\n"
        f"- 出现失败时给出下一步操作建议\n"
    )


def _fallback_skills(diagnosis: DiagnosisPayload) -> List[Dict[str, str]]:
    candidates: List[Dict[str, Any]] = []
    for q in diagnosis.questionDetails:
        gap = (q.maxScore or 10) - (q.score or 0)
        if gap <= 0:
            continue
        mapped = QUESTION_SKILL_MAP.get(q.questionId)
        if not mapped:
            continue
        candidates.append(
            {
                "questionId": q.questionId,
                "gap": gap,
                "name": mapped["name"],
                "summary": mapped["summary"],
            }
        )

    candidates.sort(key=lambda item: item["gap"], reverse=True)
    if not candidates:
        candidates = [
            {
                "questionId": "general",
                "gap": 1,
                "name": "general-reliability-hardening",
                "summary": "通用可靠性加固与回归测试",
            }
        ]

    skills: List[Dict[str, str]] = []
    for item in candidates[:3]:
        skills.append(
            {
                "name": item["name"],
                "summary": item["summary"],
                "content": _fallback_skill_content(
                    item["name"],
                    item["summary"],
                    diagnosis,
                    item["questionId"],
                ),
            }
        )
    return skills


def _github_api_request(config: RuntimeConfig, method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
    req = Request(url=url, data=body, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {config.github_token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode())


def _upload_file_to_github(config: RuntimeConfig, repo_path: str, content: str, message: str) -> bool:
    if not config.github_token:
        return False

    encoded_path = quote(repo_path)
    endpoint = f"https://api.github.com/repos/{config.github_repo}/contents/{encoded_path}"

    sha: Optional[str] = None
    try:
        existing = _github_api_request(config, "GET", f"{endpoint}?ref={quote(config.github_branch)}")
        sha = existing.get("sha")
    except Exception:
        sha = None

    payload: Dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": config.github_branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        _github_api_request(config, "PUT", endpoint, payload)
        return True
    except Exception as exc:
        logger.warning("github upload failed for %s: %s", repo_path, exc)
        return False


def _persist_skills(token: str, diagnosis: DiagnosisPayload, skills: List[Dict[str, str]], source: str, config: RuntimeConfig) -> List[GeneratedSkill]:
    token_path = _slugify(token) or "token"
    output_dir = config.generated_skills_dir / token_path
    output_dir.mkdir(parents=True, exist_ok=True)

    generated: List[GeneratedSkill] = []
    used_names: Dict[str, int] = {}

    for skill in skills:
        base_name = _slugify(skill["name"])
        idx = used_names.get(base_name, 0)
        used_names[base_name] = idx + 1
        file_name = f"{base_name}.md" if idx == 0 else f"{base_name}-{idx + 1}.md"

        local_path = output_dir / file_name
        local_path.write_text(skill["content"], encoding="utf-8")

        repo_path = f"{config.github_prefix}/{token_path}/{file_name}"
        commit_msg = f"chore(skills): update {token_path}/{file_name} via {source}"
        _upload_file_to_github(config, repo_path, skill["content"], commit_msg)

        generated.append(
            GeneratedSkill(
                name=skill["name"],
                summary=skill.get("summary", ""),
                url=_raw_url_for_path(config, repo_path),
            )
        )

    if not generated:
        fallback_name = "general-reliability-hardening"
        file_name = f"{fallback_name}.md"
        content = _fallback_skill_content(fallback_name, "通用可靠性加固与回归测试", diagnosis, "general")
        local_path = output_dir / file_name
        local_path.write_text(content, encoding="utf-8")
        repo_path = f"{config.github_prefix}/{token_path}/{file_name}"
        generated.append(
            GeneratedSkill(
                name=fallback_name,
                summary="通用可靠性加固与回归测试",
                url=_raw_url_for_path(config, repo_path),
            )
        )

    return generated


def _claude_auth_status(config: RuntimeConfig) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            [config.claude_bin, "auth", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if completed.returncode != 0:
            return {"ok": False, "error": completed.stderr.strip() or "auth status failed"}
        return {"ok": True, "raw": json.loads(completed.stdout)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "claude-code-api",
        "claudeAuth": _claude_auth_status(CONFIG),
        "githubRepo": CONFIG.github_repo,
        "githubBranch": CONFIG.github_branch,
    }


@app.post("/api/generate-skills", response_model=GenerateSkillsResponse)
def generate_skills(request: GenerateSkillsRequest) -> GenerateSkillsResponse:
    diagnosis = request.diagnosis
    token = request.token.strip() or diagnosis.token.strip()
    if not token:
        token = "anonymous"

    try:
        raw_skills = _call_claude_for_skills(diagnosis, CONFIG)
        source = "claude"
    except Exception as exc:
        logger.warning("claude generation failed, fallback enabled: %s", exc)
        raw_skills = _fallback_skills(diagnosis)
        source = "fallback"

    skills = _persist_skills(token, diagnosis, raw_skills, source, CONFIG)
    return GenerateSkillsResponse(skills=skills, source=source)

