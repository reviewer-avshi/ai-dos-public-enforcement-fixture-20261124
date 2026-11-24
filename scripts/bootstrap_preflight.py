"""Fail-closed validation for a product-enforcement installation bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


SUPPORTED_SCHEMA = "1.0"


def sha256(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in {".py", ".yml", ".yaml", ".md", ".json", ".txt"}:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def load_json(path: Path, label: str, errors: list[str]) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is unreadable or invalid: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label} must be a JSON object")
        return {}
    return value


def workflow_python_references(workflow: Path) -> set[str]:
    text = workflow.read_text(encoding="utf-8")
    return set(re.findall(r"(?<![A-Za-z0-9_/.])(scripts/[A-Za-z0-9_.-]+\.py)", text))


def compile_python(path: Path, errors: list[str]) -> None:
    result = subprocess.run([sys.executable, "-m", "py_compile", str(path)], capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        errors.append(f"Python file does not compile: {path.as_posix()} ({detail})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a product enforcement bundle")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("product-enforcement-manifest.json"))
    parser.add_argument("--state", type=Path, default=Path("docs/product/STAGE_GATE_STATE.json"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--product-id", default="")
    parser.add_argument("--expected-bundle-version", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    errors: list[str] = []
    manifest = load_json(root / args.manifest, "bundle manifest", errors)
    state = load_json(root / args.state, "Gate State", errors)
    if manifest.get("schema_version") != SUPPORTED_SCHEMA:
        errors.append(f"unsupported manifest schema: {manifest.get('schema_version')!r}")
    if manifest.get("bundle_version") != args.expected_bundle_version:
        errors.append(f"bundle version mismatch: manifest={manifest.get('bundle_version')!r}, expected={args.expected_bundle_version!r}")
    if manifest.get("source_of_truth") != "HANDOFF.md":
        errors.append("manifest source_of_truth must be HANDOFF.md")
    if manifest.get("state_path") != args.state.as_posix().replace("\\", "/"):
        errors.append("manifest state_path does not match the requested Gate State path")
    required = manifest.get("required_files")
    if not isinstance(required, list) or not required:
        errors.append("manifest required_files is missing or empty")
        required = []
    entries: dict[str, dict] = {}
    for item in required:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("manifest contains a malformed required_files entry")
            continue
        path = item["path"]
        if not safe_relative(path):
            errors.append(f"manifest path is unsafe: {path}")
            continue
        if path in entries:
            errors.append(f"manifest contains a duplicate path: {path}")
        entries[path] = item
        candidate = root / path
        if not candidate.is_file():
            errors.append(f"required bundle file is missing: {path}")
            continue
        kind = item.get("kind")
        if kind == "static":
            expected = str(item.get("sha256", "")).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                errors.append(f"static file has no valid SHA256 in manifest: {path}")
            elif sha256(candidate) != expected:
                errors.append(f"SHA256 mismatch: {path}")
            if candidate.suffix == ".py":
                compile_python(candidate, errors)
        elif kind not in {"generated", "manifest"}:
            errors.append(f"unsupported file kind {kind!r}: {path}")
    workflow_paths = manifest.get("workflow_paths", [])
    if not isinstance(workflow_paths, list) or not workflow_paths:
        errors.append("manifest workflow_paths is missing or empty")
        workflow_paths = []
    referenced: set[str] = set()
    for value in workflow_paths:
        if not isinstance(value, str) or not safe_relative(value):
            errors.append(f"unsafe workflow path: {value!r}")
            continue
        workflow = root / value
        if not workflow.is_file():
            errors.append(f"declared workflow is missing: {value}")
            continue
        referenced.update(workflow_python_references(workflow))
    for value in sorted(referenced):
        if value not in entries:
            errors.append(f"workflow references an unlisted script: {value}")
        if not (root / value).is_file():
            errors.append(f"workflow references a missing script: {value}")
    product_id = str(state.get("product_id", ""))
    if args.product_id and args.product_id != product_id:
        errors.append("Product ID argument does not match Gate State")
    if not product_id:
        errors.append("Product ID is missing from Gate State")
    if state.get("repository") != args.repository:
        errors.append(f"repository mismatch: Gate State={state.get('repository')!r}, expected={args.repository!r}")
    if state.get("source_of_truth") != "HANDOFF.md":
        errors.append("Gate State source_of_truth must be HANDOFF.md")
    if state.get("projection_purpose") != "mechanical_checks_only":
        errors.append("Gate State projection_purpose must be mechanical_checks_only")
    handoff = root / "HANDOFF.md"
    if not handoff.is_file():
        errors.append("HANDOFF.md is missing")
    else:
        handoff_text = handoff.read_text(encoding="utf-8")
        if f"Product ID: {product_id}" not in handoff_text:
            errors.append("HANDOFF Product ID does not match Gate State")
        current_stage = state.get("lifecycle", {}).get("current_stage")
        if current_stage and f"Stage: {current_stage}" not in handoff_text:
            errors.append("HANDOFF stage does not match Gate State")
    result = {"result": "PASS" if not errors else "FAIL", "check_type": "EXECUTABLE_CHECK", "enforcement_claim": "EXECUTABLE_CHECK_ONLY", "bundle_version": manifest.get("bundle_version"), "repository": args.repository, "errors": errors}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
