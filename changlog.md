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
- [HOTFIX] 출력 잘림 현상 완화
- 메인 프롬프트 `promptBuilder.py` 동적 모듈화 완료.
  - `schemaBuilder.py`는 더 이상 바베대학교 세계관 스키마를 만들지 않음
  - `graph.world.*`에서 세계관 설정 프롬프트, 세계관 스키마를 제작함
  - `promptBuilder.py`에서 모든 'Eun-seo', 'Sian' 텍스트를 {char}, {user}로 변환함
- `graph.world.default`: `build_schema()` 함수는 이제 GlobalState 노드를 생성함
  - `build_schema()` 함수를 상속받아 현재 시작 위치와 날씨를 지정할 수 있음
- `updater.time_manager`: 이제 AI가 시간을 계산하여 GlobalState 노드에 저장함
- `ooc_parser`: 더 이상 현재 시간과 장소를 계산하지 않음
- `promptBuilder.py`, `babe_univ.py`: 이제 세계관과 범용 프롬프트를 완전히 구분함.
- 이제 `manager_agent.py`가 `run_manager`에서 `world_id`를 변수로 사용함.

## 2026-04-22
- `complex_updater`, `expression_classifier`, `ooc_parser`, `state_updater`, `time_manager` 리팩토링 및 중복된 기능 삭제
- `actor_agent`, `manager_agent` 리팩토링
- `promptBuilder`, `app.py`: 리팩토링 대응 수정
- `db_utils`, `llm_utils` 추가

## 2026-04-23
- 모든 `datetime.now()`를 별도 세계관별 `start_time` 변수로 전환함
  - `app.py`의 로깅용 변수 하나는 제외함