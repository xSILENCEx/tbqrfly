from __future__ import annotations

from pathlib import Path

from ..config import RuntimeConfig


def matching_files(config: RuntimeConfig, platform: str) -> list[Path]:
    matches: list[Path] = []
    for pattern in config.artifact_patterns(platform):
        path = Path(pattern).expanduser()
        if path.is_absolute():
            if path.is_file():
                matches.append(path)
        else:
            matches.extend(
                item for item in config.project_root.glob(pattern) if item.is_file()
            )
    # Deduplicate by resolved path
    unique = {item.resolve(): item for item in matches}
    return list(unique.values())


def find_reusable(config: RuntimeConfig, platform: str) -> Path | None:
    """Simplified reuse: any matching artifact with the same glob filename hit."""
    candidates = [item for item in matching_files(config, platform) if item.stat().st_size > 1000]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def find_after_build(config: RuntimeConfig, platform: str) -> Path | None:
    return find_reusable(config, platform)
