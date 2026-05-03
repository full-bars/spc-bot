# Autumsky VWP Plotter - Reference Implementation

This directory contains the JavaScript source files from autumsky.us's VWP Hodograph plotter. These files are referenced during the hodograph refactor to ensure high-fidelity replication of the multi-pane layout and meteorological calculations.

## Key Files & Purpose

### Core Architecture

**`bbox.js`** (977 bytes)
- Bounding box coordinate transformation logic
- Maps meteorological coordinates (knots, km) ↔ pixel coordinates
- Essential for multi-pane layout (hodograph, SR wind, parameters)
- **Port to**: Python class with scale/translate methods

**`context.js`** (7.2K)
- Context2DWrapper: Proxy around CanvasRenderingContext2D
- Intercepts canvas calls (moveTo, lineTo, arc, etc.)
- Auto-transforms all coords using active BBox
- **Port to**: Wrapper around matplotlib Axes or PIL ImageDraw

### Meteorological Engine

**`parms.js`** (7.5K)
- Core calculations: SRH, bulk shear, Bunkers movers, critical angle
- Storm motion derivations
- Mean wind calculations
- **Port to**: Direct Python translation (math.hypot, numpy)

**`vwp.js`** (37K)
- VWP data structure: u, v, alt arrays
- Binary parsing from NEXRAD Level 2 files
- Pre-calculation of all parameters after parse
- **Port to**: Adapt to existing `vad_reader.py` output format

### Rendering

**`hodo.js`** (22K)
- Hodograph rendering logic
- Wind vector plotting (colored by altitude)
- Marker placement (Bunkers LM/RM, mean wind, etc.)
- Height AGL labels on curve
- **Port to**: Use new BBox system, call parms.js equivalents

**`vwp_container.js`** (21K)
- High-level orchestrator
- Frame management and animation
- Multi-pane layout control
- **Port to**: Discord embed generator + matplotlib figure manager

### Utilities

**`utils.js`** (2.5K)
- Helper functions (angle normalization, distance calc)
- Wind component operations

---

## Porting Strategy

### Phase 1: Foundation
1. **Port `bbox.js`** → Python BBox class
2. **Port `context.js`** → Canvas wrapper (matplotlib or PIL)
3. **Port `utils.js`** → Helper functions

### Phase 2: Meteorological Engine
1. **Port `parms.js`** → Direct math translation
2. **Adapt `vwp.js`** → Use existing VAD reader output

### Phase 3: Rendering
1. **Port `hodo.js`** → Use new BBox system
2. **Add SR Wind graph** → New pane with srwind[i] = hypot(u[i] - smeanwind_u, v[i] - smeanwind_v)
3. **Add parameters table** → Text layout on grid

### Phase 4: Integration
1. Wire up `vwp_container.js` logic for Discord embeds
2. Integrate with VAD Recorder GIF generation

---

## Implementation Notes

- **Canvas vs Matplotlib**: autumsky uses HTML5 Canvas. Python options:
  - **matplotlib**: More meteorological tool integration, slower GIF generation
  - **PIL/Pillow**: Direct bitmap rendering, faster for animations
  - **Recommendation**: Start with matplotlib for compatibility, switch to PIL if GIF generation is too slow

- **Coordinate System**: autumsky uses Cartesian coords (x=knots east, y=knots north) with origin at center. BBox handles projection to screen.

- **Pre-calculation**: All meteorological parameters must be computed once after VWP parse, stored, and reused during rendering. This is critical for performance.

- **Layer-mean lines**: For SR Wind graph, include horizontal reference lines at:
  - 0-2 km mean wind
  - 4-6 km mean wind
  - These provide critical context for shear depth

---

## References in Active Development

- **Event VAD Recorder**: Uses refactored hodograph renderer to generate time-evolution GIFs
- **Hodograph Refactor** (`feat/hodograph-refactor`): Direct port of this architecture
- **Progress tracking**: See `progress.md` for design decisions and brainstorm

---

*Source: autumsky.us/vad (downloaded May 3, 2026)*
