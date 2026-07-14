"""Thin wrapper around the Supabase PostgREST HTTP API (no DB driver needed)."""
import json

import httpx

from app.config import get_settings


def enabled() -> bool:
    settings = get_settings()
    return bool(settings.supabase_url and settings.supabase_key)


def endpoint(table: str) -> str:
    base = get_settings().supabase_url.rstrip("/")
    return f"{base}/rest/v1/{table}"


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    key = get_settings().supabase_key
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def insert(table: str, rows: list[dict], *, ignore_duplicates: bool = False, return_rows: bool = False):
    prefer = []
    if ignore_duplicates:
        prefer.append("resolution=ignore-duplicates")
    prefer.append("return=representation" if return_rows else "return=minimal")
    with httpx.Client(timeout=20, trust_env=False) as client:
        response = client.post(
            endpoint(table),
            headers=_headers({"Prefer": ",".join(prefer)}),
            content=json.dumps(rows),
        )
        response.raise_for_status()
        return response.json() if return_rows else None


def select(table: str, params: dict[str, str]) -> list[dict]:
    with httpx.Client(timeout=20, trust_env=False) as client:
        response = client.get(endpoint(table), headers=_headers(), params=params)
        response.raise_for_status()
        return response.json()


def update(table: str, params: dict[str, str], values: dict) -> None:
    with httpx.Client(timeout=20, trust_env=False) as client:
        response = client.patch(
            endpoint(table),
            headers=_headers({"Prefer": "return=minimal"}),
            params=params,
            content=json.dumps(values),
        )
        response.raise_for_status()
