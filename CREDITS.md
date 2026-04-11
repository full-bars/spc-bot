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
