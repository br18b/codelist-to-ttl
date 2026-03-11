from __future__ import annotations

from typing import Any, Mapping

import requests
import xmltodict

from common import (
    as_list,
    extract_lang_literals,
    load_json,
    parse_iso_date,
    parse_iso_datetime,
    require_date,
    state_rank,
    write_json,
)
from config import (
    CODELIST_HEADER_URL,
    CODELIST_ITEMS_URL,
    CODELIST_XML_URL,
    REQUEST_TIMEOUT,
)
from models import ConversionResult
from owner import fetch_publisher_ico_from_header
from paths import ProjectPaths
from ttl import build_codelist_definition, build_items_definition
from uri_audit import audit_item_uris, infer_uri_pattern


def build_request_urls(code: str, codelist_id: int) -> dict[str, str]:
    return {
        "integration": CODELIST_XML_URL.format(code=code),
        "json_header": CODELIST_HEADER_URL.format(id=codelist_id),
        "json_items": CODELIST_ITEMS_URL.format(code=code),
    }


def fetch_json(
    session: requests.Session,
    url: str,
    *,
    result: ConversionResult | None = None,
    request_key: str | None = None,
) -> dict[str, Any]:
    response = session.get(url, timeout=REQUEST_TIMEOUT)

    if result is not None and request_key is not None:
        result.request_status[request_key] = {
            "ok": response.ok,
            "status": response.status_code,
            "reason": response.reason,
            "url": url,
        }

    if result is not None and response.status_code >= 500:
        result.add_http_5xx(
            stage=request_key or "json",
            url=url,
            status=response.status_code,
            reason=response.reason,
        )

    response.raise_for_status()
    return response.json()


def try_fetch_integration_xml(
    session: requests.Session,
    *,
    paths,
    result: ConversionResult,
) -> None:
    url = result.request_urls["integration"]
    response = session.get(url, timeout=REQUEST_TIMEOUT)

    result.request_status["integration"] = {
        "ok": response.ok,
        "status": response.status_code,
        "reason": response.reason,
        "url": url,
    }

    if response.status_code >= 500:
        result.add_http_5xx(
            stage="integration",
            url=url,
            status=response.status_code,
            reason=response.reason,
        )

    if not response.ok:
        result.integration_status = {
            "ok": False,
            "status": response.status_code,
            "reason": response.reason,
            "endpoint": "integration",
        }
        result.warn(
            f"integration api failed with HTTP {response.status_code} for {url}; "
            f"continuing from header+items JSON"
        )
        return

    xml_text = response.text
    paths.xml_file.write_text(xml_text, encoding="utf-8")

    try:
        xml_data = xmltodict.parse(
            xml_text,
            force_list=("CodelistName", "CodelistItem", "ItemName", "Note"),
        )
        write_json(paths.integration_json_file, xml_data)
        result.integration_status = {
            "ok": True,
            "status": response.status_code,
            "endpoint": "integration",
        }
    except Exception as exc:
        result.integration_status = {
            "ok": True,
            "status": response.status_code,
            "endpoint": "integration",
            "xmlParsed": False,
            "parseError": f"{type(exc).__name__}: {exc}",
        }
        result.warn(
            f"integration XML downloaded but JSON conversion failed: "
            f"{type(exc).__name__}: {exc}"
        )


def record_rank(row: Mapping[str, Any]) -> tuple[Any, ...]:
    valid_from = parse_iso_datetime(row.get("validFrom"))
    ts = valid_from.timestamp() if valid_from is not None else 0.0

    return (
        0 if bool(row.get("published", True)) else 1,
        0 if not bool(row.get("temporal")) else 1,
        state_rank(str(row.get("codelistItemState") or row.get("codelistState") or "")),
        0 if row.get("effectiveTo") is None else 1,
        -ts,
        -int(row.get("id") or 0),
    )


def is_current_published_item(row: Mapping[str, Any]) -> bool:
    return (
        bool(row.get("published", True))
        and str(row.get("codelistItemState") or row.get("codelistState") or "").strip() == "PUBLISHED"
        and not bool(row.get("temporal"))
        and row.get("effectiveTo") is None
    )


def item_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "state": row.get("codelistItemState") or row.get("codelistState"),
        "published": bool(row.get("published", True)),
        "temporal": bool(row.get("temporal")),
        "validFrom": row.get("validFrom"),
        "effectiveFrom": row.get("effectiveFrom"),
        "effectiveTo": row.get("effectiveTo"),
        "locked": bool(row.get("locked")),
        "lockedBy": row.get("lockedBy"),
    }


def classify_item_duplicate_issue(
    item_code: str,
    rows_sorted: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(rows_sorted) <= 1:
        return None

    selected = rows_sorted[0]
    canonical = [row for row in rows_sorted if is_current_published_item(row)]

    if len(canonical) > 1:
        kind = "conflict"
        reason = "multiple current published non-temporal items"
    else:
        kind = "shadow"
        reason = "one canonical item plus non-canonical shadow variants"

    return {
        "itemCode": item_code,
        "kind": kind,
        "reason": reason,
        "selected": item_snapshot(selected),
        "candidates": [item_snapshot(row) for row in rows_sorted],
    }


def select_best_items(
    items_json: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_items = [
        x
        for x in as_list(items_json.get("codelistsItems"))
        if isinstance(x, Mapping)
    ]

    grouped: dict[str, list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []

    for row in raw_items:
        item_code = str(row.get("itemCode") or "").strip()
        if not item_code:
            passthrough.append(dict(row))
            continue
        grouped.setdefault(item_code, []).append(dict(row))

    selected: list[dict[str, Any]] = []
    duplicate_issues: list[dict[str, Any]] = []

    for item_code in sorted(grouped):
        rows = grouped[item_code]
        rows_sorted = sorted(rows, key=record_rank)
        chosen = rows_sorted[0]
        selected.append(chosen)

        issue = classify_item_duplicate_issue(item_code, rows_sorted)
        if issue is not None:
            duplicate_issues.append(issue)

    selected.extend(passthrough)
    return selected, duplicate_issues


def load_code_to_ontology(path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def codelist_to_ttl(
    code: str,
    *,
    codelist_id: int,
    session: requests.Session,
    project_paths: ProjectPaths | None = None,
) -> ConversionResult:
    project_paths = project_paths or ProjectPaths.default()
    project_paths.ensure_base_dirs()

    result = ConversionResult(code=code, codelist_id=codelist_id)
    result.request_urls = build_request_urls(code, codelist_id)
    paths = project_paths.for_code(code)

    try:
        try_fetch_integration_xml(session, paths=paths, result=result)

        header = fetch_json(
            session,
            result.request_urls["json_header"],
            result=result,
            request_key="json_header",
        )
        write_json(paths.header_json_file, header)

        header_code = str(header.get("code") or "").strip()
        if header_code and header_code != code:
            result.warn(
                f"requested code={code} but header id={codelist_id} returned code={header_code}"
            )

        items_json = fetch_json(
            session,
            result.request_urls["json_items"],
            result=result,
            request_key="json_items",
        )
        write_json(paths.items_json_file, items_json)

        selected_items, item_duplicate_issues = select_best_items(items_json)
        result.item_duplicate_issues.extend(item_duplicate_issues)

        for issue in item_duplicate_issues:
            if issue["kind"] == "conflict":
                sel = issue["selected"]
                result.warn(
                    f"conflicting duplicate rows for itemCode={issue['itemCode']}; "
                    f"picked id={sel['id']} state={sel['state']} temporal={sel['temporal']}"
                )

        selected_items_json = {"codelistsItems": selected_items}

        names_label = extract_lang_literals(header.get("codelistNames"))
        notes_label = extract_lang_literals(header.get("codelistNotes"))

        valid_from = require_date(
            parse_iso_date(header.get("validFrom")),
            "header validFrom",
        )
        effective_from = parse_iso_date(header.get("effectiveFrom")) or valid_from
        effective_to = parse_iso_date(header.get("effectiveTo"))

        item_valid_dates = [
            dt
            for item in selected_items
            for dt in [parse_iso_date(item.get("validFrom"))]
            if dt is not None
        ]
        last_modified = max(item_valid_dates) if item_valid_dates else valid_from

        publisher_ico = fetch_publisher_ico_from_header(session, header, result)

        uri_pattern, codelist_label = infer_uri_pattern(
            session,
            selected_items_json,
            code=code,
            result=result,
        )

        audit_item_uris(
            session,
            code=code,
            items=selected_items,
            uri_pattern=uri_pattern,
            result=result,
        )

        code_to_ontology = load_code_to_ontology(project_paths.ontology_map_file)
        ontology_entries = code_to_ontology.get(code, {})

        prefixes = [
            "@prefix skos: <http://www.w3.org/2004/02/skos/core#>.",
            "@prefix dct: <http://purl.org/dc/terms/>.",
            "@prefix dcat: <http://www.w3.org/ns/dcat#>.",
            "@prefix prov: <http://www.w3.org/ns/prov#> .",
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
            "@prefix egov: <https://data.gov.sk/def/ontology/egov/>.",
            "@prefix codelist: <https://data.gov.sk/set/codelist/>.",
            f"@prefix {codelist_label}: <{uri_pattern}>.",
            "@prefix leg: <https://data.gov.sk/def/ontology/legislation/>.",
        ]

        item_types: list[str] = ["skos:Concept"]
        for qualified_name, prefix_decl in ontology_entries.items():
            item_types.insert(0, qualified_name)
            prefixes.append(prefix_decl)

        prefixes = list(dict.fromkeys(prefixes))
        current_version_uri = f"<https://data.gov.sk/set/codelist/{code}/{valid_from}>"

        codelist_definition = build_codelist_definition(
            code=code,
            current_version_uri=current_version_uri,
            names_label=names_label,
            notes_label=notes_label,
            uri_pattern=uri_pattern,
            valid_from=valid_from,
            effective_from=effective_from,
            effective_to=effective_to,
            last_modified=last_modified,
            publisher_ico=publisher_ico,
        )

        items_definition = build_items_definition(
            code=code,
            valid_from=valid_from,
            codelist_label=codelist_label,
            items=selected_items,
            item_types=item_types,
        )

        ttl_text = "\n".join(prefixes) + "\n\n" + codelist_definition
        if items_definition:
            ttl_text += "\n\n" + items_definition
        ttl_text += "\n"

        paths.ttl_file.write_text(ttl_text, encoding="utf-8")
        result.ttl_file = paths.ttl_file
        result.created = True
        return result

    except Exception as exc:
        result.error(f"{type(exc).__name__}: {exc}")
        return result
