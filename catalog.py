from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

import requests

from common import parse_iso_datetime, state_rank
from config import CODELIST_HEADERS_URL, REQUEST_TIMEOUT
from models import CodelistRef


def _header_rank_key(header: dict[str, Any]) -> tuple[Any, ...]:
    """
    Ranking header by the following order of precedence (reorder here if you want to pick duplicit rows in some other order)
    1) state (PUBLISHED > READY_TO_PUBLISH > ISVS_PROCESSING > UPDATING > SOME_OTHER_SHIT_I_HAVENT_SEEN_YET)
    2) effectiveTo (further in the future = better, None means infinitely far into the future)
    3) validFrom (more recent is better)
    4) temporal state (False is better than True) ("príznak, či je číselník v časovej verzii" some kind of time versioning, but from the sound of it False means good)
    5) larger id wins as the final tiebreaker (cuz why not)
    """
    valid_from = parse_iso_datetime(str(header.get("validFrom") or ""))
    valid_from_rank = -valid_from.timestamp() if valid_from is not None else 0.0

    effective_to = parse_iso_datetime(header.get("effectiveTo"))
    effective_to_rank = float("-inf") if effective_to is None else -effective_to.timestamp()

    return (
        state_rank(str(header.get("codelistState") or "")),
        effective_to_rank,
        valid_from_rank,
        0 if not bool(header.get("temporal")) else 1,
        -int(header.get("id") or 0),
    )


def _is_current_published_header(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("codelistState") or "").strip() == "PUBLISHED"
        and not bool(row.get("temporal"))
        and row.get("effectiveTo") is None
    )


def _header_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "state": row.get("codelistState"),
        "temporal": bool(row.get("temporal")),
        "validFrom": row.get("validFrom"),
        "effectiveFrom": row.get("effectiveFrom"),
        "effectiveTo": row.get("effectiveTo"),
        "locked": bool(row.get("locked")),
        "lockedBy": row.get("lockedBy"),
    }


def _classify_header_duplicate_issue(
    code: str,
    rows_sorted: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(rows_sorted) <= 1:
        return None

    selected = rows_sorted[0]
    canonical = [row for row in rows_sorted if _is_current_published_header(row)]

    if len(canonical) > 1:
        kind = "conflict"
        reason = "multiple current published non-temporal headers"
    else:
        kind = "shadow"
        reason = "one canonical header plus non-canonical shadow variants"

    return {
        "code": code,
        "kind": kind,
        "reason": reason,
        "selected": _header_snapshot(selected),
        "candidates": [_header_snapshot(row) for row in rows_sorted],
    }


def fetch_codelist_headers_raw(session: requests.Session) -> list[dict[str, Any]]:
    response = session.get(CODELIST_HEADERS_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    codelists = data.get("codelists", [])
    return [x for x in codelists if isinstance(x, dict)]


def fetch_codelist_refs(
    session: requests.Session,
) -> tuple[list[CodelistRef], list[dict[str, Any]]]:
    raw = fetch_codelist_headers_raw(session)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        grouped[code].append(row)

    refs: list[CodelistRef] = []
    duplicate_issues: list[dict[str, Any]] = []

    for code in sorted(grouped):
        rows = grouped[code]
        rows_sorted = sorted(rows, key=_header_rank_key)
        chosen = rows_sorted[0]

        refs.append(
            CodelistRef(
                id=int(chosen["id"]),
                code=code,
                state=str(chosen.get("codelistState") or ""),
                temporal=bool(chosen.get("temporal")),
            )
        )

        issue = _classify_header_duplicate_issue(code, rows_sorted)
        if issue is not None:
            duplicate_issues.append(issue)

    return refs, duplicate_issues
