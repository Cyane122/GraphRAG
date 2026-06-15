# ================================
# src/core/database/driver.py
#
# Kuzu 데이터베이스 드라이버. Neo4j async session 인터페이스와 호환되는 래퍼를 제공하여
# 기존 코드(async with async_driver.session() as session: await session.run(...))를
# 수정 없이 재사용할 수 있게 합니다.
#
# Classes
#   - KuzuAsyncDriver : session() 팩토리 및 동기 execute_sync() 제공
#
# Functions
#   - set_active_driver(driver: KuzuAsyncDriver | None) : 현재 async 컨텍스트의 Kuzu 드라이버 설정
#   - reset_active_driver(token: object) -> None : 이전 Kuzu 드라이버 컨텍스트 복원
#   - current_db_path() -> str | None : 활성 Kuzu 드라이버의 DB 경로(없으면 None)
#   - _get_default_driver() -> KuzuAsyncDriver : 기본 Kuzu 드라이버 지연 생성
#   - _close_default_driver() -> None : 기본 Kuzu 드라이버 종료
#
# (module-level)
#   - async_driver : ProxyDriver 인스턴스 — 세션 드라이버가 있으면 위임, 없으면 기본 드라이버
# ================================

import asyncio
import atexit
from contextvars import ContextVar
from importlib import import_module
from pathlib import Path

import kuzu
from kuzu import QueryResult

from src.assets.worlds.base import World
from src.config import WORLD_ID
from src.core.database.proxy import ProxyDriver
from src.core.database.records import KuzuRecord, KuzuResult
from src.core.database.session import KuzuSession

_active_driver: ContextVar["KuzuAsyncDriver | None"] = ContextVar("active_kuzu_driver", default=None)

# 스키마 업데이트로 추가된 테이블/컬럼이 기존 DB에 없을 수 있으므로 시작 시 마이그레이션 시도
# Kuzu ALTER 문법은 "ADD COLUMN"이 아니라 "ADD"를 사용한다.
_TABLE_MIGRATIONS: list[str] = [
    "CREATE REL TABLE IF NOT EXISTS HAS_STATE(FROM Character TO DynamicState)",
    "CREATE NODE TABLE IF NOT EXISTS DynamicInformation(id STRING, props STRING, PRIMARY KEY(id))",
    "CREATE REL TABLE IF NOT EXISTS HAS_INFO(FROM Character TO DynamicInformation)",
    """CREATE NODE TABLE IF NOT EXISTS NeedsState(
        id STRING,
        hunger DOUBLE, rest DOUBLE, social DOUBLE,
        fun DOUBLE, safety DOUBLE, libido DOUBLE,
        PRIMARY KEY(id)
    )""",
    "CREATE REL TABLE IF NOT EXISTS HAS_NEEDS(FROM Character TO NeedsState)",
    """CREATE NODE TABLE IF NOT EXISTS Rule(
        id STRING,
        name STRING,
        summary STRING,
        prompt_hint STRING,
        prompt_priority INT64,
        tags STRING[],
        location_id STRING,
        owner_id STRING,
        scene_type STRING,
        status STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS SpeechProfile(
        id STRING,
        name STRING,
        summary STRING,
        prompt_hint STRING,
        prompt_priority INT64,
        tags STRING[],
        char_id STRING,
        audience_id STRING,
        scene_type STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS RelationshipProfile(
        id STRING,
        name STRING,
        summary STRING,
        prompt_hint STRING,
        prompt_priority INT64,
        tags STRING[],
        source_id STRING,
        target_id STRING,
        scene_type STRING,
        PRIMARY KEY(id)
    )""",
    "CREATE REL TABLE IF NOT EXISTS HAS_SPEECH_PROFILE(FROM Character TO SpeechProfile)",
    "CREATE REL TABLE IF NOT EXISTS HAS_RELATIONSHIP_PROFILE(FROM Character TO RelationshipProfile)",
    "CREATE REL TABLE IF NOT EXISTS PROFILE_TARGET(FROM RelationshipProfile TO Character)",
    "CREATE REL TABLE IF NOT EXISTS APPLIES_AT(FROM Rule TO Location)",
    "CREATE REL TABLE IF NOT EXISTS RULE_FOR_CHARACTER(FROM Rule TO Character)",
    "CREATE REL TABLE IF NOT EXISTS PART_OF(FROM Location TO Location)",
    "CREATE REL TABLE IF NOT EXISTS PURSUES(FROM Character TO Goal)",
    "CREATE REL TABLE IF NOT EXISTS GOAL_RELATED_EVENT(FROM Goal TO Event)",
    "CREATE REL TABLE IF NOT EXISTS OWNS(FROM Character TO Item)",
    "CREATE REL TABLE IF NOT EXISTS GAVE(FROM Character TO Item)",
    "CREATE REL TABLE IF NOT EXISTS ANCHORS_MEMORY(FROM Item TO Memory)",
    "CREATE REL TABLE IF NOT EXISTS HAS_SECRET(FROM Character TO Secret)",
    "CREATE REL TABLE IF NOT EXISTS ROOTED_IN(FROM Secret TO Event)",
    "CREATE REL TABLE IF NOT EXISTS TRIGGERED_BY(FROM Secret TO Item)",
    "CREATE REL TABLE IF NOT EXISTS EVENT_INVOLVES(FROM StaticEvent TO Character)",
    """CREATE NODE TABLE IF NOT EXISTS Item(
        id STRING,
        name STRING,
        description STRING,
        owner_id STRING,
        location_id STRING,
        emotional_weight INT64,
        visibility STRING,
        last_seen_at STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Goal(
        id STRING,
        owner_id STRING,
        title STRING,
        description STRING,
        status STRING,
        progress INT64,
        subtlety INT64,
        next_hint STRING,
        trigger_conditions STRING,
        completion_conditions STRING,
        last_progressed_at STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Secret(
        id STRING,
        owner_id STRING,
        title STRING,
        private_summary STRING,
        public_hint STRING,
        status STRING,
        sensitivity INT64,
        reveal_conditions STRING,
        current_reveal_level INT64,
        last_hinted_at STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Schedule(
        id STRING,
        owner_id STRING,
        name STRING,
        activity STRING,
        summary STRING,
        prompt_hint STRING,
        prompt_priority INT64,
        material STRING,
        recurrence STRING,
        day_of_week INT64,
        day_of_weeks INT64[],
        date STRING,
        start_time STRING,
        end_time STRING,
        start_minute INT64,
        end_minute INT64,
        location_id STRING,
        status STRING,
        tags STRING[],
        PRIMARY KEY(id)
    )""",
    "CREATE REL TABLE IF NOT EXISTS HAS_SCHEDULE(FROM Character TO Schedule)",
    "CREATE REL TABLE IF NOT EXISTS SCHEDULED_AT(FROM Schedule TO Location)",
    """CREATE NODE TABLE IF NOT EXISTS StaticEvent(
        id STRING,
        name STRING,
        foreshadow_conditions STRING,
        foreshadow_hint STRING,
        trigger_conditions STRING,
        status STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS PersonalFact(
        id STRING,
        subject_id STRING,
        audience_id STRING,
        category STRING,
        fact_text STRING,
        normalized_key STRING,
        status STRING,
        valid_from STRING,
        valid_until STRING,
        confidence DOUBLE,
        source STRING,
        created_at STRING,
        updated_at STRING,
        PRIMARY KEY(id)
    )""",
    "CREATE REL TABLE IF NOT EXISTS KNOWS_FACT(FROM Character TO PersonalFact)",
    """CREATE NODE TABLE IF NOT EXISTS KakaoRoom(
        id STRING,
        name STRING,
        topic STRING,
        status STRING,
        created_at STRING,
        last_active_at STRING,
        PRIMARY KEY(id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS KakaoMessage(
        id STRING,
        room_id STRING,
        sender_id STRING,
        sender_name STRING,
        content STRING,
        timestamp STRING,
        source STRING,
        status STRING,
        PRIMARY KEY(id)
    )""",
    "CREATE REL TABLE IF NOT EXISTS MEMBER_OF(FROM Character TO KakaoRoom)",
    "CREATE REL TABLE IF NOT EXISTS ROOM_HAS_MESSAGE(FROM KakaoRoom TO KakaoMessage)",
    "CREATE REL TABLE IF NOT EXISTS SENT_KAKAO(FROM Character TO KakaoMessage)",
]

_COLUMN_MIGRATIONS: list[str] = [
    "ALTER TABLE DynamicState ADD has_menstrual_cycle BOOLEAN DEFAULT true",
    "ALTER TABLE DynamicState ADD outfit STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD injury_marks STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD pregnant BOOLEAN DEFAULT false",
    "ALTER TABLE DynamicState ADD pregnancy_day INT64 DEFAULT 0",
    "ALTER TABLE DynamicState ADD cum_shots_this_cycle INT64 DEFAULT 0",
    "ALTER TABLE DynamicState ADD ts_acceptance INT64 DEFAULT 0",
    "ALTER TABLE DynamicState ADD northern_attachment INT64 DEFAULT 0",
    "ALTER TABLE DynamicState ADD body_perception STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD behavioral_facade STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD hygiene DOUBLE DEFAULT 0.0",
    "ALTER TABLE DynamicState ADD appearance DOUBLE DEFAULT 0.0",
    "ALTER TABLE DynamicState ADD physique STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD age_presentation STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD nervousness DOUBLE DEFAULT 0.0",
    "ALTER TABLE DynamicState ADD attitude STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD social_skill DOUBLE DEFAULT 0.0",
    "ALTER TABLE DynamicState ADD consideration DOUBLE DEFAULT 0.0",
    "ALTER TABLE DynamicState ADD stamina DOUBLE DEFAULT 0.0",
    "ALTER TABLE DynamicState ADD odor STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD emotional_state STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD attachment_risk DOUBLE DEFAULT 0.0",
    "ALTER TABLE DynamicState ADD expectation_gap DOUBLE DEFAULT 0.0",
    "ALTER TABLE DynamicState ADD penis_size STRING DEFAULT ''",
    "ALTER TABLE DynamicState ADD led_color STRING DEFAULT ''",
    "ALTER TABLE Location ADD district STRING DEFAULT ''",
    "ALTER TABLE Location ADD summary STRING DEFAULT ''",
    "ALTER TABLE Location ADD prompt_hint STRING DEFAULT ''",
    "ALTER TABLE Location ADD prompt_priority INT64 DEFAULT 0",
    "ALTER TABLE Location ADD tags STRING[] DEFAULT []",
    "ALTER TABLE GlobalState ADD today_schedule STRING DEFAULT ''",
    "ALTER TABLE GlobalState ADD schedule_date STRING DEFAULT ''",
    "ALTER TABLE StaticProfile ADD age INT64 DEFAULT 0",
    "ALTER TABLE StaticProfile ADD gender STRING DEFAULT ''",
    "ALTER TABLE StaticProfile ADD role STRING DEFAULT ''",
    "ALTER TABLE Event ADD safety_impact DOUBLE DEFAULT 0.0",
    "ALTER TABLE Event ADD safety_resolved BOOLEAN DEFAULT false",
    "ALTER TABLE Event ADD safety_decay_rate DOUBLE DEFAULT 0.002",
    "ALTER TABLE Event ADD need_name STRING DEFAULT ''",
    "ALTER TABLE Event ADD memory_type STRING DEFAULT 'episodic'",
    "ALTER TABLE Event ADD narrative_summary STRING DEFAULT ''",
    "ALTER TABLE Event ADD state_summary STRING DEFAULT ''",
    "ALTER TABLE Memory ADD memory_type STRING DEFAULT 'episodic'",
    "ALTER TABLE Memory ADD narrative_summary STRING DEFAULT ''",
    "ALTER TABLE Memory ADD state_summary STRING DEFAULT ''",
    "ALTER TABLE Memory ADD status STRING DEFAULT 'active'",
    "ALTER TABLE Memory ADD source_commit_id STRING DEFAULT ''",
    "ALTER TABLE Memory ADD source_type STRING DEFAULT 'direct_experience'",
    "ALTER TABLE Memory ADD confidence DOUBLE DEFAULT 0.75",
    "ALTER TABLE Memory ADD signals STRING DEFAULT '[]'",
    "ALTER TABLE Memory ADD salience DOUBLE DEFAULT 0.0",
    "ALTER TABLE Memory ADD recall_count INT64 DEFAULT 0",
    "ALTER TABLE Memory ADD last_recalled_at STRING DEFAULT ''",
    "ALTER TABLE Memory ADD reinforced_count INT64 DEFAULT 0",
    "ALTER TABLE Memory ADD last_reinforced_at STRING DEFAULT ''",
    "ALTER TABLE Memory ADD resolved_at STRING DEFAULT ''",
    "ALTER TABLE Event ADD source_commit_id STRING DEFAULT ''",
    "ALTER TABLE RELATIONSHIP ADD summary STRING DEFAULT ''",
    "ALTER TABLE RELATIONSHIP ADD active_event_id STRING DEFAULT ''",
    "ALTER TABLE Event ADD content STRING DEFAULT ''",
    "ALTER TABLE Event ADD status STRING DEFAULT 'closed'",
    "ALTER TABLE Event ADD turn_count INT64 DEFAULT 1",
    "ALTER TABLE Schedule ADD day_of_weeks INT64[] DEFAULT []",
    "ALTER TABLE Schedule ADD material STRING DEFAULT ''",
]


# 특정 노드의 속성값을 보정하는 일회성 데이터 패치.
# WHERE 조건으로 이미 값이 있으면 스킵하므로 재실행 안전.
_DATA_PATCHES: list[str] = [
    "MATCH (c:Character), (s:Secret) WHERE c.id = s.owner_id MERGE (c)-[:HAS_SECRET]->(s)",
]


class KuzuAsyncDriver:
    """
    Kuzu 데이터베이스에 대한 async 드라이버.

    런타임(async): session() → KuzuSession.run()
    스키마 초기화(sync): execute_sync() → kuzu.Connection.execute()
    """

    def __init__(self, db_path: str, world_id: str | None = None, scenario_id: str | None = None) -> None:
        self._world_id   = world_id or WORLD_ID
        self._scenario_id = scenario_id
        self._db_path    = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db   = kuzu.Database(db_path)
        self._conn = kuzu.Connection(self._db)
        self._lock = asyncio.Lock()
        self._bootstrap_schema_if_needed()
        self._run_migrations()

    @property
    def db_path(self) -> str:
        """이 드라이버가 연 Kuzu DB 경로(스레드/대화별 고유 — 캐시 격리 키 등에 사용)."""
        return self._db_path

    def close(self) -> None:
        """연결과 DB를 명시적으로 닫아 파일 락을 해제합니다."""
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            self._db.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

    def session(self) -> KuzuSession:
        """KuzuSession을 반환합니다 (async context manager로 사용)."""
        return KuzuSession(self._conn, self._lock)

    def _table_names(self) -> set[str]:
        """현재 Kuzu DB에 존재하는 테이블 이름을 반환합니다."""
        qr = self._conn.execute("CALL show_tables() RETURN name")
        tables: set[str] = set()
        try:
            while qr.has_next():
                row = qr.get_next()
                if row:
                    tables.add(row[0])
        finally:
            try:
                qr.close()
            except Exception:
                pass
        return tables

    def _bootstrap_schema_if_needed(self) -> None:
        """기준 테이블이 없으면 self._world_id의 schema.py로 기본 스키마를 먼저 생성합니다."""
        try:
            required_base_tables = {"Character", "DynamicState", "Location", "GlobalState"}
            missing = required_base_tables - self._table_names()
            if not missing:
                return
        except Exception as exc:
            print(f"[KuzuBootstrap] table scan failed; continuing with migrations: {exc}")
            return

        try:
            module = import_module(f"src.assets.worlds.{self._world_id}.schema")
            scenarios = getattr(module, "SCENARIOS", None)
            world = None
            if isinstance(scenarios, list):
                if self._scenario_id:
                    for sc in scenarios:
                        if sc.scenario_id == self._scenario_id and sc.world is not None:
                            world = sc.world
                            break
                if world is None and scenarios:
                    world = scenarios[0].world
            if world is None:
                world = getattr(module, "world_instance", None) or World()
        except (ModuleNotFoundError, AttributeError):
            world = World()
        print(f"[KuzuBootstrap] base schema missing for '{self._world_id}' ({', '.join(sorted(missing))}). Initializing schema.")
        world.build_schema(self._conn, self._scenario_id)
        # world_editor 의 전역/시나리오 schedule 템플릿을 Schedule 노드로 반영.
        from src.assets.worlds.base import apply_schedule_templates
        apply_schedule_templates(self._conn, self._world_id, self._scenario_id)

    def _run_migrations(self) -> None:
        """기존 DB에 누락된 테이블과 컬럼을 추가한다."""
        tables = self._table_names()
        required_base_tables = {"Character", "DynamicState", "Location", "GlobalState"}
        if not required_base_tables.issubset(tables):
            missing = ", ".join(sorted(required_base_tables - tables))
            print(f"[KuzuMigration] skipped: base schema is missing ({missing}).")
            return

        for ddl in [*_TABLE_MIGRATIONS, *_COLUMN_MIGRATIONS]:
            try:
                self._conn.execute(ddl)
            except Exception as exc:
                message = str(exc).lower()
                if (
                    "already exists" in message
                    or "already has property" in message
                    or "duplicate" in message
                    or "cannot find table" in message
                ):
                    continue
                print(f"[KuzuMigration] skipped failed migration: {ddl} ({exc})")
        self._migrate_has_dynamic_state_to_has_state()
        self._backfill_located_at_from_dynamic_state()
        self._run_data_patches()

    def _run_data_patches(self) -> None:
        """특정 노드 속성값을 보정하는 데이터 패치를 실행합니다."""
        for query in _DATA_PATCHES:
            try:
                qr = self._conn.execute(query)
                try:
                    qr.close()
                except Exception:
                    pass
            except Exception as exc:
                print(f"[KuzuMigration] data patch skipped: {exc}")

    def _migrate_has_dynamic_state_to_has_state(self) -> None:
        """Legacy HAS_DYNAMIC_STATE 관계만 있는 캐릭터에 HAS_STATE 관계를 보강합니다."""
        try:
            qr = self._conn.execute("""
                MATCH (c:Character)-[:HAS_DYNAMIC_STATE]->(d:DynamicState)
                RETURN c.id AS char_id, d.id AS state_id
            """)
        except Exception as exc:
            message = str(exc).lower()
            if "has_dynamic_state" not in message and "cannot find" not in message:
                print(f"[KuzuMigration] HAS_DYNAMIC_STATE scan skipped: {exc}")
            return

        try:
            col_names = qr.get_column_names()
            rows = []
            while qr.has_next():
                rows.append(dict(zip(col_names, qr.get_next())))
        finally:
            try:
                qr.close()
            except Exception:
                pass

        migrated = 0
        already_standard = 0
        for row in rows:
            char_id = row.get("char_id")
            state_id = row.get("state_id")
            if not char_id or not state_id:
                continue
            if self._has_state_relationship(char_id, state_id):
                already_standard += 1
                continue
            try:
                self._conn.execute(
                    """
                    MATCH (c:Character {id: $char_id}), (d:DynamicState {id: $state_id})
                    CREATE (c)-[:HAS_STATE]->(d)
                    """,
                    {"char_id": char_id, "state_id": state_id},
                )
                migrated += 1
            except Exception as exc:
                print(f"[KuzuMigration] HAS_STATE backfill failed for {char_id}: {exc}")
        if migrated:
            print(f"[KuzuMigration] HAS_DYNAMIC_STATE -> HAS_STATE backfilled {migrated} relationship(s)")
        if already_standard:
            print(f"[KuzuMigration] HAS_DYNAMIC_STATE legacy overlap found for {already_standard} relationship(s)")

    def _has_state_relationship(self, char_id: str, state_id: str) -> bool:
        """HAS_STATE 관계 중복 생성을 방지합니다."""
        qr = self._conn.execute(
            """
            MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(d:DynamicState {id: $state_id})
            RETURN d.id AS state_id
            """,
            {"char_id": char_id, "state_id": state_id},
        )
        try:
            return bool(qr.has_next())
        finally:
            try:
                qr.close()
            except Exception:
                pass

    def _has_located_at_relationship(self, char_id: str) -> bool:
        """캐릭터에 LOCATED_AT 관계가 이미 있는지 확인합니다."""
        qr = self._conn.execute(
            """
            MATCH (c:Character {id: $char_id})-[:LOCATED_AT]->(l:Location)
            RETURN l.id AS location_id
            """,
            {"char_id": char_id},
        )
        try:
            return bool(qr.has_next())
        finally:
            try:
                qr.close()
            except Exception:
                pass

    def _location_exists(self, location_id: str) -> bool:
        """Location 노드 존재 여부를 확인합니다."""
        qr = self._conn.execute(
            "MATCH (l:Location {id: $location_id}) RETURN l.id AS location_id",
            {"location_id": location_id},
        )
        try:
            return bool(qr.has_next())
        finally:
            try:
                qr.close()
            except Exception:
                pass

    def _backfill_located_at_from_dynamic_state(self) -> None:
        """DynamicState.location_id만 있는 기존 DB에 LOCATED_AT 관계를 보강합니다."""
        try:
            qr = self._conn.execute(
                """
                MATCH (c:Character)-[:HAS_STATE]->(d:DynamicState)
                RETURN c.id AS char_id, d.location_id AS location_id
                """
            )
        except Exception as exc:
            message = str(exc).lower()
            if "location_id" not in message and "cannot find" not in message:
                print(f"[KuzuMigration] LOCATED_AT backfill scan skipped: {exc}")
            return

        try:
            col_names = qr.get_column_names()
            rows = []
            while qr.has_next():
                rows.append(dict(zip(col_names, qr.get_next())))
        finally:
            try:
                qr.close()
            except Exception:
                pass

        migrated = 0
        for row in rows:
            char_id = row.get("char_id")
            location_id = row.get("location_id")
            if not char_id or not location_id:
                continue
            if self._has_located_at_relationship(char_id) or not self._location_exists(location_id):
                continue
            try:
                self._conn.execute(
                    """
                    MATCH (c:Character {id: $char_id}), (l:Location {id: $location_id})
                    CREATE (c)-[:LOCATED_AT]->(l)
                    """,
                    {"char_id": char_id, "location_id": location_id},
                )
                migrated += 1
            except Exception as exc:
                print(f"[KuzuMigration] LOCATED_AT backfill failed for {char_id}: {exc}")
        if migrated:
            print(f"[KuzuMigration] LOCATED_AT backfilled {migrated} relationship(s)")

    def execute_sync(self, query: str, params: dict | None = None) -> QueryResult | list[QueryResult]:
        """동기 쿼리 실행. 스키마 초기화 CLI 전용."""
        return self._conn.execute(query, params or {})

def _resolve_driver() -> KuzuAsyncDriver:
    """현재 async 컨텍스트의 활성 드라이버를 반환합니다. 설정돼 있지 않으면 기본 드라이버를 쓴다."""
    active_driver = _active_driver.get()
    if isinstance(active_driver, KuzuAsyncDriver):
        return active_driver
    return _get_default_driver()


def current_db_path() -> str | None:
    """현재 async 컨텍스트에 설정된 활성 Kuzu 드라이버의 DB 경로. 활성 드라이버가 없으면 None.

    기본 드라이버를 강제 생성하지 않도록 ContextVar만 확인한다(스레드별 캐시 격리 키 용도).
    """
    driver = _active_driver.get()
    if isinstance(driver, KuzuAsyncDriver):
        return driver.db_path
    return None


def set_active_driver(driver: KuzuAsyncDriver | None):
    """현재 async 컨텍스트에서 사용할 Kuzu 드라이버를 설정하고 reset token을 반환합니다."""
    return _active_driver.set(driver)


def reset_active_driver(token: object) -> None:
    """set_active_driver에서 받은 token으로 이전 드라이버 컨텍스트를 복원합니다."""
    _active_driver.reset(token)


_db_path        = str(Path("graph") / WORLD_ID)
_default_driver: KuzuAsyncDriver | None = None
async_driver    = ProxyDriver(_resolve_driver)


def _get_default_driver() -> KuzuAsyncDriver:
    """Lazily open the process default Kuzu driver."""
    global _default_driver
    if _default_driver is None:
        _default_driver = KuzuAsyncDriver(_db_path)
    return _default_driver


def _close_default_driver() -> None:
    """Close the lazily opened default Kuzu driver at shutdown."""
    if _default_driver is not None:
        _default_driver.close()


atexit.register(_close_default_driver)
