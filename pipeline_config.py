from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


DEFAULT_RELATION_PREDICATES: dict[str, dict[str, str]] = {
    # MetaIS "includes" is modeled as child item -> parent item.
    # The inverse is emitted when the parent item is present in the generated graph.
    "includes": {
        "predicate": "skos:broader",
        "inversePredicate": "skos:narrower",
    },
}

DEFAULT_EXTERNAL_RELATION_PREDICATES: dict[str, str | dict[str, str]] = {
    # Default policy for the currently configured external legal-subject parent.
    # Can be overridden in config/externalRelationPredicates.
    "https://data.gov.sk/id/legal-subject/": {
        "includes": "skos:broader",
    },
}

# Canonical relation keys used by the normalized graph payload, plus aliases
# found in MetaIS JSON/XML payloads and likely config spellings.
RELATION_KEY_ALIASES: dict[str, str] = {
    "includes": "includes",
    "include": "includes",
    "codelistItemIncludes": "includes",
    "CodelistItemIncludes": "includes",
    "Includes": "includes",
    "codelistItemInclude": "includes",
    "includesAlso": "includesAlso",
    "includeAlso": "includesAlso",
    "codelistItemIncludesAlso": "includesAlso",
    "CodelistItemIncludesAlso": "includesAlso",
    "IncludesAlso": "includesAlso",
    "codelistItemIncludeAlso": "includesAlso",
    "excludes": "excludes",
    "exclude": "excludes",
    "codelistItemExcludes": "excludes",
    "CodelistItemExcludes": "excludes",
    "Excludes": "excludes",
    "codelistItemExclude": "excludes",
}


@dataclass(frozen=True)
class PipelineConfig:
    path: Path | None
    hierarchy: dict[str, str] = field(default_factory=dict)
    ignore: dict[str, Any] = field(default_factory=dict)
    relation_predicates: dict[str, dict[str, str]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_RELATION_PREDICATES.items()}
    )
    external_relation_predicates: dict[str, str | dict[str, str]] = field(
        default_factory=lambda: dict(DEFAULT_EXTERNAL_RELATION_PREDICATES)
    )

    @property
    def ignored_codes(self) -> set[str]:
        return set(self.ignore)


def _normalize_uri_base(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text.rstrip("/") + "/"
    return text


def canonical_relation_key(value: Any) -> str:
    """Normalize MetaIS/config relation names to payload relation keys.

    The normalized payload uses short keys (includes, includesAlso, excludes),
    but config files may reasonably use the original MetaIS field names such as
    codelistItemIncludes or XML names such as Includes.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text in RELATION_KEY_ALIASES:
        return RELATION_KEY_ALIASES[text]
    lowered = text[:1].lower() + text[1:]
    if lowered in RELATION_KEY_ALIASES:
        return RELATION_KEY_ALIASES[lowered]
    return text


def _clean_mapping(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        code = str(key).strip()
        target = str(value).strip()
        if code and target:
            out[code] = _normalize_uri_base(target)
    return out


def _clean_relation_predicates(raw: Any) -> dict[str, dict[str, str]]:
    """Return relation-key -> predicate config.

    Preferred config shape:

        "relationPredicates": {
          "includes": {
            "predicate": "skos:broader",
            "inversePredicate": "skos:narrower"
          }
        }

    A compact string is also accepted and means forward predicate only:

        "relationPredicates": {"includesAlso": "skos:related"}

    For convenience/backward editing, flat keys such as includesPredicate and
    includesPredicateInverse are accepted too.

    If two aliases resolve to the same canonical relation key, fail loudly instead
    of letting JSON key order decide the result.  For example, using both
    "includes" and "codelistItemIncludes" is ambiguous because both configure
    the same internal relation.
    """
    out: dict[str, dict[str, str]] = {
        key: dict(value) for key, value in DEFAULT_RELATION_PREDICATES.items()
    }
    seen_relation_sources: dict[str, str] = {}

    def check_duplicate(original_key: str, relation_key: str) -> None:
        previous = seen_relation_sources.get(relation_key)
        if previous is not None and previous != original_key:
            raise ValueError(
                "Duplicate relationPredicates entries configure the same relation: "
                f"{previous!r} and {original_key!r} both resolve to {relation_key!r}. "
                "Use only one spelling."
            )
        seen_relation_sources[relation_key] = original_key

    def set_value(relation_key: str, field_name: str, value: Any) -> None:
        relation_key = canonical_relation_key(relation_key)
        predicate = str(value or "").strip()
        if not relation_key or not predicate:
            return
        out.setdefault(relation_key, {})[field_name] = predicate

    if isinstance(raw, Mapping):
        for key, value in raw.items():
            original_key = str(key).strip()
            relation_key = canonical_relation_key(original_key)
            if not relation_key:
                continue
            check_duplicate(original_key, relation_key)
            if isinstance(value, Mapping):
                for source_key, target_key in (
                    ("predicate", "predicate"),
                    ("forward", "predicate"),
                    ("forwardPredicate", "predicate"),
                    ("inversePredicate", "inversePredicate"),
                    ("inverse", "inversePredicate"),
                ):
                    if source_key in value:
                        set_value(relation_key, target_key, value[source_key])
            else:
                set_value(relation_key, "predicate", value)

    return out


def _apply_flat_relation_predicates(
    data: Mapping[str, Any],
    relation_predicates: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    out = {key: dict(value) for key, value in relation_predicates.items()}

    relation_flat_aliases = {
        "includes": ("includes", "codelistItemIncludes"),
        "includesAlso": ("includesAlso", "codelistItemIncludesAlso"),
        "excludes": ("excludes", "codelistItemExcludes"),
    }

    for relation_key, base_names in relation_flat_aliases.items():
        for field_name, aliases in {
            "predicate": tuple(
                alias
                for base in base_names
                for alias in (
                    f"{base}Predicate",
                    f"{base}_predicate",
                )
            ),
            "inversePredicate": tuple(
                alias
                for base in base_names
                for alias in (
                    f"{base}PredicateInverse",
                    f"{base}InversePredicate",
                    f"{base}_predicate_inverse",
                    f"{base}_inverse_predicate",
                )
            ),
        }.items():
            for alias in aliases:
                value = data.get(alias)
                if value is not None and str(value).strip():
                    out.setdefault(relation_key, {})[field_name] = str(value).strip()
                    break

    return out


def _clean_external_predicates(raw: Any) -> dict[str, str | dict[str, str]]:
    out: dict[str, str | dict[str, str]] = dict(DEFAULT_EXTERNAL_RELATION_PREDICATES)
    if not isinstance(raw, Mapping):
        return out

    for key, value in raw.items():
        uri_base = _normalize_uri_base(str(key).strip())
        if not uri_base:
            continue
        if isinstance(value, Mapping):
            relation_map: dict[str, str] = {}
            seen_relation_sources: dict[str, str] = {}
            for rel_key, predicate_value in value.items():
                original_rel_key = str(rel_key).strip()
                canonical_key = canonical_relation_key(original_rel_key)
                predicate = str(predicate_value or "").strip()
                if not canonical_key or not predicate:
                    continue
                previous = seen_relation_sources.get(canonical_key)
                if previous is not None and previous != original_rel_key:
                    raise ValueError(
                        "Duplicate externalRelationPredicates entries configure the same relation "
                        f"for URI base {uri_base!r}: {previous!r} and {original_rel_key!r} "
                        f"both resolve to {canonical_key!r}. Use only one spelling."
                    )
                seen_relation_sources[canonical_key] = original_rel_key
                relation_map[canonical_key] = predicate
            if relation_map:
                out[uri_base] = relation_map
        else:
            predicate = str(value).strip()
            if predicate:
                out[uri_base] = predicate
    return out


def load_pipeline_config(path: Path | None) -> PipelineConfig:
    if path is None or not path.exists():
        return PipelineConfig(path=path)

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"Config file {path} must contain a JSON object.")

    ignore = data.get("ignore") or {}
    if not isinstance(ignore, Mapping):
        raise ValueError("config.ignore must be an object.")

    relation_predicates = _clean_relation_predicates(
        data.get("relationPredicates")
        or data.get("relation_predicates")
        or data.get("relationMap")
        or {}
    )
    relation_predicates = _apply_flat_relation_predicates(data, relation_predicates)

    return PipelineConfig(
        path=path,
        hierarchy=_clean_mapping(data.get("hierarchy") or {}),
        ignore={str(k).strip(): v for k, v in ignore.items() if str(k).strip()},
        relation_predicates=relation_predicates,
        external_relation_predicates=_clean_external_predicates(
            data.get("externalRelationPredicates")
            or data.get("external_relation_predicates")
            or data.get("externalPredicates")
            or {}
        ),
    )


def relation_predicate(
    relation_key: str,
    predicate_map: Mapping[str, Mapping[str, str]] | None,
) -> str | None:
    active: dict[str, Mapping[str, str]] = {
        key: dict(value) for key, value in DEFAULT_RELATION_PREDICATES.items()
    }
    if predicate_map:
        active.update(predicate_map)
    entry = active.get(canonical_relation_key(relation_key))
    if not isinstance(entry, Mapping):
        return None
    predicate = str(
        entry.get("predicate")
        or entry.get("forward")
        or entry.get("forwardPredicate")
        or ""
    ).strip()
    return predicate or None


def relation_inverse_predicate(
    relation_key: str,
    predicate_map: Mapping[str, Mapping[str, str]] | None,
) -> str | None:
    active: dict[str, Mapping[str, str]] = {
        key: dict(value) for key, value in DEFAULT_RELATION_PREDICATES.items()
    }
    if predicate_map:
        active.update(predicate_map)
    entry = active.get(canonical_relation_key(relation_key))
    if not isinstance(entry, Mapping):
        return None
    predicate = str(
        entry.get("inversePredicate")
        or entry.get("inverse")
        or ""
    ).strip()
    return predicate or None


def predicate_for_external_base(
    uri_base: str,
    relation_key: str,
    predicate_map: Mapping[str, str | Mapping[str, str]] | None,
) -> str | None:
    active = dict(DEFAULT_EXTERNAL_RELATION_PREDICATES)
    if predicate_map:
        active.update(predicate_map)

    normalized_base = _normalize_uri_base(uri_base)
    best_key = ""
    best_value: str | Mapping[str, str] | None = None
    for key, value in active.items():
        normalized_key = _normalize_uri_base(str(key))
        if normalized_base.startswith(normalized_key) and len(normalized_key) > len(best_key):
            best_key = normalized_key
            best_value = value

    if isinstance(best_value, Mapping):
        canonical_key = canonical_relation_key(relation_key)
        return str(
            best_value.get(canonical_key)
            or best_value.get(relation_key)
            or best_value.get("default")
            or ""
        ).strip() or None
    if best_value:
        return str(best_value).strip() or None
    return None
