"""
core/database.py — SQLAlchemy engine factory + query helpers.
All DB access in the application goes through qdf() and scalar() here.
"""
import math
import uuid as _uuid
from functools import lru_cache
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        execution_options={"isolation_level": "AUTOCOMMIT"},
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def qdf(sql: str, params: Optional[Dict] = None) -> pd.DataFrame:
    """
    Execute a SQL query and return a DataFrame.
    - Casts UUID columns to str for JSON serialisation.
    - Returns an empty DataFrame on any error (never raises).
    """
    try:
        with get_engine().connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params or {})
        # UUID → str so FastAPI can serialise without errors
        for col in df.columns:
            if df[col].dtype == object and not df[col].empty:
                first = df[col].dropna().iloc[0] if not df[col].dropna().empty else None
                if isinstance(first, _uuid.UUID):
                    df[col] = df[col].astype(str)
        return df
    except Exception:
        return pd.DataFrame()


def scalar(sql: str, params: Optional[Dict] = None, default: int = 0) -> int:
    """Execute a COUNT/scalar query and return a single integer."""
    df = qdf(sql, params)
    return int(df.iloc[0, 0]) if not df.empty else default


def safe_val(v: Any) -> Any:
    """Convert NaN to None so FastAPI JSON serialisation doesn't choke."""
    if v is None:
        return None
    try:
        if math.isnan(float(v)):
            return None
    except (TypeError, ValueError):
        pass
    return v
