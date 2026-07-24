# Thermistor Calibration

Calibration of the 10 kΩ NTC thermistors used for air/refrigerant temperature sensing on
the Sequent Home Automation HAT analog inputs.

> **Status:** field calibration from two physical fixed points (ice bath + boiling water).
> Good to roughly **±0.5 °C absolute** and **~±0.2 °C on ΔT**. See [Future work](#future-work)
> for the path to a full Steinhart–Hart fit.

## Sensors

| Item | Value |
|---|---|
| Part | DROK 10 kΩ **B3950** NTC, waterproof stainless probe, 3 m lead |
| Source | Amazon `B01MZ6Y336` |
| Nominal | 10 kΩ @ 25 °C |
| Beta (B25/85) | **3950** — confirmed from the product listing/datasheet |
| Range | −25 to 125 °C (covers all HVAC use; boiling-water calibration is in-spec) |
| Type | NTC (resistance falls as temperature rises) |

## Wiring

Thermistors land on HAT analog inputs **AD1–AD4** (the "Temperature Measurement, 4 of 8
channels" configuration, User's Guide V5 p. 12). Each analog input has an **internal 15 kΩ
pull-up to 3.3 V**; the thermistor completes a divider to ground.

- Each thermistor: `ADx` → that connector's `GND` (pin 1).
- Connector pin order (top→bottom): `GND` (1), `AD4` (2), `AD3` (3), `AD2` (4), `AD1` (5).
- Confirm each probe reads a sane value; the analog connectors are easy to land one pin off.

Currently wired: **AD1, AD2**. Planned: AD3, AD4.

## Conversion math

The board has no thermistor-to-temperature command — read the raw voltage with
`ioplus 0 adcrd <ch>` and convert in software.

**1. Voltage → resistance** (15 kΩ pull-up to 3.3 V, thermistor to GND):

```
R = 15000 * V / (3.3 - V)
```

**2. Resistance → temperature** (Beta equation, NTC):

```
1/T = 1/298.15 + (1/3950) * ln(R / 10000)
°C  = T - 273.15
```

**3. Calibration correction** (see below):

```
true_C = 1.024 * reading_C - 1.20
```

## Calibration data

**Location:** Tyler, TX 75701 — elevation ~545 ft (166 m). Water boils here at
**≈ 99.4 °C**, not 100 °C (boiling point drops ~1 °C per 300 m).

| Point | True temp | AD1 read | AD2 read | Error |
|---|---|---|---|---|
| Ice / water slurry | 0.0 °C (32.0 °F) | 34.2 °F | 34.0 °F | **+1.2 °C high** |
| Rolling boil | 99.4 °C (211 °F) | 98.2 °C | 98.2 °C | **−1.2 °C low** |

Channel-to-channel agreement in the ice bath: **0.2 °F** — so ΔT is already good to ~±0.2 °C
uncalibrated.

### Interpretation

The error is **equal and opposite** across the range (+1.2 °C at cold, −1.2 °C at hot): a
**gain error**, not an offset. The reading is compressed toward mid-scale, consistent with the
board's 15 kΩ pull-up not being exactly 15.00 kΩ. A single offset would *not* fix this — a
two-point (slope + intercept) correction does.

The Beta value is confirmed correct (3950), so the residual error is analog-component
tolerance (pull-up + thermistor R₀), not the temperature model.

### Derivation of the linear correction

Two points, `true_C = a * reading_C + b`:

- Ice: `0.0 = a * 1.17 + b` (34.1 °F avg reading = 1.17 °C)
- Boil: `99.4 = a * 98.2 + b`

Solving: **a = 1.024, b = −1.20** → `true_C = 1.024 * reading_C − 1.20`.

The same gain is applied to both channels (they agreed to 0.2 °F in the ice). For per-channel
correction, boil each probe individually.

### Caveats

- The **ice point is the solid anchor** (0 °C regardless of weather/altitude). The **boiling
  point carries ~±0.5 °C** of day-to-day barometric uncertainty, so absolute accuracy is
  ~±0.5 °C.
- Calibration points were recorded in **°F/°C, not raw volts** — a proper Steinhart–Hart fit
  wants the volts (see Future work).
- Correction verified for AD1/AD2 only. AD3/AD4 should be spot-checked in the ice bath (and
  ideally boiling) once wired; if they agree within tolerance, the shared correction applies.

## Reading the sensors

**Calibrated, both channels, screen cleared each poll** (paste into an SSH session):

```bash
while :; do V1=$(ioplus 0 adcrd 1); V2=$(ioplus 0 adcrd 2); clear; echo "$V1 $V2" | awk '{for(i=1;i<=2;i++){v=$i; r=15000*v/(3.3-v); b=3950; t=1/(1/298.15+(1/b)*log(r/10000))-273.15; tc=1.024*t-1.20; printf "AD%d = %.3f V   T = %5.1f °C  (%5.1f °F)\n", i, v, tc, tc*9/5+32}}'; sleep 1; done
```

Expected after correction: **~32 °F in ice**, **~211 °F at a Tyler boil**.

**Raw voltage only** (quickest check that a channel is alive, no math):

```bash
ioplus 0 adcrd 1
```

**One-shot both channels, raw volts** (capture calibration points here):

```bash
ioplus 0 adcrd 1; ioplus 0 adcrd 2
```

## Future work

- **Capture raw voltages** at both fixed points (`ioplus 0 adcrd 1; ioplus 0 adcrd 2`) and fit
  **Steinhart–Hart** directly from volts. This solves the actual pull-up value and retires the
  linear patch, reaching ~±0.3 °C.
- **Per-channel calibration** for AD3/AD4 once wired.
- Fold the calibrated conversion into the reader application (`config.yaml`: per-channel
  `a`/`b` or Steinhart–Hart coefficients).
