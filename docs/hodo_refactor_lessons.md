# Lessons Learned: Hodograph Renderer Refactor (AutumSky Port)

## Overview
This document summarizes the technical findings from an experimental port of the autumsky.us hodograph renderer to Python (using PIL/Pillow). The experimental code is preserved on the `experiment/hodo-refactor-fidelity` branch.

## 1. Canvas Partitioning (BBox System)
- **Challenge**: The original rendering logic tended to overlay elements on top of each other because it shared a single drawing context.
- **Solution**: Implementing a `BBox` (Bounding Box) system allowed us to define three distinct logical regions:
    - **Hodograph Pane (Left 65%)**: Circular plots and wind vectors.
    - **Parameters Table (Top Right 35%)**: High-density meteorological data.
    - **SR Wind Plot (Bottom Right 35%)**: Vertical profile of storm-relative speed.
- **Key Insight**: The `Context2DWrapper` proxy must maintain separate `bbox_pixels` and `bbox_data` pairs for each pane to allow independent coordinate systems (e.g., kts/km in the hodo vs. kts/m in the SR wind plot) to coexist on one image.

## 2. PIL Rendering Limitations
- **Dashed Lines**: PIL's `ImageDraw.line` does not support dashed patterns. We solved this with a custom `Context2DWrapper.stroke()` method that manually iterates along the path vector to draw ink/gap segments.
- **Alpha Blending**: To achieve the "halo" effect for data uncertainty (RMS error), we used `draw.ellipse` with a highly transparent RGBA fill (Alpha ~25/255).
- **Coordinate Flipping**: Pilot error in the first pass involved incorrect Y-axis mapping. Meteorology uses "Y-up" (positive is higher altitude), while screen pixels use "Y-down" (positive is lower on the screen). Linear interpolation must account for this flip at the `BBox` level.

## 3. Meteorological Engine Parity
- **Interpolation**: Radar VWP data often lacks points at exact thresholds (e.g., 500m, 1km). The `met_engine.py` needs robust `np.interp` calls for both the "tip" and "tail" of layers to ensure SRH and Bunkers calculations match professional software.
- **Deprecation Note**: `np.trapz` is deprecated in newer Numpy versions; `np.trapezoid` should be used instead.
- **SRH Integration**: We found that integrating SRH on a fine-grained 20m vertical grid (via interpolation) yields values closer to reference snapshots than integrating on raw, sparse VWP levels.

## 4. Design Fidelity
- **DTM (Deviant Tornado Motion)**: This is traditionally styled as an open triangle with a dashed magenta trace to the Right Mover (RM) marker.
- **Surface Connection**: The dashed red segment from surface wind to 0km radar level is a critical visual cue for low-level shear.
- **Marker Styling**: Using black background circles with white centered text (using PIL `anchor="mm"`) provides the best readability for altitude labels.

## Next Steps for Future Iterations
- Implement the `ProcessPoolExecutor` worker pool before enabling GIF generation to prevent blocking the event loop.
- Use the `experiment/hodo-refactor-fidelity` branch as a reference for the final rendering engine.
