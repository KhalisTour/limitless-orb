"""
Phase 1 — Fit Model 3's logistic regression for EXTRA-BASE HITS (2B + 3B + HR).

The machinery lives in fit_common.py; this file is the XBH spec.

Feature set: barrel / xslg / hardhit / la. Adding whiff, k and xwoba on top of
these scored *worse* on the holdout (AUC 0.536 vs 0.538) and flipped signs, so
they are out. This script previously fit the identical feature list to the HR
script, and models_xbh.py was itself a copy of models.py, so "the XBH model" was
the HR model wearing a different label — the two ranked a slate at Spearman 0.96.

Run: python fit_model_xbh.py
"""

import sys
import traceback

from fit_common import fit_target

FEATURES = ["barrel", "xslg", "hardhit", "la"]


def main():
    return fit_target("xbh", FEATURES, "fitted_coefficients_xbh.json", tag="fit_xbh")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("\nRerun: python fit_model_xbh.py", file=sys.stderr)
        sys.exit(1)
