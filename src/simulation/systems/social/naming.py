# ================================
# src/simulation/systems/social/naming.py
#
# Deterministic Korean-name generation and Hangul romanization helpers for transient NPC creation.
#
# Functions
#   - _romanize_hangul(text: str) -> str : Transliterate Hangul syllables to Roman (Revised Romanization approx.)
#   - _kor_to_roman_id(name_kor: str) -> str : Build a name-shaped char_id from a Korean name (e.g. '민지' -> 'minji')
#   - _fallback_given_name(seed_text: str) -> str : Pick a deterministic Korean given name from the descriptor
#   - _alt_surname(seed_text: str, exclude: str = "") -> str : Choose a surname deterministically distinct from the owner
#   - _compose_name(surname: str, seed_text: str) -> tuple[str, str] : Build (Korean name, roman id base) from a surname seed
# ================================
import hashlib

_HANGUL_BASE = 0xAC00

_HANGUL_INITIALS = [
    "g", "kk", "n", "d", "tt", "r", "m", "b", "pp",
    "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h",
]

_HANGUL_MEDIALS = [
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae",
    "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i",
]

_HANGUL_FINALS = [
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l",
    "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t",
]

_FALLBACK_GIVEN_NAMES: tuple[str, ...] = (
    "\ub3c4\uc724",
    "\uc11c\uc900",
    "\ubbfc\uc900",
    "\uc9c0\ud638",
    "\ud558\uc900",
    "\uc720\ucc2c",
    "\uc740\uc6b0",
    "\uc2dc\uc6b0",
)

_KOREAN_SURNAME_ROMAN: dict[str, str] = {
    "\uae40": "kim", "\uc774": "lee", "\ubc15": "park", "\ucd5c": "choi", "\uc815": "jung",
    "\uac15": "kang", "\uc870": "jo", "\uc724": "yoon", "\uc7a5": "jang", "\uc784": "lim",
    "\ud55c": "han", "\uc624": "oh", "\uc11c": "seo", "\uc2e0": "shin", "\uad8c": "kwon",
    "\ud669": "hwang", "\uc548": "ahn", "\uc1a1": "song", "\ub958": "ryu", "\uc804": "jun",
    "\ud64d": "hong", "\uace0": "ko", "\ubb38": "moon", "\uc591": "yang", "\uc190": "son",
    "\ubc30": "bae", "\ubc31": "baek", "\ud5c8": "heo", "\uc720": "yoo", "\ub0a8": "nam",
    "\uc2ec": "shim", "\ub178": "noh", "\ud558": "ha", "\uc9c4": "jin", "\uc5c4": "eom",
    "\ubcc0": "byun", "\uc6b0": "woo", "\uad6c": "koo", "\ubbfc": "min", "\ub098": "na",
}

_FALLBACK_GIVEN_NAMES_ROMAN: tuple[str, ...] = (
    "doyun", "seojun", "minjun", "jiho", "hajun", "yuchan", "eunwoo", "siwoo",
)

_SURNAME_POOL: tuple[str, ...] = tuple(_KOREAN_SURNAME_ROMAN.keys())

def _romanize_hangul(text: str) -> str:
    """한글 음절을 개정 로마자 근사치로 변환. 영숫자는 소문자로 유지, 그 외는 버린다."""
    out: list[str] = []
    for ch in text:
        code = ord(ch) - _HANGUL_BASE
        if 0 <= code < 11172:
            out.append(_HANGUL_INITIALS[code // 588])
            out.append(_HANGUL_MEDIALS[(code % 588) // 28])
            out.append(_HANGUL_FINALS[code % 28])
        elif ch.isascii() and ch.isalnum():
            out.append(ch.lower())
    return "".join(out)

def _kor_to_roman_id(name_kor: str) -> str:
    """한국어 이름을 사람 이름 형태의 char_id 로 변환한다(예: '민지' → 'minji').

    난수/타임스탬프 없이 이름 자체를 쓰고, 중복은 _unique_char_id 가 _2, _3 … 으로
    해소한다. 한글이 전혀 없으면 마지막 안전장치로 'npc' 를 쓴다.
    """
    return _romanize_hangul(name_kor) or "npc"

def _fallback_given_name(seed_text: str) -> str:
    """Pick a deterministic Korean given name from the descriptor."""
    digest = hashlib.sha1(seed_text.encode("utf-8")).digest()[0]
    return _FALLBACK_GIVEN_NAMES[digest % len(_FALLBACK_GIVEN_NAMES)]

def _alt_surname(seed_text: str, exclude: str = "") -> str:
    """소유자와 다른 성을 결정론적으로 고른다(예: 엄마는 자식과 다른 성)."""
    pool = [s for s in _SURNAME_POOL if s != exclude] or list(_SURNAME_POOL)
    digest = hashlib.sha1(seed_text.encode("utf-8")).digest()[1]
    return pool[digest % len(pool)]

def _compose_name(surname: str, seed_text: str) -> tuple[str, str]:
    """성 + 결정론적 이름으로 (한글 이름, 로마자 id base) 쌍을 만든다."""
    given = _fallback_given_name(seed_text)
    surname_roman = _KOREAN_SURNAME_ROMAN.get(surname[0] if surname else "김", "kim")
    digest = hashlib.sha1(seed_text.encode("utf-8")).digest()[0]
    given_roman = _FALLBACK_GIVEN_NAMES_ROMAN[digest % len(_FALLBACK_GIVEN_NAMES_ROMAN)]
    return f"{surname}{given}", f"{surname_roman}_{given_roman}"
