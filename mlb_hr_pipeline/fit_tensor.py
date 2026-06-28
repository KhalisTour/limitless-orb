"""
fit_tensor.py — CP (PARAFAC) decomposition of the batter x pitch-family x outcome
tensor, used as a RANKING layer for total-bases / XBH props.

Why a tensor (and why only a ranker):
  The XBH/HR logistic models use MARGINAL season stats — overall batter power,
  overall pitcher contact-quality-allowed. They are blind to INTERACTION: which
  hitters specifically punish sliders, which feast on fastballs. A 3-way tensor
  T[batter, pitch_family, outcome] captures exactly that. CP factorizes it into
  latent batter / family / outcome profiles, borrowing strength across similar
  hitters so sparse per-batter cells get smoothed.

  The reconstruction is NOT a calibrated probability (it runs hot in the tails),
  so we use it only to RE-RANK within a slate. Concretely we emit, per batter,
  the expected total bases and expected XBH rate against each of the 8 pitch
  families. At predict time the ranker dots those vectors with the opposing
  starter's actual pitch-mix (PITCHER["arsenal"], wired in ingest_live.py):

      score(batter) = sum_family  usage(pitcher, family) * E[TB | batter, family]

Inputs:  data/backtest_*.csv  (per-PA tables already produced by backtest.py)
Output:  models_out/tensor_factors.json
"""

import json
import sys
import glob
import traceback
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd


REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"
MODELS_DIR = REPO / "models_out"
MODELS_DIR.mkdir(exist_ok=True)


# --- dimension definitions -------------------------------------------------

# Mode B: pitch families, in the SAME order/keys as PITCHER["arsenal"] so the
# ranker is a plain dot product with the arsenal usage vector.
FAMILIES = ["four_seam", "sinker", "cutter", "slider",
            "change", "curve", "split", "kn"]
FAMILY_IDX = {f: i for i, f in enumerate(FAMILIES)}

# Statcast pitch_type code -> family. Sweeper (ST) and slurve (SV) fold into
# slider; knuckle-curve (KC) and slow-curve (CS) into curve; forkball (FO) into
# split; screwball (SC) into change; generic fastball (FA) into four_seam.
PITCH_TO_FAMILY = {
    "FF": "four_seam", "FA": "four_seam",
    "SI": "sinker",
    "FC": "cutter",
    "SL": "slider", "ST": "slider", "SV": "slider",
    "CH": "change", "SC": "change",
    "CU": "curve", "KC": "curve", "CS": "curve", "EP": "curve",
    "FS": "split", "FO": "split",
    "KN": "kn",
}

# Mode C: outcome buckets. Anything not a hit is an out (0 TB).
OUTCOMES = ["out", "single", "double", "triple", "hr"]
OUTCOME_IDX = {o: i for i, o in enumerate(OUTCOMES)}
EVENT_TO_OUTCOME = {
    "single": "single", "double": "double", "triple": "triple",
    "home_run": "hr",
}
TB_WEIGHT = np.array([0.0, 1.0, 2.0, 3.0, 4.0])   # total bases per bucket
XBH_WEIGHT = np.array([0.0, 0.0, 1.0, 1.0, 1.0])  # extra-base hit indicator

MIN_PA = 50      # drop batters with too few mapped PAs to be stable
RANK = 8         # CP rank (latent components)
N_ITERS = 250
SEED = 0
SHRINK_K = 50.0  # empirical-Bayes prior strength (in PA) toward the family marginal


# --- data assembly ---------------------------------------------------------

def load_backtests() -> pd.DataFrame:
    """Concatenate the season backtest tables and dedupe by (game_pk, at_bat_number)."""
    files = sorted(glob.glob(str(DATA_DIR / "backtest_*.csv")))
    if not files:
        raise RuntimeError(f"No backtest_*.csv in {DATA_DIR}. Run backtest.py first.")
    frames = []
    for f in files:
        df = pd.read_csv(f, usecols=lambda c: c in {
            "game_pk", "at_bat_number", "batter", "batter_name",
            "events", "last_pitch_type"})
        frames.append(df)
        print(f"[tensor] loaded {Path(f).name}: {len(df):,} rows")
    allpa = pd.concat(frames, ignore_index=True)
    before = len(allpa)
    allpa = allpa.drop_duplicates(subset=["game_pk", "at_bat_number"])
    print(f"[tensor] combined {before:,} -> {len(allpa):,} after dedupe")
    return allpa


def build_tensor(pa: pd.DataFrame):
    """Return (T, batter_ids, names) where T is counts [n_batter x 8 x 5]."""
    pa = pa.copy()
    pa["family"] = pa["last_pitch_type"].map(PITCH_TO_FAMILY)
    pa["outcome"] = pa["events"].map(EVENT_TO_OUTCOME).fillna("out")
    pa = pa.dropna(subset=["family", "batter"])

    # Keep batters with enough mapped PAs
    counts = pa.groupby("batter").size()
    keep = counts[counts >= MIN_PA].index
    pa = pa[pa["batter"].isin(keep)]
    batter_ids = sorted(pa["batter"].unique().tolist())
    bidx = {b: i for i, b in enumerate(batter_ids)}
    # NOTE: Statcast's pitch-feed `player_name` is the PITCHER, not the batter, so
    # we do NOT derive batter names here — the consumer maps batter_id -> name from
    # the daily batter board instead. Keyed entirely on the (correct) batter id.

    C = np.zeros((len(batter_ids), len(FAMILIES), len(OUTCOMES)), dtype=float)
    fi = pa["family"].map(FAMILY_IDX).to_numpy()
    oi = pa["outcome"].map(OUTCOME_IDX).to_numpy()
    bi = pa["batter"].map(bidx).to_numpy()
    np.add.at(C, (bi, fi, oi), 1.0)
    names = {}

    # Empirical-Bayes shrinkage: each (batter, family) outcome distribution is
    # pulled toward the league outcome mix for that pitch family, weighted by how
    # many PAs the batter actually saw of that family. Cells with ~6 PA (sparse
    # families like knuckleball) collapse to the league prior; cells with hundreds
    # of PAs keep their own signal. This kills small-sample spikes before the CP.
    n_bf = C.sum(axis=2, keepdims=True)               # [B x 8 x 1] PA per (b,f)
    fam_tot = C.sum(axis=0)                            # [8 x 5] league counts per family
    fam_prior = fam_tot / (fam_tot.sum(axis=1, keepdims=True) + 1e-9)  # P(o|family)
    R = (C + SHRINK_K * fam_prior[None, :, :]) / (n_bf + SHRINK_K)     # [B x 8 x 5] rates
    print(f"[tensor] tensor shape {C.shape}  batters>={MIN_PA}PA: {len(batter_ids)}  "
          f"total PAs in tensor: {int(C.sum()):,}  shrink_k={SHRINK_K:g}")
    return R, C, batter_ids, names


# --- non-negative CP-ALS (numpy only) --------------------------------------

def _unfold(T, mode):
    return np.moveaxis(T, mode, 0).reshape(T.shape[mode], -1)

def _khatri_rao(mats):
    """Column-wise Khatri-Rao product of a list of matrices (same #cols)."""
    r = mats[0].shape[1]
    out = mats[0]
    for m in mats[1:]:
        out = (out[:, None, :] * m[None, :, :]).reshape(-1, r)
    return out

def nn_parafac(T, rank, n_iters=250, seed=0, eps=1e-9):
    """Non-negative CP via multiplicative updates. Returns factor list [A,B,C]."""
    rng = np.random.default_rng(seed)
    factors = [rng.random((dim, rank)) + eps for dim in T.shape]
    unfolds = [_unfold(T, m) for m in range(T.ndim)]
    for it in range(n_iters):
        for m in range(T.ndim):
            others = [factors[j] for j in range(T.ndim) if j != m]
            # _unfold (moveaxis+reshape) lays columns out with the last remaining
            # axis fastest, so the Khatri-Rao must list the other factors in
            # ASCENDING mode order (first listed = slowest).
            kr = _khatri_rao(others)
            numer = unfolds[m] @ kr
            gram = np.ones((rank, rank))
            for j in range(T.ndim):
                if j != m:
                    gram *= factors[j].T @ factors[j]
            denom = factors[m] @ gram + eps
            factors[m] *= numer / denom
    # Reconstruction error for reporting
    recon = np.einsum("ir,jr,kr->ijk", *factors)
    err = np.linalg.norm(T - recon) / (np.linalg.norm(T) + eps)
    print(f"[tensor] NN-CP rank={rank} relative reconstruction error: {err:.4f}")
    return factors, recon


# --- main ------------------------------------------------------------------

def main():
    pa = load_backtests()
    R, C, batter_ids, names = build_tensor(pa)
    if len(batter_ids) < 50:
        raise RuntimeError(f"Only {len(batter_ids)} batters >= {MIN_PA} PA; too few.")

    # Decompose the shrunk RATE tensor: every (batter, family) slice already sums
    # to ~1 over outcomes, so the CP fits the outcome-shape structure rather than
    # raw PA volume, and low rank borrows strength across similar hitters.
    factors, recon = nn_parafac(R, RANK, n_iters=N_ITERS, seed=SEED)

    # Reconstructed per (batter, family) outcome distribution -> E[TB], E[XBH].
    recon = np.clip(recon, 0, None)
    denom = recon.sum(axis=2, keepdims=True)
    denom[denom == 0] = 1.0
    P = recon / denom                       # [B x 8 x 5] normalized over outcomes
    etb = P @ TB_WEIGHT                      # [B x 8]
    exbh = P @ XBH_WEIGHT                    # [B x 8]

    # League fallback: PA-weighted average over batters, per family.
    pa_per_bf = C.sum(axis=2)               # [B x 8] PAs per (batter, family)
    w = pa_per_bf / (pa_per_bf.sum(axis=0, keepdims=True) + 1e-9)
    etb_league = (etb * w).sum(axis=0).tolist()
    exbh_league = (exbh * w).sum(axis=0).tolist()

    out = {
        "meta": {
            "fit_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "rank": RANK, "min_pa": MIN_PA, "n_batters": len(batter_ids),
            "n_pa": int(C.sum()), "iters": N_ITERS, "shrink_k": SHRINK_K,
            "note": "ranking layer only; values are expected TB/XBH vs each pitch "
                    "family, not calibrated probabilities",
        },
        "families": FAMILIES,
        "etb": {str(b): [round(v, 5) for v in etb[i]] for i, b in enumerate(batter_ids)},
        "exbh": {str(b): [round(v, 5) for v in exbh[i]] for i, b in enumerate(batter_ids)},
        "etb_league": [round(v, 5) for v in etb_league],
        "exbh_league": [round(v, 5) for v in exbh_league],
    }
    out_path = MODELS_DIR / "tensor_factors.json"
    out_path.write_text(json.dumps(out))
    print(f"[tensor] wrote {out_path}")

    # Sanity check: the tensor is used as a matchup DELTA on top of the calibrated
    # logistic (level), so report the interaction spread, not an absolute board.
    league_usage = pa_per_bf.sum(axis=0) / pa_per_bf.sum()        # league pitch mix
    slider_heavy = np.array([.20, .05, .05, .45, .05, .10, .05, 0.])
    slider_heavy /= slider_heavy.sum()
    base = etb @ league_usage
    delta_sl = etb @ slider_heavy - base
    print(f"[tensor] E[TB]/PA vs league mix: mean={base.mean():.3f} "
          f"sd={base.std():.3f} (level handled by logistic)")
    print(f"[tensor] matchup delta vs a slider-heavy arsenal: "
          f"min={delta_sl.min():+.3f} max={delta_sl.max():+.3f} "
          f"sd={delta_sl.std():.3f}  <- this is the ranking signal")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("\nRerun: python fit_tensor.py", file=sys.stderr)
        sys.exit(1)
