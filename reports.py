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


def write_per_codelist_issue_reports(
    results: list[ConversionResult],
    duplicate_header_issues: list[dict],
    *,
    project_paths: ProjectPaths,
) -> dict[str, list[Path]]:
    warnings_dir = project_paths.issues_root / "warnings"
    errors_dir = project_paths.issues_root / "errors"
    warnings_dir.mkdir(parents=True, exist_ok=True)
    errors_dir.mkdir(parents=True, exist_ok=True)

    written_warning_files: list[Path] = []
    written_error_files: list[Path] = []

    header_issues_by_code: dict[str, list[dict]] = {}
    for issue in duplicate_header_issues:
        header_issues_by_code.setdefault(issue["code"], []).append(issue)

    for result in results:
        code = result.code

        header_shadow = [
            x for x in header_issues_by_code.get(code, [])
            if x.get("kind") == "shadow"
        ]
        header_conflict = [
            x for x in header_issues_by_code.get(code, [])
            if x.get("kind") == "conflict"
        ]

        item_shadow = [
            x for x in result.item_duplicate_issues
            if x.get("kind") == "shadow"
        ]
        item_conflict = [
            x for x in result.item_duplicate_issues
            if x.get("kind") == "conflict"
        ]

        has_warning_payload = bool(
            result.warnings
            or (result.integration_status or {}).get("ok") is False
            or header_shadow
            or item_shadow
        )

        has_error_payload = bool(
            result.errors
            or header_conflict
            or item_conflict
        )

        base_payload = {
            "code": code,
            "created": result.created,
            "ttlFile": str(result.ttl_file) if result.ttl_file is not None else None,
            "integrationStatus": result.integration_status,
            "http5xx": result.http_5xx,
            "validationBlocked": result.validation_blocked,
            "validationBlockReasons": result.validation_block_reasons,
        }

        if has_warning_payload:
            warning_payload = {
                **base_payload,
                "warnings": result.warnings,
                "duplicateHeaderShadows": header_shadow,
                "duplicateItemShadows": item_shadow,
            }
            out_path = warnings_dir / f"{code}.json"
            write_json(out_path, warning_payload)
            written_warning_files.append(out_path)

        if has_error_payload:
            error_payload = {
                **base_payload,
                "errors": result.errors,
                "duplicateHeaderConflicts": header_conflict,
                "duplicateItemConflicts": item_conflict,
            }
            out_path = errors_dir / f"{code}.json"
            write_json(out_path, error_payload)
            written_error_files.append(out_path)

    return {
        "warnings": written_warning_files,
        "errors": written_error_files,
    }


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
    write_per_codelist_issue_reports(
        results,
        duplicate_header_issues,
        project_paths=project_paths,
    )

    return {
        "warnings": project_paths.warnings_report_file,
        "errors": project_paths.errors_report_file,
    }

def write_hierarchy_issue_reports(
    *,
    project_paths: ProjectPaths,
    hierarchies_no_match: dict[int, dict[str, Any]],
    hierarchies_ambiguous: dict[int, dict[str, Any]],
) -> dict[str, Path]:
    project_paths.ensure_base_dirs()

    write_json(project_paths.hierarchy_no_match_issue_file, hierarchies_no_match)
    write_json(project_paths.hierarchy_ambiguous_issue_file, hierarchies_ambiguous)

    return {
        "no_match": project_paths.hierarchy_no_match_issue_file,
        "ambiguous": project_paths.hierarchy_ambiguous_issue_file,
    }
