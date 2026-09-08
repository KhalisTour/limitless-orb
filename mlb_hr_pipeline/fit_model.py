"""
Phase 1 — Fit Model 3's logistic regression for HOME RUNS.

The machinery lives in fit_common.py; this file is the HR spec.

Feature set: barrel / xslg / hardhit / la / k, and exactly those. The wider set
this script used to fit (adding whiff, xwoba and six pitcher terms) scored no
better on a holdout and produced coefficients that only made sense jointly —
xwoba landed at -0.198 despite a +0.20 univariate association, cancelling xslg's
+0.278. predict_today then copied five of the seven into models.M3 and left the
cancelling term behind. Fitting only what the model reads makes that impossible.

Pitcher terms are omitted for now: the pitcher board joins to well under half of
the PAs in backtest.csv, so their coefficients are estimated on a biased subset.
models.M3_PITCHER stays at zero and the opponent enters through model2_matchup.
Add "p_barrel" etc. to FEATURES once the pitcher board covers the slate.

Run: python fit_model.py
"""

import sys
import traceback

from fit_common import fit_target

FEATURES = ["barrel", "xslg", "hardhit", "la", "k"]


def main():
    return fit_target("hr", FEATURES, "fitted_coefficients.json", tag="fit")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("\nRerun: python fit_model.py", file=sys.stderr)
        sys.exit(1)
