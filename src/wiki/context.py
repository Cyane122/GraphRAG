# ================================
# src/wiki/context.py
#
# Wiki world/scenario 문서를 검증하고 thread Markdown 상태로 물질화합니다.
#
# Classes
#   - WikiContextError : Wiki 런타임 문서나 식별자 계약 오류
#
# Functions
#   - _document_title(document: WikiDocument) -> str : Markdown H1 표시 제목을 반환합니다.
#   - _scenario_character_allowlist(document: WikiDocument) -> list[str] | None : scenario.md의 world-level character allowlist를 검증해 반환합니다.
#   - _validate_character_profile_id(value: str, field: str) -> str : scenario frontmatter의 character_profile ID 형식을 검증합니다.
#   - _materialize_primary_relationship(store: WikiStore, setup: WikiConversationSetup, created_at: str) -> None : 활성 Actor 관점 관계 변화 문서를 생성합니다.
#   - initialize_wiki_thread(vault_root: Path, world_id: str, scenario_id: str, thread_id: str) -> WikiConversationSetup : 새 Wiki thread와 초기 문서를 생성합니다.
#   - get_wiki_thread_runtime_status(vault_root: Path, thread_id: str) -> WikiThreadRuntimeStatus : 현재 런타임 생성 thread와 이전 형식 thread를 구분합니다.
#   - load_wiki_setup(vault_root: Path, world_id: str, scenario_id: str, thread_id: str) -> WikiConversationSetup : 런타임 메타데이터와 첫 장면을 읽습니다.
#   - read_wiki_actor_assets(vault_root: Path, world_id: str, scenario_id: str) -> list[WikiDocument] : Fixed prompt용 world 문서와 선택 prompt 추가문을 읽습니다.
#   - read_wiki_scene_prompt_assets(vault_root: Path, world_id: str, scenario_id: str, scene_types: list[str] | None = None) -> list[WikiScenePromptAsset] : 월드 씬 프롬프트에 시나리오 override를 적용해 읽습니다.
#   - read_wiki_scene_descriptions(vault_root: Path, world_id: str, scenario_id: str) -> dict[str, str] : 공용 분류 설명에 Wiki 전용 scene key를 합칩니다.
#   - read_wiki_thread_documents(vault_root: Path, thread_id: str) -> list[WikiDocument] : Actor와 Updater가 사용할 thread 문서를 읽습니다.
#   - document_body(content: str) -> str : frontmatter를 제외한 Markdown 본문을 반환합니다.
#   - scene_datetime_and_location(content: str) -> tuple[datetime, str] : 현재 장면의 한국어 또는 영어 시각과 장소를 해석합니다.
# ================================

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re

from src.wiki.manual_audit import ensure_audit_baseline
from src.wiki.models import (
    WikiConversationSetup,
    WikiDocument,
    WikiScenePromptAsset,
    WikiThreadRuntimeStatus,
)
from src.wiki.scaffold import render_wiki_template, scaffold_thread
from src.wiki.store import WikiStore
from src.wiki.variants import WikiVariantError, resolve_profile_variants


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_VALID_POV_MODES = {"1p_user", "1p_char", "3p_user", "3p_char"}
_CHARACTER_PROFILE_PREFIX = "character_profile:"
_THREAD_RUNTIME_MARKER = ".wikirag-runtime.json"
_THREAD_RUNTIME_FORMAT_VERSION = 1
_SCENE_TYPE_CATALOG_PATH = Path(__file__).with_name("prompts") / "scene_types.json"
_KOREAN_DATETIME_RE = re.compile(
    r"(?P<year>\d{4})년\s*(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일"
    r"(?:\s+[^,\n]*요일)?\s*,?\s*(?P<period>오전|오후|저녁|밤|새벽)?\s*"
    r"(?P<hour>\d{1,2})시(?:\s*(?P<minute>\d{1,2})분)?"
)
_ENGLISH_DATETIME_RE = re.compile(
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s+on\s+"
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})",
    re.IGNORECASE,
)


class WikiContextError(RuntimeError):
    """Wiki 런타임에 필요한 문서나 메타데이터가 유효하지 않을 때 발생합니다."""


def _write_thread_runtime_marker(
    thread_root: Path,
    setup: WikiConversationSetup,
) -> None:
    """성공적으로 물질화한 새 thread에 Actor 비가시 런타임 표식을 원자적으로 기록합니다."""
    marker_path = thread_root / _THREAD_RUNTIME_MARKER
    temporary_path = thread_root / f"{_THREAD_RUNTIME_MARKER}.tmp"
    payload = {
        "format_version": _THREAD_RUNTIME_FORMAT_VERSION,
        "world_id": setup.world_id,
        "scenario_id": setup.scenario_id,
        "thread_id": setup.thread_id,
    }
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(marker_path)


def get_wiki_thread_runtime_status(
    vault_root: Path,
    thread_id: str,
) -> WikiThreadRuntimeStatus:
    """현재 런타임 표식 유무와 버전으로 thread 세대를 판별합니다."""
    safe_thread_id = _validate_identifier(thread_id, "thread_id")
    thread_root = (vault_root.resolve() / "threads" / safe_thread_id).resolve()
    expected_root = vault_root.resolve() / "threads"
    if not thread_root.is_relative_to(expected_root) or not thread_root.is_dir():
        return WikiThreadRuntimeStatus(
            generation="missing",
            message="Wiki thread directory does not exist.",
        )
    marker_path = thread_root / _THREAD_RUNTIME_MARKER
    if not marker_path.is_file():
        return WikiThreadRuntimeStatus(
            generation="legacy",
            message="Thread predates the current Wiki runtime marker.",
        )
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
        format_version = int(payload.get("format_version"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return WikiThreadRuntimeStatus(
            generation="legacy",
            message="Wiki runtime marker is unreadable.",
        )
    if format_version != _THREAD_RUNTIME_FORMAT_VERSION:
        return WikiThreadRuntimeStatus(
            generation="legacy",
            format_version=format_version,
            message="Thread uses a different Wiki runtime format version.",
        )
    return WikiThreadRuntimeStatus(
        generation="current",
        format_version=format_version,
        message="Thread was materialized by the current Wiki runtime.",
    )


def _validate_identifier(value: str, field: str) -> str:
    """경로에 사용할 단일 식별자를 검증해 반환합니다."""
    normalized = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise WikiContextError(f"Invalid {field}: {value!r}")
    return normalized


def _world_root(vault_root: Path, world_id: str) -> Path:
    """검증된 world vault 경로를 반환합니다."""
    safe_world_id = _validate_identifier(world_id, "world_id")
    root = vault_root.resolve()
    path = (root / "worlds" / safe_world_id).resolve()
    if not path.is_relative_to(root / "worlds") or not path.is_dir():
        raise WikiContextError(f"Wiki world does not exist: {safe_world_id}")
    return path


def _scenario_root(vault_root: Path, world_id: str, scenario_id: str) -> Path:
    """검증된 scenario bundle 경로를 반환합니다."""
    safe_scenario_id = _validate_identifier(scenario_id, "scenario_id")
    world_root = _world_root(vault_root, world_id)
    scenarios_root = (world_root / "scenarios").resolve()
    path = (scenarios_root / safe_scenario_id).resolve()
    if not path.is_relative_to(scenarios_root) or not path.is_dir():
        raise WikiContextError(f"Wiki scenario does not exist: {safe_scenario_id}")
    for filename in ("scenario.md", "start_state.md", "opening_scene.md"):
        if not (path / filename).is_file():
            raise WikiContextError(f"Wiki scenario is missing {filename}: {safe_scenario_id}")
    return path


def document_body(content: str) -> str:
    """YAML frontmatter를 제거한 Markdown 본문을 반환합니다."""
    return _FRONTMATTER_RE.sub("", content, count=1).strip()


def _document_title(document: WikiDocument) -> str:
    """문서의 첫 H1 제목을 반환합니다."""
    match = _H1_RE.search(document_body(document.content))
    if match is None:
        raise WikiContextError(f"Wiki document has no H1 title: {document.path}")
    return match.group(1).strip()


def _validate_character_profile_id(value: str, field: str) -> str:
    """scenario frontmatter에서 쓰는 character_profile ID 형식을 검증합니다."""
    normalized = str(value or "").strip()
    if not normalized.startswith(_CHARACTER_PROFILE_PREFIX):
        raise WikiContextError(f"Invalid {field}: {value!r}")
    slug = normalized.removeprefix(_CHARACTER_PROFILE_PREFIX)
    _validate_identifier(slug, field)
    return normalized


def _scenario_character_allowlist(document: WikiDocument) -> list[str] | None:
    """scenario.md의 optional characters allowlist를 검증해 반환합니다."""
    if document.metadata is None:
        return None
    extra = document.metadata.model_extra or {}
    if "characters" not in extra or extra["characters"] is None:
        return None
    raw_allowlist = extra["characters"]
    if not isinstance(raw_allowlist, list):
        raise WikiContextError("scenario characters must be a list of character_profile IDs")
    allowlist: list[str] = []
    for index, item in enumerate(raw_allowlist):
        if not isinstance(item, str):
            raise WikiContextError(
                "scenario characters must be a list of character_profile IDs"
            )
        allowlist.append(
            _validate_character_profile_id(item, f"scenario characters[{index}]")
        )
    return allowlist


def _profile_documents(vault_root: Path, world_id: str, scenario_id: str) -> list[WikiDocument]:
    """공통 profile에 scenario allowlist를 적용하고 전용 profile을 함께 읽습니다."""
    world_root = _world_root(vault_root, world_id)
    scenario_root = _scenario_root(vault_root, world_id, scenario_id)
    store = WikiStore(world_root)
    scenario = store.read_document(
        (scenario_root / "scenario.md").relative_to(world_root).as_posix()
    )
    allowlist = _scenario_character_allowlist(scenario)
    allowed_ids = set(allowlist) if allowlist is not None else None
    world_documents: list[WikiDocument] = []
    scenario_documents: list[WikiDocument] = []
    available_ids: set[str] = set()
    for path in sorted((world_root / "characters").glob("*.md")):
        document = store.read_document(path.relative_to(world_root).as_posix())
        if document.metadata is None or document.metadata.type != "character_profile":
            raise WikiContextError(f"Expected character_profile document: {path}")
        available_ids.add(document.metadata.id)
        if allowed_ids is None or document.metadata.id in allowed_ids:
            world_documents.append(document)
    for path in sorted((scenario_root / "characters").glob("*.md")):
        document = store.read_document(path.relative_to(world_root).as_posix())
        if document.metadata is None or document.metadata.type != "character_profile":
            raise WikiContextError(f"Expected character_profile document: {path}")
        available_ids.add(document.metadata.id)
        scenario_documents.append(document)
    if allowlist is not None:
        missing_ids = [profile_id for profile_id in allowlist if profile_id not in available_ids]
        if missing_ids:
            raise WikiContextError(
                "scenario characters references unknown profile IDs: "
                + ", ".join(missing_ids)
            )
    return world_documents + scenario_documents


def _known_scenario_ids(vault_root: Path, world_id: str) -> set[str]:
    """한 world에서 작성용 프로필 선택기로 사용할 수 있는 scenario ID를 반환합니다."""
    scenarios_root = _world_root(vault_root, world_id) / "scenarios"
    return {path.name for path in scenarios_root.iterdir() if path.is_dir()}


def _world_runtime_fields(
    vault_root: Path,
    world_id: str,
) -> tuple[str, str, str, str]:
    """world.md frontmatter에서 PC/NPC profile, POV와 rating을 반환합니다."""
    world_root = _world_root(vault_root, world_id)
    document = WikiStore(world_root).read_document("world.md")
    metadata = document.metadata
    if metadata is None:
        raise WikiContextError("world.md requires frontmatter")
    pc_profile_id = str(getattr(metadata, "pc_profile_id", "") or "").strip()
    npc_profile_id = str(getattr(metadata, "npc_profile_id", "") or "").strip()
    pov_mode = str(getattr(metadata, "pov_mode", "3p_char") or "3p_char").strip()
    rating = str(getattr(metadata, "rating", "r18") or "r18").strip()
    if not pc_profile_id or not npc_profile_id:
        raise WikiContextError("world.md requires pc_profile_id and npc_profile_id")
    if pov_mode not in _VALID_POV_MODES:
        raise WikiContextError(f"Unsupported Wiki pov_mode: {pov_mode}")
    if rating not in {"all_ages", "15", "r18"}:
        raise WikiContextError(f"Unsupported Wiki rating: {rating}")
    return pc_profile_id, npc_profile_id, pov_mode, rating


def _opening_prose(document: WikiDocument) -> str:
    """opening_scene 문서에서 제목을 제외한 첫 장면 원문만 반환합니다."""
    body = document_body(document.content)
    lines = body.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lines.pop(0)
    return "\n".join(lines).strip()


def load_wiki_setup(
    vault_root: Path,
    world_id: str,
    scenario_id: str,
    thread_id: str,
) -> WikiConversationSetup:
    """Wiki 대화에 필요한 인물 식별자, 시점과 첫 장면을 읽습니다."""
    safe_world_id = _validate_identifier(world_id, "world_id")
    safe_scenario_id = _validate_identifier(scenario_id, "scenario_id")
    safe_thread_id = _validate_identifier(thread_id, "thread_id")
    scenario_root = _scenario_root(vault_root, safe_world_id, safe_scenario_id)
    profiles = _profile_documents(vault_root, safe_world_id, safe_scenario_id)
    by_id = {document.metadata.id: document for document in profiles if document.metadata}
    pc_profile_id, npc_profile_id, pov_mode, rating = _world_runtime_fields(
        vault_root,
        safe_world_id,
    )
    scenario = WikiStore(scenario_root).read_document("scenario.md")
    scenario_extra = scenario.metadata.model_extra or {} if scenario.metadata is not None else {}
    scenario_pov_mode = (
        str(scenario_extra.get("pov_mode", "") or "").strip()
    )
    if scenario_pov_mode:
        if scenario_pov_mode not in _VALID_POV_MODES:
            raise WikiContextError(
                f"Unsupported scenario pov_mode: {scenario_pov_mode}"
            )
        pov_mode = scenario_pov_mode
    scenario_npc_profile_id = str(scenario_extra.get("npc_profile_id", "") or "").strip()
    if scenario_npc_profile_id:
        npc_profile_id = _validate_character_profile_id(
            scenario_npc_profile_id,
            "scenario npc_profile_id",
        )
    try:
        pc_document = by_id[pc_profile_id]
        npc_document = by_id[npc_profile_id]
    except KeyError as exc:
        raise WikiContextError(f"Configured character profile is missing: {exc.args[0]}") from exc
    opening = WikiStore(scenario_root).read_document("opening_scene.md")
    return WikiConversationSetup(
        world_id=safe_world_id,
        scenario_id=safe_scenario_id,
        thread_id=safe_thread_id,
        pc_id=pc_profile_id,
        pc_name=_document_title(pc_document),
        npc_id=npc_profile_id,
        npc_name=_document_title(npc_document),
        pov_mode=pov_mode,
        perspective=1 if pov_mode.startswith("1p_") else 3,
        rating=rating,
        opening_scene=_opening_prose(opening),
    )


def _replace_body(content: str, body: str) -> str:
    """기존 frontmatter를 보존하면서 Markdown 본문을 교체합니다."""
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        raise WikiContextError("Scaffold document requires frontmatter")
    return f"{match.group(0).rstrip()}\n{body.strip()}\n"


def _thread_character_content(
    profile: WikiDocument,
    setup: WikiConversationSetup,
    known_scenario_ids: set[str],
) -> str:
    """작성용 분기를 제거한 완전한 character 문서를 대화 상태로 물질화합니다."""
    if profile.metadata is None:
        raise WikiContextError(f"Profile requires frontmatter: {profile.path}")
    slug = profile.metadata.id.rsplit(":", 1)[-1]
    created_at = profile.metadata.created_at.isoformat()
    try:
        body = resolve_profile_variants(
            document_body(profile.content),
            setup.scenario_id,
            known_scenario_ids,
        )
    except WikiVariantError as exc:
        raise WikiContextError(f"Invalid profile variants in {profile.path}: {exc}") from exc
    if "## 현재 상태" not in body:
        body += (
            "\n\n## 현재 상태\n\n"
            "### 현재 위치와 활동\n\n- 위치: 현재 장면 참조\n- 활동:\n\n"
            "### 신체 상태와 감정 상태\n\n- 신체 상태:\n- 감정 상태:\n\n"
            "### 욕구와 컨디션\n\n"
            "- Needs: hunger=0.3000; rest=0.2000; social=0.1000; "
            "fun=0.4000; safety=0.0500; libido=0.2000\n"
            "- Active pressure: none\n"
            "- Condition: stable\n\n"
            "### Personality Change Ledger\n\n"
            "- No durable personality change has occurred since the story began.\n\n"
            "### Reproductive State\n\n"
            "- Menstrual cycle: disabled\n"
            "- Contraception: none\n"
            "- Cycle day: 1\n"
            "- Pregnant: no\n"
            "- Pregnancy day: 0\n"
            "- Internal ejaculation count this cycle: 0\n"
            "- Other parent: unknown"
        )
    return (
        "---\n"
        f"id: character:{setup.thread_id}:{slug}\n"
        "type: character\n"
        "schema_version: 1\n"
        f"world_id: {setup.world_id}\n"
        f"thread_id: {setup.thread_id}\n"
        f"profile_id: {profile.metadata.id}\n"
        "visibility: [actor, updater, player]\n"
        f"created_at: {created_at}\n"
        "---\n"
        f"{body.strip()}\n"
    )


def _document_title(document: WikiDocument) -> str:
    """Markdown 문서의 H1 표시 제목을 반환합니다."""
    match = _H1_RE.search(document.content)
    if match is None:
        raise WikiContextError(f"Wiki document has no H1 title: {document.path}")
    return match.group(1).strip()


def _materialize_primary_relationship(
    store: WikiStore,
    setup: WikiConversationSetup,
    created_at: str,
) -> None:
    """활성 Actor가 플레이어를 보는 관계 변화 원장을 빠진 경우 생성합니다."""
    owner_slug = setup.npc_id.rsplit(":", 1)[-1]
    other_slug = setup.pc_id.rsplit(":", 1)[-1]
    relative_path = f"relationships/{owner_slug}--{other_slug}.md"
    if store.resolve_path(relative_path).exists():
        return
    owner_document = store.read_document(f"characters/{owner_slug}.md")
    other_document = store.read_document(f"characters/{other_slug}.md")
    owner_name = _document_title(owner_document)
    other_name = _document_title(other_document)
    store.create_document(
        relative_path,
        render_wiki_template(
            "relationship.md",
            {
                "DOCUMENT_ID": f"relationship:{owner_slug}--{other_slug}",
                "THREAD_ID": setup.thread_id,
                "OWNER_ID": setup.npc_id,
                "PARTICIPANT_A_ID": setup.npc_id,
                "PARTICIPANT_B_ID": setup.pc_id,
                "TITLE": f"{owner_name}'s Relationship with {other_name}",
                "CREATED_AT": created_at,
            },
        ),
    )


def initialize_wiki_thread(
    vault_root: Path,
    world_id: str,
    scenario_id: str,
    thread_id: str,
) -> WikiConversationSetup:
    """새 Wiki thread를 만들고 선택한 시작 설정과 인물 문서를 물질화합니다."""
    setup = load_wiki_setup(vault_root, world_id, scenario_id, thread_id)
    thread_root = vault_root / "threads" / setup.thread_id
    manifest_path = thread_root / "thread.md"
    scene_path = thread_root / "scene" / "current.md"
    created_thread = not manifest_path.exists()
    if not manifest_path.exists():
        scaffold_thread(
            vault_root,
            setup.thread_id,
            setup.world_id,
            f"{setup.world_id}/{setup.scenario_id}",
        )
    elif not scene_path.exists():
        raise WikiContextError(
            f"Existing Wiki thread is missing scene/current.md: {setup.thread_id}"
        )
    thread_store = WikiStore(thread_root)

    manifest = thread_store.read_document("thread.md")
    manifest_content = manifest.content
    manifest_values = {
        "활성 시나리오": setup.scenario_id,
        "플레이어 캐릭터": setup.pc_id,
        "기본 시점": setup.pov_mode,
        "시작 조건": f"scenarios/{setup.scenario_id}/start_state.md",
        "현재 단계": "opening",
    }
    for label, value in manifest_values.items():
        manifest_content = re.sub(
            rf"(?m)^- {re.escape(label)}:\s*$",
            f"- {label}: {value}",
            manifest_content,
        )
    if manifest_content != manifest.content:
        thread_store.write_document(
            "thread.md",
            manifest_content,
            expected_revision=manifest.revision,
        )

    scene = thread_store.read_document("scene/current.md")
    if "## 시작 기준" not in scene.content:
        scenario_root = _scenario_root(vault_root, setup.world_id, setup.scenario_id)
        start_state = WikiStore(scenario_root).read_document("start_state.md")
        start_body = re.sub(
            r"^#\s+.+?$",
            "# 현재 장면",
            document_body(start_state.content),
            count=1,
            flags=re.MULTILINE,
        )
        thread_store.write_document(
            "scene/current.md",
            _replace_body(scene.content, start_body),
            expected_revision=scene.revision,
        )

    profiles = _profile_documents(vault_root, setup.world_id, setup.scenario_id)
    profiles_by_id = {
        profile.metadata.id: profile
        for profile in profiles
        if profile.metadata is not None
    }
    known_scenario_ids = _known_scenario_ids(vault_root, setup.world_id)
    for profile in profiles_by_id.values():
        if profile.metadata is None:
            continue
        slug = profile.metadata.id.rsplit(":", 1)[-1]
        relative_path = f"characters/{slug}.md"
        if not thread_store.resolve_path(relative_path).exists():
            thread_store.create_document(
                relative_path,
                _thread_character_content(profile, setup, known_scenario_ids),
            )
    if manifest.metadata is None:
        raise WikiContextError("thread.md requires frontmatter metadata")
    _materialize_primary_relationship(
        thread_store,
        setup,
        manifest.metadata.created_at.isoformat(),
    )
    if created_thread:
        _write_thread_runtime_marker(thread_root, setup)
    ensure_audit_baseline(thread_store)
    return setup


def read_wiki_actor_assets(
    vault_root: Path,
    world_id: str,
    scenario_id: str,
) -> list[WikiDocument]:
    """Fixed prompt 자산과 Graph 방식의 선택 prompt 추가문을 읽습니다."""
    world_root = _world_root(vault_root, world_id)
    scenario_root = _scenario_root(vault_root, world_id, scenario_id)
    relative_paths = [Path("world.md"), Path("prose.md")]
    for directory in ("locations", "organizations"):
        relative_paths.extend(
            path.relative_to(world_root)
            for path in sorted((world_root / directory).glob("*.md"))
        )
    relative_paths.append(
        (scenario_root / "scenario.md").relative_to(world_root)
    )
    cot_append_path = scenario_root / "cot_append.md"
    if not cot_append_path.is_file():
        cot_append_path = world_root / "cot_append.md"
    if cot_append_path.is_file():
        relative_paths.append(cot_append_path.relative_to(world_root))
    blacklist_path = world_root / "blacklist.md"
    if blacklist_path.is_file():
        relative_paths.append(blacklist_path.relative_to(world_root))
    store = WikiStore(world_root)
    return [store.read_document(path.as_posix()) for path in relative_paths]


def _read_scene_prompt_asset(
    store: WikiStore,
    world_root: Path,
    path: Path,
) -> WikiScenePromptAsset:
    """한 scene prompt 문서의 metadata와 파일명 key가 일치하는지 검증합니다."""
    document = store.read_document(path.relative_to(world_root).as_posix())
    metadata = document.metadata
    if metadata is None or metadata.type != "scene_prompt":
        raise WikiContextError(f"Scene prompt requires type scene_prompt: {document.path}")
    extra = metadata.model_extra or {}
    scene_type = str(extra.get("scene_type") or "").strip()
    description = str(extra.get("description") or "").strip()
    if path.stem != scene_type:
        raise WikiContextError(
            f"Scene prompt filename must match scene_type {scene_type!r}: {document.path}"
        )
    return WikiScenePromptAsset(
        scene_type=scene_type,
        description=description,
        document=document,
    )


def read_wiki_scene_prompt_assets(
    vault_root: Path,
    world_id: str,
    scenario_id: str,
    scene_types: list[str] | None = None,
) -> list[WikiScenePromptAsset]:
    """월드 씬 프롬프트를 읽고 같은 key의 시나리오 문서로 교체합니다."""
    world_root = _world_root(vault_root, world_id)
    scenario_root = _scenario_root(vault_root, world_id, scenario_id)
    selected = set(scene_types) if scene_types is not None else None
    store = WikiStore(world_root)
    inherited: dict[str, WikiScenePromptAsset] = {}
    for directory in (world_root / "scenes", scenario_root / "scenes"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            asset = _read_scene_prompt_asset(store, world_root, path)
            if selected is None or asset.scene_type in selected:
                inherited[asset.scene_type] = asset
    return [inherited[scene_type] for scene_type in sorted(inherited)]


def read_wiki_scene_descriptions(
    vault_root: Path,
    world_id: str,
    scenario_id: str,
) -> dict[str, str]:
    """공용 Wiki scene 설명에 월드·시나리오 scene prompt 설명을 덮어 합칩니다."""
    try:
        raw_catalog = json.loads(_SCENE_TYPE_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WikiContextError("Wiki scene type catalog is unreadable") from exc
    if not isinstance(raw_catalog, dict):
        raise WikiContextError("Wiki scene type catalog must be a JSON object")
    descriptions = {
        str(scene_type): str(description)
        for scene_type, description in raw_catalog.items()
        if _IDENTIFIER_RE.fullmatch(str(scene_type))
        and str(description).strip()
    }
    for asset in read_wiki_scene_prompt_assets(vault_root, world_id, scenario_id):
        descriptions[asset.scene_type] = asset.description
    if "daily" not in descriptions:
        raise WikiContextError("Wiki scene type catalog requires daily")
    return descriptions


def read_wiki_thread_documents(vault_root: Path, thread_id: str) -> list[WikiDocument]:
    """현재 thread의 canonical Markdown 문서를 안정된 순서로 읽습니다."""
    safe_thread_id = _validate_identifier(thread_id, "thread_id")
    thread_root = (vault_root.resolve() / "threads" / safe_thread_id).resolve()
    if not thread_root.is_dir() or not thread_root.is_relative_to(vault_root.resolve() / "threads"):
        raise WikiContextError(f"Wiki thread does not exist: {safe_thread_id}")
    store = WikiStore(thread_root)
    paths = [
        path.relative_to(thread_root).as_posix()
        for path in sorted(thread_root.rglob("*.md"))
        if path.name != "commit.md" and "commits" not in path.relative_to(thread_root).parts
    ]
    return [store.read_document(path) for path in paths]


def scene_datetime_and_location(content: str) -> tuple[datetime, str]:
    """현재 장면 Markdown에서 한국어 또는 영어 날짜·시각과 장소를 읽습니다."""
    body = document_body(content)
    match = _KOREAN_DATETIME_RE.search(body)
    if match is not None:
        hour = int(match.group("hour"))
        period = match.group("period") or ""
        if period in {"오후", "저녁", "밤"} and hour < 12:
            hour += 12
        if period == "오전" and hour == 12:
            hour = 0
        parsed = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            hour,
            int(match.group("minute") or 0),
        )
    else:
        match = _ENGLISH_DATETIME_RE.search(body)
        if match is None:
            raise WikiContextError("scene/current.md has no parseable datetime")
        try:
            parsed = datetime.strptime(
                (
                    f"{match.group('month')} {match.group('day')} "
                    f"{match.group('year')} {match.group('hour')}:{match.group('minute')}"
                ),
                "%B %d %Y %H:%M",
            )
        except ValueError as exc:
            raise WikiContextError("scene/current.md has an invalid English datetime") from exc
    line_end = body.find("\n", match.end())
    line = body[match.end(): line_end if line_end >= 0 else len(body)]
    location_match = re.search(
        r"(?:,\s*)?(?:(?:in|at|between)\s+)?(.+?)(?:다\.)?\.?\s*$",
        line.strip(),
        re.IGNORECASE,
    )
    location = location_match.group(1).strip() if location_match else "현재 장소"
    return parsed, location
