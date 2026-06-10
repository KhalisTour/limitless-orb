"""
Explainability layer for HR predictions.

Decomposes model5_ensemble output into human-readable explanations by computing
each component's contribution relative to the league baseline, ranking factors,
and generating plain English summary text.
"""

from models import (model5_ensemble, model1_linear, model2_matchup,
                     model3_logistic, model4_context, _pop, zscore)
from data import LEAGUE, HITTERS


def _percentile_rank(field, value):
    if value is None:
        return 50.0
    vals = sorted(_pop(field))
    if not vals:
        return 50.0
    count_below = sum(1 for v in vals if v < value)
    return round(100.0 * count_below / len(vals), 1)


def _pct_label(pct):
    if pct >= 95:
        return "Elite"
    if pct >= 80:
        return "Above average"
    if pct >= 60:
        return "Solid"
    if pct >= 40:
        return "Average"
    if pct >= 20:
        return "Below average"
    return "Poor"


FACTOR_DISPLAY = {
    "barrel": ("barrel rate", "{:.1f}%"),
    "xslg": ("expected slugging", "{:.3f}"),
    "hardhit": ("hard-hit rate", "{:.1f}%"),
    "ev": ("exit velocity", "{:.1f} mph"),
    "la": ("launch angle", "{:.1f}°"),
    "whiff": ("whiff rate", "{:.1f}%"),
    "k": ("strikeout rate", "{:.1f}%"),
    "matchup": ("pitcher matchup", "{:.2f}"),
}


def explain_prediction(name, lineup_order, park_factor=1.0, platoon_factor=1.0):
    result = model5_ensemble(name, lineup_order,
                              park_factor=park_factor,
                              platoon_factor=platoon_factor)
    p_total = result["p_per_pa"]
    baseline = LEAGUE["hr_per_pa"]
    comps = result["components"]

    h = HITTERS[name]

    factors = []
    stat_fields = ["barrel", "xslg", "hardhit", "ev", "la", "whiff", "k"]
    for field in stat_fields:
        val = h.get(field)
        if val is None:
            continue
        pct = _percentile_rank(field, val)
        z = zscore(field, val)
        display_name, fmt = FACTOR_DISPLAY.get(field, (field, "{:.2f}"))
        contribution = abs(z) / max(sum(abs(zscore(f, h.get(f))) for f in stat_fields if h.get(f) is not None), 0.01)
        inverted = field in ("whiff", "k")
        direction = "negative" if (z > 0 and inverted) or (z < 0 and not inverted) else "positive"
        label = _pct_label(100 - pct if inverted else pct)
        text = f"{label} {display_name} ({fmt.format(val)}, {pct:.0f}th percentile)"
        factors.append({
            "factor": field,
            "value": val,
            "z_score": round(z, 2),
            "percentile": pct,
            "contribution": round(contribution, 3),
            "direction": direction,
            "text": text,
        })

    m2_score = model2_matchup(name)
    factors.append({
        "factor": "matchup",
        "value": round(m2_score, 3),
        "z_score": round(m2_score, 2),
        "percentile": None,
        "contribution": round(abs(m2_score) / max(sum(f["contribution"] for f in factors) + abs(m2_score), 0.01), 3),
        "direction": "positive" if m2_score > 0 else "negative",
        "text": f"{'Favorable' if m2_score > 0 else 'Unfavorable'} pitcher matchup (score: {m2_score:.2f})",
    })

    if park_factor != 1.0:
        pf_dir = "positive" if park_factor > 1.0 else "negative"
        factors.append({
            "factor": "park",
            "value": park_factor,
            "z_score": None,
            "percentile": None,
            "contribution": round(abs(park_factor - 1.0), 3),
            "direction": pf_dir,
            "text": f"{'Hitter-friendly' if park_factor > 1 else 'Pitcher-friendly'} park (factor: {park_factor:.3f})",
        })

    if platoon_factor != 1.0:
        pl_dir = "positive" if platoon_factor > 1.0 else "negative"
        factors.append({
            "factor": "platoon",
            "value": platoon_factor,
            "z_score": None,
            "percentile": None,
            "contribution": round(abs(platoon_factor - 1.0), 3),
            "direction": pl_dir,
            "text": f"{'Favorable' if platoon_factor > 1 else 'Unfavorable'} platoon split (factor: {platoon_factor:.3f})",
        })

    factors.sort(key=lambda f: f["contribution"], reverse=True)
    top_factors = factors[:3]

    parts = []
    for f in top_factors:
        if f["direction"] == "positive":
            parts.append(f["text"])
    explanation = ". ".join(parts) if parts else "Near league-average HR profile."

    caution = None
    whiff_val = h.get("whiff")
    k_val = h.get("k")
    if whiff_val is not None and _percentile_rank("whiff", whiff_val) > 75:
        caution = f"High whiff rate ({whiff_val:.1f}%) limits certainty."
    elif k_val is not None and _percentile_rank("k", k_val) > 80:
        caution = f"Elevated strikeout rate ({k_val:.1f}%) reduces PA quality."

    return {
        "hitter": name,
        "p_hr": round(p_total, 6),
        "p_hr_baseline": baseline,
        "top_factors": top_factors,
        "explanation": explanation,
        "caution": caution,
    }


if __name__ == "__main__":
    from data import LINEUP_ORDER
    for name in LINEUP_ORDER[:3]:
        ex = explain_prediction(name, LINEUP_ORDER)
        print(f"\n{name}:")
        print(f"  p_hr={ex['p_hr']:.4f}  baseline={ex['p_hr_baseline']:.4f}")
        for f in ex["top_factors"]:
            print(f"  [{f['direction']}] {f['text']}")
        print(f"  => {ex['explanation']}")
        if ex["caution"]:
            print(f"  ⚠ {ex['caution']}")
