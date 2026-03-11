from __future__ import annotations

import requests

from catalog import fetch_codelist_refs
from convert import codelist_to_ttl
from ontology import build_all
from paths import ProjectPaths
from reports import write_issue_reports


def main() -> None:
    project_paths = ProjectPaths.default()
    project_paths.ensure_base_dirs()

    build_all(project_paths=project_paths)

    with requests.Session() as session:
        refs, duplicate_header_issues = fetch_codelist_refs(session)
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

        for i, ref in enumerate(refs, start=1):
            print(f"processing: {ref.code} (id={ref.id}), {i}/{len(refs)}")
            result = codelist_to_ttl(
                ref.code,
                codelist_id=ref.id,
                session=session,
                project_paths=project_paths,
            )
            results.append(result)

            if result.created and result.ttl_file is not None:
                print("wrote:", result.ttl_file)
            else:
                err = result.errors[-1] if result.errors else "unknown failure"
                print(f"FAILED on code={ref.code}: {err}. No file created.")

            print()

    report_paths = write_issue_reports(
        results,
        duplicate_header_issues,
        project_paths=project_paths,
    )
    print("warnings report:", report_paths["warnings"])
    print("errors report:", report_paths["errors"])


if __name__ == "__main__":
    main()
