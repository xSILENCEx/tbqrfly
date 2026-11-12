from __future__ import annotations

import select
import sys
from pathlib import Path

from ..config import (
    ConfigError,
    RuntimeConfig,
    load_runtime_config,
    validate_for_run,
)
from ..doctor import collect_checks, has_fatal, print_checks
from .artifacts import find_after_build, find_reusable
from .build import run_build
from .fork import load_shared_state, spawn_platform_terminal, write_shared_state
from .llm import generate_summary, save_history
from .notify import upload_and_notify
from .project_info import ProjectInfo, load_project_info


def _prompt_choice(
    label: str,
    options: list[str],
    *,
    default: str,
    timeout: int,
) -> str:
    if len(options) == 1:
        print(f"{label}: 仅有 {options[0]}，自动选择")
        return options[0]
    joined = "/".join(options)
    print(f"{label} [{joined}]（{timeout}s 后自动选择 {default}）: ", end="", flush=True)
    if not sys.stdin.isatty():
        print()
        return default
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        print()
        return default
    value = sys.stdin.readline().strip() or default
    return value


def _ask_reuse(platform: str, timeout: int) -> bool:
    print(
        f"发现已有 {platform} 产物，是否复用？[y/N]（{timeout}s 后重新构建）: ",
        end="",
        flush=True,
    )
    if not sys.stdin.isatty():
        print()
        return False
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        print()
        return False
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def select_env_and_platforms(
    config: RuntimeConfig,
    *,
    env_arg: str | None,
    platform_arg: str | None,
) -> tuple[str, list[str]]:
    envs = config.env_names()
    if not envs:
        raise ConfigError("builds.json 中没有环境")
    default_env = config.default_env if config.default_env in envs else envs[0]
    env = env_arg or _prompt_choice(
        "选择环境",
        envs,
        default=default_env,
        timeout=config.wait_timeout,
    )
    if env not in envs:
        raise ConfigError(f"无效环境: {env}，可选: {', '.join(envs)}")

    platforms = config.platforms_for(env)
    if not platforms:
        raise ConfigError(f"环境 {env} 下没有平台配置")
    if platform_arg:
        if platform_arg == "all":
            selected = platforms
        elif platform_arg in platforms:
            selected = [platform_arg]
        else:
            raise ConfigError(
                f"平台 {platform_arg} 不在 builds.{env} 中: {', '.join(platforms)}"
            )
    else:
        choices = [*platforms, "all"]
        picked = _prompt_choice(
            "选择平台",
            choices,
            default="all",
            timeout=config.wait_timeout,
        )
        if picked == "all":
            selected = platforms
        elif picked in platforms:
            selected = [picked]
        else:
            raise ConfigError(f"无效平台: {picked}")
    return env, selected


def run_platform_pipeline(
    config: RuntimeConfig,
    *,
    env: str,
    platform: str,
    info: ProjectInfo,
    summary: str,
) -> None:
    print(f"\n===== 平台 {platform} =====")
    existing = find_reusable(config, platform)
    reuse = bool(existing) and _ask_reuse(platform, config.wait_timeout)
    if reuse and existing is not None:
        artifact = existing
        print(f"  复用产物: {artifact}")
    else:
        run_build(config, env, platform, info)
        artifact = find_after_build(config, platform)
        if artifact is None:
            raise RuntimeError(f"构建完成但未找到 {platform} 产物")
        print(f"  产物: {artifact} ({artifact.stat().st_size / 1024 / 1024:.2f} MB)")

    try:
        upload_and_notify(
            config,
            env=env,
            platform=platform,
            artifact=artifact,
            info=info,
            summary=summary,
        )
    except Exception:
        # Feishu/Zealot failure should fail the run per plan.
        raise


def run_workflow(
    project_root: Path,
    *,
    env: str | None = None,
    platform: str | None = None,
    shared_state: Path | None = None,
    debug: bool = False,
) -> int:
    del debug  # reserved
    checks = collect_checks(project_root=project_root, include_llm=False)
    fatal_only = [item for item in checks if item.level == "fail"]
    if fatal_only:
        print("===== 环境检查失败 =====")
        print_checks(fatal_only)
        return 2

    config = load_runtime_config(project_root)

    if shared_state is not None:
        state = load_shared_state(shared_state)
        env_name = str(state["env"])
        summary = str(state["summary"])
        raw_info = state["info"]
        info = ProjectInfo(**raw_info)
        if not platform:
            raise ConfigError("--shared-state 需要同时指定 --platform")
        validate_for_run(config, env_name, [platform])
        run_platform_pipeline(
            config, env=env_name, platform=platform, info=info, summary=summary
        )
        return 0

    env_name, platforms = select_env_and_platforms(
        config, env_arg=env, platform_arg=platform
    )
    validate_for_run(config, env_name, platforms)

    print("\n===== 共享阶段 =====")
    info = load_project_info(project_root, config.app_name)
    print(f"应用: {info.app_name}  版本: {info.version}+{info.build_number}")
    print(f"提交: {info.commit_short}  分支: {info.branch}")
    summary = generate_summary(config, info)
    print(f"摘要预览:\n{summary[:500]}{'...' if len(summary) > 500 else ''}")

    shared = write_shared_state(
        project_root,
        env=env_name,
        summary=summary,
        info={
            "app_name": info.app_name,
            "version": info.version,
            "build_number": info.build_number,
            "commit": info.commit,
            "commit_short": info.commit_short,
            "branch": info.branch,
            "build_date": info.build_date,
        },
    )

    # Prefer keeping iOS on the current terminal when present; fork the rest.
    if "ios" in platforms:
        primary = "ios"
        others = [item for item in platforms if item != "ios"]
    else:
        primary, *others = platforms

    for other in others:
        spawned = spawn_platform_terminal(
            project_root,
            env=env_name,
            platform=other,
            shared_state=shared,
        )
        if not spawned:
            print(f"改为当前终端串行执行: {other}")
            run_platform_pipeline(
                config, env=env_name, platform=other, info=info, summary=summary
            )

    run_platform_pipeline(
        config, env=env_name, platform=primary, info=info, summary=summary
    )

    # Only record history from the primary/shared process.
    try:
        save_history(config.history_path, info.commit, summary)
    except OSError as exc:
        print(f"警告: 写入 commit_history 失败: {exc}")

    print("\n===== 当前终端流程结束 =====")
    if others:
        print("其他平台可能仍在独立终端中运行，请查看对应窗口。")
    return 0
