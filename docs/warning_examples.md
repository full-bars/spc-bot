# Warning Format Examples

This document contains real examples of NWS warnings processed by SPC-Bot.
Use these as reference for warning text patterns when debugging or adding new detection logic.

**Note on Examples:** 
- Prior to 2026-05-07, tornado warning text was truncated to 500 characters in logs
- From 2026-05-07 forward, complete untruncated warning products are logged (see `[WARN_VTEC_RAW]` entries in `spc_bot.log`)
- Some examples below are reconstructed from partial logs; new complete examples will be added as severe warnings occur
- IEM AFOS archive is used for backfill when available

## Tornado Warnings

### Standard Radar-Indicated (Complete Example from Archive)

**Source:** IEM AFOS Archive, 2026-05-06 22:38 UTC

```
528 
WFUS54 KJAN 070338
TORJAN
MSC065-077-085-091-070445-
/O.NEW.KJAN.TO.W.0040.260507T0338Z-260507T0445Z/

BULLETIN - EAS ACTIVATION REQUESTED
Tornado Warning
National Weather Service Jackson MS
1038 PM CDT Wed May 6 2026

The National Weather Service in Jackson has issued a

* Tornado Warning for...
  Northern Marion County in south central Mississippi...
  Southeastern Lincoln County in south central Mississippi...
  Southern Lawrence County in south central Mississippi...
  Southwestern Jefferson Davis County in south central Mississippi...

* Until 1145 PM CDT.

* At 1038 PM CDT, a severe thunderstorm capable of producing a
  tornado was located near Ruth, or 12 miles northeast of Mccomb,
  moving east at 40 mph.

  HAZARD...Tornado.

  SOURCE...Radar indicated rotation.

  IMPACT...Flying debris will be dangerous to those caught without 
           shelter. Mobile homes will be damaged or destroyed. 
           Damage to roofs, windows, and vehicles will occur.  Tree 
           damage is likely.

* This dangerous storm will be near...
  Jayess and Topeka around 1045 PM CDT.
  Holly Springs and Tilton around 1055 PM CDT.
  Oak Vale and Morgantown around 1100 PM CDT.
  Goss and Society Hill around 1105 PM CDT.
  Columbia and Bunker Hill around 1110 PM CDT.

PRECAUTIONARY/PREPAREDNESS ACTIONS...

TAKE COVER NOW! Move to a basement or an interior room on the lowest
floor of a sturdy building. Avoid windows. If you are outdoors, in a
mobile home, or in a vehicle, move to the closest substantial shelter
and protect yourself from flying debris.

&&

LAT...LON 3118 9003 3134 9004 3133 9008 3135 9011
      3135 9036 3140 9039 3151 8985 3139 8974
      3118 8973
TIME...MOT...LOC 0338Z 263DEG 34KT 3133 9029 

TORNADO...RADAR INDICATED
MAX HAIL SIZE...<.75 IN

$$
```

**Detection:**
- Confidence: `radar_indicated` (contains "Radar indicated rotation" + "TORNADO...RADAR INDICATED")
- Severity: `standard` (no EMERGENCY or PDS designation)
- VTEC: `KJAN.TO.W.0040` — Office: KJAN (Jackson), Phenom: TO (Tornado), Sig: W (Warning), ETN: 0040

---

### Tornado Emergency + Confirmed

```
...TORNADO EMERGENCY FOR Bude, Meadeville, McCall Creek, Brookhaven, 
Bogue Chitto...

The National Weather Service in Jackson has issued a

* Tornado Warning for...
  Lincoln County in south central Mississippi...
  Eastern Franklin County in southwestern Mississippi...

* Until 815 PM CDT

At 710 PM CDT, a confirmed tornado was located near
Bogue Chitto, moving east at 35 mph.
```

**Detection:**
- Confidence: `observed` (contains "confirmed tornado")
- Severity: `emergency` (contains "TORNADO EMERGENCY")

---

### PDS (Particularly Dangerous Situation) + Confirmed

```
...A TORNADO WARNING REMAINS IN EFFECT UNTIL 745 PM CDT FOR NORTH
CENTRAL AMITE COUNTY...

At 711 PM CDT, a confirmed large and extremely dangerous tornado was
located near Meadville, moving east at 40 mph.

This is a PARTICULARLY DANGEROUS SITUATION. TAKE COVER
```

**Detection:**
- Confidence: `observed` (contains "confirmed")
- Severity: `pds` (contains "PARTICULARLY DANGEROUS SITUATION")

---

### Standard Tornado Warning (Radar Indicated)

```
...A TORNADO WARNING REMAINS IN EFFECT UNTIL 730 PM CDT...

At 715 PM CDT, Doppler radar indicated a rotating thunderstorm capable
of producing a tornado. A tornado warning remains in effect.

Locations impacted include...
```

**Detection:**
- Confidence: `radar_indicated` (default, no "OBSERVED" or "CONFIRMED" markers)
- Severity: `standard` (no EMERGENCY or PDS designation)

---

## Severe Thunderstorm Warnings

### Destructive Damage Threat

```
URGENT - WEATHER MESSAGE
Severe Thunderstorm Warning

A DESTRUCTIVE severe thunderstorm capable of producing damaging winds
is moving through the area.

DAMAGING WINDS...DESTRUCTIVE...CAPABLE OF PRODUCING SIGNIFICANT DAMAGE
LARGE HAIL...LOCALLY UP TO 2.75 INCHES...DAMAGE TO VEHICLES IS EXPECTED
```

**Detection:**
- Severity: `EWX` (Extreme Weather Warning - DESTRUCTIVE damage threat)

---

### Considerable Damage Threat (PDS-equivalent)

```
URGENT - WEATHER MESSAGE
Severe Thunderstorm Warning

A SIGNIFICANT severe thunderstorm capable of producing considerable damage
is moving rapidly through the area.

DAMAGING WINDS...CONSIDERABLE...CAPABLE OF PRODUCING CONSIDERABLE DAMAGE
LARGE HAIL...UP TO 2.00 INCHES...DAMAGE TO VEHICLES IS LIKELY
```

**Detection:**
- Severity: `EWX` (CONSIDERABLE damage threat)

---

## Flash Flood Warnings

### Flash Flood Emergency

```
URGENT - WEATHER MESSAGE
Flash Flood Emergency

A FLASH FLOOD EMERGENCY is in effect for portions of the area.

FLASH FLOOD EMERGENCY FOR...significant and destructive flooding expected.

Life-threatening flooding is imminent or already occurring.
```

**Detection:**
- Severity: `emergency` (contains "FLASH FLOOD EMERGENCY")

---

### Standard Flash Flood Warning

```
URGENT - WEATHER MESSAGE
Flash Flood Warning

...FLASH FLOOD WARNING REMAINS IN EFFECT UNTIL 730 PM CDT...

At 715 PM CDT, Doppler radar indicated heavy rainfall moving through
the area. Minor to moderate street flooding is expected.

Locations impacted include...
```

**Detection:**
- Severity: `standard` (standard Flash Flood Warning)

---

## Detection Logic Reference

### Tornado Confidence
- **`observed`**: Text contains "TORNADO...OBSERVED", "CONFIRMED TORNADO", "confirmed large", "confirmed rotating"
- **`radar_indicated`**: Default for tornado warnings without observed designation

### Tornado Severity
- **`emergency`**: Text contains "TORNADO EMERGENCY" OR params contain CATASTROPHIC damage threat
- **`pds`**: Text contains "PARTICULARLY DANGEROUS SITUATION" OR params contain CONSIDERABLE damage threat  
- **`standard`**: Default tornado warning without emergency/PDS designation

### SVR Severity (Severe Thunderstorm)
- **`EWX`** (DESTRUCTIVE): "DESTRUCTIVE" in damage threat
- **`EWX`** (CONSIDERABLE): "CONSIDERABLE" in damage threat
- Standard: No extreme designation

### FFW Severity (Flash Flood)
- **`emergency`**: "FLASH FLOOD EMERGENCY" in text
- **`standard`**: Standard Flash Flood Warning

---

## Common Text Patterns

### Tornado Confirmation Phrases
- "At X PM CDT, a confirmed tornado..."
- "...TORNADO...OBSERVED..."
- "A tornado was sighted..."

### Emergency Escalation Phrases
- "TORNADO EMERGENCY"
- "This is a PARTICULARLY DANGEROUS SITUATION. TAKE COVER"
- "FLASH FLOOD EMERGENCY"
- "DESTRUCTIVE...CAPABLE OF PRODUCING SIGNIFICANT DAMAGE"
- "CONSIDERABLE...CAPABLE OF PRODUCING CONSIDERABLE DAMAGE"

### Damage Threat Parameters (NWS API)
- `tornadoDamageThreat`: "CATASTROPHIC" (emergency), "CONSIDERABLE" (PDS)
- `thunderstormDamageThreat`: "DESTRUCTIVE" (EWX), "CONSIDERABLE" (EWX)
- `flashFloodDamageThreat`: "CATASTROPHIC" (emergency)

---

## How to Add Complete Examples

When severe warnings occur (Tornado Emergency, destructive SVR, etc.):

1. Look in `spc_bot.log` for `[WARN_VTEC_RAW]` entries (tornado warnings only)
2. Extract the complete raw NWS product text (from WFUS/WWUS header through final line)
3. Add to this file under appropriate section with:
   - Full product text in code block
   - Timestamp it occurred
   - Which attributes it demonstrates (confidence/severity combo)
4. Commit with reference to VTEC ID

Complete products capture everything needed for accurate pattern matching.
