from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path


@contextmanager
def ios_release_lock(project_root: Path, platform: str):
    if platform != "ios":
        yield
        return
    import fcntl

    directory = project_root / ".tbqrfly" / ".state"
    directory.mkdir(parents=True, exist_ok=True)
    # Keep the inode: unlinking a flock file can let another process bypass the lock.
    with (directory / "ios-release.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("已有 iOS 发布任务正在运行，请等待其结束后重试") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
