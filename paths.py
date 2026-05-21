from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class CodePaths:
    xml_file: Path
    integration_json_file: Path
    header_json_file: Path
    items_json_file: Path
    ttl_file: Path


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path
    data_root: Path
    issues_root: Path

    @classmethod
    def default(
        cls,
        *,
        project_root: Path | None = None,
        data_root: Path | None = None,
        issues_root: Path | None = None,
    ) -> "ProjectPaths":
        root = (project_root or PROJECT_ROOT).resolve()
        data = data_root if data_root is not None else root / "data"
        issues = issues_root if issues_root is not None else root / "issues"
        if not data.is_absolute():
            data = root / data
        if not issues.is_absolute():
            issues = root / issues
        return cls(project_root=root, data_root=data.resolve(), issues_root=issues.resolve())

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
    def input_dir(self) -> Path:
        return self.project_root / "input"

    @property
    def config_dir(self) -> Path:
        return self.project_root / "config"

    @property
    def default_config_file(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def jsonld_dir(self) -> Path:
        return self.data_root / "jsonld"

    @property
    def codelist_headers_file(self) -> Path:
        return self.json_dir / "codelistheaders.json"

    @property
    def ontology_map_file(self) -> Path:
        return self.json_dir / "codelist_to_ontology.json"

    @property
    def normalized_codelists_file(self) -> Path:
        return self.json_dir / "codelists.json"

    @property
    def normalized_items_file(self) -> Path:
        return self.json_dir / "items.json"

    @property
    def normalized_hierarchies_file(self) -> Path:
        return self.json_dir / "hierarchies.json"

    @property
    def hierarchy_no_match_issue_file(self) -> Path:
        return self.issues_root / "hierarchies_no_match.json"

    @property
    def hierarchy_ambiguous_issue_file(self) -> Path:
        return self.issues_root / "hierarchies_ambiguous.json"

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
            self.input_dir,
            self.config_dir,
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
