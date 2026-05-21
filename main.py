from __future__ import annotations

import argparse
from pathlib import Path
import requests

from typing import Any

from catalog import fetch_codelist_refs
from convert import fetch_and_normalize_codelist, write_ttl_from_payload
from legal_subject_lookup import lookup_legal_subject_by_code
from graph_payload_io import write_graph_payloads
from normalize_graph import resolve_hierarchies
from pipeline_config import load_pipeline_config
from ontology import build_all
from paths import ProjectPaths
from reports import write_issue_reports, write_hierarchy_issue_reports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pick-top-hierarchy-match",
        action="store_true",
        help=(
            "Accept recommended hierarchy matches only in clear cases "
            "(top score >= 6 and margin >= 1.5). "
            "Ambiguous cases are still written to issues."
        ),
    )
    parser.add_argument(
        "--strict-uri-audit",
        action="store_true",
        help=(
            "Treat item URI mismatches as hard failures and skip normalized/TTL output "
            "for affected codelists."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Config JSON with hierarchy, ignore, and externalRelationPredicates. "
            "Defaults to config/config.json next to main.py when present."
        ),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Output/cache directory. Defaults to ./data next to main.py.",
    )
    parser.add_argument(
        "--cache-max-age-seconds",
        type=int,
        default=86400,
        help="Use cached JSON files when they are newer than this many seconds. Default: 86400.",
    )
    return parser.parse_args()

def filter_hierarchies_for_emitted_items(
    hierarchies_all: dict[int, dict[str, Any]],
    emitted_items: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    emitted_ids = set(emitted_items)

    out: dict[int, dict[str, Any]] = {}
    for source_id, entry in hierarchies_all.items():
        if source_id not in emitted_ids:
            continue

        filtered = {
            "includes": [x for x in entry.get("includes", []) if x in emitted_ids],
            "includesAlso": [x for x in entry.get("includesAlso", []) if x in emitted_ids],
            "excludes": [x for x in entry.get("excludes", []) if x in emitted_ids],
            "includes_external": list(entry.get("includes_external", [])),
            "includesAlso_external": list(entry.get("includesAlso_external", [])),
            "excludes_external": list(entry.get("excludes_external", [])),
        }

        if any(filtered.values()):
            out[source_id] = filtered

    return out

def main() -> None:
    args = parse_args()

    project_paths = ProjectPaths.default(data_root=args.data_root)
    project_paths.ensure_base_dirs()

    config_path = args.config or project_paths.default_config_file
    if config_path is not None and not config_path.is_absolute():
        config_path = project_paths.project_root / config_path
    pipeline_config = load_pipeline_config(config_path)

    build_all(project_paths=project_paths)

    if pipeline_config.path and pipeline_config.path.exists():
        print(f"using config: {pipeline_config.path}")
    else:
        print("using empty config: no config file found")
    print(f"configured hierarchy entries: {len(pipeline_config.hierarchy)}")
    print(f"configured ignored codelists: {len(pipeline_config.ignored_codes)}")

    with requests.Session() as session:
        def resolve_external_ico(raw_value: str) -> dict | None:
            return lookup_legal_subject_by_code(session, raw_value=raw_value)

        refs, duplicate_header_issues = fetch_codelist_refs(
            session,
            project_paths=project_paths,
            max_cache_age_seconds=args.cache_max_age_seconds,
        )
        print(f"selected {len(refs)} canonical codelists")

        header_shadow = [x for x in duplicate_header_issues if x["kind"] == "shadow"]
        header_conflict = [x for x in duplicate_header_issues if x["kind"] == "conflict"]

        if header_conflict:
            print()
            print("conflicting duplicate codes found in global codelistheaders:")
            for issue in header_conflict:
                code = issue["code"]
                ids = ", ".join(str(x["id"]) for x in issue["candidates"])
                print(
                    f"  {code}: picked id={issue['selected']['id']}, "
                    f"candidates=[{ids}] ; {issue['reason']}"
                )
            print()

        if header_shadow:
            print(
                f"{len(header_shadow)} duplicate header groups were classified as shadow variants "
                f"(one canonical published row plus non-canonical variants)."
            )
            print()

        results = []
        codelists: dict[str, dict] = {}
        items: dict[int, dict] = {}
        successful_codes: list[str] = []

        analysis_items: dict[int, dict] = {}
        analysis_hierarchy_stubs: dict[int, dict] = {}

        for i, ref in enumerate(refs, start=1):
            print(f"processing: {ref.code} (id={ref.id}), {i}/{len(refs)}")
            result, codelist_record, item_records, code_hierarchy_stubs = fetch_and_normalize_codelist(
                ref.code,
                codelist_id=ref.id,
                session=session,
                project_paths=project_paths,
                strict_uri_audit=args.strict_uri_audit,
            )
            results.append(result)

            if codelist_record is None:
                if result.validation_blocked and result.validation_block_reasons:
                    err = "; ".join(result.validation_block_reasons)
                else:
                    err = result.errors[-1] if result.errors else "normalization skipped"
                print(f"FAILED normalize on code={ref.code}: {err}.")
                print()
                continue

            # Always contribute to analysis if normalization succeeded. This lets an ignored
            # codelist still serve as a configured parent for another emitted codelist.
            analysis_items.update(item_records)
            analysis_hierarchy_stubs.update(code_hierarchy_stubs)

            if result.validation_blocked:
                err = "; ".join(result.validation_block_reasons) or "validation blocked"
                print(f"analyzed but blocked: {ref.code} ({len(item_records)} items) ; {err}.")
                print()
                continue

            if ref.code in pipeline_config.ignored_codes:
                print(f"analyzed but ignored by config: {ref.code} ({len(item_records)} items)")
                print()
                continue

            codelists[ref.code] = codelist_record
            items.update(item_records)
            successful_codes.append(ref.code)

            print(f"normalized: {ref.code} ({len(item_records)} items)")
            print()

        hierarchies_all, hierarchies_no_match, hierarchies_ambiguous = resolve_hierarchies(
            items=analysis_items,
            hierarchy_stubs=analysis_hierarchy_stubs,
            external_ico_resolver=resolve_external_ico,
            accept_clear_recommendations=args.pick_top_hierarchy_match,
            hierarchy_config=pipeline_config.hierarchy,
            external_relation_predicates=pipeline_config.external_relation_predicates,
        )

        hierarchies = filter_hierarchies_for_emitted_items(hierarchies_all, items)

        payload_paths = write_graph_payloads(
            project_paths=project_paths,
            codelists=codelists,
            items=items,
            hierarchies=hierarchies,
        )

        hierarchy_issue_paths = write_hierarchy_issue_reports(
            project_paths=project_paths,
            hierarchies_no_match=hierarchies_no_match,
            hierarchies_ambiguous=hierarchies_ambiguous,
        )

        print("normalized codelists:", payload_paths["codelists"])
        print("normalized items:", payload_paths["items"])
        print("normalized hierarchies:", payload_paths["hierarchies"])
        print("hierarchy no-match issues:", hierarchy_issue_paths["no_match"])
        print("hierarchy ambiguous issues:", hierarchy_issue_paths["ambiguous"])
        print()

        results_by_code = {result.code: result for result in results}
        for code in successful_codes:
            result = results_by_code[code]
            write_ttl_from_payload(
                code=code,
                codelists=codelists,
                items=items,
                hierarchies=hierarchies,
                result=result,
                project_paths=project_paths,
                relation_predicates=pipeline_config.relation_predicates,
            )

            if result.created and result.ttl_file is not None:
                print("wrote:", result.ttl_file)
            else:
                err = result.errors[-1] if result.errors else "unknown failure"
                print(f"FAILED ttl on code={code}: {err}. No file created.")

        report_paths = write_issue_reports(
            results,
            duplicate_header_issues,
            project_paths=project_paths,
        )
        print("warnings report:", report_paths["warnings"])
        print("errors report:", report_paths["errors"])


if __name__ == "__main__":
    main()
