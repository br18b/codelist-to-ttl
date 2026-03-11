from __future__ import annotations

import re
from typing import Any, Mapping

import requests

from common import choose_current_or_latest
from config import CMDB_READ_CI_URL, REQUEST_TIMEOUT
from models import ConversionResult

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
ICO_RE = re.compile(r"^\d{6,10}$")


def sanitize_ico(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", "", str(value))
    return text if ICO_RE.fullmatch(text) else None


def extract_owner_uuid(manager_value: str | None) -> str | None:
    if not manager_value:
        return None
    uuids = UUID_RE.findall(manager_value)
    if not uuids:
        return None
    return uuids[-1]


def extract_ci_attribute(raw_ci: Mapping[str, Any], *names: str) -> str | None:
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


def fetch_publisher_ico_from_header(
    session: requests.Session,
    header: Mapping[str, Any],
    result: ConversionResult,
) -> str | None:
    manager = choose_current_or_latest(header.get("mainCodelistManagers"))
    if manager is None:
        manager = choose_current_or_latest(header.get("codelistManagers"))

    if manager is None:
        result.warn("no current manager found in header")
        return None

    raw_value = str(manager.get("value") or "").strip()
    owner_uuid = extract_owner_uuid(raw_value)
    if owner_uuid is None:
        result.warn(f"could not parse owner UUID from manager value {raw_value!r}")
        return None

    url = CMDB_READ_CI_URL.format(uuid=owner_uuid)
    response = session.get(url, timeout=REQUEST_TIMEOUT)

    result.request_status["owner_ci"] = {
        "ok": response.ok,
        "status": response.status_code,
        "reason": response.reason,
        "url": url,
    }

    if response.status_code >= 500:
        result.add_http_5xx(
            stage="owner_ci",
            url=url,
            status=response.status_code,
            reason=response.reason,
        )

    if not response.ok:
        result.warn(
            f"owner CI fetch failed with HTTP {response.status_code} for uuid={owner_uuid}"
        )
        return None

    raw_ci = response.json()

    name = extract_ci_attribute(
        raw_ci,
        "Gen_Profil_nazov",
        "Gen_Profil_anglicky_nazov",
        "name",
    )
    ico = sanitize_ico(extract_ci_attribute(raw_ci, "EA_Profil_PO_ico"))

    if name:
        result.info(f"manager owner resolved to {name!r}")

    if ico is None:
        result.warn(f"could not extract EA_Profil_PO_ico from owner CI uuid={owner_uuid}")
        return None

    result.info(f"publisher ICO resolved as {ico}")
    return ico
