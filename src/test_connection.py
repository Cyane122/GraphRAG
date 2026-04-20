from neo4j import GraphDatabase
from dotenv import load_dotenv
import os
from pathlib import Path

load_dotenv()

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URI"),
    auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
)

with driver.session() as session:
    result = session.run("RETURN 'Hello World!' AS message")
    print(result.single()["message"])

driver.close()