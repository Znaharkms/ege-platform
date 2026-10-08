from pathlib import Path

import yaml


def test_contract_has_unique_operations_and_valid_local_refs() -> None:
    contract_path = Path(__file__).resolve().parents[2] / "api" / "openapi.yaml"
    document = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    operation_ids: set[str] = set()
    references: list[str] = []
    methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}

    def collect(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref":
                    references.append(value)
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(document)
    for path_item in document["paths"].values():
        for method, operation in path_item.items():
            if method not in methods:
                continue
            operation_id = operation["operationId"]
            assert operation_id not in operation_ids
            operation_ids.add(operation_id)
            assert operation["responses"]

    for reference in references:
        if not reference.startswith("#/"):
            continue
        node = document
        for part in reference[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]

    assert len(operation_ids) == 89


def test_every_admin_operation_requires_admin_role() -> None:
    contract_path = Path(__file__).resolve().parents[2] / "api" / "openapi.yaml"
    document = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    methods = {"get", "post", "put", "patch", "delete"}
    for path, path_item in document["paths"].items():
        if not path.startswith("/admin/"):
            continue
        for method, operation in path_item.items():
            if method not in methods:
                continue
            assert operation["x-required-role"] == "admin"
            assert {"BearerAuth": []} in operation["security"]
