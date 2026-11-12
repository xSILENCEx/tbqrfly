from __future__ import annotations

import json
from pathlib import Path

TBQRFLY_TASK_LABEL = "tbqrfly: run"

TBQRFLY_TASK = {
    "label": TBQRFLY_TASK_LABEL,
    "type": "shell",
    "command": "tbqrfly run",
    "problemMatcher": [],
    "presentation": {
        "reveal": "always",
        "panel": "shared",
        "focus": True,
    },
    "group": "build",
}


def ensure_vscode_tbqrfly_task(project_root: Path) -> str:
    """Create or append tbqrfly run task in .vscode/tasks.json. Returns status text."""
    vscode_dir = project_root / ".vscode"
    tasks_path = vscode_dir / "tasks.json"
    vscode_dir.mkdir(parents=True, exist_ok=True)

    if tasks_path.is_file():
        try:
            payload = json.loads(tasks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f".vscode/tasks.json 不是合法 JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(".vscode/tasks.json 根节点必须是对象")
    else:
        payload = {"version": "2.0.0", "tasks": []}

    payload.setdefault("version", "2.0.0")
    tasks = payload.get("tasks")
    if tasks is None:
        tasks = []
        payload["tasks"] = tasks
    if not isinstance(tasks, list):
        raise ValueError(".vscode/tasks.json 的 tasks 必须是数组")

    for item in tasks:
        if isinstance(item, dict) and item.get("label") == TBQRFLY_TASK_LABEL:
            return f"已存在 VS Code 任务: {TBQRFLY_TASK_LABEL}（{tasks_path}）"

    tasks.append(dict(TBQRFLY_TASK))
    tasks_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return f"已写入 VS Code 任务: {TBQRFLY_TASK_LABEL}（{tasks_path}）"
