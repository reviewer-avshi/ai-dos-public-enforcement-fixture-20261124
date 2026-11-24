"""Portable product-repository handoff validator installed from the house template."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED = {"platform_configured", "product_chief_loaded", "bootstrap_completed", "fresh_conversation_passed", "founder_acceptance"}


def validate(evidence: dict, root: Path) -> list[str]:
    errors = []
    for field in ("status", "product_id", "repository", "branch", "head_sha", "platform", "checks", "founder_acceptance"):
        if field not in evidence:
            errors.append(f"missing field: {field}")
    if evidence.get("status") != "HANDOFF_VERIFIED":
        errors.append("status is not HANDOFF_VERIFIED")
    checks = evidence.get("checks") if isinstance(evidence.get("checks"), list) else []
    seen = set()
    for item in checks:
        if not isinstance(item, dict):
            errors.append("check is not an object")
            continue
        seen.add(item.get("id"))
        if item.get("status") != "PASS":
            errors.append(f"check {item.get('id')} is not PASS")
        ref = item.get("evidence_ref", "")
        if not ref or not (str(ref).startswith("https://") or (root / str(ref)).is_file()):
            errors.append(f"check {item.get('id')} has no usable evidence_ref")
    errors.extend(f"missing check: {item}" for item in sorted(REQUIRED - seen))
    acceptance = evidence.get("founder_acceptance")
    if not isinstance(acceptance, dict) or acceptance.get("status") != "EXTERNAL_VERIFIED" or not acceptance.get("approval_ref"):
        errors.append("Founder acceptance is not independent external evidence")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", required=True, type=Path)
    parser.add_argument("--acceptance-report", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    errors = []
    if not args.handoff.is_file() or "HANDOFF_VERIFIED" not in args.handoff.read_text(encoding="utf-8"):
        errors.append("HANDOFF is not verified")
    if not args.acceptance_report.is_file():
        errors.append("acceptance report is missing")
    if not args.evidence.is_file():
        errors.append("structured evidence is missing")
    else:
        try:
            errors.extend(validate(json.loads(args.evidence.read_text(encoding="utf-8")), args.evidence.parent))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
    print(json.dumps({"result": "PASS" if not errors else "FAIL", "check_type": "EXECUTABLE_CHECK", "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
