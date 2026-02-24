import json
import os
from config import MANIFEST_PATH

def load_schema_context() -> str:
    manifest_path = os.path.abspath(os.path.join(os.path.dirname(__file__), MANIFEST_PATH))

    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    canonical_lines = []
    staging_lines = []
    nodes = manifest.get("nodes", {})

    for node_id, node in nodes.items():
        if node.get("resource_type") != "model":
            continue

        model_name = node.get("name")
        schema = node.get("schema", "")
        description = node.get("description", "")
        columns = node.get("columns", {})
        is_canonical = node.get("config", {}).get("meta", {}).get("canonical", False)

        lines = []
        label = "[CANONICAL] " if is_canonical else "[STAGING] "
        lines.append(f"Table: {label}{schema}.{model_name}")
        if description:
            lines.append(f"  Description: {description}")

        for col_name, col in columns.items():
            col_desc = col.get("description", "")
            col_type = col.get("data_type", "")
            col_line = f"  - {col_name}"
            if col_type:
                col_line += f" ({col_type})"
            if col_desc:
                col_line += f": {col_desc}"
            lines.append(col_line)

        lines.append("")

        if is_canonical:
            canonical_lines.extend(lines)
        else:
            staging_lines.extend(lines)

    # Canonical tables first, staging after
    all_lines = canonical_lines + staging_lines
    return "\n".join(all_lines)


if __name__ == "__main__":
    print(load_schema_context())