# ================================
# src/assets/worlds/utils.py
#
# 세계 모듈 공유 유틸리티.
# 각 schema.py에 중복 정의되던 파일 로더와 파서를 여기서 관리합니다.
#
# Functions
#   - read_md(prompt_dir: Path, path: str) -> str : prompt/ 하위 .md 파일을 문자열로 반환
#   - parse_few_shot(path: Path) -> dict : # GOOD / # BAD 섹션을 파싱
# ================================

import re
from pathlib import Path


def read_md(prompt_dir: Path, path: str) -> str:
    """prompt/ 하위 .md 파일을 읽어 문자열로 반환합니다."""
    return (prompt_dir / path).read_text(encoding="utf-8")


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
