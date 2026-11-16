# Temperature-regulated CPU limit (Rucksack)

Status: implemented on branch `thermal-regulation` (TODO 2c). Hardware test on the T14s pending.

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
   instead of over the 128 s PL1 averaging window. Floor 5 W.
2. **scaling_max_freq** of every core (AMD or anything without writable RAPL). Floor
   `scaling_min_freq`.

Controller (level `f`, original limit × `f`):

- temperature above target: `f -= 0.02 × max(1, T − target)` (fast, proportional)
- temperature below target − 3 °C: `f += 0.02` (slow, about 1 W / 100 MHz per 2 s)
- otherwise: hold.

Each tick it also checks the value it wrote last; if firmware (power profile change, resume)
changed it, the new value becomes the original and the cap is written again.

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
