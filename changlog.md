## 2026-04-19
- GraphRAG 아이디어 브레인스토밍
- 라이브러리 설치했음
- 가상환경도 만들었음
- 그래프 스키마 빌더 (graph.schemaBuilder.py) 만들었음
- 실제로 작동할 Agent 2종 (agents.actor_agent / agents.manager_agent) 만들었음
  - actor_agent: 실제 롤플레이 메인 챗봇
  - manager_agent: 롤플레이 메인 챗봇의 출력으로 인해 변화하는 내용 그래프에 반영
- Neo4j 연결 성공 (test_connection.py)

## 2026-04-20
- Claude Credit $20 결제 완.
- app.py
  - config.toml 수정
  - stylesheet.css 수정
  - `chainlit run app.py`를 통한 실행 구현
- .env
  - 기본 모델 `claude-haiku-4-5-20251001`로 수정
  - Actor 모델:
    - 개발 중: `claude-haiku-4-5-20251001`
    - 튜닝 및 개발 완료: `claude-sonnet-4-6`
    - 목표: `claude-opus-4-7`
  - model classifier: `meta-llama/llama-3.3-70b-instruct:free` / `google/gemma-3-12b-it:free`
- schemaBuilder.py
  - 추가 등장인물 7인
  - HP 제거. 동적 현재 상태 구현 완료
- agents, ooc, updater: 프롬프트 영어로 번역. 프롬프트 추가 튜닝.

## 2026-04-21
- 출력 잘림 현상 완화
- 메인 프롬프트 `promptBuilder.py` 동적 모듈화 완료.
  - `schemaBuilder.py`는 더 이상 바베대학교 세계관 스키마를 만들지 않음
  - `graph.world.*`에서 세계관 설정 프롬프트, 세계관 스키마를 제작함
  - `promptBuilder.py`에서 모든 'Eun-seo', 'Sian' 텍스트를 {char}, {user}로 변환함
- `default.py`: `build_schema()` 함수는 이제 GlobalState 노드를 생성함
  - `build_schema()` 함수를 상속받아 현재 시작 위치와 날씨를 지정할 수 있음
- `time_manager.py`: 이제 AI가 시간을 계산하여 GlobalState 노드에 저장함
- `ooc_parser`: 더 이상 현재 시간과 장소를 계산하지 않음
- `promptBuilder.py`, `babe_univ.py`: 이제 세계관과 범용 프롬프트를 완전히 구분함.
- 이제 `manager_agent.py`가 `run_manager.py`에서 `world_id`를 변수로 사용함.

## 2026-04-22
- `complex_updater.py`, `expression_classifier.py`, `ooc_parser.py`, `state_updater.py`, `time_manager.py` 리팩토링 및 중복된 기능 삭제
- `actor_agent.py`, `manager_agent.py` 리팩토링
- `promptBuilder.py`, `app.py`: 리팩토링 대응 수정
- `utils/db_utils.py`, `utils.llm_utils.py` 추가

## 2026-04-23
- 모든 `datetime.now()`를 별도 세계관별 `start_time` 변수로 전환함
  - `app.py`의 로깅용 변수 하나는 제외함
- `complex_updater.py`, `manager_agent.py`, `time_manager.py`, `app.py`: `async` 관련 비동기 문제점 수정 및 하드코딩 제거
- `manager_agent.py`: 오타 수정
- `app.py`: `response_msg` 이중 호출 문제 수정. 이제 `run_manager.py`의 지연 커밋 로직과 중복되지 않으며, 하드코딩 제거됨
- `llm_utils.py`: `extract_json_from_llm`은 이제 JSON 배열도 파싱할 수 있음
- `promptBuilder.py`: HOT-COLD Channel 프롬프트 추가

## 2026-04-24
- `promptBuilder.py`: 프롬프트 압축 및 개선. 중복된 내용 제거.
- `app.py`: `await` 동기화 오류 수정. 불러오기 기능 추가.
- `example.env`: `.env` 예시 파일 추가
- `embedder.py`: 임베딩 모델 추가
- `conversation_logger.py`: 채팅 저장 및 불러오기 기능 추가.
- [신규] 욕구 시스템 추가
  - `needs_manager.py`
  - `traits_initializer.py`
  - `action_resolver.py`
- [신규] 스키마 대응 추가
  - `babe_univ.py`: `sexual_tendency`, `libido_drive_modifer`, `libido_excluded` 추가. `trait_*` 필드 추가
- `manager_agent.py`, `time_manager.py`, `state_updater.py`, `app.py`: LLM 호출 최적화.
- `app.py`: 코드 간결화 및 리팩토링. 이제 `calculate_and_update_time` 함수를 사용하지 않음. (`manager_agent.py`로 이관)

## 2026-04-25
- [신규] 신규 캐릭터 추가 기능
  - `world/world_builder.py`: 이제 챗봇이 새로운 캐릭터의 노드를 생성함
- `embedder.py`: 원하는 임베딩 모델 사용 가능하도록 개선
- LLM 호출 최적화: `state_updater.py`, `complex_updater.py`
- [신규] 기억 왜곡 기능 추가
  - `decay_manager.py`: 이제 중요하지 않은 기억은 점차 NPC에게 유리하게 변질되어 기억됨
  - `promptBuilder.py`: 이제 왜곡된 기억이라는 사실을 LLM에게 전달함