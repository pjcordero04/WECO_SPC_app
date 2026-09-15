"""
ETEST CSV Parser — Shared module for parsing easywire electrical test report CSVs.

Used by both the ETEST WECO SPC app and the ETEST Statistics app.

Each CSV file represents one cable tested. It contains:
  - Header metadata (S/N, Cable Number, Test Date, Test Time, Final Result)
  - "Nets Hipot Status" section (IR measurements — not parsed here)
  - "Measured Values" section with WIRE and 4WIRE resistance measurements
"""

import os
import re
import pandas as pd
from datetime import datetime


def normalize_value(value_str):
    """
    Parse a measurement string like '223 mOhm' or '0.7 Ohm' into (float, str).

    Returns (value_as_float, unit_string) or (None, None) if unparseable.
    Handles 'Not tested' gracefully.
    """
    if not value_str or 'not tested' in str(value_str).lower():
        return None, None

    value_str = str(value_str).strip()
    m = re.match(r"([\d.]+)\s*(mOhm|Ohm|GOhm|MOhm)", value_str)
    if m:
        return float(m.group(1)), m.group(2)
    return None, None


def to_mohm(value, unit):
    """Convert a resistance value to milliohms for uniform comparison."""
    if value is None or unit is None:
        return None
    unit_lower = unit.lower()
    if unit_lower == 'mohm':
        return value
    elif unit_lower == 'ohm':
        return value * 1000.0
    elif unit_lower == 'gohm':
        return value * 1e9
    elif unit_lower == 'mohm' and unit == 'MOhm':
        # MOhm = megaohm
        return value * 1e9
    return None


def to_ohm(value, unit):
    """Convert a resistance value to Ohms."""
    if value is None or unit is None:
        return None
    if unit == 'mOhm':
        return value / 1000.0
    elif unit == 'Ohm':
        return value
    elif unit == 'GOhm':
        return value * 1e9
    elif unit == 'MOhm':
        return value * 1e6
    return None


def parse_etest_csv(file_path):
    """
    Parse one ETEST CSV file and return structured data.

    Returns a dict:
    {
        'file_path': str,
        'sn': str,
        'cable_number': int or None,
        'run_number': int or None,
        'test_date': str,
        'test_time': str,
        'final_result': str ('Passed' or 'Failed'),
        'test_name': str,
        'measurements': [
            {
                'type': 'WIRE' or '4WIRE',
                'from_point': str (e.g. 'END1_1'),
                'to_point': str (e.g. 'END2_1'),
                'value': float,
                'unit': str,
                'upper_limit': float,
                'limit_unit': str,
                'parameter': str (e.g. 'WIRE_END1_1_END2_1'),
            },
            ...
        ]
    }
    """
    result = {
        'file_path': file_path,
        'sn': 'UNKNOWN',
        'cable_number': None,
        'run_number': None,
        'test_date': '',
        'test_time': '',
        'final_result': 'UNKNOWN',
        'test_name': '',
        'measurements': [],
    }

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return result

    lines = text.splitlines()

    # --- Parse header metadata ---
    for line in lines:
        line_stripped = line.strip()

        # S/N
        m = re.match(r"\s*S/N:\s*,\s*(.+)", line_stripped)
        if m:
            result['sn'] = m.group(1).strip()
            continue

        # Cable Number
        m = re.match(r"\s*Cable Number:\s*,\s*(\d+)", line_stripped)
        if m:
            result['cable_number'] = int(m.group(1))
            continue

        # Run Number
        m = re.match(r"\s*Run Number:\s*,\s*(\d+)", line_stripped)
        if m:
            result['run_number'] = int(m.group(1))
            continue

        # Test Date
        m = re.match(r"\s*Test Date:\s*,\s*(.+)", line_stripped)
        if m:
            result['test_date'] = m.group(1).strip()
            continue

        # Test Time
        m = re.match(r"\s*Test Time:\s*,\s*(.+)", line_stripped)
        if m:
            result['test_time'] = m.group(1).strip()
            continue

        # Final Test Result
        m = re.match(r"\s*Final Test Result:\s*,\s*(.+)", line_stripped)
        if m:
            result['final_result'] = m.group(1).strip()
            continue

        # Test Name
        m = re.match(r"\s*Test Name:\s*,\s*(.+)", line_stripped)
        if m:
            result['test_name'] = m.group(1).strip()
            continue

    # --- Parse "Measured Values" section ---
    in_measured_section = False
    for line in lines:
        line_stripped = line.strip()

        if 'Title:' in line and 'Measured Values' in line:
            in_measured_section = True
            continue

        if in_measured_section and line_stripped.startswith('#,'):
            # This is the header row — skip
            continue

        if in_measured_section and line_stripped.startswith('Title:'):
            # Entering a new section — stop
            break

        if in_measured_section and line_stripped:
            # Parse measurement row
            # Format: " 1, WIRE, END1_1, END2_1, 0.8 Ohm, 2.6 Ohm, Measured 0.8 Ohm"
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 6:
                try:
                    row_num = parts[0].strip()
                    if not row_num.isdigit():
                        continue

                    instr_type = parts[1].strip()  # WIRE or 4WIRE
                    if instr_type not in ('WIRE', '4WIRE'):
                        continue

                    from_point = parts[2].strip()
                    to_point = parts[3].strip()
                    value_str = parts[4].strip()
                    expected_str = parts[5].strip()

                    value, unit = normalize_value(value_str)
                    upper_limit, limit_unit = normalize_value(expected_str)

                    parameter = f"{instr_type}_{from_point}_{to_point}"

                    result['measurements'].append({
                        'type': instr_type,
                        'from_point': from_point,
                        'to_point': to_point,
                        'value': value,
                        'unit': unit,
                        'lower_limit': None,  # Placeholder — some tests may have a lower limit
                        'upper_limit': upper_limit,
                        'limit_unit': limit_unit,
                        'parameter': parameter,
                    })
                except (ValueError, IndexError):
                    continue

    return result


def parse_timestamp(test_date, test_time):
    """
    Parse the Test Date and Test Time fields into a datetime object.

    Handles formats like '6/22/2026' and '10:52:32 AM'.
    Returns datetime or None.
    """
    if not test_date or not test_time:
        return None
    try:
        combined = f"{test_date} {test_time}"
        return datetime.strptime(combined, "%m/%d/%Y %I:%M:%S %p")
    except (ValueError, TypeError):
        return None


def load_folder(folder_path, include_failed=True):
    """
    Load all ETEST CSV files from a folder and return a combined DataFrame.

    Parameters
    ----------
    folder_path : str
        Path to the folder containing CSV files.
    include_failed : bool
        If False, exclude files where Final Test Result is 'Failed'.

    Returns
    -------
    pd.DataFrame with columns:
        File, SN, CableNumber, RunNumber, TestDate, TestTime, Timestamp,
        FinalResult, Parameter, Type, FromPoint, ToPoint,
        Value, Unit, UpperLimit, LimitUnit
    """
    rows = []

    if not os.path.isdir(folder_path):
        return pd.DataFrame()

    csv_files = sorted([
        f for f in os.listdir(folder_path)
        if f.lower().endswith('.csv') and os.path.isfile(os.path.join(folder_path, f))
    ])

    for fname in csv_files:
        fpath = os.path.join(folder_path, fname)
        parsed = parse_etest_csv(fpath)

        if not include_failed and parsed['final_result'].lower() == 'failed':
            continue

        # Skip files with no measurements (failed tests show "Not tested")
        if not parsed['measurements']:
            continue

        timestamp = parse_timestamp(parsed['test_date'], parsed['test_time'])

        for meas in parsed['measurements']:
            if meas['value'] is None:
                continue  # Skip "Not tested" rows

            rows.append({
                'File': fname,
                'SN': parsed['sn'],
                'CableNumber': parsed['cable_number'],
                'RunNumber': parsed['run_number'],
                'TestDate': parsed['test_date'],
                'TestTime': parsed['test_time'],
                'Timestamp': timestamp,
                'FinalResult': parsed['final_result'],
                'Parameter': meas['parameter'],
                'Type': meas['type'],
                'FromPoint': meas['from_point'],
                'ToPoint': meas['to_point'],
                'Value': meas['value'],
                'Unit': meas['unit'],
                'LowerLimit': meas['lower_limit'],
                'UpperLimit': meas['upper_limit'],
                'LimitUnit': meas['limit_unit'],
            })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Sort by timestamp if available, otherwise by cable number
    if 'Timestamp' in df.columns and df['Timestamp'].notna().any():
        df = df.sort_values('Timestamp').reset_index(drop=True)
    elif 'CableNumber' in df.columns:
        df = df.sort_values('CableNumber').reset_index(drop=True)

    return df


# --- Standalone test ---
if __name__ == "__main__":
    import sys

    test_folder = r"C:\Users\patric54\OneDrive - kochind.com\Documents\2026\automation apps\WECO_SPC_app\26Awg 300pcs\LINK\2175420047_ 500_link"

    if len(sys.argv) > 1:
        test_folder = sys.argv[1]

    print(f"Loading ETEST CSVs from: {test_folder}")
    df = load_folder(test_folder)

    if df.empty:
        print("No data loaded!")
        sys.exit(1)

    print(f"\nLoaded {len(df)} measurement rows from {df['File'].nunique()} files")
    print(f"\nParameters found: {df['Parameter'].nunique()}")
    for param in sorted(df['Parameter'].unique()):
        subset = df[df['Parameter'] == param]
        print(f"  {param}: n={len(subset)}, "
              f"mean={subset['Value'].mean():.3f}, "
              f"std={subset['Value'].std():.3f}, "
              f"min={subset['Value'].min():.3f}, "
              f"max={subset['Value'].max():.3f} {subset['Unit'].iloc[0]}, "
              f"upper_limit={subset['UpperLimit'].iloc[0]} {subset['LimitUnit'].iloc[0]}")
