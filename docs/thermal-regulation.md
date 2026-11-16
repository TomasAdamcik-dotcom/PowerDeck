# Temperature-regulated CPU limit (Rucksack)

Status: implemented in v0.4, verified on the T14s Gen 4 at the desk and in a bag on battery (2026-11-16).

## Why

Rucksack used to cap the CPU statically (turbo off, max 50 %). With turbo off intel_pstate
lowers `cpuinfo_max_freq` to the base clock (1.1 GHz on the T14s Gen 4), so 50 % meant about
550 MHz all the time, even when the laptop was cool. A regulator gives the CPU as much power as
keeps it at a target temperature and no more.

## Design

| Part | What it does |
|---|---|
| Profile key `cpu.thermal_target_celsius` | `55`–`90` = regulate to this °C, `null` = no regulation, missing = leave unmanaged |
| `powerdeck-helper set-thermal-target N` / `clear-thermal-target` | Starts `powerdeck-thermal@N.service` (after stopping any other instance) / stops every instance. Allowlisted like the other helper commands. |
| `/usr/libexec/powerdeck-thermal` (`powerdeck/thermal_limit.py`) | Root daemon, stdlib only, `python3 -I`. Every 2 s it reads the package temperature and moves one *level* (fraction of the original limit, 0.1–1.0). |
| `powerdeck-thermal@.service` | System template unit, never enabled (like the turbo setting, it is gone after a reboot). Hardened (`ProtectSystem=strict`, `ProtectHome`, `PrivateNetwork`, …). `ExecStopPost=… --restore` puts the original limits back even after a crash. |

Actuator, first one available:

1. **RAPL** (`/sys/class/powercap/intel-rapl:0` and `intel-rapl-mmio:0`, package domains): the
   long-term (PL1) and short-term (PL2) power limits. Both are lowered, so the cap acts at once
   instead of over the 128 s PL1 averaging window. Floor 5 W. PL2 is also never above the PL1
   now in effect in the same domain (even at level 1.0): with power-saver the MMIO PL1 is 11 W
   but PL2 stayed 55 W, and in the bag test that turbo burst reached 84 °C in seconds, before
   the controller could react, so the guard suspended the laptop.
2. **scaling_max_freq** of every core (AMD or anything without writable RAPL). Floor
   `scaling_min_freq`.

Controller (level `f`, original limit × `f`), a PI controller in velocity form per 2 s tick:

    Δf = −0.005 × (T − target) − 0.03 × (T − T_previous),   |Δf| ≤ 0.2

The second term stops cutting power once the temperature already falls. The package lags the
power limit by a few seconds; the first, purely proportional controller (and a variant with a
capped step) kept cutting until the 5 W floor after a load spike (82–94 °C) on the T14s. Gains
were chosen on a two-node thermal model fitted to those runs (`test_load_spike_does_not_overshoot_to_the_floor`);
in the model the limit stays above ~8 W after a 94 °C spike (before: the 5 W floor) and the
temperature settles within 1 °C, at the price of ~20 s above 72 °C right after the spike;
higher gains oscillated when the lag was longer.

Side effects: a temperature that rises below the target already trims the limit a little, and
a steady 1 °C above target lowers it only slowly (0.005 per tick); 10 °C above still reaches the
floor in about 36 s.

Each tick it also checks the value it wrote last. If firmware (power profile change, resume)
raised it, the new value becomes the original. If firmware lowered it (lap mode, power profile),
the regulator never goes above that value, but it is not what gets restored on stop.

The state file is written before the first limit changes, so `--restore` (ExecStopPost) can undo
a killed daemon at any point, also for limits left exactly at the 5 W floor. The helper holds
`/run/powerdeck-thermal.lock` (flock) around stop + start, so two parallel calls never leave two
instances fighting over the same limits. Targets are canonical (`65`, not `065`).

The daemon writes `/run/powerdeck-thermal/state.json` (0644: target, level, limit, original
values). The app reads it to show the regulation in `status` / `monitor` and to know what to
revert to. `--restore` only writes originals to paths the daemon itself discovers.

## Profiles

- **Rucksack**: turbo on, max frequency 100 %, target 65 °C, guard 70/82 °C kept as a safety net.
- **Default, Travel, Max Performance**: `thermal_target_celsius: null` (switching to them stops
  the regulation, like Travel already lifts Rucksack's frequency cap).
- Without RAPL or cpufreq (e.g. the VM) the key is skipped like turbo on a machine without turbo.

## Security (CLAUDE.md root rules)

- The helper accepts only `set-thermal-target <55–90>` and `clear-thermal-target`.
- The daemon re-validates its instance argument, writes only sysfs files it discovers itself and
  its own state file in a root-owned runtime directory; nothing comes from user-writable input.
- Uninstall: `uninstall-reset.sh` runs `clear-thermal-target`; `install-helper.sh --uninstall`
  and the .deb remove the daemon and the unit.

## Tests

Unit tests with fake powercap / cpufreq / thermal trees: discovery, controller steps, firmware
change, restore, helper allowlist, profile validation, plan and revert, root-rule checks
(shebang, unit `ExecStart`). Hardware: Rucksack on the T14s with a load (`stress-ng` or a build),
watch `powerdeck monitor`, then `restore`.
