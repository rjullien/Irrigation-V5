# Fix: delayed open cascade on laggy valves (Tuya)

## Incident (2026-07-24, Eyguians)

Scheduled start at 21:30 CEST. Software zones advanced every ~5s
(`latency: 5`, `continue_on_unexpected_state: false`) because Tuya did not
confirm `open` in time. Hardware then opened valves 1/3/4/5 nearly together
(opens applied 12–25s late). Valve 1 stayed open after the program ended.

## Root cause

`async_solenoid_turn_off` skipped `close_valve` when HA still reported
`closed`. After `open_valve` was sent but not yet reflected, abort → no close
→ in-flight opens stacked across zones.

This is independent of resume-after-reboot; resume can worsen orphans via
`skip_startup_off` if a similar race happens across a restart.

## Fix (V2026.07.24.1)

1. Track `_solenoid_commanded_open` / `_solenoid_open_confirmed`.
2. Always send close when open was commanded, even if state still looks closed.
3. On abort with unconfirmed open: settle ~30s, re-close if a delayed open appears.
4. Confirmed watering cycles keep the short latency close path.

## Ops note

With flaky Tuya Wi‑Fi, prefer raising program **latency** (e.g. 15–30s) so
zones do not abort before the controller acknowledges.
