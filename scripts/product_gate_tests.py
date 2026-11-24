"""Isolated tests for the product PR gate.

The positive path uses an ephemeral test key created inside a temporary
directory.  It is never a Founder key, never committed, and never accepted
as evidence for a real handoff.  The test proves only the cryptographic and
binding mechanics of the gate.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from product_pr_gate import artifact_digest
from product_lifecycle_gate import STAGES
from verify_founder_attestation import canonical_bytes


def write_state(root: Path, stage: str, product_id: str = "E2E-TEST-ONLY") -> None:
    stages = {}
    for item in STAGES:
        stages[item] = {"status": "ACCEPTED", "required_artifacts": []}
    stages[stage] = {"status": "IN_PROGRESS", "required_artifacts": ["docs/product/IMPLEMENTATION_PLAN.md"]}
    state = {
        "schema_version": "1.1",
        "source_of_truth": "HANDOFF.md",
        "projection_purpose": "mechanical_checks_only",
        "product_id": product_id,
        "repository": "avshih/e2e-test-only",
        "lifecycle": {"stage_order": list(stages), "current_stage": stage, "stages": stages},
        "stages": {stage: {"traceability_ref": "docs/product/REQUIREMENTS_TRACEABILITY.md"}},
    }
    (root / "docs/product").mkdir(parents=True, exist_ok=True)
    (root / "docs/product/STAGE_GATE_STATE.json").write_text(json.dumps(state), encoding="utf-8")
    (root / "docs/product/IMPLEMENTATION_PLAN.md").write_text("test-only", encoding="utf-8")
    (root / "docs/product/REQUIREMENTS_TRACEABILITY.md").write_text("test-only", encoding="utf-8")
    (root / "HANDOFF.md").write_text(f"Product ID: {product_id}\nStage: {stage}\n", encoding="utf-8")
    (root / "PRODUCT.md").write_text("test-only", encoding="utf-8")


def sign_attestation(root: Path, candidate: Path, head_sha: str, action: str, stage: str, product_id: str = "E2E-TEST-ONLY") -> None:
    authority = root / "authority"
    authority.mkdir(parents=True, exist_ok=True)
    private = authority / "private.pem"
    public = authority / "public.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(private), "2048"], check=True, capture_output=True)
    subprocess.run(["openssl", "rsa", "-in", str(private), "-pubout", "-out", str(public)], check=True, capture_output=True)
    attestation = {
        "schema_version": "1.0", "authority": "FOUNDER", "product_id": product_id,
        "repository": "avshih/e2e-test-only", "action": action, "stage": stage,
        "head_sha": head_sha, "artifact_digest": artifact_digest(json.loads((candidate / "docs/product/STAGE_GATE_STATE.json").read_text()), candidate),
        "issued_at": datetime.now(timezone.utc).isoformat(), "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
    attestation_path = candidate / "docs/product/FOUNDER_ATTESTATION.json"
    signature_path = candidate / "docs/product/FOUNDER_ATTESTATION.sig"
    attestation_path.write_text(json.dumps(attestation, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    payload = candidate / "docs/product/payload.json"
    payload.write_bytes(canonical_bytes(attestation))
    raw_sig = candidate / "docs/product/signature.bin"
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(private), "-out", str(raw_sig), str(payload)], check=True, capture_output=True)
    signature_path.write_text(base64.b64encode(raw_sig.read_bytes()).decode("ascii"), encoding="ascii")
    (root / "trusted-public.pem").write_bytes(public.read_bytes())
    (root / "trusted-public.sha256").write_text(hashlib.sha256(public.read_bytes()).hexdigest(), encoding="ascii")


def run_gate(base: Path, candidate: Path, changed: list[str], head: str, attestation: Path, signature: Path, key: Path, fingerprint: Path) -> tuple[int, str]:
    changed_file = base / "changed.txt"
    changed_file.write_text("\n".join(changed) + "\n", encoding="utf-8")
    state = candidate / "docs/product/STAGE_GATE_STATE.json"
    result = subprocess.run([
        sys.executable, str(Path(__file__).with_name("product_pr_gate.py")),
        "--base-root", str(base), "--candidate-root", str(candidate),
        "--base-state", str(base / "docs/product/STAGE_GATE_STATE.json"), "--candidate-state", str(state),
        "--changed-files", str(changed_file), "--product-id", "E2E-TEST-ONLY",
        "--repository", "avshih/e2e-test-only", "--head-sha", head,
        "--attestation", str(attestation), "--signature", str(signature),
        "--trusted-public-key", str(key), "--trusted-key-sha256-file", str(fingerprint),
    ], capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def main() -> int:
    results = []
    with tempfile.TemporaryDirectory(prefix="product_gate_test_") as folder:
        root = Path(folder)
        base = root / "base"
        candidate = root / "candidate"
        base.mkdir(); candidate.mkdir()
        write_state(base, "WIREFRAMES")
        write_state(candidate, "WIREFRAMES")
        code, output = run_gate(base, candidate, ["app/MainScreen.kt"], "head-early", candidate / "missing.json", candidate / "missing.sig", root / "missing.pem", root / "missing.sha")
        results.append(("UI_BEFORE_APPROVAL", code != 0 and "blocked before" in output))

        write_state(base, "DEVELOPMENT")
        write_state(candidate, "DEVELOPMENT")
        if shutil.which("openssl"):
            sign_attestation(root, candidate, "head-valid", "implementation", "DEVELOPMENT")
            code, output = run_gate(base, candidate, ["app/MainScreen.kt"], "head-valid", candidate / "docs/product/FOUNDER_ATTESTATION.json", candidate / "docs/product/FOUNDER_ATTESTATION.sig", root / "trusted-public.pem", root / "trusted-public.sha256")
            results.append(("SIGNED_POSITIVE_CONTROL", code == 0 and '"result": "PASS"' in output))
        else:
            results.append(("SIGNED_POSITIVE_CONTROL", None))

        fake = candidate / "docs/product/fake.json"
        fake.write_text(json.dumps({"authority": "FOUNDER", "product_id": "E2E-TEST-ONLY"}), encoding="utf-8")
        code, output = run_gate(base, candidate, ["app/MainScreen.kt"], "head-valid", fake, candidate / "missing.sig", root / "trusted-public.pem", root / "trusted-public.sha256")
        results.append(("FAKE_JSON_REJECTED", code != 0 and "signature is missing" in output))

        if shutil.which("openssl"):
            sign_attestation(root, candidate, "head-wrong", "implementation", "DEVELOPMENT", product_id="OTHER-PRODUCT")
            code, output = run_gate(base, candidate, ["app/MainScreen.kt"], "head-valid", candidate / "docs/product/FOUNDER_ATTESTATION.json", candidate / "docs/product/FOUNDER_ATTESTATION.sig", root / "trusted-public.pem", root / "trusted-public.sha256")
            results.append(("WRONG_PRODUCT_REJECTED", code != 0 and "different Product ID" in output))
        else:
            results.append(("WRONG_PRODUCT_REJECTED", None))

        code, output = run_gate(base, candidate, [".github/workflows/product-governance-gate.yml"], "head-control", candidate / "missing.json", candidate / "missing.sig", root / "missing.pem", root / "missing.sha")
        results.append(("CONTROL_PLANE_REJECTED", code != 0 and bool(output)))

        write_state(candidate, "DEVELOPMENT")
        code, output = run_gate(base, candidate, ["docs/product/STAGE_GATE_STATE.json"], "head-transition", candidate / "missing.json", candidate / "missing.sig", root / "missing.pem", root / "missing.sha")
        results.append(("STATE_ONLY_TRANSITION_REJECTED", code != 0 and "Founder attestation is missing" in output))
    for name, passed in results:
        status = "NOT_TESTED" if passed is None else ("PASS" if passed else "FAIL")
        print(json.dumps({"test_id": name, "status": status, "expected": "reject or allow as named", "test_authority": "EPHEMERAL_TEST_ONLY"}, ensure_ascii=False))
    return 0 if all(passed is True for _, passed in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
