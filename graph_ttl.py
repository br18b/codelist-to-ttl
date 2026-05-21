from __future__ import annotations

from typing import Any, Mapping

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, RDF, RDFS, SKOS, XSD

from common import parse_iso_date, require_date
from pipeline_config import relation_inverse_predicate, relation_predicate

DCAT = Namespace("http://www.w3.org/ns/dcat#")
PROV = Namespace("http://www.w3.org/ns/prov#")

EGOV = Namespace("https://data.gov.sk/def/ontology/egov/")
PPER = Namespace("https://data.gov.sk/def/ontology/physical-person/")
LEG = Namespace("https://data.gov.sk/def/ontology/legislation/")
FIN = Namespace("https://data.gov.sk/def/ontology/finance/")
LSUB = Namespace("https://data.gov.sk/def/ontology/legal-subject/")
LOCA = Namespace("https://data.gov.sk/def/ontology/location/")

CODELIST = Namespace("https://data.gov.sk/set/codelist/")

DATASET_TYPE_CODE_LIST = URIRef(
    "http://publications.europa.eu/resource/authority/dataset-type/CODE_LIST"
)
THEME_GOVE = URIRef(
    "http://publications.europa.eu/resource/authority/data-theme/GOVE"
)
ISVS_63 = URIRef("https://data.gov.sk/id/egov/isvs/63")

KNOWN_PREFIXES: dict[str, Namespace] = {
    "egov": EGOV,
    "pper": PPER,
    "leg": LEG,
    "fin": FIN,
    "lsub": LSUB,
    "loca": LOCA,
}

PREDICATE_QNAMES: dict[str, URIRef] = {
    "skos:broader": SKOS.broader,
    "skos:narrower": SKOS.narrower,
    "skos:related": SKOS.related,
    "dct:relation": DCTERMS.relation,
    "dct:isPartOf": DCTERMS.isPartOf,
    "dct:hasPart": DCTERMS.hasPart,
    "rdfs:seeAlso": RDFS.seeAlso,
}

PREDICATE_PREFIXES: dict[str, Namespace] = {
    "skos": SKOS,
    "dct": DCTERMS,
    "dcat": DCAT,
    "prov": PROV,
    "rdfs": RDFS,
    "egov": EGOV,
    "pper": PPER,
    "leg": LEG,
    "fin": FIN,
    "lsub": LSUB,
    "loca": LOCA,
}


def _predicate_ref(value: Any) -> URIRef:
    text = str(value or "").strip()
    if not text:
        return DCTERMS.relation
    if text in PREDICATE_QNAMES:
        return PREDICATE_QNAMES[text]
    if text.startswith("http://") or text.startswith("https://"):
        return URIRef(text)
    if ":" in text:
        prefix, local = text.split(":", 1)
        namespace = PREDICATE_PREFIXES.get(prefix)
        if namespace is not None and local:
            return URIRef(namespace[local])
    raise ValueError(
        f"Unsupported predicate name {text!r}. Use a known QName prefix "
        "(skos, dct, dcat, prov, rdfs, egov, pper, leg, fin, lsub, loca) or a full URI."
    )


def _safe_prefix_name(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in text.strip())
    cleaned = cleaned.strip("_").lower() or "items"
    if cleaned[0].isdigit():
        cleaned = f"ns_{cleaned}"
    return cleaned


def _prefix_aliases_for_codes(
    codes: list[str],
    *,
    codelists: dict[str, dict[str, Any]],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    used: set[str] = set()

    for code in codes:
        codelist = codelists[code]
        preferred = _safe_prefix_name(str(codelist.get("item_prefix") or code))
        alias = preferred
        if alias in used:
            alias = f"{preferred}_{_safe_prefix_name(code)}"
        i = 2
        while alias in used:
            alias = f"{preferred}_{i}"
            i += 1
        aliases[code] = alias
        used.add(alias)

    return aliases


def bind_namespaces(
    g: Graph,
    *,
    codelists: dict[str, dict[str, Any]],
    current_code: str,
    referenced_codes: set[str],
) -> None:
    g.bind("skos", SKOS)
    g.bind("dct", DCTERMS)
    g.bind("dcat", DCAT)
    g.bind("prov", PROV)
    g.bind("xsd", XSD)
    g.bind("rdfs", RDFS)

    g.bind("codelist", CODELIST)

    all_codes = [current_code] + sorted(x for x in referenced_codes if x != current_code)
    aliases = _prefix_aliases_for_codes(all_codes, codelists=codelists)
    for code in all_codes:
        item_ns_uri = str(codelists[code]["uri_pattern"])
        g.bind(aliases[code], Namespace(item_ns_uri))

    for prefix, ns in KNOWN_PREFIXES.items():
        g.bind(prefix, ns)


def _add_lang_literal(g: Graph, subject: URIRef, predicate: URIRef, text: str | None, lang: str) -> None:
    if text:
        g.add((subject, predicate, Literal(text, lang=lang)))


def _add_lang_pair(g: Graph, subject: URIRef, predicate: URIRef, *, sk: str | None, en: str | None) -> None:
    _add_lang_literal(g, subject, predicate, sk, "sk")
    _add_lang_literal(g, subject, predicate, en, "en")


def add_time_validity(
    g: Graph,
    subject: URIRef,
    *,
    start: str | None,
    end: str | None,
) -> None:
    start_date = parse_iso_date(start)
    end_date = parse_iso_date(end)
    if start_date is None and end_date is None:
        return

    period = BNode()
    g.add((subject, EGOV.timeValidity, period))
    g.add((period, RDF.type, DCTERMS.PeriodOfTime))

    if start_date is not None:
        g.add((period, DCAT.startDate, Literal(start_date.isoformat(), datatype=XSD.date)))

    if end_date is not None:
        g.add((period, DCAT.endDate, Literal(end_date.isoformat(), datatype=XSD.date)))


def _add_bool_literal(g: Graph, subject: URIRef, predicate: URIRef, value: bool | None) -> None:
    if value is not None:
        g.add((subject, predicate, Literal(value, datatype=XSD.boolean)))


def _add_int_literal(g: Graph, subject: URIRef, predicate: URIRef, value: int | None) -> None:
    if value is not None:
        g.add((subject, predicate, Literal(value, datatype=XSD.integer)))


def _item_ref(item: dict[str, Any]) -> URIRef:
    return URIRef(str(item["item_uri"]))


RELATION_KEYS = ("includes", "includesAlso", "excludes")


def _relation_indexes(
    hierarchies: dict[int, dict[str, Any]],
) -> tuple[
    dict[str, dict[int, list[int]]],
    dict[str, dict[int, list[int]]],
    dict[str, dict[int, list[dict[str, Any]]]],
]:
    outgoing_by_relation: dict[str, dict[int, list[int]]] = {key: {} for key in RELATION_KEYS}
    incoming_by_relation: dict[str, dict[int, list[int]]] = {key: {} for key in RELATION_KEYS}
    external_by_relation: dict[str, dict[int, list[dict[str, Any]]]] = {key: {} for key in RELATION_KEYS}

    for source_id, bucket in hierarchies.items():
        source_id = int(source_id)
        for relation_key in RELATION_KEYS:
            targets = [int(x) for x in bucket.get(relation_key, [])]
            if targets:
                outgoing_by_relation[relation_key][source_id] = targets
            for target_id in targets:
                incoming_by_relation[relation_key].setdefault(target_id, []).append(source_id)

            external_key = f"{relation_key}_external"
            external_targets = [
                x for x in bucket.get(external_key, [])
                if isinstance(x, Mapping)
            ]
            if external_targets:
                external_by_relation[relation_key][source_id] = external_targets

    return outgoing_by_relation, incoming_by_relation, external_by_relation


def _configured_relation_keys(
    relation_predicates: Mapping[str, Mapping[str, str]] | None,
) -> tuple[str, ...]:
    keys = set(RELATION_KEYS)
    if relation_predicates:
        keys.update(str(key) for key in relation_predicates)
    return tuple(key for key in RELATION_KEYS if key in keys)

def create_codelist_graph_from_payload(
    *,
    code: str,
    codelists: dict[str, dict[str, Any]],
    items: dict[int, dict[str, Any]],
    hierarchies: dict[int, dict[str, Any]],
    relation_predicates: Mapping[str, Mapping[str, str]] | None = None,
) -> Graph:
    codelist = codelists[code]
    codelist_item_ids = [int(x) for x in codelist.get("items", [])]
    codelist_items = [items[item_id] for item_id in codelist_item_ids if item_id in items]

    outgoing_by_relation, incoming_by_relation, external_by_relation = _relation_indexes(hierarchies)

    referenced_codes: set[str] = set()
    for item_id in codelist_item_ids:
        for relation_key in RELATION_KEYS:
            if not (relation_predicate(relation_key, relation_predicates) or relation_inverse_predicate(relation_key, relation_predicates)):
                continue
            for target_id in outgoing_by_relation.get(relation_key, {}).get(item_id, []):
                target = items.get(target_id)
                if target is not None:
                    referenced_codes.add(str(target["codelist_code"]))
            for source_id in incoming_by_relation.get(relation_key, {}).get(item_id, []):
                source = items.get(source_id)
                if source is not None:
                    referenced_codes.add(str(source["codelist_code"]))

    g = Graph()
    bind_namespaces(g, codelists=codelists, current_code=code, referenced_codes=referenced_codes)

    root_uri = str(codelist["uri"]).strip()
    codelist_root = URIRef(root_uri)

    valid_from = require_date(parse_iso_date(codelist.get("valid_from")), f"codelist {code} valid_from")
    version_uri = URIRef(f"{root_uri}/{valid_from.isoformat()}")

    g.add((codelist_root, RDF.type, DCAT.Dataset))
    g.add((codelist_root, RDF.type, SKOS.ConceptScheme))
    g.add((codelist_root, DCTERMS.type, DATASET_TYPE_CODE_LIST))
    g.add((codelist_root, DCAT.theme, THEME_GOVE))
    g.add((codelist_root, DCAT.hasVersion, version_uri))
    g.add((codelist_root, DCAT.hasCurrentVersion, version_uri))
    g.add((codelist_root, EGOV.uriPattern, Literal(f"{codelist['uri_pattern']}*")))

    _add_lang_pair(
        g,
        codelist_root,
        DCTERMS.title,
        sk=codelist.get("name_sk"),
        en=codelist.get("name_en"),
    )
    _add_lang_pair(
        g,
        codelist_root,
        RDFS.label,
        sk=codelist.get("label_sk"),
        en=codelist.get("label_en"),
    )
    _add_lang_pair(
        g,
        codelist_root,
        SKOS.note,
        sk=codelist.get("note_sk"),
        en=codelist.get("note_en"),
    )

    for source_value in codelist.get("source_values", []):
        g.add((codelist_root, DCTERMS.source, Literal(str(source_value))))

    g.add((version_uri, RDF.type, DCAT.Dataset))
    g.add((version_uri, RDF.type, SKOS.ConceptScheme))
    g.add((version_uri, DCAT.isVersionOf, codelist_root))
    g.add((version_uri, PROV.wasDerivedFrom, ISVS_63))
    g.add((version_uri, DCAT.version, Literal(valid_from.isoformat(), datatype=XSD.string)))
    g.add((version_uri, DCAT.distribution, URIRef(f"{root_uri}/{valid_from.isoformat()}.rdf")))
    g.add((version_uri, DCTERMS.issued, Literal(valid_from.isoformat(), datatype=XSD.date)))

    last_modified = parse_iso_date(codelist.get("last_modified"))
    if last_modified is not None:
        g.add((version_uri, DCTERMS.modified, Literal(last_modified.isoformat(), datatype=XSD.date)))

    publisher_ico = str(codelist.get("publisher_ico") or "").strip()
    if publisher_ico:
        g.add(
            (
                version_uri,
                DCTERMS.publisher,
                URIRef(f"https://data.gov.sk/id/legal-subject/{publisher_ico}"),
            )
        )

    _add_lang_pair(
        g,
        version_uri,
        DCTERMS.title,
        sk=codelist.get("name_sk"),
        en=codelist.get("name_en"),
    )
    _add_lang_pair(
        g,
        version_uri,
        RDFS.label,
        sk=codelist.get("label_sk"),
        en=codelist.get("label_en"),
    )
    _add_lang_pair(
        g,
        version_uri,
        SKOS.note,
        sk=codelist.get("note_sk"),
        en=codelist.get("note_en"),
    )

    add_time_validity(
        g,
        version_uri,
        start=codelist.get("effective_from"),
        end=codelist.get("effective_to"),
    )

    for item in codelist_items:
        item_ref = _item_ref(item)

        ordered_item_types = [URIRef(uri) for uri in item.get("item_type_uris", [])] + [SKOS.Concept]
        for item_type in ordered_item_types:
            g.add((item_ref, RDF.type, item_type))

        g.add((item_ref, SKOS.notation, Literal(str(item["item_code"]))))
        g.add((item_ref, SKOS.topConceptOf, codelist_root))
        g.add((item_ref, SKOS.inScheme, version_uri))

        _add_lang_pair(
            g,
            item_ref,
            SKOS.prefLabel,
            sk=item.get("name_sk"),
            en=item.get("name_en"),
        )
        _add_lang_pair(
            g,
            item_ref,
            RDFS.label,
            sk=item.get("label_sk"),
            en=item.get("label_en"),
        )
        _add_lang_pair(
            g,
            item_ref,
            SKOS.note,
            sk=item.get("note_sk"),
            en=item.get("note_en"),
        )

        issued_date = require_date(
            parse_iso_date(item.get("valid_from")) or parse_iso_date(item.get("effective_from")),
            f"item {item['item_code']} issued/effective date",
        )
        g.add((item_ref, DCTERMS.issued, Literal(issued_date.isoformat(), datatype=XSD.date)))

        add_time_validity(
            g,
            item_ref,
            start=item.get("effective_from"),
            end=item.get("effective_to"),
        )

        _add_int_literal(g, item_ref, EGOV.logicalOrder, item.get("logical_order"))
        _add_bool_literal(g, item_ref, LEG.legallyRecognized, item.get("legislative_validity"))

        source_item_id = int(item["id"])
        for relation_key in RELATION_KEYS:
            forward_predicate_name = relation_predicate(relation_key, relation_predicates)
            inverse_predicate_name = relation_inverse_predicate(relation_key, relation_predicates)

            if forward_predicate_name:
                forward_predicate = _predicate_ref(forward_predicate_name)
                for target_id in outgoing_by_relation.get(relation_key, {}).get(source_item_id, []):
                    target = items.get(target_id)
                    if target is None:
                        continue
                    g.add((item_ref, forward_predicate, _item_ref(target)))

            for external_target in external_by_relation.get(relation_key, {}).get(source_item_id, []):
                target_uri = str(external_target.get("uri") or "").strip()
                if not target_uri:
                    continue

                predicate_name = str(external_target.get("predicate") or forward_predicate_name or "").strip()
                if not predicate_name:
                    continue
                g.add((item_ref, _predicate_ref(predicate_name), URIRef(target_uri)))

            if inverse_predicate_name:
                inverse_predicate = _predicate_ref(inverse_predicate_name)
                for inverse_source_id in incoming_by_relation.get(relation_key, {}).get(source_item_id, []):
                    source = items.get(inverse_source_id)
                    if source is None:
                        continue
                    g.add((item_ref, inverse_predicate, _item_ref(source)))

    return g
