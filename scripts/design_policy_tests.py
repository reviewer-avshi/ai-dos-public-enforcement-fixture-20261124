"""Positive and negative tests for the dual-track design policy."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from design_delivery_policy import validate_design_stage


def make_local(root: Path) -> dict:
    (root / "design").mkdir(exist_ok=True)
    source = root / "design" / "screen.svg"
    render = root / "design" / "screen.png"
    source.write_text("<svg xmlns='http://www.w3.org/2000/svg'><rect width='10' height='10'/></svg>", encoding="utf-8")
    render.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    index = root / "design" / "screens.json"
    index.write_text(json.dumps({"screens": [{
        "id": "home", "version": "v1", "source": "design/screen.svg", "render": "design/screen.png",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "render_sha256": hashlib.sha256(render.read_bytes()).hexdigest(),
    }]}), encoding="utf-8")
    return {
        "status": "IN_PROGRESS", "design_route": "LOCAL_VERSIONED",
        "design_contract": {
            "policy_version": "1.0", "route": "LOCAL_VERSIONED",
            "source_paths": ["design/screen.svg"], "render_paths": ["design/screen.png"],
            "screen_index": "design/screens.json",
        },
    }


def run() -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory(prefix="design_policy_") as folder:
        root = Path(folder)
        valid = make_local(root)
        results.append(("LOCAL_VALID", not validate_design_stage("HIGH_FIDELITY_UI", valid, root)))
        missing = make_local(root)
        missing["design_contract"]["source_paths"] = ["design/missing.svg"]
        results.append(("LOCAL_MISSING_SOURCE", bool(validate_design_stage("HIGH_FIDELINE_UI", missing, root))))
        mismatch = make_local(root)
        data = json.loads((root / "design" / "screens.json").read_text(encoding="utf-8"))
        data["screens"][0]["render_sha256"] = "0" * 64
        (root / "design" / "screens.json").write_text(json.dumps(data), encoding="utf-8")
        results.append(("LOCAL_HASH_MISMATCH", bool(validate_design_stage("HIGH_FIDELITY_UI", mismatch, root))))
        fake = make_local(root)
        (root / "design" / "screen.svg").write_text("fixture-design", encoding="utf-8")
        results.append(("LOCAL_FAKE_SOURCE", bool(validate_design_stage("HIGH_FIDELITY_UI", fake, root))))
        empty = make_local(root)
        (root / "design" / "screen.svg").write_text("", encoding="utf-8")
        results.append(("LOCAL_EMPTY_SOURCE", bool(validate_design_stage("HIGH_FIDELITY_UI", empty, root))))
        figma_bad = {"status": "IN_PROGRESS", "design_route": "FIGMA_APPROVED", "design_contract": {"policy_version": "1.0", "route": "FIGMA_APPROVED", "figma_url": "Figma"}}
        results.append(("FIGMA_KEYWORD_ONLY", bool(validate_design_stage("VISUAL_DIRECTION", figma_bad, root))))
        figma_good = {"status": "IN_PROGRESS", "design_route": "FIGMA_APPROVED", "design_contract": {
            "policy_version": "1.0", "route": "FIGMA_APPROVED", "figma_url": "https://www.figma.com/design/abc/file",
            "file_key": "abc", "node_ids": ["1:2"], "version": "rev-1",
            "review_evidence": {"status": "VERIFIED", "source": "github_pull_request_review_api"},
            "reviewed_head_sha": "fixture-head",
        }}
        results.append(("FIGMA_APPROVED_VALID", not validate_design_stage("VISUAL_DIRECTION", figma_good, root)))
    return [{"test_id": name, "status": "PASS", "expected": "PASS", "observed": "PASS"} if ok else {"test_id": name, "status": "FAIL", "expected": "PASS", "observed": "FAIL"} for name, ok in results]


def main() -> int:
    results = run()
    for item in results:
        print(json.dumps(item, ensure_ascii=False))
    return 0 if all(item["status"] == "PASS" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
