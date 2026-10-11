"""Firmware core is independent of Qt; importing this package starts no UI."""

import os

# Every solve here is tiny (a few hundred rows); an OpenBLAS thread pool makes each SVD inside
# least_squares ~10x slower on many-core machines. Must be set before numpy loads; users can override.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

__version__ = "0.1.0"
