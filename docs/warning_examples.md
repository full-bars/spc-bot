# Warning Format Examples

This document contains real examples of NWS warnings processed by SPC-Bot.
Use these as reference for warning text patterns when debugging or adding new detection logic.

**Note:** Prior to 2026-05-07, tornado warning text was truncated to 500 characters in logs.
From 2026-05-07 forward, complete untruncated warning products are logged and can be added to this file.

## Tornado Warnings

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
