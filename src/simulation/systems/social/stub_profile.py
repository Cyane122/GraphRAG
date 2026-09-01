# ================================
# src/simulation/systems/social/stub_profile.py
#
# Conservative transient-NPC profile synthesis and relation-descriptor validation for the Social system.
#
# Functions
#   - _normalize_relation_descriptor_for_family(name_kor: str) -> str | None : Validate sibling descriptors against known family data
#   - _is_stub_candidate(name_kor: str) -> bool : Return whether text is concrete enough to create a stub
#   - _build_conservative_stub_profile(name_kor: str, main_npc_id: str, source_text: str = "", world_config: dict | None = None) -> dict : Build a conservative transient profile
# ================================
import hashlib
import json
import re

from src.config import MODEL_STATE_UPDATER as STUB_MODEL
from src.core.database import async_driver
from src.core.llm.client import extract_json_from_llm, get_model
from src.simulation.systems.social.identity import _normalize_reference_kind
from src.simulation.systems.social.models import StubProfile
from src.simulation.systems.social.naming import _FALLBACK_GIVEN_NAMES_ROMAN, _KOREAN_SURNAME_ROMAN, _alt_surname, _fallback_given_name, _kor_to_roman_id

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

async def _primary_name_for_id(char_id: str) -> str:
    """char_id 로 캐릭터의 대표 이름을 반환한다. 없으면 빈 문자열."""
    if not char_id:
        return ""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character {id: $cid}) RETURN c.name AS name", cid=char_id
        )
        row = await rec.single()
    return str(row["name"]) if row and row["name"] else ""

async def _lookup_surname(name_part: str) -> str:
    """이름 일부로 캐릭터를 찾아 성(첫 글자)을 반환한다. 없으면 빈 문자열."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)
            WHERE c.name CONTAINS $partial
            RETURN c.name AS name
            ORDER BY size(c.name) ASC
            LIMIT 1
        """, partial=name_part)
        row = await rec.single()
    if row and row["name"] and re.match(r"^[가-힣]", row["name"]):
        return row["name"][0]
    return ""

async def _fetch_static_family_text(name_part: str) -> str:
    """Fetch StaticProfile.family text for the character matching a Korean name or alias."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:HAS_PROFILE]->(sp:StaticProfile)
            WHERE c.name CONTAINS $partial OR $partial IN c.aliases
            RETURN sp.props AS props_json
            ORDER BY size(c.name) ASC
            LIMIT 1
        """, partial=name_part)
        row = await rec.single()
    if not row or not row["props_json"]:
        return ""
    try:
        props = json.loads(row["props_json"])
    except (TypeError, json.JSONDecodeError):
        return ""
    family = props.get("family", "")
    return str(family).lower() if family else ""

def _sibling_role_from_family(family_text: str) -> str | None:
    """Infer the explicit sibling role described by a StaticProfile.family string."""
    if not family_text:
        return None
    if any(marker in family_text for marker in _NO_SIBLING_MARKERS):
        return "none"

    has_brother = any(marker in family_text for marker in _BROTHER_MARKERS)
    has_sister = any(marker in family_text for marker in _SISTER_MARKERS)
    if has_brother and not has_sister:
        return "\ub0a8\ub3d9\uc0dd"
    if has_sister and not has_brother:
        return "\uc5ec\ub3d9\uc0dd"
    return None

async def _normalize_relation_descriptor_for_family(name_kor: str) -> str | None:
    """Reject or normalize sibling descriptors that contradict an existing profile."""
    parsed = _parse_relation_descriptor(name_kor)
    if not parsed:
        return name_kor

    related_to, role = parsed
    if role not in _SIBLING_ROLE_SET:
        return name_kor

    expected = _sibling_role_from_family(await _fetch_static_family_text(related_to))
    if expected == "none":
        print(f"[WorldBuilder] sibling descriptor rejected by profile: {name_kor}")
        return None
    if expected and role == "\ub3d9\uc0dd":
        return f"{related_to} {expected}"
    if expected and role != expected:
        print(
            "[WorldBuilder] sibling descriptor rejected by profile: "
            f"{name_kor} (expected {expected})"
        )
        return None
    return name_kor

def _parse_relation_descriptor(value: str) -> tuple[str, str] | None:
    """Parse descriptors such as '유람의 남동생' or '유람 남동생'."""
    text = str(value or "").strip()
    patterns = (
        r"^([\uac00-\ud7a3]{2,4})\uc758\s*([\uac00-\ud7a3]+)$",
        r"^([\uac00-\ud7a3]{2,4})\s+([\uac00-\ud7a3]+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        related_to, role = match.group(1), match.group(2)
        if _KINSHIP_DESCRIPTOR_RE.search(role):
            return related_to, role
    return None

def _is_usable_generated_name(generated: str, original: str) -> bool:
    """Return whether generated is a real Korean-style name, not a descriptor."""
    if not generated:
        return False
    if _parse_relation_descriptor(generated):
        return False
    if not _KOREAN_NAME_RE.match(generated):
        return False
    # original이 관계 서술어이고 generated가 그것과 같다면 이름으로 쓸 수 없다
    if generated == original and _parse_relation_descriptor(original):
        return False
    return True

def _is_stub_candidate(name_kor: str) -> bool:
    """Return whether text is concrete enough to persist as a transient character."""
    value = str(name_kor or "").strip()
    if _KOREAN_NAME_RE.match(value):
        return True
    return _parse_relation_descriptor(value) is not None

def _sentence_snippets_for_name(source_text: str, name_kor: str) -> list[str]:
    """Return short source sentences that explicitly mention the transient character token."""
    text = re.sub(r"\s+", " ", str(source_text or "")).strip()
    if not text or not name_kor:
        return []
    pieces = re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", text)
    snippets: list[str] = []
    for piece in pieces:
        sentence = piece.strip()
        if not sentence or name_kor not in sentence:
            continue
        snippets.append(sentence[:220])
        if len(snippets) >= 3:
            break
    if snippets:
        return snippets
    index = text.find(name_kor)
    if index < 0:
        return []
    start = max(0, index - 80)
    end = min(len(text), index + len(name_kor) + 140)
    return [text[start:end].strip()]

def _snippet_with_markers(snippets: list[str], markers: tuple[str, ...]) -> str:
    """Return the first snippet containing any requested marker."""
    for snippet in snippets:
        if any(marker in snippet for marker in markers):
            return snippet
    return ""

def _stub_world_context(world_config: dict | None) -> str:
    """Return compact world/scenario text for plausible transient NPC defaults."""
    sections = (world_config or {}).get("prompt", {}).get("sections", {})
    parts: list[str] = []
    for key, limit in (("world", 900), ("scenario", 1400)):
        value = str(sections.get(key) or "").strip()
        if value:
            parts.append(value[:limit])
    return "\n\n".join(parts) if parts else "(none)"

async def _fill_plausible_stub_fields(
    name_kor: str,
    stub: dict,
    snippets: list[str],
    world_config: dict | None,
) -> dict:
    """Fill empty transient NPC fields with plausible defaults without overriding evidence."""
    from src.core.llm.client import extract_json_from_llm, get_model

    observed = " / ".join(snippets) if snippets else "(no direct descriptive sentence)"
    # 원문 토큰의 종류와 성별/나이를 먼저 확정해야 이름·신체값이 함께 일관된다.
    prompt = f"""Create minimal plausible defaults for a newly mentioned transient NPC.

Anchoring rules (decide in this order):
1. Classify the source token:
   - proper_name: an actual person name already (e.g. "서윤", "박민지")
   - role_title: a job/title/role label, not a name (e.g. "검사관", "접수원", "직원")
   - kinship_descriptor: a relational description (e.g. "유빈의 남동생", "엄마")
   - generic_person: a generic unnamed person label (e.g. "여학생", "손님")
2. Choose name_kor:
   - proper_name: preserve the token as name_kor.
   - role_title / generic_person / kinship_descriptor: generate a plausible full Korean name.
     Never use the role/title itself as name_kor.
3. Set promotion_eligible:
   - role_title and generic_person default false unless evidence clearly identifies a recurring named person.
   - proper_name and kinship_descriptor default true.
4. Determine biological_sex and age — these anchor every physical value.
5. Derive height (cm), weight (kg), measurements, and physique so they are mutually
   consistent and realistic for that sex, age, and build (e.g. a slender teenage girl
   and a heavyset middle-aged man must not share the same numbers).
6. measurements: for female use "B-W-H" in cm (e.g. "84-60-88"); for male use chest/waist
   in cm or leave blank if unnatural to state. physique: one short build descriptor
   (e.g. "마른", "보통", "탄탄한", "통통한").

Constraints:
- Preserve observed evidence exactly. Do not contradict it.
- Fill only missing fields. Do not overwrite observed fields.
- If appearance is observed, reuse it; otherwise invent a generic plausible appearance
  consistent with the anchored sex/age/build.
- If relationship/role is observed, reuse it; otherwise invent only a low-detail plausible role/status.
- Do not create secrets, durable biography, trauma, special skills, or strong personality unless evidence says so.
- Korean is OK. Return concise field values.

Character token: {name_kor}
Observed evidence: {observed}
Existing stub:
{json.dumps(stub, ensure_ascii=False)}

World/scenario context:
{_stub_world_context(world_config)}

Return ONLY JSON with optional fields:
{{
  "reference_kind": "proper_name | role_title | kinship_descriptor | generic_person",
  "source_token": "{name_kor}",
  "name_kor": "",
  "name_roman": "",
  "promotion_eligible": true,
  "biological_sex": "",
  "age": "",
  "height": "",
  "weight": "",
  "measurements": "",
  "physique": "",
  "appearance": "",
  "family": "",
  "formative_background": "",
  "initial_mood": "",
  "personality": "",
  "speech_style": "",
  "relation_type": "",
  "relation_status": "",
  "initial_affinity": 0
}}"""

    try:
        model = get_model(
            STUB_MODEL,
            system_prompt="Generate conservative, internally consistent defaults for transient roleplay NPC records.",
        )
        resp = await model.generate_content_async(
            prompt,
            generation_config={
                "temperature": 0.35,
                "max_output_tokens": 1024,
                "response_mime_type": "application/json",
                "log_source": "transient_npc_stub",
            },
        )
        parsed = extract_json_from_llm(resp.text, source="transient_npc_stub")
    except Exception as exc:
        print(f"[WorldBuilder] transient stub default generation failed: {exc}")
        return stub

    if not isinstance(parsed, dict):
        return stub

    # LLM 출력을 StubProfile로 검증/정규화한 뒤, 관찰 증거(이미 채워진 stub 필드)는 덮어쓰지 않는다.
    try:
        defaults = StubProfile.model_validate(parsed)
    except Exception as exc:
        print(f"[WorldBuilder] transient stub validation failed: {exc}")
        return stub

    merged = dict(stub)
    reference_kind = _normalize_reference_kind(defaults.reference_kind or merged.get("reference_kind"))
    merged["reference_kind"] = reference_kind
    if defaults.source_token:
        merged["source_token"] = defaults.source_token[:120]
    if defaults.name_kor:
        candidate_name = defaults.name_kor.strip()
        if _is_usable_generated_name(candidate_name, name_kor):
            merged["name_kor"] = candidate_name[:40]
    if defaults.name_roman:
        roman = re.sub(r"[^a-z0-9_]", "", defaults.name_roman.strip().lower())
        if roman:
            merged["name_roman"] = roman[:80]
    if defaults.promotion_eligible is not None:
        merged["promotion_eligible"] = bool(defaults.promotion_eligible)
    elif reference_kind in {"role_title", "generic_person"}:
        merged["promotion_eligible"] = False

    for key in (
        "biological_sex",
        "age",
        "height",
        "weight",
        "measurements",
        "physique",
        "appearance",
        "family",
        "formative_background",
        "initial_mood",
        "personality",
        "speech_style",
        "relation_type",
        "relation_status",
    ):
        if str(merged.get(key) or "").strip():
            continue
        value = str(getattr(defaults, key) or "").strip()
        if value:
            merged[key] = value[:500]
    if not merged.get("initial_affinity"):
        merged["initial_affinity"] = defaults.initial_affinity
    return merged

async def _build_conservative_stub_profile(
    name_kor: str,
    main_npc_id: str,
    source_text: str = "",
    world_config: dict | None = None,
) -> dict:
    """Build a transient NPC stub from observed text, filling unknowns plausibly."""
    parsed = _parse_relation_descriptor(name_kor)
    # 소유격 없이 호칭만 등장한 경우(예: '아빠','엄마') → 현재 메인 NPC의 가족으로 간주하고
    # 그 인물을 소유자로 삼아 이름을 생성한다. 부계 호칭(아빠/형 등)은 같은 성을 쓰고,
    # 엄마/어머니는 _SAME_SURNAME_ROLE_SET 에서 빠져 있어 자연히 다른 성이 부여된다.
    if parsed is None and _KINSHIP_DESCRIPTOR_RE.fullmatch(str(name_kor or "")):
        owner_name = await _primary_name_for_id(main_npc_id)
        if owner_name:
            parsed = (owner_name, name_kor)
    related_to, role = parsed if parsed else ("", name_kor)
    snippets = _sentence_snippets_for_name(source_text, name_kor)
    observed_context = " / ".join(snippets[:2])
    appearance = _snippet_with_markers(snippets, _APPEARANCE_MARKERS)
    role_evidence = _snippet_with_markers(snippets, _ROLE_MARKERS)
    if parsed:
        surname = ""
        if role in _SAME_SURNAME_ROLE_SET:
            surname = await _lookup_surname(related_to)
        if not surname:
            # \uac19\uc740 \uc131\uc774 \uc544\ub2cc \ud638\uce6d(\uc5c4\ub9c8/\uc5b4\uba38\ub2c8 \ub4f1)\uc740 \uc18c\uc720\uc790\uc640 \ub2e4\ub978 \uc131\uc744 \ubd80\uc5ec\ud55c\ub2e4.
            owner_surname = related_to[0] if related_to and "\uac00" <= related_to[0] <= "\ud7a3" else ""
            surname = _alt_surname(name_kor, owner_surname)
        generated_name = f"{surname}{_fallback_given_name(name_kor)}"
        surname_roman = _KOREAN_SURNAME_ROMAN.get(surname[0] if surname else "\uae40", "kim")
        digest = hashlib.sha1(name_kor.encode("utf-8")).digest()[0]
        given_roman = _FALLBACK_GIVEN_NAMES_ROMAN[digest % len(_FALLBACK_GIVEN_NAMES_ROMAN)]
        name_roman = f"{surname_roman}_{given_roman}"
        context = f"{related_to}의 {role} 관계로 언급되어 처음 인식된 인물."
        relation_type = "family" if role in _FAMILY_ROLE_SET else "acquaintance"
        reference_kind = "kinship_descriptor"
        promotion_eligible = True
    else:
        generated_name = name_kor
        name_roman = _kor_to_roman_id(name_kor)
        context = f"{name_kor}로 명시적으로 언급되어 처음 인식된 인물."
        relation_type = "acquaintance"
        reference_kind = "proper_name"
        promotion_eligible = True
    if observed_context:
        context = f"{context} Observed evidence: {observed_context}"

    stub = {
        "name_kor":             generated_name,
        "name_roman":           name_roman,
        "reference_kind":       reference_kind,
        "source_token":         name_kor,
        "biological_sex":       "",
        "age":                  "",
        "height":               "",
        "weight":               "",
        "measurements":         "",
        "physique":             "",
        "appearance":           appearance,
        "family":               "",
        "formative_background": "",
        "initial_mood":         "",
        "personality":          "",
        "speech_style":         "",
        "context":              context,
        "relation_type":        relation_type,
        "relation_status":      role_evidence,
        "initial_affinity":     0,
        "promotion_eligible":   promotion_eligible,
    }
    return await _fill_plausible_stub_fields(name_kor, stub, snippets, world_config)
