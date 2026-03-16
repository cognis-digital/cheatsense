# Demo 01 - Basic scan

This demo analyzes a small game session log (`session.jsonl`) containing
input events from three players. Each line is one JSON input event with a
player id, a monotonic timestamp `t` (seconds), and optional `reaction`,
`yaw`/`pitch`, and `hit` fields.

## The cast

- **honest_hank** - normal human play. Reaction times around 200-300ms,
  irregular action timing, modest view changes. Should NOT be flagged.

- **bot_betty** - an autoclicker / macro. Fires actions on a metronome:
  inter-action intervals are nearly identical (very low timing variance)
  and many are byte-identical to the millisecond. Trips
  `robotic_cadence` and `autoclicker_interval`.

- **aimbot_alice** - an aim assistant. Reacts faster than the human floor
  (<100ms) and her view yaw teleports ~150 degrees onto a target the same
  tick she lands a hit. Trips `inhuman_reaction` and `aim_snap`.

## Run it

```
python -m cheatsense scan demos/01-basic/session.jsonl
python -m cheatsense scan demos/01-basic/session.jsonl --format json
```

## Expected result

- `honest_hank` -> score 0, not flagged.
- `bot_betty` -> flagged (robotic_cadence + autoclicker_interval).
- `aimbot_alice` -> flagged (inhuman_reaction + aim_snap).
- `flagged_count` = 2, so the process exits with code **1** (CI gate trips).
