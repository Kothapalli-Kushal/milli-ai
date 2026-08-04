
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
import asyncio
import json
import os
import re
from urllib.parse import unquote
from sqlalchemy import create_engine, text
from core.config import load_settings, DATA_DIR

# Initialize MCP Server
app = Server("sql-mcp-server")

# Per-db_id engine cache: db_id -> (engine, connection_string)
_engines: dict[str, tuple] = {}

_SYSTEM_SCHEMAS = {
    'information_schema', 'sys', 'guest', 'db_accessadmin',
    'db_backupoperator', 'db_datareader', 'db_datawriter',
    'db_ddladmin', 'db_denydatareader', 'db_denydatawriter',
    'db_owner', 'db_securityadmin',
}

# Mutation keywords that indicate a write operation
_WRITE_KEYWORDS = {
    "insert", "update", "delete", "drop", "create", "alter",
    "truncate", "replace", "merge", "upsert", "grant", "revoke",
}


def _is_write_query(query: str) -> bool:
    first_word = query.strip().split()[0].lower() if query.strip() else ""
    return first_word in _WRITE_KEYWORDS


def _load_db_configs() -> list[dict]:
    """Load db_configs.json from the data directory."""
    path = os.path.join(DATA_DIR, "db_configs.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def _extract_database_name(connection_string: str) -> str | None:
    """Extract the DATABASE name from an ODBC or SQLAlchemy connection string."""
    # SQLAlchemy URL-encodes the inner ODBC string (`odbc_connect=DATABASE%3D...`);
    # decode once so the same regex matches both raw ODBC and URL-encoded forms.
    haystack = unquote(connection_string)
    match = re.search(r'DATABASE=([^;]+)', haystack, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _engine_kwargs(conn_str: str) -> dict:
    """Engine kwargs shared by all connections.

    - connect timeout: bound the TCP/login handshake so a slow or unreachable
      DB server cannot block past the MCP tool-call timeout (60s). pyodbc uses
      the `timeout` kwarg (login timeout); psycopg/pymysql use `connect_timeout`.
    - pool_pre_ping: health-check pooled connections before use so stale
      connections (dropped by the server or a network blip) are transparently
      replaced instead of hanging on the first execute().
    """
    lowered = conn_str.lower()
    if "pyodbc" in lowered or "odbc_connect" in lowered:
        connect_args = {"timeout": 10}
    else:
        connect_args = {"connect_timeout": 10}
    return {"echo": False, "pool_pre_ping": True, "connect_args": connect_args}


def get_db_engine(db_id: str | None = None):
    """Return (engine, db_name) for the given db_id, or fall back to global setting."""
    global _engines

    if db_id:
        if db_id in _engines:
            return _engines[db_id]
        configs = _load_db_configs()
        config = next((c for c in configs if c.get("id") == db_id), None)
        if not config:
            raise ValueError(f"No database config found for db_id='{db_id}'.")
        conn_str = config.get("connection_string", "")
        if not conn_str:
            raise ValueError(f"Database config '{db_id}' has no connection string.")
        db_name = _extract_database_name(conn_str)
        if not db_name:
            raise ValueError(f"Could not extract DATABASE name from connection string for db_id='{db_id}'.")
        engine = create_engine(conn_str, **_engine_kwargs(conn_str))
        _engines[db_id] = (engine, db_name)
        return engine, db_name

    # Fallback: global sql_connection_string from settings
    settings = load_settings()
    db_url = settings.get("sql_connection_string", "")
    if not db_url:
        # Auto-select the only configured database when there is exactly one
        configs = _load_db_configs()
        if len(configs) == 1:
            return get_db_engine(configs[0]["id"])
        raise ValueError("No db_id provided and no global SQL connection string found in settings.")
    cache_key = "__global__"
    if cache_key in _engines:
        return _engines[cache_key]
    db_name = _extract_database_name(db_url)
    if not db_name:
        raise ValueError("Could not extract DATABASE name from global SQL connection string.")
    engine = create_engine(db_url, **_engine_kwargs(db_url))
    _engines[cache_key] = (engine, db_name)
    return engine, db_name


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    db_id_prop = {
        "db_id": {
            "type": "string",
            "description": "The ID of the database config to use (from LINKED DATABASES in your system prompt). Required when multiple databases are configured."
        }
    }
    return [
        types.Tool(
            name="list_tables",
            description="List all tables in a database. Provide db_id when multiple databases are linked.",
            inputSchema={
                "type": "object",
                "properties": db_id_prop,
            }
        ),
        types.Tool(
            name="get_table_schema",
            description="Get the detailed schema (columns, types, foreign keys) for specific table(s). Provide db_id when multiple databases are linked.",
            inputSchema={
                "type": "object",
                "properties": {
                    **db_id_prop,
                    "table_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of table names to inspect."
                    }
                },
                "required": ["table_names"]
            }
        ),
        types.Tool(
            name="run_sql_query",
            description=(
                "Execute a SQL query against a linked database. "
                "Provide db_id when multiple databases are linked. "
                "Write queries (INSERT/UPDATE/DELETE/DROP/etc.) require allow_db_write to be enabled in settings "
                "AND explicit user confirmation BEFORE calling this tool."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **db_id_prop,
                    "query": {
                        "type": "string",
                        "description": "The SQL query to execute."
                    }
                },
                "required": ["query"]
            }
        )
    ]


def _run_list_tables(engine, db: str) -> str:
    """Blocking DB work for list_tables — must run in a worker thread."""
    with engine.connect() as conn:
        # Get all non-system schemas from the target database
        schema_rows = conn.execute(text(f"""
            SELECT name
            FROM [{db}].sys.schemas
            WHERE name NOT IN (
                'sys','guest','INFORMATION_SCHEMA',
                'db_owner','db_datareader','db_datawriter',
                'db_ddladmin','db_denydatareader','db_denydatawriter',
                'db_accessadmin','db_backupoperator','db_securityadmin'
            )
            ORDER BY name
        """)).fetchall()
        schema_names = [row[0] for row in schema_rows] or ["dbo"]

        all_tables = []
        for schema_name in schema_names:
            table_rows = conn.execute(text(f"""
                SELECT t.name
                FROM [{db}].sys.tables t
                JOIN [{db}].sys.schemas s ON s.schema_id = t.schema_id
                WHERE s.name = :schema
                ORDER BY t.name
            """), {"schema": schema_name}).fetchall()
            for (table_name,) in table_rows:
                all_tables.append(f"{schema_name}.{table_name}")

    return f"Found {len(all_tables)} tables:\n" + "\n".join([f"- {t}" for t in all_tables])


def _run_get_table_schema(engine, db: str, table_names: list[str]) -> str:
    """Blocking DB work for get_table_schema — must run in a worker thread."""
    schemas = []
    with engine.connect() as conn:
        for table in table_names:
            try:
                # Support schema-qualified names like "dbo.MyTable"
                if "." in table:
                    schema_name, table_name = table.split(".", 1)
                else:
                    schema_name, table_name = "dbo", table

                # Get columns with type info
                col_rows = conn.execute(text(f"""
                    SELECT
                        c.name,
                        tp.name AS type_name,
                        c.is_nullable,
                        CASE WHEN ic.column_id IS NOT NULL THEN 1 ELSE 0 END AS is_pk
                    FROM [{db}].sys.columns c
                    JOIN [{db}].sys.objects o ON o.object_id = c.object_id
                    JOIN [{db}].sys.schemas s ON s.schema_id = o.schema_id
                    JOIN [{db}].sys.types tp ON tp.user_type_id = c.user_type_id
                    LEFT JOIN [{db}].sys.indexes i
                        ON i.object_id = o.object_id AND i.is_primary_key = 1
                    LEFT JOIN [{db}].sys.index_columns ic
                        ON ic.object_id = o.object_id
                       AND ic.index_id = i.index_id
                       AND ic.column_id = c.column_id
                    WHERE s.name = :schema AND o.name = :table
                    ORDER BY c.column_id
                """), {"schema": schema_name, "table": table_name}).fetchall()

                # Get foreign keys
                fk_rows = conn.execute(text(f"""
                    SELECT
                        fkc.parent_column_id,
                        cp.name AS parent_col,
                        rs.name AS ref_schema,
                        rt.name AS ref_table,
                        cr.name AS ref_col
                    FROM [{db}].sys.foreign_keys fk
                    JOIN [{db}].sys.foreign_key_columns fkc
                        ON fkc.constraint_object_id = fk.object_id
                    JOIN [{db}].sys.objects tp ON tp.object_id = fk.parent_object_id
                    JOIN [{db}].sys.schemas ps ON ps.schema_id = tp.schema_id
                    JOIN [{db}].sys.columns cp
                        ON cp.object_id = fk.parent_object_id
                       AND cp.column_id = fkc.parent_column_id
                    JOIN [{db}].sys.objects rt ON rt.object_id = fk.referenced_object_id
                    JOIN [{db}].sys.schemas rs ON rs.schema_id = rt.schema_id
                    JOIN [{db}].sys.columns cr
                        ON cr.object_id = fk.referenced_object_id
                       AND cr.column_id = fkc.referenced_column_id
                    WHERE ps.name = :schema AND tp.name = :table
                """), {"schema": schema_name, "table": table_name}).fetchall()

                col_strings = []
                for col in col_rows:
                    nullable = "NULL" if col[2] else "NOT NULL"
                    pk_marker = " (PK)" if col[3] else ""
                    col_strings.append(f"- {col[0]} ({col[1]}) {nullable}{pk_marker}")

                fk_strings = [
                    f"-> FK: {row[1]} -> {row[2]}.{row[3]}.{row[4]}"
                    for row in fk_rows
                ]

                schema_text = (
                    f"**Table: {schema_name}.{table_name}**\nColumns:\n" +
                    "\n".join(col_strings) +
                    ("\nForeign Keys:\n" + "\n".join(fk_strings) if fk_strings else "")
                )
                schemas.append(schema_text)
            except Exception as e:
                schemas.append(f"Error inspecting table {table}: {str(e)}")
    return "\n\n".join(schemas)


def _run_sql_query(engine, query: str) -> str:
    """Blocking DB work for run_sql_query — must run in a worker thread."""
    with engine.connect() as connection:
        result = connection.execute(text(query))
        # Commit for write operations so changes persist
        if _is_write_query(query):
            connection.commit()
            return f"Query executed successfully. Rows affected: {result.rowcount}"
        keys = list(result.keys())
        rows = [dict(zip(keys, row)) for row in result.fetchall()]
        if len(rows) > 50:
            rows = rows[:50]
            suffix = "\n...(Truncated to 50 rows)"
        else:
            suffix = ""
        return json.dumps(rows, default=str, indent=2) + suffix


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    try:
        db_id = arguments.get("db_id") or None
        # get_db_engine may hit the filesystem/settings but never the network;
        # engine creation is lazy so it is safe on the event loop. All actual
        # DB I/O below is offloaded to a worker thread via asyncio.to_thread so
        # a slow query or dead connection can never freeze the event loop and
        # starve the MCP stdio reader/writer tasks.
        engine, db_name = get_db_engine(db_id)
        db = db_name  # e.g. "ALDB_VEN"

        if name == "list_tables":
            text_out = await asyncio.to_thread(_run_list_tables, engine, db)
            return [types.TextContent(type="text", text=text_out)]

        elif name == "get_table_schema":
            table_names = arguments.get("table_names", [])
            text_out = await asyncio.to_thread(_run_get_table_schema, engine, db, table_names)
            return [types.TextContent(type="text", text=text_out)]

        elif name == "run_sql_query":
            query = arguments.get("query", "").strip()

            if _is_write_query(query):
                settings = load_settings()
                allow_db_write = settings.get("allow_db_write", False)
                if not allow_db_write:
                    return [types.TextContent(
                        type="text",
                        text=(
                            "BLOCKED: This query modifies the database and DB write access is currently disabled. "
                            "A user with admin access must enable 'Allow agents to modify database' in General Settings."
                        )
                    )]

            text_out = await asyncio.to_thread(_run_sql_query, engine, query)
            return [types.TextContent(type="text", text=text_out)]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())