"""Quick cross-codebase search test."""
import sqlite3
from waverider.database import DatabaseManager
from waverider.embeddings import OllamaEmbeddings
import time

db = DatabaseManager()
embedder = OllamaEmbeddings(model="nomic-embed-text")

# Build codebase name -> id map
conn = sqlite3.connect("data/waverider.db")
codebase_map = {row[1]: row[0] for row in conn.execute("SELECT id, name FROM codebase_metadata").fetchall()}
conn.close()

tests = [
    ("next-wave", "invoice payment processing"),
    ("payroll", "employee salary calculation"),
    ("identity", "user authentication login"),
    ("reef", "GraphQL resolver query"),
    ("embedded-payroll", "payroll API endpoint"),
]

for codebase, query in tests:
    cid = codebase_map[codebase]
    start = time.time()
    qvec = embedder.embed(query)
    results = db.search_embeddings(qvec, codebase_id=cid, limit=3)
    elapsed = time.time() - start
    print(f"\n{codebase} (id={cid}) - '{query}' ({len(results)} results in {elapsed:.3f}s)")
    for r in results:
        rp = r.get("relative_path", "?")
        sl = r.get("start_line", "?")
        nm = r.get("name", "?")
        sc = r.get("score", 0)
        print(f"  {rp}:{sl} - {nm} (score={sc:.4f})")
