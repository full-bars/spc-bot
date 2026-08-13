# Credits

## vad-plotter

The hodograph generation library in `lib/vad_plotter/` is based on
[vad-plotter](https://github.com/tsupinie/vad-plotter) by
[Tim Supinie](https://github.com/tsupinie), used under the MIT License.

Modifications made for spc-bot integration:
- Import paths updated for use as a package under `lib/vad_plotter/`
- `vad.py` updated to resolve absolute paths when run as a subprocess
  from the project root

---

## SounderPy

Sounding data retrieval and plot generation uses
[SounderPy](https://github.com/kylejgillett/sounderpy) by
[Kyle J. Gillett](https://github.com/kylejgillett), used under the MIT License.

Gillett, K. J., 2025: SounderPy: An atmospheric sounding visualization and
analysis tool for Python. J. Open Source Software, 10(112), 8087.

No modifications were made to the SounderPy source code. It is used as a
dependency via pip.

---

## BowEcho

NEXRAD radar decoding and rendering for `/radar` uses several crates from
[BowEcho](https://github.com/FahrenheitResearch/bowecho) by
[Fahrenheit Research](https://github.com/FahrenheitResearch), used under the
MIT License.

No modifications were made to the BowEcho source. It is used as a dependency
via Cargo (git).

---

## Data Sources & Acknowledgements

This project relies heavily on the incredible data and services provided by the meteorological community:

- **National Weather Service (NWS)** and **Storm Prediction Center (SPC)**: The foundation of all real-time alerts, convective outlooks, and mesoscale discussions.
- **Iowa Environmental Mesonet (IEM)**: Provides critical API infrastructure, the `iembot` feed, and the Autoplot services used for warning and track maps.
- **NWS Damage Assessment Toolkit (DAT)**: The source for official damage survey tracks and EF-rating verification.
- **Tornado Archive**: Chronological data exploration and historical context are made possible via integration with the [Tornado Archive](https://tornadoarchive.com/) data explorer.
- **Colorado State University (CSU)**: Severe weather machine learning probabilities (CSU-MLP).
- **Northern Illinois University (NIU)**: Supercell Composite Parameter (SCP) forecast graphics by Victor Gensini.
- **National Center for Atmospheric Research (NCAR)**: WxNext2 AI convective hazard guidance.
- **Project WxEye** ([sonde.projectweathereye.org](https://sonde.projectweathereye.org)): Weather briefing and situational awareness data used by the `/wxsummary` command.
- **Wikipedia (Enhanced Fujita scale)**: The EF-scale damage indicator table in `config/ef_scale.json` (28 damage indicators, 224 degrees of damage with LB/EXP/UB wind estimates and the EF color palette) is reproduced from the [Enhanced Fujita scale](https://en.wikipedia.org/wiki/Enhanced_Fujita_scale) article, used under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The table derives from Texas Tech University's "A recommendation for an Enhanced Fujita scale" (McDonald & Mehta, 2006), the basis for NWS operational EF rating.
