# ================================
# tests/smoke_wiki_storage_roots.py
#
# Wiki world/thread root 분리 계약을 검증합니다: config 기본값과 사용자 지정 root의 cwd 독립성,
# root 겹침 거부, 경로 탈출과 junction 탈출 거부, export·delete·branch가 threads root 안에 머무는지.
# 실제 assets/data는 읽거나 쓰지 않고 임시 디렉터리만 사용합니다.
#
# Functions
#   - _configured_roots(cwd: Path, overrides: dict[str, str]) -> subprocess.CompletedProcess[str] : 별도 프로세스에서 config Wiki root를 해석합니다.
#   - _link_directory(link: Path, target: Path) -> None : 디렉터리 junction(Windows) 또는 symlink를 만듭니다.
#   - _expect_raises(error: type[BaseException], action: Callable[[], object]) -> None : action이 지정 예외를 내는지 확인합니다.
#   - _check_config_roots(cwd: Path) -> None : 기본·상대·절대·겹침 root 설정을 검증합니다.
#   - _check_overlap_rejected(base: Path) -> None : 서로 포함하는 root 쌍을 거부하는지 검증합니다.
#   - _check_escapes_rejected(base: Path) -> None : 식별자 탈출, 외부 junction, 형제 thread alias를 resolver와 읽기·commit 경로에서 거부하는지 검증합니다.
#   - _check_lifecycle_stays_in_threads(base: Path) -> None : export·delete·branch가 threads root 밖을 건드리지 않는지 검증합니다.
#   - main() -> None : 모든 검증을 임시 디렉터리에서 실행합니다.
# ================================

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apps.app.models import ChatMessage, ConversationState  # noqa: E402
from src.apps.app.storage import ConversationStore  # noqa: E402
import src.apps.app.conversation_lifecycle as conversation_lifecycle  # noqa: E402
import src.apps.app.wiki_branching as wiki_branching  # noqa: E402
from src.wiki import (  # noqa: E402
    WikiScaffoldError,
    apply_pending_wiki_commit,
    scaffold_thread,
    scaffold_world,
)
from src.wiki.context import (  # noqa: E402
    get_wiki_thread_runtime_status,
    load_wiki_setup,
    read_wiki_thread_documents,
)
from src.wiki.paths import WikiContextError, WikiRoots, wiki_thread_root  # noqa: E402
from tests.wiki_smoke_fixtures import temporary_wiki_roots  # noqa: E402

_PRINT_ROOTS = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from src.wiki.paths import WIKI_ROOTS; print(WIKI_ROOTS.worlds); print(WIKI_ROOTS.threads)"
)


def _configured_roots(cwd: Path, overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Resolve the configured Wiki roots in a fresh process from an unrelated cwd."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"WIKI_WORLDS_ROOT", "WIKI_THREADS_ROOT"}
    }
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", _PRINT_ROOTS, str(ROOT)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _link_directory(link: Path, target: Path) -> None:
    """Create a directory junction on Windows or a directory symlink elsewhere."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _expect_raises(error: type[BaseException], action: Callable[[], object]) -> None:
    """Assert that action raises the given exception type."""
    try:
        action()
    except error:
        return
    raise AssertionError(f"expected {error.__name__}")


def _check_config_roots(cwd: Path) -> None:
    """Default, repo-relative, absolute, and overlapping root settings resolve independent of cwd."""
    default = _configured_roots(cwd, {})
    assert default.returncode == 0, default.stderr
    assert default.stdout.splitlines() == [
        str(ROOT / "assets" / "worlds" / "wiki"),
        str(ROOT / "data" / "wiki" / "threads"),
    ], default.stdout

    relative = _configured_roots(
        cwd,
        {"WIKI_WORLDS_ROOT": "custom/worlds", "WIKI_THREADS_ROOT": "custom/state/threads"},
    )
    assert relative.returncode == 0, relative.stderr
    assert relative.stdout.splitlines() == [
        str(ROOT / "custom" / "worlds"),
        str(ROOT / "custom" / "state" / "threads"),
    ], relative.stdout

    absolute_worlds = cwd / "elsewhere" / "worlds"
    absolute_threads = cwd / "other" / "threads"
    absolute = _configured_roots(
        cwd,
        {"WIKI_WORLDS_ROOT": str(absolute_worlds), "WIKI_THREADS_ROOT": str(absolute_threads)},
    )
    assert absolute.returncode == 0, absolute.stderr
    assert absolute.stdout.splitlines() == [str(absolute_worlds), str(absolute_threads)]

    overlapping = _configured_roots(
        cwd,
        {"WIKI_WORLDS_ROOT": "shared", "WIKI_THREADS_ROOT": "shared/threads"},
    )
    assert overlapping.returncode != 0
    assert "must not overlap" in overlapping.stderr, overlapping.stderr
    assert not (ROOT / "custom").exists() and not (ROOT / "shared").exists()


def _check_overlap_rejected(base: Path) -> None:
    """Equal or nested world/thread roots are rejected in either direction."""
    for worlds, threads in (
        (base / "same", base / "same"),
        (base / "worlds", base / "worlds" / "threads"),
        (base / "threads" / "worlds", base / "threads"),
    ):
        _expect_raises(WikiContextError, lambda w=worlds, t=threads: WikiRoots(worlds=w, threads=t))


def _check_escapes_rejected(base: Path) -> None:
    """Traversal identifiers and junctioned world/thread directories cannot escape their root."""
    roots = temporary_wiki_roots(base / "escape")
    scaffold_world(roots.worlds, "demo_world", "Demo")
    roots.threads.mkdir(parents=True)

    for thread_id in ("../outside", "a/b", "..", "", "a\\b"):
        _expect_raises(WikiContextError, lambda value=thread_id: wiki_thread_root(roots, value))
    _expect_raises(
        WikiContextError,
        lambda: load_wiki_setup(roots, "../demo_world", "default", "thread_ok"),
    )

    scaffold_thread(roots, "real_thread", "demo_world", "Real")
    assert wiki_thread_root(roots, "real_thread") == roots.threads.resolve() / "real_thread"
    assert wiki_thread_root(roots, "not_created_yet") == roots.threads.resolve() / "not_created_yet"
    _link_directory(roots.threads / "alias_thread", roots.threads / "real_thread")
    _expect_raises(WikiContextError, lambda: wiki_thread_root(roots, "alias_thread"))

    outside_thread = base / "outside_thread"
    (outside_thread / "characters").mkdir(parents=True)
    (outside_thread / "characters" / "leak.md").write_text("# Leak\n", encoding="utf-8")
    _link_directory(roots.threads / "linked_thread", outside_thread)
    _expect_raises(WikiContextError, lambda: wiki_thread_root(roots, "linked_thread"))
    _expect_raises(WikiContextError, lambda: read_wiki_thread_documents(roots, "linked_thread"))
    _expect_raises(WikiContextError, lambda: get_wiki_thread_runtime_status(roots, "linked_thread"))
    _expect_raises(WikiContextError, lambda: apply_pending_wiki_commit(roots, "linked_thread"))
    _expect_raises(
        WikiScaffoldError,
        lambda: scaffold_thread(roots, "linked_thread", "demo_world", "Linked"),
    )

    outside_world = base / "outside_world"
    scaffold_world(base / "outside_worlds_root", "demo_world", "Outside")
    (base / "outside_worlds_root" / "demo_world").replace(outside_world)
    _link_directory(roots.worlds / "linked_world", outside_world)
    _expect_raises(
        WikiContextError,
        lambda: load_wiki_setup(roots, "linked_world", "default", "thread_ok"),
    )
    _expect_raises(
        WikiScaffoldError,
        lambda: scaffold_thread(roots, "thread_ok", "linked_world", "Linked"),
    )
    assert (outside_thread / "characters" / "leak.md").is_file()


def _check_lifecycle_stays_in_threads(base: Path) -> None:
    """Export, delete, and branch refuse junctioned threads and never touch the worlds root."""
    roots = temporary_wiki_roots(base / "lifecycle")
    scaffold_world(roots.worlds, "demo_world", "Demo")
    world_manifest = roots.worlds / "demo_world" / "world.md"
    world_text = world_manifest.read_text(encoding="utf-8")
    store = ConversationStore(base / "lifecycle" / "data" / "threads")
    previous = (conversation_lifecycle.WIKI_ROOTS, wiki_branching.WIKI_ROOTS)
    conversation_lifecycle.WIKI_ROOTS = wiki_branching.WIKI_ROOTS = roots
    try:
        roots.threads.mkdir(parents=True)
        _link_directory(roots.threads / "linked_thread", roots.worlds / "demo_world")
        linked = ConversationState(
            world_mode="wiki",
            world_id="demo_world",
            scenario_id="default",
            thread_id="linked_thread",
            messages=[ChatMessage(id="linked_user", role="user", content="hi")],
        )
        store.save(linked)
        _expect_raises(
            WikiContextError,
            lambda: conversation_lifecycle.export_wiki_conversation(linked),
        )
        _expect_raises(
            WikiContextError,
            lambda: conversation_lifecycle.delete_wiki_conversation(linked, store),
        )
        _expect_raises(
            WikiContextError,
            lambda: wiki_branching.branch_wiki_conversation_before_message(linked, "linked_user", store),
        )
        assert world_manifest.read_text(encoding="utf-8") == world_text
        assert store.exists("linked_thread")

        scaffold_thread(roots, "real_thread", "demo_world", "Real")
        real = ConversationState(
            world_mode="wiki",
            world_id="demo_world",
            scenario_id="default",
            thread_id="real_thread",
        )
        store.save(real)
        archive, _filename = conversation_lifecycle.export_wiki_conversation(real)
        assert archive
        conversation_lifecycle.delete_wiki_conversation(real, store)
        assert not (roots.threads / "real_thread").exists()
        assert not store.exists("real_thread")
        assert sorted(path.name for path in roots.threads.iterdir()) == ["linked_thread"]
        assert world_manifest.read_text(encoding="utf-8") == world_text
    finally:
        conversation_lifecycle.WIKI_ROOTS, wiki_branching.WIKI_ROOTS = previous


def main() -> None:
    """Run every storage-root check against temporary directories only."""
    with TemporaryDirectory(prefix="smoke_wiki_storage_roots_") as temporary:
        base = Path(temporary)
        _check_config_roots(base)
        _check_overlap_rejected(base)
        _check_escapes_rejected(base)
        _check_lifecycle_stays_in_threads(base)
    print("smoke_wiki_storage_roots: ok")


if __name__ == "__main__":
    main()
