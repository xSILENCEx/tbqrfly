from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path

from ..config import RuntimeConfig, render_placeholders
from .project_info import ProjectInfo


def _load_history(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(k): v for k, v in payload.items() if isinstance(v, str)}


def save_history(path: Path, commit: str, summary: str) -> None:
    history = _load_history(path)
    history[commit] = summary
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _git_log(project_root: Path, previous: str | None, current: str) -> str:
    args = [
        "git",
        "log",
        "--reverse",
        "--date=short",
        "--format=提交: %H%n作者: %an%n日期: %ad%n标题: %s%n说明: %b",
        "--stat",
        "--no-color",
    ]
    args.extend([f"{previous}..{current}"] if previous else ["-1", current])
    output = subprocess.check_output(
        args, cwd=project_root, text=True, stderr=subprocess.STDOUT
    ).strip()
    return output or subprocess.check_output(
        ["git", "show", "--stat", "--no-color", current],
        cwd=project_root,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def _git_subject_fallback(project_root: Path, previous: str | None, current: str) -> str:
    args = ["git", "log", "--reverse", "--format=- %s"]
    args.extend([f"{previous}..{current}"] if previous else ["-1", current])
    try:
        subjects = subprocess.check_output(
            args, cwd=project_root, text=True, stderr=subprocess.STDOUT
        ).strip()
    except subprocess.CalledProcessError:
        subjects = f"- 当前提交 {current[:12]}"
    return f"LLM 不可用，本次提交记录：\n{subjects[:2000]}"


def generate_summary(config: RuntimeConfig, info: ProjectInfo) -> str:
    history = _load_history(config.history_path)
    current = info.commit
    cached = history.get(current, "").strip()
    if cached:
        print("使用 commit_history 中的缓存摘要")
        return cached

    recorded = list(history)
    previous = recorded[-1] if recorded else None
    if previous == current:
        previous = recorded[-2] if len(recorded) > 1 else None

    try:
        git_log = _git_log(config.project_root, previous, current)
    except subprocess.CalledProcessError as exc:
        return f"无法读取 git 日志: {exc}"

    range_text = (
        f"从上次发布提交 {previous}（不包含）到当前提交 {current}（包含）"
        if previous
        else f"首次记录，仅总结当前提交 {current}"
    )

    if not config.llm.get("enabled", True):
        print("LLM 已禁用，使用 git 提交日志作为 summary")
        return _git_subject_fallback(config.project_root, previous, current)

    command_raw = str(config.llm.get("command") or "claude -p").strip()
    command = shlex.split(command_raw)
    if not command or not shutil.which(command[0]):
        print(f"警告: LLM 命令不可用（{command_raw}），回退 git 提交日志")
        return _git_subject_fallback(config.project_root, previous, current)

    prompt = render_placeholders(
        config.prompt_text or "{git_log}",
        {
            "git_log": git_log[:60000],
            "range_text": range_text,
            "commit": info.commit_short,
        },
    )
    try:
        completed = subprocess.run(
            command,
            cwd=config.project_root,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise RuntimeError(completed.stderr.strip() or "LLM 未返回内容")
        summary = completed.stdout.strip()
        print("LLM 摘要生成成功")
        return summary
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"警告: LLM 失败（{exc}），回退 git 提交日志")
        return _git_subject_fallback(config.project_root, previous, current)
