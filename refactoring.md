src/
│
├── assets/                                   ◀ NEW
│   └── worlds/
│       ├── babe_univ/
│       │   ├── world.md                      ◀ babe_univ.py 에서 추출
│       │   ├── prose_3p.md                   ◀ babe_univ.py 에서 추출
│       │   ├── prose_1p.md                   ◀ babe_univ.py 에서 추출
│       │   ├── few_shot/
│       │   │   ├── daily_good.md             ◀ babe_univ.py 에서 추출
│       │   │   └── daily_bad.md              ◀ babe_univ.py 에서 추출
│       │   └── schema.py                     ◀ graph/world/babe_univ.py (프롬프트 제거, 경량화)
│       ├── babe_univ_altered/                ◀ graph/world/babe_univ_altered.py 동일 방식
│       ├── rofan/                            ◀ graph/world/rofan.py 동일 방식
│       └── sses/
│           ├── ...                           ◀ graph/world/sses.py 동일 방식
│           └── schedule_generator.py         ◀ graph/world/sses_schedule_generator.py (이동)
│
├── core/                                     ◀ NEW (구 utils/ + graph/ 인프라)
│   ├── database/
│   │   ├── driver.py                         ◀ utils/db_utils.py 에서 분리 (드라이버 초기화)
│   │   ├── helpers.py                        ◀ utils/db_utils.py 에서 분리 (헬퍼 함수들)
│   │   └── schema_builder.py                 ◀ graph/schemaBuilder.py (이동+개명)
│   ├── llm/
│   │   └── client.py                         ◀ utils/llm_utils.py (이동+개명)
│   ├── embedding/
│   │   └── encoder.py                        ◀ utils/embedder.py (이동+개명)
│   └── logging/
│       └── conversation_logger.py            ◀ utils/conversation_logger.py (이동)
│
├── simulation/                               ◀ NEW (구 memory/ + needs/ + updater/ + world/)
│   ├── engine.py                             ◀ NEW (시뮬레이션 루프 총괄)
│   ├── systems/
│   │   ├── memory.py                         ◀ memory/decay_manager.py (이동+개명)
│   │   ├── needs.py                          ◀ needs/needs_manager.py
│   │   │                                        + needs/traits_initializer.py 통합
│   │   ├── organic.py                        ◀ updater/pregnancy_manager.py (이동+개명)
│   │   └── social.py                         ◀ world/world_narrator.py
│   │                                            + world/world_builder.py 통합
│   └── state/
│       ├── updater.py                        ◀ updater/state_updater.py
│       │                                        + updater/time_manager.py
│       │                                        + updater/complex_updater.py 통합
│       └── classifier.py                     ◀ updater/expression_classifier.py (이동+개명)
│
└── agents/                                   ◀ 기존 유지, 내부 개편
    ├── actor.py                              ◀ agents/actor_agent.py (개명)
    ├── manager.py                            ◀ agents/manager_agent.py (개명)
    ├── resolver.py                           ◀ needs/action_resolver.py (이동+개명)
    └── prompt_factory/
        ├── builder.py                        ◀ prompt/promptBuilder.py (이동+개명, 경량화)
        └── ooc_handler.py                    ◀ ooc/ooc_parser.py (이동+개명)
app.py                                        ◀ 사용자 상호작용용 엔트리 포인트

위의 방식으로 리팩토링할 생각이야. 다만, import 경로가 자주 바뀔 수 있으므로, 한 폴더 옮기고 테스트, 다른 폴더 옮기고 테스트, ... 하는 방식으로 진행해.

이 과정을 단계적으로 수행해.
1. 폴더 생성
2. utils부터 core로 이동하고, import 일괄 수정
3. memory/needs/updater 이동 및 통합
4. agents 이동 및 정리.

아래 폴더들은 분산/통합/흡수되어 완전히 사라질 거야.
src/
├── src/graph/          → core/database/ + assets/worlds/ 로 분산
├── src/memory/         → simulation/systems/memory.py 로 흡수
├── src/needs/          → simulation/systems/needs.py + agents/resolver.py 로 분산
├── src/ooc/            → agents/prompt_factory/ooc_handler.py 로 흡수
├── src/prompt/         → agents/prompt_factory/builder.py 로 흡수
├── src/updater/        → simulation/state/ 로 흡수
├── src/utils/          → core/ 로 흡수
└── src/world/          → simulation/systems/social.py 로 흡수