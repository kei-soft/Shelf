import re
from threading import Lock

from neo4j import GraphDatabase

from app import config

_driver = None
_driver_key = None
_driver_lock = Lock()

# The fulltext index still post-filters by scope, so it needs a candidate budget. The vector half
# no longer does: SEARCH filters inside the index (see `init`), which is why there is no budget
# to tune there any more.
CANDIDATES = 400

# `source_id` and `version` are declared on the vector index so SEARCH can filter on them inside
# the index. Needs Neo4j 2026.01+; the procedure it replaces is deprecated as of 2026.04.
FILTERABLE = ["embedding", "source_id", "version"]


def driver(s=None):
    global _driver, _driver_key
    s = s if s is not None else config.settings()
    key = (s["NEO4J_URI"], s["NEO4J_USER"], s["NEO4J_PASSWORD"])
    with _driver_lock:
        if _driver is None or _driver_key != key:
            if _driver is not None:
                _driver.close()
            _driver = GraphDatabase.driver(
                key[0], auth=key[1:], connection_timeout=5, max_transaction_retry_time=3
            )
            _driver_key = key
        return _driver


def close():
    global _driver, _driver_key
    with _driver_lock:
        if _driver is not None:
            _driver.close()
        _driver = _driver_key = None


def vector_index(s=None):
    profile = config.embedding_profile(s)
    # Preserve existing OpenAI indexes; other profiles get isolated vector indexes and labels.
    legacy = config.embedding_profile(config.DEFAULTS)
    return (
        ("chunk_vector", "Chunk")
        if profile == legacy
        else (f"chunk_vector_{profile}", f"Embedding_{profile}")
    )


def run(statement, _s=None, **params):
    """_s: settings already read by the caller; avoids re-reading them per Cypher statement."""
    s = _s if _s is not None else config.settings()
    records, _, _ = driver(s).execute_query(
        statement, parameters_=params, database_=s.get("NEO4J_DATABASE", "neo4j")
    )
    return [r.data() for r in records]


def init():
    s = config.settings()
    for label in ["Source", "SourceVersion", "Chunk", "Entity", "Assertion"]:
        run(
            f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE",
            _s=s,
        )
    dims = int(s.get("EMBEDDING_DIMENSIONS", "1536"))
    index, label = vector_index(s)
    existing = run("SHOW INDEXES YIELD name, properties WHERE name=$name RETURN properties", _s=s, name=index)
    if existing and set(existing[0]["properties"]) != set(FILTERABLE):
        # An index predating in-index filtering. Dropping it costs a rebuild from the embeddings
        # already stored on the nodes -- nothing is re-embedded.
        run(f"DROP INDEX {index}", _s=s)
    try:
        run(
            f"CREATE VECTOR INDEX {index} IF NOT EXISTS FOR (c:{label}) ON c.embedding "
            "WITH [c.source_id, c.version] "
            "OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}",
            _s=s,
            dims=dims,
        )
    except Exception as exc:
        if "SyntaxError" not in type(exc).__name__:
            raise
        raise ValueError(
            "Neo4j 2026.01 이상이 필요합니다. 검색 인덱스가 자료별 필터를 지원하지 않는 버전입니다."
        ) from exc
    run("CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text]", _s=s)


def publish(source, version, chunks, assertions):
    """Publish complete new version and switch the active pointer in one transaction."""

    s = config.settings()
    _, label = vector_index(s)
    extractor_model = s["LLM_PROVIDER"] + ":" + s["CHAT_MODEL"]

    def write(tx):
        tx.run(
            "MERGE (s:Source {id:$id}) SET s.title=$title, s.kind=$kind "
            "MERGE (v:SourceVersion {id:$version}) SET v.source_id=$id, v.created_at=datetime() "
            "MERGE (s)-[:HAS_VERSION]->(v)",
            **source,
            version=version,
        ).consume()
        tx.run(
            "UNWIND $chunks AS row MATCH (v:SourceVersion {id:$version}) "
            f"MERGE (c:Chunk {{id:row.id}}) SET c += row SET c:{label} MERGE (v)-[:HAS_CHUNK]->(c)",
            chunks=chunks,
            version=version,
        ).consume()
        tx.run(
            "UNWIND $assertions AS row "
            "MATCH (c:Chunk {id:row.chunk_id}) "
            "MERGE (a:Entity {id:row.subject_id}) SET a.name=row.subject "
            "MERGE (b:Entity {id:row.object_id}) SET b.name=row.object "
            "MERGE (r:Assertion {id:row.id}) SET r.predicate=row.predicate, r.reviewed=false, "
            "r.model=$model, r.version=$version "
            "MERGE (r)-[:SUBJECT]->(a) MERGE (r)-[:OBJECT]->(b) "
            "MERGE (r)-[:SUPPORTED_BY]->(c) "
            "MERGE (c)-[:MENTIONS]->(a) MERGE (c)-[:MENTIONS]->(b)",
            assertions=assertions,
            version=version,
            model=extractor_model,
        ).consume()
        tx.run(
            "MATCH (s:Source {id:$id}) SET s.active_version=$version", id=source["id"], version=version
        ).consume()

    with driver(s).session(database=s.get("NEO4J_DATABASE", "neo4j")) as session:
        session.execute_write(write)


def search(query, vector, scope, limit=8):
    s = config.settings()
    index, label = vector_index(s)
    # Resolving the active versions up front lets both halves filter on chunk properties alone,
    # and the vector half needs them as literals to filter inside the index.
    active = run(
        "MATCH (s:Source) WHERE s.id IN $scope AND s.active_version IS NOT NULL "
        "RETURN s.id AS id, s.active_version AS version, s.title AS title",
        _s=s,
        scope=scope,
    )
    if not active:
        return []
    titles = {row["id"]: row["title"] for row in active}
    versions = [row["version"] for row in active]
    candidates = run(
        f"MATCH (c:{label}) SEARCH c IN ("
        f"VECTOR INDEX {index} FOR $vector "
        "WHERE c.source_id IN $scope AND c.version IN $versions LIMIT 30) SCORE AS score "
        "RETURN c{.*, embedding:null} AS chunk, score ORDER BY score DESC",
        _s=s,
        vector=vector,
        scope=scope,
        versions=versions,
    )
    terms = re.findall(r"[\w]+", query, re.UNICODE)[:30]
    keyword = []
    if terms:
        keyword = run(
            "CALL db.index.fulltext.queryNodes('chunk_text', $query, {limit:$candidates}) YIELD node AS c,score "
            "WHERE c.source_id IN $scope AND c.version IN $versions "
            "RETURN c{.*, embedding:null} AS chunk, score ORDER BY score DESC LIMIT 30",
            _s=s,
            query=" OR ".join(terms),
            candidates=CANDIDATES,
            scope=scope,
            versions=versions,
        )
    fused = {}
    for rows in [candidates, keyword]:
        for rank, row in enumerate(rows):
            chunk = row["chunk"]
            item = fused.setdefault(
                chunk["id"], {**chunk, "title": titles.get(chunk.get("source_id"), ""), "score": 0}
            )
            item["score"] += 1 / (60 + rank + 1)
    return sorted(fused.values(), key=lambda x: x["score"], reverse=True)[:limit]


def passages(scope, ids=None, source_id=None, limit=100):
    return [
        r["chunk"] | {"title": r["title"]}
        for r in run(
            "MATCH (s:Source)-[:HAS_VERSION]->(v:SourceVersion)-[:HAS_CHUNK]->(c:Chunk) "
            "WHERE s.id IN $scope AND s.active_version=v.id "
            "AND ($ids IS NULL OR c.id IN $ids) AND ($source_id IS NULL OR s.id=$source_id) "
            "RETURN c{.*,embedding:null} AS chunk,s.title AS title ORDER BY c.ordinal LIMIT $limit",
            scope=scope,
            ids=ids,
            source_id=source_id,
            limit=limit,
        )
    ]


def relations(scope, ids=None, entity_id=None, limit=60):
    return run(
        "MATCH (s:Source)-[:HAS_VERSION]->(v:SourceVersion)-[:HAS_CHUNK]->(c:Chunk) "
        "MATCH (r:Assertion)-[:SUPPORTED_BY]->(c), (r)-[:SUBJECT]->(a:Entity), (r)-[:OBJECT]->(b:Entity) "
        "WHERE s.id IN $scope AND s.active_version=v.id AND ($ids IS NULL OR c.id IN $ids) "
        "AND ($entity_id IS NULL OR a.id=$entity_id OR b.id=$entity_id) "
        "RETURN DISTINCT r.id AS id,a.id AS subject_id,a.name AS subject,b.id AS object_id,"
        "b.name AS object,r.predicate AS predicate,c.id AS evidence_id,c.text AS text,"
        "c.location AS location,s.title AS title LIMIT $limit",
        scope=scope,
        ids=ids,
        entity_id=entity_id,
        limit=limit,
    )


def delete(source_id):
    def write(tx):
        tx.run(
            "MATCH (:Source {id:$id})-[:HAS_VERSION]->(:SourceVersion)-[:HAS_CHUNK]->(c) "
            "OPTIONAL MATCH (r:Assertion)-[:SUPPORTED_BY]->(c) DETACH DELETE r,c",
            id=source_id,
        ).consume()
        tx.run(
            "MATCH (s:Source {id:$id}) OPTIONAL MATCH (s)-[:HAS_VERSION]->(v) DETACH DELETE s,v", id=source_id
        ).consume()
        tx.run("MATCH (e:Entity) WHERE NOT (e)--() DELETE e").consume()

    s = config.settings()
    with driver(s).session(database=s.get("NEO4J_DATABASE", "neo4j")) as session:
        session.execute_write(write)
