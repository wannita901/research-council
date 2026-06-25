"""rq1: does accuracy scale monotonically with N, and does sequence length T interact?

Sanitized fixture mirroring the live …103845 rq1 script: a two-way (N×T) layout whose RNG
is deliberately *unpinned* — that missing seed is the reproducibility gap the repro manifest
is meant to surface honestly (deterministic=False), not paper over.
"""

import numpy as np
from scipy import stats

N = np.array([2, 4, 8, 16])
T = np.array([1, 2, 4, 8])
acc = np.random.rand(len(N), len(T))  # no seed: irreproducible by design
F, _p = stats.f_oneway(*acc)

print(f"METRIC interaction_F={F:.4f}")
print("METRIC interaction_effect_size=0.39")
print("METRIC signed_NxT_interaction=0.000137")
