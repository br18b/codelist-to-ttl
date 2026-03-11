from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from rdflib import Graph

from common import load_json, write_json
from config import DEFAULT_LABELS, DEFAULT_ONTOLOGIES, DEFAULT_PREFIXES
from paths import ProjectPaths


def ontology_context(ontology: str) -> dict[str, Any]:
    return {
        "@vocab": f"https://data.gov.sk/def/ontology/{ontology}/",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "owl": "http://www.w3.org/2002/07/owl#",
        "dct": "http://purl.org/dc/terms/",
        "skos": "http://www.w3.org/2004/02/skos/core#",
        "kdp": "https://data.gov.sk/def/ontology/kdp/",
        "adms": "http://www.w3.org/ns/adms#",
        "label": "rdfs:label",
        "description": "rdfs:description",
        "note": "skos:note",
        "isDefinedBy": {"@id": "rdfs:isDefinedBy", "@type": "@id"},
        "xmlElementName": "kdp:xmlElementName",
        "identifierKDP": {"@id": "adms:identifier", "@type": "@id"},
        "source": {"@id": "dct:source", "@type": "@id"},
    }


def rdf_to_jsonld(
    ontology: str,
    *,
    rdf_dir: Path,
    jsonld_dir: Path,
) -> Path | None:
    rdf_file = rdf_dir / f"{ontology}.rdf"
    jsonld_out = jsonld_dir / f"{ontology}.jsonld"

    if not rdf_file.exists():
        print(f"warning: missing ontology {rdf_file} ; skipping {ontology}")
        return None

    jsonld_out.parent.mkdir(parents=True, exist_ok=True)

    g = Graph()
    g.parse(rdf_file, format="xml")

    jsonld_data = g.serialize(
        format="json-ld",
        context=ontology_context(ontology),
        indent=2,
        auto_compact=True,
    )
    if isinstance(jsonld_data, bytes):
        jsonld_text = jsonld_data.decode("utf-8")
    else:
        jsonld_text = str(jsonld_data)

    jsonld_out.write_text(jsonld_text, encoding="utf-8")
    return jsonld_out


def rdf_many_to_jsonld(
    ontologies: list[str],
    *,
    rdf_dir: Path,
    jsonld_dir: Path,
) -> list[Path]:
    out: list[Path] = []
    for ontology in ontologies:
        jsonld_file = rdf_to_jsonld(
            ontology,
            rdf_dir=rdf_dir,
            jsonld_dir=jsonld_dir,
        )
        if jsonld_file is not None:
            out.append(jsonld_file)
    return out


def _uri_tail(uri: str) -> str:
    return uri.rstrip("/").split("/")[-1]


def _jsonld_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        maybe = value.get("@id")
        if isinstance(maybe, str):
            return maybe
    return None


def build_codelist_to_ontology(
    *,
    ontologies: list[str] | None = None,
    labels: dict[str, str] | None = None,
    prefixes: dict[str, str] | None = None,
    jsonld_dir: Path,
) -> dict[str, dict[str, str]]:
    ontologies = ontologies or DEFAULT_ONTOLOGIES
    labels = labels or DEFAULT_LABELS
    prefixes = prefixes or DEFAULT_PREFIXES

    code_to_ont: defaultdict[str, dict[str, str]] = defaultdict(dict)

    for ontology_name in ontologies:
        ontology_label = labels[ontology_name]
        ontology_prefix = prefixes[ontology_name]

        jsonld_file = jsonld_dir / f"{ontology_name}.jsonld"
        if not jsonld_file.exists():
            print(f"warning: missing ontology {jsonld_file} ; it will not be considered when generating ttl files")
            continue

        ont_data = load_json(jsonld_file)
        graph = ont_data.get("@graph", [])

        if not isinstance(graph, list):
            continue

        for node in graph:
            if not isinstance(node, dict):
                continue

            codelist_uri = _jsonld_id(node.get("source"))
            data_uri = _jsonld_id(node.get("@id"))

            if not isinstance(codelist_uri, str) or not isinstance(data_uri, str):
                continue

            code = _uri_tail(codelist_uri)
            if "CL" not in code:
                continue

            local_name = _uri_tail(data_uri)
            qualified_name = f"{ontology_label}:{local_name}"

            code_to_ont[code][qualified_name] = ontology_prefix

    return dict(code_to_ont)


def save_codelist_to_ontology(
    mapping: dict[str, dict[str, str]],
    *,
    out_path: Path,
) -> Path:
    write_json(out_path, mapping)
    return out_path


def build_and_save_codelist_to_ontology(
    *,
    ontologies: list[str] | None = None,
    labels: dict[str, str] | None = None,
    prefixes: dict[str, str] | None = None,
    jsonld_dir: Path,
    out_path: Path,
) -> tuple[dict[str, dict[str, str]], Path]:
    mapping = build_codelist_to_ontology(
        ontologies=ontologies,
        labels=labels,
        prefixes=prefixes,
        jsonld_dir=jsonld_dir,
    )
    out_file = save_codelist_to_ontology(mapping, out_path=out_path)
    return mapping, out_file


def build_all(
    *,
    ontologies: list[str] | None = None,
    project_paths: ProjectPaths | None = None,
) -> tuple[list[Path], Path]:
    project_paths = project_paths or ProjectPaths.default()
    project_paths.ensure_base_dirs()

    ontologies = ontologies or DEFAULT_ONTOLOGIES

    jsonld_files = rdf_many_to_jsonld(
        ontologies,
        rdf_dir=project_paths.rdf_dir,
        jsonld_dir=project_paths.jsonld_dir,
    )

    _, mapping_file = build_and_save_codelist_to_ontology(
        ontologies=ontologies,
        jsonld_dir=project_paths.jsonld_dir,
        out_path=project_paths.ontology_map_file,
    )

    return jsonld_files, mapping_file
