from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodePaths:
    xml_file: Path
    integration_json_file: Path
    header_json_file: Path
    items_json_file: Path
    ttl_file: Path


@dataclass(frozen=True)
class ProjectPaths:
    data_root: Path
    issues_root: Path

    @classmethod
    def default(cls) -> "ProjectPaths":
        return cls(
            data_root=Path("data"),
            issues_root=Path("issues"),
        )

    @property
    def xml_dir(self) -> Path:
        return self.data_root / "xml"

    @property
    def json_dir(self) -> Path:
        return self.data_root / "json"

    @property
    def ttl_dir(self) -> Path:
        return self.data_root / "ttl"

    @property
    def rdf_dir(self) -> Path:
        return self.data_root / "rdf"

    @property
    def jsonld_dir(self) -> Path:
        return self.data_root / "jsonld"

    @property
    def ontology_map_file(self) -> Path:
        return self.json_dir / "codelist_to_ontology.json"

    @property
    def warnings_report_file(self) -> Path:
        return self.issues_root / "warnings.json"

    @property
    def errors_report_file(self) -> Path:
        return self.issues_root / "errors.json"

    def ensure_base_dirs(self) -> None:
        for directory in (
            self.xml_dir,
            self.json_dir,
            self.ttl_dir,
            self.rdf_dir,
            self.jsonld_dir,
            self.issues_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def for_code(self, code: str) -> CodePaths:
        self.ensure_base_dirs()
        return CodePaths(
            xml_file=self.xml_dir / f"{code}.xml",
            integration_json_file=self.json_dir / f"{code}.json",
            header_json_file=self.json_dir / f"{code}_header.json",
            items_json_file=self.json_dir / f"{code}_items.json",
            ttl_file=self.ttl_dir / f"{code}.ttl",
        )
