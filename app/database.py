"""Server-owned PostgreSQL query catalog; never executes SQL supplied by an agent."""

import json
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.config import get
from app.store import now


def db_config():
    path = Path(get("DATABASE_CONFIG", "database.json"))
    if not path.exists():
        raise ValueError("database.example.json을 database.json으로 복사하고 PostgreSQL을 설정해주세요.")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def engine():
    cfg = db_config()
    url = get(cfg["url_env"], "")
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("SOURCE_DATABASE_URL에 postgresql+psycopg:// 읽기 전용 접속 정보를 설정해주세요.")
    return create_engine(url, connect_args={"connect_timeout": 5}, pool_pre_ping=True)


def catalog():
    return {
        name: {"description": q["description"], "parameters": q.get("parameters", {})}
        for name, q in db_config().get("queries", {}).items()
    }


def validate_params(spec, params):
    if set(params) != set(spec):
        raise ValueError(f"필요한 매개변수: {', '.join(spec)}")
    types = {"integer": int, "number": (float, int), "string": str, "boolean": bool}
    for name, kind in spec.items():
        if kind not in types or not isinstance(params[name], types[kind]):
            raise ValueError(f"{name}: {kind} 형식이 필요합니다.")
        if kind in {"integer", "number"} and isinstance(params[name], bool):
            raise ValueError(f"{name}: 숫자가 필요합니다.")
        if isinstance(params[name], str) and len(params[name]) > 1000:
            raise ValueError("매개변수가 너무 깁니다.")


def query(name, parameters):
    cfg = db_config()
    if name not in cfg.get("queries", {}):
        raise ValueError("허용되지 않은 조회입니다.")
    item = cfg["queries"][name]
    validate_params(item.get("parameters", {}), parameters)
    db = engine()
    try:
        with db.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
            result = connection.execution_options(stream_results=True).execute(text(item["sql"]), parameters)
            rows = [dict(row._mapping) for row in result.fetchmany(101)]
            return {
                "query": name,
                "description": item["description"],
                "parameters": parameters,
                "rows": rows[:100],
                "truncated": len(rows) > 100,
                "queried_at": now(),
            }
    finally:
        db.dispose()


def schema_text():
    cfg = db_config()
    db = engine()
    try:
        with db.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '5s'")
            inspector = inspect(connection)
            sections = []
            for schema in cfg.get("schemas", ["public"]):
                for table in cfg.get("tables", []):
                    columns = inspector.get_columns(table, schema=schema)
                    foreign_keys = inspector.get_foreign_keys(table, schema=schema)
                    body = f"테이블 {schema}.{table}\n" + "\n".join(
                        f"{c['name']}: {c['type']} {c.get('comment') or ''}" for c in columns
                    )
                    sections.append((f"{schema}.{table}", body + "\n외래키: " + str(foreign_keys)))
            if not sections:
                raise ValueError("database.json의 tables에 인덱싱할 테이블을 지정해주세요.")
            return sections
    finally:
        db.dispose()
