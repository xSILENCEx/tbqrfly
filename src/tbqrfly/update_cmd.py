from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

DEFAULT_REPO = os.environ.get("TBQRFLY_REPO", "xSILENCEx/tbqrfly")
DEFAULT_REF = os.environ.get("TBQRFLY_REF", "main")
INSTALL_DIR = Path(
    os.environ.get("TBQRFLY_HOME", Path.home() / ".tbqrfly")
).expanduser()


def _python() -> str:
    return sys.executable or "python3"


def install_or_update(*, ref: str | None = None, repo: str | None = None) -> int:
    repo = repo or DEFAULT_REPO
    ref = ref or DEFAULT_REF
    if sys.version_info < (3, 9):
        print(f"需要 Python ≥ 3.9，当前为 {sys.version.split()[0]}", file=sys.stderr)
        return 2

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    venv = INSTALL_DIR / "venv"
    if not (venv / "bin" / "python").exists():
        print(f"创建虚拟环境: {venv}")
        subprocess.check_call([_python(), "-m", "venv", str(venv)])

    pip = venv / "bin" / "pip"
    target = f"git+https://github.com/{repo}.git@{ref}"
    print(f"安装/更新: {target}")
    try:
        subprocess.check_call([str(pip), "install", "--upgrade", "pip"])
        # Same package version can hide git updates; always force reinstall from ref.
        subprocess.check_call(
            [
                str(pip),
                "install",
                "--upgrade",
                "--force-reinstall",
                "--no-cache-dir",
                target,
            ]
        )
    except subprocess.CalledProcessError:
        # Fallback: install from local checkout when developing.
        local = Path(__file__).resolve().parents[2]
        if (local / "pyproject.toml").is_file():
            print(f"GitHub 安装失败，回退本地安装: {local}")
            subprocess.check_call(
                [
                    str(pip),
                    "install",
                    "--upgrade",
                    "--force-reinstall",
                    "--no-cache-dir",
                    str(local),
                ]
            )
        else:
            raise

    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "tbqrfly"
    source = venv / "bin" / "tbqrfly"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(source)
    print(f"已链接: {link} -> {source}")
    if str(bin_dir) not in os.environ.get("PATH", ""):
        print(f"请将 {bin_dir} 加入 PATH，例如:")
        print(f'  export PATH="{bin_dir}:$PATH"')
    from . import __version__

    print(f"完成。当前版本: {__version__}")
    print("运行: tbqrfly --help")
    return 0


def fetch_install_script() -> str:
    url = f"https://raw.githubusercontent.com/{DEFAULT_REPO}/{DEFAULT_REF}/install.sh"
    with urlopen(url, timeout=30) as response:  # noqa: S310
        return response.read().decode("utf-8")
