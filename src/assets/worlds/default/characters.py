# ================================
# src/assets/worlds/default/characters.py
#
# Default 세계 캐릭터 스텁. 새 세계관 제작 시 이 파일을 복사·수정합니다.
# 실제 DB 삽입은 DefaultWorld.build_schema()가 인라인으로 처리하므로,
# build_schema / build_relationship 은 현재 no-op.
#
# Classes
#   - Char   : 캐릭터 (NPC / narrator)
#   - Player : 플레이어 (PC)
# ================================

from src.assets.worlds.base_character import Character


class Char(Character):
    """기본 NPC. 새 세계 생성 시 이 클래스를 서술자로 교체합니다."""
    id        = "char"
    name      = "캐릭터"
    aliases   = ["캐릭터"]
    char_type = "npc"


class Player(Character):
    """기본 PC. 새 세계 생성 시 이 클래스를 플레이어로 교체합니다."""
    id        = "player"
    name      = "플레이어"
    aliases   = ["플레이어"]
    char_type = "pc"
