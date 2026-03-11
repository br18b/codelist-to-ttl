from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ttl_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_iso_date(value: Any) -> date | None:
    dt = parse_iso_datetime(value)
    return dt.date() if dt is not None else None


def require_date(value: date | None, label: str) -> date:
    if value is None:
        raise ValueError(f"Missing or invalid date for {label}.")
    return value


def state_rank(state: str | None) -> int:
    order = {
        "PUBLISHED": 0,
        "READY_TO_PUBLISH": 1,
        "ISVS_PROCESSING": 2,
        "UPDATING": 3,
    }
    return order.get(str(state or "").strip(), 99)


def choose_current_or_latest(entries: Any) -> dict[str, Any] | None:
    rows = [x for x in as_list(entries) if isinstance(x, Mapping)]
    if not rows:
        return None

    current = [x for x in rows if x.get("effectiveTo") in (None, "", "null")]
    if current:
        rows = current

    rows = sorted(
        rows,
        key=lambda x: (
            parse_iso_datetime(x.get("effectiveFrom")).timestamp()
            if parse_iso_datetime(x.get("effectiveFrom")) is not None
            else float("-inf")
        ),
        reverse=True,
    )
    return dict(rows[0])


def choose_current_per_language(entries: Any) -> list[dict[str, Any]]:
    rows = [x for x in as_list(entries) if isinstance(x, Mapping)]
    grouped: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        lang = str(row.get("language") or "")
        grouped.setdefault(lang, []).append(dict(row))

    chosen: list[dict[str, Any]] = []
    for lang in sorted(grouped):
        chosen_row = choose_current_or_latest(grouped[lang])
        if chosen_row is not None:
            chosen.append(chosen_row)
    return chosen


def extract_lang_literals(entries: Any, *, text_key: str = "value") -> str:
    literals: list[str] = []

    for row in choose_current_per_language(entries):
        lang = str(row.get("language") or "").strip()
        text = str(row.get(text_key) or "").strip()
        if not text:
            continue

        literal = f'"{ttl_escape(text)}"'
        if lang:
            literal += f"@{lang}"
        literals.append(literal)

    return " , ".join(literals)


def item_name_values(entries: Any) -> list[str]:
    out: list[str] = []
    for row in choose_current_per_language(entries):
        text = str(row.get("value") or "").strip()
        if text:
            out.append(text)
    return out
