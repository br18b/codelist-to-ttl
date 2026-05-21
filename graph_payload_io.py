from __future__ import annotations

from typing import Any

from common import load_json, write_json
from paths import ProjectPaths


def load_code_to_ontology(path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def write_graph_payloads(
    *,
    project_paths: ProjectPaths,
    codelists: dict[str, dict[str, Any]],
    items: dict[int, dict[str, Any]],
    hierarchies: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    project_paths.ensure_base_dirs()

    write_json(project_paths.normalized_codelists_file, codelists)
    write_json(project_paths.normalized_items_file, items)
    write_json(project_paths.normalized_hierarchies_file, hierarchies)

    return {
        "codelists": project_paths.normalized_codelists_file,
        "items": project_paths.normalized_items_file,
        "hierarchies": project_paths.normalized_hierarchies_file,
    }
