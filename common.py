from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Mapping


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]



def is_fresh_file(path: Path, *, max_age_seconds: int) -> bool:
    if not path.exists():
        return False
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return False
    age = datetime.now(timezone.utc) - mtime
    return age.total_seconds() <= max_age_seconds


def is_http_uri_like(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    parts = urlsplit(text)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def normalize_http_uri(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    path = "/".join(part for part in parts.path.split("/") if part)
    path = "/" + path if path else ""
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def uri_local_name(value: Any) -> str:
    text = str(value or "").strip().rstrip("/#")
    if not text:
        return ""
    return re_split_uri_tail(text)


def re_split_uri_tail(value: str) -> str:
    import re
    return re.split(r"[/#]", value.rstrip("/#"))[-1]


def item_identity_from_row(row: Mapping[str, Any]) -> tuple[str, str | None, bool]:
    raw_code = str(row.get("itemCode") or row.get("ItemCode") or "").strip()
    raw_uri_value = row.get("itemUri") or row.get("ItemUri") or row.get("uri")
    raw_uri = str(raw_uri_value or "").strip()

    if raw_uri:
        item_uri = normalize_http_uri(raw_uri) or raw_uri
        if is_http_uri_like(raw_code):
            return uri_local_name(raw_code), item_uri, False
        return raw_code or uri_local_name(item_uri), item_uri, False

    if is_http_uri_like(raw_code):
        item_uri = normalize_http_uri(raw_code) or raw_code
        return uri_local_name(item_uri), item_uri, True

    return raw_code, None, False


def normalize_item_identity_row(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    item_code, item_uri, recovered = item_identity_from_row(row)
    if recovered:
        normalized.setdefault("itemCodeOriginal", row.get("itemCode"))
        normalized.setdefault("itemUriOriginal", row.get("itemUri"))
        normalized["itemCode"] = item_code
        normalized["itemUri"] = item_uri
        normalized["itemUriRecoveredFromItemCode"] = True
    return normalized

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


def lang_values(entries: Any, *, text_key: str = "value") -> dict[str, str]:
    out: dict[str, str] = {}
    for row in choose_current_per_language(entries):
        lang = str(row.get("language") or "").strip().lower() or "und"
        text = str(row.get(text_key) or "").strip()
        if text:
            out[lang] = text
    return out


def lang_value(entries: Any, lang: str, *, text_key: str = "value") -> str | None:
    return lang_values(entries, text_key=text_key).get(lang.lower())


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


def unique_nonempty(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def parse_bool_strict(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)

    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None
