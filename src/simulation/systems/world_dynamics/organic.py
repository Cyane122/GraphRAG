# ================================
# src/simulation/systems/world_dynamics/organic.py
#
# 임신 확률 계산 및 생리 주기/임신 상태 관리를 담당합니다.
#
# Functions
#   - has_pregnancy_risk_signal(actor_response: str) -> bool : 사정/질내 관련 표현 존재 여부(동기, LLM 미호출) — organic 게이트 공용
#   - detect_internal_ejaculation(actor_response: str) -> bool : 명시적 사정 표현 prefilter 후 Flash로 현재 질내·콘돔 여부를 분류
#   - process_ejaculation(npc_id: str, actor_response: str, scene_char_ids: list[str] | None, intimate_char_ids: list[str] | None) -> str | None : 질내사정 감지 시 확률 계산 후 임신 여부 결정
#   - set_pregnant_manual(char_ref: str, father_ref: str | None) -> str | None : 이름/ID로 캐릭터를 직접 임신 상태로 전환 (강제 임신; 아빠 지정 가능)
#   - simulate_internal_ejaculation(mother_ref: str, father_ref: str | None, shots: int) -> str | None : N회 질내사정을 가정해 가임 주기 기반 확률로 임신 여부를 시뮬레이션 (pregManager 누락 보정)
#   - tick_pregnancy_day(npc_id: str, days_passed: int) -> None : 게임 내 날짜 경과 시 pregnancy_day 증가
#   - tick_cycle_day(npc_id: str, days_passed: int) -> None : 게임 내 날짜 경과 시 cycle_day 증가 (28일 주기)
#   - tick_all_cycles(days_passed: int) -> None : has_menstrual_cycle=true인 모든 캐릭터 일괄 갱신
# ================================

import random
import re
from datetime import datetime

from src.config import MODEL_CLASSIFIER as _FLASH_MODEL
from src.core.database import async_driver, update_dynamic_state
from src.core.llm.client import (
    extract_json_from_llm,
    get_model,
    get_response_text,
    log_empty_response_diagnostics,
)

# ── 확률 파라미터 ─────────────────────────────────────────
BASE_FERTILE    = 0.27   # 가임기 단발 기준 확률
BASE_INFERTILE  = 0.01   # 비가임기 희박 확률
PROB_CAP        = 0.45   # 한 주기 최대 임신 확률
_EJAC_CLASSIFIER_OUTPUT_TOKENS = 128
_EJAC_CLASSIFIER_RETRY_OUTPUT_TOKENS = 256

DAY_WEIGHT: dict[int, float] = {
    10: 0.30,
    11: 0.50,
    12: 0.70,
    13: 0.90,
    14: 1.00,  # 배란 피크
    15: 0.80,
    16: 0.30,
    17: 0.10,
}

# ── 사정·절정 표현 prefilter/fallback ─────────────────────
# LLM 호출 전 명시적 사정 묘사가 있는 턴만 통과시키고,
# Flash 분류 실패 시에는 보수적 fallback으로도 사용합니다.
_EJAC_RE = re.compile(
    # 직접 사정 표현
    r"질내사정"
    r"|(?:안|속)에\s*(?:쏟|싸|쌌|뿌렸|채웠|사정)"
    r"|자궁\s*(?:안|속)에\s*(?:쏟|싸|뿌렸|사정)"
    r"|(?:정액|하얀\s*액체|체액)\s*(?:을|를)?\s*(?:쏟|싸|쌌|뿌렸|채웠)"
    r"|(?:정액|semen|cum)"
    r"|사정\s*(?:했|해버렸|해\s*버렸|하고\s*말았|하며)"
    # 절정·클라이막스 (간접 묘사)
    r"|절정\s*(?:에\s*달했|을\s*맞이|했|에\s*이르|을\s*느)"
    r"|클라이막스"
    r"|(?:쾌감|욕구|충동).*통제할\s*수\s*없"
    # 콘돔·고무 파열과 함께하는 삽입 묘사
    r"|(?:찢어진|터진|파열된)\s*(?:라텍스|콘돔|고무)"
    r"|(?:라텍스|콘돔|고무\s*막)\s*(?:찢어지|터지|파열)"
    # English fallback terms sometimes appear in model/user mixed-language turns.
    r"|came\s+inside"
    r"|cum(?:med|s|ming)?\s+inside"
    r"|ejaculat(?:ed|es|ing|ion).*inside"
    r"|filled\s+her\s+(?:inside|womb|pussy|vagina)"
    r"|inside\s+(?:her\s+)?(?:vagina|pussy|womb)"
    r"|condom\s*(?:broke|split|tore|slipped)"
    r"|bareback"
    r"|without\s+(?:a\s+)?condom",
    re.IGNORECASE,
)

_EXPLICIT_INTERNAL_EJAC_RE = re.compile(
    r"질내사정"
    r"|(?:질|보지|자궁|몸\s*안|안|속)\s*(?:에|으로|안에|속에)?\s*"
    r"(?:정액을\s*)?(?:쏟|쏟아|싸|쌌|싸버렸|뿌렸|채웠|사정)"
    r"|(?:쏟|쏟아|싸|쌌|싸버렸|뿌렸|채웠|사정).*?(?:질|보지|자궁|몸\s*안|안|속)"
    r"|(?:정액|체액)\s*(?:이|가)?\s*(?:질|보지|자궁|몸\s*안|안쪽|속)\s*(?:에|으로)?\s*(?:흘러|들어|고였|찼)"
    r"|사정(?:했|해버렸|하며).*?(?:다\s*담기지\s*못한|넘친|새어\s*나온|흘러나온).*?"
    r"(?:정액|체액).*?(?:다리\s*사이|허벅지\s*사이|가랑이)"
    r"|came\s+inside"
    r"|cum(?:med)?\s+inside"
    r"|ejaculat(?:ed|ion).*inside"
    r"|filled\s+her\s+(?:inside|womb|pussy|vagina)"
    r"|inside\s+(?:her\s+)?(?:vagina|pussy|womb)",
    re.IGNORECASE,
)

_PROTECTED_CONDOM_RE = re.compile(
    r"(?:콘돔|라텍스|고무)\s*(?:을|를|이|가)?\s*(?:꼈|끼고|낀|착용|쓴|사용)"
    r"|(?:콘돔|라텍스|고무)\s*(?:안|속)"
    r"|(?:wearing|wore|used|with)\s+(?:a\s+)?condom"
    r"|condom\s+(?:on|protected|intact)"
    r"|inside\s+(?:the\s+)?condom",
    re.IGNORECASE,
)

_BROKEN_OR_ABSENT_CONDOM_RE = re.compile(
    r"(?:콘돔|라텍스|고무)\s*(?:없이|안\s*끼|안\s*꼈|찢|터지|파열|벗겨|빠지)"
    r"|(?:생으로|노콘)"
    r"|(?:without|no)\s+(?:a\s+)?condom"
    r"|condom\s*(?:broke|split|tore|torn|slipped|off)"
    r"|bareback",
    re.IGNORECASE,
)

_NON_VAGINAL_EJAC_RE = re.compile(
    r"(?:입\s*안|입속|입에|입으로|목구멍|구강|oral|mouth|throat)"
    r"|(?:항문|애널|후장|anal|anus|ass)"
    r"|(?:밖에|밖으로|외부|배\s*위|얼굴|가슴|손에|external|outside|belly|face|chest|hand)",
    re.IGNORECASE,
)

_NON_ACTUAL_EJAC_CONTEXT_RE = re.compile(
    r"(?:싸|쌌|사정|질내사정|정액|ejaculat|cum|came).*?"
    r"(?:도\s*돼|도\s*되|도\s*괜찮|해도\s*돼|해도\s*되|해도\s*괜찮|할까|할래|하면|한다면|할\s*경우|할\s*수도|"
    r"할\s*지도|하려고|하려\s*는|하고\s*싶|싶어|말했|물었|생각|상상|걱정|위험|가능성|"
    r"asked|asks|ask|could|would|whether|if|want(?:ed)?|worr(?:y|ied))"
    r"|(?:해도\s*돼|해도\s*되|하면|한다면|상상|걱정|물었|말했).*?"
    r"(?:싸|쌌|사정|질내사정|정액|ejaculat|cum|came)"
    r"|(?:asked|asks|ask|could|would|whether|if|want(?:ed)?|worr(?:y|ied)).*?"
    r"(?:ejaculat|cum|came|inside)"
    r"|(?:아까|방금\s*전|이전|어제|지난|전에|예전에|과거|기억|회상).*?"
    r"(?:싸|쌌|사정|질내사정|정액|ejaculat|cum|came)"
    r"|(?:아직|결국|끝내|실제로)?.*?"
    r"(?:싸지\s*않|싸지\s*못|쏘지\s*않|사정하지\s*않|사정하지\s*못|질내사정하지\s*않|"
    r"not\s+(?:ejaculat|cum|come|came)|did(?:n't| not)\s+(?:ejaculat|cum|come)|"
    r"never\s+(?:ejaculat|cum|came|came\s+inside))",
    re.IGNORECASE,
)

_COMPLETED_EJAC_RE = re.compile(
    r"(?:질내사정\s*(?:했|해버렸|하고\s*말았|한다)|"
    r"쌌|싸버렸|사정했|사정해버렸|사정하며|쏟아냈|쏟았다|뿌렸다|채웠다|찼다|흘러들어갔다|"
    r"came\s+inside|cummed\s+inside|ejaculated|filled\s+her)",
    re.IGNORECASE,
)

_SEGMENT_SPLIT_RE = re.compile(r"[\n\r.!?。？！]+")

_EJAC_CLASSIFY_SYSTEM = """\
Classify current-scene ejaculation in Korean adult roleplay text.
Return JSON only. No explanation. No markdown.

## False Conditions

talk / anticipation / permission / hypothetical / past reference -> current_ejaculation=false.
earlier semen/fluid still leaking/remains -> current_ejaculation=false.
no active sexual act -> current_ejaculation=false.

## Fields

current_ejaculation = true iff ejaculation happens in this accepted scene now.
vaginal = true iff current male ejaculation inside vagina is actively depicted.
anal / oral / external / no ejaculation -> vaginal=false.
condom_protected = true iff condom worn AND no break/slip/absence.
no condom / broken condom / bareback -> condom_protected=false.
recipient_refs = receiver names/ids/pronouns only. Exclude ejaculating character. Unknown/none -> [].

## Examples

Input: "그가 사정했다. 다 담기지 못한 정액이 다리 사이로 흘러나왔다."
Output: {"current_ejaculation": true, "vaginal": true, "condom_protected": false, "recipient_refs": []}

Input: "아까 싼 정액이 아직도 흘러나왔다."
Output: {"current_ejaculation": false, "vaginal": false, "condom_protected": false, "recipient_refs": []}

Input: "민지가 그의 사정을 받아냈고 소라는 옆에서 지켜봤다."
Output: {"current_ejaculation": true, "vaginal": true, "condom_protected": false, "recipient_refs": ["민지"]}

Output schema: {"current_ejaculation": true, "vaginal": true, "condom_protected": false, "recipient_refs": []}"""


def _calc_prob(cycle_day: int, count: int) -> float:
    """누적 확률 계산.
    p = 1 - (1 - base * weight)^count, capped at PROB_CAP.
    """
    if 10 <= cycle_day <= 17:
        weight = DAY_WEIGHT.get(cycle_day, 0.1)
        base   = BASE_FERTILE * weight
    else:
        base   = BASE_INFERTILE

    return min(1 - (1 - base) ** count, PROB_CAP)


async def _classify_ejaculation(actor_response: str) -> dict:
    """Flash로 사정 맥락(질내 여부·콘돔 상태)을 분류한다.

    실패 시 빈 dict 반환 → 호출부에서 fallback 처리.
    """
    model = get_model(model_name=_FLASH_MODEL, system_prompt=_EJAC_CLASSIFY_SYSTEM)
    try:
        config = {
            "max_output_tokens": _EJAC_CLASSIFIER_OUTPUT_TOKENS,
            "temperature": 0.0,
            "thinking_config": {"thinking_budget": 0},
        }
        response = await model.generate_content_async(
            actor_response[:3000],
            generation_config={**config, "response_mime_type": "application/json"},
        )
        raw = get_response_text(response)
        if not raw.strip():
            log_empty_response_diagnostics(response, "ejac_classifier:json_mode")
            response = await model.generate_content_async(
                actor_response[:3000],
                generation_config={**config, "max_output_tokens": _EJAC_CLASSIFIER_RETRY_OUTPUT_TOKENS},
            )
            raw = get_response_text(response)
        if not raw.strip():
            log_empty_response_diagnostics(response, "ejac_classifier:retry")
            return {}
        return extract_json_from_llm(raw, source="ejac_classifier") or {}
    except Exception as e:
        print(f"[PregnancyMgr] ejaculation classify failed: {e}")
        return {}


def _has_explicit_unprotected_internal_ejaculation(actor_response: str) -> bool:
    """LLM 실패 시 사용할 엄격한 질내사정 fallback을 판정합니다."""
    for segment in _iter_ejaculation_segments(actor_response):
        if not _EXPLICIT_INTERNAL_EJAC_RE.search(segment):
            continue
        if _NON_VAGINAL_EJAC_RE.search(segment):
            continue
        if not _COMPLETED_EJAC_RE.search(segment):
            continue
        if _NON_ACTUAL_EJAC_CONTEXT_RE.search(segment):
            continue
        if _BROKEN_OR_ABSENT_CONDOM_RE.search(segment):
            return True
        if _PROTECTED_CONDOM_RE.search(segment):
            continue
        return True
    return False


def _recipient_refs_from_classifier(result: dict) -> list[str]:
    """Return recipient refs from classifier output as clean strings."""
    refs = result.get("recipient_refs")
    if not isinstance(refs, list):
        return []
    return [str(ref).strip() for ref in refs if str(ref or "").strip()]


def _is_clearly_non_pregnancy_input(actor_response: str) -> bool:
    """Return whether a signal-bearing turn is clearly non-pregnancy-relevant."""
    segments = list(_iter_ejaculation_segments(actor_response))
    if not segments:
        return False
    for segment in segments:
        if _NON_VAGINAL_EJAC_RE.search(segment):
            continue
        if _NON_ACTUAL_EJAC_CONTEXT_RE.search(segment):
            continue
        return False
    return True


def _iter_ejaculation_segments(actor_response: str) -> list[str]:
    """Return punctuation-delimited segments that contain ejaculation signals."""
    parts = [p.strip() for p in _SEGMENT_SPLIT_RE.split(actor_response) if p.strip()]
    if not parts:
        parts = [actor_response.strip()]
    signal_parts = [part for part in parts if _EJAC_RE.search(part) or _EXPLICIT_INTERNAL_EJAC_RE.search(part)]
    if len(signal_parts) > 1 and actor_response.strip():
        return [actor_response.strip(), *signal_parts]
    return signal_parts


def _text_mentions_ref(actor_response: str, char_id: str, char_name: str) -> bool:
    """Return whether a character id or name is explicitly mentioned in the response."""
    return bool((char_id and char_id in actor_response) or (char_name and char_name in actor_response))


def has_pregnancy_risk_signal(actor_response: str) -> bool:
    """사정/질내 관련 표현이 있는지 동기로 빠르게 확인한다(LLM 미호출).

    organic 시스템 게이트가 임신 감지기(_EJAC_RE)와 동일한 기준으로 발화하도록
    공용으로 노출한다. 여기서 True여도 실제 임신 판정은 process_ejaculation 내부의
    분류기·확률·주기 조건을 모두 통과해야 한다(false positive는 그 단계에서 걸러짐).
    """
    return bool(_EJAC_RE.search(actor_response or ""))


async def detect_internal_ejaculation(actor_response: str) -> bool:
    """임신 가능한 질내사정 여부를 감지한다.

    명시적 사정 표현이 없으면 LLM 호출 없이 False를 반환합니다.
    표현이 있으면 Flash 분류로 질내 여부·콘돔 상태를 판단합니다.
    Flash 실패 시 명시적·무방비 질내사정 표현이 있을 때만 True.
    """
    analysis = await _analyze_internal_ejaculation(actor_response)
    return bool(analysis.get("detected"))


async def _analyze_internal_ejaculation(actor_response: str) -> dict:
    """Return pregnancy-risk detection details for the accepted response."""
    if not _EJAC_RE.search(actor_response):
        return {"detected": False, "recipient_refs": []}
    if _is_clearly_non_pregnancy_input(actor_response):
        return {"detected": False, "recipient_refs": []}

    result = await _classify_ejaculation(actor_response)
    current_ejaculation = result.get("current_ejaculation")
    vaginal          = result.get("vaginal")
    condom_protected = result.get("condom_protected")
    recipient_refs   = _recipient_refs_from_classifier(result)

    print(
        "[PregnancyMgr] ejac classify → "
        f"current={current_ejaculation} vaginal={vaginal} "
        f"condom_protected={condom_protected} recipients={recipient_refs}"
    )

    if current_ejaculation is False:
        return {"detected": False, "recipient_refs": recipient_refs}

    explicit_unprotected = _has_explicit_unprotected_internal_ejaculation(actor_response)
    if explicit_unprotected:
        print("[PregnancyMgr] ejac classify override → explicit unprotected internal")
        return {"detected": True, "recipient_refs": recipient_refs}

    if vaginal is None:
        print("[PregnancyMgr] ejac classify fallback → explicit_internal=False")
        return {"detected": False, "recipient_refs": recipient_refs}
    if not vaginal:
        return {"detected": False, "recipient_refs": recipient_refs}
    if condom_protected:
        return {"detected": False, "recipient_refs": recipient_refs}
    return {"detected": True, "recipient_refs": recipient_refs}


async def _get_char_name(char_id: str) -> str:
    """Character 노드에서 이름을 조회합니다. ID 또는 한글 이름 모두 허용."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character) WHERE c.id = $cid OR c.name = $cid RETURN c.name AS name",
            cid=char_id,
        )
        row = await rec.single()
    return (row["name"] if row else None) or char_id


async def _resolve_char_id(name_or_id: str) -> str | None:
    """이름 또는 ID로 실제 Character.id를 반환합니다."""
    async with async_driver.session() as session:
        rec = await session.run(
            "MATCH (c:Character) WHERE c.id = $ref OR c.name = $ref RETURN c.id AS cid",
            ref=name_or_id,
        )
        row = await rec.single()
    return row["cid"] if row else None


async def _resolve_pregnancy_candidate_ids(
    npc_id: str,
    actor_response: str,
    scene_char_ids: list[str] | None,
    intimate_char_ids: list[str] | None,
    classifier_recipient_refs: list[str] | None = None,
) -> list[str]:
    """Resolve canonical character ids that should receive pregnancy probability checks."""
    if intimate_char_ids is not None:
        raw_refs = intimate_char_ids
    elif classifier_recipient_refs:
        raw_refs = classifier_recipient_refs
    else:
        raw_refs = [npc_id, *(scene_char_ids or [])]

    seen: set[str] = set()
    resolved: list[tuple[str, str, str]] = []
    for ref in raw_refs:
        canonical = await _resolve_char_id(ref)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        resolved.append((canonical, ref, await _get_char_name(canonical)))

    if intimate_char_ids is not None:
        return [char_id for char_id, _, _ in resolved]
    if classifier_recipient_refs and resolved:
        return [char_id for char_id, _, _ in resolved]

    if classifier_recipient_refs and not resolved:
        return await _resolve_pregnancy_candidate_ids(
            npc_id,
            actor_response,
            scene_char_ids,
            None,
            None,
        )

    mentioned = [
        char_id
        for char_id, original_ref, char_name in resolved
        if _text_mentions_ref(actor_response, original_ref, char_name)
    ]
    if mentioned:
        return mentioned

    npc_canonical = await _resolve_char_id(npc_id)
    scene_refs = set(scene_char_ids or [])
    scene_only = [
        char_id
        for char_id, original_ref, _ in resolved
        if original_ref in scene_refs and char_id != npc_canonical
    ]
    if len(scene_only) == 1:
        return scene_only

    return [npc_canonical] if npc_canonical else []


async def _get_cycle_state(npc_id: str) -> dict:
    """DynamicState에서 임신/주기 관련 필드 조회. ID 또는 한글 이름 모두 허용."""
    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:HAS_STATE]->(d:DynamicState)
            WHERE c.id = $cid OR c.name = $cid
            RETURN d.cycle_day             AS cycle_day,
                   d.pregnant              AS pregnant,
                   d.pregnancy_day         AS pregnancy_day,
                   d.cum_shots_this_cycle  AS cum_shots,
                   d.has_menstrual_cycle   AS has_menstrual_cycle
        """, cid=npc_id)
        row = await rec.single()
        if not row:
            return {"cycle_day": 1, "pregnant": False, "pregnancy_day": 0, "cum_shots": 0, "has_menstrual_cycle": False}
        raw_cycle = row["has_menstrual_cycle"]
        return {
            "cycle_day":           int(row["cycle_day"]    or 1),
            "pregnant":            bool(row["pregnant"]    or False),
            "pregnancy_day":       int(row["pregnancy_day"] or 0),
            "cum_shots":           int(row["cum_shots"]    or 0),
            "has_menstrual_cycle": False if raw_cycle is None else bool(raw_cycle),
        }


async def process_ejaculation(
    npc_id: str,
    actor_response: str,
    scene_char_ids: list[str] | None = None,
    intimate_char_ids: list[str] | None = None,
    father_id: str | None = None,
) -> str | None:
    """
    질내사정 감지 시 실제 성행위에 참여한 NPC만 임신 확률 계산.

    intimate_char_ids: 실제 성행위 참여자 명시 목록 (없으면 npc_id만 처리).
    scene_char_ids: 씬 존재 캐릭터 전체 — 임신 계산에는 사용하지 않음.
    father_id: 사정한 캐릭터 ID (보통 PC). 임신 확정 시 pregnancy_father_id에 저장.
    Returns:
        임신 확정 시 OOC 메시지 문자열, 아니면 None.
    """
    analysis = await _analyze_internal_ejaculation(actor_response)
    if not analysis.get("detected"):
        return None

    # scene_chars may contain both a Korean name ("민지") and an ID ("minji") for the same
    # character. Resolve and deduplicate before DB writes because update_dynamic_state
    # matches on id, not name.
    candidates = await _resolve_pregnancy_candidate_ids(
        npc_id,
        actor_response,
        scene_char_ids,
        intimate_char_ids,
        analysis.get("recipient_refs") or None,
    )

    for char_id in candidates:
        state = await _get_cycle_state(char_id)
        if not state["has_menstrual_cycle"]:
            continue
        if state["pregnant"]:
            continue

        new_count = state["cum_shots"] + 1
        cycle_day = state["cycle_day"]

        await update_dynamic_state(char_id, {"cum_shots_this_cycle": new_count})

        prob = _calc_prob(cycle_day, new_count)
        roll = random.random()

        print(
            f"[PregnancyMgr] {char_id}: cycle_day={cycle_day} | shots={new_count} | "
            f"prob={prob:.1%} | roll={roll:.3f} | {'임신!' if roll < prob else '미임신'}"
        )

        if roll >= prob:
            continue

        # ── 임신 확정 ──────────────────────────────────────────
        pregnancy_updates: dict = {
            "pregnant":             True,
            "pregnancy_day":        1,
            "cum_shots_this_cycle": 0,
        }
        resolved_father = await _resolve_char_id(father_id) if father_id else None
        if resolved_father:
            pregnancy_updates["pregnancy_father_id"] = resolved_father

        await update_dynamic_state(char_id, pregnancy_updates)

        char_name   = await _get_char_name(char_id)
        father_name = await _get_char_name(resolved_father) if resolved_father else None
        father_part = f" (아버지: {father_name})" if father_name else ""
        ooc_msg = (
            f"*[시스템] {char_name}이(가) 임신했습니다.{father_part} (임신 1일째) "
            f"가임기 {cycle_day}일째, 질내사정 {new_count}회 누적. "
            f"임신 13주(91일) 이후 안정기 진입.*"
        )
        print(f"[PregnancyMgr] {char_id} 임신 확정 → OOC 주입 예약 (father={resolved_father})")
        return ooc_msg

    return None


async def set_pregnant_manual(char_ref: str, father_ref: str | None = None) -> str | None:
    """
    이름 또는 ID로 캐릭터를 직접 임신 상태로 전환합니다 (강제 임신).

    확률·가임 주기를 무시하고 무조건 임신시킨다. father_ref가 주어지면
    아버지(pregnancy_father_id)를 함께 기록한다. 생리 주기가 없던 캐릭터도
    임신 일수가 진행되도록 has_menstrual_cycle을 함께 켠다.
    Returns:
        OOC 확인 메시지 문자열, 캐릭터(엄마)를 찾지 못하면 None.
    """
    char_id = await _resolve_char_id(char_ref)
    if not char_id:
        return None

    state = await _get_cycle_state(char_id)
    if state["pregnant"]:
        char_name = await _get_char_name(char_id)
        return f"*[시스템] {char_name}은(는) 이미 임신 중입니다. (임신 {state['pregnancy_day']}일째)*"

    updates: dict = {
        "pregnant":             True,
        "pregnancy_day":        1,
        "cum_shots_this_cycle": 0,
        "has_menstrual_cycle":  True,
    }
    resolved_father = await _resolve_char_id(father_ref) if father_ref else None
    if resolved_father:
        updates["pregnancy_father_id"] = resolved_father
    await update_dynamic_state(char_id, updates)

    state = await _get_cycle_state(char_id)
    char_name = await _get_char_name(char_id)
    father_name = await _get_char_name(resolved_father) if resolved_father else None
    father_part = f" (아버지: {father_name})" if father_name else ""
    ooc_msg = (
        f"*[시스템] {char_name}이(가) 임신했습니다.{father_part} (임신 1일째) "
        f"강제 설정 — 가임기 {state['cycle_day']}일째. "
        f"임신 13주(91일) 이후 안정기 진입.*"
    )
    print(f"[PregnancyMgr] {char_id} 강제 임신 설정 완료 (father={resolved_father})")
    return ooc_msg


async def simulate_internal_ejaculation(
    mother_ref: str,
    father_ref: str | None = None,
    shots: int = 1,
) -> str | None:
    """
    질내사정 N회를 가정해 가임 주기 기반 확률로 임신 여부를 시뮬레이션한다.

    pregManager 자동 감지가 놓친 상황을 위한 수동 보정. cycle_day와
    누적 사정 횟수(기존 cum_shots + shots)로 누적 확률을 구해 단판 판정한다.
    임신으로 나오면 상태를 반영하고 father_ref를 아버지로 기록한다.
    Returns:
        결과 OOC 메시지(임신/미임신), 엄마를 찾지 못하면 None.
    """
    char_id = await _resolve_char_id(mother_ref)
    if not char_id:
        return None

    shots = max(1, int(shots))
    state = await _get_cycle_state(char_id)
    char_name = await _get_char_name(char_id)

    if not state["has_menstrual_cycle"]:
        return f"*[시스템] {char_name}은(는) 생리 주기가 없어 임신 시뮬레이션 대상이 아닙니다. (강제 임신을 사용하세요.)*"
    if state["pregnant"]:
        return f"*[시스템] {char_name}은(는) 이미 임신 중입니다. (임신 {state['pregnancy_day']}일째)*"

    cycle_day = state["cycle_day"]
    total_shots = state["cum_shots"] + shots
    prob = _calc_prob(cycle_day, total_shots)
    roll = random.random()
    conceived = roll < prob

    print(
        f"[PregnancyMgr] simulate {char_id}: cycle_day={cycle_day} | shots={total_shots} | "
        f"prob={prob:.1%} | roll={roll:.3f} | {'임신!' if conceived else '미임신'}"
    )

    if not conceived:
        await update_dynamic_state(char_id, {"cum_shots_this_cycle": total_shots})
        return (
            f"*[시스템] {char_name} 임신 시뮬레이션 결과 — 미임신. "
            f"가임기 {cycle_day}일째, 질내사정 {total_shots}회 누적 (임신 확률 {prob:.0%}).*"
        )

    pregnancy_updates: dict = {
        "pregnant":             True,
        "pregnancy_day":        1,
        "cum_shots_this_cycle": 0,
    }
    resolved_father = await _resolve_char_id(father_ref) if father_ref else None
    if resolved_father:
        pregnancy_updates["pregnancy_father_id"] = resolved_father
    await update_dynamic_state(char_id, pregnancy_updates)

    father_name = await _get_char_name(resolved_father) if resolved_father else None
    father_part = f" (아버지: {father_name})" if father_name else ""
    print(f"[PregnancyMgr] {char_id} 시뮬레이션 임신 확정 (father={resolved_father})")
    return (
        f"*[시스템] {char_name}이(가) 임신했습니다.{father_part} (임신 1일째) "
        f"임신 시뮬레이션 — 가임기 {cycle_day}일째, 질내사정 {total_shots}회 누적 (임신 확률 {prob:.0%}). "
        f"임신 13주(91일) 이후 안정기 진입.*"
    )


async def tick_pregnancy_day(npc_id: str, days_passed: int) -> None:
    """
    게임 내 날짜 경과 시 pregnancy_day 증가.
    manager_agent에서 days_passed > 0 일 때 호출.
    """
    if days_passed <= 0:
        return

    state = await _get_cycle_state(npc_id)
    if not state["pregnant"]:
        return

    new_day = state["pregnancy_day"] + days_passed
    await update_dynamic_state(npc_id, {"pregnancy_day": new_day})

    trimester = "안정기" if new_day >= 91 else ("초기" if new_day < 42 else "중기")
    print(f"[PregnancyMgr] 임신 {new_day}일째 ({trimester})")


async def tick_cycle_day(npc_id: str, days_passed: int) -> None:
    """단일 NPC의 cycle_day 증가. tick_all_cycles 미지원 환경용 fallback."""
    if days_passed <= 0:
        return

    state = await _get_cycle_state(npc_id)
    if not state["has_menstrual_cycle"]:
        return
    if state["pregnant"]:
        await tick_pregnancy_day(npc_id, days_passed)
        return

    new_cycle_day = ((state["cycle_day"] - 1 + days_passed) % 28) + 1
    updates: dict = {"cycle_day": new_cycle_day}
    # days_passed >= 28이면 날짜 비교와 무관하게 완전한 주기가 경과했으므로 반드시 리셋
    if new_cycle_day < state["cycle_day"] or days_passed >= 28:
        updates["cum_shots_this_cycle"] = 0
    await update_dynamic_state(npc_id, updates)
    print(f"[PregnancyMgr] {npc_id} cycle_day → {new_cycle_day}")


async def tick_all_cycles(days_passed: int) -> None:
    """
    날짜 경과 시 has_menstrual_cycle=true인 모든 캐릭터의 cycle_day를 일괄 갱신.
    주 NPC만 처리하던 기존 방식 대신 이 함수를 사용해야 한다.
    """
    if days_passed <= 0:
        return

    async with async_driver.session() as session:
        rec = await session.run("""
            MATCH (c:Character)-[:HAS_STATE]->(d:DynamicState)
            WHERE d.has_menstrual_cycle IS NULL OR d.has_menstrual_cycle = true
            RETURN c.id                   AS char_id,
                   d.cycle_day            AS cycle_day,
                   d.pregnant             AS pregnant,
                   d.pregnancy_day        AS pregnancy_day,
                   d.cum_shots_this_cycle AS cum_shots
        """)
        rows = await rec.data()

    for row in rows:
        char_id   = row["char_id"]
        cycle_day = int(row["cycle_day"] or 1)
        pregnant  = bool(row["pregnant"] or False)

        if pregnant:
            new_day = int(row["pregnancy_day"] or 0) + days_passed
            await update_dynamic_state(char_id, {"pregnancy_day": new_day})
            trimester = "안정기" if new_day >= 91 else ("초기" if new_day < 42 else "중기")
            print(f"[PregnancyMgr] {char_id} 임신 {new_day}일째 ({trimester})")
        else:
            new_cycle_day = ((cycle_day - 1 + days_passed) % 28) + 1
            updates: dict = {"cycle_day": new_cycle_day}
            # days_passed >= 28이면 날짜 비교와 무관하게 완전한 주기가 경과했으므로 반드시 리셋
            if new_cycle_day < cycle_day or days_passed >= 28:
                updates["cum_shots_this_cycle"] = 0
            await update_dynamic_state(char_id, updates)
            print(f"[PregnancyMgr] {char_id} cycle_day {cycle_day} → {new_cycle_day}")
