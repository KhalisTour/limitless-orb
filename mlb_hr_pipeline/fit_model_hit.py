"""
Phase 1 — Fit Model 3's logistic regression for HITS (1B + 2B + 3B + HR).

The machinery lives in fit_common.py; this file is the hit spec.

Feature set: xba / k / la / hardhit — deliberately not the HR set. Measured over
backtest.csv, the power features that drive home runs are inert or negative
against the hit label (barrel -0.008, la -0.059, whiff -0.062, k -0.072 log-odds
per league SD), and expected batting average is the strongest single term. This
script used to fit the HR feature list to the hit label and produce a barrel
coefficient of -0.162, which is what a collinear power block does when asked to
explain singles.

Expect modest numbers. Per-PA hit outcomes are close to a coin flip against
season rate stats; a holdout AUC near 0.52 is the honest ceiling here, and the
0.555 the old script reported was in-sample.

Run: python fit_model_hit.py
"""

import sys
import traceback

from fit_common import fit_target

FEATURES = ["xba", "k", "la", "hardhit"]


def main():
    return fit_target("hit", FEATURES, "fitted_coefficients_hit.json", tag="fit_hit")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("\nRerun: python fit_model_hit.py", file=sys.stderr)
        sys.exit(1)
