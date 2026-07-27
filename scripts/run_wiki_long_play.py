# ================================
# scripts/run_wiki_long_play.py
#
# 작성된 Markdown 턴 스크립트를 따라 Wiki 장기 플레이를 무인 실행하고 deferred commit 안전성·LLM 지표를 수집합니다.
#
# Functions
#   - _scenario_choices() -> tuple[str, ...] : 장기 플레이 기본 시나리오 목록을 반환합니다.
#   - _empty_token_counter() -> dict[str, int] : 토큰 합계/known/unknown 카운터 초기값을 반환합니다.
#   - _empty_llm_bucket() -> dict[str, object] : 호출 수·지연·토큰 집계 버킷 초기값을 반환합니다.
#   - _append_token_value(bucket: dict[str, object], key: str, value: object) -> None : null을 unknown으로 유지하며 토큰 집계를 갱신합니다.
#   - _bucket_json(bucket: dict[str, object]) -> dict[str, object] : 내부 집계 버킷을 JSON-직렬화 가능한 dict로 변환합니다.
#   - _merge_bucket(target: dict[str, object], source: dict[str, object]) -> None : 두 LLM 집계 버킷을 합칩니다.
#   - _changed_documents(before: dict[str, str], after: dict[str, str]) -> list[str] : 두 canonical snapshot 사이에서 바뀐 문서 경로를 반환합니다.
#   - _trim_surrounding_blank_lines(lines: Sequence[str]) -> str : 턴 본문 주변의 blank line만 제거하고 내부 줄바꿈은 보존합니다.
#   - _read_turn_script(path: Path) -> list[dict[str, object]] : H2 기반 Markdown 턴 스크립트를 순서대로 파싱합니다.
#   - _render_init_template(scenario_id: str, turn_count: int = 20) -> str : 빈 장기 플레이 스크립트 템플릿을 렌더링합니다.
#   - _init_scripts(script_root: Path, scenarios: Sequence[str]) -> dict[str, list[str]] : 없는 시나리오 스크립트만 생성하고 created/skipped 목록을 반환합니다.
#   - _log_size(path: Path) -> int : 없는 파일을 허용하며 현재 로그 바이트 길이를 반환합니다.
#   - _slice_llm_log(path: Path, start_offset: int, end_offset: int | None = None) -> list[dict[str, object]] : append-only LLM 로그의 특정 바이트 구간만 JSONL로 파싱합니다.
#   - _summarize_llm_calls(rows: Sequence[dict[str, object]]) -> dict[str, object] : raw LLM 호출을 log_source 및 model 기준으로 집계합니다.
#   - _load_price_table(path: Path | None) -> dict[str, dict[str, float]] | None : 모델별 입력/출력 백만 토큰 가격표를 읽습니다.
#   - _cost_for_bucket(bucket: dict[str, object], price: dict[str, float]) -> dict[str, object] : prompt/output 토큰 known 합계로 비용을 계산합니다.
#   - _format_token_counter(counter: dict[str, object]) -> str : known/unknown 토큰 집계를 Markdown 한 줄로 포맷합니다.
#   - _format_currency(value: float | None) -> str : USD 비용 값을 사람이 읽기 쉬운 문자열로 포맷합니다.
#   - _render_scenario_summary(result: dict[str, object], price_table: dict[str, dict[str, float]] | None) -> str : 시나리오 단위 Markdown 요약을 렌더링합니다.
#   - _render_report(run_root: Path, scenario_results: Sequence[dict[str, object]], price_table: dict[str, dict[str, float]] | None) -> str : 전체 장기 플레이 집계 Markdown 리포트를 렌더링합니다.
#   - _simulate_applied_documents(thread_root: Path, thread_id: str) -> dict[str, str] : pending commit를 복제 thread에만 적용한 뒤 canonical snapshot을 반환합니다.
#   - _turn_actor_response(events: Sequence[dict[str, object]]) -> str : complete 이벤트에서 현재 턴 assistant 응답 본문을 추출합니다.
#   - _runtime_modules() -> tuple[object, object, type[object]] : 실제 실행 시 필요한 앱 모듈과 ConversationStore 클래스를 지연 import합니다.
#   - _run_one_scenario(scenario_id: str, script_path: Path, turns: Sequence[dict[str, object]], actor_model: str | None, scenario_root: Path, price_table: dict[str, dict[str, float]] | None, stop_on_error: bool) -> dict[str, object] : 한 시나리오의 장기 플레이와 artifact 기록을 실행합니다.
#   - _run_long_play(scenarios: Sequence[str], script_root: Path, output_root: Path, actor_model: str | None, max_turns: int | None, price_table: dict[str, dict[str, float]] | None, stop_on_error: bool) -> Path : 선택된 시나리오들을 순차 실행하고 집계 report를 갱신합니다.
#   - _parse_args() -> argparse.Namespace : CLI 인자를 파싱합니다.
#   - main() -> None : 장기 플레이를 실행하거나 템플릿 스크립트를 초기화합니다.
# ================================

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.wiki_validation_common import (
    canonical_documents,
    patch_vault_root,
    render_document_diff,
    write_json,
)


def _scenario_choices() -> tuple[str, ...]:
    """Return the supported babe_university long-play scenarios."""
    return ("lover", "best_friends", "amputee_fwb", "ntr_lite", "altered")


def _empty_token_counter() -> dict[str, int]:
    """Return an empty token sum/known/unknown counter."""
    return {"sum": 0, "known_calls": 0, "unknown_calls": 0}


def _empty_llm_bucket() -> dict[str, object]:
    """Return one empty latency/token aggregation bucket."""
    return {
        "call_count": 0,
        "total_elapsed_ms": 0,
        "max_elapsed_ms": 0,
        "prompt_tokens": _empty_token_counter(),
        "output_tokens": _empty_token_counter(),
        "thought_tokens": _empty_token_counter(),
        "total_tokens": _empty_token_counter(),
    }


def _append_token_value(bucket: dict[str, object], key: str, value: object) -> None:
    """Update one token counter, treating null or invalid values as unknown."""
    counter = bucket[key]
    assert isinstance(counter, dict)
    if value is None:
        counter["unknown_calls"] += 1
        return
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        counter["unknown_calls"] += 1
        return
    counter["sum"] += numeric
    counter["known_calls"] += 1


def _bucket_json(bucket: dict[str, object]) -> dict[str, object]:
    """Convert one mutable aggregation bucket into a JSON-safe copy."""
    return {
        "call_count": int(bucket["call_count"]),
        "total_elapsed_ms": int(bucket["total_elapsed_ms"]),
        "max_elapsed_ms": int(bucket["max_elapsed_ms"]),
        "prompt_tokens": dict(bucket["prompt_tokens"]),
        "output_tokens": dict(bucket["output_tokens"]),
        "thought_tokens": dict(bucket["thought_tokens"]),
        "total_tokens": dict(bucket["total_tokens"]),
    }


def _merge_bucket(target: dict[str, object], source: dict[str, object]) -> None:
    """Merge one source bucket into the target bucket in place."""
    target["call_count"] = int(target["call_count"]) + int(source["call_count"])
    target["total_elapsed_ms"] = int(target["total_elapsed_ms"]) + int(source["total_elapsed_ms"])
    target["max_elapsed_ms"] = max(
        int(target["max_elapsed_ms"]),
        int(source["max_elapsed_ms"]),
    )
    for token_key in (
        "prompt_tokens",
        "output_tokens",
        "thought_tokens",
        "total_tokens",
    ):
        target_counter = target[token_key]
        source_counter = source[token_key]
        assert isinstance(target_counter, dict)
        assert isinstance(source_counter, dict)
        target_counter["sum"] += int(source_counter["sum"])
        target_counter["known_calls"] += int(source_counter["known_calls"])
        target_counter["unknown_calls"] += int(source_counter["unknown_calls"])


def _changed_documents(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Return canonical document paths whose content changed between snapshots."""
    return sorted(
        document
        for document in set(before) | set(after)
        if before.get(document) != after.get(document)
    )


def _trim_surrounding_blank_lines(lines: Sequence[str]) -> str:
    """Drop only leading/trailing blank lines while preserving inner content."""
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[start:end])


def _read_turn_script(path: Path) -> list[dict[str, object]]:
    """Parse one Markdown turn script using H2 headings as turn boundaries."""
    turns: list[dict[str, object]] = []
    current_label: str | None = None
    current_lines: list[str] = []
    current_index = 0

    def _flush_current() -> None:
        """Persist the current turn buffer, trimming surrounding blank lines."""
        nonlocal current_label, current_lines, current_index
        if current_label is None:
            return
        body = _trim_surrounding_blank_lines(current_lines)
        current_index += 1
        turns.append({
            "turn_index": current_index,
            "turn_label": current_label,
            "user_input": body,
            "is_empty": body == "",
        })
        current_label = None
        current_lines = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("## "):
            _flush_current()
            current_label = raw_line[3:].strip()
            current_lines = []
            continue
        if current_label is not None:
            current_lines.append(raw_line)
    _flush_current()
    return turns


def _render_init_template(scenario_id: str, turn_count: int = 20) -> str:
    """Render one empty long-play authoring template for a scenario."""
    lines = [
        f"# Wiki Long Play Script: {scenario_id}",
        "",
        "Author notes in this preamble are ignored by the harness.",
        "",
        "How turns work:",
        "- Each `## ` heading starts one turn.",
        "- Everything under that heading until the next `## ` is sent verbatim as the user input for that turn.",
        "- Surrounding blank lines are stripped.",
        "- If a turn body is left empty, the harness records it as skipped and does not send an empty input.",
        "",
        "Fill in the turn bodies yourself. Do not use JSON escaping here; plain Markdown prose is expected.",
        "",
    ]
    for index in range(1, turn_count + 1):
        lines.extend((f"## Turn {index:02d}", "", ""))
    return "\n".join(lines)


def _init_scripts(script_root: Path, scenarios: Sequence[str]) -> dict[str, list[str]]:
    """Create missing scenario templates and report which files were created or skipped."""
    script_root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    skipped: list[str] = []
    for scenario_id in scenarios:
        path = script_root / f"{scenario_id}.md"
        if path.exists():
            skipped.append(path.as_posix())
            continue
        path.write_text(_render_init_template(scenario_id), encoding="utf-8")
        created.append(path.as_posix())
    return {"created": created, "skipped": skipped}


def _log_size(path: Path) -> int:
    """Return the current log size in bytes, tolerating a missing file."""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _slice_llm_log(
    path: Path,
    start_offset: int,
    end_offset: int | None = None,
) -> list[dict[str, object]]:
    """Parse JSONL rows from one appended byte window of the latency log."""
    if not path.exists():
        return []
    file_size = path.stat().st_size
    if end_offset is None:
        end_offset = file_size
    if start_offset >= file_size or end_offset <= start_offset:
        return []
    end_offset = min(end_offset, file_size)
    with path.open("rb") as handle:
        handle.seek(start_offset)
        chunk = handle.read(end_offset - start_offset)
    if not chunk:
        return []
    if chunk[-1:] != b"\n":
        last_newline = chunk.rfind(b"\n")
        if last_newline < 0:
            return []
        chunk = chunk[:last_newline]
    rows: list[dict[str, object]] = []
    for line in chunk.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _summarize_llm_calls(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    """Aggregate raw LLM call rows by log_source and by log_source/model."""
    by_source: dict[str, dict[str, object]] = {}
    by_source_model: dict[str, dict[str, object]] = {}
    for row in rows:
        source = str(row.get("log_source") or "unknown")
        model = str(row.get("model") or "unknown")
        source_bucket = by_source.setdefault(source, _empty_llm_bucket())
        source_model_bucket = by_source_model.setdefault(
            f"{source}::{model}",
            _empty_llm_bucket(),
        )
        for bucket in (source_bucket, source_model_bucket):
            bucket["call_count"] = int(bucket["call_count"]) + 1
            elapsed_ms = int(row.get("elapsed_ms") or 0)
            bucket["total_elapsed_ms"] = int(bucket["total_elapsed_ms"]) + elapsed_ms
            bucket["max_elapsed_ms"] = max(int(bucket["max_elapsed_ms"]), elapsed_ms)
            for token_key in (
                "prompt_tokens",
                "output_tokens",
                "thought_tokens",
                "total_tokens",
            ):
                _append_token_value(bucket, token_key, row.get(token_key))
    source_json = {
        source: _bucket_json(bucket)
        for source, bucket in sorted(by_source.items())
    }
    source_model_json: dict[str, object] = {}
    for composite_key, bucket in sorted(by_source_model.items()):
        source, model = composite_key.split("::", 1)
        source_model_json[composite_key] = {
            "log_source": source,
            "model": model,
            **_bucket_json(bucket),
        }
    return {
        "call_count": len(rows),
        "by_source": source_json,
        "by_source_model": source_model_json,
    }


def _load_price_table(path: Path | None) -> dict[str, dict[str, float]] | None:
    """Load per-model per-million-token input/output prices when provided."""
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("price table must be a JSON object keyed by model name")
    table: dict[str, dict[str, float]] = {}
    for model, values in payload.items():
        if not isinstance(values, dict):
            raise ValueError(f"price table entry for {model!r} must be an object")
        input_price = values.get("input_per_million", values.get("input"))
        output_price = values.get("output_per_million", values.get("output"))
        if input_price is None or output_price is None:
            raise ValueError(
                f"price table entry for {model!r} must define input/output per-million prices"
            )
        table[str(model)] = {
            "input_per_million": float(input_price),
            "output_per_million": float(output_price),
        }
    return table


def _cost_for_bucket(
    bucket: dict[str, object],
    price: dict[str, float],
) -> dict[str, object]:
    """Compute cost from known prompt/output token totals for one aggregated bucket."""
    prompt_tokens = bucket["prompt_tokens"]
    output_tokens = bucket["output_tokens"]
    assert isinstance(prompt_tokens, dict)
    assert isinstance(output_tokens, dict)
    prompt_cost = (
        int(prompt_tokens["sum"]) / 1_000_000 * float(price["input_per_million"])
    )
    output_cost = (
        int(output_tokens["sum"]) / 1_000_000 * float(price["output_per_million"])
    )
    return {
        "usd": prompt_cost + output_cost,
        "prompt_unknown_calls": int(prompt_tokens["unknown_calls"]),
        "output_unknown_calls": int(output_tokens["unknown_calls"]),
    }


def _format_token_counter(counter: dict[str, object]) -> str:
    """Format one token counter while preserving unknown-call visibility."""
    text = f"{int(counter['sum'])}"
    unknown_calls = int(counter["unknown_calls"])
    if unknown_calls:
        text += f" (unknown in {unknown_calls} calls)"
    return text


def _format_currency(value: float | None) -> str:
    """Format a USD value or missing cost for Markdown output."""
    if value is None:
        return "n/a"
    return f"${value:.6f}"


def _render_scenario_summary(
    result: dict[str, object],
    price_table: dict[str, dict[str, float]] | None,
) -> str:
    """Render one scenario's Markdown summary artifact."""
    turns = result["turns"]
    assert isinstance(turns, list)
    lines = [
        f"# Wiki Long Play Summary — {result['scenario_id']}",
        "",
        f"- thread id: `{result['thread_id']}`",
        f"- actor model: `{result['actor_model']}`",
        f"- script path: `{result['script_path']}`",
        f"- turns attempted: `{result['turns_attempted']}`",
        f"- turns completed: `{result['turns_completed']}`",
        f"- turns failed: `{result['turns_failed']}`",
        f"- turns skipped: `{result['turns_skipped']}`",
        f"- deferred invariant violations: `{result['deferred_invariant_violations']}`",
        f"- final apply status: `{result['final_apply_status']}`",
        f"- abandoned: `{result['abandoned']}`",
    ]
    if result.get("abandon_reason"):
        lines.append(f"- abandon reason: `{result['abandon_reason']}`")
    if result.get("final_apply_error"):
        lines.append(f"- final apply error: `{result['final_apply_error']}`")
    lines.extend(("", "## Turns", ""))
    for turn in turns:
        assert isinstance(turn, dict)
        lines.append(
            f"- T{int(turn['turn_index']):02d} `{turn['turn_label']}`: "
            f"status=`{turn['status']}` update=`{turn['wiki_update_status']}` "
            f"patches=`{turn['pending_patch_count']}` creations=`{turn['pending_creation_count']}` "
            f"invariant=`{turn['canonical_unchanged_during_generation']}`"
        )
    summary = result["llm_summary"]
    assert isinstance(summary, dict)
    by_source_model = summary["by_source_model"]
    assert isinstance(by_source_model, dict)
    if by_source_model:
        lines.extend(("", "## LLM Totals", ""))
        for entry in by_source_model.values():
            assert isinstance(entry, dict)
            cost_text = "n/a"
            if price_table is not None:
                price = price_table.get(str(entry["model"]))
                if price is not None:
                    cost_text = _format_currency(_cost_for_bucket(entry, price)["usd"])
            lines.append(
                f"- `{entry['log_source']}` / `{entry['model']}`: "
                f"calls=`{entry['call_count']}` "
                f"prompt=`{_format_token_counter(entry['prompt_tokens'])}` "
                f"output=`{_format_token_counter(entry['output_tokens'])}` "
                f"cost=`{cost_text}`"
            )
    return "\n".join(lines) + "\n"


def _render_report(
    run_root: Path,
    scenario_results: Sequence[dict[str, object]],
    price_table: dict[str, dict[str, float]] | None,
) -> str:
    """Render the aggregated top-level Markdown report for all finished scenarios."""
    turns_attempted = 0
    turns_completed = 0
    turns_failed = 0
    turns_skipped = 0
    invariant_violations = 0
    patch_total = 0
    creation_total = 0
    aggregated_by_source: dict[str, dict[str, object]] = {}
    aggregated_by_source_model: dict[str, dict[str, object]] = {}
    lines = [
        "# Wiki Long Play Report",
        "",
        f"- run root: `{run_root.as_posix()}`",
        f"- scenarios finished: `{len(scenario_results)}`",
    ]
    if price_table is not None:
        lines.append("- price table: supplied")
    else:
        lines.append("- price table: not supplied")
    lines.extend(("", "## Scenario Status", ""))
    for result in scenario_results:
        turns_attempted += int(result["turns_attempted"])
        turns_completed += int(result["turns_completed"])
        turns_failed += int(result["turns_failed"])
        turns_skipped += int(result["turns_skipped"])
        invariant_violations += int(result["deferred_invariant_violations"])
        patch_total += int(result["patch_total"])
        creation_total += int(result["creation_total"])
        summary = result["llm_summary"]
        assert isinstance(summary, dict)
        for source, bucket in summary["by_source"].items():
            aggregated_by_source.setdefault(source, _empty_llm_bucket())
            _merge_bucket(aggregated_by_source[source], bucket)
        for composite_key, bucket in summary["by_source_model"].items():
            aggregated_by_source_model.setdefault(composite_key, _empty_llm_bucket())
            _merge_bucket(aggregated_by_source_model[composite_key], bucket)
        lines.append(
            f"- `{result['scenario_id']}`: attempted=`{result['turns_attempted']}` "
            f"completed=`{result['turns_completed']}` failed=`{result['turns_failed']}` "
            f"skipped=`{result['turns_skipped']}` abandoned=`{result['abandoned']}`"
        )
    lines.extend(
        (
            "",
            "## Totals",
            "",
            f"- turns attempted: `{turns_attempted}`",
            f"- turns completed: `{turns_completed}`",
            f"- turns failed: `{turns_failed}`",
            f"- turns skipped: `{turns_skipped}`",
            f"- deferred-invariant violations: `{invariant_violations}`",
            f"- pending patches queued: `{patch_total}`",
            f"- pending creations queued: `{creation_total}`",
        )
    )
    if aggregated_by_source_model:
        lines.extend(("", "## Tokens By Log Source And Model", ""))
        for composite_key, bucket in sorted(aggregated_by_source_model.items()):
            source, model = composite_key.split("::", 1)
            entry = {
                "log_source": source,
                "model": model,
                **_bucket_json(bucket),
            }
            cost_text = "n/a"
            if price_table is not None and model in price_table:
                cost_text = _format_currency(
                    _cost_for_bucket(entry, price_table[model])["usd"]
                )
            lines.append(
                f"- `{source}` / `{model}`: "
                f"prompt=`{_format_token_counter(entry['prompt_tokens'])}` "
                f"output=`{_format_token_counter(entry['output_tokens'])}` "
                f"thought=`{_format_token_counter(entry['thought_tokens'])}` "
                f"total=`{_format_token_counter(entry['total_tokens'])}` "
                f"cost=`{cost_text}`"
            )
    if aggregated_by_source:
        lines.extend(("", "## Latency By Log Source", ""))
        for source, bucket in sorted(aggregated_by_source.items()):
            call_count = int(bucket["call_count"])
            mean_ms = (
                int(bucket["total_elapsed_ms"]) / call_count
                if call_count
                else 0.0
            )
            lines.append(
                f"- `{source}`: calls=`{call_count}` "
                f"mean_ms=`{mean_ms:.1f}` max_ms=`{int(bucket['max_elapsed_ms'])}`"
            )
    return "\n".join(lines) + "\n"


def _simulate_applied_documents(thread_root: Path, thread_id: str) -> dict[str, str]:
    """Apply the current pending commit on a copied thread and return its snapshot."""
    from src.wiki import apply_pending_wiki_commit

    with tempfile.TemporaryDirectory(prefix="wiki_long_play_apply_") as temporary:
        temporary_root = Path(temporary)
        vault_root = temporary_root / "wiki_v2"
        copied_thread_root = vault_root / "threads" / thread_id
        copied_thread_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(thread_root, copied_thread_root)
        apply_pending_wiki_commit(vault_root, thread_id)
        return canonical_documents(copied_thread_root)


def _turn_actor_response(events: Sequence[dict[str, object]]) -> str:
    """Return the completed assistant response content for the current turn."""
    for event in reversed(events):
        if event.get("type") != "complete":
            continue
        message = event.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "")
    return ""


def _runtime_modules() -> tuple[object, object, type[object]]:
    """Import runtime-only app modules lazily so help/init paths stay side-effect light."""
    import src.apps.app.service as app_service
    import src.apps.app.wiki_controls as wiki_controls
    from src.apps.app.storage import ConversationStore

    return app_service, wiki_controls, ConversationStore


async def _run_one_scenario(
    scenario_id: str,
    script_path: Path,
    turns: Sequence[dict[str, object]],
    actor_model: str | None,
    scenario_root: Path,
    price_table: dict[str, dict[str, float]] | None,
    stop_on_error: bool,
) -> dict[str, object]:
    """Run one scenario in an isolated vault and persist its evidence artifacts."""
    app_service, wiki_controls, conversation_store_cls = _runtime_modules()
    scenario_root.mkdir(parents=True, exist_ok=False)
    log_path = Path("logs") / "llm_latency.jsonl"
    llm_rows_all: list[dict[str, object]] = []
    turn_records: list[dict[str, object]] = []
    attempted_turns = 0
    completed_turns = 0
    failed_turns = 0
    skipped_turns = 0
    deferred_invariant_violations = 0
    patch_total = 0
    creation_total = 0
    abandoned = False
    abandon_reason = ""
    final_apply_status = "not_needed"
    final_apply_error = ""

    with tempfile.TemporaryDirectory(prefix=f"wiki_long_play_{scenario_id}_") as temporary:
        temporary_root = Path(temporary)
        vault_root = temporary_root / "wiki_v2"
        source_world = Path("wiki_v2/worlds/babe_university")
        shutil.copytree(source_world, vault_root / "worlds" / "babe_university")
        patch_vault_root(vault_root)
        store = conversation_store_cls(temporary_root / "data" / "threads")
        state = app_service.create_conversation(
            "babe_university",
            scenario_id,
            store,
            actor_model=actor_model,
            world_mode="wiki",
        )
        thread_root = vault_root / "threads" / state.thread_id
        opening_documents = canonical_documents(thread_root)

        previous_turn_record: dict[str, object] | None = None
        for turn in turns:
            if bool(turn["is_empty"]):
                skipped_turns += 1
                turn_records.append({
                    "turn_index": int(turn["turn_index"]),
                    "turn_label": str(turn["turn_label"]),
                    "user_input": "",
                    "status": "skipped_empty",
                    "actor_response": "",
                    "wiki_update_status": str(getattr(state, "wiki_update_status", "idle")),
                    "wiki_update_error": str(getattr(state, "wiki_update_error", "")),
                    "wiki_pending_commit_id": getattr(state, "wiki_pending_commit_id", None),
                    "pending_patch_count": 0,
                    "pending_creation_count": 0,
                    "applied_changed_documents": [],
                    "canonical_unchanged_during_generation": None,
                    "wall_time_seconds": 0.0,
                    "llm_calls": [],
                    "llm_summary": _summarize_llm_calls([]),
                    "stream_exception": "",
                })
                continue

            before_turn = canonical_documents(thread_root)
            expected_generation_baseline = before_turn
            if (
                previous_turn_record is not None
                and getattr(state, "wiki_update_status", "") == "queued"
                and (thread_root / "commit.md").is_file()
            ):
                expected_generation_baseline = _simulate_applied_documents(
                    thread_root,
                    state.thread_id,
                )
                previous_turn_record["applied_changed_documents"] = _changed_documents(
                    before_turn,
                    expected_generation_baseline,
                )

            log_start = _log_size(log_path)
            started = perf_counter()
            attempted_turns += 1
            events: list[dict[str, object]] = []
            stream_exception = ""
            try:
                async for event in app_service.append_user_and_stream(
                    state,
                    str(turn["user_input"]),
                    store,
                    actor_model=actor_model,
                ):
                    if event.get("type") != "token":
                        events.append(event)
            except Exception as exc:
                stream_exception = str(exc)
            wall_time_seconds = perf_counter() - started
            log_end = _log_size(log_path)
            after_turn = canonical_documents(thread_root)
            llm_rows = _slice_llm_log(log_path, log_start, log_end)
            llm_rows_all.extend(llm_rows)
            llm_summary = _summarize_llm_calls(llm_rows)
            invariant_ok = after_turn == expected_generation_baseline
            if not invariant_ok:
                deferred_invariant_violations += 1

            commit_status = wiki_controls.get_wiki_commit_status(state)
            commit_payload = commit_status.commit or {}
            patch_count = len(commit_payload.get("patches") or [])
            creation_count = len(commit_payload.get("creations") or [])
            actor_response = _turn_actor_response(events)
            status = "completed"
            if stream_exception:
                status = "stream_exception"
                failed_turns += 1
                abandoned = True
                abandon_reason = stream_exception
            elif str(commit_status.update_status) == "failed":
                status = "updater_failed"
                failed_turns += 1
            else:
                completed_turns += 1
            patch_total += patch_count
            creation_total += creation_count

            turn_record = {
                "turn_index": int(turn["turn_index"]),
                "turn_label": str(turn["turn_label"]),
                "user_input": str(turn["user_input"]),
                "status": status,
                "actor_response": actor_response,
                "wiki_update_status": str(commit_status.update_status),
                "wiki_update_error": str(commit_status.update_error or ""),
                "wiki_pending_commit_id": getattr(state, "wiki_pending_commit_id", None),
                "pending_patch_count": patch_count,
                "pending_creation_count": creation_count,
                "applied_changed_documents": [],
                "canonical_unchanged_during_generation": invariant_ok,
                "wall_time_seconds": wall_time_seconds,
                "llm_calls": llm_rows,
                "llm_summary": llm_summary,
                "stream_exception": stream_exception,
            }
            turn_records.append(turn_record)
            previous_turn_record = turn_record

            if stream_exception:
                break

        if (
            not abandoned
            and previous_turn_record is not None
            and getattr(state, "wiki_update_status", "") == "queued"
            and (thread_root / "commit.md").is_file()
        ):
            before_final_apply = canonical_documents(thread_root)
            try:
                apply_result = wiki_controls.apply_wiki_commit_now(state, store)
                final_apply_status = str(apply_result.update_status)
            except Exception as exc:
                final_apply_status = "failed"
                final_apply_error = str(exc)
                if stop_on_error:
                    abandoned = True
                    abandon_reason = final_apply_error
            after_final_apply = canonical_documents(thread_root)
            previous_turn_record["applied_changed_documents"] = _changed_documents(
                before_final_apply,
                after_final_apply,
            )
        elif previous_turn_record is not None and previous_turn_record["applied_changed_documents"] == []:
            previous_turn_record["applied_changed_documents"] = []

        final_documents = canonical_documents(thread_root)
        llm_summary_all = _summarize_llm_calls(llm_rows_all)
        result = {
            "world_id": state.world_id,
            "scenario_id": scenario_id,
            "thread_id": state.thread_id,
            "actor_model": state.actor_model,
            "script_path": script_path.as_posix(),
            "turns_attempted": attempted_turns,
            "turns_completed": completed_turns,
            "turns_failed": failed_turns,
            "turns_skipped": skipped_turns,
            "deferred_invariant_violations": deferred_invariant_violations,
            "patch_total": patch_total,
            "creation_total": creation_total,
            "final_apply_status": final_apply_status,
            "final_apply_error": final_apply_error,
            "abandoned": abandoned,
            "abandon_reason": abandon_reason,
            "turns": turn_records,
            "llm_summary": llm_summary_all,
        }
        write_json(scenario_root / "turns.json", result)
        (scenario_root / "summary.md").write_text(
            _render_scenario_summary(result, price_table),
            encoding="utf-8",
        )
        (scenario_root / "canonical.diff").write_text(
            render_document_diff(opening_documents, final_documents),
            encoding="utf-8",
        )
        shutil.copytree(thread_root, scenario_root / "thread_vault")
        return result


async def _run_long_play(
    scenarios: Sequence[str],
    script_root: Path,
    output_root: Path,
    actor_model: str | None,
    max_turns: int | None,
    price_table: dict[str, dict[str, float]] | None,
    stop_on_error: bool,
) -> Path:
    """Run all selected scenarios sequentially and refresh the top-level report."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    run_root = output_root / stamp
    run_root.mkdir(parents=True, exist_ok=False)
    scenario_results: list[dict[str, object]] = []
    for scenario_id in scenarios:
        script_path = script_root / f"{scenario_id}.md"
        scenario_root = run_root / scenario_id
        try:
            turns = _read_turn_script(script_path)
        except Exception as exc:
            result = {
                "world_id": "babe_university",
                "scenario_id": scenario_id,
                "thread_id": "",
                "actor_model": actor_model or "",
                "script_path": script_path.as_posix(),
                "turns_attempted": 0,
                "turns_completed": 0,
                "turns_failed": 0,
                "turns_skipped": 0,
                "deferred_invariant_violations": 0,
                "patch_total": 0,
                "creation_total": 0,
                "final_apply_status": "not_started",
                "final_apply_error": "",
                "abandoned": True,
                "abandon_reason": str(exc),
                "turns": [],
                "llm_summary": _summarize_llm_calls([]),
            }
            scenario_root.mkdir(parents=True, exist_ok=False)
            write_json(scenario_root / "turns.json", result)
            (scenario_root / "summary.md").write_text(
                _render_scenario_summary(result, price_table),
                encoding="utf-8",
            )
            (scenario_root / "canonical.diff").write_text("", encoding="utf-8")
        else:
            limited_turns: list[dict[str, object]] = []
            attempted_budget = 0
            for turn in turns:
                limited_turns.append(turn)
                if not bool(turn["is_empty"]):
                    attempted_budget += 1
                if max_turns is not None and attempted_budget >= max_turns:
                    break
            result = await _run_one_scenario(
                scenario_id=scenario_id,
                script_path=script_path,
                turns=limited_turns,
                actor_model=actor_model,
                scenario_root=scenario_root,
                price_table=price_table,
                stop_on_error=stop_on_error,
            )
        scenario_results.append(result)
        (run_root / "report.md").write_text(
            _render_report(run_root, scenario_results, price_table),
            encoding="utf-8",
        )
        if stop_on_error and bool(result.get("abandoned")):
            raise RuntimeError(
                f"long-play stopped on {scenario_id}: {result.get('abandon_reason') or 'error'}"
            )
    return run_root


def _parse_args() -> argparse.Namespace:
    """Parse long-play harness CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Run unattended multi-turn Wiki long-play validation from Markdown turn scripts.",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=_scenario_choices(),
        default=list(_scenario_choices()),
        help="Scenario ids to run sequentially. Default: all five babe_university scenarios.",
    )
    parser.add_argument(
        "--script-root",
        type=Path,
        default=Path("docs/wiki_long_play"),
        help="Directory containing one <scenario>.md turn script per scenario.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("docs/wiki_llm_runs/long_play"),
        help="Directory where run artifacts are written under one UTC-stamped subdirectory.",
    )
    parser.add_argument(
        "--actor-model",
        help="Optional Actor model override for all scenarios in this run.",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Maximum number of non-empty authored turns to attempt per scenario.",
    )
    parser.add_argument(
        "--price-table",
        type=Path,
        help=(
            "Optional JSON file mapping model name to "
            "{input_per_million, output_per_million} prices."
        ),
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop the overall run after the first abandoned scenario or final-apply failure.",
    )
    parser.add_argument(
        "--init-scripts",
        action="store_true",
        help="Create missing turn-script templates for the selected scenarios and exit.",
    )
    return parser.parse_args()


def main() -> None:
    """Initialize templates or run the sequential Wiki long-play harness."""
    args = _parse_args()
    if args.max_turns is not None and args.max_turns <= 0:
        raise ValueError("--max-turns must be a positive integer when provided")
    if args.init_scripts:
        result = _init_scripts(args.script_root, args.scenarios)
        for created in result["created"]:
            print(f"created: {created}")
        for skipped in result["skipped"]:
            print(f"skipped existing: {skipped}")
        return
    price_table = _load_price_table(args.price_table)
    run_root = asyncio.run(
        _run_long_play(
            scenarios=args.scenarios,
            script_root=args.script_root,
            output_root=args.output_root,
            actor_model=args.actor_model,
            max_turns=args.max_turns,
            price_table=price_table,
            stop_on_error=args.stop_on_error,
        )
    )
    print(run_root)


if __name__ == "__main__":
    main()
