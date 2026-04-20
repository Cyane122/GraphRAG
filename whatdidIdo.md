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