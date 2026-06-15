# ================================
# src/apps/app/runtime.py
#
# Standalone web UI world, driver, and context lifecycle helpers.
#
# Classes
#   - ActiveConversation : Async context manager for one conversation's Kuzu driver.
#
# Functions
#   - discover_world_profiles() -> list[dict] : Discover selectable worlds and scenarios.
#   - initialize_conversation(state: ConversationState) -> ConversationState : Populate world config fields.
#   - sync_conversation_perspective(state: ConversationState) -> ConversationState : Keep persisted POV fields aligned.
#   - resolve_opening_scene(world_id: str, scenario_id: str | None) -> str : Resolve a world/scenario opening scene.
#   - conversation_db_path(thread_id: str) -> str : Resolve the per-conversation Kuzu path.
#   - snapshot_game_time() -> str | None : Read current in-world time from active Kuzu.
#   - restore_game_time(value: str | None) -> None : Restore current in-world time on active Kuzu.
#   - current_game_datetime() -> datetime : Return active in-world datetime or wall-clock fallback.
# ================================

from __future__ import annotations

from datetime import datetime
from importlib import import_module
from pathlib import Path
from types import TracebackType

from src.agents.manager import load_world_instance
from src.core.database import KuzuAsyncDriver
from src.core.database.driver import reset_active_driver, set_active_driver
from src.apps.app.models import ConversationState

_ACTIVE_DRIVERS: dict[str, KuzuAsyncDriver] = {}


def discover_world_profiles() -> list[dict]:
    """Discover selectable worlds and scenarios from schema modules."""
    worlds_dir = Path("src/assets/worlds")
    worlds: list[dict] = []
    for schema_path in sorted(worlds_dir.glob("*/schema.py")):
        world_id = schema_path.parent.name
        if world_id == "default":
            continue
        scenarios: list[dict] = []
        try:
            module = import_module(f"src.assets.worlds.{world_id}.schema")
            scenario_defs = getattr(module, "SCENARIOS", None)
            if isinstance(scenario_defs, list) and scenario_defs:
                scenarios = [
                    {
                        "id": scenario.scenario_id,
                        "label": getattr(scenario, "display_name", None) or scenario.scenario_id,
                    }
                    for scenario in scenario_defs
                ]
            else:
                world = getattr(module, "world_instance", None)
                world_scenarios = getattr(world, "SCENARIOS", None)
                if isinstance(world_scenarios, dict) and world_scenarios:
                    scenarios = [
                        {
                            "id": scenario_id,
                            "label": getattr(scenario, "display_name", None) or scenario_id,
                        }
                        for scenario_id, scenario in world_scenarios.items()
                    ]
        except Exception:
            scenarios = []
        worlds.append(
            {
                "id": world_id,
                "label": world_id,
                "scenarios": scenarios or [{"id": "default", "label": "default"}],
            }
        )
    return worlds


def initialize_conversation(state: ConversationState) -> ConversationState:
    """Populate world-derived fields on a conversation state."""
    scenario_id = state.scenario_id or "default"
    world = load_world_instance(state.world_id, scenario_id)
    perspective = world.get_default_perspective()
    world_config = world.get_full_config(perspective, scenario_id)
    world_config["npc_name_map"] = world.get_npc_name_map()
    perspective = _configured_perspective(world_config, perspective)
    world_config["perspective"] = perspective
    state.scenario_id = scenario_id
    state.perspective = perspective
    state.world_config = world_config
    state.pc_id = world_config["pc_id"]
    state.npc_id = world_config["npc_id"]
    state.npc_name_kor = world_config["npc_name_kor"]
    state.title = f"{state.world_id}/{state.scenario_id}"
    return state


def sync_conversation_perspective(state: ConversationState) -> ConversationState:
    """Align persisted perspective fields with the configured prompt POV."""
    if not state.world_config:
        return initialize_conversation(state)
    perspective = _configured_perspective(state.world_config, state.perspective)
    state.perspective = perspective
    state.world_config["perspective"] = perspective
    return state


def _configured_perspective(world_config: dict, fallback: int) -> int:
    """Return the numeric perspective implied by prompt.pov.mode when present."""
    raw = str(
        world_config.get("pov_mode")
        or world_config.get("pov_type")
        or world_config.get("prompt", {}).get("pov", {}).get("mode")
        or ""
    ).strip().lower()
    if raw.startswith("1p_"):
        return 1
    if raw.startswith("3p_"):
        return 3
    return fallback


def resolve_opening_scene(world_id: str, scenario_id: str | None) -> str:
    """Resolve a world/scenario opening scene without creating a conversation."""
    try:
        state = initialize_conversation(ConversationState(world_id=world_id, scenario_id=scenario_id or "default"))
    except (FileNotFoundError, RuntimeError, KeyError) as exc:
        print(f"[WebApp] opening scene unavailable for {world_id}/{scenario_id or 'default'}: {exc}")
        return ""
    return (
        str(state.world_config.get("opening_scene") or "")
        or str(state.world_config.get("prompt", {}).get("sections", {}).get("opening_scene") or "")
    ).strip()


def conversation_db_path(thread_id: str) -> str:
    """Return the Kuzu DB path for a standalone web conversation."""
    return str(Path("data") / "threads" / thread_id / "schema")


class ActiveConversation:
    """Async context manager that activates one conversation Kuzu driver."""

    def __init__(self, state: ConversationState) -> None:
        """Create a context manager for the given conversation state."""
        self.state = state
        self.driver: KuzuAsyncDriver | None = None
        self.token: object | None = None

    async def __aenter__(self) -> "ActiveConversation":
        """Open or reuse the conversation driver and activate it."""
        db_path = conversation_db_path(self.state.thread_id)
        driver = _ACTIVE_DRIVERS.get(db_path)
        if driver is None:
            scenario_id = self.state.scenario_id or "default"
            driver = KuzuAsyncDriver(db_path, world_id=self.state.world_id, scenario_id=scenario_id)
            _ACTIVE_DRIVERS[db_path] = driver
        self.driver = driver
        self.token = set_active_driver(driver)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Restore the previous active-driver context."""
        if self.token is not None:
            reset_active_driver(self.token)


async def snapshot_game_time() -> str | None:
    """Read current in-world time from the active Kuzu driver."""
    from src.core.database import async_driver

    async with async_driver.session() as session:
        result = await session.run("MATCH (gs:GlobalState {id: 'singleton'}) RETURN gs.currentTime AS ct")
        row = await result.single()
    return str(row["ct"]) if row and row.get("ct") else None


async def restore_game_time(value: str | None) -> None:
    """Restore current in-world time on the active Kuzu driver."""
    if not value:
        return
    from src.core.database import async_driver

    safe_value = value.replace("\\", "\\\\").replace("'", "\\'")
    async with async_driver.session() as session:
        await session.run(f"MATCH (gs:GlobalState {{id: 'singleton'}}) SET gs.currentTime = '{safe_value}'")


async def current_game_datetime() -> datetime:
    """Return active in-world datetime or a wall-clock fallback."""
    raw = await snapshot_game_time()
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now()
