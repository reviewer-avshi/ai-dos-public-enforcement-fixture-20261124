"""Verify a signed Founder authority attestation.

GitHub Code Review and Founder authority are separate controls.  This module
verifies a detached RSA/SHA-256 signature made by a private key held outside
the repository.  The public key must be supplied from the trusted base
revision, never from the untrusted PR candidate.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


REQUIRED = {
    "schema_version",
    "authority",
    "product_id",
    "repository",
    "action",
    "stage",
    "head_sha",
    "artifact_digest",
    "issued_at",
    "expires_at",
}


def canonical_bytes(attestation: dict) -> bytes:
    return (json.dumps(attestation, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def _run_openssl(public_key: Path, signature: bytes, payload: bytes) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="founder_attestation_") as folder:
        root = Path(folder)
        signature_path = root / "signature.bin"
        payload_path = root / "payload.json"
        signature_path.write_bytes(signature)
        payload_path.write_bytes(payload)
        try:
            result = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(public_key),
                    "-signature",
                    str(signature_path),
                    str(payload_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"openssl verification unavailable: {exc}"
    if result.returncode != 0 or result.stdout.strip().lower() != "verified ok":
        return False, (result.stderr or result.stdout or "signature verification failed").strip()
    return True, "signature verified"


def validate(
    attestation_path: Path,
    signature_path: Path,
    trusted_public_key: Path,
    trusted_key_sha256: str,
    *,
    expected_product_id: str,
    expected_repository: str,
    expected_action: str,
    expected_stage: str,
    expected_head_sha: str,
    expected_artifact_digest: str,
) -> list[str]:
    errors: list[str] = []
    if not attestation_path.is_file():
        return [f"Founder attestation is missing: {attestation_path}"]
    if not signature_path.is_file():
        return [f"Founder attestation signature is missing: {signature_path}"]
    if not trusted_public_key.is_file():
        return [f"trusted Founder public key is missing: {trusted_public_key}"]
    try:
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Founder attestation is invalid: {exc}"]
    if not isinstance(attestation, dict):
        return ["Founder attestation must be an object"]

    missing = sorted(REQUIRED - set(attestation))
    errors.extend(f"Founder attestation is missing: {field}" for field in missing)
    if attestation.get("schema_version") != "1.0":
        errors.append("unsupported Founder attestation schema")
    if attestation.get("authority") != "FOUNDER":
        errors.append("attestation authority is not FOUNDER")
    if attestation.get("product_id") != expected_product_id:
        errors.append("Founder attestation is for a different Product ID")
    if attestation.get("repository") != expected_repository:
        errors.append("Founder attestation is for a different repository")
    if attestation.get("action") != expected_action:
        errors.append("Founder attestation action does not match the requested operation")
    if attestation.get("stage") != expected_stage:
        errors.append("Founder attestation stage does not match the candidate state")
    if attestation.get("head_sha") != expected_head_sha:
        errors.append("Founder attestation is not bound to the current PR head")
    if attestation.get("artifact_digest") != expected_artifact_digest:
        errors.append("Founder attestation is not bound to the required artifact digest")
    try:
        now = dt.datetime.now(dt.timezone.utc)
        if parse_time(str(attestation.get("issued_at"))) > now + dt.timedelta(minutes=5):
            errors.append("Founder attestation is issued in the future")
        if parse_time(str(attestation.get("expires_at"))) <= now:
            errors.append("Founder attestation is expired")
    except (TypeError, ValueError) as exc:
        errors.append(f"Founder attestation time is invalid: {exc}")

    actual_key_hash = hashlib.sha256(trusted_public_key.read_bytes()).hexdigest()
    if actual_key_hash != trusted_key_sha256.strip().lower():
        errors.append("trusted Founder public-key fingerprint does not match the base revision")
    try:
        signature = base64.b64decode(signature_path.read_text(encoding="ascii"), validate=True)
    except (OSError, ValueError) as exc:
        errors.append(f"Founder signature is not valid base64: {exc}")
        signature = b""
    if signature:
        verified, message = _run_openssl(trusted_public_key, signature, canonical_bytes(attestation))
        if not verified:
            errors.append(message)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attestation", required=True, type=Path)
    parser.add_argument("--signature", required=True, type=Path)
    parser.add_argument("--trusted-public-key", required=True, type=Path)
    parser.add_argument("--trusted-key-sha256", required=True)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--artifact-digest", required=True)
    args = parser.parse_args()
    errors = validate(
        args.attestation,
        args.signature,
        args.trusted_public_key,
        args.trusted_key_sha256,
        expected_product_id=args.product_id,
        expected_repository=args.repository,
        expected_action=args.action,
        expected_stage=args.stage,
        expected_head_sha=args.head_sha,
        expected_artifact_digest=args.artifact_digest,
    )
    print(json.dumps({"result": "PASS" if not errors else "FAIL", "check_type": "EXECUTABLE_CHECK", "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
