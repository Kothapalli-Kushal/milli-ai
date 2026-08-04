"""
DB configuration management endpoints (CRUD + schema refresh).
"""
import os
import re
from datetime import datetime
from urllib.parse import unquote
from fastapi import APIRouter, HTTPException
from core.models import DBConfig
from core.config import DATA_DIR
from core.json_store import JsonStore

_SYSTEM_SCHEMAS = {
    'information_schema', 'sys', 'guest', 'db_accessadmin',
    'db_backupoperator', 'db_datareader', 'db_datawriter',
    'db_ddladmin', 'db_denydatareader', 'db_denydatawriter',
    'db_owner', 'db_securityadmin',
}


def _extract_database_name(connection_string: str) -> str | None:
    """Extract the DATABASE name from an ODBC or SQLAlchemy connection string."""
    # SQLAlchemy URL-encodes the inner ODBC string (`odbc_connect=DATABASE%3D...`);
    # decode once so the same regex matches both raw ODBC and URL-encoded forms.
    haystack = unquote(connection_string)
    match = re.search(r'DATABASE=([^;]+)', haystack, re.IGNORECASE)
    return match.group(1).strip() if match else None


router = APIRouter()

_db_configs_store = JsonStore(os.path.join(DATA_DIR, "db_configs.json"))


def load_db_configs() -> list[dict]:
    return _db_configs_store.load()


def save_db_configs(configs: list[dict]):
    _db_configs_store.save(configs)


@router.get("/api/db-configs")
async def get_db_configs():
    return load_db_configs()


@router.post("/api/db-configs")
async def create_db_config(config: DBConfig):
    configs = load_db_configs()
    for i, c in enumerate(configs):
        if c["id"] == config.id:
            configs[i] = config.dict()
            save_db_configs(configs)
            return config
    configs.append(config.dict())
    save_db_configs(configs)
    return config


@router.delete("/api/db-configs/{config_id}")
async def delete_db_config(config_id: str):
    configs = load_db_configs()
    configs = [c for c in configs if c["id"] != config_id]
    save_db_configs(configs)
    return {"status": "success"}


@router.post("/api/db-configs/{config_id}/refresh-schema")
async def refresh_db_schema(config_id: str):
    configs = load_db_configs()
    config = next((c for c in configs if c["id"] == config_id), None)
    if not config:
        raise HTTPException(status_code=404, detail="DB config not found")

    connection_string = config.get("connection_string", "")
    if not connection_string:
        raise HTTPException(status_code=400, detail="No connection string configured")

    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(connection_string, connect_args={"connect_timeout": 10})

        db_name = _extract_database_name(connection_string)
        if not db_name:
            raise ValueError("Could not extract DATABASE name from connection string.")

        db = db_name  # e.g. "ALDB_VEN"

        schema_lines = []
        with engine.connect() as conn:
            # 1. Get all non-system schemas from the target database
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

            # 2. For each schema, get tables and their columns
            for schema_name in schema_names:
                table_rows = conn.execute(text(f"""
                    SELECT t.name
                    FROM [{db}].sys.tables t
                    JOIN [{db}].sys.schemas s ON s.schema_id = t.schema_id
                    WHERE s.name = :schema
                    ORDER BY t.name
                """), {"schema": schema_name}).fetchall()

                for (table_name,) in table_rows:
                    col_rows = conn.execute(text(f"""
                        SELECT c.name, tp.name AS type_name
                        FROM [{db}].sys.columns c
                        JOIN [{db}].sys.objects o ON o.object_id = c.object_id
                        JOIN [{db}].sys.schemas s ON s.schema_id = o.schema_id
                        JOIN [{db}].sys.types tp ON tp.user_type_id = c.user_type_id
                        WHERE s.name = :schema AND o.name = :table
                        ORDER BY c.column_id
                    """), {"schema": schema_name, "table": table_name}).fetchall()

                    col_defs = ", ".join(f"{col[0]} ({col[1]})" for col in col_rows)
                    schema_lines.append(f"  {schema_name}.{table_name}({col_defs})")

        schema_info = "Tables:\n" + "\n".join(schema_lines) if schema_lines else "No tables found."

        engine.dispose()

        config["schema_info"] = schema_info
        config["status"] = "connected"
        config["error_message"] = None
        config["last_tested"] = datetime.utcnow().isoformat()

        for i, c in enumerate(configs):
            if c["id"] == config_id:
                configs[i] = config
                break
        save_db_configs(configs)

        return {"status": "connected", "schema_info": schema_info}

    except Exception as e:
        config["status"] = "error"
        config["error_message"] = str(e)
        config["last_tested"] = datetime.utcnow().isoformat()

        for i, c in enumerate(configs):
            if c["id"] == config_id:
                configs[i] = config
                break
        save_db_configs(configs)

        raise HTTPException(status_code=400, detail=str(e))
