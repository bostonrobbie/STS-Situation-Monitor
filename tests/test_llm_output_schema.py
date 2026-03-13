import json
from pathlib import Path


def test_local_llm_output_schema_has_required_statuses() -> None:
    schema_path = Path('docs/local-llm-output-schema.json')
    schema = json.loads(schema_path.read_text())

    statuses = schema["$defs"]["claim"]["properties"]["status"]["enum"]
    assert statuses == ["supported", "disputed", "unknown", "monitor-next"]


def test_local_llm_output_schema_requires_evidence_references() -> None:
    schema = json.loads(Path('docs/local-llm-output-schema.json').read_text())

    required = set(schema["$defs"]["evidenceRef"]["required"])
    assert {"source", "url", "captured_at"}.issubset(required)
