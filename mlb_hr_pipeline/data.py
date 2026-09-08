"""
data.py — Input data transcribed from the uploaded Statcast-style tables.

All values are season-to-date rate stats. Percentile color (red=high, blue=low)
is captured separately in `pctile_flags` where it materially changes how a stat
should be read, but the model works off raw values and computes its own z-scores.

NOTE ON INTEGRITY: BBE (batted ball events) is the sample size behind every rate
here. Low-BBE players (e.g. Langford at 64) have noisy rates and the model shrinks
them toward the population mean accordingly. See README design doc.
"""

# ---- HITTERS -------------------------------------------------------------
# Keys map across the four tables (Hitters / Batted Ball / Plate Discipline).
# Where a column wasn't visible for a player, value is None and the model
# falls back to the population mean for that field (documented in README).

HITTERS = {
    "Joc Pederson":    dict(pos="DH", bbe=120, la=14.2, ev=91.7, hardhit=47.5, xwoba=.351, xba=.253, xslg=.411, k=20.6, bb=14.3, sprint=25.4,
                            weak=2.5, topped=27.5, under=25.8, flare=25.8, solid=9.2, barrel=9.2,
                            zone=49.8, zonesw=62.7, chase=26.2, edge=38.6, fps=27.5, swing=44.3, whiff=30.5),
    "Corey Seager":    dict(pos="SS", bbe=112, la=13.0, ev=91.2, hardhit=45.5, xwoba=.336, xba=.227, xslg=.457, k=26.9, bb=11.8, sprint=24.9,
                            weak=2.7, topped=32.1, under=23.2, flare=18.8, solid=7.1, barrel=16.1,
                            zone=42.2, zonesw=80.9, chase=28.9, edge=40.4, fps=48.6, swing=50.8, whiff=34.8),
    "Josh Jung":       dict(pos="3B", bbe=186, la=12.0, ev=89.5, hardhit=44.1, xwoba=.355, xba=.302, xslg=.454, k=15.6, bb=7.0, sprint=25.9,
                            weak=3.8, topped=31.7, under=18.8, flare=31.7, solid=8.6, barrel=5.4,
                            zone=47.6, zonesw=65.4, chase=32.3, edge=39.2, fps=35.7, swing=48.0, whiff=17.7),
    "Brandon Nimmo":   dict(pos="RF", bbe=181, la=11.5, ev=91.6, hardhit=50.3, xwoba=.375, xba=.283, xslg=.509, k=21.4, bb=8.3, sprint=28.2,
                            weak=3.3, topped=28.7, under=23.8, flare=24.3, solid=7.7, barrel=12.2,
                            zone=51.9, zonesw=67.9, chase=27.6, edge=43.0, fps=31.2, swing=48.5, whiff=23.4),
    "Wyatt Langford":  dict(pos="LF", bbe=64,  la=14.1, ev=91.0, hardhit=37.5, xwoba=.282, xba=.247, xslg=.362, k=22.7, bb=4.5, sprint=28.1,
                            weak=1.6, topped=29.7, under=29.7, flare=28.1, solid=4.7, barrel=6.3,
                            zone=49.6, zonesw=66.9, chase=28.6, edge=43.5, fps=34.1, swing=47.6, whiff=26.1),
    "Ezequiel Duran":  dict(pos="2B", bbe=133, la=10.8, ev=90.3, hardhit=39.8, xwoba=.312, xba=.258, xslg=.378, k=23.6, bb=8.2, sprint=28.8,
                            weak=1.5, topped=37.6, under=21.8, flare=24.1, solid=7.5, barrel=6.1,
                            zone=45.8, zonesw=64.3, chase=36.4, edge=38.9, fps=36.4, swing=49.2, whiff=29.7),
    "Jake Burger":     dict(pos="1B", bbe=163, la=12.7, ev=90.5, hardhit=48.5, xwoba=.304, xba=.239, xslg=.403, k=27.0, bb=6.9, sprint=27.1,
                            weak=4.3, topped=32.5, under=23.3, flare=20.9, solid=9.8, barrel=9.2,
                            zone=None, zonesw=None, chase=None, edge=None, fps=None, swing=None, whiff=None),
    "Evan Carter":     dict(pos="CF", bbe=131, la=19.1, ev=87.8, hardhit=38.9, xwoba=.295, xba=.196, xslg=.337, k=23.3, bb=12.9, sprint=28.9,
                            weak=8.4, topped=26.0, under=34.4, flare=15.3, solid=8.4, barrel=7.6,
                            zone=None, zonesw=None, chase=None, edge=None, fps=None, swing=None, whiff=None),
    "Kyle Higashioka": dict(pos="C",  bbe=69,  la=15.8, ev=89.4, hardhit=40.6, xwoba=.314, xba=.241, xslg=.394, k=27.3, bb=9.1, sprint=24.6,
                            weak=5.8, topped=27.5, under=24.6, flare=27.5, solid=2.9, barrel=11.6,
                            zone=None, zonesw=None, chase=None, edge=None, fps=None, swing=None, whiff=None),
}

# Batting order (top of lineup gets more PAs and more 3rd-time-through looks).
LINEUP_ORDER = [
    "Joc Pederson", "Corey Seager", "Josh Jung", "Brandon Nimmo",
    "Wyatt Langford", "Ezequiel Duran", "Jake Burger", "Evan Carter", "Kyle Higashioka",
]

# ---- OPPOSING PITCHER ----------------------------------------------------
# Tanner Bibee. Arsenal usage % + contact-quality allowed.
PITCHER = dict(
    name="Tanner Bibee",
    arsenal=dict(four_seam=25.7, sinker=17.6, cutter=25.3, slider=0.0,
                 change=19.4, curve=10.1, split=0.0, kn=0.0),
    # Contact quality allowed (Statcast Batted Ball Profile, pitcher row)
    weak=1.9, topped=29.5, under=27.5, flare=23.7, solid=6.8, barrel=10.6,
)

# Pitch families grouped by typical zone tendency, for the matchup model.
# "rise" = elevated hard stuff hitters can lift; "sink" = down-in-zone movers.
PITCH_FAMILIES = {
    "rise": ["four_seam", "cutter"],     # ~51% of Bibee's pitches, work up/through
    "sink": ["sinker", "split"],          # down in zone, induce grounders
    "soft": ["change", "curve", "slider", "kn"],  # offspeed/breaking, expand zone
}

# League baselines (approx MLB-average for normalization & logistic intercept)
LEAGUE = dict(
    hr_per_pa=0.032,      # ~3.2% baseline HR/PA
    xbh_per_pa=0.076,     # doubles + triples + HR
    hit_per_pa=0.218,     # all hits
    tb_per_pa=0.360,      # total bases
    barrel=7.5, hardhit=40.0, ev=89.0, la=12.5, xslg=.400, k=22.5, whiff=24.5,
)

# Population moments the models standardize features against — mean and SD of
# each rate stat over real plate appearances (data/backtest.csv joined to the
# season batter board, n=110,256 PAs). PA-weighted on purpose: the fitted
# coefficients are estimated on PA rows, so the scale has to match.
#
# The alternative, and what the code did before, is to take the moments from
# whoever happens to be in HITTERS. Once predict_today swaps a live lineup in,
# that population is nine hitters, and nine-hitter SDs are far tighter than the
# league's — which both inflates every z-score and puts fitted coefficients on
# the wrong scale. ingest_live overrides these when a fit supplies its own.
LEAGUE_MOMENTS = {
    "barrel":  (8.4226, 4.7857),
    "xslg":    (0.4067, 0.0806),
    "hardhit": (39.8741, 8.9872),
    "la":      (13.8153, 4.9752),
    "ev":      (89.0803, 2.5782),
    "whiff":   (24.6174, 6.4724),
    "k":       (21.6342, 6.4604),
    "xwoba":   (0.3230, 0.0440),
    "xba":     (0.2485, 0.0334),
    "chase":   (30.1562, 6.4647),
    "bb":      (9.2012, 3.6761),
}
