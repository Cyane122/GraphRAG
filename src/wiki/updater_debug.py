# ================================
# src/wiki/updater_debug.py
#
# Wiki Updater의 모델 원문과 검증 결과를 대화별 진단 자료로 보존합니다.
#
# Functions
#   - create_updater_debug_run(debug_root: Path | None, model_name: str, max_attempts: int, user_input_hash: str, actor_response_hash: str) -> Path | None : 단일 Updater 실행의 진단 디렉터리를 만듭니다.
#   - write_updater_attempt_debug(run_dir: Path | None, attempt: int, prompt: str, response_text: str, error: str | None) -> None : 한 시도의 요청·모델 원문·검증 오류를 기록합니다.
#   - finish_updater_debug_run(run_dir: Path | None, status: str, attempts: int, error: str | None = None) -> None : 실행 전체의 최종 상태를 기록합니다.
# ================================

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from uuid import uuid4


logger = logging.getLogger(__name__)


def create_updater_debug_run(
    debug_root: Path | None,
    model_name: str,
    max_attempts: int,
    user_input_hash: str,
    actor_response_hash: str,
) -> Path | None:
    """단일 Updater 실행의 진단 디렉터리를 만들고 경로를 반환합니다."""
    if debug_root is None:
        return None
    created_at = datetime.now(timezone.utc)
    run_name = f"{created_at.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex[:8]}"
    run_dir = debug_root / run_name
    metadata = {
        "created_at": created_at.isoformat(),
        "model": model_name,
        "max_attempts": max_attempts,
        "user_input_hash": user_input_hash,
        "actor_response_hash": actor_response_hash,
    }
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("[WikiUpdaterDebug] failed to create debug run")
        return None
    return run_dir


def write_updater_attempt_debug(
    run_dir: Path | None,
    attempt: int,
    prompt: str,
    response_text: str,
    error: str | None,
) -> None:
    """한 시도의 실제 요청, 모델 원문과 검증 오류를 UTF-8 파일로 기록합니다."""
    if run_dir is None:
        return
    prefix = f"attempt_{attempt:02d}"
    try:
        (run_dir / f"{prefix}_prompt.txt").write_text(prompt, encoding="utf-8")
        (run_dir / f"{prefix}_response.txt").write_text(
            response_text,
            encoding="utf-8",
        )
        if error is not None:
            (run_dir / f"{prefix}_error.txt").write_text(error, encoding="utf-8")
    except OSError:
        logger.exception(
            "[WikiUpdaterDebug] failed to write attempt %s in %s",
            attempt,
            run_dir,
        )


def finish_updater_debug_run(
    run_dir: Path | None,
    status: str,
    attempts: int,
    error: str | None = None,
) -> None:
    """Updater 실행의 성공 또는 실패 상태를 result.json에 기록합니다."""
    if run_dir is None:
        return
    result = {
        "status": status,
        "attempts": attempts,
        "error": error,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        (run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("[WikiUpdaterDebug] failed to finish debug run %s", run_dir)
