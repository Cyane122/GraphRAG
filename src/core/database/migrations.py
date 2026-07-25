# ================================
# src/core/database/migrations.py
#
# Kuzu DDL 마이그레이션 정의 (TABLE CREATE, COLUMN ADD, DATA PATCH).
# driver.py의 _run_migrations()에서 소비한다.
#
# Classes
#   - MigrationOp : Parsed migration descriptor (stable id + kind + target table/column/endpoints).
#
# Functions
#   - migration_ops() -> list[MigrationOp] : Parse the DDL lists into ordered ops (nodes → rels → columns).
#
# Variables
#   - _TABLE_MIGRATIONS : list[str] — 실행할 CREATE TABLE/REL TABLE DDL 목록
#   - _COLUMN_MIGRATIONS : list[str] — ALTER TABLE ADD COLUMN DDL 목록
#   - _DATA_PATCHES : list[str] — 일회성 데이터 보정 쿼리 목록
# ================================

import re
from dataclasses import dataclass

# 스키마 업데이트로 추가된 테이블/관계가 기존 DB에 없을 수 있으므로 시작 시 마이그레이션 시도
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

# Kuzu ALTER 문법은 "ADD COLUMN"이 아니라 "ADD"를 사용한다.
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


# ── DDL 파서 ────────────────────────────────────────────────
# driver._run_migrations()는 더 이상 DDL을 무작정 실행하고 에러 문자열로 idempotency를
# 판단하지 않는다. 대신 각 DDL을 구조적으로 파싱해 (a) 대상 테이블/컬럼이 이미 있으면
# introspection으로 건너뛰고, (b) rel은 양끝 노드 테이블이 생긴 뒤에 만들고(없으면 다음
# 기동 때까지 보류), (c) 적용 완료를 SchemaMigration 원장에 기록한다.

@dataclass(frozen=True)
class MigrationOp:
    """파싱된 마이그레이션 한 건.

    id        : 원장(ledger) 키. "table:<name>" 또는 "column:<table>.<col>".
    kind      : "node" | "rel" | "column".
    ddl       : 실행할 원본 DDL.
    table     : 대상 테이블(노드/렐/컬럼 모두 해당).
    column    : 컬럼 추가일 때만.
    endpoints : rel일 때 (FROM, TO) 노드 테이블 이름.
    """
    id: str
    kind: str
    ddl: str
    table: str
    column: str | None = None
    endpoints: tuple[str, str] | None = None


_NODE_RE = re.compile(r"CREATE\s+NODE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(", re.IGNORECASE)
_REL_RE = re.compile(
    r"CREATE\s+REL\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(\s*FROM\s+(\w+)\s+TO\s+(\w+)",
    re.IGNORECASE,
)
_COL_RE = re.compile(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+(\w+)", re.IGNORECASE)


def _parse_table_ddl(ddl: str) -> MigrationOp | None:
    """CREATE NODE/REL TABLE DDL을 MigrationOp로 파싱한다."""
    rel = _REL_RE.search(ddl)
    if rel:
        name, frm, to = rel.group(1), rel.group(2), rel.group(3)
        return MigrationOp(id=f"table:{name}", kind="rel", ddl=ddl, table=name, endpoints=(frm, to))
    node = _NODE_RE.search(ddl)
    if node:
        return MigrationOp(id=f"table:{node.group(1)}", kind="node", ddl=ddl, table=node.group(1))
    return None


def _parse_column_ddl(ddl: str) -> MigrationOp | None:
    """ALTER TABLE ... ADD <col> DDL을 MigrationOp로 파싱한다."""
    col = _COL_RE.search(ddl)
    if col:
        table, column = col.group(1), col.group(2)
        return MigrationOp(id=f"column:{table}.{column}", kind="column", ddl=ddl, table=table, column=column)
    return None


def migration_ops() -> list[MigrationOp]:
    """모든 DDL을 적용 순서대로(노드 → rel → 컬럼) 파싱해 반환한다.

    노드를 rel보다 먼저 두어, rel이 참조하는 양끝 테이블이 항상 먼저 생성되게 한다
    (기존 DB에서 rel DDL이 노드보다 앞서 실행돼 'cannot find table'로 영구 누락되던 버그 해결).
    """
    table_ops: list[MigrationOp] = []
    for ddl in _TABLE_MIGRATIONS:
        op = _parse_table_ddl(ddl)
        if op is None:
            print(f"[KuzuMigration] unparsed table DDL (skipped): {ddl[:60]}")
            continue
        table_ops.append(op)

    column_ops: list[MigrationOp] = []
    for ddl in _COLUMN_MIGRATIONS:
        op = _parse_column_ddl(ddl)
        if op is None:
            print(f"[KuzuMigration] unparsed column DDL (skipped): {ddl[:60]}")
            continue
        column_ops.append(op)

    nodes = [op for op in table_ops if op.kind == "node"]
    rels = [op for op in table_ops if op.kind == "rel"]
    return nodes + rels + column_ops
