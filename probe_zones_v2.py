"""
Probe LeagueDashPlayerShotLocations — the endpoint that powers NBA.com's
'Shooting by Zone' page. Returns one row per player with per-zone splits
as separate columns.
"""
from nba_api.stats.endpoints import LeagueDashPlayerShotLocations

SEASON = "2025-26"

print(f"Pulling LeagueDashPlayerShotLocations for {SEASON}...\n")

try:
    result = LeagueDashPlayerShotLocations(
        season=SEASON,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
        distance_range="By Zone",
        timeout=60,
    )
    dfs = result.get_data_frames()
    print(f"✅ Got {len(dfs)} DataFrame(s)")

    for i, df in enumerate(dfs):
        print(f"\nDataFrame [{i}] shape: {df.shape}")
        print(f"Columns ({len(df.columns)}):")
        for col in df.columns:
            print(f"  {col}")

        # Show one superstar's full row
        stars = df[df["PLAYER_NAME"].isin(["Luka Dončić", "Nikola Jokić", "Norman Powell"])] if "PLAYER_NAME" in df.columns else df.head(0)
        if len(stars):
            print(f"\nSample star rows:")
            print(stars.to_string())
except Exception as exc:
    print(f"❌ {type(exc).__name__}: {exc}")
