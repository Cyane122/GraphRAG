# GraphRAG 기반 챗봇 아키텍처

## 파일 구조
```
project-root/
│
├── app.py                           # Chainlit 메인 앱. 세션 초기화·메시지 루프 관리.
│                                    # 유저 입력 수신 → OOC 분기 → Manager 파이프라인 → Actor 스트리밍 응답 출력
│                                    # 이전 턴의 DB 커밋을 다음 입력 시점에 지연 확정(Deferred Commit) 처리
│
└── src/
    │
    ├── agents/
    │   ├── manager_agent.py         # 파이프라인 오케스트레이터.
    │   │                            # (1) 씬 타입 분류 (daily/emotional/physical/intimate 등)
    │   │                            # (2) PromptBuilder 호출로 fixed/genre/dynamic 프롬프트 조립
    │   │                            # (3) 세계 인스턴스 로드 및 World 설정 주입
    │   │                            # OpenRouter를 통해 경량 모델(Gemma/LLaMA)로 씬 분류, 실패 시 fallback -> ['daily']
    │   │
    │   └── actor_agent.py           # Claude 호출 래퍼 (동기 버전, 단독 실행용).
    │                                # Prompt Caching(ephemeral) 적용: fixed_prompt를 system에 캐싱
    │                                # app.py에서는 AsyncAnthropic 스트리밍으로 직접 대체하여 사용
    │
    ├── graph/
    │   ├── schemaBuilder.py         # CLI 스크립트. --world_id 인자로 지정된 World 인스턴스의 build_schema()를 실행해 Neo4j 그래프 스키마를 초기 구축
    │   │
    │   └── world/
    │       ├── default.py           # World 베이스 클래스.
    │       │                        # world_section / specific_prose_rules / few_shot_examples /
    │       │                        # blacklist / npc_name_map / start_time 등 인터페이스 정의
    │       │                        # 모든 세계 모듈은 이 클래스를 상속해 오버라이드
    │       │
    │       └── babe_univ.py         # "바베대학교" 세계 구현체.
    │                                # 캐릭터(은서·시안) 프로필, 세계관 설명, 씬별 작법 규칙,
    │                                # 퓨샷 예시(good/bad), 블랙리스트, Neo4j 초기 노드 데이터 포함
    │
    ├── ooc/
    │   └── ooc_parser.py            # OOC(Out of Character) 명령 파서.
    │                                # *asterisk* 입력 감지 → LLM으로 상태 변화 JSON 추출
    │                                # (시간 경과, 장소 이동, 감정·신체 상태 변경 등)
    │                                # 추출 결과를 db_utils로 Neo4j에 즉시 반영
    │
    ├── prompt/
    │   └── promptBuilder.py         # 3-파트 프롬프트 조립기.
    │                                # [fixed]  operator_policy + rules + world + character (캐시 대상)
    │                                # [genre]  씬 타입별 묘사 규칙 + 퓨샷 예시
    │                                # [dynamic] 현재 헤더(시간·날씨·장소) + Neo4j 컨텍스트 + 유저 입력
    │                                # Neo4j에서 캐릭터 DynamicState·관계·이벤트를 쿼리해 동적 주입
    │
    ├── updater/
    │   ├── state_updater.py         # Actor 응답 후 실행되는 경량 상태 업데이터.
    │   │                            # expression_classifier 결과를 받아 단순 필드 변경은 직접 DB 반영,
    │   │                            # 복합 이벤트(부상·호감도·입원)는 complex_updater로 위임
    │   │
    │   ├── complex_updater.py       # 다중 노드 업데이트 및 이벤트 노드 생성 담당.
    │   │                            # Claude Sonnet으로 업데이트 플랜 생성 → DynamicState 갱신 + 호감도 delta 적용 + Event 노드 Neo4j 저장
    │   │                            # 중요도(0–10) 스케일로 이벤트 생성 여부 판단
    │   │
    │   ├── time_manager.py          # 씬 간 경과 시간 계산 및 GlobalState 업데이트.
    │   │                            # 유저 입력·이전 문맥 분석 → 경과 분(minutes) 및 action_type 추론
    │   │                            # GlobalState.currentTime 갱신, 장소 이동·생리주기 일자 진행 처리
    │   │
    │   └── expression_classifier.py # Actor 응답 텍스트의 표현 분류기.
    │                                # LITERAL(실제 신체 사건) vs FIGURATIVE(감정·비유적 표현) 판별
    │                                # 분류 결과에 따라 DynamicState 업데이트 필드(mood/physical 등) 추출
    │
    └── utils/
        ├── db_utils.py              # Neo4j 공통 CRUD 유틸리티 모음.
        │                            # 싱글톤 AsyncGraphDatabase 드라이버 제공
        │                            # update_dynamic_state / update_relationship_affinity / move_location / advance_cycle_day / get_in_universe_time
        │
        └── llm_utils.py             # LLM 공통 유틸리티. 
                                     # 싱글톤 Anthropic 클라이언트(llm_client) 제공
                                     # extract_json_from_llm(): 마크다운 펜스·trailing comma 제거 후
                                     # 안전하게 JSON 파싱
```