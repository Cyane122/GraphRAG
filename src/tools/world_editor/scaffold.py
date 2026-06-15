# ================================
# src/tools/world_editor/scaffold.py
#
# 새 월드/캐릭터의 "최소 요건"을 템플릿으로 생성합니다 (대부분 신규 파일 작성 = 안전).
# 생성물은 전부 clean 리터럴(빈 _LOCATIONS/_RULES/_EVENTS, 리터럴 kwargs blob, 무조건 _state,
# 빈 _RELS)이라 이후 편집/추가가 전부 가능합니다. 캐릭터 등록(기존 파일 수정)은 source_edit에 위임.
#
# Functions
#   - create_world(world_id: str, display_name: str) -> dict : 월드 패키지 전체를 스캐폴딩.
#   - character_source(char_id: str, name: str, aliases: list[str], char_type: str, gender: str = "Female") -> str : 성별 _default_cfg 골격으로 캐릭터 .py 소스.
#   - character_source_from_cfg(char_id, name, aliases, char_type, default_cfg: dict) -> str : 재구성된 cfg 를 그대로 주입한 캐릭터 .py 소스.
#   - create_character(world_id: str, char_id: str, name: str, aliases: list[str], char_type: str, gender: str = "Female") -> dict : 파일 생성 + 등록.
#   - _default_cfg(gender: str) -> dict : §8 기본 key 구조 DEFAULT_CFG 를 성별 기반으로 생성.
# ================================

from __future__ import annotations

import re
from pathlib import Path

from src.tools.world_editor import source_create, source_edit
from src.tools.world_editor.worlds import world_pkg_dir

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 여성으로 간주하는 gender 입력값.
_FEMALE_TOKENS = frozenset({"female", "f", "여", "여성", "woman", "girl"})


def _is_female(gender: str) -> bool:
    """gender 문자열이 여성을 가리키는지 판정합니다."""
    return (gender or "Female").strip().lower() in _FEMALE_TOKENS


def _default_cfg(gender: str) -> dict:
    """스펙 §8 기본 key 구조에 맞춘 DEFAULT_CFG dict 를 만듭니다.

    성별에 따라 has_menstrual_cycle 기본값과 여성 전용 / 생리주기 조건부 key 를 포함합니다.
    사용자는 이후 cfg 에디터에서 모든 값을 수정할 수 있습니다.
    """
    female = _is_female(gender)
    static = {
        "birth_year": 2000,
        "birthday": "2000-01-01",
        "gender": "female" if female else "male",
        "nationality": "Korean",
        "formative_background": "",
    }
    personality = {
        "core_traits": "",
        "speech_style": "",
        "dialogue_example": "",
    }
    info = {
        "age": 20,
        "height": "",
        "weight": "",
        "body_type": "",
        "appearance": "",
        "skills": "",
        "hobby": "",
        "current_reputation": "",
        "sexual_information": "",
        "ideal_type": "",
    }
    if female:
        info["measurements"] = ""
    state = {
        "mood": "neutral",
        "emotional_state": "",
        "mental_condition": "",
        "physical_condition": "",
        "stress_level": 2,
        "location_id": "",
        "outfit": "",
        "has_menstrual_cycle": female,
    }
    if female:
        state.update({
            "cycle_day": 1,
            "pregnant": False,
            "pregnancy_day": 0,
            "cum_shots_this_cycle": 0,
        })
    return {"static": static, "personality": personality, "info": info, "state": state}


def _camel(snake: str) -> str:
    """snake_case 식별자를 CamelCase 클래스명으로 변환합니다 (예: kim_nayun → KimNayun)."""
    return "".join(part.capitalize() for part in snake.split("_") if part)


def _ok(message: str, created: list[str] | None = None) -> dict:
    """성공 결과 dict."""
    return {"ok": True, "message": message, "backup": None, "formatted": False, "created": created or []}


def _fail(message: str) -> dict:
    """실패 결과 dict (무변경)."""
    return {"ok": False, "message": message, "backup": None, "formatted": False, "created": []}


# ──────────────────────────────────────────────────────────────────────
# 템플릿 (%%PLACEHOLDER%% 는 str.replace 로 치환 — 코드 내 {} 가 많아 format 미사용)
# ──────────────────────────────────────────────────────────────────────

_SCHEMA_TMPL = '''# ================================
# src/assets/worlds/%%WID%%/schema.py
#
# %%DISPLAY%% 세계 정의. (world_editor 로 생성)
# ================================

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import kuzu

from src.assets.worlds.base import World, Scenario, insert_rule, insert_schedule
from src.assets.worlds.utils import read_inherited_md_map, read_optional_md, parse_few_shot
# 캐릭터 import 는 world_editor 가 캐릭터 생성 시 자동으로 추가합니다.

_PROMPT_DIR = Path(__file__).parent / "prompt"


class %%CLASS%%(World):
    WORLD_ID = "%%WID%%"
    DEFAULT_PERSPECTIVE = (3, "char", False)   # (인칭, 중심 char|user, 사칭)

    SCENE_TYPES: dict[str, str] = {
        "daily": "Everyday life with no significant conflict.",
        "bonding": "Emotional intimacy and closeness between characters.",
    }

    def __init__(self, narrator=None, pc=None, chars=None, perspective=None, scenario_id=None) -> None:
        super().__init__(
            narrator=narrator,
            pc=pc,
            chars=chars or [],
            perspective=perspective,
            scenario_id=scenario_id,
        )
    def get_default_time(self) -> datetime:
        return datetime(2024, 1, 1, 9, 0)

    def get_default_location_id(self) -> str:
        return "%%WID%%_default"

    def get_npc_name_map(self) -> dict:
        npc_map: dict = {}
        for char in self.chars:
            if hasattr(char, "getAlias"):
                npc_map.update(char.getAlias())
        return npc_map

    def get_prompt_config(self, scenario_id: str | None = None) -> dict:
        pov_mode, _impersonation = self.resolve_pov()   # DEFAULT_PERSPECTIVE 3-튜플 → pov_mode
        scene_keys = list(self.SCENE_TYPES)
        char_ids = list(getattr(self, "PROMPT_CHARACTER_IDS", ()))
        scenario_key = scenario_id or self.scenario_id
        _scenario_dir = _PROMPT_DIR / "scenarios" / (scenario_key or "default")
        return {
            "pov": {"mode": pov_mode},
            "sections": {
                "world": read_optional_md(_PROMPT_DIR / "world.md"),
                "prose": read_optional_md(_PROMPT_DIR / "prose.md"),
                "opening_scene": read_optional_md(_scenario_dir / "opening_scene.md"),
                "scenario": read_optional_md(_scenario_dir / "scenario.md"),
            },
            "characters": {
                "focus": {cid: read_optional_md(_PROMPT_DIR / "characters" / f"{cid}.md") for cid in char_ids},
                "blacklist": {cid: read_optional_md(_PROMPT_DIR / "characters" / f"{cid}.cot_append.md") for cid in char_ids},
            },
            "scenes": {
                "prompt": read_inherited_md_map(_PROMPT_DIR, scene_keys, scenario_key, "scenes"),
                "blacklist": read_inherited_md_map(_PROMPT_DIR, scene_keys, scenario_key, "scenes", suffix=".cot_append.md"),
            },
            "blacklist": {
                "world": read_optional_md(_PROMPT_DIR / "cot_append.md"),
                "unified": True,
            },
            "few_shot": {k: parse_few_shot(_PROMPT_DIR / "few_shot" / f"{k}.md") for k in scene_keys},
        }

    def get_full_config(self, perspective=None, scenario_id=None) -> dict:
        res = super().get_full_config(perspective, scenario_id)
        res.update({
            "rating": "r18",
            "world_cot_append": read_optional_md(
                _PROMPT_DIR / "scenarios" / (self.scenario_id or "default") / "cot_append.md"
            ) or read_optional_md(_PROMPT_DIR / "cot_append.md"),
            "prompt": self.get_prompt_config(scenario_id),
        })
        return res

    # ── 시드 데이터 (world_editor 가 항목을 추가/삭제합니다) ──────────

    def _build_locations(self, conn: kuzu.Connection) -> None:
        _LOCATIONS: list[tuple] = [
        ]
        for loc_id, name, desc, hint, priority, tags, _links, scenarios in _LOCATIONS:
            if scenarios and self.scenario_id not in scenarios:
                continue
            conn.execute(
                """CREATE (:Location {
                    id: $id, name: $name, description: $description,
                    prompt_hint: $prompt_hint, prompt_priority: $priority, tags: $tags
                })""",
                {"id": loc_id, "name": name, "description": desc,
                 "prompt_hint": hint, "priority": priority, "tags": tags},
            )
        for loc_id, _n, _d, _h, _p, _t, links, scenarios in _LOCATIONS:
            if scenarios and self.scenario_id not in scenarios:
                continue
            for linked_id in links:
                conn.execute(
                    "MATCH (a:Location {id: $a}), (b:Location {id: $b}) CREATE (a)-[:PART_OF]->(b)",
                    {"a": loc_id, "b": linked_id},
                )

    def _build_rule(self, conn: kuzu.Connection) -> None:
        _RULES: list[tuple] = [
        ]
        for rule_id, name, summary, prompt_hint, priority, tags, location_id, scenarios in _RULES:
            if scenarios and self.scenario_id not in scenarios:
                continue
            insert_rule(
                conn, rule_id=rule_id, name=name, summary=summary,
                prompt_hint=prompt_hint, prompt_priority=priority,
                tags=tags, location_id=location_id,
            )

    def _build_seed_events(self, conn: kuzu.Connection) -> None:
        char_ids = {c.id for c in self.chars}
        _EVENTS: list[dict] = [
        ]
        for ev in _EVENTS:
            involved = ev.pop("_involved", [])
            location_id = ev.pop("_location_id", "")
            if involved and not set(involved).issubset(char_ids):
                continue
            conn.execute(
                """CREATE (:Event {
                    id: $id, summary: $summary, timestamp: $timestamp,
                    importance: $importance, impact: $impact,
                    memory_type: $memory_type, decay_rate: $decay_rate,
                    narrative_summary: $narrative_summary, state_summary: $state_summary,
                    summary_level: $summary_level, embedding: NULL
                })""",
                ev,
            )
            for cid in involved:
                conn.execute(
                    "MATCH (c:Character {id: $cid}), (e:Event {id: $eid}) CREATE (c)-[:INVOLVED_IN]->(e)",
                    {"cid": cid, "eid": ev["id"]},
                )
            if location_id:
                conn.execute(
                    "MATCH (e:Event {id: $eid}), (l:Location {id: $lid}) CREATE (e)-[:OCCURRED_AT]->(l)",
                    {"eid": ev["id"], "lid": location_id},
                )

    def build_schema(self, conn: kuzu.Connection, scenario_id=None) -> None:
        self._build_tables(conn)
        self._build_locations(conn)
        self._build_rule(conn)
        for char in self.chars:
            char.build_schema(conn)
        for char in self.chars:
            for other in self.chars:
                if char.id != other.id:
                    char.build_relationship(conn, other)
        self._build_seed_events(conn)
        self.build_scenario_data(conn, scenario_id)


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="default",
        display_name="%%DISPLAY%%",
        world=%%CLASS%%(
            narrator=None,
            pc=None,
            chars=[],
            scenario_id="default",
        ),
    ),
]

world_instance = SCENARIOS[0].world
'''

_CHARACTER_TMPL = '''# ================================
# src/assets/worlds/%%WID%%/characters/%%CID%%.py
#
# %%NAME%% 캐릭터 정의. (world_editor 로 생성)
#
# Classes
#   - %%CLASS%% : %%NAME%%
# ================================

from __future__ import annotations

import kuzu

from src.assets.worlds.base_character import Character, _insert_rel


class %%CLASS%%(Character):
    id = "%%CID%%"
    name = "%%NAME%%"
    aliases = %%ALIASES%%
    char_type = "%%CTYPE%%"

    # 기본값. SCENARIO_OVERRIDES 에는 시나리오별로 달라지는 값만 delta 로 적습니다.
    # (값 편집은 world_editor 의 캐릭터 cfg 에디터에서도 가능합니다.)
    DEFAULT_CFG: dict = %%DEFAULT_CFG%%
    SCENARIO_OVERRIDES: dict[str, dict] = {}

    def build_schema(self, conn: kuzu.Connection) -> None:
        """캐릭터 노드와 4-tier 프로파일을 self.cfg 기반으로 생성합니다.

        커스텀 노드나 schedule 이 필요하면 super().build_schema(conn) 호출 뒤 추가하세요.
        """
        super().build_schema(conn)

    def build_relationship(self, conn: kuzu.Connection, other: Character) -> None:
        """self → other 방향 관계 엣지를 생성합니다 (world_editor 가 항목을 추가/삭제)."""
        _RELS: dict[str, tuple[str, int, int, str]] = {
        }
        if other.id not in _RELS:
            return
        rel_type, affinity, trust, status = _RELS[other.id]
        _insert_rel(conn, self.id, other.id, rel_type, affinity, trust, status)
'''

# 프롬프트 스텁: (상대경로, 내용)
_PROMPT_FILES: list[tuple[str, str]] = [
    ("world.md", "# %%DISPLAY%%\n\n여기에 세계 설정(배경·규칙·분위기)을 작성하세요.\n"),
    ("prose.md", "# 작법 규칙\n\n문체·시점·묘사 지침을 작성하세요.\n"),
    ("cot_append.md", ""),
    ("scenes/daily.md", "# daily\n\n일상 씬 행동 규칙을 작성하세요.\n"),
    ("scenes/bonding.md", "# bonding\n\n교감 씬 행동 규칙을 작성하세요.\n"),
    ("few_shot/daily.md", "# GOOD\n\n(좋은 예시)\n\n# BAD\n\n(나쁜 예시)\n"),
    ("few_shot/bonding.md", "# GOOD\n\n(좋은 예시)\n\n# BAD\n\n(나쁜 예시)\n"),
    ("scenarios/default/scenario.md", "# %%DISPLAY%%\n\n시나리오 스코프·톤·규칙을 작성하세요.\n"),
    ("scenarios/default/opening_scene.md", "{user}와 {char}의 이야기가 시작된다.\n"),
]


def create_world(world_id: str, display_name: str) -> dict:
    """월드 패키지 전체(schema.py·characters/·prompt/)를 템플릿으로 스캐폴딩합니다."""
    if not _ID_RE.match(world_id or ""):
        return _fail("world_id 는 소문자/숫자/밑줄로, 소문자로 시작해야 합니다.")
    pkg = world_pkg_dir(world_id)
    if pkg.exists():
        return _fail(f"이미 존재하는 월드입니다: {world_id}")

    display = display_name.strip() or world_id
    cls = _camel(world_id)
    created: list[str] = []

    def _write(rel: str, content: str) -> None:
        """패키지 기준 상대경로에 파일을 쓰고 created 에 기록합니다 (상위 디렉터리 자동 생성)."""
        path = pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(rel)

    # 1. 패키지 골격
    _write("__init__.py", f'"""{display} 세계 패키지. (world_editor 로 생성)"""\n')
    _write("characters/__init__.py", '"""캐릭터 클래스 export. (world_editor 가 자동 갱신)"""\n')

    # 2. schema.py
    schema_src = (_SCHEMA_TMPL
                  .replace("%%WID%%", world_id)
                  .replace("%%CLASS%%", cls)
                  .replace("%%DISPLAY%%", display))
    _write("schema.py", schema_src)

    # 3. prompt 스텁들
    for rel, content in _PROMPT_FILES:
        _write(f"prompt/{rel}", content.replace("%%DISPLAY%%", display))

    return _ok(f"월드 '{world_id}' 를 생성했습니다 ({len(created)}개 파일).", created)


def character_source_from_cfg(char_id: str, name: str, aliases: list[str], char_type: str,
                              default_cfg: dict) -> str:
    """재구성된 DEFAULT_CFG(default_cfg)를 그대로 주입해 캐릭터 .py 소스를 만듭니다.

    %%WID%% 는 헤더 주석용 placeholder 로 남겨 둔다(호출부에서 world_id 로 치환).
    default_cfg 는 source_edit._emit 으로 ast 안전 리터럴 직렬화되므로 clean 값만 들어와야 한다.
    자동 파일화(_repair_missing_character_source)가 컴파일된 프로파일 값을 보존할 때 쓴다.
    """
    cfg_src = source_edit._emit(default_cfg, "    ")
    return (_CHARACTER_TMPL
            .replace("%%DEFAULT_CFG%%", cfg_src)
            .replace("%%CID%%", char_id)
            .replace("%%CLASS%%", _camel(char_id))
            .replace("%%NAME%%", name)
            .replace("%%ALIASES%%", repr(list(aliases)))
            .replace("%%CTYPE%%", char_type))


def character_source(char_id: str, name: str, aliases: list[str], char_type: str,
                     gender: str = "Female") -> str:
    """새 캐릭터 .py 소스 문자열을 만듭니다 (성별 기반 _default_cfg 골격 → 전부 편집 가능)."""
    return character_source_from_cfg(char_id, name, list(aliases), char_type,
                                     _default_cfg(gender))


def create_character(world_id: str, char_id: str, name: str, aliases: list[str], char_type: str,
                     gender: str = "Female") -> dict:
    """캐릭터 .py 를 생성하고 characters/__init__.py·schema.py 에 등록합니다."""
    if not _ID_RE.match(char_id or ""):
        return _fail("char_id 는 소문자/숫자/밑줄로, 소문자로 시작해야 합니다.")
    if char_type not in ("PC", "npc"):
        return _fail("char_type 은 'PC' 또는 'npc' 여야 합니다.")
    pkg = world_pkg_dir(world_id)
    if not (pkg / "schema.py").is_file():
        return _fail(f"월드를 찾지 못했습니다: {world_id}")

    char_dir = pkg / "characters"
    char_dir.mkdir(parents=True, exist_ok=True)
    init_path = char_dir / "__init__.py"
    if not init_path.exists():
        init_path.write_text('"""캐릭터 클래스 export."""\n', encoding="utf-8")

    file_path = char_dir / f"{char_id}.py"
    if file_path.exists():
        return _fail(f"이미 존재하는 캐릭터 파일입니다: {char_id}.py")

    # 캐릭터 헤더의 %%WID%% 채우기.
    src = character_source(char_id, name, list(aliases), char_type, gender).replace("%%WID%%", world_id)
    file_path.write_text(src, encoding="utf-8")

    # 기존 파일(__init__·schema) 수정은 source_edit 에 위임 — AST 안전 삽입.
    reg = source_create.register_character(world_id, _camel(char_id), char_id, char_type)
    if not reg.get("ok"):
        # 파일은 만들었으나 등록 실패 — 사용자에게 수동 등록을 안내한다.
        return {"ok": False,
                "message": f"{char_id}.py 는 생성했지만 등록 실패: {reg.get('message')}",
                "backup": reg.get("backup"), "formatted": False, "created": [f"characters/{char_id}.py"]}
    return _ok(f"캐릭터 '{char_id}' 를 생성하고 등록했습니다.", [f"characters/{char_id}.py"])
