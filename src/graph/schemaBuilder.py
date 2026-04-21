from importlib import import_module

from neo4j import GraphDatabase
from dotenv import load_dotenv
from pathlib import Path
import os
import argparse

from src.graph.world.default import World

load_dotenv(Path(__file__).parent.parent.parent / ".env")

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--world_id", type=str, default="default", help="world that you want to build a graph")
    args = parser.parse_args()

    world_id = args.world_id
    try:
        module = import_module(f"src.graph.world.{world_id}")
        world = module.world_instance
    except (ModuleNotFoundError, AttributeError):
        world = World()

    print(f"현재 World: [{world_id}]")
    try:
        world.build_schema(driver)
    finally:
        driver.close()