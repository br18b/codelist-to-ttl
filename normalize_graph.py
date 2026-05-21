from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any, Callable, Mapping

from common import (
    as_list,
    choose_current_or_latest,
    lang_values,
    parse_bool_strict,
    parse_iso_date,
    unique_nonempty,
)
from pipeline_config import predicate_for_external_base
from uri_audit import normalize_uri

_URI_RE = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
_CODELIKE_RE = re.compile(
    r"\b(?:[A-Z]{1,}[A-Z0-9]*\d[A-Z0-9.]*|\d{2}(?:\.\d{1,2})+|\d{4,})\b"
)
_SPLIT_RE = re.compile(r"[,;\n\r\t]+")
_WS_RE = re.compile(r"\s+")

_ITEM_TEXT_FIELDS = (
    "item_code",
    "name_sk",
    "name_en",
    "label_sk",
    "label_en",
    "note_sk",
    "note_en",
    "abbreviated_name_sk",
    "abbreviated_name_en",
    "additional_content_sk",
    "additional_content_en",
)

_TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ž.]+", re.UNICODE)

_STOP_TOKENS = {
    "a", "aj", "alebo", "and", "at", "do", "for", "from", "in", "na", "o", "of",
    "po", "pod", "pre", "pri", "s", "sa", "so", "the", "to", "u", "v", "vo", "z", "za",
}

_CODELIST_STOP_TOKENS = _STOP_TOKENS | {
    "ciselnik",
    "číselník",
    "hodnota",
    "hodnoty",
    "kod",
    "kód",
    "kody",
    "kódy",
    "zoznam",
    "typ",
    "trieda",
}

_DETAIL_TOKENS = {
    "detail",
    "detaily",
    "detailu",
    "detailov",
    "podrobnost",
    "podrobnosti",
    "podrobny",
    "podrobný",
    "podrobne",
    "podrobné",
}

def _looks_like_ico_candidate(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.isdigit() and len(text) <= 8

def _item_text_values(item: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for field in _ITEM_TEXT_FIELDS:
        text = str(item.get(field) or "").strip()
        if text:
            values.append(text)
    return values


def _item_text_norms(item: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for value in _item_text_values(item):
        normalized = _normalize_text(value)
        if normalized:
            out.add(normalized)
    return out


def _tokenize(value: Any, *, stop_tokens: set[str] | None = None) -> set[str]:
    normalized = _normalize_text(value)
    if not normalized:
        return set()
    active_stop_tokens = stop_tokens if stop_tokens is not None else _STOP_TOKENS
    tokens = {tok.casefold() for tok in _TOKEN_RE.findall(normalized)}
    return {tok for tok in tokens if len(tok) >= 2 and tok not in active_stop_tokens}


def _item_token_set(item: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for value in _item_text_values(item):
        out |= _tokenize(value)
    return out

def _codelist_text_values(item: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for field in (
        "codelist_name_sk",
        "codelist_name_en",
        "codelist_label_sk",
        "codelist_label_en",
    ):
        text = str(item.get(field) or "").strip()
        if text:
            values.append(text)
    return values


def _codelist_token_set(item: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for value in _codelist_text_values(item):
        out |= _tokenize(value, stop_tokens=_CODELIST_STOP_TOKENS)
    return out


def _strip_detail_tokens(tokens: set[str]) -> set[str]:
    return {tok for tok in tokens if tok not in _DETAIL_TOKENS}


def _uri_family(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    normalized = _normalize_uri_or_none(raw)
    if not normalized:
        return None

    trimmed = normalized.rstrip("/")
    parts = trimmed.split("/")
    if len(parts) < 2:
        return None

    if raw.endswith("/"):
        return parts[-1] or None
    return parts[-2] or None


def _family_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        tok
        for tok in re.split(r"[-_/]+", value.casefold())
        if tok and tok not in _STOP_TOKENS
    }


def _family_parent(value: str | None) -> str | None:
    if not value:
        return None
    if value.endswith("-detail"):
        return value[: -len("-detail")]
    return None

def _candidate_summary(item_id: int, item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "codelist_code": item.get("codelist_code"),
        "codelist_name_sk": item.get("codelist_name_sk"),
        "codelist_name_en": item.get("codelist_name_en"),
        "item_code": item.get("item_code"),
        "name_sk": item.get("name_sk"),
        "name_en": item.get("name_en"),
        "item_uri": item.get("item_uri"),
    }


def _score_candidate(
    raw_value: str,
    *,
    source_item: Mapping[str, Any],
    candidate_item: Mapping[str, Any],
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    raw_norm = _normalize_text(raw_value)
    raw_uri = _normalize_uri_or_none(raw_value)
    raw_code = raw_value.strip()
    raw_tail = _uri_tail(raw_value)

    candidate_text_norms = _item_text_norms(candidate_item)
    candidate_tokens = _item_token_set(candidate_item)
    source_tokens = _item_token_set(source_item)

    source_codelist_tokens = _codelist_token_set(source_item)
    candidate_codelist_tokens = _codelist_token_set(candidate_item)
    source_codelist_core = _strip_detail_tokens(source_codelist_tokens)
    candidate_codelist_core = _strip_detail_tokens(candidate_codelist_tokens)

    source_family = _uri_family(
        source_item.get("codelist_uri_pattern") or source_item.get("item_uri")
    )
    candidate_family = _uri_family(
        candidate_item.get("codelist_uri_pattern") or candidate_item.get("item_uri")
    )
    source_family_tokens = _family_tokens(source_family)
    candidate_family_tokens = _family_tokens(candidate_family)
    source_family_core = _strip_detail_tokens(source_family_tokens)
    candidate_family_core = _strip_detail_tokens(candidate_family_tokens)

    candidate_codes = {
        str(candidate_item.get("item_code") or "").strip(),
        _uri_tail(str(candidate_item.get("item_uri") or "")) or "",
        _uri_tail(str(candidate_item.get("item_uri_raw_normalized") or "")) or "",
    }
    candidate_codes.discard("")

    candidate_uris = {
        _normalize_uri_or_none(candidate_item.get("item_uri")),
        _normalize_uri_or_none(candidate_item.get("item_uri_raw_normalized")),
    }
    candidate_uris.discard(None)

    if raw_uri and raw_uri in candidate_uris:
        score += 10.0
        reasons.append("uri_exact")

    if raw_code and raw_code in candidate_codes:
        score += 6.0
        reasons.append("code_exact")

    if raw_tail and raw_tail in candidate_codes and raw_tail != raw_code:
        score += 5.0
        reasons.append("uri_tail_exact")

    if raw_norm and raw_norm in candidate_text_norms:
        score += 5.0
        reasons.append("text_exact")

    clues = _split_clues(raw_value)
    for clue in clues:
        clue_norm = _normalize_text(clue)
        if clue_norm and clue_norm in candidate_text_norms:
            score += 3.0
            reasons.append(f"clue_text:{clue}")

        overlap = _tokenize(clue) & candidate_tokens
        if overlap:
            bonus = min(2.0, 0.75 * len(overlap))
            score += bonus
            reasons.append("clue_tokens:" + ",".join(sorted(list(overlap))[:4]))

    source_exact_hits = _item_text_norms(source_item) & candidate_text_norms
    if source_exact_hits:
        bonus = min(8.0, 4.0 * len(source_exact_hits))
        score += bonus
        reasons.append(
            "source_text_exact:" + ",".join(sorted(list(source_exact_hits))[:2])
        )

    source_overlap = source_tokens & candidate_tokens
    if source_overlap:
        bonus = min(5.0, 1.25 * len(source_overlap))
        score += bonus
        reasons.append(
            "source_token_overlap:" + ",".join(sorted(list(source_overlap))[:4])
        )

    codelist_overlap = source_codelist_core & candidate_codelist_core
    if codelist_overlap:
        bonus = min(4.0, 1.25 * len(codelist_overlap))
        score += bonus
        reasons.append(
            "codelist_token_overlap:" + ",".join(sorted(list(codelist_overlap))[:4])
        )

    if (
        source_codelist_tokens & _DETAIL_TOKENS
        and not (candidate_codelist_tokens & _DETAIL_TOKENS)
        and codelist_overlap
    ):
        bonus = 4.0 if len(codelist_overlap) >= 2 else 2.5
        score += bonus
        reasons.append("codelist_parent_name_match")

    family_overlap = source_family_core & candidate_family_core
    if family_overlap:
        bonus = min(3.0, 1.5 * len(family_overlap))
        score += bonus
        reasons.append(
            "family_token_overlap:" + ",".join(sorted(list(family_overlap))[:4])
        )

    if _family_parent(source_family) == candidate_family:
        score += 4.0
        reasons.append(f"family_parent_match:{source_family}->{candidate_family}")

    return score, reasons

def _rank_candidates(
    raw_value: str,
    *,
    source_item: Mapping[str, Any],
    candidate_item_ids: list[int],
    items: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    ranked: list[dict[str, Any]] = []

    for item_id in candidate_item_ids:
        item = items.get(item_id)
        if not item:
            continue
        score, reasons = _score_candidate(
            raw_value,
            source_item=source_item,
            candidate_item=item,
        )
        ranked.append(
            {
                **_candidate_summary(item_id, item),
                "score": round(score, 3),
                "signals": reasons,
            }
        )

    ranked.sort(key=lambda row: (-row["score"], row["item_id"]))

    if not ranked:
        return ranked, None

    if len(ranked) == 1:
        top = ranked[0]
        confidence = min(0.99, 0.55 + 0.04 * top["score"])
        recommendation = {
            **top,
            "confidence": round(confidence, 3),
            "recommended_reason": " + ".join(top["signals"]) if top["signals"] else None,
        }
        return ranked, recommendation

    top = ranked[0]
    second = ranked[1]
    margin = top["score"] - second["score"]

    recommendation: dict[str, Any] | None = None
    if top["score"] >= 6.0 and margin >= 1.5:
        confidence = min(0.99, 0.45 + 0.05 * top["score"] + 0.08 * margin)
        recommendation = {
            **top,
            "confidence": round(confidence, 3),
            "recommended_reason": " + ".join(top["signals"]) if top["signals"] else None,
        }

    return ranked, recommendation

def _lang_slots(prefix: str, entries: Any) -> dict[str, Any]:
    values = lang_values(entries)
    return {
        f"{prefix}_sk": values.get("sk"),
        f"{prefix}_en": values.get("en"),
    }



def _canonical_item_uri(*, uri_pattern: str, item_code: str) -> str:
    return f"{uri_pattern}{item_code}"



def _normalize_uri_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return normalize_uri(str(value))
    except ValueError:
        return None



def _normalize_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("\u00a0", " ")
    text = _WS_RE.sub(" ", text)
    return text.casefold()



def _clean_relation_token(value: str) -> str:
    token = value.strip().strip("\"'")
    token = re.sub(r"^[\-•·*]+\s*", "", token)
    token = re.sub(r"^pozri\s+", "", token, flags=re.IGNORECASE)
    token = _WS_RE.sub(" ", token).strip(" ,;:")
    return token



def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None



def _logical_order(item: Mapping[str, Any]) -> int | None:
    row = choose_current_or_latest(item.get("codelistItemLogicalOrders"))
    if row is None:
        return None
    return _maybe_int(row.get("value"))



def _legislative_validity(item: Mapping[str, Any]) -> bool | None:
    row = choose_current_or_latest(item.get("codelistItemLegislativeValidities"))
    if row is None:
        return None
    return parse_bool_strict(row.get("value"))



def _effective_bounds_for_item(item: Mapping[str, Any]) -> tuple[date | None, date | None]:
    item_valid_from = parse_iso_date(item.get("validFrom"))
    validity = choose_current_or_latest(item.get("codelistItemValidities")) or {}
    effective_from = parse_iso_date(validity.get("effectiveFrom")) or item_valid_from
    effective_to = parse_iso_date(validity.get("effectiveTo"))
    return effective_from, effective_to



def _extract_relation_values(item: Mapping[str, Any], key: str) -> list[str]:
    values: list[str] = []
    for row in as_list(item.get(key)):
        if not isinstance(row, Mapping):
            continue
        raw_value = str(row.get("value") or "").strip()
        if raw_value:
            values.append(raw_value)
    return unique_nonempty(values)



def _match_singleton(index: dict[str, set[int]], key: str | None) -> int | None:
    if not key:
        return None
    ids = index.get(key)
    if ids and len(ids) == 1:
        return next(iter(ids))
    return None



def _candidate_ids(index: dict[str, set[int]], key: str | None) -> set[int]:
    if not key:
        return set()
    return set(index.get(key) or set())



def _uri_tail(value: str) -> str | None:
    normalized = _normalize_uri_or_none(value)
    if normalized is None:
        return None
    tail = normalized.rstrip("/").rsplit("/", 1)[-1].strip()
    return tail or None



def _split_clues(raw_value: str) -> list[str]:
    parts = [_clean_relation_token(part) for part in _SPLIT_RE.split(raw_value)]
    return [part for part in parts if part]



def _extract_explicit_refs(raw_value: str) -> list[str]:
    refs: list[str] = []
    refs.extend(match.group(0) for match in _URI_RE.finditer(raw_value))
    refs.extend(match.group(0) for match in _CODELIKE_RE.finditer(raw_value))
    return unique_nonempty(_clean_relation_token(ref) for ref in refs)



def _resolve_explicit_ref(
    raw_ref: str,
    *,
    by_uri: dict[str, set[int]],
    by_code: dict[str, set[int]],
) -> int | None:
    direct_uri = _match_singleton(by_uri, _normalize_uri_or_none(raw_ref))
    if direct_uri is not None:
        return direct_uri

    tail = _uri_tail(raw_ref)
    direct_code = _match_singleton(by_code, tail or raw_ref.strip())
    if direct_code is not None:
        return direct_code

    return None



def _resolve_composite_value(
    raw_value: str,
    *,
    by_uri: dict[str, set[int]],
    by_code: dict[str, set[int]],
    by_text: dict[str, set[int]],
) -> int | None:
    for candidate in (
        _match_singleton(by_uri, _normalize_uri_or_none(raw_value)),
        _match_singleton(by_code, raw_value.strip()),
        _match_singleton(by_code, _uri_tail(raw_value)),
        _match_singleton(by_text, _normalize_text(raw_value)),
    ):
        if candidate is not None:
            return candidate

    clues = _split_clues(raw_value)
    if len(clues) < 2:
        return None

    evidence_sets: list[set[int]] = []
    singleton_hits: list[int] = []
    for clue in clues:
        candidate_set = set()
        candidate_set |= _candidate_ids(by_uri, _normalize_uri_or_none(clue))
        candidate_set |= _candidate_ids(by_code, clue)
        candidate_set |= _candidate_ids(by_code, _uri_tail(clue))
        candidate_set |= _candidate_ids(by_text, _normalize_text(clue))
        if not candidate_set:
            continue
        evidence_sets.append(candidate_set)
        if len(candidate_set) == 1:
            singleton_hits.append(next(iter(candidate_set)))

    if len(evidence_sets) < 2:
        return None

    intersection = set(evidence_sets[0])
    for candidate_set in evidence_sets[1:]:
        intersection &= candidate_set
    if len(intersection) == 1:
        return next(iter(intersection))

    unique_singletons = set(singleton_hits)
    if len(unique_singletons) == 1 and len(singleton_hits) >= 2:
        return next(iter(unique_singletons))

    return None



def _resolve_relation_targets(
    raw_value: str,
    *,
    by_uri: dict[str, set[int]],
    by_code: dict[str, set[int]],
    by_text: dict[str, set[int]],
) -> list[int]:
    composite = _resolve_composite_value(
        raw_value,
        by_uri=by_uri,
        by_code=by_code,
        by_text=by_text,
    )
    if composite is not None:
        return [composite]

    explicit_targets: list[int] = []
    seen: set[int] = set()
    for raw_ref in _extract_explicit_refs(raw_value):
        target = _resolve_explicit_ref(raw_ref, by_uri=by_uri, by_code=by_code)
        if target is None or target in seen:
            continue
        explicit_targets.append(target)
        seen.add(target)
    return explicit_targets


def _source_item_summary(item_id: int, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "codelist_code": item.get("codelist_code"),
        "codelist_name_sk": item.get("codelist_name_sk"),
        "codelist_name_en": item.get("codelist_name_en"),
        "item_code": item.get("item_code"),
        "name_sk": item.get("name_sk"),
        "name_en": item.get("name_en"),
        "item_uri": item.get("item_uri"),
    }


def _debug_relation_value(
    raw_value: str,
    *,
    source_item: Mapping[str, Any],
    by_uri: dict[str, set[int]],
    by_code: dict[str, set[int]],
    by_text: dict[str, set[int]],
    items: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    normalized_uri = _normalize_uri_or_none(raw_value)
    raw_code = raw_value.strip()
    tail_code = _uri_tail(raw_value)
    normalized_text = _normalize_text(raw_value)

    uri_candidates = _candidate_ids(by_uri, normalized_uri)
    code_candidates = _candidate_ids(by_code, raw_code)
    tail_candidates = _candidate_ids(by_code, tail_code)
    text_candidates = _candidate_ids(by_text, normalized_text)

    all_candidates = set()
    all_candidates |= uri_candidates
    all_candidates |= code_candidates
    all_candidates |= tail_candidates
    all_candidates |= text_candidates

    codeish_candidates = set()
    codeish_candidates |= code_candidates
    codeish_candidates |= tail_candidates

    if not all_candidates:
        reason = "no_match"
    elif len(all_candidates) == 1:
        reason = "unexpected_singleton_not_selected"
    elif codeish_candidates and not uri_candidates and not text_candidates:
        reason = "ambiguous_code"
    elif uri_candidates and not codeish_candidates and not text_candidates:
        reason = "ambiguous_uri"
    elif text_candidates and not uri_candidates and not codeish_candidates:
        reason = "ambiguous_text"
    else:
        reason = "ambiguous_match"

    sorted_ids = sorted(all_candidates)
    candidates, recommendation = _rank_candidates(
        raw_value,
        source_item=source_item,
        candidate_item_ids=sorted_ids,
        items=items,
    )

    return {
        "value": raw_value,
        "reason": reason,
        "candidate_item_ids": sorted_ids,
        "candidate_codes": [
            items[item_id].get("item_code")
            for item_id in sorted_ids
            if item_id in items
        ],
        "candidate_codelists": [
            items[item_id].get("codelist_code")
            for item_id in sorted_ids
            if item_id in items
        ],
        "candidate_names_sk": [
            items[item_id].get("name_sk")
            for item_id in sorted_ids
            if item_id in items
        ],
        "match_sources": {
            "uri": sorted(uri_candidates),
            "code": sorted(code_candidates),
            "uri_tail": sorted(tail_candidates),
            "text": sorted(text_candidates),
        },
        "candidates": candidates,
        "recommended_item_id": recommendation["item_id"] if recommendation else None,
        "recommended_codelist_code": recommendation["codelist_code"] if recommendation else None,
        "recommended_item_code": recommendation["item_code"] if recommendation else None,
        "recommended_name_sk": recommendation["name_sk"] if recommendation else None,
        "recommended_reason": recommendation["recommended_reason"] if recommendation else None,
        "confidence": recommendation["confidence"] if recommendation else None,
    }


def normalize_codelist_payload(
    *,
    code: str,
    header: Mapping[str, Any],
    items: list[dict[str, Any]],
    uri_pattern: str,
    item_prefix: str,
    publisher_ico: str | None,
    last_modified: date | None,
    ontology_entry: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    ontology_entry = ontology_entry or {}
    header_uri = str(header.get("uri") or f"https://data.gov.sk/set/codelist/{code}").strip()
    codelist_valid_from = parse_iso_date(header.get("validFrom"))
    codelist_effective_from = parse_iso_date(header.get("effectiveFrom")) or codelist_valid_from
    codelist_effective_to = parse_iso_date(header.get("effectiveTo"))

    codelist_record: dict[str, Any] = {
        "code": code,
        "uri": header_uri,
        "valid_from": codelist_valid_from.isoformat() if codelist_valid_from else None,
        "effective_from": codelist_effective_from.isoformat() if codelist_effective_from else None,
        "effective_to": codelist_effective_to.isoformat() if codelist_effective_to else None,
        "last_modified": last_modified.isoformat() if last_modified else None,
        "publisher_ico": publisher_ico,
        "uri_pattern": uri_pattern,
        "item_prefix": item_prefix,
        "items": [],
        "item_type_uris": list(ontology_entry.get("itemTypeUris") or []),
        "item_type_qnames": list(ontology_entry.get("itemTypeQNames") or []),
        "source_values": unique_nonempty(as_list(header.get("codelistSource"))),
        **_lang_slots("name", header.get("codelistNames")),
        **_lang_slots("label", header.get("codelistLabels")),
        **_lang_slots("note", header.get("codelistNotes")),
    }

    item_records: dict[int, dict[str, Any]] = {}
    hierarchy_stubs: dict[int, dict[str, Any]] = {}

    for item in items:
        item_id = _maybe_int(item.get("id"))
        item_code = str(item.get("itemCode") or "").strip()
        if item_id is None or not item_code:
            continue

        item_valid_from = parse_iso_date(item.get("validFrom"))
        item_effective_from, item_effective_to = _effective_bounds_for_item(item)
        canonical_uri = _canonical_item_uri(uri_pattern=uri_pattern, item_code=item_code)
        raw_item_uri = str(item.get("itemUri") or "").strip() or None

        item_record: dict[str, Any] = {
            "id": item_id,
            "codelist_code": code,
            "codelist_uri": header_uri,
            "codelist_uri_pattern": uri_pattern,
            "codelist_name_sk": codelist_record.get("name_sk"),
            "codelist_name_en": codelist_record.get("name_en"),
            "codelist_label_sk": codelist_record.get("label_sk"),
            "codelist_label_en": codelist_record.get("label_en"),
            "item_code": item_code,
            "item_uri": canonical_uri,
            "item_uri_raw": raw_item_uri,
            "item_uri_raw_normalized": _normalize_uri_or_none(raw_item_uri),
            "valid_from": item_valid_from.isoformat() if item_valid_from else None,
            "effective_from": item_effective_from.isoformat() if item_effective_from else None,
            "effective_to": item_effective_to.isoformat() if item_effective_to else None,
            "logical_order": _logical_order(item),
            "legislative_validity": _legislative_validity(item),
            "item_type_uris": list(ontology_entry.get("itemTypeUris") or []),
            "item_type_qnames": list(ontology_entry.get("itemTypeQNames") or []),
            **_lang_slots("name", item.get("codelistItemNames")),
            **_lang_slots("label", item.get("codelistItemLabels")),
            **_lang_slots("note", item.get("codelistItemNotes")),
            **_lang_slots("abbreviated_name", item.get("codelistItemAbbreviatedNames")),
            **_lang_slots("additional_content", item.get("codelistItemAdditionalContents")),
        }
        item_records[item_id] = item_record
        codelist_record["items"].append(item_id)

        raw_relations = {
            "includes": _extract_relation_values(item, "codelistItemIncludes"),
            "includesAlso": _extract_relation_values(item, "codelistItemIncludesAlso"),
            "excludes": _extract_relation_values(item, "codelistItemExcludes"),
        }
        if any(raw_relations.values()):
            hierarchy_stubs[item_id] = {"raw": raw_relations}

    return codelist_record, item_records, hierarchy_stubs




def _build_lookup_indexes(
    items: dict[int, dict[str, Any]],
    *,
    codelist_code: str | None = None,
) -> tuple[dict[str, set[int]], dict[str, set[int]], dict[str, set[int]]]:
    by_uri: dict[str, set[int]] = defaultdict(set)
    by_code: dict[str, set[int]] = defaultdict(set)
    by_text: dict[str, set[int]] = defaultdict(set)

    for item_id, item in items.items():
        if codelist_code is not None and str(item.get("codelist_code") or "") != codelist_code:
            continue

        for candidate in (
            item.get("item_uri"),
            item.get("item_uri_raw_normalized"),
        ):
            normalized = _normalize_uri_or_none(candidate)
            if normalized is not None:
                by_uri[normalized].add(item_id)

        code = str(item.get("item_code") or "").strip()
        if code:
            by_code[code].add(item_id)

        tail = _uri_tail(str(item.get("item_uri") or ""))
        if tail:
            by_code[tail].add(item_id)

        raw_tail = _uri_tail(str(item.get("item_uri_raw_normalized") or ""))
        if raw_tail:
            by_code[raw_tail].add(item_id)

        for field in _ITEM_TEXT_FIELDS:
            normalized_text = _normalize_text(item.get(field))
            if normalized_text is not None:
                by_text[normalized_text].add(item_id)

    return by_uri, by_code, by_text


def _is_uri_base(value: str | None) -> bool:
    text = str(value or "").strip()
    return text.startswith("http://") or text.startswith("https://")


def _normalize_external_base(value: str) -> str:
    return str(value or "").strip().rstrip("/") + "/"


def _external_identifier_from_value(raw_value: str, *, parent_base: str) -> tuple[str | None, str | None]:
    text = str(raw_value or "").strip()
    if not text:
        return None, None

    base = _normalize_external_base(parent_base)
    normalized_uri = _normalize_uri_or_none(text)
    if normalized_uri and normalized_uri.startswith(base):
        identifier = normalized_uri[len(base):].strip("/")
        return identifier or None, normalized_uri

    uri_match = _URI_RE.search(text)
    if uri_match:
        uri = _normalize_uri_or_none(uri_match.group(0))
        if uri and uri.startswith(base):
            identifier = uri[len(base):].strip("/")
            return identifier or None, uri

    if "legal-subject" in base:
        digits = re.sub(r"\D+", "", text)
        if digits and len(digits) <= 8:
            identifier = digits.zfill(8)
            return identifier, base + identifier
        return None, None

    tokens = [x for x in _CODELIKE_RE.findall(text) if x.strip()]
    if len(tokens) == 1:
        identifier = tokens[0].strip()
        return identifier, base + identifier

    cleaned = text.strip().strip("/ ")
    if cleaned and not any(ch.isspace() for ch in cleaned) and "://" not in cleaned:
        return cleaned, base + cleaned

    return None, None


def _external_kind_for_base(parent_base: str) -> str:
    base = _normalize_external_base(parent_base)
    if "legal-subject" in base:
        return "legal_subject"
    return "external_uri"


def resolve_hierarchies(
    *,
    items: dict[int, dict[str, Any]],
    hierarchy_stubs: dict[int, dict[str, Any]],
    external_ico_resolver: Callable[[str], dict[str, Any] | None] | None = None,
    accept_clear_recommendations: bool = False,
    hierarchy_config: Mapping[str, str] | None = None,
    external_relation_predicates: Mapping[str, Any] | None = None,
) -> tuple[
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
]:
    by_uri, by_code, by_text = _build_lookup_indexes(items)

    configured_parent_indexes: dict[str, tuple[dict[str, set[int]], dict[str, set[int]], dict[str, set[int]]]] = {}
    if hierarchy_config is not None:
        for parent_spec in set(hierarchy_config.values()):
            if parent_spec and not _is_uri_base(parent_spec):
                configured_parent_indexes[parent_spec] = _build_lookup_indexes(
                    items,
                    codelist_code=parent_spec,
                )

    resolved: dict[int, dict[str, Any]] = {}
    no_match: dict[int, dict[str, Any]] = {}
    ambiguous: dict[int, dict[str, Any]] = {}

    for source_item_id, stub in hierarchy_stubs.items():
        raw_bucket = (stub or {}).get("raw") or {}
        source_item = items.get(source_item_id, {})

        resolved_entry = {
            "includes": [],
            "includesAlso": [],
            "excludes": [],
            "includes_external": [],
            "includesAlso_external": [],
            "excludes_external": [],
        }

        no_match_entry = {
            "source": _source_item_summary(source_item_id, source_item),
            "includes": [],
            "includesAlso": [],
            "excludes": [],
        }

        ambiguous_entry = {
            "source": _source_item_summary(source_item_id, source_item),
            "includes": [],
            "includesAlso": [],
            "excludes": [],
        }

        for relation_key in ("includes", "includesAlso", "excludes"):
            seen_ids: set[int] = set()
            seen_issue_values: set[str] = set()

            for raw_value in raw_bucket.get(relation_key, []):
                text = str(raw_value or "").strip()
                if not text:
                    continue

                if hierarchy_config is not None:
                    source_code = str(source_item.get("codelist_code") or "").strip()
                    parent_spec = str(hierarchy_config.get(source_code) or "").strip()

                    if not parent_spec:
                        if text not in seen_issue_values:
                            no_match_entry[relation_key].append({
                                "raw_value": text,
                                "reason": "no_parent_config",
                                "message": "Source codelist has relation values but no parent configured in config.hierarchy.",
                            })
                            seen_issue_values.add(text)
                        continue

                    if _is_uri_base(parent_spec):
                        identifier, target_uri = _external_identifier_from_value(
                            text,
                            parent_base=parent_spec,
                        )
                        if target_uri:
                            external_key = f"{relation_key}_external"
                            existing_external_uris = {
                                str(x.get("uri") or "").strip()
                                for x in resolved_entry[external_key]
                                if isinstance(x, Mapping)
                            }
                            if target_uri not in existing_external_uris:
                                resolved_entry[external_key].append({
                                    "kind": _external_kind_for_base(parent_spec),
                                    "identifier": identifier,
                                    "uri": target_uri,
                                    "uri_base": _normalize_external_base(parent_spec),
                                    "predicate": predicate_for_external_base(
                                        parent_spec,
                                        relation_key,
                                        external_relation_predicates,
                                    ),
                                    "matched_via": "config_external_uri_base",
                                    "raw_value": text,
                                })
                            continue

                        if text not in seen_issue_values:
                            no_match_entry[relation_key].append({
                                "raw_value": text,
                                "reason": "external_identifier_not_found",
                                "configured_parent": parent_spec,
                                "message": "Could not extract an identifier to append to configured external URI base.",
                            })
                            seen_issue_values.add(text)
                        continue

                    parent_indexes = configured_parent_indexes.get(parent_spec)
                    if parent_indexes is None:
                        if text not in seen_issue_values:
                            no_match_entry[relation_key].append({
                                "raw_value": text,
                                "reason": "parent_not_indexed",
                                "configured_parent": parent_spec,
                                "message": "Configured parent codelist was not present in normalized analysis items.",
                            })
                            seen_issue_values.add(text)
                        continue

                    p_by_uri, p_by_code, p_by_text = parent_indexes
                    target_item_ids = _resolve_relation_targets(
                        text,
                        by_uri=p_by_uri,
                        by_code=p_by_code,
                        by_text=p_by_text,
                    )

                    if len(target_item_ids) == 1:
                        target_item_id = target_item_ids[0]
                        if target_item_id not in seen_ids:
                            resolved_entry[relation_key].append(target_item_id)
                            seen_ids.add(target_item_id)
                        continue

                    if len(target_item_ids) > 1:
                        if text not in seen_issue_values:
                            ambiguous_entry[relation_key].append({
                                "raw_value": text,
                                "reason": "multiple_matches_in_configured_parent",
                                "configured_parent": parent_spec,
                                "candidates": [
                                    _candidate_summary(item_id, items[item_id])
                                    for item_id in target_item_ids
                                    if item_id in items
                                ],
                                "auto_selected": False,
                                "auto_selected_item_id": None,
                            })
                            seen_issue_values.add(text)
                        continue

                    if text not in seen_issue_values:
                        no_match_entry[relation_key].append({
                            "raw_value": text,
                            "reason": "no_match_in_configured_parent",
                            "configured_parent": parent_spec,
                            "message": "No item in the configured parent codelist matched this relation value.",
                        })
                        seen_issue_values.add(text)
                    continue

                target_item_ids = _resolve_relation_targets(
                    text,
                    by_uri=by_uri,
                    by_code=by_code,
                    by_text=by_text,
                )

                if target_item_ids:
                    for target_item_id in target_item_ids:
                        if target_item_id not in seen_ids:
                            resolved_entry[relation_key].append(target_item_id)
                            seen_ids.add(target_item_id)
                    continue

                external_key = f"{relation_key}_external"
                external_target = None

                if external_ico_resolver is not None and _looks_like_ico_candidate(text):
                    external_target = external_ico_resolver(text)

                if external_target is not None:
                    existing_external_uris = {
                        str(x.get("uri") or "").strip()
                        for x in resolved_entry[external_key]
                        if isinstance(x, Mapping)
                    }
                    ext_uri = str(external_target.get("uri") or "").strip()
                    if ext_uri and ext_uri not in existing_external_uris:
                        external_target.setdefault("predicate", predicate_for_external_base(
                            str(external_target.get("uri") or ""),
                            relation_key,
                            external_relation_predicates,
                        ))
                        resolved_entry[external_key].append(external_target)
                    continue

                if text in seen_issue_values:
                    continue

                debug_entry = _debug_relation_value(
                    text,
                    source_item=source_item,
                    by_uri=by_uri,
                    by_code=by_code,
                    by_text=by_text,
                    items=items,
                )
                seen_issue_values.add(text)

                if debug_entry["reason"] == "no_match":
                    no_match_entry[relation_key].append(debug_entry)
                    continue

                recommended_id = debug_entry.get("recommended_item_id")
                if accept_clear_recommendations and recommended_id is not None:
                    recommended_id = int(recommended_id)
                    if recommended_id not in seen_ids:
                        resolved_entry[relation_key].append(recommended_id)
                        seen_ids.add(recommended_id)
                    debug_entry["auto_selected"] = True
                    debug_entry["auto_selected_item_id"] = recommended_id
                else:
                    debug_entry["auto_selected"] = False
                    debug_entry["auto_selected_item_id"] = None

                ambiguous_entry[relation_key].append(debug_entry)

        if any(resolved_entry[k] for k in resolved_entry):
            resolved[source_item_id] = resolved_entry

        if any(no_match_entry[k] for k in ("includes", "includesAlso", "excludes")):
            no_match[source_item_id] = no_match_entry

        if any(ambiguous_entry[k] for k in ("includes", "includesAlso", "excludes")):
            ambiguous[source_item_id] = ambiguous_entry

    return resolved, no_match, ambiguous
