# ================================
# src/simulation/systems/social/identity.py
#
# Character identity cache and deterministic transient-NPC identity helpers for the Social system.
#
# Functions
#   - _get_known_chars() -> dict[str, str] : Fetch character names and aliases by active DB session
#   - _get_primary_names() -> dict[str, str] : Fetch character id-to-primary-name mappings
#   - _invalidate_cache() -> None : Invalidate the active session character cache
#   - _resolve_identity(name: str, known: dict[str, str]) -> str | None : Resolve an existing character name
# ================================
import re
from datetime import datetime

from src.core.database import async_driver
from src.simulation.systems.social.naming import _alt_surname, _compose_name

_known_chars_cache: dict[str, dict[str, str]] = {}

_KINSHIP_DESCRIPTOR_RE = re.compile(
    "|".join(
        [
            "\ub0a8\ub3d9\uc0dd",
            "\uc5ec\ub3d9\uc0dd",
            "\ub3d9\uc0dd",
            "\uc624\ube60",
            "\uc5b8\ub2c8",
            "\ub204\ub098",
            "\ud615",
            "\uc544\ube60",
            "\uc5c4\ub9c8",
            "\uc544\ubc84\uc9c0",
            "\uc5b4\uba38\ub2c8",
            "\uce5c\uad6c",
            "\uc120\ubc30",
            "\ud6c4\ubc30",
        ]
    )
)

_SAME_SURNAME_ROLE_SET: frozenset[str] = frozenset({
    "\ub0a8\ub3d9\uc0dd",
    "\uc5ec\ub3d9\uc0dd",
    "\ub3d9\uc0dd",
    "\ud615",
    "\uc624\ube60",
    "\uc5b8\ub2c8",
    "\ub204\ub098",
    "\uc544\ubc84\uc9c0",
    "\uc544\ube60",
    "\ud560\uc544\ubc84\uc9c0",
})

_FAMILY_ROLE_SET: frozenset[str] = _SAME_SURNAME_ROLE_SET | frozenset({
    "엄마", "어머니", "할머니",
})

_KOREAN_NAME_RE = re.compile(r"^[\uac00-\ud7a3]{2,4}$")

_SIBLING_ROLE_SET: frozenset[str] = frozenset({
    "\ub3d9\uc0dd",
    "\ub0a8\ub3d9\uc0dd",
    "\uc5ec\ub3d9\uc0dd",
})

_BROTHER_MARKERS: tuple[str, ...] = (
    "younger brother",
    "little brother",
    "brother",
    "\ub0a8\ub3d9\uc0dd",
)

_SISTER_MARKERS: tuple[str, ...] = (
    "younger sister",
    "little sister",
    "sister",
    "\uc5ec\ub3d9\uc0dd",
)

_NO_SIBLING_MARKERS: tuple[str, ...] = (
    "no siblings",
    "only child",
    "\uc678\ub3d9",
    "\ud615\uc81c\uac00 \uc5c6",
    "\uc790\ub9e4\uac00 \uc5c6",
)

_APPEARANCE_MARKERS: tuple[str, ...] = (
    "키", "몸", "체형", "얼굴", "머리", "머리카락", "눈", "입술", "피부", "외모",
    "인상", "표정", "복장", "옷", "유니폼", "제복", "앞치마", "화장", "향수",
    "마른", "통통", "작은", "큰", "긴", "짧은", "검은", "갈색", "금발",
)

_ROLE_MARKERS: tuple[str, ...] = (
    "직원", "종업원", "알바", "사장", "손님", "친구", "선배", "후배", "동급생",
    "동료", "가족", "남동생", "여동생", "동생", "언니", "누나", "오빠", "형",
    "아버지", "어머니", "엄마", "아빠", "담당", "관리자", "경호원", "운전기사",
)

_FEMALE_SEX_MARKERS: tuple[str, ...] = (
    "female",
    "woman",
    "girl",
    "여성",
    "여자",
)

_MALE_SEX_MARKERS: tuple[str, ...] = (
    "male",
    "man",
    "boy",
    "남성",
    "남자",
)

_REFERENCE_KINDS: frozenset[str] = frozenset({
    "proper_name",
    "role_title",
    "kinship_descriptor",
    "generic_person",
})

def _is_female_sex(value: object) -> bool:
    """Return whether a sex label should track a menstrual cycle."""
    text = str(value or "").strip().lower()
    if not text:
        return False
    if any(marker in text for marker in _FEMALE_SEX_MARKERS):
        return True
    if any(marker in text for marker in _MALE_SEX_MARKERS):
        return False
    return False

def _initial_cycle_day(seed_text: str) -> int:
    """Return a deterministic initial menstrual cycle day."""
    digest = hashlib.sha1(str(seed_text or "").encode("utf-8")).digest()[2]
    return (digest % 28) + 1

def _coerce_int(value: object) -> int | None:
    """Parse an integer-like value, returning None when it is not usable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"-?\d+", str(value or ""))
    return int(match.group(0)) if match else None

def _normalize_reference_kind(value: object) -> str:
    """Normalize transient source-token classification."""
    kind = str(value or "").strip().lower()
    return kind if kind in _REFERENCE_KINDS else "proper_name"

def _requires_generated_name(reference_kind: str) -> bool:
    """Return whether display name must differ from the source token."""
    return reference_kind in {"role_title", "generic_person", "kinship_descriptor"}

def _fallback_identity_for_reference(source_token: str) -> tuple[str, str]:
    """Generate fallback identity for source tokens that are not proper names."""
    return _compose_name(_alt_surname(source_token), source_token)

def _cache_key() -> str:
    """캐릭터 이름 캐시 키 = 현재 활성 Kuzu DB 경로(스레드/대화별 격리). 활성 드라이버 없으면 '__global__'."""
    from src.core.database.driver import current_db_path

    return current_db_path() or "__global__"

async def _get_known_chars() -> dict[str, str]:
    """현재 세션의 캐릭터 이름→id 캐시를 반환한다. 최초 호출 시 DB 조회."""
    key = _cache_key()
    if key in _known_chars_cache:
        return _known_chars_cache[key]

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            RETURN c.id AS id, c.name AS name, c.aliases AS aliases
        """)
        rows = await rec.data()

    result: dict[str, str] = {}
    for r in rows:
        if r["id"]:
            result[r["id"]] = r["id"]
        if r["name"]:
            result[r["name"]] = r["id"]
        for alias in (r["aliases"] or []):
            result[alias] = r["id"]

    _known_chars_cache[key] = result
    return result

async def _get_primary_names() -> dict[str, str]:
    """캐릭터 id -> 대표 이름 맵을 반환한다."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            RETURN c.id AS id, c.name AS name
        """)
        rows = await rec.data()
    return {
        str(row["id"]): str(row["name"])
        for row in rows
        if row.get("id") and row.get("name")
    }

def _invalidate_cache() -> None:
    """현재 세션의 캐릭터 캐시를 무효화한다."""
    _known_chars_cache.pop(_cache_key(), None)

def _resolve_identity(name: str, known: dict[str, str]) -> str | None:
    """이름이 known dict에 정확히 있으면 char_id를 반환한다."""
    if name in known:
        return known[name]
    if _KINSHIP_DESCRIPTOR_RE.search(name):
        return None
    return None
