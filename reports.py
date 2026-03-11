from __future__ import annotations

from pathlib import Path

from common import write_json
from models import ConversionResult
from paths import ProjectPaths


def _warning_entries(results: list[ConversionResult]) -> list[dict]:
    out = []
    for result in results:
        if result.warnings or (result.integration_status or {}).get("ok") is False:
            out.append(result.to_report_dict())
    return out


def _error_entries(results: list[ConversionResult]) -> list[dict]:
    out = []
    for result in results:
        if result.errors:
            out.append(result.to_report_dict())
    return out

def write_http_5xx_report(
    results: list[ConversionResult],
    *,
    project_paths: ProjectPaths,
) -> Path:
    entries = []
    for result in results:
        entries.extend(result.http_5xx)

    payload = {
        "summary": {
            "total5xx": len(entries),
            "uniqueUrls": len({e["url"] for e in entries}),
            "affectedCodelists": len({e["code"] for e in entries}),
        },
        "entries": entries,
    }

    out_path = project_paths.issues_root / "http_5xx.json"
    write_json(out_path, payload)
    return out_path

def write_issue_reports(
    results: list[ConversionResult],
    duplicate_header_issues: list[dict],
    *,
    project_paths: ProjectPaths | None = None,
) -> dict[str, Path]:
    project_paths = project_paths or ProjectPaths.default()
    project_paths.ensure_base_dirs()

    all_item_duplicate_issues: list[dict] = []
    for result in results:
        all_item_duplicate_issues.extend(result.item_duplicate_issues)

    header_shadow = [x for x in duplicate_header_issues if x["kind"] == "shadow"]
    header_conflict = [x for x in duplicate_header_issues if x["kind"] == "conflict"]

    item_shadow = [x for x in all_item_duplicate_issues if x["kind"] == "shadow"]
    item_conflict = [x for x in all_item_duplicate_issues if x["kind"] == "conflict"]

    warnings_payload = {
        "summary": {
            "totalProcessed": len(results),
            "ttlCreated": sum(1 for r in results if r.created),
            "ttlFailed": sum(1 for r in results if not r.created),
            "withWarnings": sum(1 for r in results if r.warnings),
            "integrationFailures": sum(
                1
                for r in results
                if (r.integration_status or {}).get("ok") is False
            ),
            "duplicateHeaderShadows": len(header_shadow),
            "duplicateItemShadows": len(item_shadow),
        },
        "duplicateHeaderShadows": header_shadow,
        "duplicateItemShadows": item_shadow,
        "codelistsWithWarnings": _warning_entries(results),
    }

    errors_payload = {
        "summary": {
            "totalProcessed": len(results),
            "ttlCreated": sum(1 for r in results if r.created),
            "ttlFailed": sum(1 for r in results if not r.created),
            "withErrors": sum(1 for r in results if r.errors),
            "duplicateHeaderConflicts": len(header_conflict),
            "duplicateItemConflicts": len(item_conflict),
        },
        "duplicateHeaderConflicts": header_conflict,
        "duplicateItemConflicts": item_conflict,
        "codelistsWithErrors": _error_entries(results),
    }

    write_json(project_paths.warnings_report_file, warnings_payload)
    write_json(project_paths.errors_report_file, errors_payload)
    write_http_5xx_report(results, project_paths=project_paths)

    return {
        "warnings": project_paths.warnings_report_file,
        "errors": project_paths.errors_report_file,
    }
