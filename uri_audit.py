from __future__ import annotations

import html
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import requests

from common import as_list
from config import DEREF_TIMEOUT
from models import ConversionResult, DerefCheck, UriPatternInfo
from common import item_name_values


DEREF_TITLE_RE = re.compile(
    r"znalosti o:</b></font><br>\s*(?:<font[^>]*><b>(.*?)</b></font>)?",
    re.IGNORECASE | re.DOTALL,
)

DEREF_PREFLABEL_RE = re.compile(
    r"skos:prefLabel.*?<div>&nbsp;.*?>\s*([^<]+)\s*</div>",
    re.IGNORECASE | re.DOTALL,
)


def uri_base(uri: str) -> str:
    parts = urlsplit(uri)
    path = parts.path.rstrip("/")
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    return urlunsplit((parts.scheme, parts.netloc, parent + "/", "", ""))


def normalize_uri(uri: str) -> str:
    raw = str(uri).strip()
    parts = urlsplit(raw)

    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"not a valid http(s) URI: {raw!r}")

    path = re.sub(r"/+", "/", parts.path)
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def uri_tail(uri: str) -> str:
    return uri.rstrip("/").split("/")[-1]


def classify_uri_base(base: str) -> UriPatternInfo:
    parts = urlsplit(base)
    path_parts = [x for x in parts.path.strip("/").split("/") if x]
    label = base.rstrip("/").split("/")[-1]

    if parts.netloc.casefold().endswith("data.gov.sk"):
        if len(path_parts) >= 2 and path_parts[0] == "def":
            if len(path_parts) == 2:
                return UriPatternInfo(
                    kind="data_gov_def_simple",
                    base=base,
                    label=label,
                    path_parts=path_parts,
                )
            if len(path_parts) == 3:
                return UriPatternInfo(
                    kind="data_gov_def_namespaced",
                    base=base,
                    label=label,
                    path_parts=path_parts,
                )

        if len(path_parts) == 3 and path_parts[:2] == ["set", "codelist"]:
            return UriPatternInfo(
                kind="data_gov_set_codelist",
                base=base,
                label=label,
                path_parts=path_parts,
            )

    return UriPatternInfo(
        kind="external_other",
        base=base,
        label=label,
        path_parts=path_parts,
    )


def choose_best_base(bases: list[str]) -> str:
    counts: dict[str, int] = {}
    for base in bases:
        counts[base] = counts.get(base, 0) + 1

    def base_rank(base: str) -> tuple[Any, ...]:
        info = classify_uri_base(base)
        kind_rank = {
            "data_gov_def_simple": 0,
            "data_gov_set_codelist": 1,
            "data_gov_def_namespaced": 2,
            "external_other": 3,
        }.get(info.kind, 99)

        return (
            kind_rank,
            -counts[base],
            len(base),
            base,
        )

    return sorted(counts, key=base_rank)[0]


def probe_uri(session: requests.Session, uri: str) -> str:
    try:
        response = session.get(uri, timeout=DEREF_TIMEOUT)
        text = response.text or ""
        return f"HTTP {response.status_code}, {len(text)} chars"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def infer_uri_pattern(
    session: requests.Session,
    items_json: Mapping[str, Any],
    *,
    code: str,
    result: ConversionResult,
) -> tuple[str, str]:
    bases: list[str] = []
    total_items = 0
    missing_uri = 0
    missing_code = 0
    invalid_uri = 0
    first_bad_uri: str | None = None

    for item in as_list(items_json.get("codelistsItems")):
        if not isinstance(item, Mapping):
            continue

        total_items += 1
        item_uri_raw = item.get("itemUri")
        item_code = str(item.get("itemCode") or "").strip()

        if not item_code:
            missing_code += 1
            continue

        if item_uri_raw in (None, ""):
            missing_uri += 1
            continue

        try:
            item_uri = normalize_uri(str(item_uri_raw))
        except ValueError:
            invalid_uri += 1
            if first_bad_uri is None:
                first_bad_uri = str(item_uri_raw)
            continue

        bases.append(uri_base(item_uri))

    if not bases:
        msg = (
            f"[{code}] Could not infer item URI pattern. "
            f"total_items={total_items}, missing_uri={missing_uri}, "
            f"missing_code={missing_code}, invalid_uri={invalid_uri}"
        )
        if first_bad_uri is not None:
            msg += (
                f", sample_bad_uri={first_bad_uri!r}, "
                f"probe={probe_uri(session, first_bad_uri)}"
            )
        raise ValueError(msg)

    unique_bases = sorted(set(bases))
    chosen_base = choose_best_base(bases)
    chosen_info = classify_uri_base(chosen_base)

    if len(unique_bases) > 1:
        result.warn("item URIs do not share one consistent base.")
        for base in unique_bases:
            info = classify_uri_base(base)
            result.warn(f"  base={base} ; kind={info.kind} ; pathParts={info.path_parts}")
        result.warn(f"using chosen base: {chosen_base} ; kind={chosen_info.kind}")

    return chosen_base, chosen_info.label


def _html_unescape_and_strip(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(html.unescape(text).split()).strip()


def inspect_deref_page(
    session: requests.Session,
    uri: str,
    *,
    result: ConversionResult | None = None,
    stage: str = "deref",
    item_code: str | None = None,
) -> DerefCheck:
    try:
        response = session.get(uri, timeout=DEREF_TIMEOUT)
    except requests.RequestException:
        return DerefCheck(
            ok=False,
            uri=uri,
            title=None,
            pref_labels=[],
            has_rdf_type=False,
            has_pref_label_prop=False,
            has_nonempty_rows=False,
            status_code=0,
        )

    status = response.status_code

    if result is not None and status >= 500:
        extra = {}
        if item_code:
            extra["itemCode"] = item_code
        result.add_http_5xx(
            stage=stage,
            url=uri,
            status=status,
            reason=response.reason,
            extra=extra,
        )

    if not response.ok:
        return DerefCheck(
            ok=False,
            uri=uri,
            title=None,
            pref_labels=[],
            has_rdf_type=False,
            has_pref_label_prop=False,
            has_nonempty_rows=False,
            status_code=status,
        )

    body = response.text

    title_match = DEREF_TITLE_RE.search(body)
    title = _html_unescape_and_strip(title_match.group(1) if title_match else None)

    pref_labels = [
        _html_unescape_and_strip(x)
        for x in DEREF_PREFLABEL_RE.findall(body)
        if _html_unescape_and_strip(x)
    ]

    has_rdf_type = "rdf:type" in body
    has_pref_label_prop = "skos:prefLabel" in body
    has_nonempty_rows = has_rdf_type or has_pref_label_prop or bool(pref_labels) or bool(title)
    ok = bool(title) and has_nonempty_rows

    return DerefCheck(
        ok=ok,
        uri=uri,
        title=title or None,
        pref_labels=pref_labels,
        has_rdf_type=has_rdf_type,
        has_pref_label_prop=has_pref_label_prop,
        has_nonempty_rows=has_nonempty_rows,
        status_code=status,
    )


def deref_text_candidates(check: DerefCheck) -> set[str]:
    out: set[str] = set()
    if check.title:
        out.add(check.title.casefold())
    out.update(x.casefold() for x in check.pref_labels if x)
    return out


def audit_item_uris(
    session: requests.Session,
    *,
    code: str,
    items: list[dict[str, Any]],
    uri_pattern: str,
    result: ConversionResult,
) -> None:
    for item in items:
        item_code = str(item.get("itemCode") or "").strip()
        item_uri_raw = item.get("itemUri")
        if not item_code or item_uri_raw in (None, ""):
            continue

        labels = item_name_values(item.get("codelistItemNames"))

        try:
            item_uri = normalize_uri(str(item_uri_raw))
        except ValueError:
            result.warn(
                f"itemCode={item_code} has invalid URI {item_uri_raw!r}; "
                f"probe={probe_uri(session, str(item_uri_raw))}"
            )
            continue

        expected_uri = uri_pattern + item_code
        if item_uri == expected_uri:
            continue

        current_tail = uri_tail(item_uri)
        expected_tail = item_code
        current_base = uri_base(item_uri)
        expected_base = uri_pattern

        if item_uri.rstrip("/") == uri_pattern.rstrip("/"):
            result.warn(
                f"item URI missing tail: itemCode={item_code}, uri={item_uri}. "
                f"Resolved canonical URI to {expected_uri}."
            )
            continue

        current_check = inspect_deref_page(
            session,
            item_uri,
            result=result,
            stage="deref_current",
            item_code=item_code,
        )
        expected_check = inspect_deref_page(
            session,
            expected_uri,
            result=result,
            stage="deref_expected",
            item_code=item_code,
        )

        current_candidates = deref_text_candidates(current_check)
        expected_candidates = deref_text_candidates(expected_check)

        expected_matches = any(
            label.strip() and label.casefold() in expected_candidates
            for label in labels
        )
        current_matches = any(
            label.strip() and label.casefold() in current_candidates
            for label in labels
        )

        if current_tail == expected_tail and current_base != expected_base:
            if expected_check.ok and expected_matches and not (current_check.ok and current_matches):
                result.warn(
                    f"item URI base mismatch: itemCode={item_code}, uri={item_uri}. "
                    f"Dereference indicates canonical URI is {expected_uri}."
                )
            else:
                result.warn(
                    f"item URI base mismatch: itemCode={item_code}, uri={item_uri}. "
                    f"Current base={current_base}, canonical base={expected_base}."
                )
            continue

        if current_tail != expected_tail and current_base == expected_base:
            result.warn(
                f"item URI tail mismatch: itemCode={item_code}, uri={item_uri}. "
                f"Expected tail {expected_tail!r}, got {current_tail!r}. "
                f"Using canonical URI {expected_uri}."
            )
            result.info(
                f"resolved tail mismatch for {item_code}: current tail={current_tail!r}, "
                f"expected tail={expected_tail!r}"
            )
            continue

        if expected_check.ok and expected_matches and not (current_check.ok and current_matches):
            result.warn(
                f"item URI mismatch: itemCode={item_code}, uri={item_uri}. "
                f"Dereference indicates canonical URI is {expected_uri}."
            )
        else:
            result.warn(
                f"item URI mismatch: itemCode={item_code}, uri={item_uri}. "
                f"Using canonical URI {expected_uri}."
            )

        if current_tail != expected_tail:
            result.info(
                f"resolved tail mismatch for {item_code}: current tail={current_tail!r}, "
                f"expected tail={expected_tail!r}"
            )
