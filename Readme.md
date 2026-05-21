# MetaIS Codelist to Turtle Pipeline

This project converts MetaIS codelists into Turtle (`.ttl`) files and preserves the raw source payloads and validation reports produced during the run.

At a high level, the pipeline:

- converts local ontology RDF files into JSON-LD,
- builds a codelist-to-ontology lookup used to enrich item typing,
- fetches codelist headers and items from MetaIS,
- resolves duplicate headers and duplicate item rows,
- infers and audits canonical item URIs,
- renders Turtle output for each selected codelist,
- writes structured warning/error/HTTP-5xx reports.

The result is a set of TTL files, local archive of the input payloads and a machine-readable record of suspicious or failed cases.

## What the pipeline produces

For each selected codelist, the pipeline stores:

- raw integration XML,
- parsed integration JSON (when XML parsing succeeds),
- header JSON,
- items JSON,
- generated Turtle.

It also writes run-level reports describing warnings, hard failures, duplicate conflicts, and recorded HTTP 5xx responses.

## Directory layout
```text
.
├── data/
│   ├── rdf/                          (local ontology RDF files - must exist prior to launching main.py)
│   ├── jsonld/                       (ontology RDF converted to JSON-LD)
│   ├── xml/                          (raw integration XML per codelist)
│   ├── json/
│   │   ├── codelist_to_ontology.json (code -> ontology classes/prefixes map)
│   │   ├── <CODE>.json               (integration XML converted to JSON)
│   │   ├── <CODE>_header.json        (codelist header payload)
│   │   └── <CODE>_items.json         (codelist items payload)
│   └── ttl/
│       └── <CODE>.ttl                (generated Turtle)
└── issues/
    ├── warnings.json
    ├── errors.json
    └── http_5xx.json
```

## Pipeline overview

### 1. Build ontology enrichment data

Before processing codelists, the pipeline reads local ontology RDF files from `data/rdf/`, converts them to JSON-LD, and builds `data/json/codelist_to_ontology.json`.

By default, it processes these ontologies:

- `egov`
- `fin`
- `pper`
- `leg`
- `loca`
- `lsub`

This mapping is later used to enrich codelist items with domain-specific ontology classes and the prefix declarations needed in the final Turtle.

### 2. Fetch the global codelist catalog

The pipeline downloads the full list of codelist headers from the MetaIS codelist header endpoint. These rows may contain duplicate entries for the same `code`, so the pipeline groups them by codelist code and chooses one canonical header row per code.

### 3. Resolve duplicate codelist headers

When multiple header rows exist for the same codelist code, they are ranked and one best candidate is selected.

Header ranking order:

1. **state** — `PUBLISHED` > `READY_TO_PUBLISH` > `ISVS_PROCESSING` > `UPDATING` > anything else
2. **effectiveTo** — open-ended rows (`None`) win; otherwise rows valid further into the future win
3. **validFrom** — more recent rows win
4. **temporal** — non-temporal (`False`) rows win over temporal (`True`) rows (temporal=`príznak, či je číselník v časovej verzii`)
5. **id** — larger ID wins as a final tie-breaker

Duplicate header groups are also classified as:

- **shadow** — one canonical published non-temporal current row plus weaker variants
- **conflict** — multiple rows look canonical at the same time

### 4. Process each selected codelist

For each canonical `(code, id)` pair, the pipeline downloads three source payloads:

1. **Integration XML**  
   `api/integration/read/codelist/{code}`
2. **Header JSON**  
   `api/codelist-repo/codelists/codelistheaders/{id}`
3. **Items JSON**  
   `api/codelist-repo/codelists/codelistheaders/{code}/codelistitems?language=sk&pageNumber=1&perPage=10000&sortBy=itemCode&ascending=true&lang=sk`

These payloads are written to disk under `data/xml/` and `data/json/` so the run leaves behind a local record of the source data.

### 5. Tolerate integration endpoint failures

The integration XML endpoint is useful, but it is not required for the rest of the conversion to continue.

If the integration endpoint returns a non-OK response, the pipeline:

- records the failure in the result object,
- records HTTP 5xx responses separately,
- emits a warning,
- continues using header JSON and items JSON.

So an integration endpoint failure does **not** necessarily abort TTL generation.

### 6. Deduplicate item rows inside a codelist

The items payload may contain multiple rows with the same `itemCode`. The pipeline groups those rows, ranks them, and selects one best row per item code.

Item ranking prefers:

1. published rows,
2. non-temporal rows,
3. better state ranking,
4. open-ended validity (`effectiveTo is None`),
5. newer timestamps,
6. larger IDs.

Duplicate item groups are classified as:

- **shadow** — one canonical item plus weaker variants
- **conflict** — multiple current published non-temporal rows

Conflicts are recorded as warnings in the per-codelist result.

### 7. Extract labels, notes, dates, and metadata

For both codelists and items, the pipeline picks the current/latest multilingual values and converts them into Turtle-ready literals.

It also extracts or computes:

- `validFrom`
- `effectiveFrom`
- `effectiveTo`
- codelist version date
- a rough `last_modified` date from item timestamps

These values are later used when rendering dataset metadata and item validity blocks.

### 8. Resolve the publisher ICO

From the selected codelist header, the pipeline looks at the current manager relation, extracts the owner UUID, fetches the corresponding CI from MetaIS, and tries to read `EA_Profil_PO_ico`.

If successful, that ICO is emitted as the `dct:publisher` of the generated codelist version.

If not, conversion still continues, but the missing publisher is noted as a warning.

### 9. Infer the canonical URI pattern for items

The pipeline inspects item URIs, normalizes them, extracts their base paths, and tries to infer the most plausible canonical URI base for the codelist.

The selection prefers common `data.gov.sk` URI families and also takes frequency into account. If multiple bases appear, the pipeline warns but still chooses the best candidate.

### 10. Audit suspicious item URIs

Once a canonical URI pattern has been inferred, the pipeline compares each item’s actual URI with the expected canonical URI.

It warns about cases such as:

- missing item URI tails,
- mismatched tails,
- mismatched bases,
- general URI mismatches.

To decide which URI looks more trustworthy, the pipeline can dereference both the current URI and the expected URI and inspect the rendered RDF page for labels and titles.

Dereference HTTP 5xx responses are also recorded and later included in `issues/http_5xx.json`.

### 11. Attach ontology types and prefixes

If the codelist code appears in `codelist_to_ontology.json`, the pipeline enriches item typing with the mapped ontology classes.

That means items are not emitted only as `skos:Concept`, but also as one or more ontology-specific classes, with the corresponding prefix declarations added to the Turtle header.

### 12. Render Turtle

The final Turtle file contains three main pieces:

1. **codelist dataset block**
2. **current version block**
3. **item concept blocks**

The generated TTL includes, as available:

- multilingual labels,
- notes,
- issued / modified dates,
- time validity,
- URI pattern,
- publisher,
- scheme membership,
- ontology typing.

Files are written under:

```text
data/ttl/<CODE>.ttl
```

### 13. Write final reports

Throughout the run, each codelist accumulates structured status information:

- warnings,
- hard errors,
- integration endpoint status,
- request URLs and response metadata,
- duplicate item issues,
- recorded HTTP 5xx failures.

At the end of the run, this is aggregated into:

- `issues/warnings.json`
- `issues/errors.json`
- `issues/http_5xx.json`

These reports make it easy to inspect what succeeded, what looked suspicious, and what failed badly without re-reading terminal output.

## Running the pipeline

Run the pipeline like this:

```bash
python main.py
```

This will:

1. ensure the expected output directories exist,
2. build ontology JSON-LD files and the codelist-to-ontology mapping,
3. fetch canonical codelist headers,
4. convert each selected codelist to Turtle,
5. write the final issue reports.

## Python dependencies

- `requests`
- `xmltodict`
- `rdflib`

## Notes and behavior

- pipeline is intentionally **fault-tolerant**: some endpoint failures are recorded but do not automatically stop the run
- duplicate rows are not silently ignored (selected based on ranking, reported)
- URI auditing is opinionated: it will choose a canonical pattern even when the source data is inconsistent, and it records those inconsistencies as warnings
- output is designed to be **human-inspectable** and **machine-auditable**

## Current patched behavior

This version keeps the modular graph-based pipeline, but adds the config-driven behavior used by the standalone converter.

### Project-root-safe paths

`main.py` can now be launched from any working directory. Paths are resolved relative to the directory containing `main.py` unless explicitly overridden.

Default paths:

```text
input/*.rdf                 ontology RDF inputs
data/json/*.json            cached JSON payloads + normalized payloads
data/xml/*.xml              integration XML payloads
data/ttl/*.ttl              generated Turtle files
issues/*.json               audit and issue reports
config/config.json          hierarchy / ignore / relation predicate config
```

### JSON caching

Header and item JSON files are reused when they are younger than one day by default. Older or missing JSON files are fetched again.

```bash
python main.py --cache-max-age-seconds 86400
```

The integration XML endpoint is still fetched during processing so HTTP 500 failures can be reported for contractor tickets.

### Config-driven hierarchy and ignore list

The default config path is:

```text
config/config.json
```

You can provide another config file:

```bash
python main.py --config /path/to/config.json
```

Supported keys:

```json
{
  "hierarchy": {
    "CHILD_CODE": "PARENT_CODE_OR_EXTERNAL_URI_BASE"
  },
  "relationPredicates": {
    "codelistItemIncludes": {
      "predicate": "skos:broader",
      "inversePredicate": "skos:narrower"
    },
    "codelistItemIncludesAlso": {
      "predicate": "skos:related"
    },
    "codelistItemExcludes": {
      "predicate": "egov:excludes",
      "inversePredicate": "egov:isExcludedBy"
    }
  },
  "externalRelationPredicates": {
    "https://data.gov.sk/id/legal-subject/": {
      "codelistItemIncludes": "skos:broader",
      "codelistItemIncludesAlso": "skos:related",
      "codelistItemExcludes": "egov:excludes"
    }
  },
  "ignore": {
    "CODE": {"reason": "handled elsewhere"}
  }
}
```

`relationPredicates` controls internal codelist-to-codelist relations. The forward predicate is emitted from source item to target item; the inverse predicate is emitted when the target item is present in the generated graph. The config may use either the canonical internal keys (`includes`, `includesAlso`, `excludes`) or the original MetaIS field names (`codelistItemIncludes`, `codelistItemIncludesAlso`, `codelistItemExcludes`). The compact flat form is also accepted, e.g. `"includesPredicate": "skos:broader"`, `"codelistItemIncludesPredicate": "skos:broader"`, and `"includesPredicateInverse": "skos:narrower"`.

Ignored codelists are still fetched and normalized for analysis, so they can serve as configured parents, but TTL files are not emitted for them.

### External URI hierarchy targets

If a hierarchy parent is an external URI base, e.g.

```json
{
  "hierarchy": {
    "CL000644": "https://data.gov.sk/id/legal-subject/"
  }
}
```

then an item include value like `35683813` emits:

```turtle
skos:broader <https://data.gov.sk/id/legal-subject/35683813> ;
```

The external predicate is configurable through `externalRelationPredicates`. Prefer the nested relation-specific form so only the intended relation key is emitted:

```json
{
  "externalRelationPredicates": {
    "https://data.gov.sk/id/legal-subject/": {
      "codelistItemIncludes": "skos:broader"
    }
  }
}
```

Supported predicate shorthands include any QName using the bound prefixes `skos`, `dct`, `dcat`, `prov`, `rdfs`, `egov`, `pper`, `leg`, `fin`, `lsub`, and `loca`, plus full HTTP(S) predicate URIs. That means project-specific predicates such as `egov:excludes` can be configured without changing Python code.

### URI-in-itemCode recovery

The pipeline now recovers MetaIS rows where the item URI was accidentally stored in `itemCode` while `itemUri` is null, for example:

```json
{
  "itemCode": "http://eidas.europa.eu/LoA/high",
  "itemUri": null
}
```

This is normalized internally as:

```json
{
  "itemCode": "high",
  "itemUri": "http://eidas.europa.eu/LoA/high"
}
```

The conversion result records a warning with the number of recovered rows.

### `codelist_to_ontology.json`

This file is generated automatically from the ontology RDF files in `input/*.rdf`. It maps codelist codes to ontology classes whose `dct:source` points at that codelist. If no ontology classes reference a particular codelist, conversion still works; the items are emitted as plain `skos:Concept` values.
