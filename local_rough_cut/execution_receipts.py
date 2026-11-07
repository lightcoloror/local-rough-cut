# Copyright 2026 lightcoloror
# SPDX-License-Identifier: Apache-2.0
# Extracted/adapted from mvp-video-pipeline on 2026-11-07; see PROVENANCE.md.
from __future__ import annotations
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping
from .contract_io import artifact_reference, atomic_write_json, atomic_write_text, payload_sha256, validate_artifact_reference
from .errors import ContractError
from .review_resource_guard import ReviewResourceGuard, ReviewResourcePolicy, apply_ffmpeg_thread_limit, below_normal_priority_kwargs
FFMPEG_EXECUTION_RECEIPT_SCHEMA = "video_creation_pipeline.ffmpeg_execution_receipt.v1"
FFMPEG_OUTLET_ID = "video_creation_pipeline.single_ffmpeg_outlet"
FFMPEG_OPERATIONS = {"render", "trim", "technical_qa", "frame_extract"}
RESOURCE_GUARDED_REVIEW_OPERATIONS = {"frame_extract", "technical_qa"}

def resolve_media_tool(tool: str) -> str:
    """Resolve the FFmpeg/FFprobe executable behind a Windows command wrapper."""

    if tool not in {"ffmpeg", "ffprobe"}:
        raise ContractError("unsupported media tool")
    executable = _resolve_windows_tool_wrapper(shutil.which(tool), tool)
    if not executable:
        raise ContractError(f"{tool} executable was not found")
    return executable


def probe_media_metadata(
    media_path: str | Path,
    *,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Read local media metadata through the shared FFprobe tool boundary."""
    media = Path(media_path).expanduser().resolve()
    if not media.is_file():
        raise ContractError(f"media file not found: {media}")
    executable = _resolve_windows_tool_wrapper(shutil.which("ffprobe"), "ffprobe")
    if not executable:
        raise ContractError("ffprobe executable was not found")
    try:
        result = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-protocol_whitelist", "file",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(media),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=float(timeout_s),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"ffprobe metadata read failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise ContractError(f"ffprobe metadata read failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("ffprobe returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ContractError("ffprobe returned an invalid metadata document")
    return payload


def build_ffmpeg_execution_receipt(
    operation: str,
    command: list[str],
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    *,
    requested_backend: str = "software",
    selected_backend: str = "software",
    hardware_accelerated: bool = False,
    fallback_used: bool = False,
    fallback_reason: str = "",
    resource_guard: Mapping[str, Any] | None = None,
    executed_command: list[str] | None = None,
) -> dict[str, Any]:
    if operation not in FFMPEG_OPERATIONS:
        raise ContractError("FFmpeg receipt operation is unsupported")
    normalized_command = [str(value) for value in command]
    if not normalized_command or normalized_command[0] != "ffmpeg":
        raise ContractError("FFmpeg receipt command must start with ffmpeg")
    if not str(requested_backend).strip() or not str(selected_backend).strip():
        raise ContractError("FFmpeg execution backends are required")
    if not isinstance(hardware_accelerated, bool) or not isinstance(
        fallback_used, bool
    ):
        raise ContractError("FFmpeg execution profile flags must be booleans")
    if fallback_used and not str(fallback_reason).strip():
        raise ContractError("FFmpeg fallback requires a visible reason")
    receipt: dict[str, Any] = {
        "schema": FFMPEG_EXECUTION_RECEIPT_SCHEMA,
        "outlet_id": FFMPEG_OUTLET_ID,
        "status": "completed",
        "operation": operation,
        "execution_profile": {
            "selected_by": FFMPEG_OUTLET_ID,
            "requested_backend": str(requested_backend),
            "selected_backend": str(selected_backend),
            "hardware_accelerated": hardware_accelerated,
            "fallback_used": fallback_used,
            "fallback_reason": str(fallback_reason),
            "automatic_local_cloud_fallback": False,
        },
        "command": normalized_command,
        "inputs": _build_artifact_rows(inputs, "FFmpeg input"),
        "outputs": _build_artifact_rows(outputs, "FFmpeg output"),
        "execution_boundary": {
            "orchestration_owner": FFMPEG_OUTLET_ID,
            "second_ffmpeg_orchestrator_created": False,
            "source_media_modified": False,
            "automatic_publish": False,
        },
    }
    if resource_guard is not None:
        receipt["resource_guard"] = dict(resource_guard)
    if executed_command is not None:
        receipt["executed_command"] = [str(value) for value in executed_command]
    receipt["receipt_sha256"] = payload_sha256(receipt, "receipt_sha256")
    # Both computed and supplied references were content-verified above.
    # Avoid a second full-file pass; public validation still verifies contents.
    validate_ffmpeg_execution_receipt(receipt, verify_artifacts=False)
    return receipt


def execute_ffmpeg_with_receipt(
    operation: str,
    command: list[str],
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    receipt_path: str | Path,
    *,
    capture_log_path: str | Path | None = None,
    timeout_s: float = 120.0,
    resource_policy: ReviewResourcePolicy | None = None,
    resource_snapshot_provider: Callable[[], Mapping[str, Any]] | None = None,
    guard_resource: bool | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    resource_lock_path: str | Path | None = None,
) -> dict[str, Any]:
    if not command or command[0] != "ffmpeg":
        raise ContractError("FFmpeg outlet command must start with ffmpeg")
    executable = _resolve_windows_tool_wrapper(shutil.which("ffmpeg"), "ffmpeg")
    if not executable:
        raise ContractError("ffmpeg executable was not found")
    selected_runner = runner or subprocess.run
    guarded = (
        operation in RESOURCE_GUARDED_REVIEW_OPERATIONS
        if guard_resource is None
        else bool(guard_resource)
    )
    resource_guard: Mapping[str, Any] | None = None
    executed_command: list[str] | None = None
    try:
        if guarded:
            policy = resource_policy or ReviewResourcePolicy.from_environment()
            guard = ReviewResourceGuard(
                policy=policy,
                lock_path=resource_lock_path,
                snapshot_provider=resource_snapshot_provider,
            )
            executed_command = apply_ffmpeg_thread_limit(
                command, policy.ffmpeg_threads
            )
            with guard:
                result = selected_runner(
                    [executable, *executed_command[1:]],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=float(timeout_s),
                    check=False,
                    **below_normal_priority_kwargs(),
                )
            resource_guard = guard.receipt_metadata()
        else:
            result = selected_runner(
                [executable, *command[1:]],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=float(timeout_s),
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError(f"FFmpeg outlet execution failed: {exc}") from exc
    if capture_log_path is not None:
        log_path = Path(capture_log_path).expanduser().resolve()
        atomic_write_text(
            (result.stderr or "") + (result.stdout or ""),
            log_path,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        raise ContractError(
            f"FFmpeg outlet returned exit code {result.returncode}: {detail}"
        )
    receipt = build_ffmpeg_execution_receipt(
        operation,
        command,
        inputs,
        outputs,
        resource_guard=resource_guard,
        executed_command=executed_command,
    )
    # ``build_ffmpeg_execution_receipt`` has already validated the receipt and
    # bound its artifacts.  Writing it directly avoids hashing a large input a
    # second time merely to persist the same contract.
    atomic_write_json(receipt, receipt_path)
    return receipt


def _resolve_windows_tool_wrapper(candidate: str | None, tool: str) -> str | None:
    if not candidate:
        return None
    path = Path(candidate).expanduser().resolve()
    if path.suffix.casefold() not in {".cmd", ".bat"}:
        return str(path)
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ContractError(f"{tool} command wrapper is unreadable: {exc}") from exc
    pattern = re.compile(
        r'^\s*"%~dp0(?P<relative>[^"\r\n]+\.exe)"\s+%\*\s*$',
        flags=re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(content)
    if match is None:
        raise ContractError(
            f"{tool} command wrapper is unsupported; an explicit executable is required"
        )
    target = (path.parent / match.group("relative")).resolve()
    if not target.is_file():
        raise ContractError(f"{tool} command wrapper target was not found")
    return str(target)


def validate_ffmpeg_execution_receipt(
    receipt: dict[str, Any], *, verify_artifacts: bool = True
) -> None:
    if receipt.get("schema") != FFMPEG_EXECUTION_RECEIPT_SCHEMA:
        raise ContractError("unsupported FFmpeg execution receipt schema")
    if receipt.get("receipt_sha256") != payload_sha256(receipt, "receipt_sha256"):
        raise ContractError("FFmpeg execution receipt integrity check failed")
    if receipt.get("outlet_id") != FFMPEG_OUTLET_ID:
        raise ContractError("FFmpeg work must use the existing single outlet")
    if receipt.get("status") != "completed":
        raise ContractError("only completed FFmpeg receipts can be consumed")
    if receipt.get("operation") not in FFMPEG_OPERATIONS:
        raise ContractError("FFmpeg receipt operation is unsupported")

    profile = _object(receipt.get("execution_profile"))
    if profile.get("selected_by") != FFMPEG_OUTLET_ID:
        raise ContractError("FFmpeg hardware profile was not selected by the outlet")
    for field in ("requested_backend", "selected_backend"):
        if not str(profile.get(field) or "").strip():
            raise ContractError(f"FFmpeg execution profile {field} is required")
    if not isinstance(profile.get("hardware_accelerated"), bool):
        raise ContractError("FFmpeg execution profile must report acceleration")
    if not isinstance(profile.get("fallback_used"), bool):
        raise ContractError("FFmpeg execution profile must report fallback")
    if (
        profile["fallback_used"]
        and not str(profile.get("fallback_reason") or "").strip()
    ):
        raise ContractError("FFmpeg hardware fallback requires a visible reason")
    if profile.get("automatic_local_cloud_fallback") is not False:
        raise ContractError("FFmpeg receipt cannot use local/cloud fallback")
    command = receipt.get("command")
    if not isinstance(command, list) or not command or command[0] != "ffmpeg":
        raise ContractError("FFmpeg receipt requires the executed argument vector")

    inputs = receipt.get("inputs")
    outputs = receipt.get("outputs")
    if not isinstance(inputs, list) or not inputs:
        raise ContractError("FFmpeg receipt requires input artifacts")
    if not isinstance(outputs, list) or not outputs:
        raise ContractError("FFmpeg receipt requires output artifacts")
    _validate_artifact_rows(
        inputs, "FFmpeg input", verify_artifacts=verify_artifacts
    )
    _validate_artifact_rows(
        outputs, "FFmpeg output", verify_artifacts=verify_artifacts
    )
    for row in outputs:
        if receipt["operation"] == "frame_extract":
            _nonnegative_number(row.get("media_time_s"), "frame media_time_s")
            if str(row.get("role") or "") != "frame":
                raise ContractError("frame extraction outputs must use role=frame")

    boundary = _object(receipt.get("execution_boundary"))
    if (
        boundary.get("orchestration_owner") != FFMPEG_OUTLET_ID
        or boundary.get("second_ffmpeg_orchestrator_created") is not False
        or boundary.get("source_media_modified") is not False
        or boundary.get("automatic_publish") is not False
    ):
        raise ContractError("FFmpeg receipt execution boundary is invalid")


def _validate_artifact_rows(
    rows: list[Any], label: str, *, verify_artifacts: bool = True
) -> None:
    roles: set[str] = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ContractError(f"{label} row must be an object")
        role = str(row.get("role") or "").strip()
        if not role:
            raise ContractError(f"{label} role is required")
        unique_role = f"{role}:{index}"
        if unique_role in roles:
            raise ContractError(f"{label} role is duplicated")
        roles.add(unique_role)
        artifact = _object(row.get("artifact"))
        if verify_artifacts:
            validate_artifact_reference(artifact, f"{label} {index}")
        else:
            _validate_artifact_reference_metadata(artifact, f"{label} {index}")


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_sha256(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "").lower()))


def _nonnegative_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ContractError(f"{label} must be a non-negative finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label} must be a non-negative finite number") from exc
    if result < 0 or not math.isfinite(result):
        raise ContractError(f"{label} must be a non-negative finite number")

    return result


def _build_artifact_rows(
    rows: list[dict[str, Any]], label: str
) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ContractError(f"{label} artifacts must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ContractError(f"{label} row must be an object")
        role = str(row.get("role") or "").strip()
        if not role:
            raise ContractError(f"{label} role is required")
        supplied = row.get("artifact")
        if supplied is not None:
            validate_artifact_reference(_object(supplied), f"{label} {index}")
        artifact = (
            _validate_artifact_reference_metadata(
                _object(supplied), f"{label} {index}"
            )
            if supplied is not None
            else artifact_reference(str(row.get("path") or ""), f"{label} {index}")
        )
        normalized_row: dict[str, Any] = {"role": role, "artifact": artifact}
        if row.get("media_time_s") is not None:
            normalized_row["media_time_s"] = _nonnegative_number(
                row["media_time_s"], f"{label} media_time_s"
            )
        normalized.append(normalized_row)
    return normalized


def _validate_artifact_reference_metadata(
    reference: dict[str, Any], label: str
) -> dict[str, Any]:
    """Validate a precomputed artifact without re-reading its full contents."""

    path = Path(str(reference.get("path") or "")).expanduser().resolve()
    if not path.is_file():
        raise ContractError(f"{label} not found: {path}")
    try:
        byte_count = int(reference.get("bytes"))
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label} byte count is invalid") from exc
    if byte_count != path.stat().st_size:
        raise ContractError(f"{label} size changed after hashing")
    sha256 = str(reference.get("sha256") or "").lower()
    if not _is_sha256(sha256):
        raise ContractError(f"{label} SHA-256 is invalid")
    content_kind = str(reference.get("content_kind") or "")
    canonical = reference.get("canonical_sha256")
    if content_kind == "binary":
        if canonical is not None:
            raise ContractError(f"{label} binary canonical hash must be null")
    elif content_kind == "json":
        if not _is_sha256(canonical):
            raise ContractError(f"{label} JSON canonical hash is invalid")
    else:
        raise ContractError(f"{label} content kind is invalid")
    return {
        "path": str(path),
        "bytes": byte_count,
        "sha256": sha256,
        "content_kind": content_kind,
        "canonical_sha256": canonical,
    }
