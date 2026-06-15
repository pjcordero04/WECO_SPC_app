# WECO SPC Control v2

A real-time Statistical Process Control (SPC) monitoring application that applies the **Western Electric Company (WECO) rules** to cable assembly test data. Built with Python and Tkinter, it continuously watches for new test results, evaluates control chart rules, and triggers visual alarms when process deviations are detected.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![License](https://img.shields.io/badge/License-Internal-yellow)

---

## Features

- **Real-time monitoring** — Polls for new test data every second and evaluates SPC rules automatically
- **All 4 WECO rules** implemented and individually toggleable:
  | Rule | Description |
  |------|-------------|
  | 1 | One point beyond 3σ from the centerline |
  | 2 | Eight or more consecutive points on the same side of the centerline |
  | 3 | Two out of three consecutive points beyond 2σ (same side) |
  | 4 | Four out of five consecutive points beyond 1σ (same side) |
- **Full-screen flashing alarm banner** — Replaces modal popups for maximum visibility on the production floor
- **Automatic SPC chart generation** — Saved to disk with violation markers and control limits
- **CSV violation logging** — Every triggered rule is logged with serial number, parameter, rule, and timestamp
- **OPEN detection** — Automatically identifies and excludes invalid measurements (S2_1/S4_3 < -50 dB)
- **Parameter filtering** — Load parameter families from fourport tester limits JSON configuration
- **Configurable centerline** — Choose between Moving Average or Grand Mean mode
- **Failed-unit exclusion** — Optionally exclude failed units from centerline/σ calculations
- **SN deduplication** — Once a serial number triggers on one parameter, it won't re-trigger on subsequent parameters

---

## Screenshots

*SPC control chart with ±1σ, ±2σ, ±3σ bands, spec limits, and flagged violation points:*

![alt text](3100520083-20260508-161629-01-2901-01871-06-15-2026--14-33-30--Rule1.png)


---

## Requirements

### Python Dependencies

```
pandas
numpy
matplotlib
```

### System Requirements

- Windows OS
- Python 3.8+
- Fourport tester config directory at `C:\FourPortTester\config\constellation_config\controller`
- Test data directory at `C:\TESTDATA\<part_number>\`

---

## Installation

### Run from Source

```bash
pip install pandas numpy matplotlib
python weco_spc_gui_v2.py
```

### Build Standalone Executable

```bash
# Clean previous build artifacts (recommended)
rmdir /s /q build dist

# Build the exe
pyinstaller --onefile --windowed weco_spc_gui_v2.py
```

The executable will be located at `dist/weco_spc_gui_v2.exe`.

---

## Usage

1. **Launch the application** — Run `WECO_SPC.exe`
2. **Configure rules** — Check/uncheck which WECO rules to enforce
3. **Set window size** — Number of historical points for the rolling baseline (default: 30)
4. **Select centerline mode** — Moving Average (rolling window) or Grand Mean (all data)
5. **Enter Part Number** — The application monitors `C:\TESTDATA\<part_number>\` for new data
6. **Parameter Filter** *(optional)* — Click to select/deselect specific parameter families
7. **Click START** — Begins continuous monitoring
8. **Acknowledge alarms** — When a violation triggers, a full-screen red flashing banner appears; click **ACKNOWLEDGE** to dismiss and resume monitoring
9. **Click DONE TESTING** — Stops monitoring and resets the session

---

## Simulation Mode

If you don't have a live fourport tester connected, you can simulate the app by manually setting up the test data folder structure.

### Setup Steps

1. **Create the TESTDATA folder** on your C drive:
   ```
   C:\TESTDATA\
   ```

2. **Create a subfolder** with the part number you want to simulate:
   ```
   C:\TESTDATA\<part_number>\
   ```
   For example, if your part number is `3100520083`:
   ```
   C:\TESTDATA\3100520083\
   ```

3. **Add test data** — Place timestamped subfolders containing `summary.txt` files inside the part number folder:
   ```
   C:\TESTDATA\3100520083\
   ├── 20260615-143022\
   │   └── summary.txt
   ├── 20260615-143125\
   │   └── summary.txt
   └── ...
   ```

4. **Launch the app**, enter the part number (e.g. `3100520083`), and click **START**

5. **To simulate real-time data**, drop new timestamped subfolders with `summary.txt` files while the app is running — it polls every second and will pick up new results automatically

> **Note:** The included `move data 1by1.zip` contains sample test data that can be used for simulation. Extract its contents into your `C:\TESTDATA\<part_number>\` folder to try the app.

---

## Directory Structure

```
C:\TESTDATA\
└── <part_number>\
    └── <timestamp_folder>\         # e.g., 20260615-143022
        ├── summary.txt             # Test results file
        └── *.zip                   # Or zipped results containing summary.txt

C:\TESTDATA\SPC\
└── <part_number>\
    ├── <SN>-<date>--<time>--Rule<N>.png   # SPC violation charts
    └── <part_number>_spc_violations.csv    # Violation log

C:\FourPortTester\config\constellation_config\controller\
├── parts\
│   └── <first_6_digits>\
│       └── <part_number>.json      # Part config → references limits file
└── limits\
    └── <limits_name>.json          # Parameter families and spec limits
```

---

## How It Works

### Data Flow

```
Test Station → summary.txt → App parses results → WECO evaluation → Alarm / Log / Chart
```

1. The fourport tester writes `summary.txt` files (or zips) to timestamped subfolders
2. The app reads up to 160 recent subfolders to build a historical dataset
3. OPEN units (S2_1/S4_3 < -50 dB) are detected and excluded from downstream analysis
4. For each monitored parameter:
   - Failed units are optionally excluded from the baseline calculation
   - Centerline and σ are computed from the calculation population
   - The latest data point is evaluated against all enabled WECO rules
5. If a rule is triggered:
   - A flashing red alarm banner appears (requires manual acknowledgment)
   - An SPC chart image is saved to `C:\TESTDATA\SPC\<part_number>\`
   - The violation is logged to CSV
   - The serial number is marked to prevent re-triggering on other parameters

### WECO Rule Evaluation

The app evaluates whether the **latest point** participates in any rule violation pattern:

- **Rule 1**: Direct comparison — is the latest point beyond ±3σ?
- **Rule 2**: Run detection — does the latest point extend or complete a run of 8+ same-side points?
- **Rule 3**: Pattern check — is the latest point part of a 2-of-3 beyond ±2σ pattern?
- **Rule 4**: Pattern check — is the latest point part of a 4-of-5 beyond ±1σ pattern?

---

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| Window Size | 30 | Number of points for moving average / baseline establishment |
| Centerline Mode | Moving Average | Toggle between rolling window and grand mean |
| Exclude Failed | Enabled | Remove failed units from σ calculation |
| OPEN Threshold | -50.0 dB | S2_1/S4_3 readings below this invalidate the unit |
| Poll Interval | 1000 ms | How often the app checks for new data |
| History Depth | 160 subfolders | Maximum historical subfolders loaded |

---

## Changes from v1

- ❌ No more popup modal dialogs for violations
- ✅ Full-screen flashing red banner with **ACKNOWLEDGE** button
- ❌ No pop-up plot windows (matplotlib uses non-interactive `Agg` backend)
- ✅ Charts saved silently to `C:\TESTDATA\SPC\<part_number>\`
- ✅ SN deduplication — once a unit triggers SPC on one parameter, it won't trigger again on subsequent parameters

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Waiting for data..." stays indefinitely | Verify that summary.txt files are being written to `C:\TESTDATA\<part_number>\` |
| Parameter Filter shows no parameters | Ensure the part number JSON exists in `C:\FourPortTester\config\...\parts\` |
| Charts not saving | Check write permissions on `C:\TESTDATA\SPC\` |
| App doesn't detect new tests | Confirm the tester writes to timestamped subfolders (format: `YYYYMMDD-HHMMSS`) |

---

## License

Internal use only — Koch Industries / Molex.
