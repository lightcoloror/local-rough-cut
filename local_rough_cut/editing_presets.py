# Copyright 2026 lightcoloror
# SPDX-License-Identifier: Apache-2.0
# Extracted/adapted from mvp-video-pipeline on 2026-11-07; see PROVENANCE.md.
import math
from .errors import ContractError
# Original editing-presets.v1.json audio_splices.maximum_fade_s.
POLICY = {"audio_splices": {"maximum_fade_s": 0.02}}

def number(value, label="time"):
    if isinstance(value, bool):
        raise ContractError(f"{label} must be numeric")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label} must be numeric") from exc
    if not math.isfinite(value):
        raise ContractError(f"{label} must be finite")
    return value


def fade_filter(row):
    duration = row["end"] - row["start"]
    filters = []
    for key in ("fade_in_s", "fade_out_s"):
        amount = number(row[key], key)
        if (
            not 0
            <= amount
            <= min(POLICY["audio_splices"]["maximum_fade_s"], duration / 4) + 1e-7
        ):
            raise ContractError("unsafe splice fade duration")
        if amount:
            start = 0 if key == "fade_in_s" else duration - amount
            kind = "in" if key == "fade_in_s" else "out"
            filters.append(f"afade=t={kind}:st={start:.9f}:d={amount:.9f}")
    return ",".join(filters)
