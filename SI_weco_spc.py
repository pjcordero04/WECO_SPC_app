"""
WECO SPC GUI v2 — Statistical Process Control using the 4 Western Electric Company rules.

Changes from v1:
  - Flashing red banner replaces messagebox modal (more visible, requires ACKNOWLEDGE click)
  - No pop-up plot windows — charts are saved to spc_plots/ silently
  - Once a unit (SN) triggers SPC on one parameter, it won't trigger again on subsequent parameters

WECO Rules:
  1. One point is more than 3 standard deviations from the centerline.
  2. Eight or more consecutive points are on the same side of the centerline.
  3. Two out of three consecutive points are more than 2σ from the centerline (same side).
  4. Four out of five consecutive points are more than 1σ from the centerline (same side).
"""

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend — no plot windows
import matplotlib.pyplot as plt
from datetime import datetime
import os
import re
import json
import tkinter as tk
from tkinter import filedialog, messagebox, font
import pandas as pd
import numpy as np
from pathlib import Path
import zipfile
import csv
import hashlib

# Base directory for SPC output (images + CSV). A subfolder per part number is created automatically.
SPC_OUTPUT_BASE = r"C:\TESTDATA\SPC"

# --- Fourport tester config paths (for loading parameter lists from limits JSON) ---
FOURPORT_CONFIG_BASE = r"C:\FourPortTester\config\constellation_config\controller"
FOURPORT_PARTS_DIR = os.path.join(FOURPORT_CONFIG_BASE, "parts")
FOURPORT_LIMITS_DIR = os.path.join(FOURPORT_CONFIG_BASE, "limits")

# --- OPEN detection settings ---
OPEN_GATE_PREFIXES = ("S2_1", "S4_3")
OPEN_THRESHOLD = -50.0  # if any S2_1/S4_3 Result < -50, treat unit as OPEN and invalidate downstream parameters


def load_parameters_from_limits(part_number):
    """
    Load the parameter list from the fourport tester limits JSON.

    Lookup path:
      1. parts/<first_6_digits>/<part_number>.json  →  read "cable" → "limits" field
      2. limits/<limits_name>.json  →  extract top-level keys under "limits"

    Returns a list of parameter family names (e.g. ["S2_1", "SDD11", "ZDD11", ...])
    or None if lookup fails.
    """
    if not part_number or len(part_number) < 6:
        return None

    prefix = part_number[:6]
    subfolder = os.path.join(FOURPORT_PARTS_DIR, prefix)

    if not os.path.isdir(subfolder):
        return None

    # Search for a JSON file matching the part number (case-insensitive)
    part_json_path = None
    try:
        for fname in os.listdir(subfolder):
            if fname.lower().endswith('.json'):
                name_no_ext = os.path.splitext(fname)[0]
                if name_no_ext.lower() == part_number.lower():
                    part_json_path = os.path.join(subfolder, fname)
                    break
    except OSError:
        return None

    if not part_json_path:
        return None

    # Read part JSON to get limits filename
    try:
        with open(part_json_path, 'r', encoding='utf-8') as f:
            part_config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error reading part config {part_json_path}: {e}")
        return None

    # Extract limits name from cable.limits
    limits_name = None
    cable = part_config.get("cable", {})
    if isinstance(cable, dict):
        limits_name = cable.get("limits")

    if not limits_name:
        return None

    # Open the limits JSON
    limits_json_path = os.path.join(FOURPORT_LIMITS_DIR, f"{limits_name}.json")
    if not os.path.isfile(limits_json_path):
        print(f"Limits file not found: {limits_json_path}")
        return None

    try:
        with open(limits_json_path, 'r', encoding='utf-8') as f:
            limits_config = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error reading limits file {limits_json_path}: {e}")
        return None

    # Extract parameter families (top-level keys under "limits")
    limits_dict = limits_config.get("limits", {})
    if not isinstance(limits_dict, dict):
        return None

    return list(limits_dict.keys())


# --- Helpers from your summary.txt parser ---
def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def extract_first_number(s):
    if s is None:
        return None
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(s))
    return float(m.group(0)) if m else None


def extract_unit(range_str):
    match = re.search(r"\)\s*([a-zA-Z]+)", str(range_str))
    return match.group(1) if match else ""


def parse_summary_csv(file_path):
    """
    Parse a summary CSV file containing pre-computed Mean and Std Dev per parameter.

    The CSV has rows as statistics (index column: Statistic, Unit, Count, Mean, Std Dev, etc.)
    and columns as parameter names.

    Returns: {param_name: {"mean": float, "std": float}} dict.
    Skips parameters where Mean or Std Dev is "None" or not parseable.
    """
    try:
        df = pd.read_csv(file_path, index_col=0)
    except Exception as e:
        print(f"Error reading summary CSV {file_path}: {e}")
        return {}

    result = {}
    for col in df.columns:
        try:
            mean_val = df.loc["Mean", col] if "Mean" in df.index else None
            std_val = df.loc["Std Dev", col] if "Std Dev" in df.index else None

            # Handle "None" string values gracefully
            if mean_val is None or str(mean_val).strip().lower() == "none":
                continue
            if std_val is None or str(std_val).strip().lower() == "none":
                continue

            mean_f = float(mean_val)
            std_f = float(std_val)
            result[col] = {"mean": mean_f, "std": std_f}
        except (ValueError, TypeError, KeyError):
            continue

    return result


def extract_sn_from_text(text):
    for line in text.splitlines()[:5]:
        match = re.search(r"S/N:\s*(\S+)", line)
        if match:
            return match.group(1)
    match = re.search(r"S/N:\s*(\S+)", text)
    return match.group(1) if match else "UNKNOWN"


def parse_summary_text(text, source_label=None):
    data = []
    sn = extract_sn_from_text(text)
    stop_marker = "Background Limit Results"
    overall_result = "UNKNOWN"
    lines = text.splitlines()

    # Primary: use the last non-empty line (expected format: "Result  PASS").
    for line in reversed(lines):
        if line.strip():
            m = re.match(r"^\s*Result\s+(PASS|FAIL)\s*$", line.strip(), flags=re.IGNORECASE)
            if m:
                overall_result = m.group(1).upper()
            break

    # Fallback: if footer line is missing/unexpected, scan from bottom for first valid Result line.
    if overall_result == "UNKNOWN":
        for line in reversed(lines):
            m = re.match(r"^\s*Result\s+(PASS|FAIL)\s*$", line.strip(), flags=re.IGNORECASE)
            if m:
                overall_result = m.group(1).upper()
                break

    # Extract test start timestamp
    test_time = None
    for line in text.splitlines():
        if line.strip().startswith("Started test at"):
            m = re.search(r"Started test at (.+) \(Local Time\)", line)
            if m:
                test_time = pd.to_datetime(
                    m.group(1),
                    format="%a %b %d %H:%M:%S %Y",
                    errors="coerce"
                )
            break

    for line in text.splitlines():
        if stop_marker.lower() in line.lower():
            break
        if ":" not in line:
            continue
        parts = [p.strip() for p in line.split(":")]
        if len(parts) < 9:
            continue
        try:
            parameter = f"{parts[0]} | {parts[1]} | {parts[2]} | {parts[3]} | {parts[6]}"
            value = safe_float(parts[4])
            result = parts[5]
            lower = extract_first_number(parts[7])
            upper = extract_first_number(parts[8])
            unit = extract_unit(parts[6])
            data.append({
                "SN": sn,
                "Parameter": parameter,
                "Lower Limit": lower,
                "Result": value,
                "Upper Limit": upper,
                "Pass/Fail": result,
                "Overall Result": overall_result,
                "Unit": unit,
                "Source": source_label,
                "Test Time": test_time
            })
        except Exception:
            continue

    return pd.DataFrame(
        data,
        columns=[
            "SN", "Parameter", "Lower Limit", "Result", "Upper Limit",
            "Pass/Fail", "Overall Result", "Unit", "Source", "Test Time"
        ]
    )


def sanitize_filename_component(value):
    """Return a Windows-safe filename fragment."""
    text = str(value) if value is not None else ""
    text = re.sub(r'[<>:"/\\|?*]', '_', text).strip().rstrip('.')
    return text or "UNKNOWN"


def log_violation_to_csv(sn, parameter, rule_number, part_number):
    """Append a violation record to the part-number-specific CSV log file."""
    try:
        output_dir = os.path.join(SPC_OUTPUT_BASE, part_number)
        os.makedirs(output_dir, exist_ok=True)
        csv_filename = f"{part_number}_spc_violations.csv"
        csv_path = os.path.join(output_dir, csv_filename)
        file_exists = os.path.isfile(csv_path)
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["SN", "Parameter", "Rule", "Timestamp"])
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            writer.writerow([sn, parameter, rule_number, timestamp])
    except PermissionError as e:
        print(f"PermissionError writing violation CSV: {e}")
    except Exception as e:
        print(f"Error writing violation CSV: {e}")


def parameter_prefix(param: str) -> str:
    """Extract the parameter family/prefix from your composite 'Parameter' field."""
    return param.split(" | ")[0].strip() if " | " in str(param) else str(param).strip()


def detect_open_sns(df: pd.DataFrame, threshold: float = OPEN_THRESHOLD) -> set:
    """
    OPEN definition:
      If any S2_1 or S4_3 row for an SN has Result < threshold, mark SN as OPEN.

    This is treated as an INVALID measurement condition for downstream parameters.
    """
    if df is None or df.empty:
        return set()

    fam = df["Parameter"].astype(str).str.split(" | ").str[0].str.strip()
    is_gate = fam.isin(list(OPEN_GATE_PREFIXES))

    vals = pd.to_numeric(df["Result"], errors="coerce")
    open_mask = is_gate & vals.notna() & (vals < float(threshold))

    return set(df.loc[open_mask, "SN"].dropna().tolist())


# --- WECO SPC Rule Checks ---
def evaluate_spc_rules(results, window, rules_enabled, mode, fixed_mean=None, fixed_std=None):
    """Evaluate WECO rules on a results series. Returns (violated, violating_indices)."""
    results = pd.to_numeric(results, errors="coerce").dropna().reset_index(drop=True)

    if mode == "fixed_limit":
        if len(results) < 1:
            return [], []
        centerline = pd.Series([fixed_mean] * len(results), index=results.index, dtype="float64")
        std_line = pd.Series([fixed_std] * len(results), index=results.index, dtype="float64")
        # For fixed_limit, evaluate from the start (no window skip)
        to_check = results.reset_index(drop=True)
        cl_check = centerline.reset_index(drop=True)
        std_check = std_line.reset_index(drop=True)
    else:
        if len(results) <= window:
            return [], []

        if mode == "moving_average":
            centerline = results.rolling(window=window).mean()
            std_line = results.rolling(window=window).std()
        elif mode == "fixed":
            baseline = results.iloc[:window]
            mean = baseline.mean()
            std = baseline.std(ddof=1) if len(baseline) > 1 else 0.0
            centerline = pd.Series(mean, index=results.index, dtype="float64")
            std_line = pd.Series(std, index=results.index, dtype="float64")
        else:  # grand_mean
            mean = results.mean()
            std = results.std(ddof=1) if len(results) > 1 else 0.0
            centerline = pd.Series(mean, index=results.index, dtype="float64")
            std_line = pd.Series(std, index=results.index, dtype="float64")

        to_check = results.iloc[window:].reset_index(drop=True)
        cl_check = centerline.iloc[window:].reset_index(drop=True)
        std_check = std_line.iloc[window:].reset_index(drop=True)

    valid = (~cl_check.isna()) & (~std_check.isna())

    upper_3s = cl_check + 3 * std_check
    lower_3s = cl_check - 3 * std_check
    upper_2s = cl_check + 2 * std_check
    lower_2s = cl_check - 2 * std_check
    upper_1s = cl_check + 1 * std_check
    lower_1s = cl_check - 1 * std_check

    violated = []
    violating_indices = []

    # Determine the offset for index mapping
    offset = 0 if mode == "fixed_limit" else window

    # WECO Rule 1: One point beyond 3σ
    if rules_enabled[0]:
        mask = valid & ((to_check > upper_3s) | (to_check < lower_3s))
        if mask.any():
            violated.append("WECO Rule 1: One point beyond 3σ from centerline")
            for j in np.flatnonzero(mask.to_numpy()):
                violating_indices.append(offset + int(j))

    # WECO Rule 2: 8+ consecutive points on the same side of centerline
    if rules_enabled[1]:
        run_above = 0
        run_below = 0
        hit = False
        for j in range(len(to_check)):
            if not bool(valid.iloc[j]):
                run_above = 0
                run_below = 0
                continue
            if to_check.iloc[j] > cl_check.iloc[j]:
                run_above += 1
                run_below = 0
            elif to_check.iloc[j] < cl_check.iloc[j]:
                run_below += 1
                run_above = 0
            else:
                run_above = 0
                run_below = 0
            if run_above >= 8:
                hit = True
                violating_indices.extend(range(offset + j - 7, offset + j + 1))
            if run_below >= 8:
                hit = True
                violating_indices.extend(range(offset + j - 7, offset + j + 1))
        if hit:
            violated.append("WECO Rule 2: 8+ consecutive points on same side of centerline")

    # WECO Rule 3: 2 out of 3 consecutive points beyond 2σ (same side)
    if rules_enabled[2]:
        beyond_2s_upper = valid & (to_check > upper_2s)
        beyond_2s_lower = valid & (to_check < lower_2s)
        hit = False
        for j in range(len(to_check) - 2):
            if beyond_2s_upper.iloc[j:j+3].sum() >= 2:
                hit = True
                violating_indices.extend(range(offset + j, offset + j + 3))
            elif beyond_2s_lower.iloc[j:j+3].sum() >= 2:
                hit = True
                violating_indices.extend(range(offset + j, offset + j + 3))
        if hit:
            violated.append("WECO Rule 3: 2/3 consecutive points beyond 2σ (same side)")

    # WECO Rule 4: 4 out of 5 consecutive points beyond 1σ (same side)
    if rules_enabled[3]:
        beyond_1s_upper = valid & (to_check > upper_1s)
        beyond_1s_lower = valid & (to_check < lower_1s)
        hit = False
        for j in range(len(to_check) - 4):
            if beyond_1s_upper.iloc[j:j+5].sum() >= 4:
                hit = True
                violating_indices.extend(range(offset + j, offset + j + 5))
            elif beyond_1s_lower.iloc[j:j+5].sum() >= 4:
                hit = True
                violating_indices.extend(range(offset + j, offset + j + 5))
        if hit:
            violated.append("WECO Rule 4: 4/5 consecutive points beyond 1σ (same side)")

    return violated, sorted(set(violating_indices))


def compute_centerline_and_sigma(results, window, mode, fixed_mean=None, fixed_std=None):
    results = pd.to_numeric(results, errors="coerce").dropna().reset_index(drop=True)
    if mode == "moving_average":
        centerline = results.rolling(window=window).mean()
        std_line = results.rolling(window=window).std()
    elif mode == "fixed":
        baseline = results.iloc[:window]
        mean = baseline.mean()
        std = baseline.std(ddof=1) if len(baseline) > 1 else 0.0
        centerline = pd.Series(mean, index=results.index, dtype="float64")
        std_line = pd.Series(std, index=results.index, dtype="float64")
    elif mode == "fixed_limit":
        centerline = pd.Series([fixed_mean] * len(results), index=results.index, dtype="float64")
        std_line = pd.Series([fixed_std] * len(results), index=results.index, dtype="float64")
    else:  # grand_mean
        mean = results.mean()
        std = results.std(ddof=1) if len(results) > 1 else 0.0
        centerline = pd.Series(mean, index=results.index, dtype="float64")
        std_line = pd.Series(std, index=results.index, dtype="float64")
    return centerline, std_line


def evaluate_spc_rules_against_limits(results, centerline, std_line, window, rules_enabled, start_idx_override=None):
    """Evaluate WECO rules against pre-computed centerline/std."""
    results = pd.to_numeric(results, errors="coerce").dropna().reset_index(drop=True)
    centerline = pd.to_numeric(centerline, errors="coerce").reset_index(drop=True)
    std_line = pd.to_numeric(std_line, errors="coerce").reset_index(drop=True)

    length = min(len(results), len(centerline), len(std_line))
    if length == 0:
        return [], []

    results = results.iloc[:length].reset_index(drop=True)
    centerline = centerline.iloc[:length].reset_index(drop=True)
    std_line = std_line.iloc[:length].reset_index(drop=True)

    valid_all = (~centerline.isna()) & (~std_line.isna())
    valid_positions = np.flatnonzero(valid_all.to_numpy())
    if len(valid_positions) == 0:
        return [], []

    # Ensure we skip at least the first 'window' points (baseline establishment period).
    # In moving-average mode, rolling NaNs naturally push start forward; in grand-mean mode
    # the centerline/std are never NaN, so we must enforce the skip explicitly.
    if start_idx_override is not None:
        start_idx = start_idx_override
    else:
        start_idx = max(int(valid_positions[0]), window)
    if start_idx >= length:
        return [], []
    to_check = results.iloc[start_idx:].reset_index(drop=True)
    cl_check = centerline.iloc[start_idx:].reset_index(drop=True)
    std_check = std_line.iloc[start_idx:].reset_index(drop=True)
    valid = (~cl_check.isna()) & (~std_check.isna())

    upper_3s = cl_check + 3 * std_check
    lower_3s = cl_check - 3 * std_check
    upper_2s = cl_check + 2 * std_check
    lower_2s = cl_check - 2 * std_check
    upper_1s = cl_check + 1 * std_check
    lower_1s = cl_check - 1 * std_check

    violated = []
    violating_indices = []

    # WECO Rule 1: One point beyond 3σ
    if rules_enabled[0]:
        mask = valid & ((to_check > upper_3s) | (to_check < lower_3s))
        if mask.any():
            violated.append("WECO Rule 1: One point beyond 3σ from centerline")
            for j in np.flatnonzero(mask.to_numpy()):
                violating_indices.append(start_idx + int(j))

    # WECO Rule 2: 8+ consecutive points on the same side of centerline
    if rules_enabled[1]:
        run_above = 0
        run_below = 0
        hit = False
        for j in range(len(to_check)):
            if not bool(valid.iloc[j]):
                run_above = 0
                run_below = 0
                continue
            if to_check.iloc[j] > cl_check.iloc[j]:
                run_above += 1
                run_below = 0
            elif to_check.iloc[j] < cl_check.iloc[j]:
                run_below += 1
                run_above = 0
            else:
                run_above = 0
                run_below = 0
            if run_above >= 8:
                hit = True
                violating_indices.extend(range(start_idx + j - 7, start_idx + j + 1))
            if run_below >= 8:
                hit = True
                violating_indices.extend(range(start_idx + j - 7, start_idx + j + 1))
        if hit:
            violated.append("WECO Rule 2: 8+ consecutive points on same side of centerline")

    # WECO Rule 3: 2 out of 3 consecutive points beyond 2σ (same side)
    if rules_enabled[2]:
        beyond_2s_upper = valid & (to_check > upper_2s)
        beyond_2s_lower = valid & (to_check < lower_2s)
        hit = False
        for j in range(len(to_check) - 2):
            if beyond_2s_upper.iloc[j:j+3].sum() >= 2:
                hit = True
                violating_indices.extend(range(start_idx + j, start_idx + j + 3))
            elif beyond_2s_lower.iloc[j:j+3].sum() >= 2:
                hit = True
                violating_indices.extend(range(start_idx + j, start_idx + j + 3))
        if hit:
            violated.append("WECO Rule 3: 2/3 consecutive points beyond 2σ (same side)")

    # WECO Rule 4: 4 out of 5 consecutive points beyond 1σ (same side)
    if rules_enabled[3]:
        beyond_1s_upper = valid & (to_check > upper_1s)
        beyond_1s_lower = valid & (to_check < lower_1s)
        hit = False
        for j in range(len(to_check) - 4):
            if beyond_1s_upper.iloc[j:j+5].sum() >= 4:
                hit = True
                violating_indices.extend(range(start_idx + j, start_idx + j + 5))
            elif beyond_1s_lower.iloc[j:j+5].sum() >= 4:
                hit = True
                violating_indices.extend(range(start_idx + j, start_idx + j + 5))
        if hit:
            violated.append("WECO Rule 4: 4/5 consecutive points beyond 1σ (same side)")

    return violated, sorted(set(violating_indices))


RULE_TRIGGER_LOOKBACK = {
    1: 1,
    2: 8,
    3: 3,
    4: 5,
}


def evaluate_latest_rule_triggers(results, centerline, std_line, window, rules_enabled, start_idx_override=None):
    """
    Return rules triggered by the latest point.

    A rule fires if the latest point is part of ANY violating pattern — i.e. the latest
    index appears in the violating_indices returned by evaluate_spc_rules_against_limits.

    For multi-point rules (Rule 2: 8 consecutive, Rule 3: 2/3, Rule 4: 4/5), the violation
    is detected as soon as the latest point completes or extends the pattern.
    """
    results = pd.to_numeric(results, errors="coerce").dropna().reset_index(drop=True)
    centerline = pd.to_numeric(centerline, errors="coerce").reset_index(drop=True)
    std_line = pd.to_numeric(std_line, errors="coerce").reset_index(drop=True)

    latest_idx = len(results) - 1
    if latest_idx < 0:
        return [], []

    latest_violated = []
    latest_indices = []

    for rule_num in range(1, 5):
        if not rules_enabled[rule_num - 1]:
            continue

        rule_only = [False] * 4
        rule_only[rule_num - 1] = True
        violated_rule, violating_rule_indices = evaluate_spc_rules_against_limits(
            results,
            centerline,
            std_line,
            window,
            rule_only,
            start_idx_override=start_idx_override
        )
        if not violating_rule_indices:
            continue

        # Trigger if the latest point is part of any violating pattern
        if latest_idx in violating_rule_indices:
            latest_violated.extend(violated_rule)
            latest_indices.extend(violating_rule_indices)

    return sorted(set(latest_violated)), sorted(set(latest_indices))


def check_spc_rules(results, window, rules_enabled, mode):
    violated, _ = evaluate_spc_rules(results, window, rules_enabled, mode)
    return violated


# --- GUI ---
class SPCApp:
    def __init__(self, root):
        self.root = root
        self.root.title("WECO SPC Control")
        self.rules = [tk.BooleanVar(value=True) for _ in range(4)]
        self.window_size = tk.IntVar(value=30)
        self.centerline_mode = tk.StringVar(value="moving_average")
        self.exclude_failed_units = tk.BooleanVar(value=True)
        # Fixed base directory for all part numbers
        self.base_dir = r"C:\TESTDATA\FOURPORT_TESTDATA"
        self.part_number = tk.StringVar()
        self.violated_text = tk.StringVar()
        self.part_entry = None
        self.param_filter_btn = None
        self.start_btn = None
        self.done_btn = None
        self.poll_interval_ms = 1000
        self.poll_job = None
        self.seen_sources = set()
        self.last_violations = {}
        self.available_parameters = []
        self.selected_parameters = set()
        self.param_selector_window = None
        self.started = False
        self.paused = False

        # --- Fixed Limit mode state ---
        self.fixed_limit_path = tk.StringVar()
        self.fixed_limits = {}  # {parameter_name: {"mean": float, "std": float}}

        # --- v2: SN deduplication across parameters ---
        self.triggered_sns_session = set()

        # --- v2: Parameter families from limits JSON (None = use all from data) ---
        self.limits_parameter_families = None

        # --- v2: Flashing banner state ---
        self.banner_visible = False
        self.flash_job = None
        self.flash_state = False  # toggles for flash effect

        self.create_widgets()

    def create_widgets(self):
        f = font.Font(size=16, weight="bold")
        tk.Label(self.root, text="WECO SPC Rules", font=f).grid(row=0, column=0, sticky="w", pady=(10, 0))
        rule_names = [
            "Rule 1 – One point is more than 3σ from the centerline",
            "Rule 2 – Eight or more consecutive points on the same side of the centerline",
            "Rule 3 – Two out of three consecutive points are more than 2σ from the centerline (same side)",
            "Rule 4 – Four out of five consecutive points are more than 1σ from the centerline (same side)",
        ]
        for i, name in enumerate(rule_names):
            tk.Checkbutton(self.root, text=name, variable=self.rules[i], wraplength=700, anchor="w",
                           justify="left").grid(row=i + 1, column=0, sticky="w")
        window_frame = tk.Frame(self.root)
        window_frame.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        tk.Label(window_frame, text="SPC Window Size:").pack(side="left")
        self.window_size_entry = tk.Entry(window_frame, textvariable=self.window_size, width=6)
        self.window_size_entry.pack(side="left", padx=(10, 0))
        cl_frame = tk.LabelFrame(self.root, text="Centerline Mode")
        cl_frame.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        tk.Radiobutton(cl_frame, text="Moving Average", variable=self.centerline_mode,
                       value="moving_average").pack(side="left", padx=(4, 10))
        tk.Radiobutton(cl_frame, text="Grand Mean", variable=self.centerline_mode,
                       value="grand_mean").pack(side="left", padx=(0, 10))
        tk.Radiobutton(cl_frame, text="Fixed Limit",
                       variable=self.centerline_mode, value="fixed_limit").pack(side="left", padx=(0, 4))

        # --- Fixed Limit file browse row (row 7) ---
        self.limit_file_frame = tk.Frame(self.root)
        self.limit_file_frame.grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))
        tk.Label(self.limit_file_frame, text="Limit File:").pack(side="left")
        self.limit_file_entry = tk.Entry(self.limit_file_frame, textvariable=self.fixed_limit_path, width=40)
        self.limit_file_entry.pack(side="left", padx=(6, 4))
        self.limit_browse_btn = tk.Button(self.limit_file_frame, text="Browse...", command=self.browse_fixed_limit_file)
        self.limit_browse_btn.pack(side="left", padx=(0, 6))
        self.limit_status_label = tk.Label(self.limit_file_frame, text="", fg="#666666")
        self.limit_status_label.pack(side="left")

        self.exclude_failed_cb = tk.Checkbutton(
            self.root,
            text="Exclude Failed Units from Centerline/3σ Calculation",
            variable=self.exclude_failed_units,
            anchor="w",
            justify="left"
        )
        self.exclude_failed_cb.grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Trace centerline_mode to enable/disable widgets (must be after all widgets are created)
        self.centerline_mode.trace_add("write", self._on_mode_change)
        self._on_mode_change()  # Set initial state
        tk.Label(self.root, text=f"Base Directory: {self.base_dir}").grid(row=9, column=0, columnspan=2, sticky="w",
                                                                          pady=(10, 0))
        part_frame = tk.Frame(self.root)
        part_frame.grid(row=10, column=0, columnspan=2, sticky="w", pady=(10, 0))
        tk.Label(part_frame, text="Part Number:").pack(side="left")
        self.part_entry = tk.Entry(part_frame, textvariable=self.part_number, width=30)
        self.part_entry.pack(side="left", padx=(10, 0))
        self.param_filter_btn = tk.Button(self.root, text="PARAMETER FILTER", command=self.open_parameter_selector,
                                          bg="#1f6aa5", fg="white", font=f)
        self.param_filter_btn.grid(row=11, column=0, pady=10)
        self.start_btn = tk.Button(self.root, text="START", command=self.toggle_start_pause, bg="#228B22",
                                   fg="white", font=f)
        self.start_btn.grid(row=12, column=0, pady=10)
        self.done_btn = tk.Button(self.root, text="DONE TESTING", command=self.done_testing, bg="#b30000",
                                  fg="white", font=f)
        self.done_btn.grid(row=13, column=0, pady=10)
        self.done_btn.config(state="disabled")
        self.big_label = tk.Label(self.root, textvariable=self.violated_text, font=("Arial", 12, "bold"),
                                  fg="#b30000", wraplength=700, justify="center")
        self.big_label.grid(row=14, column=0, columnspan=2, pady=10)

        # --- v2: Alarm banner window (separate Toplevel, initially not created) ---
        self.banner_window = None

    def _on_mode_change(self, *args):
        """Enable/disable widgets based on centerline mode."""
        is_fixed_limit = self.centerline_mode.get() == "fixed_limit"
        # Fixed Limit: enable limit file, disable window size and exclude-failed
        self.limit_file_entry.config(state="normal" if is_fixed_limit else "disabled")
        self.limit_browse_btn.config(state="normal" if is_fixed_limit else "disabled")
        self.window_size_entry.config(state="disabled" if is_fixed_limit else "normal")
        self.exclude_failed_cb.config(state="disabled" if is_fixed_limit else "normal")

    def browse_fixed_limit_file(self):
        """Open file dialog to select a summary CSV for Fixed Limit mode."""
        file_path = filedialog.askopenfilename(
            title="Select Summary CSV for Fixed Limits",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if file_path:
            self.fixed_limit_path.set(file_path)
            limits = parse_summary_csv(file_path)
            if limits:
                self.fixed_limits = limits
                self.limit_status_label.config(text=f"{len(limits)} params loaded", fg="#228B22")
                # Populate parameter filter from the CSV columns
                self.available_parameters = sorted(limits.keys())
                if not self.selected_parameters:
                    self.selected_parameters = set(self.available_parameters)
            else:
                self.fixed_limits = {}
                self.limit_status_label.config(text="No valid data found", fg="#b30000")

    def toggle_start_pause(self):
        """Handle START/PAUSE/RESUME button clicks."""
        if not self.started:
            # Not started yet — start analysis
            self.start_analysis()
        elif not self.paused:
            # Running — pause
            self.pause_analysis()
        else:
            # Paused — resume
            self.resume_analysis()

    def pause_analysis(self):
        """Pause the SPC monitoring."""
        self.paused = True
        if self.poll_job is not None:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        self.start_btn.config(text="RESUME", bg="#2196F3")
        # Re-enable param filter and part entry during pause
        self.param_filter_btn.config(state="normal")
        self.part_entry.config(state="normal")

    def resume_analysis(self):
        """Resume the SPC monitoring after pause."""
        self.paused = False
        self.start_btn.config(text="PAUSE", bg="#FF9800")
        self.param_filter_btn.config(state="disabled")
        self.part_entry.config(state="disabled")
        self.schedule_next_scan()

    def show_banner(self, sn, param, violated_rules):
        """Show a separate alarm window at the top of the screen."""
        rules_text = "\n".join(violated_rules)
        self.banner_visible = True
        self.flash_state = False

        # Create a new Toplevel window for the alarm
        if self.banner_window is not None:
            try:
                self.banner_window.destroy()
            except Exception:
                pass

        alarm = tk.Toplevel(self.root)
        self.banner_window = alarm
        alarm.title("\u26a0 SPC ALARM \u26a0")
        alarm.configure(bg="#cc0000")
        alarm.overrideredirect(True)  # Remove title bar for a clean banner look
        alarm.attributes("-topmost", True)  # Always on top

        # Size: full screen width, positioned at top of monitor
        screen_width = alarm.winfo_screenwidth()
        # Dynamic height based on number of violated rules
        num_lines = 5 + len(violated_rules)  # SN + param + rules + "KEEP THE UNIT" + spacing
        banner_height = max(300, num_lines * 45 + 100)
        alarm.geometry(f"{screen_width}x{banner_height}+0+0")

        # Alarm content
        self.banner_label = tk.Label(
            alarm,
            text=f"\u26a0  SPC RULE VIOLATED  \u26a0\n\nS/N: {sn}\n{param}\n{rules_text}\n\nPLEASE KEEP THE UNIT",
            font=("Arial", 20, "bold"),
            fg="white",
            bg="#cc0000",
            wraplength=screen_width - 100,
            justify="center"
        )
        self.banner_label.pack(expand=True, fill="both", padx=20, pady=(20, 10))

        self.banner_ack_btn = tk.Button(
            alarm,
            text="\u26a0  ACKNOWLEDGE  \u26a0",
            font=("Arial", 18, "bold"),
            bg="white",
            fg="#cc0000",
            activebackground="#ffcccc",
            width=25,
            height=2,
            command=self.acknowledge_violation
        )
        self.banner_ack_btn.pack(pady=(0, 20))

        # Start flashing
        self.flash_banner()

    def flash_banner(self):
        """Toggle alarm window color between red and dark-red for flashing effect."""
        if not self.banner_visible or self.banner_window is None:
            return
        self.flash_state = not self.flash_state
        bg_color = "#cc0000" if self.flash_state else "#800000"
        try:
            self.banner_window.configure(bg=bg_color)
            self.banner_label.config(bg=bg_color)
        except Exception:
            return
        self.flash_job = self.root.after(500, self.flash_banner)

    def acknowledge_violation(self):
        """Dismiss the alarm window and resume polling."""
        self.banner_visible = False
        if self.flash_job is not None:
            self.root.after_cancel(self.flash_job)
            self.flash_job = None
        if self.banner_window is not None:
            try:
                self.banner_window.destroy()
            except Exception:
                pass
            self.banner_window = None
        # Resume polling after acknowledgment
        self.schedule_next_scan()

    def start_analysis(self):
        mode = self.centerline_mode.get()

        # Validate fixed_limit mode
        if mode == "fixed_limit" and not self.fixed_limits:
            messagebox.showerror("Error", "Fixed Limit mode requires a valid summary CSV file.\n"
                                 "Please browse and select a limit file first.")
            return

        self.part_entry.config(state="disabled")
        self.param_filter_btn.config(state="disabled")
        self.start_btn.config(text="PAUSE", bg="#FF9800")
        self.done_btn.config(state="normal")
        self.paused = False

        # --- v2: Load limits-based parameter families if not already loaded ---
        # Only populate selected_parameters if user hasn't already set them via PARAMETER FILTER
        if self.limits_parameter_families is None and not self.selected_parameters:
            raw_part_number = self.part_number.get().strip()
            part_number = re.sub(r'[\\/:*?"<>|]', '', raw_part_number)
            if part_number:
                limits_families = load_parameters_from_limits(part_number)
                if limits_families:
                    self.limits_parameter_families = limits_families
                    self.available_parameters = sorted(limits_families)
                    self.selected_parameters = set(limits_families)
                    print(f"Loaded {len(limits_families)} parameter families from limits config: {limits_families}")

        self.started = True
        first_scan_ok = self.run_spc(silent=False)
        if not first_scan_ok:
            self.started = False
            self.paused = False
            self.part_entry.config(state="normal")
            self.param_filter_btn.config(state="normal")
            self.start_btn.config(text="START", bg="#228B22")
            self.done_btn.config(state="disabled")
            return
        self.schedule_next_scan()

    def done_testing(self):
        self.started = False
        self.paused = False
        if self.poll_job is not None:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        if self.flash_job is not None:
            self.root.after_cancel(self.flash_job)
            self.flash_job = None
        self.banner_visible = False
        if self.banner_window is not None:
            try:
                self.banner_window.destroy()
            except Exception:
                pass
            self.banner_window = None
        self.seen_sources.clear()
        self.last_violations.clear()
        self.triggered_sns_session.clear()  # v2: reset SN dedup
        self.limits_parameter_families = None  # v2: reset limits filter
        self.part_number.set("")
        self.part_entry.config(state="normal")
        self.param_filter_btn.config(state="normal")
        self.start_btn.config(text="START", bg="#228B22")
        self.done_btn.config(state="disabled")
        self.violated_text.set("")

    def parameter_family(self, param):
        return param.split(" | ")[0].strip() if " | " in param else str(param)

    def folder_time_key(self, path_or_name):
        name = os.path.basename(path_or_name)
        m = re.search(r"(20\d{6})-(\d{6})", name)
        if m:
            return (m.group(1) + m.group(2), name.lower())
        return ("", name.lower())

    def sync_parameter_catalog(self, parameters):
        current = sorted(set(parameters))
        if not current:
            return
        if not self.available_parameters:
            self.available_parameters = current
            self.selected_parameters = set(current)
            return
        prev = set(self.available_parameters)
        curr = set(current)
        removed = prev - curr
        self.selected_parameters -= removed
        # Do NOT auto-add new parameters — preserve user's filter choices
        self.available_parameters = current

    def load_latest_summary_data(self, dir_path):
        all_dfs = []
        active_params = set()
        current_sources = set()

        subfolders = [
            entry.path
            for entry in os.scandir(dir_path)
            if entry.is_dir()
        ]
        if not subfolders:
            return all_dfs, current_sources, active_params

        latest_subfolder = max(
            subfolders,
            key=lambda p: (self.folder_time_key(p), os.path.getmtime(p))
        )

        try:
            files = [
                name for name in os.listdir(latest_subfolder)
                if os.path.isfile(os.path.join(latest_subfolder, name))
            ]
        except OSError:
            return all_dfs, current_sources, active_params

        zip_files = [name for name in files if name.lower().endswith('.zip')]
        if zip_files:
            latest_zip = max(
                zip_files,
                key=lambda name: (
                    os.path.getmtime(os.path.join(latest_subfolder, name)),
                    name.lower()
                )
            )
            zip_path = os.path.join(latest_subfolder, latest_zip)
            try:
                with zipfile.ZipFile(zip_path, 'r') as z:
                    for name in z.namelist():
                        if name.lower().endswith('summary.txt'):
                            with z.open(name) as f:
                                text = f.read().decode('utf-8', errors='replace')
                            source_label = zip_path + '::' + name
                            zip_stat = os.stat(zip_path)
                            text_hash = hashlib.md5(text.encode('utf-8', errors='replace')).hexdigest()
                            source_sig = (
                                source_label,
                                zip_stat.st_mtime_ns,
                                zip_stat.st_size,
                                text_hash
                            )
                            df_one = parse_summary_text(text, source_label=source_label)
                            if not df_one.empty:
                                current_sources.add(source_sig)
                                active_params.update(df_one["Parameter"].dropna().unique().tolist())
                                all_dfs.append(df_one)
            except Exception as e:
                print(f"Error reading zip {zip_path}: {e}")
        else:
            for file in files:
                if file.lower() == 'summary.txt':
                    file_path = os.path.join(latest_subfolder, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                            text = f.read()
                    except OSError:
                        continue
                    file_stat = os.stat(file_path)
                    text_hash = hashlib.md5(text.encode('utf-8', errors='replace')).hexdigest()
                    source_sig = (
                        file_path,
                        file_stat.st_mtime_ns,
                        file_stat.st_size,
                        text_hash
                    )
                    df_one = parse_summary_text(text, source_label=file_path)
                    if not df_one.empty:
                        current_sources.add(source_sig)
                        active_params.update(df_one["Parameter"].dropna().unique().tolist())
                        all_dfs.append(df_one)

        return all_dfs, current_sources, active_params

    def load_recent_history_data(self, dir_path, max_subfolders=160):
        all_dfs = []
        active_params = set()

        subfolders = [
            entry.path
            for entry in os.scandir(dir_path)
            if entry.is_dir()
        ]
        if not subfolders:
            return all_dfs, active_params

        subfolders = sorted(
            subfolders,
            key=lambda p: (self.folder_time_key(p), os.path.getmtime(p)),
            reverse=True
        )

        for subfolder in subfolders[:max_subfolders]:
            try:
                files = [
                    name for name in os.listdir(subfolder)
                    if os.path.isfile(os.path.join(subfolder, name))
                ]
            except OSError:
                continue

            zip_files = [name for name in files if name.lower().endswith('.zip')]
            if zip_files:
                latest_zip = max(
                    zip_files,
                    key=lambda name: (
                        os.path.getmtime(os.path.join(subfolder, name)),
                        name.lower()
                    )
                )
                zip_path = os.path.join(subfolder, latest_zip)
                try:
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for name in z.namelist():
                            if name.lower().endswith('summary.txt'):
                                with z.open(name) as f:
                                    text = f.read().decode('utf-8', errors='replace')
                                source_label = zip_path + '::' + name
                                df_one = parse_summary_text(text, source_label=source_label)
                                if not df_one.empty:
                                    active_params.update(df_one["Parameter"].dropna().unique().tolist())
                                    all_dfs.append(df_one)
                except Exception as e:
                    print(f"Error reading zip {zip_path}: {e}")
            else:
                for file in files:
                    if file.lower() == 'summary.txt':
                        file_path = os.path.join(subfolder, file)
                        try:
                            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                                text = f.read()
                        except OSError:
                            continue
                        df_one = parse_summary_text(text, source_label=file_path)
                        if not df_one.empty:
                            active_params.update(df_one["Parameter"].dropna().unique().tolist())
                            all_dfs.append(df_one)

        return all_dfs, active_params

    def open_parameter_selector(self):
        if not self.available_parameters:
            # If Fixed Limit mode and CSV is loaded, use those parameters
            if self.centerline_mode.get() == "fixed_limit" and self.fixed_limits:
                self.available_parameters = sorted(self.fixed_limits.keys())
                if not self.selected_parameters:
                    self.selected_parameters = set(self.available_parameters)
            else:
                # Try loading from fourport limits JSON
                raw_part_number = self.part_number.get().strip()
                part_number = re.sub(r'[\\/:*?"<>|]', '', raw_part_number)
                if not part_number:
                    messagebox.showinfo("Parameter Filter",
                                        "Enter a part number or load a Fixed Limit CSV first.")
                    return

                limits_families = load_parameters_from_limits(part_number)
                if limits_families:
                    self.limits_parameter_families = limits_families
                    self.available_parameters = sorted(limits_families)
                    if not self.selected_parameters:
                        self.selected_parameters = set(limits_families)
                    print(f"Loaded {len(limits_families)} parameter families from limits config: {limits_families}")
                else:
                    self.limits_parameter_families = None
                    messagebox.showinfo(
                        "Parameter Filter",
                        f"Could not find limits config for part number '{part_number}'.\n"
                        f"Checked: {FOURPORT_PARTS_DIR}"
                    )
                    return

            if not self.available_parameters:
                messagebox.showinfo(
                    "Parameter Filter",
                    "No parameters found."
                )
                return

        if self.param_selector_window is not None and self.param_selector_window.winfo_exists():
            self.param_selector_window.lift()
            self.param_selector_window.focus_force()
            return

        top = tk.Toplevel(self.root)
        top.title("WECO SPC Parameter Filter")
        top.geometry("900x620")
        self.param_selector_window = top

        status_text = tk.StringVar()
        param_vars = {}

        def update_status():
            selected_count = sum(1 for v in param_vars.values() if v.get())
            status_text.set(f"Selected: {selected_count} / {len(self.available_parameters)}")

        def select_all():
            for p in self.available_parameters:
                param_vars[p].set(True)
            for g in group_vars:
                group_vars[g].set(True)
            update_status()

        def clear_all():
            for p in self.available_parameters:
                param_vars[p].set(False)
            for g in group_vars:
                group_vars[g].set(False)
            update_status()

        def apply_selection():
            self.selected_parameters = {p for p, v in param_vars.items() if v.get()}
            self.param_selector_window = None
            top.destroy()

        def cancel_selection():
            self.param_selector_window = None
            top.destroy()

        # Pack the bottom frame FIRST with side="bottom" so it's always visible
        bottom_frame = tk.Frame(top)
        bottom_frame.pack(side="bottom", fill="x", padx=10, pady=(6, 10))
        tk.Button(bottom_frame, text="APPLY", command=apply_selection,
                  bg="#228B22", fg="white", font=("Arial", 12, "bold"),
                  width=10).pack(side="left", padx=(0, 10))
        tk.Button(bottom_frame, text="Cancel", command=cancel_selection,
                  font=("Arial", 12), width=10).pack(side="left")

        controls = tk.Frame(top)
        controls.pack(fill="x", padx=10, pady=(10, 6))
        tk.Button(controls, text="Select All", command=select_all).pack(side="left", padx=(0, 6))
        tk.Button(controls, text="Clear All", command=clear_all).pack(side="left")
        tk.Label(controls, textvariable=status_text, fg="#1f6aa5").pack(side="right")

        canvas = tk.Canvas(top, highlightthickness=0)
        scroll = tk.Scrollbar(top, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        params_frame = tk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=params_frame, anchor="nw")

        def on_frame_config(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas_config(event):
            canvas.itemconfig(canvas_window, width=event.width)

        params_frame.bind("<Configure>", on_frame_config)
        canvas.bind("<Configure>", on_canvas_config)

        # Group parameters by their prefix (text before the first "|")
        from collections import OrderedDict
        groups = OrderedDict()
        for p in self.available_parameters:
            prefix = p.split("|")[0].strip() if "|" in p else p
            if prefix not in groups:
                groups[prefix] = []
            groups[prefix].append(p)

        group_vars = {}  # group_name -> BooleanVar

        def toggle_group(group_name):
            """Toggle all parameters in a group when the group checkbox is clicked."""
            state = group_vars[group_name].get()
            for p in groups[group_name]:
                param_vars[p].set(state)
            update_status()

        def update_group_var(group_name):
            """Update group checkbox state based on individual params."""
            all_checked = all(param_vars[p].get() for p in groups[group_name])
            group_vars[group_name].set(all_checked)
            update_status()

        for group_name, group_params in groups.items():
            # Group header checkbox (bold)
            all_checked = all(p in self.selected_parameters for p in group_params)
            group_vars[group_name] = tk.BooleanVar(value=all_checked)
            tk.Checkbutton(
                params_frame,
                text=f"▸ {group_name}  ({len(group_params)})",
                variable=group_vars[group_name],
                command=lambda g=group_name: toggle_group(g),
                anchor="w",
                justify="left",
                font=("Arial", 11, "bold"),
                fg="#1f6aa5"
            ).pack(fill="x", anchor="w", pady=(8, 0))

            # Individual parameter checkboxes (indented)
            for p in group_params:
                checked = p in self.selected_parameters
                param_vars[p] = tk.BooleanVar(value=checked)
                tk.Checkbutton(
                    params_frame,
                    text=p,
                    variable=param_vars[p],
                    command=lambda g=group_name: update_group_var(g),
                    anchor="w",
                    justify="left",
                    font=("Arial", 10)
                ).pack(fill="x", anchor="w", padx=(25, 0), pady=1)

        update_status()

        def on_close():
            # Window close (X) = Cancel — preserves previous selection
            self.param_selector_window = None
            top.destroy()

        top.protocol("WM_DELETE_WINDOW", on_close)

    def schedule_next_scan(self):
        if self.started and not self.banner_visible and not self.paused:
            self.poll_job = self.root.after(self.poll_interval_ms, self.monitor_spc)

    def monitor_spc(self):
        if not self.started or self.paused:
            return
        if self.banner_visible:
            return  # Don't poll while banner is showing
        self.run_spc(silent=True)
        self.schedule_next_scan()

    def run_spc(self, silent=False):
        raw_part_number = self.part_number.get().strip()
        part_number = re.sub(r'[\\/:*?"<>|]', '', raw_part_number)
        if not part_number:
            if not silent:
                messagebox.showerror("Error", "Please enter a valid part number.")
            return False

        dir_path = os.path.join(self.base_dir, part_number)
        if not os.path.isdir(dir_path):
            # Directory doesn't exist yet — create it and wait for data
            try:
                os.makedirs(dir_path, exist_ok=True)
            except OSError:
                pass
            self.violated_text.set("Waiting for data...")
            return True

        latest_dfs, current_sources, _ = self.load_latest_summary_data(dir_path)
        if not latest_dfs:
            # No data yet — just wait for data to arrive (don't block startup)
            self.violated_text.set("Waiting for data...")
            return True

        if silent and current_sources == self.seen_sources:
            return True
        self.seen_sources = current_sources

        max_history_subfolders = max(120, self.window_size.get() * 4)
        history_dfs, active_params = self.load_recent_history_data(dir_path, max_subfolders=max_history_subfolders)
        all_dfs = history_dfs if history_dfs else latest_dfs

        df = pd.concat(all_dfs, ignore_index=True)

        # --- Detect OPEN SNs using S2_1/S4_3 Result < -50 (always excluded downstream) ---
        open_sns = detect_open_sns(df, threshold=OPEN_THRESHOLD)

        # Only sync parameter catalog from data if NOT using limits-based families or fixed_limit CSV
        if not self.limits_parameter_families and not self.fixed_limits:
            all_params = df["Parameter"].dropna().unique().tolist()
            self.sync_parameter_catalog(all_params)
        param_groups = df.groupby("Parameter")

        # Parameters to exclude from SPC analysis (still used as OPEN gates)
        excluded_prefixes = OPEN_GATE_PREFIXES

        # Determine parameter test-sequence order by first occurrence in the data.
        param_order = {}
        for p in df["Parameter"].dropna():
            if p not in param_order:
                param_order[p] = len(param_order)
        sorted_params_by_order = sorted(param_order.keys(), key=lambda p: param_order[p])

        # For each parameter, collect the set of SNs that have already failed any earlier parameter.
        prior_failed_sns_for: dict = {}
        failed_so_far: set = set()
        for p in sorted_params_by_order:
            prior_failed_sns_for[p] = frozenset(failed_so_far)
            param_rows = df[df["Parameter"] == p]
            new_failures = param_rows.loc[
                param_rows["Pass/Fail"].fillna("").str.strip().str.upper().eq("FAIL"),
                "SN"
            ].dropna()
            failed_so_far.update(new_failures.tolist())

        mode = self.centerline_mode.get()

        new_violations = []
        for param, group in param_groups:
            active_params.add(param)

            param_label = parameter_prefix(param)

            # Skip parameters not in the selected set
            if self.limits_parameter_families:
                # Limits mode: selected_parameters contains family names
                if param_label not in self.selected_parameters:
                    self.last_violations[param] = set()
                    continue
            else:
                # Legacy mode: selected_parameters contains full composite param names
                if param not in self.selected_parameters:
                    self.last_violations[param] = set()
                    continue

            # Skip excluded parameter prefixes from SPC analysis (but they were used for OPEN detection)
            if any(param_label.startswith(pfx) for pfx in excluded_prefixes):
                continue

            if "Test Time" in group.columns:
                group = group.sort_values("Test Time").reset_index(drop=True)

            # Existing "prior failed" exclusion
            prior_failed = prior_failed_sns_for.get(param, frozenset())
            base_group = group[~group["SN"].isin(prior_failed)].copy()

            # --- OPEN exclusion (always) for downstream parameters ---
            base_group = base_group[~base_group["SN"].isin(open_sns)].copy()

            # Trigger evaluation population: PASS-only for historical points, but always
            # include the latest point so that a new outlier (which may FAIL its spec) still
            # gets evaluated for SPC rule violations.
            base_overall_pass_mask = base_group["Overall Result"].fillna("").str.strip().str.upper().eq("PASS")
            base_row_pass_mask = base_group["Pass/Fail"].fillna("").str.strip().str.upper().eq("PASS")
            pass_mask = base_overall_pass_mask & base_row_pass_mask
            # Always include the last row (latest unit) even if it failed
            if len(base_group) > 0:
                pass_mask.iloc[-1] = True
            trigger_group = base_group[pass_mask].copy()

            # Control limit population (checkbox-controlled) BUT OPEN already removed above
            if self.exclude_failed_units.get():
                calc_group = trigger_group.copy()
            else:
                calc_group = base_group.copy()

            calc_numeric = calc_group.copy()
            calc_numeric["ResultNum"] = pd.to_numeric(calc_numeric["Result"], errors="coerce")
            calc_numeric = calc_numeric[calc_numeric["ResultNum"].notna()].copy()
            calc_numeric["orig_pos"] = calc_numeric.index

            trigger_numeric = trigger_group.copy()
            trigger_numeric["ResultNum"] = pd.to_numeric(trigger_numeric["Result"], errors="coerce")
            trigger_numeric = trigger_numeric[trigger_numeric["ResultNum"].notna()].copy()
            trigger_numeric["orig_pos"] = trigger_numeric.index

            rules_enabled = [r.get() for r in self.rules]
            enabled_rule_ids = [i + 1 for i, enabled in enumerate(rules_enabled) if enabled]
            if not enabled_rule_ids:
                self.last_violations[param] = set()
                continue

            min_trigger_points = max(RULE_TRIGGER_LOOKBACK[rid] for rid in enabled_rule_ids)
            if len(trigger_numeric) < min_trigger_points:
                self.last_violations[param] = set()
                continue

            # --- Fixed Limit mode: lookup parameter in self.fixed_limits ---
            if mode == "fixed_limit":
                # Look up using exact parameter name match
                limit_info = self.fixed_limits.get(param)
                if limit_info is None:
                    # Parameter not found in limit file — skip
                    self.last_violations[param] = set()
                    continue
                fl_mean = limit_info["mean"]
                fl_std = limit_info["std"]

                # For fixed_limit: require only 1 data point
                if len(calc_numeric) < 1:
                    self.last_violations[param] = set()
                    continue

                calc_results = calc_numeric["ResultNum"].reset_index(drop=True)
                trigger_results = trigger_numeric["ResultNum"].reset_index(drop=True)

                centerline_calc, std_calc = compute_centerline_and_sigma(
                    calc_results,
                    self.window_size.get(),
                    mode,
                    fixed_mean=fl_mean,
                    fixed_std=fl_std
                )

                calc_pos_by_orig = {orig_pos: i for i, orig_pos in enumerate(calc_numeric["orig_pos"].tolist())}
                trigger_calc_positions = trigger_numeric["orig_pos"].map(calc_pos_by_orig)
                valid_trigger = trigger_calc_positions.notna()
                if not bool(valid_trigger.all()):
                    trigger_numeric = trigger_numeric.loc[valid_trigger].copy()
                    trigger_results = trigger_numeric["ResultNum"].reset_index(drop=True)
                    trigger_calc_positions = trigger_calc_positions.loc[valid_trigger]

                trigger_calc_positions = trigger_calc_positions.astype(int).reset_index(drop=True)
                centerline_trigger = centerline_calc.iloc[trigger_calc_positions].reset_index(drop=True)
                std_trigger = std_calc.iloc[trigger_calc_positions].reset_index(drop=True)

                violated, violating_indices = evaluate_latest_rule_triggers(
                    trigger_results,
                    centerline_trigger,
                    std_trigger,
                    self.window_size.get(),
                    rules_enabled,
                    start_idx_override=0
                )
            else:
                # Original modes: moving_average, grand_mean, fixed
                if mode in ("moving_average", "fixed") and len(calc_numeric) < self.window_size.get():
                    self.last_violations[param] = set()
                    continue
                if mode == "grand_mean" and len(calc_numeric) < 2:
                    self.last_violations[param] = set()
                    continue

                calc_results = calc_numeric["ResultNum"].reset_index(drop=True)
                trigger_results = trigger_numeric["ResultNum"].reset_index(drop=True)

                centerline_calc, std_calc = compute_centerline_and_sigma(
                    calc_results,
                    self.window_size.get(),
                    mode
                )

                calc_pos_by_orig = {orig_pos: i for i, orig_pos in enumerate(calc_numeric["orig_pos"].tolist())}
                trigger_calc_positions = trigger_numeric["orig_pos"].map(calc_pos_by_orig)
                valid_trigger = trigger_calc_positions.notna()
                if not bool(valid_trigger.all()):
                    trigger_numeric = trigger_numeric.loc[valid_trigger].copy()
                    trigger_results = trigger_numeric["ResultNum"].reset_index(drop=True)
                    trigger_calc_positions = trigger_calc_positions.loc[valid_trigger]

                trigger_calc_positions = trigger_calc_positions.astype(int).reset_index(drop=True)
                centerline_trigger = centerline_calc.iloc[trigger_calc_positions].reset_index(drop=True)
                std_trigger = std_calc.iloc[trigger_calc_positions].reset_index(drop=True)

                violated, violating_indices = evaluate_latest_rule_triggers(
                    trigger_results,
                    centerline_trigger,
                    std_trigger,
                    self.window_size.get(),
                    rules_enabled
                )

            trigger_sn = trigger_numeric["SN"].reset_index(drop=True)
            violated_set = set(violated)
            self.last_violations[param] = violated_set

            latest_is_violating = len(violating_indices) > 0

            if violated and latest_is_violating:
                latest_sn = trigger_sn.iloc[-1] if len(trigger_sn) > 0 else "UNKNOWN"

                # --- v2: Skip if this SN already triggered on a previous parameter ---
                if latest_sn in self.triggered_sns_session:
                    continue

                new_violations.append((param, group, calc_group, trigger_group, prior_failed, violated, latest_sn))

        stale_params = [p for p in self.last_violations.keys() if p not in active_params]
        for p in stale_params:
            del self.last_violations[p]

        if new_violations:
            # Only fire on the first violation found for this cycle
            param, group, calc_group, trigger_group, prior_failed, violated, latest_sn = new_violations[0]
            self.violated_text.set(f"{param}\n" + "\n".join(violated))

            # --- v2: Mark this SN as already triggered ---
            self.triggered_sns_session.add(latest_sn)

            for rule_desc in violated:
                rule_num = re.search(r"Rule (\d+)", rule_desc)
                rule_label = f"Rule {rule_num.group(1)}" if rule_num else rule_desc
                log_violation_to_csv(latest_sn, param, rule_label, part_number)

            # --- v2: Save plot silently (no pop-up window) ---
            self.save_spc_plot(group, param, calc_group, trigger_group, prior_failed, latest_violating_sn=latest_sn)

            # --- v2: Show flashing banner instead of modal ---
            self.show_banner(latest_sn, param, violated)
            return True

        self.violated_text.set("No SPC rule violated.")
        return True

    def save_spc_plot(self, group, param, calc_group=None, trigger_group=None, prior_failed_sns=None, latest_violating_sn=None):
        """Generate and save the SPC chart to disk (no pop-up window)."""
        if calc_group is None:
            calc_group = group
        if trigger_group is None:
            trigger_group = calc_group
        if prior_failed_sns is None:
            prior_failed_sns = frozenset()

        group_reset = group.reset_index(drop=True)
        results = pd.to_numeric(group_reset["Result"], errors="coerce").reset_index(drop=True)
        window = self.window_size.get()

        ll = group_reset["Lower Limit"].iloc[0] if not pd.isna(group_reset["Lower Limit"].iloc[0]) else None
        ul = group_reset["Upper Limit"].iloc[0] if not pd.isna(group_reset["Upper Limit"].iloc[0]) else None

        plt.close('all')
        fig, ax = plt.subplots(figsize=(12, 5))

        calc_group = calc_group.copy()
        trigger_group = trigger_group.copy()

        calc_group["ResultNum"] = pd.to_numeric(calc_group["Result"], errors="coerce")
        calc_numeric = calc_group[calc_group["ResultNum"].notna()].copy()
        calc_numeric["orig_pos"] = calc_numeric.index

        trigger_group["ResultNum"] = pd.to_numeric(trigger_group["Result"], errors="coerce")
        trigger_numeric = trigger_group[trigger_group["ResultNum"].notna()].copy()
        trigger_numeric["orig_pos"] = trigger_numeric.index

        if len(calc_numeric) == 0 or len(trigger_numeric) == 0:
            plt.close(fig)
            return

        calc_results = calc_numeric["ResultNum"].reset_index(drop=True)
        mode = self.centerline_mode.get()

        # Determine fixed_limit parameters for compute
        fl_mean = None
        fl_std = None
        if mode == "fixed_limit":
            limit_info = self.fixed_limits.get(param)
            if limit_info:
                fl_mean = limit_info["mean"]
                fl_std = limit_info["std"]
            else:
                # Fallback: use grand mean if param not found
                fl_mean = calc_results.mean()
                fl_std = calc_results.std(ddof=1) if len(calc_results) > 1 else 0.0

        centerline_calc, std_calc = compute_centerline_and_sigma(
            calc_results,
            window,
            mode,
            fixed_mean=fl_mean,
            fixed_std=fl_std
        )

        calc_pos_by_orig = {orig_pos: i for i, orig_pos in enumerate(calc_numeric["orig_pos"].tolist())}
        trigger_calc_positions = trigger_numeric["orig_pos"].map(calc_pos_by_orig)
        valid_trigger = trigger_calc_positions.notna()
        if not bool(valid_trigger.all()):
            trigger_numeric = trigger_numeric.loc[valid_trigger].copy()
            trigger_calc_positions = trigger_calc_positions.loc[valid_trigger]

        trigger_calc_positions = trigger_calc_positions.astype(int).reset_index(drop=True)
        trigger_results = trigger_numeric["ResultNum"].reset_index(drop=True)
        centerline_trigger = centerline_calc.iloc[trigger_calc_positions].reset_index(drop=True)
        std_trigger = std_calc.iloc[trigger_calc_positions].reset_index(drop=True)

        start_override = 0 if mode == "fixed_limit" else None
        _, spc_violating = evaluate_spc_rules_against_limits(
            trigger_results,
            centerline_trigger,
            std_trigger,
            window,
            [r.get() for r in self.rules],
            start_idx_override=start_override
        )

        if mode == "moving_average":
            centerline = pd.Series(np.nan, index=group_reset.index, dtype="float64")
            std_line = pd.Series(np.nan, index=group_reset.index, dtype="float64")
            centerline.iloc[calc_numeric["orig_pos"].to_numpy()] = centerline_calc.to_numpy()
            std_line.iloc[calc_numeric["orig_pos"].to_numpy()] = std_calc.to_numpy()
            # Get representative values for legend (last valid value)
            valid_cl = centerline.dropna()
            valid_std = std_line.dropna()
            cl_value = valid_cl.iloc[-1] if len(valid_cl) > 0 else 0
            std_value = valid_std.iloc[-1] if len(valid_std) > 0 else 0
            centerline_label = f"Moving Avg ({window}) X\u0304={cl_value:.4f}"
        elif mode == "fixed":
            baseline = calc_results.iloc[:window]
            fixed_mean_val = baseline.mean()
            fixed_std_val = baseline.std(ddof=1) if len(baseline) > 1 else 0.0
            centerline = pd.Series(fixed_mean_val, index=group_reset.index, dtype="float64")
            std_line = pd.Series(fixed_std_val, index=group_reset.index, dtype="float64")
            cl_value = fixed_mean_val
            std_value = fixed_std_val
            centerline_label = f"Fixed CL (first {window}) X\u0304={cl_value:.4f}"
        elif mode == "fixed_limit":
            centerline = pd.Series(fl_mean, index=group_reset.index, dtype="float64")
            std_line = pd.Series(fl_std, index=group_reset.index, dtype="float64")
            cl_value = fl_mean
            std_value = fl_std
            centerline_label = f"Fixed Limit X\u0304={cl_value:.4f}"
        else:  # grand_mean
            grand_mean = calc_results.mean()
            grand_std = calc_results.std(ddof=1) if len(calc_results) > 1 else 0.0
            centerline = pd.Series(grand_mean, index=group_reset.index, dtype="float64")
            std_line = pd.Series(grand_std, index=group_reset.index, dtype="float64")
            cl_value = grand_mean
            std_value = grand_std
            centerline_label = f"Grand Mean X\u0304={cl_value:.4f}"

        upper_3s = centerline + 3 * std_line
        lower_3s = centerline - 3 * std_line
        upper_2s = centerline + 2 * std_line
        lower_2s = centerline - 2 * std_line
        upper_1s = centerline + 1 * std_line
        lower_1s = centerline - 1 * std_line

        # Determine the effective baseline window for skipping violations on chart
        effective_window = 0 if mode == "fixed_limit" else window

        violating_sns = []
        for i in spc_violating:
            if i < len(trigger_numeric):
                orig_pos = int(trigger_numeric.iloc[i]["orig_pos"])
                if orig_pos < effective_window:
                    continue  # skip points in the baseline window
                sn_value = trigger_numeric.iloc[i]["SN"]
                if pd.notna(sn_value) and sn_value not in prior_failed_sns and sn_value not in violating_sns:
                    violating_sns.append(sn_value)

        violating_indices = [
            int(trigger_numeric.iloc[i]["orig_pos"])
            for i in spc_violating
            if i < len(trigger_numeric)
        ]
        # Only show violations on chart positions beyond the baseline window
        violating_indices = sorted(set(idx for idx in violating_indices if idx >= effective_window))

        if violating_sns:
            display_sns = violating_sns[:5]
            flagged_label = "Flagged units: " + ", ".join(display_sns)
            if len(violating_sns) > 5:
                flagged_label += f" ... (+{len(violating_sns) - 5} more)"
            fig.suptitle(flagged_label, fontsize=10, y=0.98)
            fig.subplots_adjust(top=0.86)

        normal_idx = [i for i in range(len(results)) if i not in violating_indices]
        if normal_idx:
            ax.plot([i + 1 for i in normal_idx], [results.iloc[i] for i in normal_idx],
                    marker='o', label='Unit Reading', color='blue', linestyle='None', markersize=3)

        if violating_indices:
            # Show the measured value of the latest violating point in the legend
            latest_viol_idx = violating_indices[-1]
            latest_viol_value = results.iloc[latest_viol_idx] if latest_viol_idx < len(results) else None
            viol_label = f'SPC Failure = {latest_viol_value:.4f}' if latest_viol_value is not None else 'SPC Failure'
            ax.plot([i + 1 for i in violating_indices], [results.iloc[i] for i in violating_indices],
                    marker='o', color='red', linestyle='None', markersize=3, label=viol_label, zorder=5)

        ax.plot(centerline.index + 1, centerline, label=centerline_label, color='orange', linewidth=2)

        valid = (~centerline.isna()) & (~std_line.isna())

        # Compute representative sigma values for legend labels
        u3s_val = cl_value + 3 * std_value
        l3s_val = cl_value - 3 * std_value
        u2s_val = cl_value + 2 * std_value
        l2s_val = cl_value - 2 * std_value
        u1s_val = cl_value + 1 * std_value
        l1s_val = cl_value - 1 * std_value

        ax.plot((upper_3s.index[valid] + 1), upper_3s[valid], label=f'+3\u03c3 = {u3s_val:.4f}', color='red', linestyle='--')
        ax.plot((lower_3s.index[valid] + 1), lower_3s[valid], label=f'-3\u03c3 = {l3s_val:.4f}', color='red', linestyle='--')
        ax.plot((upper_2s.index[valid] + 1), upper_2s[valid], label=f'+2\u03c3 = {u2s_val:.4f}', color='darkorange', linestyle=':')
        ax.plot((lower_2s.index[valid] + 1), lower_2s[valid], label=f'-2\u03c3 = {l2s_val:.4f}', color='darkorange', linestyle=':')
        ax.plot((upper_1s.index[valid] + 1), upper_1s[valid], label=f'+1\u03c3 = {u1s_val:.4f}', color='gold', linestyle=':')
        ax.plot((lower_1s.index[valid] + 1), lower_1s[valid], label=f'-1\u03c3 = {l1s_val:.4f}', color='gold', linestyle=':')

        if ll is not None:
            ax.axhline(ll, color='green', linestyle='-.', label='Lower Spec')
        if ul is not None:
            ax.axhline(ul, color='purple', linestyle='-.', label='Upper Spec')

        ax.set_title(f'WECO SPC Chart: {param}')
        ax.set_xlabel('Unit Number')
        ax.set_ylabel('Result')
        ax.legend(loc='upper left', bbox_to_anchor=(1.01, 1), borderaxespad=0, fontsize=8)
        ax.grid(True)
        fig.subplots_adjust(right=0.78)  # make room for legend on the right

        if len(violating_sns) == 1:
            sn = violating_sns[0]
        elif len(violating_sns) > 1:
            sn = f"MULTI-{len(violating_sns)}units"
        elif latest_violating_sn:
            sn = latest_violating_sn
        elif len(trigger_group) > 0 and "SN" in trigger_group.columns and not pd.isna(trigger_group["SN"].iloc[-1]):
            sn = trigger_group["SN"].iloc[-1]
        else:
            sn = "UNKNOWN"

        now = datetime.now().strftime('%m-%d-%Y--%H-%M-%S')

        rule_number = 'RuleX'
        for i in range(1, 5):
            if f'Rule {i}' in self.violated_text.get():
                rule_number = f'Rule{i}'
                break

        sn_safe = sanitize_filename_component(sn)
        filename = f"{sn_safe}-{now}--{rule_number}.png"
        output_dir = os.path.join(SPC_OUTPUT_BASE, self.part_number.get().strip())
        os.makedirs(output_dir, exist_ok=True)
        save_path = os.path.join(output_dir, filename)
        try:
            fig.savefig(save_path)
            print(f"Saved SPC plot: {save_path}")
        except Exception as e:
            print(f"Failed to save SPC plot to {save_path}: {e}")

        plt.close(fig)


def main():
    root = tk.Tk()
    app = SPCApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
