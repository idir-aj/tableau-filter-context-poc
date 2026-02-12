import os
import time
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Tableau Filter Context API")

# ✅ CORS: en POC, on autorise tout. En prod, restreins aux domaines Tableau Cloud + ton hébergement extensions.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def sf_connect():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
        role=os.environ.get("SNOWFLAKE_ROLE"),
    )

TABLE = os.environ.get("SNOWFLAKE_TABLE", "FILTER_CONTEXT_VALUES")

# ---------- Models ----------
class CreateContextRequest(BaseModel):
    filters: Dict[str, Optional[List[str]]] = Field(
        ...,
        description="Map filter_name -> list of values. Use null/[] for ALL (meaning: do not apply)."
    )
    ttl_hours: int = Field(24, ge=1, le=24 * 30)

class CreateContextResponse(BaseModel):
    context_id: str

class GetContextResponse(BaseModel):
    context_id: str
    filters: Dict[str, List[str]]

# ---------- Helpers ----------
def new_context_id() -> str:
    # 6–10 chars is fine; keep URL short.
    return secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

# ---------- Endpoints ----------
@app.post("/contexts", response_model=CreateContextResponse)
def create_context(req: CreateContextRequest):
    t0 = time.perf_counter()
    context_id = new_context_id()
    expires_at = utcnow() + timedelta(hours=req.ttl_hours)

    rows = []
    for fname, values in req.filters.items():
        if not values:
            # ALL => do not store anything for this filter
            continue
        for v in values:
            rows.append((context_id, expires_at, fname, v))
    t1 = time.perf_counter()

    try:
        con = sf_connect()
        t2 = time.perf_counter()
        cur = con.cursor()
        try:
            if rows:
                cur.executemany(
                    f"""
                    insert into {TABLE} (CONTEXT_ID, EXPIRES_AT, FILTER_NAME, FILTER_VALUE)
                    values (%s, %s, %s, %s)
                    """,
                    rows
                )
                t3 = time.perf_counter()
        finally:
            cur.close()
            con.close()
            t4 = time.perf_counter()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snowflake insert failed: {e}")
    
    print({
        "build_rows": round(t1-t0, 3),
        "connect": round(t2-t1, 3),
        "insert": round(t3-t2, 3),
        "close": round(t4-t3, 3),
        "total": round(t4-t0, 3),
        "rows": len(rows),
    })

    return CreateContextResponse(context_id=context_id)

@app.get("/contexts/{context_id}", response_model=GetContextResponse)
def get_context(context_id: str):
    try:
        con = sf_connect()
        cur = con.cursor()
        try:
            cur.execute(
                f"""
                select FILTER_NAME, FILTER_VALUE
                from {TABLE}
                where CONTEXT_ID = %s
                  and (EXPIRES_AT is null or EXPIRES_AT > current_timestamp())
                """,
                (context_id,)
            )
            results = cur.fetchall()
        finally:
            cur.close()
            con.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snowflake read failed: {e}")

    filters: Dict[str, List[str]] = {}
    for fname, fval in results:
        filters.setdefault(fname, []).append(fval)

    # context_id peut exister avec 0 ligne (tous ALL) => filters={}
    return GetContextResponse(context_id=context_id, filters=filters)
