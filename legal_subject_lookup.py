from __future__ import annotations

import re
from typing import Any, Mapping

import requests

from config import CMDB_READ_CILISTFILTERED_URL, REQUEST_TIMEOUT


_ICO_RE = re.compile(r"^\d{1,8}$")


def _normalize_ico_candidate(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None

    digits = re.sub(r"\D+", "", text)
    if not digits or not _ICO_RE.fullmatch(digits):
        return None

    return digits.zfill(8)


def _extract_ci_attribute(raw_ci: Mapping[str, Any], *names: str) -> str | None:
    wanted = {x.casefold() for x in names}
    attrs = raw_ci.get("attributes") or []

    if isinstance(attrs, list):
        for attr in attrs:
            if not isinstance(attr, Mapping):
                continue
            name = str(attr.get("name") or "").casefold()
            if name in wanted:
                value = attr.get("value")
                if value is not None:
                    return str(value).strip()

    for key in names:
        value = raw_ci.get(key)
        if value is not None:
            return str(value).strip()

    return None


def _extract_ci_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]

    if isinstance(payload, Mapping):
        value = payload.get("configurationItemSet")
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    return []


def _search_po_by_attr(
    session: requests.Session,
    *,
    attr_name: str,
    attr_value: str,
) -> list[dict[str, Any]]:
    payload = {
        "filter": {
            "type": ["PO"],
            "attributes": [
                {
                    "name": attr_name,
                    "filterValue": [
                        {
                            "equality": "EQUAL",
                            "value": attr_value,
                        }
                    ],
                }
            ],
            "metaAttributes": {
                "state": ["DRAFT"],
            },
        },
        "page": 1,
        "perpage": 100,
    }

    response = session.post(
        CMDB_READ_CILISTFILTERED_URL,
        params={"lang": "sk"},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return []

    try:
        body = response.json()
    except ValueError:
        return []

    return _extract_ci_rows(body)


def lookup_legal_subject_by_code(
    session: requests.Session,
    *,
    raw_value: str,
    attr_name: str = "Gen_Profil_kod_metais"
) -> dict[str, Any] | None:
    code = _normalize_ico_candidate(raw_value)
    if code is None:
        return None

    rows = _search_po_by_attr(session, attr_name=attr_name, attr_value=code)
    if len(rows) != 1:
        return None

    row = rows[0]
    name = _extract_ci_attribute(
        row,
        "Gen_Profil_nazov",
        "Gen_Profil_anglicky_nazov",
    )
    ico = _extract_ci_attribute(
        row,
        "EA_Profil_PO_ico"
    )

    return {
        "kind": "legal_subject",
        "ico": ico,
        "uri": f"https://data.gov.sk/id/legal-subject/{ico}",
        "name": name,
        "citype": str(row.get("type") or "PO").strip() or "PO",
        "uuid": str(row.get("uuid") or "").strip() or None,
        "matched_via": "ico_lookup",
        "matched_attribute": attr_name,
    }
