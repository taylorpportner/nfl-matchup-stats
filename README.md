# nfl-matchup-stats

Team and quarterback efficiency numbers for upcoming NFL games, rebuilt
daily from [nflverse](https://github.com/nflverse/nflverse-data)
play-by-play and published as one JSON file:

`data/nfl_matchups.json`

## What's in it

Per team, offense and defense, with a league rank (1 = best; for pace,
1 = fastest; for pass rate over expected, 1 = most pass-heavy):

| Group | Metrics |
|---|---|
| Spread | EPA/play, success rate, dropback EPA, rush EPA |
| Total | neutral-situation seconds per play, pass rate over expected, explosive play rate (20+ yards) |
| Noisy | giveaways / takeaways per game, 3rd-down rate, red-zone TD rate, points for / against per game |

"Neutral" = quarters 1–3 with the score within 7. Kneels, spikes, and
two-point tries are excluded.

Quarterbacks: EPA per dropback and CPOE (min. 50 dropbacks).

Schedule: games in the next 10 days with rest days, roof (falling back
to the stadium's last known roof), surface, division game, and listed
starting quarterbacks.

## Blending

Last season counts as 4 games of evidence, so the current season's
numbers take over as games are played (50/50 after 4 games). A
quarterback's prior season counts as up to 150 dropbacks.

## Data

All data comes from nflverse. Please credit them if you use it.
