"""Fail-closed PR gate for product lifecycle and UI changes.

This script is executed from the trusted base revision by the product
workflow.  The candidate tree is read-only input.  It deliberately treats
unclassified application source and control-plane changes as sensitive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from product_lifecycle_gate import APPROVED_STATUSES, STAGES, check as lifecycle_check
from verify_founder_attestation import validate as validate_attestation


UI_EXTENSIONS = {
    ".css", ".scss", ".less", ".html", ".htm", ".tsx", ".jsx", ".vue", ".svelte",
    ".kt", ".swift", ".dart", ".storyboard", ".xib", ".xml", ".fig", ".svg", ".png", ".jpg", ".jpeg", ".webp",
}
SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".mjs", ".c", ".cc", ".cpp", ".h", ".java", ".cs", ".go", ".rs", ".rb", ".php"}
NON_UI_PREFIXES = ("backend/", "server/", "api/", "infra/", "infrastructure/", "database/", "migrations/", "tests/", "test/")
CONTROL_PATHS = {
    "handoff.md", "product.md", "development.md", "agents.md",
    "docs/product/stage_gate_state.json",
    "product-enforcement-manifest.json",
}
CONTROL_PREFIXES = (".github/workflows/", ".github/founder-authority/", "scripts/", "docs/governance/")
DESIGN_STAGE_IDS = ("UX_RESEARCH_IA", "WIREFRAMES", "DESIGN_PREFERENCES", "VISUAL_DIRECTION", "HIGH_FIDELITY_UI", "PROTOTYPE")


def classify(path: str) -> str:
    normalized = path.replace("\\", "/").lower().lstrip("./")
    if normalized in CONTROL_PATHS or normalized.startswith(CONTROL_PREFIXES):
        return "CONTROL_PLANE"
    suffix = Path(normalized).suffix
    if suffix in UI_EXTENSIONS:
        return "UI"
    if suffix in SOURCE_EXTENSIONS and not normalized.startswith(NON_UI_PREFIXES):
        return "UNCLASSIFIED_SOURCE"
    if normalized.startswith(("design/", "assets/", "res/", "resources/", "screens/", "components/", "views/", "navigation/")):
        return "UNCLASSIFIED_SOURCE"
    if normalized.startswith(("docs/", ".github/")):
        return "DOCUMENTATION"
    if normalized.startswith(NON_UI_PREFIXES):
        return "NON_UI"
    if suffix in {".md", ".txt", ".rst", ".adoc"} or normalized in {"readme", "license", "license.md", "changelog.md"}:
        return "DOCUMENTATION"
    # Unknown extensions, configuration and data files fail closed.  They may
    # influence UI state or the gate and must not silently bypass approval.
    return "UNCLASSIFIED_SOURCE"


def changed_paths(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    result = []
    for line in lines:
        value = line.strip()
        if not value:
            continue
        if "\t" in value:
            value = value.split("\t", 1)[1]
        result.append(value)
    return result


def artifact_digest(state: dict, root: Path) -> str:
    paths: set[str] = {"HANDOFF.md", "PRODUCT.md"}
    lifecycle = state.get("lifecycle", {})
    current = lifecycle.get("current_stage")
    stage = lifecycle.get("stages", {}).get(current, {})
    for item in stage.get("required_artifacts", []):
        paths.add(item.get("path") if isinstance(item, dict) else item)
    traceability = state.get("stages", {}).get(current, {}).get("traceability_ref")
    if traceability:
        paths.add(traceability)
    records = []
    for value in sorted(path for path in paths if isinstance(path, str)):
        candidate = root / value
        if candidate.is_file():
            records.append(f"{value}:{hashlib.sha256(candidate.read_bytes()).hexdigest()}")
        elif candidate.is_dir():
            for child in sorted(item for item in candidate.rglob("*") if item.is_file()):
                rel = child.relative_to(root).as_posix()
                records.append(f"{rel}:{hashlib.sha256(child.read_bytes()).hexdigest()}")
        else:
            records.append(f"{value}:MISSING")
    return hashlib.sha256(("\n".join(records) + "\n").encode("utf-8")).hexdigest()


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def state_errors(state: dict, handoff: str, product_id: str, repository: str) -> list[str]:
    errors: list[str] = []
    lifecycle = state.get("lifecycle", {})
    current = lifecycle.get("current_stage")
    if state.get("source_of_truth") != "HANDOFF.md":
        errors.append("Gate State source_of_truth must be HANDOFF.md")
    if state.get("projection_purpose") != "mechanical_checks_only":
        errors.append("Gate State must remain a mechanical projection")
    if state.get("product_id") and state.get("product_id") != product_id:
        errors.append("Gate State Product ID does not match the requested product")
    if state.get("repository") and state.get("repository") != repository:
        errors.append("Gate State repository does not match the requested repository")
    if current not in STAGES:
        errors.append("Gate State current stage is unknown")
        return errors
    if f"{current}" not in handoff:
        errors.append("HANDOFF does not identify the current lifecycle stage")
    for prior in STAGES[: STAGES.index(current)]:
        if lifecycle.get("stages", {}).get(prior, {}).get("status") not in APPROVED_STATUSES:
            errors.append(f"prior stage is not approved: {prior}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-root", required=True, type=Path)
    parser.add_argument("--candidate-root", required=True, type=Path)
    parser.add_argument("--base-state", required=True, type=Path)
    parser.add_argument("--candidate-state", required=True, type=Path)
    parser.add_argument("--changed-files", required=True, type=Path)
    parser.add_argument("--product-id", default="")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--attestation", required=True, type=Path)
    parser.add_argument("--signature", required=True, type=Path)
    parser.add_argument("--trusted-public-key", required=True, type=Path)
    parser.add_argument("--trusted-key-sha256-file", required=True, type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    try:
        paths = changed_paths(args.changed_files)
        candidate_state = load_state(args.candidate_state)
        base_state = load_state(args.base_state)
        handoff = (args.candidate_root / "HANDOFF.md").read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "FAIL", "check_type": "EXECUTABLE_CHECK", "errors": [str(exc)]}, ensure_ascii=False))
        return 2

    product_id = args.product_id or candidate_state.get("product_id") or ""
    if not product_id:
        errors.append("Product ID is missing from the candidate Gate State")
    classifications = {path: classify(path) for path in paths}
    sensitive = {kind for kind in classifications.values() if kind in {"UI", "UNCLASSIFIED_SOURCE", "CONTROL_PLANE"}}
    errors.extend(state_errors(candidate_state, handoff, product_id, args.repository))
    current = candidate_state.get("lifecycle", {}).get("current_stage")
    base_current = base_state.get("lifecycle", {}).get("current_stage")
    if any(kind == "CONTROL_PLANE" for kind in classifications.values()):
        expected_action = "control-plane-change"
    elif current != base_current:
        expected_action = "stage-transition"
    elif sensitive:
        expected_action = "implementation"
    else:
        expected_action = "documentation"

    if expected_action != "documentation":
        if current not in STAGES:
            errors.append("sensitive change has no valid current stage")
        if any(kind in {"UI", "UNCLASSIFIED_SOURCE"} for kind in classifications.values()) and current in STAGES and STAGES.index(current) < STAGES.index("DEVELOPMENT"):
            errors.append("UI or application implementation is blocked before the DEVELOPMENT stage")
        try:
            trusted_key_sha256 = args.trusted_key_sha256_file.read_text(encoding="ascii").strip()
        except OSError as exc:
            errors.append(f"trusted Founder key fingerprint is unavailable: {exc}")
            trusted_key_sha256 = ""
        digest = artifact_digest(candidate_state, args.candidate_root)
        errors.extend(validate_attestation(
            args.attestation,
            args.signature,
            args.trusted_public_key,
            trusted_key_sha256,
            expected_product_id=product_id,
            expected_repository=args.repository,
            expected_action=expected_action,
            expected_stage=current or "UNKNOWN",
            expected_head_sha=args.head_sha,
            expected_artifact_digest=digest,
        ))
        approval = {
            "status": "VERIFIED" if not errors else "FAILED",
            "source": "founder_signed_attestation",
            "stage": current,
            "product_id": product_id,
            "repository": args.repository,
            "head_sha": args.head_sha,
        }
        allowed, lifecycle_errors = lifecycle_check(candidate_state, args.candidate_root, "stage-transition" if expected_action == "stage-transition" else "implementation", approval)
        if not allowed:
            errors.extend(lifecycle_errors)

    result = {
        "result": "PASS" if not errors else "FAIL",
        "check_type": "EXECUTABLE_CHECK",
        "enforcement_claim": "EXECUTABLE_CHECK_ONLY",
        "action": expected_action,
        "changed_files": classifications,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
