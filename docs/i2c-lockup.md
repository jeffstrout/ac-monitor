# I²C Bus Lockup — Investigation & Status

Running record of an intermittent fault where the Sequent HAT stops responding on the I²C
bus. **Open issue — leading theory identified, fix test in progress.**

## Symptom

The card drops off the I²C bus during normal operation:

- `ioplus 0 <cmd>` → `IO-PLUS id 0 not detected`
- `i2cdetect -y 1` **hangs mid-scan** (doesn't cleanly report the card absent)
- `pinctrl get 2,3` → **`lo/lo`** — both SDA (GPIO2) and SCL (GPIO3) held low
- The **Raspberry Pi stays fully healthy** — SSH responsive, only I²C to the card is dead
- **Recovers only on a full power cycle** (pulling power), never on `sudo reboot` and never on
  its own

The power-cycle-only recovery is the key tell: a physical short would persist after a power
cycle, so this is a **latched hung state** — the card's STM32 holding the bus lines — not a
wiring short.

## Diagnostic method

A soak script (`i2c_soak.sh`, kept on the Pi at `~/`) polls AD1/AD2/OPTO-5 every 10 s, guards
each `ioplus` call with a 5 s `timeout`, and logs a timestamped `*** I2C FAIL ***` the moment
the card stops responding. A later version also records **time-to-first-failure** and the I²C
baudrate the run used, so different configurations can be compared directly.

Reproduce / check results:

```bash
grep -E "FIRST FAILURE|FAIL" ~/i2c_soak*.log     # when did it drop, and after how long
```

## Findings

| Run | Config | Ran clean before lockup |
|---|---|---|
| Relay soak | cycling relays 1&2 every ~30 s | **~1 h 40 m** |
| Sensor-only soak | no relay activity at all | **~3 h 49 m** |
| Low-baudrate soak | I²C at 10 kHz, sensors only | *test in progress* |

### What was ruled out

- **Relays are NOT the root cause.** The first captured failure landed on a relay-cycle
  iteration, which briefly suggested relay switching (coil de-energize back-EMF) as the
  trigger. But a **sensor-only run with no relay activity also locked up** (~3 h 49 m). Relays
  appear to *accelerate* the failure (~1 h 40 m vs ~3 h 49 m, n=1 each) but are not required.
- **Not wiring / not moisture.** Fails with only sensor reads; the board stayed dry.
- **Not the opto/analog field wiring** — reproduces with minimal connections.

### Leading theory: Raspberry Pi 3B+ I²C clock-stretch hardware bug

The Broadcom I²C controller ("BSC") in the BCM2835/6/7 family — original Pi through the
**3B+** — mishandles **clock stretching**. When an I²C slave (here, the card's STM32) holds
the clock line low to buy processing time, the controller doesn't wait correctly and the
transaction can corrupt, occasionally leaving the bus wedged. This is a long-documented Pi
hardware erratum.

The observed behavior fits it well:

- **Variable, multi-hour time-to-failure** = a small per-transaction corruption probability
  that eventually latches — not a fixed-interval fault.
- **No relays needed** — just bus traffic.
- **Relays accelerate it** — more/noisier bus activity raises the per-hour odds.
- **Latches until power cycle** — a hung slave holding the line that the Pi's buggy controller
  can't clock out.

Not yet proven; marginal 5 V power or a flaky card unit could produce a similar signature.

## Test in progress: lower the I²C clock

The clock-stretch bug is speed-sensitive, so the bus was slowed from the default 100 kHz to
**10 kHz** as a free, low-risk test (keeps `ioplus` on `/dev/i2c-1`, unchanged):

```
# /boot/firmware/config.txt
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=10000
```

Then a full power cycle and a fresh sensor-only soak into `~/i2c_soak_10k.log`.

**Interpreting the result vs the 3 h 49 m baseline:**

- **Runs clean / fails much later** → confirms the Pi's I²C controller is the culprit → fix
  with software I²C (below) or a Pi 5.
- **Fails again at ~4 h** → the Pi's I²C is exonerated → investigate **5 V power** and the
  **card** instead; do *not* change the Pi.

## Fix options (if confirmed as the I²C controller)

1. **Software (bit-banged) I²C — preferred first step.** The `i2c-gpio` device-tree overlay
   bit-bangs I²C on the same GPIO2/3 pins and handles clock stretching correctly, sidestepping
   the hardware bug. Free, keeps the existing Pi, no rewiring. It's a standard, widely-used
   kernel overlay, not a hack. One integration detail: software I²C usually lands on a
   different bus number, and `ioplus` defaults to `/dev/i2c-1` — so the bus number must be
   forced or `ioplus` pointed at it. It's also *diagnostic*: if it makes the card solid, the
   hardware controller was the cause.
2. **New Pi — Pi 5, NOT Pi 4.** The Pi 4 (BCM2711) is reported to still carry the same BSC
   clock-stretch limitation, so it's a poor choice for escaping this specific bug. The Pi 5's
   I/O moved to the separate RP1 controller with a redesigned I²C that more likely resolves it
   — but verify rather than assume.

## Mitigations regardless of root cause

For an **unattended** monitor, the card *can* enter a state only a power cycle clears, so a
recovery path is essential:

- **HAT hardware watchdog** — the card power-cycles the Pi if it stops receiving periodic
  reset commands. **Must be verified against this specific failure**, since here the *card*
  hangs while the *Pi* stays healthy — it needs confirming that (a) the watchdog still fires
  when the card's STM32 is hung and (b) the power cut actually resets the card, not just the
  Pi. See the watchdog notes for the test procedure.
- **Poll less often** (every 30–60 s vs 10 s) — fewer transactions, higher MTBF if the fault
  is transaction-probability-driven.
- **Stiffer / cleaner 5 V supply** — rules out the power contribution.

## Status

- [x] Reproduced and characterized (latched hang, power-cycle-only recovery)
- [x] Relay-as-root-cause ruled out (sensor-only also fails)
- [x] Leading theory: Pi 3B+ I²C clock-stretch bug
- [ ] 10 kHz baudrate soak — **in progress**, compare time-to-failure vs 3 h 49 m
- [ ] If confirmed: software I²C on the 3B+
- [ ] Watchdog verified against this failure mode
