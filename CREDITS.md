# Credits

## vad-plotter

The hodograph generation library in `lib/vad_plotter/` is based on
[vad-plotter](https://github.com/tsupinie/vad-plotter) by
[Tim Supinie](https://github.com/tsupinie), used under the MIT License.

Modifications made for spc-bot integration:
- Import paths updated for use as a package under `lib/vad_plotter/`
- `vad.py` updated to resolve absolute paths when run as a subprocess
  from the project root
