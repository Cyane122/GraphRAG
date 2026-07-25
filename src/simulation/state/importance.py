# ================================
# src/simulation/state/importance.py
#
# 이벤트 중요도(0-10) 채점 루브릭을 단일 정의로 공유합니다.
#
# 세 곳(primary updater / OOC event-only / unified turn extractor)이 동일 문구를
# 쓰도록 상수를 한 곳에 둔다. 핵심: '첫경험'류는 5-7로 뭉개지 않고 8-10으로 채점한다.
#
# Constants
#   - IMPORTANCE_RUBRIC : 프롬프트에 삽입하는 중요도 루브릭 텍스트
# ================================

# 첫경험/최초 사건을 명시적으로 8-10 구간에 배치해, LLM이 불확실할 때 5-7로 수렴하던 문제를 막는다.
IMPORTANCE_RUBRIC = """Importance (0-10):
9-10: Life-altering core memory — first sexual experience, hospitalization, surgery, death, marriage, pregnancy revelation, irreversible betrayal.
8: Major first/milestone — VERY FIRST emotional intimacy, first kiss/confession that lands, first meeting with someone who becomes important.
5-7: Significant but NOT a first — major fight+reconciliation, near-breakup, public humiliation, a repeat of a previously-experienced milestone.
2-4: Minor durable — new injury, new named character, promise, secret, gift, location transition, new object/doc, repeated routine sex/arrangement.
0-1: Routine or atmospheric. Do NOT create unless it leaves a durable record worth remembering.

First-time rule: if the scene is plausibly a character's FIRST time experiencing something significant (first intimacy, first kiss, first confession, first loss), score 8-10 — never collapse a genuine first into the 5-7 band."""
