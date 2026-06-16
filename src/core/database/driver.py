# ================================
# src/core/database/driver.py
#
# Kuzu 데이터베이스 드라이버. Neo4j async session 인터페이스와 호환되는 래퍼를 제공하여
# 기존 코드(async with async_driver.session() as session: await session.run(...))를
# 수정 없이 재사용할 수 있게 합니다.
# DDL 마이그레이션 데이터는 migrations.py 참조.
#
# Classes
#   - KuzuAsyncDriver : session()/transaction() 팩토리 및 동기 execute_sync() 제공
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
from datetime import datetime
from importlib import import_module
from pathlib import Path

import kuzu
from kuzu import QueryResult

from src.assets.worlds.base import World
from src.config import WORLD_ID
from src.core.database.migrations import _DATA_PATCHES, migration_ops
from src.core.database.proxy import ProxyDriver
from src.core.database.records import KuzuRecord, KuzuResult
from src.core.database.session import KuzuSession, KuzuTransaction

_active_driver: ContextVar["KuzuAsyncDriver | None"] = ContextVar("active_kuzu_driver", default=None)


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

    def transaction(self) -> KuzuTransaction:
        """원자적 다중 쓰기용 KuzuTransaction을 반환합니다 (async context manager로 사용)."""
        return KuzuTransaction(self._conn, self._lock)

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
        """기존 DB에 누락된 테이블과 컬럼을 추가한다.

        idempotency 판단을 에러 문자열 매칭이 아니라 구조적 introspection + 적용 원장
        (SchemaMigration)으로 한다:
          1. 원장에 이미 기록된 op은 건너뛴다.
          2. 대상 테이블/컬럼이 이미 존재하면(기존 DB) 실행 없이 원장만 보강한다.
          3. rel은 양끝 노드 테이블이 모두 생긴 뒤에만 만든다 — 없으면 기록하지 않고 보류해
             다음 기동 때 재시도한다(이전엔 'cannot find table'을 성공으로 삼켜 영구 누락).
          4. 실행이 'already exists/duplicate' 류로 실패하면 양성으로 보고 원장에 기록,
             그 외 진짜 실패는 기록하지 않고 로깅한다(다음 기동 재시도 + 가시화).
        """
        tables = self._table_names()
        required_base_tables = {"Character", "DynamicState", "Location", "GlobalState"}
        if not required_base_tables.issubset(tables):
            missing = ", ".join(sorted(required_base_tables - tables))
            print(f"[KuzuMigration] skipped: base schema is missing ({missing}).")
            return

        self._ensure_migration_ledger()
        applied = self._load_applied_migrations()
        existing = self._table_names()

        for op in migration_ops():
            if op.id in applied:
                continue

            # 2: 기존 DB에 이미 있는 테이블/컬럼 → 실행 없이 원장만 보강
            if op.kind in ("node", "rel") and op.table in existing:
                self._record_migration(op.id)
                continue
            if op.kind == "column":
                columns = self._table_columns(op.table)
                if columns and op.column in columns:
                    self._record_migration(op.id)
                    continue
                if op.table not in existing:
                    print(f"[KuzuMigration] defer column {op.id}: table '{op.table}' missing")
                    continue

            # 3: rel은 양끝 노드 테이블이 있어야 만든다 (없으면 보류 → 다음 기동 재시도)
            if op.kind == "rel" and op.endpoints:
                frm, to = op.endpoints
                if frm not in existing or to not in existing:
                    print(f"[KuzuMigration] defer rel '{op.table}': endpoint table missing ({frm}, {to})")
                    continue

            try:
                self._conn.execute(op.ddl)
                if op.kind in ("node", "rel"):
                    existing = self._table_names()  # 새 테이블이 이후 rel의 endpoint가 될 수 있다
                self._record_migration(op.id)
            except Exception as exc:
                message = str(exc).lower()
                if "already exists" in message or "already has property" in message or "duplicate" in message:
                    self._record_migration(op.id)  # 양성: 이미 적용됨
                else:
                    print(f"[KuzuMigration] failed migration {op.id}: {exc}")  # 진짜 실패 — 기록 안 함

        self._migrate_has_dynamic_state_to_has_state()
        self._backfill_located_at_from_dynamic_state()
        self._run_data_patches()

    _MIGRATION_LEDGER = "SchemaMigration"

    def _ensure_migration_ledger(self) -> None:
        """적용된 마이그레이션을 기록하는 SchemaMigration 노드 테이블을 보장한다."""
        try:
            self._conn.execute(
                f"CREATE NODE TABLE IF NOT EXISTS {self._MIGRATION_LEDGER}"
                "(id STRING, applied_at STRING, PRIMARY KEY(id))"
            )
        except Exception as exc:
            print(f"[KuzuMigration] ledger init failed (continuing without ledger): {exc}")

    def _load_applied_migrations(self) -> set[str]:
        """원장에 기록된 적용 완료 마이그레이션 id 집합을 반환한다."""
        try:
            qr = self._conn.execute(f"MATCH (m:{self._MIGRATION_LEDGER}) RETURN m.id AS id")
        except Exception:
            return set()
        applied: set[str] = set()
        try:
            while qr.has_next():
                row = qr.get_next()
                if row and row[0]:
                    applied.add(row[0])
        finally:
            try:
                qr.close()
            except Exception:
                pass
        return applied

    def _record_migration(self, migration_id: str) -> None:
        """마이그레이션 적용 사실을 원장에 기록한다(이미 있으면 타임스탬프 갱신)."""
        try:
            self._conn.execute(
                f"MERGE (m:{self._MIGRATION_LEDGER} {{id: $id}}) SET m.applied_at = $ts",
                {"id": migration_id, "ts": datetime.now().isoformat()},
            )
        except Exception as exc:
            print(f"[KuzuMigration] ledger record failed for {migration_id}: {exc}")

    def _table_columns(self, table: str) -> set[str]:
        """주어진 테이블의 컬럼 이름 집합을 반환한다(introspection; 실패 시 빈 집합)."""
        try:
            qr = self._conn.execute(f"CALL table_info('{table}') RETURN name")
        except Exception:
            return set()
        columns: set[str] = set()
        try:
            while qr.has_next():
                row = qr.get_next()
                if row and row[0]:
                    columns.add(row[0])
        finally:
            try:
                qr.close()
            except Exception:
                pass
        return columns

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
