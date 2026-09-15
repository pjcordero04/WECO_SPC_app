# WECO SPC Apps — Real-Time Statistical Process Control

Two SPC monitoring applications that apply the **Western Electric Company (WECO) rules** to cable assembly test data. Built with Python and Tkinter for real-time monitoring on the production floor.

| App | File | Description |
|-----|------|-------------|
| **ETEST WECO SPC** | `etest_weco_spc_app.py` | Monitors electrical test (WIRE/4WIRE) resistance data from CSV files |
| **SI WECO SPC** | `weco_spc_gui_v2.py` | Monitors signal integrity (S-parameter/impedance) data from fourport tester summary.txt files |

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-Internal-yellow)

---

## WECO Rules

Both apps implement the same 4 rules:

| Rule | Description |
|------|-------------|
| 1 | One point beyond 3σ from the centerline |
| 2 | Eight or more consecutive points on the same side of the centerline |
| 3 | Two out of three consecutive points beyond 2σ (same side) |
| 4 | Four out of five consecutive points beyond 1σ (same side) |

---

## Features

- **Real-time monitoring** — Polls for new test data every 1 second
- **3 centerline modes:**
  - **Moving Average** — Rolling window of the last N points
  - **Grand Mean** — Mean of all accumulated data
  - **Fixed Limit** — Upload a pre-computed summary CSV with Mean and Std Dev per parameter (evaluates from the 1st data point)
- **Flashing alarm banner** — Appears at the top of the screen when a violation is detected
- **Automatic SPC chart generation** — Saved as PNG with violation markers, sigma bands, and spec limits
- **CSV violation logging** — Every triggered rule is logged with timestamp and unit details
- **Pause / Resume** — Pause monitoring to change parameter filter, then resume without losing data
- **Grouped parameter filter** — Parameters organized by family with group checkboxes for bulk select/deselect
- **Mode-aware UI** — Window Size and Exclude Failed are disabled in Fixed Limit mode (not applicable)

---

## Requirements

### Standalone EXE (no Python needed)

| Requirement | Minimum |
|-------------|---------|
| Operating System | Windows 10 (64-bit) or later |
| RAM | ~200 MB |
| Disk space | ~110 MB per exe |

### Run from Source

| Requirement | Minimum |
|-------------|---------|
| Python | 3.9 or later |
| OS | Windows 10 |

```bash
pip install pandas numpy matplotlib
```

---

## Quick Start

### ETEST WECO SPC

```bash
python etest_weco_spc_app.py
```

1. Select a **Centerline Mode** (Moving Average, Grand Mean, or Fixed Limit)
2. If **Fixed Limit**: click Browse next to "Limit File" and select your summary CSV (e.g. `2175420047_500_link_etest_summary.csv`)
3. Select the **Data Folder** containing ETEST CSV files
4. *(Optional)* Click **PARAMETER FILTER** to select which parameters to monitor
5. Click **START** — monitoring begins
6. When a violation is detected, a red flashing banner appears — click **ACKNOWLEDGE** to dismiss

### SI WECO SPC

```bash
python weco_spc_gui_v2.py
```

1. Select a **Centerline Mode**
2. If **Fixed Limit**: click Browse and select your summary CSV (e.g. `3100520083_summary.csv`)
3. Enter the **Part Number** (e.g. `3100520083`)
4. *(Optional)* Click **PARAMETER FILTER** to select parameter families
5. Click **START** — the app monitors `C:\TESTDATA\FOURPORT_TESTDATA\<part_number>\` for new data
6. Acknowledge alarms as they appear

---

## Deriving the SPC Limit File

The **Fixed Limit** mode requires a summary CSV file containing pre-computed Mean and Std Dev for each parameter. This file is generated from your historical test data using the statistics/data plotter apps.

### For ETEST Data

1. Open the **ETEST Statistics App** (`etest_statistics_app.py`)
2. Click **Load Folder** and select a folder containing your baseline ETEST CSV files (e.g. 300+ cables of known-good production data)
3. The app computes statistics (Mean, Std Dev, Min, Max, Cpk, etc.) for every wire pair
4. Click **Download CSV** — save it as your limit file (e.g. `2175420047_500_link_etest_summary.csv`)
5. Use this CSV as the "Limit File" in the ETEST WECO SPC app's Fixed Limit mode

### For SI Data

1. Use your SI test data plotter/statistics tool to compute Mean and Std Dev for each S-parameter
2. Export the statistics as a CSV in the same wide format:
   - First column: statistic names (`Mean`, `Std Dev`, `Count`, etc.)
   - Remaining columns: one per parameter, with the parameter name as the column header
3. Use this CSV as the "Limit File" in the SI WECO SPC app's Fixed Limit mode

### Summary CSV Format

```
Statistic,  PARAM_1,     PARAM_2,     ...
Unit,       mOhm,        mOhm,        ...
Count,      321,          321,         ...
Mean,       194.3489,     221.9875,    ...
Std Dev,    2.0501,       1.2796,      ...
Min,        190.0000,     219.0000,    ...
Max,        203.0000,     230.0000,    ...
...
```

The SPC app reads the **Mean** and **Std Dev** rows to set the fixed centerline and sigma bands.

---

## Simulation Mode

If you don't have a live tester connected, use the included data mover scripts to simulate real-time testing.

### ETEST Simulation

**Files needed:**
- `move_etest_data.py` — copies individual CSV files one at a time
- Sample data: `sample_data/etest/2175420047_500_link.zip` (extract to get ~300 CSV files)
- Sample limit file: `sample_data/etest/2175420047_500_link_etest_summary.csv`

**Steps:**

1. Extract `sample_data/etest/2175420047_500_link.zip` to a temporary folder (this is your **source**)

2. Create an empty **destination** folder (e.g. `C:\TESTDATA\ETEST_SPC\LINK\500V`)

3. Launch the data mover:
   ```bash
   python move_etest_data.py
   ```
   - Set **Source Folder** to the extracted CSV folder
   - Set **Destination Folder** to `C:\TESTDATA\ETEST_SPC\LINK\500V`
   - Set delay (1 second default)
   - Click **Start** — it copies CSV files one at a time

4. Launch the SPC app:
   ```bash
   python etest_weco_spc_app.py
   ```
   - Select **Fixed Limit** mode
   - Load `sample_data/etest/2175420047_500_link_etest_summary.csv` as the limit file
   - Set **Data Folder** to `C:\TESTDATA\ETEST_SPC\LINK\500V`
   - Click **START** — the app will detect each new CSV as the mover copies it

### SI Simulation

**Files needed:**
- `move_data.py` — copies subfolders (containing zip files with summary.txt) one at a time

**Steps:**

1. Prepare a folder with timestamped subfolders containing your SI test data (zip files with `summary.txt` inside)

2. Create an empty destination folder (e.g. `C:\TESTDATA\FOURPORT_TESTDATA\3100520083`)

3. Launch the data mover:
   ```bash
   python move_data.py
   ```
   - Set **Source Folder** to your prepared data folder
   - Set **Destination Folder** to `C:\TESTDATA\FOURPORT_TESTDATA\3100520083`
   - Click **Start** — it copies subfolders one at a time

4. Launch the SPC app:
   ```bash
   python weco_spc_gui_v2.py
   ```
   - Select **Fixed Limit** mode and load your summary CSV
   - Enter Part Number: `3100520083`
   - Click **START**

---

## Building Standalone Executables

```bash
# ETEST SPC
pyinstaller --onefile --windowed --name "ETEST_WECO_SPC" ^
  --exclude-module PyQt5 --exclude-module PySide6 ^
  --exclude-module IPython --exclude-module nbformat --exclude-module zmq ^
  etest_weco_spc_app.py

# SI SPC
pyinstaller --onefile --windowed --name "SI_WECO_SPC" ^
  --exclude-module PyQt5 --exclude-module PySide6 ^
  --exclude-module IPython --exclude-module nbformat --exclude-module zmq ^
  weco_spc_gui_v2.py
```

Executables are output to `dist/ETEST_WECO_SPC.exe` and `dist/SI_WECO_SPC.exe`.

---

## File Structure

```
WECO_SPC_app/
├── etest_weco_spc_app.py         # ETEST SPC app
├── etest_csv_parser.py           # ETEST CSV file parser
├── etest_statistics_app.py       # ETEST statistics/plotter (generates limit CSV)
├── weco_spc_gui_v2.py            # SI SPC app
├── move_etest_data.py            # ETEST data mover (simulation)
├── move_data.py                  # SI data mover (simulation)
└── sample_data/
    └── etest/
        ├── 2175420047_500_link.zip                  # Sample ETEST test data (~300 cables)
        └── 2175420047_500_link_etest_summary.csv    # Sample limit file for Fixed Limit mode
```

### Output Directories

```
C:\TESTDATA\ETEST_SPC\<test_name>\
├── Cable<N>_SN<serial>-<date>--<time>--Rule<N>.png   # SPC violation charts
└── etest_spc_violations.csv                           # Violation log

C:\TESTDATA\SPC\<part_number>\
├── <SN>-<date>--<time>--Rule<N>.png                   # SPC violation charts
└── <part_number>_spc_violations.csv                   # Violation log
```

---

## License

Internal use only — Koch Industries / Molex.
