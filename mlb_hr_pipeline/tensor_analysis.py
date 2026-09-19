"""
Interpretability analysis for the fitted CP tensor model.

After fitting an R=5 model, prints the top-3 latent modes:
for each component r, shows the highest-loading batters, pitchers,
zones, counts, pitch families, and parks.

Uses DEVIATION from factor mean to identify meaningful structure,
since early-stopped models have embeddings close to initialization.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from tensor_model import CPContactModel, ZONE_ORDER, COUNT_ORDER, FAMILY_ORDER

REPO = Path(__file__).resolve().parent
DATA_DIR = REPO / "data"


ZONE_DESCRIPTIONS = {
    1: "high-inside", 2: "high-middle", 3: "high-outside",
    4: "mid-inside", 5: "heart", 6: "mid-outside",
    7: "low-inside", 8: "low-middle", 9: "low-outside",
    11: "up-and-in (chase)", 12: "up-and-away (chase)",
    13: "down-and-in (chase)", 14: "down-and-away (chase)",
}


def load_name_maps(pitches_path):
    """Build pitcher_id -> name from the player_name column (which is pitcher name in Statcast)."""
    df = pd.read_csv(pitches_path, usecols=["batter", "pitcher", "player_name"])
    pitcher_names = df.groupby("pitcher")["player_name"].first().to_dict()
    return pitcher_names


def analyze_mode(model, component, pitcher_names=None, top_n=10):
    """Analyze a single latent component using deviation from factor mean."""
    mode = {"component": component, "factors": {}}

    for name in model.factor_names_:
        inv_vocab = {idx: val for val, idx in model.vocabs_[name].items()}
        emb = model.embeddings_[name]
        mean_val = emb[:, component].mean()
        std_val = emb[:, component].std()
        if std_val < 1e-10:
            std_val = 1.0

        entries = []
        for i in range(len(emb)):
            val = inv_vocab[i]
            raw = float(emb[i, component])
            dev = (raw - mean_val) / std_val
            entry = {"id": val, "loading": round(raw, 4), "deviation": round(dev, 3)}
            if name == "pitcher" and pitcher_names:
                entry["name"] = pitcher_names.get(int(val), f"P-{val}")
            elif name == "batter":
                entry["name"] = f"B-{val}"
            elif name == "zone":
                entry["description"] = ZONE_DESCRIPTIONS.get(int(val), f"zone {val}")
            elif name == "count":
                entry["description"] = f"{val} count"
            entries.append(entry)

        entries.sort(key=lambda x: x["deviation"], reverse=True)
        mode["factors"][name] = entries[:top_n]

        bottom = sorted(entries, key=lambda x: x["deviation"])[:top_n]
        mode["factors"][f"{name}_bottom"] = bottom

    return mode


def name_mode(mode):
    """Name a mode based on which small factors have the strongest deviations."""
    family_top = mode["factors"]["pitch_family"][0]
    family_bot = mode["factors"]["pitch_family_bottom"][0]
    zone_top = mode["factors"]["zone"][:3]
    zone_bot = mode["factors"]["zone_bottom"][:3]
    count_top = mode["factors"]["count"][:2]
    park_top = mode["factors"]["park"][:2]

    top_fam = family_top["id"]
    top_fam_dev = family_top["deviation"]
    bot_fam = family_bot["id"]
    bot_fam_dev = family_bot["deviation"]

    zone_descs_top = [z.get("description", str(z["id"])) for z in zone_top]
    zone_descs_bot = [z.get("description", str(z["id"])) for z in zone_bot]
    count_descs = [c.get("description", str(c["id"])) for c in count_top]
    park_descs = [p["id"] for p in park_top]

    parts = []

    if abs(top_fam_dev) > 0.5:
        parts.append(f"{top_fam} favoring")
    elif abs(bot_fam_dev) > 0.5:
        parts.append(f"anti-{bot_fam}")

    if zone_top[0]["deviation"] > 0.5:
        parts.append(f"in {zone_descs_top[0]}")
    elif zone_bot[0]["deviation"] < -0.5:
        parts.append(f"avoiding {zone_descs_bot[0]}")

    if count_top[0]["deviation"] > 0.3:
        parts.append(f"({count_descs[0]})")

    if park_top[0]["deviation"] > 0.5:
        parts.append(f"at {park_descs[0]}")

    if not parts:
        parts.append(f"{top_fam} × {zone_descs_top[0]} interaction")

    return " ".join(parts)


def compute_mode_variance(model, X):
    comp_scores = model.component_scores(X)
    total_var = comp_scores.var(axis=0)
    frac = total_var / total_var.sum() if total_var.sum() > 0 else total_var
    return total_var, frac


def deviation_summary(mode, factor_name, n=5, direction="top"):
    """Format top/bottom deviations for a factor."""
    key = factor_name if direction == "top" else f"{factor_name}_bottom"
    entries = mode["factors"][key][:n]
    lines = []
    for e in entries:
        label = e.get("name", e.get("description", str(e["id"])))
        lines.append(f"    {label:<30s}  dev={e['deviation']:+.3f}  raw={e['loading']:.4f}")
    return "\n".join(lines)


def main():
    model_path = DATA_DIR / "cp_model_R5.json"
    if not model_path.exists():
        print(f"ERROR: {model_path} not found. Run tensor_experiment.py first.",
              file=sys.stderr)
        sys.exit(1)

    pitches_path = DATA_DIR / "pitches_2025.csv"
    if not pitches_path.exists():
        print(f"ERROR: {pitches_path} not found.", file=sys.stderr)
        sys.exit(1)

    print("=" * 70)
    print("TENSOR MODEL — LATENT MODE ANALYSIS (R=5)")
    print("=" * 70)

    model = CPContactModel.load(model_path)
    pitcher_names = load_name_maps(pitches_path)
    print(f"Loaded model: R={model.rank}, bias={model.bias_:.4f}")
    print(f"Loaded {len(pitcher_names)} pitcher name mappings")

    from tensor_experiment import collapse_pitches_to_pa, prepare_features
    pitches = pd.read_csv(pitches_path)
    pa = collapse_pitches_to_pa(pitches)
    X, y, dates, pa_clean = prepare_features(pa)

    total_var, frac = compute_mode_variance(model, X)
    print(f"\nComponent variance fractions: {[f'{f:.3f}' for f in frac]}")

    for name in model.factor_names_:
        emb = model.embeddings_[name]
        spread = emb.std(axis=0)
        print(f"  {name:>15s}: embedding std per component = "
              f"{[f'{s:.4f}' for s in spread]}")

    modes = []
    for r in range(model.rank):
        print(f"\n{'='*70}")
        print(f"MODE {r} (variance fraction: {frac[r]:.3f})")
        print("=" * 70)

        mode = analyze_mode(model, r, pitcher_names, top_n=10)
        mode_name = name_mode(mode)
        mode["name"] = mode_name
        mode["variance_fraction"] = round(float(frac[r]), 4)

        print(f"  Interpretation: {mode_name}")

        for factor_name in ["pitch_family", "zone", "count", "park"]:
            print(f"\n  {factor_name.upper()} — highest deviation:")
            print(deviation_summary(mode, factor_name, n=5, direction="top"))
            print(f"  {factor_name.upper()} — lowest deviation:")
            print(deviation_summary(mode, factor_name, n=3, direction="bottom"))

        print(f"\n  PITCHER — highest deviation (HR-prone):")
        print(deviation_summary(mode, "pitcher", n=5, direction="top"))
        print(f"  PITCHER — lowest deviation (HR-suppressing):")
        print(deviation_summary(mode, "pitcher", n=5, direction="bottom"))

        print(f"\n  BATTER — highest deviation (HR-prone):")
        print(deviation_summary(mode, "batter", n=5, direction="top"))

        modes.append(mode)

    ranked = sorted(modes, key=lambda m: m["variance_fraction"], reverse=True)[:3]
    print(f"\n{'='*70}")
    print("TOP 3 MODES BY VARIANCE")
    print("=" * 70)
    for i, m in enumerate(ranked):
        print(f"\n  {i+1}. Component {m['component']}: {m['name']}")
        print(f"     Variance fraction: {m['variance_fraction']:.3f}")

        top_pitchers = [
            e.get("name", str(e["id"])) for e in m["factors"]["pitcher"][:3]
        ]
        top_zones = [
            e.get("description", str(e["id"])) for e in m["factors"]["zone"][:3]
        ]
        top_families = [
            f"{e['id']} (dev={e['deviation']:+.2f})"
            for e in m["factors"]["pitch_family"][:3]
        ]
        print(f"     Top pitchers: {', '.join(top_pitchers)}")
        print(f"     Top zones: {', '.join(top_zones)}")
        print(f"     Pitch families: {', '.join(top_families)}")

    def clean_for_json(obj):
        if isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)
        if isinstance(obj, (np.floating, np.float64, np.float32)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    modes_json = []
    for m in modes:
        cleaned = {
            "component": m["component"],
            "name": m["name"],
            "variance_fraction": m["variance_fraction"],
            "factors": {},
        }
        for fname, entries in m["factors"].items():
            if fname.endswith("_bottom"):
                continue
            cleaned["factors"][fname] = [
                {k: clean_for_json(v) for k, v in e.items()} for e in entries
            ]
        modes_json.append(cleaned)

    out_path = DATA_DIR / "tensor_modes.json"
    with open(out_path, "w") as f:
        json.dump(modes_json, f, indent=2, default=str)
    print(f"\nSaved mode descriptions to {out_path}")


if __name__ == "__main__":
    main()
