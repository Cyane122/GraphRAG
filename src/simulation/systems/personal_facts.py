# ================================
# src/simulation/systems/personal_facts.py
#
# PersonalFact extraction, retrieval, rendering, and commit helpers.
#
# Functions
#   - extract_personal_facts(user_input: str, subject_id: str, audience_id: str, current_dt: datetime, subject_aliases: dict[str, str] | None = None) -> list[dict] : Extract explicit user-stated daily facts
#   - fetch_active_personal_facts(subject_id: str | None, audience_id: str, current_dt: datetime, user_input: str = "", limit: int = 6) -> list[dict] : Fetch prompt-ready active facts
#   - merge_prompt_facts(stored_facts: list[dict], new_facts: list[dict], user_input: str = "", limit: int = 6) -> list[dict] : Merge stored and current-turn facts
#   - render_personal_facts(facts: list[dict]) -> str : Render known facts for Actor prompt injection
#   - commit_personal_facts(facts: list[dict], subject_id: str, audience_id: str, current_dt: datetime | None = None) -> None : Upsert accepted pending facts
# ================================

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta

from src.config import MODEL_STATE_UPDATER
from src.core.database import async_driver
from src.core.llm.client import extract_json_from_llm, get_model, get_response_text


PERSONAL_FACT_CATEGORIES = {
    "household",
    "schedule",
    "absence",
    "preference",
    "promise",
    "boundary",
    "routine",
    "misc",
}

KR_TERMS = {
    "parents": "\ubd80\ubaa8",
    "younger_sibling": "\ub3d9\uc0dd",
    "younger_brother": "\ub0a8\ub3d9\uc0dd",
    "family": "\uac00\uc871",
    "home": "\uc9d1",
    "travel": "\uc5ec\ud589",
    "sleep_over": "\uc678\ubc15",
    "sleep": "\uc790\uace0",
    "friend": "\uce5c\uad6c",
    "tomorrow": "\ub0b4\uc77c",
    "returned": "\ub3cc\uc544\uc624",
    "returned_past": "\ub3cc\uc544\uc654",
    "came": "\uc654\uc5b4",
    "honorific_came": "\uc624\uc168\uc5b4",
}

_FACT_EXTRACT_TIMEOUT_SECONDS = 12
_MAX_FACT_INPUT_LEN = 700
_FACT_INPUT_CONTEXT_CHARS = 180
_MAX_FACT_TEXT_LEN = 180
_FACT_SIGNAL_RE = re.compile(
    "|".join(re.escape(term) for term in KR_TERMS.values())
    + r"|routine|schedule|promise|prefer|family|travel|tomorrow",
    re.IGNORECASE,
)
_RETURNED_RE = re.compile(
    "|".join(
        [
            re.escape(KR_TERMS["returned"]),
            re.escape(KR_TERMS["returned_past"]),
            re.escape("\uadc0\uac00"),
            re.escape(KR_TERMS["came"]),
            re.escape(KR_TERMS["honorific_came"]),
            r"\uc9d1\uc5d0\s*\uc788",
        ]
    )
)


async def extract_personal_facts(
    user_input: str,
    subject_id: str,
    audience_id: str,
    current_dt: datetime,
    subject_aliases: dict[str, str] | None = None,
) -> list[dict]:
    """Extract explicit personal facts from user input without touching the DB."""
    text = (user_input or "").strip()
    if not text or not _FACT_SIGNAL_RE.search(text):
        return []

    aliases = _normalize_subject_aliases(subject_aliases or {}, subject_id)
    facts = _rule_based_household_facts(text, subject_id, audience_id, current_dt, aliases)
    try:
        facts.extend(await _extract_facts_with_llm(text, subject_id, audience_id, current_dt, aliases))
    except Exception as exc:
        print(f"[PersonalFact] extractor fallback only: {exc}")
    return _dedupe_facts(_sanitize_extracted_facts(facts, subject_id, audience_id, current_dt, aliases))


async def fetch_active_personal_facts(
    subject_id: str | None,
    audience_id: str,
    current_dt: datetime,
    user_input: str = "",
    limit: int = 6,
) -> list[dict]:
    """Fetch active facts known by this audience, optionally scoped to one subject."""
    now = current_dt.isoformat()
    try:
        async with async_driver.session() as session:
            subject_filter = "" if subject_id is None else "AND fact.subject_id = $subject_id"
            query = """
                MATCH (aud:Character {id: $audience_id})-[:KNOWS_FACT]->(fact:PersonalFact)
                WHERE fact.audience_id = $audience_id
                  __SUBJECT_FILTER__
                  AND fact.status = 'active'
                  AND (fact.valid_until IS NULL OR fact.valid_until = '' OR fact.valid_until >= $now)
                RETURN fact.id AS id,
                       fact.subject_id AS subject_id,
                       fact.audience_id AS audience_id,
                       fact.category AS category,
                       fact.fact_text AS fact_text,
                       fact.normalized_key AS normalized_key,
                       fact.status AS status,
                       fact.valid_from AS valid_from,
                       fact.valid_until AS valid_until,
                       fact.confidence AS confidence,
                       fact.source AS source,
                       fact.created_at AS created_at,
                       fact.updated_at AS updated_at
                ORDER BY fact.updated_at DESC
                LIMIT 16
                """.replace("__SUBJECT_FILTER__", subject_filter)
            params = {"audience_id": audience_id, "now": now}
            if subject_id is not None:
                params["subject_id"] = subject_id
            result = await session.run(query, **params)
            rows = await result.data()
    except Exception as exc:
        print(f"[PersonalFact] active fact fetch skipped: {exc}")
        return []
    return _rank_facts(rows, user_input, limit)


def merge_prompt_facts(
    stored_facts: list[dict],
    new_facts: list[dict],
    user_input: str = "",
    limit: int = 6,
) -> list[dict]:
    """Merge committed facts with current-turn facts before DB commit."""
    by_key: dict[str, dict] = {}
    for fact in stored_facts + new_facts:
        if fact.get("status") != "active":
            continue
        key = _fact_merge_key(fact)
        if key:
            by_key[key] = fact
    return _rank_facts(list(by_key.values()), user_input, limit)


def render_personal_facts(facts: list[dict]) -> str:
    """Render facts as a compact Actor-facing block."""
    active = [fact for fact in facts if fact.get("status") == "active" and fact.get("fact_text")]
    if not active:
        return ""
    lines = [
        "[Known Personal Facts]",
        "- Treat these only as facts this NPC personally knows; do not share them with other NPCs unless told.",
    ]
    for fact in active[:6]:
        category = fact.get("category") or "misc"
        subject = fact.get("subject_id") or "unknown"
        valid_until = fact.get("valid_until")
        suffix = f" (valid until {valid_until})" if valid_until else ""
        lines.append(f"- [{category} / subject={subject}] {fact['fact_text']}{suffix}")
    return "\n".join(lines)


async def commit_personal_facts(
    facts: list[dict],
    subject_id: str,
    audience_id: str,
    current_dt: datetime | None = None,
) -> None:
    """Upsert accepted pending facts for the current subject/audience pair."""
    if not facts:
        return
    base_dt = current_dt or datetime.now()
    now = base_dt.isoformat()
    known_subjects = {
        str(fact.get("subject_id")): str(fact.get("subject_id"))
        for fact in facts
        if isinstance(fact, dict) and fact.get("subject_id")
    }
    clean_facts = _sanitize_extracted_facts(facts, subject_id, audience_id, base_dt, known_subjects)
    for fact in clean_facts:
        await _upsert_personal_fact(fact, now)


async def _extract_facts_with_llm(
    user_input: str,
    subject_id: str,
    audience_id: str,
    current_dt: datetime,
    subject_aliases: dict[str, str],
) -> list[dict]:
    """Use the lightweight model to extract explicit facts as JSON."""
    system_prompt = "Extract explicit personal daily-life facts from Korean roleplay input. No inference."
    subjects = _render_subject_options(subject_aliases, subject_id)
    fact_input = _compact_fact_input(user_input)
    prompt = f"""Return ONLY valid JSON.
Explicit facts only. Skip emotions/metaphors/flirting/scene actions unless durable & practical.

[Time] {current_dt.strftime("%Y-%m-%d %H:%M")}
[Subject] default={subject_id}. Use other id only when explicitly named.
[Subjects/aliases] {subjects}
[Audience] {audience_id}
[Categories] household/schedule/absence/preference/promise/boundary/routine/misc

[Rules]
- subject_id: allowed ids only. audience_id: "{audience_id}".
- normalized_key: stable snake_case (e.g. "household.parents_absent"). Contradiction→reuse same key.
- Normalize relative dates to current time. valid_until: ISO8601 or null.
- "today/tonight" absence/schedule → valid_until=tomorrow 12:00 unless stated otherwise.
- confidence: 0.0-1.0

[Input] {fact_input}

[Output] {{"facts":[{{"category":"household","fact_text":"...","normalized_key":"household.x","subject_id":"{subject_id}","audience_id":"{audience_id}","valid_until":null,"confidence":0.9}}]}}
"""
    model = get_model(MODEL_STATE_UPDATER, system_prompt=system_prompt)
    response = await asyncio.wait_for(
        model.generate_content_async(
            prompt,
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 1024,
                "thinking_config": {"thinking_budget": 0},
                "response_mime_type": "application/json",
            },
        ),
        timeout=_FACT_EXTRACT_TIMEOUT_SECONDS,
    )
    raw = get_response_text(response)
    if not raw:
        return []
    parsed = extract_json_from_llm(raw, source="personal_fact_extractor")
    if not isinstance(parsed, dict):
        return []
    facts = parsed.get("facts")
    return facts if isinstance(facts, list) else []


def _compact_fact_input(user_input: str) -> str:
    """Return a compact excerpt around fact signals for the extractor prompt."""
    text = (user_input or "").strip()
    if len(text) <= _MAX_FACT_INPUT_LEN:
        return text

    match = _FACT_SIGNAL_RE.search(text)
    if not match:
        return text[:_MAX_FACT_INPUT_LEN]

    start = max(0, match.start() - _FACT_INPUT_CONTEXT_CHARS)
    end = min(len(text), match.end() + _FACT_INPUT_CONTEXT_CHARS)
    excerpt = text[start:end].strip()
    return excerpt[:_MAX_FACT_INPUT_LEN]


def _rule_based_household_facts(
    text: str,
    subject_id: str,
    audience_id: str,
    current_dt: datetime,
    subject_aliases: dict[str, str],
) -> list[dict]:
    """Handle common household absence facts even if the LLM is unavailable."""
    facts: list[dict] = []
    detected_subject = _detect_explicit_subject(text, subject_aliases) or subject_id
    valid_until = _infer_valid_until(text, current_dt)

    if KR_TERMS["parents"] in text and (
        KR_TERMS["travel"] in text
        or KR_TERMS["tomorrow"] in text
        or "\uc5c6" in text
    ):
        facts.append({
            "category": "household",
            "fact_text": (
                "PC's parents are away from home and are expected to return tomorrow."
                if KR_TERMS["tomorrow"] in text
                else "PC's parents are currently away from home."
            ),
            "normalized_key": "household.parents_absent",
            "subject_id": detected_subject,
            "audience_id": audience_id,
            "valid_until": valid_until,
            "confidence": 0.8,
        })

    if (
        KR_TERMS["younger_brother"] in text
        or KR_TERMS["younger_sibling"] in text
    ) and (
        KR_TERMS["friend"] in text
        or KR_TERMS["sleep"] in text
        or KR_TERMS["sleep_over"] in text
    ):
        fact_text = (
            "PC's younger brother is sleeping over at a friend's house."
            if detected_subject == subject_id
            else f"{detected_subject}'s younger sibling is sleeping away from home tonight."
        )
        facts.append({
            "category": "household",
            "fact_text": fact_text,
            "normalized_key": (
                "household.younger_brother_absent"
                if detected_subject == subject_id
                else f"household.{detected_subject}.younger_sibling_absent"
            ),
            "subject_id": detected_subject,
            "audience_id": audience_id,
            "valid_until": valid_until,
            "confidence": 0.8,
        })

    if KR_TERMS["parents"] in text and _RETURNED_RE.search(text):
        facts.append({
            "category": "household",
            "fact_text": "PC's parents have returned home.",
            "normalized_key": "household.parents_absent",
            "subject_id": detected_subject,
            "audience_id": audience_id,
            "valid_until": current_dt.isoformat(),
            "confidence": 0.9,
        })
    return facts


def _sanitize_extracted_facts(
    facts: list[dict],
    subject_id: str,
    audience_id: str,
    current_dt: datetime,
    subject_aliases: dict[str, str] | None = None,
) -> list[dict]:
    """Normalize extractor output into DB-safe fact dictionaries."""
    allowed_subjects = set((subject_aliases or {}).values()) | {subject_id}
    sanitized: list[dict] = []
    for raw in facts:
        if not isinstance(raw, dict):
            continue
        fact_text = str(raw.get("fact_text") or "").strip()
        raw_subject_id = str(raw.get("subject_id") or subject_id).strip()
        fact_subject_id = raw_subject_id if raw_subject_id in allowed_subjects else subject_id
        normalized_key = _normalize_key(raw.get("normalized_key"), raw.get("category"), fact_text)
        if not fact_text or not normalized_key:
            continue
        category = str(raw.get("category") or "misc").strip().lower()
        if category not in PERSONAL_FACT_CATEGORIES:
            category = "misc"
        status = _infer_status(raw, category, normalized_key, fact_text)
        sanitized.append({
            "id": _fact_id(fact_subject_id, audience_id, normalized_key),
            "subject_id": fact_subject_id,
            "audience_id": audience_id,
            "category": category,
            "fact_text": fact_text[:_MAX_FACT_TEXT_LEN],
            "normalized_key": normalized_key,
            "status": status,
            "valid_from": str(raw.get("valid_from") or current_dt.isoformat()),
            "valid_until": _valid_until(raw.get("valid_until")),
            "confidence": _confidence(raw.get("confidence")),
            "source": str(raw.get("source") or "user_input")[:60],
        })
    return sanitized


def _infer_status(raw: dict, category: str, normalized_key: str, fact_text: str) -> str:
    """Infer whether a fact supersedes an older active fact."""
    explicit = str(raw.get("status") or "").strip().lower()
    if explicit in {"active", "expired", "superseded"}:
        return explicit
    if (category == "absence" or "absent" in normalized_key) and _RETURNED_RE.search(fact_text):
        return "expired"
    return "active"


def _normalize_key(raw_key: object, category: object, fact_text: str) -> str:
    """Return a stable dotted key for upsert matching."""
    key = str(raw_key or "").strip().lower()
    key = re.sub(r"[^a-z0-9_.]+", "_", key).strip("._")
    if "." in key:
        return key[:120]
    prefix = str(category or "misc").strip().lower()
    prefix = prefix if prefix in PERSONAL_FACT_CATEGORIES else "misc"
    if key:
        return f"{prefix}.{key}"[:120]
    digest = hashlib.blake2s(fact_text.encode("utf-8"), digest_size=5).hexdigest()
    return f"{prefix}.{digest}"


def _fact_id(subject_id: str, audience_id: str, normalized_key: str) -> str:
    """Create a deterministic id for the subject/audience/key tuple."""
    digest = hashlib.blake2s(
        f"{subject_id}|{audience_id}|{normalized_key}".encode("utf-8"),
        digest_size=8,
    ).hexdigest()
    return f"pf_{digest}"


def _valid_until(value: object) -> str | None:
    """Normalize valid_until values to an ISO string or None."""
    if value in (None, "", "null"):
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        return text[:40]


def _confidence(value: object) -> float:
    """Clamp confidence to 0..1."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.7


def _normalize_subject_aliases(subject_aliases: dict[str, str], default_subject_id: str) -> dict[str, str]:
    """Return a non-empty alias map that always includes the default subject id."""
    aliases = {
        str(alias).strip(): str(char_id).strip()
        for alias, char_id in subject_aliases.items()
        if str(alias).strip() and str(char_id).strip()
    }
    aliases.setdefault(default_subject_id, default_subject_id)
    return aliases


def _detect_explicit_subject(text: str, subject_aliases: dict[str, str]) -> str | None:
    """Detect a named subject from the user text using the world alias map."""
    matches = [(alias, char_id) for alias, char_id in subject_aliases.items() if alias and alias in text]
    if not matches:
        return None
    matches.sort(key=lambda item: len(item[0]), reverse=True)
    return matches[0][1]


def _infer_valid_until(text: str, current_dt: datetime) -> str | None:
    """Infer a conservative TTL for temporary daily-life facts."""
    lowered = text.lower()
    if KR_TERMS["tomorrow"] in text:
        tomorrow = current_dt + timedelta(days=1)
        return tomorrow.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
    if "\uc624\ub298" in text or "tonight" in lowered or KR_TERMS["sleep_over"] in text:
        tomorrow = current_dt + timedelta(days=1)
        return tomorrow.replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
    return None


def _render_subject_options(subject_aliases: dict[str, str], default_subject_id: str) -> str:
    """Render compact subject options for the extractor prompt."""
    by_id: dict[str, list[str]] = {}
    for alias, char_id in subject_aliases.items():
        by_id.setdefault(char_id, []).append(alias)
    by_id.setdefault(default_subject_id, [default_subject_id])
    return "\n".join(
        f"- {char_id}: {', '.join(sorted(set(aliases))[:8])}"
        for char_id, aliases in sorted(by_id.items())
    )


def _dedupe_facts(facts: list[dict]) -> list[dict]:
    """Keep the last fact for each subject/key pair."""
    by_key: dict[str, dict] = {}
    for fact in facts:
        by_key[_fact_merge_key(fact)] = fact
    return list(by_key.values())


def _fact_merge_key(fact: dict) -> str:
    """Return a semantic merge key so rule fallback and LLM facts do not duplicate."""
    subject = str(fact.get("subject_id") or "")
    normalized_key = str(fact.get("normalized_key") or fact.get("id") or "")
    lowered = normalized_key.lower()
    if any(token in lowered for token in ("younger_brother_absent", "younger_sibling_absent", "sibling_staying_out")):
        return f"{subject}|household.younger_sibling_absent"
    if "parents_absent" in lowered:
        return f"{subject}|household.parents_absent"
    return f"{subject}|{normalized_key}"


def _rank_facts(facts: list[dict], user_input: str, limit: int) -> list[dict]:
    """Rank facts by category/key lexical overlap and recency."""
    query = set(re.findall(r"[\w\uac00-\ud7a3]+", (user_input or "").lower()))

    def score(fact: dict) -> tuple[int, str]:
        haystack = " ".join(
            str(fact.get(key) or "").lower()
            for key in ("category", "normalized_key", "fact_text")
        )
        overlap = sum(1 for token in query if len(token) > 1 and token in haystack)
        updated = str(fact.get("updated_at") or fact.get("created_at") or fact.get("valid_from") or "")
        return overlap, updated

    return sorted(facts, key=score, reverse=True)[:limit]


async def _upsert_personal_fact(fact: dict, now: str) -> None:
    """Apply one PersonalFact upsert or expiration to Kuzu."""
    async with async_driver.session() as session:
        existing = await session.run(
            "MATCH (fact:PersonalFact {id: $id}) RETURN fact.id AS id",
            id=fact["id"],
        )
        exists = await existing.single() is not None

        if fact["status"] in {"expired", "superseded"}:
            if exists:
                await session.run(
                    """
                    MATCH (fact:PersonalFact {id: $id})
                    SET fact.status = $status,
                        fact.fact_text = $fact_text,
                        fact.valid_until = $valid_until,
                        fact.updated_at = $updated_at
                    """,
                    id=fact["id"],
                    status=fact["status"],
                    fact_text=fact["fact_text"],
                    valid_until=fact.get("valid_until") or now,
                    updated_at=now,
                )
            return

        if exists:
            await session.run(
                """
                MATCH (fact:PersonalFact {id: $id})
                SET fact.category = $category,
                    fact.fact_text = $fact_text,
                    fact.status = 'active',
                    fact.valid_from = $valid_from,
                    fact.valid_until = $valid_until,
                    fact.confidence = $confidence,
                    fact.source = $source,
                    fact.updated_at = $updated_at
                """,
                id=fact["id"],
                category=fact["category"],
                fact_text=fact["fact_text"],
                valid_from=fact["valid_from"],
                valid_until=fact.get("valid_until"),
                confidence=fact["confidence"],
                source=fact["source"],
                updated_at=now,
            )
            return

        await session.run(
            """
            MATCH (aud:Character {id: $audience_id})
            CREATE (fact:PersonalFact {
                id: $id,
                subject_id: $subject_id,
                audience_id: $audience_id,
                category: $category,
                fact_text: $fact_text,
                normalized_key: $normalized_key,
                status: 'active',
                valid_from: $valid_from,
                valid_until: $valid_until,
                confidence: $confidence,
                source: $source,
                created_at: $created_at,
                updated_at: $updated_at
            })
            CREATE (aud)-[:KNOWS_FACT]->(fact)
            """,
            id=fact["id"],
            subject_id=fact["subject_id"],
            audience_id=fact["audience_id"],
            category=fact["category"],
            fact_text=fact["fact_text"],
            normalized_key=fact["normalized_key"],
            valid_from=fact["valid_from"],
            valid_until=fact.get("valid_until"),
            confidence=fact["confidence"],
            source=fact["source"],
            created_at=now,
            updated_at=now,
        )
