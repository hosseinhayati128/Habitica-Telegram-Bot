import json
import os
import re
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import avatar_renderer as renderer


VALID_PNG = renderer.PNG_SIGNATURE + b"test-png-payload"


@pytest.fixture
def renderer_files(tmp_path):
    renderer_path = tmp_path / "renderer" / "render_avatar_from_json.js"
    renderer_path.parent.mkdir()
    renderer_path.write_text("// test renderer", encoding="utf-8")
    renderer_path.with_name("habitica-avatar.bundle.js").write_text(
        "// test bundle",
        encoding="utf-8",
    )
    return renderer_path, tmp_path / "cache"


@pytest.fixture(autouse=True)
def block_unmocked_subprocesses(monkeypatch):
    def unexpected_subprocess(*args, **kwargs):
        raise AssertionError("unexpected subprocess invocation")

    monkeypatch.setattr(renderer.subprocess, "run", unexpected_subprocess)


def _install_fake_node(monkeypatch):
    monkeypatch.setattr(renderer, "detect_node_binary", lambda override=None: "/fake/node")


def _render(renderer_path, cache_dir, **kwargs):
    return renderer.render_avatar_png(
        {
            "preferences": {"costume": True},
            "items": {"gear": {"equipped": {"weapon": "weapon_test"}}},
            "stats": {"class": "wizard"},
        },
        cache_key="habitica-user-id",
        cache_dir=cache_dir,
        renderer_path=renderer_path,
        **kwargs,
    )


def test_missing_node_returns_none_without_starting_a_process(
    monkeypatch,
    renderer_files,
):
    renderer_path, cache_dir = renderer_files
    monkeypatch.setattr(renderer, "detect_node_binary", lambda override=None: None)

    assert _render(renderer_path, cache_dir) is None
    assert not cache_dir.exists()


def test_missing_renderer_script_returns_none(monkeypatch, tmp_path):
    _install_fake_node(monkeypatch)
    missing_script = tmp_path / "missing" / "render_avatar_from_json.js"

    assert _render(missing_script, tmp_path / "cache") is None


def test_missing_renderer_bundle_returns_none(monkeypatch, tmp_path):
    _install_fake_node(monkeypatch)
    renderer_path = tmp_path / "render_avatar_from_json.js"
    renderer_path.write_text("// test renderer", encoding="utf-8")

    assert _render(renderer_path, tmp_path / "cache") is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1])
def test_invalid_render_timeout_uses_the_default(value):
    assert renderer._render_timeout(value) == renderer.DEFAULT_RENDER_TIMEOUT_SECONDS


def test_renderer_timeout_returns_none_and_leaves_no_cache_file(
    monkeypatch,
    renderer_files,
):
    renderer_path, cache_dir = renderer_files
    _install_fake_node(monkeypatch)

    def time_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(renderer.subprocess, "run", time_out)

    assert _render(renderer_path, cache_dir, timeout_seconds=0.25) is None
    assert list(cache_dir.glob("*")) == []


def test_nonzero_renderer_exit_returns_none(monkeypatch, renderer_files):
    renderer_path, cache_dir = renderer_files
    _install_fake_node(monkeypatch)
    monkeypatch.setattr(
        renderer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7),
    )

    assert _render(renderer_path, cache_dir) is None
    assert list(cache_dir.glob("*")) == []


@pytest.mark.parametrize("output_bytes", [None, b"not-a-png"])
def test_missing_or_invalid_png_is_not_cached(
    monkeypatch,
    renderer_files,
    output_bytes,
):
    renderer_path, cache_dir = renderer_files
    _install_fake_node(monkeypatch)

    def complete_without_a_valid_png(args, **kwargs):
        if output_bytes is not None:
            Path(args[3]).write_bytes(output_bytes)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(renderer.subprocess, "run", complete_without_a_valid_png)

    assert _render(renderer_path, cache_dir) is None
    assert list(cache_dir.glob("*")) == []


def test_cache_paths_are_opaque_and_distinct(monkeypatch, renderer_files):
    renderer_path, cache_dir = renderer_files
    _install_fake_node(monkeypatch)

    def write_png(args, **kwargs):
        Path(args[3]).write_bytes(VALID_PNG)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(renderer.subprocess, "run", write_png)
    raw_keys = ("alice@example.com", "alice@example.com/other")
    paths = [
        renderer.render_avatar_png(
            {"preferences": {}, "items": {}, "stats": {}},
            cache_key=cache_key,
            cache_dir=cache_dir,
            renderer_path=renderer_path,
        )
        for cache_key in raw_keys
    ]

    assert paths[0] != paths[1]
    for path in paths:
        assert path is not None
        assert re.fullmatch(r"avatar-[0-9a-f]{24}\.png", Path(path).name)
        assert all(raw_key not in path for raw_key in raw_keys)


def test_renderer_receives_only_the_minimal_private_json(
    monkeypatch,
    renderer_files,
):
    renderer_path, cache_dir = renderer_files
    _install_fake_node(monkeypatch)
    observed = {}

    def inspect_json_and_write_png(args, **kwargs):
        json_path = Path(args[2])
        observed["payload"] = json.loads(json_path.read_text(encoding="utf-8"))
        observed["json_path"] = json_path
        observed["mode"] = stat.S_IMODE(json_path.stat().st_mode)
        Path(args[3]).write_bytes(VALID_PNG)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(renderer.subprocess, "run", inspect_json_and_write_png)
    profile = {
        "preferences": {"costume": True},
        "items": {"gear": {}},
        "stats": {"class": "healer"},
        "auth": {"local": {"email": "private@example.com"}},
        "apiToken": "habitica-api-token-secret",
        "profile": {"name": "Private Name"},
    }

    result = renderer.render_avatar_png(
        profile,
        cache_key="private-user-id",
        cache_dir=cache_dir,
        renderer_path=renderer_path,
    )

    assert result is not None
    assert observed["payload"] == {
        "preferences": profile["preferences"],
        "items": profile["items"],
        "stats": profile["stats"],
    }
    assert observed["mode"] == 0o600
    assert not observed["json_path"].exists()


def test_success_is_atomically_cached_and_reused(
    monkeypatch,
    renderer_files,
):
    renderer_path, cache_dir = renderer_files
    _install_fake_node(monkeypatch)
    process = Mock()

    def write_png(args, **kwargs):
        process(args, **kwargs)
        Path(args[3]).write_bytes(VALID_PNG)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(renderer.subprocess, "run", write_png)
    real_replace = os.replace
    replacements = []

    def track_replace(source, destination):
        source = Path(source)
        destination = Path(destination)
        assert source.exists()
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(renderer.os, "replace", track_replace)

    first = _render(renderer_path, cache_dir)

    assert first is not None
    first_path = Path(first)
    assert renderer.is_valid_png(first_path)
    assert first_path.read_bytes() == VALID_PNG
    assert stat.S_IMODE(first_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache_dir.stat().st_mode) == 0o700
    assert len(replacements) == 1
    staged_path, destination = replacements[0]
    assert staged_path.parent == cache_dir
    assert staged_path.suffix == ".tmp"
    assert not staged_path.exists()
    assert destination == first_path

    monkeypatch.setattr(
        renderer,
        "detect_node_binary",
        lambda override=None: pytest.fail("cached render looked for Node"),
    )
    monkeypatch.setattr(
        renderer.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("cached render started a process"),
    )

    assert _render(renderer_path, cache_dir) == first
    assert process.call_count == 1


def test_concurrent_requests_for_one_key_render_only_once(
    monkeypatch,
    renderer_files,
):
    renderer_path, cache_dir = renderer_files
    _install_fake_node(monkeypatch)
    calls = []

    def write_png(args, **kwargs):
        calls.append(tuple(args))
        Path(args[3]).write_bytes(VALID_PNG)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(renderer.subprocess, "run", write_png)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_render, renderer_path, cache_dir) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]

    assert results[0] == results[1]
    assert results[0] is not None
    assert len(calls) == 1
