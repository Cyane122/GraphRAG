# ================================
# src/assets/worlds/utils.py
#
# 세계 모듈 공유 유틸리티.
# 각 schema.py에 중복 정의되던 파일 로더와 파서를 여기서 관리합니다.
#
# Functions
#   - read_md(prompt_dir: Path, path: str) -> str : prompt/ 하위 .md 파일을 문자열로 반환
#   - read_optional_md(path: Path) -> str : 파일이 있으면 Markdown 문자열을 반환
#   - read_md_map(directory: Path, keys: list[str], suffix: str = ".md") -> dict[str, str] : key별 Markdown 파일 로드
#   - read_inherited_md_map(prompt_dir: Path, keys: list[str], scenario_id: str | None, directory: str = "scenes", suffix: str = ".md") -> dict[str, str] : 시나리오 파일을 월드 파일 위에 덮어 읽기
#   - read_few_shot_map(directory: Path, keys: list[str]) -> dict : key별 few-shot 파일 로드
#   - parse_few_shot(path: Path) -> dict : # GOOD / # BAD 섹션을 파싱
# ================================

import re
from pathlib import Path


def read_md(prompt_dir: Path, path: str) -> str:
    """prompt/ 하위 .md 파일을 읽어 문자열로 반환합니다."""
    return (prompt_dir / path).read_text(encoding="utf-8")


def read_optional_md(path: Path) -> str:
    """파일이 있으면 읽고, 없으면 빈 문자열을 반환합니다."""
    return path.read_text(encoding="utf-8") if path.exists() else ""


def read_md_map(directory: Path, keys: list[str], suffix: str = ".md") -> dict[str, str]:
    """디렉터리에서 key 목록에 대응하는 Markdown 파일들을 읽습니다."""
    result: dict[str, str] = {}
    for key in keys:
        text = read_optional_md(directory / f"{key}{suffix}")
        if text:
            result[key] = text
    return result


def read_inherited_md_map(
    prompt_dir: Path,
    keys: list[str],
    scenario_id: str | None,
    directory: str = "scenes",
    suffix: str = ".md",
) -> dict[str, str]:
    """월드 공통 파일을 읽고, 같은 키의 시나리오 파일이 있으면 덮어씁니다."""
    result = read_md_map(prompt_dir / directory, keys, suffix=suffix)
    scenario_key = scenario_id or "default"
    scenario_dir = prompt_dir / "scenarios" / scenario_key / directory
    result.update(read_md_map(scenario_dir, keys, suffix=suffix))
    return result


def read_few_shot_map(directory: Path, keys: list[str]) -> dict:
    """디렉터리에서 key 목록에 대응하는 few-shot 파일들을 읽습니다."""
    result = {}
    for key in keys:
        entry = parse_few_shot(directory / f"{key}.md")
        if entry["good"] or entry["bad"]:
            result[key] = entry
    return result


def parse_few_shot(path: Path) -> dict:
    """# GOOD / # BAD 섹션을 {"good": [...], "bad": [...]} 로 파싱합니다."""
    if not path.exists():
        return {"good": [], "bad": []}
    text = path.read_text(encoding="utf-8")
    good_m = re.search(r'#\s*GOOD\s*\n(.*?)(?=\n#\s*BAD|\Z)', text, re.DOTALL)
    bad_m  = re.search(r'#\s*BAD\s*\n(.*?)$', text, re.DOTALL)
    good = [s.strip() for s in good_m.group(1).split("\n---\n") if s.strip()] if good_m else []
    bad  = [s.strip() for s in bad_m.group(1).split("\n---\n")  if s.strip()] if bad_m  else []
    return {"good": good, "bad": bad}
