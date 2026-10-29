#!/usr/bin/env python3
"""Prepare the System IO package's SDK/runtime bindings."""
from pathlib import Path
import os,sys
script=Path(__file__).resolve().parents[2]/'sputnik-lang/packages/system_io/tools/configure.py'
os.execv(sys.executable,[sys.executable,str(script),*sys.argv[1:]])
