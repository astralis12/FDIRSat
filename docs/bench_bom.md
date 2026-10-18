# Bench parts list (8S1P US18650VTC6 testbed), 18 Oct 2026

Budget, hobbyist parts (CLAUDE.md). Search terms are for Tokopedia; use its location filter
"Yogyakarta" to find sellers you can collect from. Prices and stock were not checked here.
Sizing follows CLAUDE.md "Plan doc updates": 8S, up to 33.6 V, FDIR cases at <= 1 C (~3 A),
1.5 C (~4.5 A) only as short overcurrent pulses.

| # | Part | Qty | Spec / why | Search terms |
|---|---|---|---|---|
| 1 | Current shunt for the INA226 | 1 | Replace the module's 0.1 ohm (R100) with 0.01 ohm: INA226 full scale is +-81.92 mV, so 0.1 ohm saturates at 0.82 A and 0.01 ohm gives ~8.2 A at 25 uA LSB. Either a 2512 SMD R010 rated 2-3 W, or an external 4-terminal shunt "10 A 75 mV" (7.5 mohm, ~10.9 A full scale). Calibrate against the multimeter | `resistor shunt 2512 R010 3W`, `shunt resistor 10A 75mV` |
| 2 | Electronic load | 1 | Atorch DL24P class (180 W, 0-200 V, 0-20 A, constant current/power). Use <= 100 W continuous (~3 A at 33.6 V) with its fan; 150 W only in short pulses | `DL24P electronic load 180W` |
| 3 | Fixed load resistors (3 load channels, per-unit loads) | 3-4 | Aluminium-housed wirewound with heatsink. At ~33 V: survival ~0.06 C (0.18 A) -> ~180 ohm 10 W; primary ~0.08 C -> ~140 ohm 10 W; secondary ~0.06 C -> ~180 ohm 10 W; task burst ~0.3 C (0.9 A) -> ~39 ohm 50 W | `resistor aluminium 50W 39 ohm`, `resistor aluminium 10W 180 ohm` |
| 4 | Load / charge-path switches | 4 | Logic-level MOSFET modules rated >= 15 A, >= 40 V DC (most hobby relays are rated only 30 V DC, below 33.6 V) | `modul mosfet D4184`, `mosfet switch module 15A 400W` |
| 5 | Battery temperature sensors | 3 | DS18B20 (median-of-3, ADR-0020), +-0.5 degC; 4.7 kohm pull-up; fix to the pack with Kapton tape and thermal paste | `DS18B20 waterproof probe`, `DS18B20 TO-92` |
| 6 | Cell taps | 2 | ADS1115 16-bit ADC modules (4 channels each) plus 0.1 % resistor dividers per tap (tap voltages reach 33.6 V; ADS1115 inputs must stay within its supply) | `ADS1115 module`, `resistor 0.1% 1/4W` |
| 7 | 8S BMS with balancing | 1 | Independent hardware protection, separate from the FDIR: overcharge ~4.25 V, over-discharge, overcurrent >= 10 A. Mandatory for Li-ion safety | `BMS 8S 20A balance li-ion 3.7V` |
| 8 | Fuse and wiring | 1 set | Inline blade fuse holder + 10 A fuse at the pack; XT60 connectors; 14-16 AWG silicone wire | `fuse holder blade inline`, `XT60`, `kabel silikon 14 AWG` |
| 9 | Cells and pack build | 8 (+1 spare) | Genuine VTC6 from a reputable seller (counterfeits are common; the C/5 capacity test will show it). Spot-welded nickel strip, not spring holders, at 3-4.5 A | `Sony VTC6 original`, `jasa spot welding baterai 18650` |
| 10 | USB isolator | 1 | Protects the laptop from pack faults on the DAQ/DUT USB link | `ADuM3160 USB isolator` |
| 11 | Fire safety | 1 set | LiPo safe bag or steel ammo box for the pack, ABC or CO2 extinguisher, sand bucket; never charge unattended | `lipo safe bag`, `ammo box besi`, `APAR` |

Already owned: STM32F411 Black Pill + ST-Link (DUT), INA226 module, 8S charger (CV fixed 4.2 V/cell,
ISS-049), multimeter. Still needed for DAQ board #2: a second Black Pill.
