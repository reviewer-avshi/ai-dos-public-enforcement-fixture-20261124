"""Fail-closed lifecycle checker for UI-product stage progression.

This is an executable check, not by itself a physical enforcement claim.
A product repository must wire its result into required CI before it can
block a Merge. Founder approvals must come from independent evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from design_delivery_policy import validate_design_stage

STAGES = [
    "RECOVERY",
    "DISCOVERY",
    "SPECIFICATION",
    "UX_RESEARCH_IA",
    "WIREFRAMES",
    "DESIGN_PREFERENCES",
    "VISUAL_DIRECTION",
    "HIGH_FIDELITY_UI",
    "PROTOTYPE",
    "ARCHITECTURE",
    "WORK_PACKAGES",
    "DEVELOPMENT",
    "INTEGRATION_QA",
    "UAT",
    "RELEASE",
    "PRODUCTION_OPERATIONS",
]

DESIGN_STAGES = {
    "WIREFRAMES",
    "VISUAL_DIRECTION",
    "HIGH_FIDELITY_UI",
    "PROTOTYPE",
}

APPROVED_STATUSES = {"ACCEPTED", "FOUNDER_APPROVED", "N/A"}
ACTIVE_STATUSES = {"IN_PROGRESS", "FOUNDER_APPROVED", "VERIFIED", "ACCEPTED"}


def _artifact_items(stage: dict) -> list[dict]:
    items = []
    for item in stage.get("required_artifacts", []):
        items.append(item if isinstance(item, dict) else {"path": item, "kind": "file"})
    return items


def _valid_artifact(path: str, kind: str | None, root: Path) -> bool:
    candidate = root / path
    if not candidate.is_file():
        return False
    if kind in {"design_file", "prototype"}:
        return candidate.suffix.lower() in {".fig", ".json", ".png", ".jpg", ".jpeg", ".svg", ".html", ".pdf"}
    return True


def _check_na(stage_id: str, stage: dict, errors: list[str]) -> None:
    if stage.get("status") == "N/A":
        if not stage.get("na_reason"):
            errors.append(f"N/A stage {stage_id} has no written reason")
        approval = stage.get("founder_approval")
        if not isinstance(approval, dict) or approval.get("source") in {None, "state_file", "markdown", "json"}:
            errors.append(f"N/A stage {stage_id} has no independent Founder approval")


def check(state: dict, root: Path, action: str, approval: dict | None = None) -> tuple[bool, list[str]]:
    errors: list[str] = []
    lifecycle = state.get("lifecycle", {})
    declared = lifecycle.get("stage_order", STAGES)
    if declared != STAGES:
        errors.append("lifecycle stage order is incomplete or reordered")

    current = lifecycle.get("current_stage")
    stages = lifecycle.get("stages", {})
    if current not in STAGES or current not in stages:
        errors.append("current lifecycle stage is missing or unknown")
        return False, errors

    current_index = STAGES.index(current)
    for stage_id in STAGES[:current_index]:
        prior = stages.get(stage_id, {})
        _check_na(stage_id, prior, errors)
        if prior.get("status") not in APPROVED_STATUSES:
            errors.append(f"prior stage {stage_id} is not accepted")

    stage = stages[current]
    _check_na(current, stage, errors)

    for artifact in _artifact_items(stage):
        path = artifact.get("path")
        kind = artifact.get("kind")
        if not path or not _valid_artifact(path, kind, root):
            errors.append(f"required artifact is missing or wrong type: {path}")
        if current in DESIGN_STAGES and kind in {None, "text", "markdown"}:
            errors.append(f"design stage {current} requires a design artifact, not text only: {path}")

    if current in DESIGN_STAGES:
        if not stage.get("founder_gate") and stage.get("status") != "N/A":
            errors.append(f"design stage {current} has no explicit Founder gate")

    if current == "PROTOTYPE" and stage.get("status") != "N/A":
        manifest = stage.get("prototype_manifest")
        if not manifest or not (root / manifest).is_file():
            errors.append("interactive prototype manifest is missing")
        else:
            try:
                data = json.loads((root / manifest).read_text(encoding="utf-8"))
                interaction = data.get("interaction_test", {})
                if interaction.get("status") != "PASS" or not interaction.get("tested_paths"):
                    errors.append("interactive prototype was not tested successfully")
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"prototype manifest is invalid: {exc}")

    if current in DESIGN_STAGES and (state.get("design_policy_version") or stage.get("design_policy_version")) and stage.get("status") != "N/A":
        errors.extend(validate_design_stage(current, stage, root))

    if action in {"implementation", "stage-transition"}:
        if stage.get("status") not in ACTIVE_STATUSES:
            errors.append(f"current stage {current} is not active or accepted")
        if approval is None:
            errors.append("external Founder approval evidence is required")
        else:
            if approval.get("status") != "VERIFIED":
                errors.append("Founder approval evidence is not verified")
            if approval.get("source") in {None, "state_file", "markdown", "json"}:
                errors.append("Founder approval source is not independent")
            if approval.get("stage") != current:
                errors.append("Founder approval is for a different stage")
            if state.get("product_id") and approval.get("product_id") != state.get("product_id"):
                errors.append("Founder approval is for a different product")
            if state.get("repository") and approval.get("repository") != state.get("repository"):
                errors.append("Founder approval is for a different repository")
            if action == "stage-transition" and not approval.get("head_sha"):
                errors.append("stage-transition approval is not bound to a Git head")

    return not errors, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--action", choices=["planning", "implementation", "stage-transition"], required=True)
    parser.add_argument("--approval-evidence", type=Path)
    args = parser.parse_args()
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        approval = json.loads(args.approval_evidence.read_text(encoding="utf-8")) if args.approval_evidence else None
        allowed, errors = check(state, args.root.resolve(), args.action, approval)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "FAIL", "check_type": "EXECUTABLE_CHECK", "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    payload = {
        "result": "PASS" if allowed else "FAIL",
        "check_type": "EXECUTABLE_CHECK",
        "action": args.action,
        "errors": errors,
        "enforcement_claim": "EXECUTABLE_CHECK_ONLY",
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if allowed else 2


if __name__ == "__main__":
    sys.exit(main())
