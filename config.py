from __future__ import annotations

BASE_URL = "https://metais.slovensko.sk"

CODELIST_XML_URL = BASE_URL + "/api/integration/read/codelist/{code}"
CODELIST_HEADER_URL = BASE_URL + "/api/codelist-repo/codelists/codelistheaders/{id}"
CODELIST_ITEMS_URL = (
    BASE_URL
    + "/api/codelist-repo/codelists/codelistheaders/{code}/codelistitems"
    + "?language=sk&pageNumber=1&perPage=10000&sortBy=itemCode&ascending=true&lang=sk"
)
CMDB_READ_CI_URL = BASE_URL + "/api/cmdb/read/ci/{uuid}"

CODELIST_HEADERS_URL = (
    BASE_URL
    + "/api/codelist-repo/codelists/codelistheaders"
    + "?language=sk&pageNumber=1&perPage=10000"
)

REQUEST_TIMEOUT = 60
DEREF_TIMEOUT = 20

DEFAULT_ONTOLOGIES = ["egov", "fin", "pper", "leg", "loca", "lsub"]

DEFAULT_LABELS = {
    "egov": "egov",
    "fin": "fin",
    "pper": "pper",
    "leg": "leg",
    "loca": "loca",
    "lsub": "lsub",
}

DEFAULT_PREFIXES = {
    "egov": "@prefix egov: <https://data.gov.sk/def/ontology/egov/>.",
    "fin": "@prefix fin: <https://data.gov.sk/def/ontology/finance/>.",
    "pper": "@prefix pper: <https://data.gov.sk/def/ontology/physical-person/>.",
    "leg": "@prefix leg: <https://data.gov.sk/def/ontology/legislation/>.",
    "loca": "@prefix loca: <https://data.gov.sk/def/ontology/location/>.",
    "lsub": "@prefix lsub: <https://data.gov.sk/def/ontology/legal-subject/>.",
}
