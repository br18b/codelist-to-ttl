from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from rdflib import Graph
from rdflib.namespace import DCTERMS, OWL, RDF

from common import write_json
from config import DEFAULT_LABELS, DEFAULT_ONTOLOGIES
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
    input_dir: Path,
    jsonld_dir: Path,
) -> Path | None:
    rdf_file = input_dir / f"{ontology}.rdf"
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
    input_dir: Path,
    jsonld_dir: Path,
) -> list[Path]:
    out: list[Path] = []
    for ontology in ontologies:
        jsonld_file = rdf_to_jsonld(
            ontology,
            input_dir=input_dir,
            jsonld_dir=jsonld_dir,
        )
        if jsonld_file is not None:
            out.append(jsonld_file)
    return out


def _uri_tail(uri: str) -> str:
    return uri.rstrip("/").split("/")[-1]


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted(dict.fromkeys(values))


def build_codelist_to_ontology(
    *,
    ontologies: list[str] | None = None,
    labels: dict[str, str] | None = None,
    input_dir: Path,
) -> dict[str, dict[str, Any]]:
    ontologies = ontologies or DEFAULT_ONTOLOGIES
    labels = labels or DEFAULT_LABELS

    code_to_ont: defaultdict[str, dict[str, list[str]]] = defaultdict(
        lambda: {
            "itemTypeUris": [],
            "itemTypeQNames": [],
            "ontologies": [],
        }
    )

    for ontology_name in ontologies:
        ontology_label = labels[ontology_name]
        rdf_file = input_dir / f"{ontology_name}.rdf"

        if not rdf_file.exists():
            print(
                f"warning: missing ontology {rdf_file} ; "
                f"it will not be considered when generating ttl files"
            )
            continue

        g = Graph()
        g.parse(rdf_file, format="xml")

        for class_uri, _, codelist_uri in g.triples((None, DCTERMS.source, None)):
            if (class_uri, RDF.type, OWL.Class) not in g:
                continue

            codelist_uri_str = str(codelist_uri)
            class_uri_str = str(class_uri)

            code = _uri_tail(codelist_uri_str)
            if "CL" not in code:
                continue

            local_name = _uri_tail(class_uri_str)
            qname = f"{ontology_label}:{local_name}"

            bucket = code_to_ont[code]
            bucket["itemTypeUris"].append(class_uri_str)
            bucket["itemTypeQNames"].append(qname)
            bucket["ontologies"].append(ontology_name)

    out: dict[str, dict[str, Any]] = {}
    for code, payload in sorted(code_to_ont.items()):
        out[code] = {
            "itemTypeUris": _sorted_unique(payload["itemTypeUris"]),
            "itemTypeQNames": _sorted_unique(payload["itemTypeQNames"]),
            "ontologies": _sorted_unique(payload["ontologies"]),
        }
    return out


def save_codelist_to_ontology(
    mapping: dict[str, dict[str, Any]],
    *,
    out_path: Path,
) -> Path:
    write_json(out_path, mapping)
    return out_path


def build_and_save_codelist_to_ontology(
    *,
    ontologies: list[str] | None = None,
    labels: dict[str, str] | None = None,
    input_dir: Path,
    out_path: Path,
) -> tuple[dict[str, dict[str, Any]], Path]:
    mapping = build_codelist_to_ontology(
        ontologies=ontologies,
        labels=labels,
        input_dir=input_dir,
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
        input_dir=project_paths.input_dir,
        jsonld_dir=project_paths.jsonld_dir,
    )

    _, mapping_file = build_and_save_codelist_to_ontology(
        ontologies=ontologies,
        input_dir=project_paths.input_dir,
        out_path=project_paths.ontology_map_file,
    )

    return jsonld_files, mapping_file
