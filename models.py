from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodelistRef:
    id: int
    code: str
    state: str
    temporal: bool


@dataclass
class ConversionResult:
    code: str
    codelist_id: int | None = None
    ttl_file: Path | None = None
    created: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    integration_status: dict[str, Any] = field(default_factory=dict)
    item_duplicate_issues: list[dict[str, Any]] = field(default_factory=list)
    request_urls: dict[str, str] = field(default_factory=dict)
    request_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    http_5xx: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        if self.warnings or self.errors:
            return True
        return (self.integration_status or {}).get("ok") is False

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f"Warning [{self.code}]: {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        print(f"Error   [{self.code}]: {msg}", file=sys.stderr)

    def info(self, msg: str) -> None:
        self.infos.append(msg)
        print(f"Info    [{self.code}]: {msg}")

    def add_http_5xx(
        self,
        *,
        stage: str,
        url: str,
        status: int,
        reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "code": self.code,
            "codelistId": self.codelist_id,
            "stage": stage,
            "url": url,
            "status": status,
        }
        if reason:
            entry["reason"] = reason
        if extra:
            entry.update(extra)
        self.http_5xx.append(entry)

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "id": self.codelist_id,
            "created": self.created,
            "ttlFile": str(self.ttl_file) if self.ttl_file is not None else None,
            "warnings": self.warnings,
            "errors": self.errors,
            "infos": self.infos,
            "integrationStatus": self.integration_status,
            "itemDuplicateIssues": self.item_duplicate_issues,
            "requestUrls": self.request_urls,
            "requestStatus": self.request_status,
            "http5xx": self.http_5xx,
        }


@dataclass(frozen=True)
class UriPatternInfo:
    kind: str
    base: str
    label: str
    path_parts: list[str]


@dataclass
class DerefCheck:
    ok: bool
    uri: str
    title: str | None
    pref_labels: list[str]
    has_rdf_type: bool
    has_pref_label_prop: bool
    has_nonempty_rows: bool
    status_code: int
