from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .beta_init import init_beta
from .beta_doctor import collect_beta_checks
from .config import ConfigError, find_project_root, load_runtime_config
from .doctor import collect_checks, print_checks
from .init_cmd import repair_export_options, run_init
from .update_cmd import install_or_update
from .workflow.run import run_workflow


def _project_root(args: argparse.Namespace) -> Path:
    explicit = Path(args.project).expanduser().resolve() if args.project else None
    root = find_project_root(explicit=explicit)
    if root is None:
        raise ConfigError("无法定位 Flutter 项目（需要 pubspec.yaml）")
    return root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tbqrfly", description="Flutter 打包与二维码发布工具"
    )
    parser.add_argument("--version", action="version", version=f"tbqrfly {__version__}")
    sub = parser.add_subparsers(dest="command")

    init_p = sub.add_parser("init", help="初始化 .tbqrfly 配置目录")
    init_p.add_argument("--project", help="Flutter 项目根目录")
    init_p.add_argument("--target", choices=("run", "beta"), default="run")
    init_p.add_argument("--force", action="store_true", help="覆盖已有配置")
    init_p.add_argument(
        "--repair-export",
        action="store_true",
        help="仅根据 Xcode DEVELOPMENT_TEAM 生成/回填 ExportOptions.plist",
    )

    run_p = sub.add_parser("run", help="构建、上传并发送飞书二维码")
    run_p.add_argument("--project", help="Flutter 项目根目录")
    run_p.add_argument("--env", help="环境名，例如 dev/prod")
    run_p.add_argument("--platform", help="平台名，或 all")
    run_p.add_argument(
        "--shared-state",
        help="内部使用：跳过共享阶段，读取已生成的 summary/state",
    )
    run_p.add_argument("--debug", action="store_true")

    beta_p = sub.add_parser("beta", help="构建 prod 包并发布到平台内测通道")
    beta_p.add_argument("--project", help="Flutter 项目根目录")
    beta_p.add_argument("--platform", help="平台名，或 all")
    beta_p.add_argument("--shared-state", help=argparse.SUPPRESS)
    beta_p.add_argument("--debug", action="store_true")

    doctor_p = sub.add_parser("doctor", help="检查运行环境与配置")
    doctor_p.add_argument("--target", choices=("run", "beta"), default="run")
    doctor_p.add_argument("--project", help="Flutter 项目根目录")

    update_p = sub.add_parser("update", help="从 GitHub 更新 tbqrfly")
    update_p.add_argument("--repo", help="GitHub owner/repo，默认环境变量 TBQRFLY_REPO")
    update_p.add_argument("--ref", help="分支或 tag，默认 main")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    try:
        if args.command == "update":
            return install_or_update(ref=args.ref, repo=args.repo)

        if args.command == "init":
            root = _project_root(args)
            if args.target == "beta":
                if args.repair_export:
                    raise ConfigError(
                        "--repair-export 用于 Zealot；Beta 导出配置请在 beta.json 中指定"
                    )
                return init_beta(root, force=args.force)
            if args.repair_export:
                return repair_export_options(root)
            return run_init(root, force=args.force)

        if args.command == "doctor":
            if args.target == "beta":
                code = print_checks(collect_beta_checks(_project_root(args)))
                return 0 if code != 2 else 2
            root = find_project_root(
                explicit=(
                    Path(args.project).expanduser().resolve() if args.project else None
                )
            )
            config = None
            if root is not None:
                try:
                    config = load_runtime_config(root)
                except ConfigError:
                    config = None
            items = collect_checks(
                project_root=root, config=config, include_llm=True, for_init=False
            )
            code = print_checks(items)
            return 0 if code != 2 else 2

        if args.command in ("run", "beta"):
            root = _project_root(args)
            shared = (
                Path(args.shared_state).expanduser().resolve()
                if args.shared_state
                else None
            )
            return run_workflow(
                root,
                env=getattr(args, "env", None),
                platform=args.platform,
                shared_state=shared,
                debug=args.debug,
                target=args.command,
            )

        parser.error(f"未知命令: {args.command}")
        return 2
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n用户取消", file=sys.stderr)
        return 130
    except Exception as exc:
        if getattr(args, "debug", False):
            raise
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
