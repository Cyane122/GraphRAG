# ================================
# tests/smoke_wiki_long_play.py
#
# Wiki 장기 플레이 harness의 순수 파서·로그 집계·리포트 렌더링 헬퍼를 실제 LLM 없이 검증합니다.
#
# Functions
#   - _check_turn_script_parsing(root: Path) -> None : H2 기반 턴 스크립트 파싱과 preamble/empty/no-heading 처리를 검증합니다.
#   - _check_log_slicing(root: Path) -> None : missing/truncated/unparseable JSONL 바이트 슬라이스 처리를 검증합니다.
#   - _check_llm_aggregation_and_cost() -> None : null 토큰 집계와 가격표 기반 비용 계산을 검증합니다.
#   - _check_report_rendering(root: Path) -> None : top-level report Markdown 렌더링을 검증합니다.
#   - main() -> None : 장기 플레이 harness 헬퍼 스모크 테스트를 실행합니다.
# ================================

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.run_wiki_long_play import (
    _cost_for_bucket,
    _load_price_table,
    _read_turn_script,
    _render_report,
    _slice_llm_log,
    _summarize_llm_calls,
)


def _check_turn_script_parsing(root: Path) -> None:
    """Verify preamble ignore, empty-body skip metadata, and no-heading behavior."""
    scripted = root / "script.md"
    scripted.write_text(
        "# Notes\n\nIgnored preamble.\n\n"
        "## Turn A\n\n첫 줄.\n둘째 줄.\n\n"
        "## Turn B\n\n\n"
        "## Turn C\n마지막 턴.\n",
        encoding="utf-8",
    )
    turns = _read_turn_script(scripted)
    assert len(turns) == 3
    assert turns[0]["turn_label"] == "Turn A"
    assert turns[0]["user_input"] == "첫 줄.\n둘째 줄."
    assert turns[0]["is_empty"] is False
    assert turns[1]["turn_label"] == "Turn B"
    assert turns[1]["user_input"] == ""
    assert turns[1]["is_empty"] is True
    assert turns[2]["turn_label"] == "Turn C"
    assert turns[2]["user_input"] == "마지막 턴."

    no_headings = root / "no_headings.md"
    no_headings.write_text("preamble only\nstill ignored\n", encoding="utf-8")
    assert _read_turn_script(no_headings) == []


def _check_log_slicing(root: Path) -> None:
    """Verify missing files, truncated final lines, and unparseable rows are tolerated."""
    missing = root / "missing.jsonl"
    assert _slice_llm_log(missing, 0) == []

    log_path = root / "llm_latency.jsonl"
    line_a = json.dumps({
        "log_source": "actor",
        "model": "m1",
        "elapsed_ms": 120,
        "prompt_tokens": 10,
        "output_tokens": 20,
        "thought_tokens": None,
        "total_tokens": 30,
    }, ensure_ascii=False)
    bad_line = "not json at all"
    line_b = json.dumps({
        "log_source": "updater",
        "model": "m2",
        "elapsed_ms": 250,
        "prompt_tokens": None,
        "output_tokens": 7,
        "thought_tokens": None,
        "total_tokens": None,
    }, ensure_ascii=False)
    log_path.write_bytes(
        (
            (line_a + "\n").encode("utf-8")
            + (bad_line + "\n").encode("utf-8")
            + line_b.encode("utf-8")
        )
    )
    rows = _slice_llm_log(log_path, 0)
    assert len(rows) == 1
    assert rows[0]["log_source"] == "actor"

    appended = (json.dumps({
        "log_source": "actor",
        "model": "m1",
        "elapsed_ms": 80,
        "prompt_tokens": 5,
        "output_tokens": 6,
        "thought_tokens": 1,
        "total_tokens": 12,
    }) + "\n").encode("utf-8")
    start_offset = log_path.stat().st_size
    with log_path.open("ab") as handle:
        handle.write(appended)
    sliced = _slice_llm_log(log_path, start_offset)
    assert len(sliced) == 1
    assert sliced[0]["elapsed_ms"] == 80


def _check_llm_aggregation_and_cost() -> None:
    """Verify null-token handling and optional price-table cost computation."""
    rows = [
        {
            "log_source": "actor",
            "model": "model-a",
            "elapsed_ms": 100,
            "prompt_tokens": 10,
            "output_tokens": 20,
            "thought_tokens": None,
            "total_tokens": 30,
        },
        {
            "log_source": "actor",
            "model": "model-a",
            "elapsed_ms": 40,
            "prompt_tokens": None,
            "output_tokens": 5,
            "thought_tokens": 3,
            "total_tokens": None,
        },
    ]
    summary = _summarize_llm_calls(rows)
    actor = summary["by_source"]["actor"]
    assert actor["call_count"] == 2
    assert actor["total_elapsed_ms"] == 140
    assert actor["max_elapsed_ms"] == 100
    assert actor["prompt_tokens"]["sum"] == 10
    assert actor["prompt_tokens"]["unknown_calls"] == 1
    assert actor["output_tokens"]["sum"] == 25
    assert actor["thought_tokens"]["sum"] == 3
    assert actor["thought_tokens"]["unknown_calls"] == 1

    with TemporaryDirectory() as temporary:
        price_path = Path(temporary) / "prices.json"
        price_path.write_text(
            json.dumps({
                "model-a": {
                    "input_per_million": 1.5,
                    "output_per_million": 6.0,
                }
            }),
            encoding="utf-8",
        )
        price_table = _load_price_table(price_path)
    assert price_table is not None
    model_bucket = summary["by_source_model"]["actor::model-a"]
    cost = _cost_for_bucket(model_bucket, price_table["model-a"])
    expected = (10 / 1_000_000 * 1.5) + (25 / 1_000_000 * 6.0)
    assert abs(cost["usd"] - expected) < 1e-12
    assert cost["prompt_unknown_calls"] == 1
    assert cost["output_unknown_calls"] == 0
    assert _load_price_table(None) is None


def _check_report_rendering(root: Path) -> None:
    """Verify the aggregate report includes turn, token, cost, and latency summaries."""
    scenario_results = [{
        "world_id": "babe_university",
        "scenario_id": "lover",
        "thread_id": "thread_1",
        "actor_model": "model-a",
        "script_path": "docs/wiki_long_play/lover.md",
        "turns_attempted": 2,
        "turns_completed": 1,
        "turns_failed": 1,
        "turns_skipped": 1,
        "deferred_invariant_violations": 1,
        "patch_total": 3,
        "creation_total": 1,
        "final_apply_status": "applied",
        "final_apply_error": "",
        "abandoned": False,
        "abandon_reason": "",
        "turns": [],
        "llm_summary": {
            "call_count": 2,
            "by_source": {
                "actor": {
                    "call_count": 2,
                    "total_elapsed_ms": 150,
                    "max_elapsed_ms": 100,
                    "prompt_tokens": {"sum": 10, "known_calls": 1, "unknown_calls": 1},
                    "output_tokens": {"sum": 25, "known_calls": 2, "unknown_calls": 0},
                    "thought_tokens": {"sum": 3, "known_calls": 1, "unknown_calls": 1},
                    "total_tokens": {"sum": 30, "known_calls": 1, "unknown_calls": 1},
                }
            },
            "by_source_model": {
                "actor::model-a": {
                    "log_source": "actor",
                    "model": "model-a",
                    "call_count": 2,
                    "total_elapsed_ms": 150,
                    "max_elapsed_ms": 100,
                    "prompt_tokens": {"sum": 10, "known_calls": 1, "unknown_calls": 1},
                    "output_tokens": {"sum": 25, "known_calls": 2, "unknown_calls": 0},
                    "thought_tokens": {"sum": 3, "known_calls": 1, "unknown_calls": 1},
                    "total_tokens": {"sum": 30, "known_calls": 1, "unknown_calls": 1},
                }
            },
        },
    }]
    price_table = {
        "model-a": {
            "input_per_million": 1.5,
            "output_per_million": 6.0,
        }
    }
    report = _render_report(root / "run", scenario_results, price_table)
    assert "# Wiki Long Play Report" in report
    assert "turns attempted: `2`" in report
    assert "deferred-invariant violations: `1`" in report
    assert "`actor` / `model-a`" in report
    assert "cost=`$" in report
    assert "mean_ms=`75.0`" in report


def main() -> None:
    """Run pure-data smoke checks for the Wiki long-play harness helpers."""
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        _check_turn_script_parsing(root)
        _check_log_slicing(root)
        _check_report_rendering(root)
    _check_llm_aggregation_and_cost()
    print("smoke_wiki_long_play: ok")


if __name__ == "__main__":
    main()
