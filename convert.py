from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import requests
import xmltodict

from common import (
    as_list,
    is_fresh_file,
    load_json,
    normalize_item_identity_row,
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
from graph_ttl import create_codelist_graph_from_payload
from graph_payload_io import load_code_to_ontology
from models import ConversionResult
from normalize_graph import normalize_codelist_payload
from owner import fetch_publisher_ico_from_header
from paths import ProjectPaths
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
    cache_path=None,
    max_cache_age_seconds: int = 86400,
) -> dict[str, Any]:
    if cache_path is not None and is_fresh_file(cache_path, max_age_seconds=max_cache_age_seconds):
        if result is not None and request_key is not None:
            result.request_status[request_key] = {
                "ok": True,
                "status": None,
                "reason": "fresh local cache",
                "url": url,
                "cached": True,
                "cacheFile": str(cache_path),
            }
        return load_json(cache_path)

    response = session.get(url, timeout=REQUEST_TIMEOUT)

    if result is not None and request_key is not None:
        result.request_status[request_key] = {
            "ok": response.ok,
            "status": response.status_code,
            "reason": response.reason,
            "url": url,
            "cached": False,
        }

    if result is not None and response.status_code >= 500:
        result.add_http_5xx(
            stage=request_key or "json",
            url=url,
            status=response.status_code,
            reason=response.reason,
        )

    response.raise_for_status()
    data = response.json()
    if cache_path is not None:
        write_json(cache_path, data)
    return data


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
        parsed = xmltodict.parse(xml_text)
    except Exception as exc:
        result.warn(f"integration xml parse failed: {type(exc).__name__}: {exc}")
        result.integration_status = {
            "ok": True,
            "status": response.status_code,
            "reason": response.reason,
            "endpoint": "integration",
            "xmlParsed": False,
        }
        return

    write_json(paths.integration_json_file, parsed)
    result.integration_status = {
        "ok": True,
        "status": response.status_code,
        "reason": response.reason,
        "endpoint": "integration",
        "xmlParsed": True,
    }


def record_rank(item: Mapping[str, Any]) -> tuple[Any, ...]:
    valid_from = parse_iso_datetime(str(item.get("validFrom") or ""))
    valid_from_rank = -valid_from.timestamp() if valid_from is not None else 0.0

    effective_to = parse_iso_datetime(item.get("effectiveTo"))
    effective_to_rank = float("-inf") if effective_to is None else -effective_to.timestamp()

    return (
        state_rank(str(item.get("codelistItemState") or "")),
        effective_to_rank,
        valid_from_rank,
        0 if not bool(item.get("temporal")) else 1,
        -int(item.get("id") or 0),
    )


def is_current_published_item(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("codelistItemState") or "").strip() == "PUBLISHED"
        and not bool(row.get("temporal"))
        and row.get("published") is not False
    )


def item_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "state": row.get("codelistItemState"),
        "temporal": bool(row.get("temporal")),
        "published": row.get("published"),
        "validFrom": row.get("validFrom"),
        "itemCode": row.get("itemCode"),
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
        normalize_item_identity_row(x)
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


def fetch_and_normalize_codelist(
    code: str,
    *,
    codelist_id: int,
    session: requests.Session,
    project_paths: ProjectPaths | None = None,
    strict_uri_audit: bool = False,
) -> tuple[ConversionResult, dict[str, Any] | None, dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
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
            cache_path=paths.header_json_file,
        )

        header_code = str(header.get("code") or "").strip()
        if header_code and header_code != code:
            result.warn(
                f"requested code={code} but header id={codelist_id} returned code={header_code}"
            )

        if not bool(header.get("base")):
            result.warn("header is not base=true; skipping normalization and ttl generation")
            return result, None, {}, {}

        items_json = fetch_json(
            session,
            result.request_urls["json_items"],
            result=result,
            request_key="json_items",
            cache_path=paths.items_json_file,
        )

        selected_items, item_duplicate_issues = select_best_items(items_json)
        result.item_duplicate_issues.extend(item_duplicate_issues)

        recovered_count = sum(
            1 for item in selected_items
            if bool(item.get("itemUriRecoveredFromItemCode"))
        )
        if recovered_count:
            result.warn(
                f"recovered {recovered_count} item URI(s) from URI-like itemCode values "
                f"where itemUri was null/empty"
            )

        for issue in item_duplicate_issues:
            if issue["kind"] == "conflict":
                sel = issue["selected"]
                result.warn(
                    f"conflicting duplicate rows for itemCode={issue['itemCode']}; "
                    f"picked id={sel['id']} state={sel['state']} temporal={sel['temporal']}"
                )

        selected_items_json = {"codelistsItems": selected_items}

        valid_from = require_date(
            parse_iso_date(header.get("validFrom")),
            "header validFrom",
        )

        item_valid_dates = [
            dt
            for item in selected_items
            for dt in [parse_iso_date(item.get("validFrom"))]
            if dt is not None
        ]
        last_modified: date | None = max(item_valid_dates) if item_valid_dates else valid_from

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
            strict_uri_audit=strict_uri_audit,
        )

        if strict_uri_audit and result.errors:
            result.block_validation(
                f"strict URI audit failed with {len(result.errors)} blocking issue(s)"
            )

        code_to_ontology = load_code_to_ontology(project_paths.ontology_map_file)
        ontology_entry = code_to_ontology.get(code, {})

        codelist_record, item_records, hierarchy_stubs = normalize_codelist_payload(
            code=code,
            header=header,
            items=selected_items,
            uri_pattern=uri_pattern,
            item_prefix=codelist_label,
            publisher_ico=publisher_ico,
            last_modified=last_modified,
            ontology_entry=ontology_entry,
        )

        if result.validation_blocked:
            return result, codelist_record, item_records, hierarchy_stubs

        return result, codelist_record, item_records, hierarchy_stubs

    except Exception as exc:
        result.error(f"{type(exc).__name__}: {exc}")
        return result, None, {}, {}


def write_ttl_from_payload(
    *,
    code: str,
    codelists: dict[str, dict[str, Any]],
    items: dict[int, dict[str, Any]],
    hierarchies: dict[int, dict[str, Any]],
    result: ConversionResult,
    project_paths: ProjectPaths | None = None,
    relation_predicates: Mapping[str, Mapping[str, str]] | None = None,
) -> ConversionResult:
    project_paths = project_paths or ProjectPaths.default()
    project_paths.ensure_base_dirs()
    paths = project_paths.for_code(code)

    if result.validation_blocked:
        result.info(
            f"ttl emission skipped because validation is blocked: "
            f"{'; '.join(result.validation_block_reasons)}"
        )
        return result

    try:
        graph = create_codelist_graph_from_payload(
            code=code,
            codelists=codelists,
            items=items,
            hierarchies=hierarchies,
            relation_predicates=relation_predicates,
        )
        ttl_text = graph.serialize(format="turtle")
        paths.ttl_file.write_text(str(ttl_text), encoding="utf-8")

        result.ttl_file = paths.ttl_file
        result.created = True
        return result
    except Exception as exc:
        result.error(f"{type(exc).__name__}: {exc}")
        return result
