"""
Interactive SpreadCycle Chopper Timing Tuner for MicroPython

Tunes TMC2209 CHOPCONF parameters (TOFF, HSTRT, HEND, TBL) for a selected axis (X, Y, or Z).
Tests motion in SpreadCycle mode with relative back-and-forth moves near the center of travel.

Usage in MicroPython REPL:
    from tests.test_spreadcycle import main
    main()
    # or
    from tests.test_spreadcycle import tune_spreadcycle
    tune_spreadcycle(axis="y", speed=3.0, dist=3.0)
"""

import sys
import time
import json
from tmc import reg
from tools import lh


def apply_chopper_config(tmc, toff, hstrt, hend, tbl):
    """Directly sets TOFF, HSTRT, HEND, and TBL in CHOPCONF over UART."""
    chopconf = tmc.tmc_uart.read_u32(reg.CHOPCONF)
    # Clear old bits: toff[3:0], hstrt[6:4], hend[10:7], tbl[16:15]
    mask = 0x0F | (0x07 << 4) | (0x0F << 7) | (0x03 << 15)
    chopconf &= ~mask
    chopconf |= (int(toff) & 0x0F)
    chopconf |= (int(hstrt) & 0x07) << 4
    chopconf |= (int(hend) & 0x0F) << 7
    chopconf |= (int(tbl) & 0x03) << 15
    tmc.tmc_uart.write_reg_check(reg.CHOPCONF, chopconf)


def read_chopper_config(tmc):
    """Directly reads back TOFF, HSTRT, HEND, and TBL from CHOPCONF."""
    chopconf = tmc.tmc_uart.read_u32(reg.CHOPCONF)
    return {
        "toff": chopconf & 0x0F,
        "hstrt": (chopconf >> 4) & 0x07,
        "hend": (chopconf >> 7) & 0x0F,
        "tbl": (chopconf >> 15) & 0x03,
    }

try:
    from control import constants
except ImportError:
    constants = None


PRESETS = [
    {"name": "Trinamic Default", "toff": 3, "hstrt": 4, "hend": 1, "tbl": 2},
    {"name": "High Torque (24V)", "toff": 4, "hstrt": 5, "hend": 0, "tbl": 1},
    {"name": "Smooth / Damped", "toff": 3, "hstrt": 5, "hend": 1, "tbl": 2},
    {"name": "High Inductance Leadscrew", "toff": 5, "hstrt": 4, "hend": 2, "tbl": 2},
    {"name": "Low TOFF / Fast", "toff": 2, "hstrt": 4, "hend": 1, "tbl": 2},
    {"name": "Wide Hysteresis", "toff": 4, "hstrt": 3, "hend": 3, "tbl": 1},
    {"name": "Balanced Medium", "toff": 3, "hstrt": 3, "hend": 0, "tbl": 1},
]


def generate_grid_candidates(exclude_presets):
    """Generates structured parametric grid candidates excluding already tested presets."""
    candidates = []
    seen = set((p["toff"], p["hstrt"], p["hend"], p["tbl"]) for p in exclude_presets)

    # Systematic sweep: TOFF -> HSTRT -> HEND -> TBL
    for toff in [3, 4, 2, 5]:
        for hstrt in [4, 5, 2, 6]:
            for hend in [1, 0, 2, 3]:
                # Check Trinamic constraint: HSTRT + HEND <= 16
                if hstrt + hend > 16:
                    continue
                for tbl in [2, 1]:
                    key = (toff, hstrt, hend, tbl)
                    if key not in seen:
                        seen.add(key)
                        candidates.append({
                            "name": f"Grid TOFF={toff} HSTRT={hstrt} HEND={hend} TBL={tbl}",
                            "toff": toff,
                            "hstrt": hstrt,
                            "hend": hend,
                            "tbl": tbl,
                        })
    return candidates


def parse_args():
    axis = None
    speed = 3.0
    dist = 3.0

    args = getattr(sys, "argv", [])[1:]
    i = 0
    while i < len(args):
        if args[i] == "--axis" and i + 1 < len(args):
            axis = args[i + 1].lower()
            i += 2
        elif args[i] == "--speed" and i + 1 < len(args):
            try:
                speed = float(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif args[i] == "--dist" and i + 1 < len(args):
            try:
                dist = float(args[i + 1])
            except ValueError:
                pass
            i += 2
        else:
            i += 1

    return axis, speed, dist


def select_axis_interactively():
    print("\n==========================================")
    print("      TMC2209 SpreadCycle Auto-Tuner      ")
    print("==========================================")
    while True:
        try:
            choice = input("Select axis to tune (x / y / z) [default: y]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return None
        if not choice:
            return "y"
        if choice in ["x", "y", "z"]:
            return choice
        print("Invalid axis. Please choose 'x', 'y', or 'z'.")


def disable_all_motors():
    """Immediately cut power to all steppers."""
    try:
        lh.enable_steppers = False
    except Exception:
        pass
    if hasattr(lh, "steppers") and lh.steppers:
        for s in lh.steppers.values():
            try:
                s.motor_enabled = False
            except Exception:
                pass


def tune_spreadcycle(axis=None, speed=3.0, dist=3.0):
    """
    Main tuning routine. Can be called directly from MicroPython REPL.
    """
    try:
        _run_tune_spreadcycle(axis=axis, speed=speed, dist=dist)
    except KeyboardInterrupt:
        print("\n\n[!] ABORT: Emergency stop (Ctrl+C).")
    finally:
        disable_all_motors()
        print("All steppers disabled.")


def _run_tune_spreadcycle(axis=None, speed=3.0, dist=3.0):
    if axis is None or axis not in ["x", "y", "z"]:
        axis = select_axis_interactively()
        if axis is None:
            return

    if not hasattr(lh, "steppers") or axis not in lh.steppers:
        print(f"Error: Stepper driver for axis '{axis}' is not available on lh.steppers.")
        return

    tmc = lh.steppers[axis]

    print(f"\n--- Initializing Tuning Session for Axis: {axis.upper()} ---")
    print(f"Test Feedrate: {speed:.1f} mm/s")
    print(f"Test Relative Displacement: +/- {dist:.1f} mm")
    print("\n[IMPORTANT] Ensure the stage is positioned near the middle of its travel range!")
    try:
        input("Press Enter to begin...")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        return

    # ---------------------------------------------------------
    # Step 0: Baseline StealthChop Verification (Sanity Check)
    # ---------------------------------------------------------
    print("\n------------------------------------------------------------")
    print(f"[Sanity Check] Verifying baseline motion on {axis.upper()} in StealthChop...")
    print("------------------------------------------------------------")

    lh.enable_steppers = True
    tmc.motor_enabled = True
    tmc.spread_cycle = False
    tmc.clearGSTAT()

    axis_index = {"x": 0, "y": 1, "z": 2}[axis]
    vec_fwd = [0.0, 0.0, 0.0]
    vec_fwd[axis_index] = dist
    vec_bwd = [0.0, 0.0, 0.0]
    vec_bwd[axis_index] = -dist

    while True:
        print(f"Executing baseline move in StealthChop (+{dist} mm / -{dist} mm)...")
        lh.gotopoint(vec_fwd, speed=speed, absolute=False, check_sensors=False, validate_limits=False)
        time.sleep(0.1)
        lh.gotopoint(vec_bwd, speed=speed, absolute=False, check_sensors=False, validate_limits=False)
        time.sleep(0.05)

        try:
            res = input("\nDid the motor move properly in StealthChop? [y=Yes / n=No / r=Retry / a=Abort]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            lh.enable_steppers = False
            return

        if res == "y":
            print("Baseline motion confirmed! Proceeding to SpreadCycle tuning...")
            break
        elif res == "r":
            continue
        elif res in ["n", "a"]:
            print("\n[!] Baseline movement failed in StealthChop mode.")
            print("    Please check motor power (VMOT), current (mA), wiring, or mechanical binding first.")
            lh.enable_steppers = False
            return

    # ---------------------------------------------------------
    # Step 1: SpreadCycle Tuning
    # ---------------------------------------------------------
    tmc.spread_cycle = True
    print(f"\nEnabled SpreadCycle on axis {axis.upper()}. Starting preset trials...")

    tested_results = []
    candidates = list(PRESETS)
    grid_candidates = None

    idx = 0
    while idx < len(candidates):
        cand = candidates[idx]
        print("\n------------------------------------------------------------")
        print(f"Testing Candidate [{idx + 1}/{len(candidates)}]: {cand['name']}")
        print(f"Parameters: TOFF={cand['toff']}, HSTRT={cand['hstrt']}, HEND={cand['hend']}, TBL={cand['tbl']}")

        # 1. Clear faults and apply chopper settings
        try:
            tmc.clearGSTAT()
            apply_chopper_config(
                tmc,
                toff=cand["toff"],
                hstrt=cand["hstrt"],
                hend=cand["hend"],
                tbl=cand["tbl"],
            )
            # Verify readback
            rb = read_chopper_config(tmc)
            print(f"Register Readback: TOFF={rb['toff']}, HSTRT={rb['hstrt']}, HEND={rb['hend']}, TBL={rb['tbl']}")
        except Exception as e:
            print(f"Error applying chopper settings ({type(e).__name__}): {e}")
            try:
                choice = input("[r]etry or [s]kip? ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                break
            if choice == "r":
                continue
            idx += 1
            continue

        # 2. Execute test move
        lh.enable_steppers = True
        time.sleep(0.05)

        vec_fwd = [0.0, 0.0, 0.0]
        vec_fwd[axis_index] = dist
        vec_bwd = [0.0, 0.0, 0.0]
        vec_bwd[axis_index] = -dist

        print(f"Moving +{dist} mm...")
        lh.gotopoint(vec_fwd, speed=speed, absolute=False, check_sensors=False, validate_limits=False)
        time.sleep(0.1)

        print(f"Moving -{dist} mm...")
        lh.gotopoint(vec_bwd, speed=speed, absolute=False, check_sensors=False, validate_limits=False)
        time.sleep(0.05)

        # 3. Check driver diagnostics
        try:
            drv = tmc.drvstatus_parsed
            gstat = tmc.gstat
            if drv.get("error_150c") or drv.get("short_to_gnd_a") or drv.get("short_to_gnd_b") or (gstat & 0x02):
                print(f"[!] Driver Fault Reported by TMC2209: DRVSTATUS={drv}, GSTAT={hex(gstat)}")
                print("Auto-marking as FAILED due to hardware fault flag.")
                tested_results.append({"cand": cand, "score": 3, "status": "Hardware Fault"})
                tmc.clearGSTAT()
                idx += 1
                continue
        except Exception as e:
            print(f"Warning reading driver diagnostics: {e}")

        # 4. User Evaluation
        while True:
            prompt = (
                "\nResult for this configuration:\n"
                "  [1] Smooth & Good\n"
                "  [2] Moved, but noisy/vibrating\n"
                "  [3] Failed / Stalled / Locked up\n"
                "  [r] Re-test this candidate\n"
                "  [a] Abort and show results\n"
                "Choice [1/2/3/r/a]: "
            )
            try:
                ans = input(prompt).strip().lower()
            except (KeyboardInterrupt, EOFError):
                ans = "a"

            if ans in ["1", "2", "3", "r", "a"]:
                break

        if ans == "r":
            continue
        elif ans == "a":
            break
        elif ans in ["1", "2", "3"]:
            score = int(ans)
            status_text = {1: "Smooth & Good", 2: "Noisy/Vibrating", 3: "Failed/Stalled"}[score]
            tested_results.append({"cand": cand, "score": score, "status": status_text})
            idx += 1

        # If we finished presets and user wants to explore more, generate grid
        if idx >= len(candidates) and grid_candidates is None:
            try:
                ans = input("\nTested all standard presets! Explore deeper parametric grid? (y/n) [default: n]: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                ans = "n"
            if ans == "y":
                grid_candidates = generate_grid_candidates(PRESETS)
                candidates.extend(grid_candidates)
                print(f"Added {len(grid_candidates)} grid candidates to the queue.")

    # Show Final Summary
    print("\n============================================================")
    print("                    TUNING RESULTS SUMMARY                  ")
    print("============================================================")
    if not tested_results:
        print("No configurations were evaluated.")
        return

    working = [r for r in tested_results if r["score"] in [1, 2]]
    working.sort(key=lambda x: x["score"])

    if not working:
        print("No working SpreadCycle configurations found.")
        print("Consider checking current (mA), mechanical friction, or staying with StealthChop.")
    else:
        print(f"Found {len(working)} working configuration(s):")
        for i, r in enumerate(working, 1):
            c = r["cand"]
            tag = "PERFECT" if r["score"] == 1 else "NOISY"
            print(f" [{i}] [{tag}] {c['name']} -> TOFF={c['toff']}, HSTRT={c['hstrt']}, HEND={c['hend']}, TBL={c['tbl']}")

        try:
            choice = input("\nSave one of these to config.json? Enter candidate # (or Enter to skip): ").strip()
        except (KeyboardInterrupt, EOFError):
            choice = ""

        if choice.isdigit() and 1 <= int(choice) <= len(working):
            selected = working[int(choice) - 1]["cand"]
            save_to_config(axis, selected)

    # Disable steppers after tuning session
    lh.enable_steppers = False


def save_to_config(axis, cand):
    # 1. Update in-memory constants.CONFIG if present
    if constants is not None and hasattr(constants, "CONFIG"):
        try:
            motors_cfg = constants.CONFIG.setdefault("motors", {})
            ax_cfg = motors_cfg.setdefault(axis, {})
            ax_cfg["spread_cycle"] = True
            ax_cfg["chopper_timings"] = {
                "toff": cand["toff"],
                "hstrt": cand["hstrt"],
                "hend": cand["hend"],
                "tbl": cand["tbl"],
            }
            constants.update_config()
            print(f"Updated live constants.CONFIG for axis '{axis}'.")
        except Exception as e:
            print(f"Warning updating constants.CONFIG: {e}")

    # 2. Update config.json file directly
    possible_paths = ["config.json", "src/root/config.json", "src/root/mock_config.json"]
    saved = False
    for path in possible_paths:
        try:
            with open(path, "r") as f:
                cfg = json.load(f)

            if "motors" not in cfg:
                cfg["motors"] = {}
            if axis not in cfg["motors"]:
                cfg["motors"][axis] = {}

            cfg["motors"][axis]["spread_cycle"] = True
            cfg["motors"][axis]["chopper_timings"] = {
                "toff": cand["toff"],
                "hstrt": cand["hstrt"],
                "hend": cand["hend"],
                "tbl": cand["tbl"],
            }

            with open(path, "w") as f:
                json.dump(cfg, f)

            print(f"Successfully saved configuration to '{path}'!")
            saved = True
            break
        except Exception:
            continue

    if not saved:
        print("Note: Could not automatically locate config.json file on disk to persist.")


def main():
    cli_axis, speed, dist = parse_args()
    tune_spreadcycle(axis=cli_axis, speed=speed, dist=dist)


if __name__ == "__main__":
    main()
