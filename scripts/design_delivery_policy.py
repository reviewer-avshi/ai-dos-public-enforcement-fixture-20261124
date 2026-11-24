"""Validation contract for versioned local and approved Figma design evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DESIGN_POLICY_VERSION = "1.0"
DESIGN_ROUTES = {"LOCAL_VERSIONED", "FIGMA_APPROVED"}


def _path(root: Path, value: str) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_render(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        return False
    data = path.read_bytes()
    if path.suffix.lower() == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    return data.startswith(b"\xff\xd8\xff")


def _valid_source(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="ignore") if suffix in {".svg", ".html", ".css", ".js", ".json"} else ""
    if suffix == ".svg":
        return "<svg" in text.lower() and "</svg>" in text.lower()
    if suffix == ".html":
        return any(token in text.lower() for token in ("<html", "<body", "<main", "<button", "<a "))
    return True


def _validate_local(stage_id: str, contract: dict, root: Path) -> list[str]:
    errors: list[str] = []
    sources = contract.get("source_paths")
    renders = contract.get("render_paths")
    index_path = _path(root, contract.get("screen_index"))
    if not isinstance(sources, list) or not sources:
        errors.append("LOCAL_VERSIONED requires non-empty source_paths")
    if not isinstance(renders, list) or not renders:
        errors.append("LOCAL_VERSIONED requires non-empty render_paths")
    if index_path is None or not index_path.is_file():
        errors.append("LOCAL_VERSIONED screen_index is missing")
        return errors
    source_paths = {_path(root, value) for value in sources or []}
    render_paths = {_path(root, value) for value in renders or []}
    if None in source_paths or any(not item or not _valid_source(item) for item in source_paths):
        errors.append("LOCAL_VERSIONED contains a missing or invalid editable source")
    if None in render_paths or any(not item or not _valid_render(item) for item in render_paths):
        errors.append("LOCAL_VERSIONED contains a missing or invalid PNG/JPEG render")
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"screen_index is invalid: {exc}")
        return errors
    screens = index.get("screens") if isinstance(index, dict) else None
    if not isinstance(screens, list) or not screens:
        errors.append("screen_index must contain a non-empty screens list")
        return errors
    for screen in screens:
        if not isinstance(screen, dict) or not screen.get("id") or not screen.get("version"):
            errors.append("each screen index entry requires id and version")
            continue
        source = _path(root, screen.get("source"))
        render = _path(root, screen.get("render"))
        if source not in source_paths or render not in render_paths:
            errors.append(f"screen {screen.get('id')} is not mapped to declared source and render")
            continue
        if screen.get("source_sha256") != _sha256(source):
            errors.append(f"screen {screen.get('id')} source hash does not match")
        if screen.get("render_sha256") != _sha256(render):
            errors.append(f"screen {screen.get('id')} render hash does not match")
    if stage_id == "PROTOTYPE":
        prototype = _path(root, contract.get("prototype_path"))
        if prototype is None or not prototype.is_file():
            errors.append("LOCAL_VERSIONED PROTOTYPE requires prototype_path")
    return errors


def _validate_figma(contract: dict) -> list[str]:
    errors: list[str] = []
    url = contract.get("figma_url")
    if not isinstance(url, str) or not url.startswith(("https://www.figma.com/", "https://figma.com/")):
        errors.append("FIGMA_APPROVED requires a real Figma URL")
    if not contract.get("file_key"):
        errors.append("FIGMA_APPROVED requires file_key")
    if not isinstance(contract.get("node_ids"), list) or not contract.get("node_ids"):
        errors.append("FIGMA_APPROVED requires node_ids")
    if not contract.get("version"):
        errors.append("FIGMA_APPROVED requires a version or revision")
    evidence = contract.get("review_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "VERIFIED":
        errors.append("FIGMA_APPROVED requires verified external review evidence")
    if isinstance(evidence, dict) and evidence.get("source") in {None, "state_file", "markdown", "json"}:
        errors.append("FIGMA_APPROVED review evidence cannot be state-file or text-only")
    if not contract.get("reviewed_head_sha"):
        errors.append("FIGMA_APPROVED evidence must bind to a reviewed Git head")
    return errors


def validate_design_stage(stage_id: str, stage: dict, root: Path) -> list[str]:
    if stage.get("status") == "N/A":
        return []
    route = stage.get("design_route")
    if route not in DESIGN_ROUTES:
        return [f"design stage {stage_id} requires design_route LOCAL_VERSIONED or FIGMA_APPROVED"]
    contract = stage.get("design_contract")
    if not isinstance(contract, dict):
        return [f"design stage {stage_id} is missing design_contract"]
    if contract.get("policy_version") != DESIGN_POLICY_VERSION:
        return [f"design stage {stage_id} has unsupported design policy version"]
    if contract.get("route") != route:
        return [f"design stage {stage_id} route and contract.route disagree"]
    return _validate_local(stage_id, contract, root) if route == "LOCAL_VERSIONED" else _validate_figma(contract)
