# ================================
# src/core/logging/prompt_debug.py
#
# PromptCard 캐싱 전 단계에서 프롬프트 안정성을 관측하기 위한 fingerprint 로그 유틸리티입니다.
#
# Functions
#   - build_prompt_fingerprint(fixed_prompt: str, genre_prompt: str, dynamic_prompt: str, history: list[dict] | None) -> dict : 프롬프트 파트별 해시와 크기 메타데이터 생성
#   - append_prompt_fingerprint_log(record: dict, logs_dir: Path | str) -> None : fingerprint record를 JSONL 로그로 저장
#   - format_prompt_fingerprint(record: dict) -> str : 콘솔 출력용 한 줄 요약 생성
# ================================

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path


def build_prompt_fingerprint(
    fixed_prompt: str,
    genre_prompt: str,
    dynamic_prompt: str,
    history: list[dict] | None = None,
) -> dict:
    """프롬프트 파트별 fingerprint와 캐싱 관측용 메타데이터를 생성합니다."""
    genre_prompt = genre_prompt or ""
    system_text = f"{fixed_prompt}\n\n{genre_prompt}" if genre_prompt else fixed_prompt
    history_text = _history_to_stable_text(history or [])
    final_text = "\n\n".join(part for part in (system_text, history_text, dynamic_prompt) if part)

    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "parts": {
            "fixed": _fingerprint_text(fixed_prompt),
            "genre": _fingerprint_text(genre_prompt),
            "system": _fingerprint_text(system_text),
            "history": _fingerprint_text(history_text),
            "dynamic": _fingerprint_text(dynamic_prompt),
            "final": _fingerprint_text(final_text),
        },
        "dynamic_markers": _extract_dynamic_markers(dynamic_prompt),
    }


def append_prompt_fingerprint_log(record: dict, logs_dir: Path | str = "logs") -> None:
    """fingerprint record를 logs/prompt_fingerprints.jsonl에 한 줄로 추가합니다."""
    try:
        path = Path(logs_dir)
        path.mkdir(exist_ok=True)
        with open(path / "prompt_fingerprints.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as e:
        print(f"[PromptFingerprint] 저장 실패: {e}")


def format_prompt_fingerprint(record: dict) -> str:
    """프롬프트 fingerprint record를 콘솔용 한 줄로 변환합니다."""
    parts = record.get("parts", {})
    fixed = parts.get("fixed", {})
    genre = parts.get("genre", {})
    dynamic = parts.get("dynamic", {})
    system = parts.get("system", {})
    final = parts.get("final", {})
    return (
        "[PromptFingerprint] "
        f"fixed={fixed.get('sha12')}:{fixed.get('chars')}c | "
        f"genre={genre.get('sha12')}:{genre.get('chars')}c | "
        f"system={system.get('sha12')}:{system.get('chars')}c | "
        f"dynamic={dynamic.get('sha12')}:{dynamic.get('chars')}c | "
        f"final={final.get('sha12')}:{final.get('chars')}c"
    )


def _fingerprint_text(text: str) -> dict:
    """단일 텍스트 블록의 안정성 비교용 해시와 크기를 반환합니다."""
    encoded = text.encode("utf-8")
    return {
        "sha12": hashlib.sha256(encoded).hexdigest()[:12],
        "chars": len(text),
        "bytes": len(encoded),
        "lines": text.count("\n") + (1 if text else 0),
    }


def _history_to_stable_text(history: list[dict]) -> str:
    """대화 히스토리를 내용 해시 산출용 안정 문자열로 직렬화합니다."""
    if not history:
        return ""
    stable = [
        {
            "role": item.get("role", ""),
            "content": item.get("content", ""),
        }
        for item in history
    ]
    return json.dumps(stable, ensure_ascii=False, separators=(",", ":"))


def _extract_dynamic_markers(dynamic_prompt: str) -> list[dict]:
    """동적 프롬프트의 주요 XML/브래킷 블록 위치를 기록합니다."""
    markers: list[dict] = []
    for line_no, line in enumerate(dynamic_prompt.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("<") and stripped.endswith(">"):
            markers.append({"line": line_no, "marker": stripped[:80]})
        elif stripped.startswith("[") and "]" in stripped[:80]:
            markers.append({"line": line_no, "marker": stripped[:80]})
    return markers[:80]
