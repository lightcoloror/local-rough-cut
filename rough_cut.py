#!/usr/bin/env python3
# Copyright 2026 lightcoloror. SPDX-License-Identifier: Apache-2.0
"""Portable entry point, including when Python is started in isolated mode."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from local_rough_cut.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
