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
from ..beta_config import load_beta_config, validate_beta
from ..beta_doctor import collect_beta_checks
from ..clients.testflight import prepare_fastlane
from .beta import run_beta_platform, notify_beta
from .release_lock import ios_release_lock
from ..doctor import collect_checks, has_fatal, print_checks
from .artifacts import find_after_build, find_reusable
from .build import run_build
from .fork import (
    cleanup_runtime_temps,
    load_shared_state,
    remove_path_quiet,
    resolve_terminal_mode,
    spawn_platform_inline,
    spawn_platform_terminal,
    wait_inline_process,
    wait_terminal_process,
    write_shared_state,
)
from .llm import generate_summary, save_history
from .notify import notify_failure, upload_and_notify
from .pre_commands import run_commands, run_pre_commands
from .project_info import ProjectInfo, load_project_info


def _classify_step(error: BaseException) -> str:
    text = str(error)
    lowered = text.lower()
    if "前置命令" in text:
        return "pre_commands"
    if "后置命令" in text:
        return "next_commands"
    if "构建失败" in text or "ipa 导出" in lowered:
        return "build"
    if "产物" in text:
        return "artifact"
    if "zealot" in lowered or "channel" in lowered or "上传" in text:
        return "zealot_upload"
    if "飞书" in text or "feishu" in lowered:
        return "feishu_notify"
    return "pipeline"


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
    print(
        f"{label} [{joined}]（{timeout}s 后自动选择 {default}）: ", end="", flush=True
    )
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
    with ios_release_lock(config.project_root, platform):
        _run_platform_pipeline(
            config, env=env, platform=platform, info=info, summary=summary
        )


def _run_platform_pipeline(
    config: RuntimeConfig,
    *,
    env: str,
    platform: str,
    info: ProjectInfo,
    summary: str,
) -> None:
    print(f"\n===== 平台 {platform} =====")
    try:
        block = config.platform_config(env, platform)
        run_commands(
            config.project_root,
            block.get("pre_commands", []),
            label=f"{env}/{platform} 前置命令",
        )
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
            print(
                f"  产物: {artifact} ({artifact.stat().st_size / 1024 / 1024:.2f} MB)"
            )

        upload_and_notify(
            config,
            env=env,
            platform=platform,
            artifact=artifact,
            info=info,
            summary=summary,
        )
        run_commands(
            config.project_root,
            block.get("next_commands", []),
            label=f"{env}/{platform} 后置命令",
        )
    except Exception as exc:
        notify_failure(
            config,
            env=env,
            platform=platform,
            info=info,
            error=str(exc),
            step=_classify_step(exc),
        )
        raise


def run_workflow(
    project_root: Path,
    *,
    env: str | None = None,
    platform: str | None = None,
    shared_state: Path | None = None,
    debug: bool = False,
    target: str = "run",
) -> int:
    del debug  # reserved
    beta = target == "beta"
    if target not in ("run", "beta"):
        raise ConfigError(f"未知发布入口: {target}")
    if beta and env not in (None, "prod"):
        raise ConfigError("Beta 发布固定为 prod")
    checks = (
        [] if beta else collect_checks(project_root=project_root, include_llm=False)
    )
    fatal_only = [item for item in checks if item.level == "fail"]
    if fatal_only:
        print("===== 环境检查失败 =====")
        print_checks(fatal_only)
        return 2

    config = (
        load_beta_config(project_root) if beta else load_runtime_config(project_root)
    )
    validate = validate_beta if beta else validate_for_run
    pipeline = run_beta_platform if beta else run_platform_pipeline

    if shared_state is not None:
        state = load_shared_state(shared_state)
        env_name = str(state["env"])
        summary = str(state["summary"])
        raw_info = state["info"]
        info = ProjectInfo(**raw_info)
        if not platform:
            raise ConfigError("--shared-state 需要同时指定 --platform")
        validate(config, env_name, [platform])
        if beta:
            checks = collect_beta_checks(project_root, [platform])
            if any(item.level == "fail" for item in checks):
                print_checks(checks)
                return 2
        pipeline(config, env=env_name, platform=platform, info=info, summary=summary)
        return 0

    env_name, platforms = select_env_and_platforms(
        config, env_arg="prod" if beta else env, platform_arg=platform
    )
    validate(config, env_name, platforms)
    if beta:
        checks = collect_beta_checks(project_root, platforms)
        if any(item.level == "fail" for item in checks):
            print_checks(checks)
            return 2
        prepare_fastlane(config)

    print("\n===== 共享阶段 =====")
    cleanup_runtime_temps(project_root)
    try:
        run_pre_commands(project_root, config.pre_commands)
    except Exception:
        if beta:
            notify_beta(
                config,
                platform="all",
                info=None,
                message="Beta 全局前置命令失败，未开始发布。",
            )
        raise
    info = load_project_info(project_root, config.app_name)
    print(f"应用: {info.app_name}  版本: {info.version}+{info.build_number}")
    print(f"提交: {info.commit_short}  分支: {info.branch}")
    summary = generate_summary(config, info)
    print(f"摘要预览:\n{summary[:500]}{'...' if len(summary) > 500 else ''}")

    # Prefer keeping iOS on the current terminal when present; fork the rest.
    if "ios" in platforms:
        primary = "ios"
        others = [item for item in platforms if item != "ios"]
    else:
        primary, *others = platforms

    terminal_mode = resolve_terminal_mode(config.terminal)
    print(f"并行终端模式: {terminal_mode}（builds.terminal={config.terminal}）")

    # Shared state is only needed when forking other platforms.
    shared = None
    if others:
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

    inline_jobs: list[tuple[str, object]] = []
    exit_code = 0
    spawned_system = False
    terminal_jobs: list[str] = []

    try:
        for other in others:
            if terminal_mode == "inline":
                process = spawn_platform_inline(
                    project_root,
                    env=env_name,
                    platform=other,
                    shared_state=shared,  # type: ignore[arg-type]
                    command=target,
                )
                inline_jobs.append((other, process))
                continue

            spawned = spawn_platform_terminal(
                project_root,
                env=env_name,
                platform=other,
                shared_state=shared,  # type: ignore[arg-type]
                command=target,
            )
            if spawned:
                spawned_system = True
                terminal_jobs.append(other)
            else:
                print(f"改为当前终端串行执行: {other}")
                try:
                    pipeline(
                        config, env=env_name, platform=other, info=info, summary=summary
                    )
                except Exception as exc:
                    print(f"平台 {other} 失败: {exc}")
                    exit_code = 1

        try:
            pipeline(config, env=env_name, platform=primary, info=info, summary=summary)
        except Exception as exc:
            print(f"平台 {primary} 失败: {exc}")
            exit_code = 1

        for other, process in inline_jobs:
            code = wait_inline_process(other, process)  # type: ignore[arg-type]
            if code != 0:
                exit_code = 1

        if config.next_commands or beta:
            for other in terminal_jobs:
                if wait_terminal_process(shared, other) != 0:
                    exit_code = 1
            if exit_code == 0:
                try:
                    run_commands(
                        project_root, config.next_commands, label="全局后置命令"
                    )
                except Exception as exc:
                    print(f"全局后置命令失败: {exc}")
                    exit_code = 1
                    if beta:
                        notify_beta(
                            config,
                            platform="all",
                            info=info,
                            message="各平台上传已完成，但全局后置命令失败；无需因此重复上传。",
                        )

        # Only record history after all awaited tasks and global hooks succeed.
        if exit_code == 0:
            try:
                save_history(config.history_path, info.commit, summary)
            except OSError as exc:
                print(f"警告: 写入 commit_history 失败: {exc}")

        print("\n===== 当前终端流程结束 =====")
        if others and spawned_system and not config.next_commands and not beta:
            print("其他平台可能仍在独立系统终端中运行，请查看对应窗口。")
        return exit_code
    finally:
        # Detached Terminal children may still need the shared file.
        if shared is not None and (not spawned_system or config.next_commands or beta):
            remove_path_quiet(shared)
