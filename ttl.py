from __future__ import annotations

from datetime import date
from typing import Any

from common import (
    choose_current_or_latest,
    extract_lang_literals,
    parse_iso_date,
    require_date,
    ttl_escape,
)


def render_time_validity_block(
    start: date | None,
    end: date | None,
    *,
    indent: str,
    terminator: str,
) -> list[str]:
    inner = indent + "\t"
    props = [f"{inner}a dct:PeriodOfTime"]

    if start is not None:
        props.append(f'{inner}dcat:startDate "{start}"^^xsd:date')
    if end is not None:
        props.append(f'{inner}dcat:endDate "{end}"^^xsd:date')

    lines = [f"{indent}egov:timeValidity ["]
    for i, prop in enumerate(props):
        suffix = " ;" if i < len(props) - 1 else ""
        lines.append(prop + suffix)
    lines.append(f"{indent}] {terminator}")
    return lines


def finalize_ttl_block(lines: list[str]) -> str:
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        return ""

    last = lines[-1].rstrip()
    if last.endswith(";"):
        lines[-1] = last[:-1] + "."
    elif not last.endswith("."):
        lines[-1] = last + " ."

    return "\n".join(lines)

def append_if(lines: list[str], condition: bool, line: str) -> None:
    if condition:
        lines.append(line)

def build_codelist_definition(
    *,
    code: str,
    current_version_uri: str,
    names_label: str,
    notes_label: str,
    uri_pattern: str,
    valid_from: date,
    effective_from: date | None,
    effective_to: date | None,
    last_modified: date | None,
    publisher_ico: str | None,
) -> str:
    title_literal = names_label or f'"{ttl_escape(code)}"'

    lines: list[str] = [
        f"codelist:{code} a dcat:Dataset ;",

        # identity
        '\tdct:type <http://publications.europa.eu/resource/authority/dataset-type/CODE_LIST> ;',
        f"\tdct:title {title_literal} ;",
        '\tdcat:theme <http://publications.europa.eu/resource/authority/data-theme/GOVE> ;',
        f'\tegov:uriPattern "{ttl_escape(uri_pattern)}*" ;',

        # governance / provenance
    ]

    append_if(
        lines,
        publisher_ico is not None,
        f"\tdct:publisher <https://data.gov.sk/id/legal-subject/{publisher_ico}> ;",
    )

    lines.append("\tprov:wasDerivedFrom <https://data.gov.sk/id/egov/isvs/63> ;")

    # versioning / access
    lines.extend(
        [
            f"\tdcat:hasCurrentVersion {current_version_uri} ;",
            f'\tdcat:version "{valid_from}"^^xsd:string ;',
            f"\tdcat:distribution <https://data.gov.sk/set/codelist/{code}/{valid_from}.rdf> ;",
        ]
    )

    # dates / validity
    lines.append(f'\tdct:issued "{valid_from}"^^xsd:date ;')

    append_if(
        lines,
        last_modified is not None,
        f'\tdct:modified "{last_modified}"^^xsd:date ;',
    )

    lines.extend(
        render_time_validity_block(
            effective_from,
            effective_to,
            indent="\t",
            terminator=";",
        )
    )

    # descriptive note
    append_if(
        lines,
        bool(notes_label),
        f"\tskos:note {notes_label} ;",
    )

    return finalize_ttl_block(lines)


def build_items_definition(
    *,
    code: str,
    valid_from: date,
    codelist_label: str,
    items: list[dict[str, Any]],
    item_types: list[str],
) -> str:
    blocks: list[str] = []
    belongs_ontology = ", ".join(item_types)

    for item in items:
        item_code = str(item.get("itemCode") or "").strip()
        if not item_code:
            continue

        item_labels = extract_lang_literals(item.get("codelistItemNames"))
        item_notes = extract_lang_literals(item.get("codelistItemNotes"))
        item_valid_from = parse_iso_date(item.get("validFrom"))

        validity = choose_current_or_latest(item.get("codelistItemValidities")) or {}
        effective_from = parse_iso_date(validity.get("effectiveFrom")) or item_valid_from
        effective_to = parse_iso_date(validity.get("effectiveTo"))

        issued_date = require_date(
            item_valid_from or effective_from,
            f"item {item_code} issued/effective date",
        )

        lines: list[str] = [
            f"{codelist_label}:{item_code} a {belongs_ontology} ;",
            f'\tskos:notation "{ttl_escape(item_code)}" ;',
        ]

        if item_labels:
            lines.append(f"\tskos:prefLabel {item_labels} ;")

        lines.extend(
            [
                f"\tskos:topConceptOf codelist:{code} ;",
                f'\tdct:issued "{issued_date}"^^xsd:date ;',
            ]
        )

        if effective_from is not None or effective_to is not None:
            lines.extend(
                render_time_validity_block(
                    effective_from,
                    effective_to,
                    indent="\t",
                    terminator=";",
                )
            )

        if item_notes:
            lines.append(f"\tskos:note {item_notes} ;")

        lines.append(
            f"\tskos:inScheme <https://data.gov.sk/set/codelist/{code}/{valid_from}> ;"
        )
        blocks.append(finalize_ttl_block(lines))

    return "\n\n".join(blocks)
