# ================================
# src/apps/world_editor/state_normalize.py
#
# Normalize DynamicState scalar fields (ints/bools) coming from the world editor
# into Kuzu-compatible values before they are written back into world source files.
#
# Functions
#   - normalize_state_fields(fields: dict) -> dict : Normalize known DynamicState scalar fields
#   - normalize_cfg_state_values(values: dict) -> dict : Normalize the state section inside a cfg dict
# ================================

from __future__ import annotations

_STATE_INT_FIELDS: tuple[str, ...] = (
    "stress_level",
    "cycle_day",
    "workplace_stress_level",
    "pregnancy_day",
    "cum_shots_this_cycle",
)


_STATE_INT_DEFAULTS: dict[str, int] = {
    "stress_level": 0,
    "cycle_day": 1,
    "workplace_stress_level": 0,
    "pregnancy_day": 0,
    "cum_shots_this_cycle": 0,
}


_STATE_BOOL_FIELDS: tuple[str, ...] = (
    "has_menstrual_cycle",
    "pregnant",
)


def _coerce_state_int_value(field: str, value: object) -> int | object:
    """DynamicState 정수 필드의 빈 문자열과 숫자 문자열을 int 값으로 정규화합니다."""
    if field not in _STATE_INT_FIELDS:
        return value
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return _STATE_INT_DEFAULTS[field]
        try:
            return int(raw)
        except ValueError:
            return value
    return value


def _coerce_state_bool_value(field: str, value: object) -> bool | object:
    """DynamicState boolean 필드의 빈 문자열과 boolean 문자열을 bool 값으로 정규화합니다."""
    if field not in _STATE_BOOL_FIELDS:
        return value
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip().lower()
        if not raw:
            return False
        if raw == "true":
            return True
        if raw == "false":
            return False
    return value


def normalize_state_fields(fields: dict) -> dict:
    """DynamicState 저장 payload의 알려진 scalar 필드를 Kuzu 호환 값으로 정리합니다."""
    normalized: dict = {}
    for key, value in fields.items():
        value = _coerce_state_int_value(key, value)
        value = _coerce_state_bool_value(key, value)
        normalized[key] = value
    return normalized


def normalize_cfg_state_values(values: dict) -> dict:
    """DEFAULT_CFG/SCENARIO_OVERRIDES 내부 state 섹션의 scalar 필드를 정규화합니다."""
    normalized = dict(values)
    state_values = normalized.get("state")
    if isinstance(state_values, dict):
        normalized["state"] = normalize_state_fields(state_values)
    return normalized
