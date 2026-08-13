"""Safe wrapper around the existing Node/Puppeteer avatar renderer."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_RENDER_TIMEOUT_SECONDS = 45.0
MAX_RENDER_TIMEOUT_SECONDS = 120.0
MAX_AVATAR_BYTES = 10 * 1024 * 1024

_render_lock = threading.Lock()


def detect_node_binary(override: str | None = None) -> str | None:
    """Return an executable Node.js path without running it."""
    candidate = override or os.environ.get("NODE_BIN")
    if candidate:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

        return None

    for executable in ("node", "nodejs"):
        resolved = shutil.which(executable)
        if resolved:
            return resolved

    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        candidates = sorted(nvm_root.glob("v*/bin/node"), reverse=True)
        for path in candidates:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)

    return None


def is_valid_png(path: str | os.PathLike[str]) -> bool:
    """Return whether *path* is a non-empty, reasonably sized PNG file."""
    try:
        file_path = Path(path)
        size = file_path.stat().st_size
        if size <= len(PNG_SIGNATURE) or size > MAX_AVATAR_BYTES:
            return False
        with file_path.open("rb") as image:
            return image.read(len(PNG_SIGNATURE)) == PNG_SIGNATURE
    except OSError:
        return False


def _render_timeout(value: float | None) -> float:
    if value is None:
        raw = os.environ.get("AVATAR_RENDER_TIMEOUT_SECONDS", "")
        try:
            value = float(raw) if raw else DEFAULT_RENDER_TIMEOUT_SECONDS
        except ValueError:
            logger.warning("Invalid avatar render timeout; using the default")
            value = DEFAULT_RENDER_TIMEOUT_SECONDS

    if not math.isfinite(value) or value <= 0:
        return DEFAULT_RENDER_TIMEOUT_SECONDS
    return min(value, MAX_RENDER_TIMEOUT_SECONDS)


def _avatar_payload(user_data: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only fields consumed by the tracked habitica-avatar bundle."""
    return {
        "preferences": user_data.get("preferences") or {},
        "items": user_data.get("items") or {},
        "stats": user_data.get("stats") or {},
    }


def _cache_path(cache_dir: Path, cache_key: str) -> Path:
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"avatar-{digest}.png"


def render_avatar_png(
    user_data: Mapping[str, Any],
    *,
    cache_key: str,
    cache_dir: str | os.PathLike[str],
    renderer_path: str | os.PathLike[str],
    force_refresh: bool = False,
    node_bin: str | None = None,
    timeout_seconds: float | None = None,
) -> str | None:
    """Render and atomically cache an avatar, returning its absolute path."""
    if not isinstance(user_data, Mapping) or not cache_key:
        logger.warning("Avatar render skipped because required profile data is missing")
        return None

    cache_directory = Path(cache_dir).resolve()
    renderer = Path(renderer_path).resolve()
    bundle = renderer.with_name("habitica-avatar.bundle.js")
    final_path = _cache_path(cache_directory, cache_key)

    if not force_refresh and is_valid_png(final_path):
        return str(final_path)

    executable = detect_node_binary(node_bin)
    if executable is None:
        logger.error("Avatar renderer is unavailable because Node.js was not found")
        return None
    if not renderer.is_file():
        logger.error("Avatar renderer script is missing")
        return None
    if not bundle.is_file():
        logger.error("Avatar renderer bundle is missing")
        return None

    timeout = _render_timeout(timeout_seconds)

    with _render_lock:
        if not force_refresh and is_valid_png(final_path):
            return str(final_path)

        try:
            cache_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(cache_directory, 0o700)
        except OSError:
            logger.exception("Could not prepare the private avatar cache directory")
            return None

        staged_path: Path | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="habitica-avatar-") as temp_dir:
                temp_root = Path(temp_dir)
                user_json_path = temp_root / "user.json"
                rendered_path = temp_root / "avatar.png"
                user_json_path.write_text(
                    json.dumps(_avatar_payload(user_data), ensure_ascii=False),
                    encoding="utf-8",
                )
                os.chmod(user_json_path, 0o600)

                with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                    try:
                        result = subprocess.run(
                            [
                                executable,
                                str(renderer),
                                str(user_json_path),
                                str(rendered_path),
                            ],
                            cwd=str(renderer.parent),
                            stdout=stdout,
                            stderr=stderr,
                            check=False,
                            timeout=timeout,
                        )
                    except subprocess.TimeoutExpired:
                        logger.error("Avatar rendering timed out after %.1f seconds", timeout)
                        return None
                    except OSError as exc:
                        logger.error(
                            "Avatar renderer could not start (%s)",
                            type(exc).__name__,
                        )
                        return None

                if result.returncode != 0:
                    logger.error("Avatar renderer exited with code %s", result.returncode)
                    return None
                if not is_valid_png(rendered_path):
                    logger.error("Avatar renderer did not produce a valid PNG")
                    return None

                with tempfile.NamedTemporaryFile(
                    dir=cache_directory,
                    prefix=".avatar-",
                    suffix=".tmp",
                    delete=False,
                ) as staged:
                    staged_path = Path(staged.name)
                    with rendered_path.open("rb") as source:
                        shutil.copyfileobj(source, staged)
                    staged.flush()
                    os.fsync(staged.fileno())

                os.chmod(staged_path, 0o600)
                os.replace(staged_path, final_path)
                staged_path = None
        except (OSError, TypeError, ValueError):
            logger.exception("Avatar rendering failed while handling local files")
            return None
        finally:
            if staged_path is not None:
                try:
                    staged_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove a temporary avatar file")

    return str(final_path) if is_valid_png(final_path) else None
