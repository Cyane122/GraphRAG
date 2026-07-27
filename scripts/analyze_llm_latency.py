# ================================
# scripts/analyze_llm_latency.py
#
# logs/llm_latency.jsonl 을 분석해 LLM 호출 병목과 토큰/비용 사용량을 요약합니다.
#
# Classes
#   - LogRow : 분석에 사용할 단일 LLM 호출 로그 레코드
#   - TokenFieldStats : 단일 토큰 필드의 합계와 known/unknown 호출 수
#   - TokenBucketStats : 버킷별 호출 수와 토큰 집계를 보관
#   - PriceSpec : 모델별 input/output 단가(백만 토큰당)
#
# Functions
#   - _build_parser() -> argparse.ArgumentParser : CLI 인자를 정의한다.
#   - _coerce_int(value: object) -> int | None : int 또는 정수 문자열을 안전하게 int로 변환한다.
#   - _parse_row(payload: object) -> LogRow | None : JSON payload를 분석 가능한 LogRow로 정규화한다.
#   - _load(path: Path) -> tuple[list[LogRow], int] : 지연 로그 라인을 읽어 정렬된 레코드와 skipped row 수를 반환한다.
#   - _filter_rows(rows: list[LogRow], log_source: str | None) -> list[LogRow] : 선택된 log_source만 남긴다.
#   - _per_source(rows: list[LogRow]) -> None : log_source 별 호출 수/평균/최대 지연 출력
#   - _sequential_chains(rows: list[LogRow], gap_ms: int) -> None : 시간 간격으로 턴을 군집화해 순차/병렬 구간 추정
#   - _new_bucket() -> TokenBucketStats : 새 토큰 집계 버킷을 만든다.
#   - _accumulate_bucket(bucket: TokenBucketStats, row: LogRow) -> None : 한 레코드의 토큰 값을 버킷에 누적한다.
#   - _format_token_cell(stats: TokenFieldStats) -> str : 토큰 합계와 known/unknown 호출 수를 셀 문자열로 만든다.
#   - _format_cost_cell(prompt_stats: TokenFieldStats, output_stats: TokenFieldStats, estimated_cost: float) -> str : 비용 추정치 또는 unknown 문자열을 만든다.
#   - _print_token_summary_by_source(rows: list[LogRow]) -> None : log_source 별 토큰 집계를 출력한다.
#   - _print_token_summary_by_source_model(rows: list[LogRow]) -> None : log_source+model 별 토큰 집계를 출력한다.
#   - _load_prices(path: Path) -> dict[str, PriceSpec] | None : 모델 단가표 JSON을 읽어 정규화한다.
#   - _print_cost_summary(rows: list[LogRow], prices: dict[str, PriceSpec]) -> None : 알려진 prompt/output 토큰만으로 모델별 비용을 추정한다.
#   - main(argv: Sequence[str] | None = None) -> None : 로그를 읽어 지연/토큰/비용 요약 리포트를 출력한다.
# ================================
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

_LOG = Path("logs") / "llm_latency.jsonl"
_TOKEN_FIELDS: tuple[str, ...] = (
    "prompt_tokens",
    "output_tokens",
    "thought_tokens",
    "total_tokens",
)


@dataclass(frozen=True)
class LogRow:
    """분석에 사용할 단일 LLM 호출 로그 레코드다."""

    ts: int
    log_source: str
    model: str
    elapsed_ms: int
    mime: str | None
    status: str
    prompt_tokens: int | None
    output_tokens: int | None
    thought_tokens: int | None
    total_tokens: int | None


@dataclass
class TokenFieldStats:
    """단일 토큰 필드의 합계와 known/unknown 호출 수를 보관한다."""

    total: int = 0
    known_calls: int = 0
    unknown_calls: int = 0


@dataclass
class TokenBucketStats:
    """버킷별 호출 수와 토큰 집계를 보관한다."""

    calls: int = 0
    fields: dict[str, TokenFieldStats] = field(
        default_factory=lambda: {name: TokenFieldStats() for name in _TOKEN_FIELDS}
    )


@dataclass(frozen=True)
class PriceSpec:
    """모델별 input/output 단가(백만 토큰당)를 보관한다."""

    input_per_million: float
    output_per_million: float


def _build_parser() -> argparse.ArgumentParser:
    """CLI 인자를 정의한다."""
    parser = argparse.ArgumentParser(
        description=(
            "Analyze logs/llm_latency.jsonl for latency, token usage, and optional cost."
        )
    )
    parser.add_argument(
        "log_path",
        nargs="?",
        default=str(_LOG),
        help=f"Path to the latency jsonl log (default: {str(_LOG)!r}).",
    )
    parser.add_argument(
        "--prices",
        type=str,
        help=(
            "Optional JSON file with per-model pricing: "
            "{model: {input_per_million|input, output_per_million|output}}."
        ),
    )
    parser.add_argument(
        "--log-source",
        type=str,
        help="Optional exact-match filter for log_source before summarizing.",
    )
    parser.add_argument(
        "--gap-ms",
        type=int,
        default=4000,
        help="Gap between call start times that starts a new turn cluster (default: 4000).",
    )
    return parser


def _coerce_int(value: object) -> int | None:
    """int 또는 정수 문자열을 안전하게 int로 변환한다."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text[0] in {"+", "-"}:
            digits = text[1:]
        else:
            digits = text
        if digits.isdigit():
            return int(text)
        return None
    return None


def _parse_row(payload: object) -> LogRow | None:
    """JSON payload를 분석 가능한 LogRow로 정규화한다."""
    if not isinstance(payload, dict):
        return None

    ts = _coerce_int(payload.get("ts"))
    elapsed_ms = _coerce_int(payload.get("elapsed_ms"))
    if ts is None or elapsed_ms is None:
        return None

    token_values: dict[str, int | None] = {}
    for field_name in _TOKEN_FIELDS:
        raw_value = payload.get(field_name)
        if raw_value is None or field_name not in payload:
            token_values[field_name] = None
            continue
        value = _coerce_int(raw_value)
        if value is None:
            return None
        token_values[field_name] = value

    mime_value = payload.get("mime")
    mime = mime_value if isinstance(mime_value, str) else None

    status_value = payload.get("status")
    if status_value is None:
        status = "unknown"
    else:
        status = str(status_value)

    log_source_value = payload.get("log_source")
    model_value = payload.get("model")
    log_source = str(log_source_value) if log_source_value is not None else "None"
    model = str(model_value) if model_value is not None else "<unknown>"

    return LogRow(
        ts=ts,
        log_source=log_source,
        model=model,
        elapsed_ms=elapsed_ms,
        mime=mime,
        status=status,
        prompt_tokens=token_values["prompt_tokens"],
        output_tokens=token_values["output_tokens"],
        thought_tokens=token_values["thought_tokens"],
        total_tokens=token_values["total_tokens"],
    )


def _load(path: Path) -> tuple[list[LogRow], int]:
    """지연 로그 라인을 읽어 정렬된 레코드와 skipped row 수를 반환한다."""
    if not path.exists():
        print(f"[analyze] no log file: {path}")
        return [], 0

    rows: list[LogRow] = []
    skipped_rows = 0

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    skipped_rows += 1
                    continue

                row = _parse_row(payload)
                if row is None:
                    skipped_rows += 1
                    continue
                rows.append(row)
    except OSError:
        print(f"[analyze] could not read log file: {path}")
        return [], 0

    rows.sort(key=lambda row: row.ts)
    return rows, skipped_rows


def _filter_rows(rows: list[LogRow], log_source: str | None) -> list[LogRow]:
    """선택된 log_source만 남긴다."""
    if log_source is None:
        return rows
    return [row for row in rows if row.log_source == log_source]


def _per_source(rows: list[LogRow]) -> None:
    """log_source 별 호출 수, 총/평균/최대 지연(ms)을 큰 평균순으로 출력한다."""
    by_source: dict[str, list[int]] = {}
    for row in rows:
        by_source.setdefault(row.log_source, []).append(row.elapsed_ms)

    print("\n=== per log_source (sorted by avg ms) ===")
    print(f"{'log_source':32} {'n':>4} {'avg':>8} {'max':>8} {'total':>9}")
    summary: list[tuple[float, str, int, int, int]] = []
    for source, times in by_source.items():
        avg = sum(times) / len(times) if times else 0
        summary.append((avg, source, len(times), max(times or [0]), sum(times)))
    for avg, source, count, max_ms, total_ms in sorted(summary, reverse=True):
        print(f"{source:32} {count:>4} {avg:>8.0f} {max_ms:>8} {total_ms:>9}")


def _sequential_chains(rows: list[LogRow], gap_ms: int = 4000) -> None:
    """호출 시작 간격이 gap_ms 이상 벌어지면 새 턴으로 보고 순차/병렬 구간을 추정한다."""
    if not rows:
        return
    print(f"\n=== turn clusters (new turn when start-gap > {gap_ms}ms) ===")
    cluster: list[LogRow] = []
    last_ts: int | None = None

    def _flush(group: list[LogRow]) -> None:
        """군집 하나의 벽시계/합산 지연을 출력한다."""
        if not group:
            return
        starts = [row.ts for row in group]
        ends = [row.ts + row.elapsed_ms for row in group]
        wall = max(ends) - min(starts)
        summed = sum(row.elapsed_ms for row in group)
        kind = "parallel" if summed > wall * 1.3 else "sequential"
        sources = ", ".join(row.log_source for row in group)
        print(f"- {len(group)} calls | wall={wall}ms summed={summed}ms ({kind})\n    {sources}")

    for row in rows:
        if last_ts is not None and row.ts - last_ts > gap_ms:
            _flush(cluster)
            cluster = []
        cluster.append(row)
        last_ts = row.ts
    _flush(cluster)


def _new_bucket() -> TokenBucketStats:
    """새 토큰 집계 버킷을 만든다."""
    return TokenBucketStats()


def _accumulate_bucket(bucket: TokenBucketStats, row: LogRow) -> None:
    """한 레코드의 토큰 값을 버킷에 누적한다."""
    bucket.calls += 1
    for field_name in _TOKEN_FIELDS:
        value = getattr(row, field_name)
        stats = bucket.fields[field_name]
        if value is None:
            stats.unknown_calls += 1
            continue
        stats.total += value
        stats.known_calls += 1


def _format_token_cell(stats: TokenFieldStats) -> str:
    """토큰 합계와 known/unknown 호출 수를 셀 문자열로 만든다."""
    if stats.known_calls == 0:
        return f"unknown [k=0 u={stats.unknown_calls}]"
    return f"{stats.total} [k={stats.known_calls} u={stats.unknown_calls}]"


def _format_cost_cell(
    prompt_stats: TokenFieldStats,
    output_stats: TokenFieldStats,
    estimated_cost: float,
) -> str:
    """비용 추정치 또는 unknown 문자열을 만든다."""
    if prompt_stats.known_calls == 0 and output_stats.known_calls == 0:
        return "unknown"
    return f"{estimated_cost:.6f}"


def _print_token_summary_by_source(rows: list[LogRow]) -> None:
    """log_source 별 토큰 집계를 출력한다."""
    buckets: dict[str, TokenBucketStats] = {}
    for row in rows:
        bucket = buckets.setdefault(row.log_source, _new_bucket())
        _accumulate_bucket(bucket, row)

    print("\n=== tokens per log_source ===")
    print("token cells show: total [k=known_calls u=unknown_calls]")
    print(
        f"{'log_source':32} {'n':>4} {'prompt_tokens':>26} {'output_tokens':>26} "
        f"{'thought_tokens':>26} {'total_tokens':>26}"
    )
    for source, bucket in sorted(
        buckets.items(),
        key=lambda item: (
            item[1].fields["total_tokens"].total,
            item[1].calls,
            item[0],
        ),
        reverse=True,
    ):
        print(
            f"{source:32} {bucket.calls:>4} "
            f"{_format_token_cell(bucket.fields['prompt_tokens']):>26} "
            f"{_format_token_cell(bucket.fields['output_tokens']):>26} "
            f"{_format_token_cell(bucket.fields['thought_tokens']):>26} "
            f"{_format_token_cell(bucket.fields['total_tokens']):>26}"
        )


def _print_token_summary_by_source_model(rows: list[LogRow]) -> None:
    """log_source+model 별 토큰 집계를 출력한다."""
    buckets: dict[tuple[str, str], TokenBucketStats] = {}
    for row in rows:
        key = (row.log_source, row.model)
        bucket = buckets.setdefault(key, _new_bucket())
        _accumulate_bucket(bucket, row)

    print("\n=== tokens per log_source + model ===")
    print("token cells show: total [k=known_calls u=unknown_calls]")
    print(
        f"{'log_source':28} {'model':32} {'n':>4} {'prompt_tokens':>26} {'output_tokens':>26} "
        f"{'thought_tokens':>26} {'total_tokens':>26}"
    )
    for (source, model), bucket in sorted(
        buckets.items(),
        key=lambda item: (
            item[1].fields["total_tokens"].total,
            item[1].calls,
            item[0][0],
            item[0][1],
        ),
        reverse=True,
    ):
        print(
            f"{source:28} {model:32} {bucket.calls:>4} "
            f"{_format_token_cell(bucket.fields['prompt_tokens']):>26} "
            f"{_format_token_cell(bucket.fields['output_tokens']):>26} "
            f"{_format_token_cell(bucket.fields['thought_tokens']):>26} "
            f"{_format_token_cell(bucket.fields['total_tokens']):>26}"
        )


def _load_prices(path: Path) -> dict[str, PriceSpec] | None:
    """모델 단가표 JSON을 읽어 정규화한다."""
    if not path.exists():
        print(f"[analyze] no price table: {path}")
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"[analyze] invalid price table: {path}")
        return None

    if not isinstance(raw, dict):
        print(f"[analyze] invalid price table root: {path}")
        return None

    prices: dict[str, PriceSpec] = {}
    for model, spec in raw.items():
        if not isinstance(model, str) or not isinstance(spec, dict):
            print(f"[analyze] skipping invalid price entry for model: {model!r}")
            continue

        input_value = spec.get("input_per_million", spec.get("input"))
        output_value = spec.get("output_per_million", spec.get("output"))
        if not isinstance(input_value, (int, float)) or not isinstance(
            output_value, (int, float)
        ):
            print(f"[analyze] skipping invalid price entry for model: {model}")
            continue

        prices[model] = PriceSpec(
            input_per_million=float(input_value),
            output_per_million=float(output_value),
        )

    return prices


def _print_cost_summary(rows: list[LogRow], prices: dict[str, PriceSpec]) -> None:
    """알려진 prompt/output 토큰만으로 모델별 비용을 추정한다."""
    buckets: dict[str, TokenBucketStats] = {}
    for row in rows:
        bucket = buckets.setdefault(row.model, _new_bucket())
        _accumulate_bucket(bucket, row)

    skipped_models: list[str] = []
    priced_lines: list[tuple[str, TokenBucketStats, float]] = []
    for model, bucket in buckets.items():
        price = prices.get(model)
        if price is None:
            skipped_models.append(model)
            continue
        prompt_total = bucket.fields["prompt_tokens"].total
        output_total = bucket.fields["output_tokens"].total
        estimated_cost = (
            (prompt_total / 1_000_000) * price.input_per_million
            + (output_total / 1_000_000) * price.output_per_million
        )
        priced_lines.append((model, bucket, estimated_cost))

    print("\n=== estimated cost by model ===")
    print("cost uses only known prompt_tokens/output_tokens for priced models.")
    print(
        f"{'model':36} {'calls':>5} {'prompt_tokens':>26} {'output_tokens':>26} {'cost_usd':>12}"
    )
    for model, bucket, estimated_cost in sorted(
        priced_lines,
        key=lambda item: (item[2], item[1].calls, item[0]),
        reverse=True,
    ):
        print(
            f"{model:36} {bucket.calls:>5} "
            f"{_format_token_cell(bucket.fields['prompt_tokens']):>26} "
            f"{_format_token_cell(bucket.fields['output_tokens']):>26} "
            f"{_format_cost_cell(bucket.fields['prompt_tokens'], bucket.fields['output_tokens'], estimated_cost):>12}"
        )
    if not priced_lines:
        print("(no priced models matched the loaded rows)")
    if skipped_models:
        skipped = ", ".join(sorted(skipped_models))
        print(f"[analyze] skipped unpriced models: {skipped}")


def main(argv: Sequence[str] | None = None) -> None:
    """로그를 읽어 지연/토큰/비용 요약 리포트를 출력한다."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    path = Path(args.log_path)
    rows, skipped_rows = _load(path)
    filtered_rows = _filter_rows(rows, args.log_source)
    print(f"[analyze] {len(filtered_rows)} calls from {path}")
    if args.log_source is not None:
        print(f"[analyze] filter log_source={args.log_source!r}")
    if skipped_rows:
        print(f"[analyze] skipped {skipped_rows} malformed rows")
    if not rows:
        return

    if not filtered_rows:
        return

    _per_source(filtered_rows)
    _sequential_chains(filtered_rows, gap_ms=args.gap_ms)
    _print_token_summary_by_source(filtered_rows)
    _print_token_summary_by_source_model(filtered_rows)

    if args.prices:
        prices = _load_prices(Path(args.prices))
        if prices is not None:
            _print_cost_summary(filtered_rows, prices)


if __name__ == "__main__":
    main()
