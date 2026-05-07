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

### Tornado Emergency + Observed + PDS (Complete Product)

**Source:** Real-time 2026-05-06 21:01 UTC, KJAN

```
288 
WWUS54 KJAN 070101
SVSJAN

Severe Weather Statement
National Weather Service Jackson MS
801 PM CDT Wed May 6 2026

MSC085-070115-
/O.CON.KJAN.TO.W.0035.000000T0000Z-260507T0115Z/
Lincoln MS-
801 PM CDT Wed May 6 2026

...TORNADO EMERGENCY FOR Brookhaven, Bogue Chitto...

...A TORNADO WARNING REMAINS IN EFFECT UNTIL 815 PM CDT FOR EASTERN
LINCOLN COUNTY...

At 801 PM CDT, a confirmed large and destructive tornado was located
over East Lincoln, or 10 miles southeast of Brookhaven, moving east
at 50 mph.

TORNADO EMERGENCY for Brookhaven, Bogue Chitto. This is a 
PARTICULARLY DANGEROUS SITUATION. TAKE COVER NOW!

HAZARD...Deadly tornado.

SOURCE...Radar confirmed tornado.

IMPACT...You are in a life-threatening situation. Flying debris may 
         be deadly to those caught without shelter. Mobile homes 
         will be destroyed. Considerable damage to homes, 
         businesses, and vehicles is likely and complete destruction 
         is possible.

This tornadic thunderstorm will remain over mainly rural areas of
eastern Lincoln County.

PRECAUTIONARY/PREPAREDNESS ACTIONS...

To repeat, a large, extremely dangerous, and potentially deadly
tornado is on the ground. To protect your life, TAKE COVER NOW! Move
to an interior room on the lowest floor of a sturdy building. Avoid
windows. If in a mobile home, a vehicle or outdoors, move to the
closest substantial shelter and protect yourself from flying debris.

LAT...LON 3157 9050 3168 9034 3154 9025 3141 9024
      3141 9049
TIME...MOT...LOC 0101Z 260DEG 43KT 3150 9030 

TORNADO...OBSERVED
TORNADO DAMAGE THREAT...CATASTROPHIC
MAX HAIL SIZE...1.75 IN
```

**Detection:**
- Confidence: `observed` (contains "TORNADO...OBSERVED" + "confirmed large and destructive tornado")
- Severity: `emergency` (contains "TORNADO EMERGENCY" AND "TORNADO DAMAGE THREAT...CATASTROPHIC")
- **Also contains PDS:** "PARTICULARLY DANGEROUS SITUATION" (secondary designation alongside EMERGENCY)
- VTEC: `KJAN.TO.W.0035` — Continuation (CON) update of existing warning
- Product type: Severe Weather Statement (SVS, not the primary TOR product)

---

### PDS (Particularly Dangerous Situation) + Observed (Complete Product)

**Source:** Real-time 2026-05-06 20:51 UTC, KJAN

```
327 
WWUS54 KJAN 070051
SVSJAN

Severe Weather Statement
National Weather Service Jackson MS
751 PM CDT Wed May 6 2026

MSC085-070115-
/O.CON.KJAN.TO.W.0035.000000T0000Z-260507T0115Z/
Lincoln MS-
751 PM CDT Wed May 6 2026

...A TORNADO WARNING REMAINS IN EFFECT UNTIL 815 PM CDT FOR EASTERN
LINCOLN COUNTY...

At 751 PM CDT, a confirmed large and extremely dangerous tornado was
located near Bogue Chitto, or 7 miles south of Brookhaven, moving
east at 50 mph.

This is a PARTICULARLY DANGEROUS SITUATION. TAKE COVER NOW!

HAZARD...Damaging tornado.

SOURCE...Radar confirmed tornado.

IMPACT...You are in a life-threatening situation. Flying debris may 
         be deadly to those caught without shelter. Mobile homes 
         will be destroyed. Considerable damage to homes, 
         businesses, and vehicles is likely and complete destruction 
         is possible.

The tornado will be near...
  Brookhaven, Enterprise, and East Lincoln around 755 PM CDT.

PRECAUTIONARY/PREPAREDNESS ACTIONS...

To repeat, a large, extremely dangerous and potentially deadly
tornado is on the ground. To protect your life, TAKE COVER NOW! Move
to a basement or an interior room on the lowest floor of a sturdy
building. Avoid windows. If you are outdoors, in a mobile home, or in
a vehicle, move to the closest substantial shelter and protect
yourself from flying debris.

LAT...LON 3157 9057 3170 9035 3154 9025 3141 9024
      3141 9055
TIME...MOT...LOC 0051Z 260DEG 43KT 3148 9045 

TORNADO...OBSERVED
TORNADO DAMAGE THREAT...CONSIDERABLE
MAX HAIL SIZE...1.75 IN
```

**Detection:**
- Confidence: `observed` (contains "TORNADO...OBSERVED" + "confirmed large and extremely dangerous tornado")
- Severity: `pds` (contains "PARTICULARLY DANGEROUS SITUATION" and "TORNADO DAMAGE THREAT...CONSIDERABLE")
- VTEC: `KJAN.TO.W.0035` — Continuation (CON) update
- Product type: Severe Weather Statement (SVS)
- **Key difference from Emergency:** CONSIDERABLE damage threat instead of CATASTROPHIC

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

### Destructive Damage Threat (Complete Product)

**Source:** Real-time 2026-05-06 21:11 UTC, KBMX

```
648 
WUUS54 KBMX 070011
SVRBMX
ALC063-065-091-105-119-070115-
/O.NEW.KBMX.SV.W.0064.260507T0011Z-260507T0115Z/

BULLETIN - EAS ACTIVATION REQUESTED
Severe Thunderstorm Warning
National Weather Service Birmingham AL
711 PM CDT Wed May 6 2026

The National Weather Service in Birmingham has issued a

* Severe Thunderstorm Warning for...
  Northeastern Marengo County in southwestern Alabama...
  Perry County in central Alabama...
  Southeastern Greene County in west central Alabama...
  Southern Hale County in west central Alabama...
  Southeastern Sumter County in west central Alabama...

* Until 815 PM CDT.

* At 710 PM CDT, a severe thunderstorm was located near Forkland, or
  9 miles northwest of Demopolis, moving east at 45 mph.

  THIS IS A DESTRUCTIVE STORM IN GREENE, HALE, AND NORTHERN MARENGO 
COUNTIES.

  HAZARD...Three inch hail and 70 mph wind gusts.

  SOURCE...Radar indicated.

  IMPACT...People and animals outdoors will be severely injured. 
           Expect shattered windows, extensive damage to roofs, 
           siding, and vehicles.

* Locations impacted include...
  Demopolis, Marion, Greensboro, Uniontown, Forkland, Boligee,
  Newbern, Faunsdale, Vaiden, Walden Quarters, Thornhill, Arcola, Dug
  Hill, Tishabee, Greensboro Municipal Airport, Duffys Bend,
  Sawyerville, Coatopa, Radford, and Old Spring Hill.

PRECAUTIONARY/PREPAREDNESS ACTIONS...

Remain alert for a possible tornado! Tornadoes can develop quickly
from severe thunderstorms. If you spot a tornado go at once into the
basement or small central room in a sturdy structure.

This storm is producing destructive winds and large damaging hail.
SEEK SHELTER NOW inside a sturdy structure and stay away from
windows!

Large hail, damaging wind, and continuous cloud to ground lightning
are occurring with this storm. Move indoors immediately. Lightning is
one of nature's leading killers. Remember, if you can hear thunder,
you are close enough to be struck by lightning.

Torrential rainfall is occurring with this storm, and may lead to
flash flooding. Do not drive your vehicle through flooded roadways.

A Tornado Watch remains in effect until 1100 PM CDT for south
central, central, southwestern and west central Alabama.

LAT...LON 3277 8803 3274 8702 3258 8706 3257 8708
      3249 8711 3248 8742 3231 8742 3250 8814
TIME...MOT...LOC 0010Z 275DEG 39KT 3261 8792 

TORNADO...POSSIBLE
THUNDERSTORM DAMAGE THREAT...DESTRUCTIVE
HAIL THREAT...RADAR INDICATED
MAX HAIL SIZE...3.00 IN
WIND THREAT...RADAR INDICATED
MAX WIND GUST...70 MPH
```

**Detection:**
- Severity: `EWX` (Extreme Weather Warning - DESTRUCTIVE damage threat)
- VTEC: `KBMX.SV.W.0064` — New warning
- Key markers: "THIS IS A DESTRUCTIVE STORM" + "THUNDERSTORM DAMAGE THREAT...DESTRUCTIVE"

---

### Considerable Damage Threat (Complete Product - Continuation)

**Source:** Real-time 2026-05-06 21:35 UTC, KBMX (CON update to same event)

```
662 
WWUS54 KBMX 070035
SVSBMX

Severe Weather Statement
National Weather Service Birmingham AL
735 PM CDT Wed May 6 2026

ALC065-091-105-070115-
/O.CON.KBMX.SV.W.0064.000000T0000Z-260507T0115Z/
Marengo AL-Perry AL-Hale AL-
735 PM CDT Wed May 6 2026

...A SEVERE THUNDERSTORM WARNING REMAINS IN EFFECT UNTIL 815 PM CDT
FOR NORTHEASTERN MARENGO...PERRY AND SOUTHEASTERN HALE COUNTIES...

At 735 PM CDT, a severe thunderstorm was located near Newbern, or 8
miles south of Greensboro, moving east at 45 mph.

HAZARD...Tennis ball size hail and 60 mph wind gusts.

SOURCE...Radar indicated.

IMPACT...People and animals outdoors will be injured. Expect hail 
         damage to roofs, siding, windows, and vehicles. Expect wind 
         damage to roofs, siding, and trees.

Locations impacted include...
Marion, Greensboro, Uniontown, Newbern, Faunsdale, Greensboro
Municipal Airport, Vaiden, Perry County Correctional Center, Judson
College, Radford, Old Spring Hill, Gallion, Folsom, Suttle, Sprott,
Vaiden Field Airport, Laneville, and Cedarville.

PRECAUTIONARY/PREPAREDNESS ACTIONS...

A Tornado Watch remains in effect until 1100 PM CDT for south
central, central, southwestern and west central Alabama.

Remain alert for a possible tornado! Tornadoes can develop quickly
from severe thunderstorms. If you spot a tornado go at once into the
basement or small central room in a sturdy structure.

This storm is producing destructive winds and large damaging hail.
SEEK SHELTER NOW inside a sturdy structure and stay away from
windows.

Torrential rainfall is occurring with this storm, and may lead to
flash flooding. Do not drive your vehicle through flooded roadways.

LAT...LON 3273 8766 3272 8705 3249 8711 3248 8742
      3231 8742 3242 8781 3259 8775 3259 8774
      3260 8775 3262 8774
TIME...MOT...LOC 0035Z 275DEG 39KT 3258 8758 

TORNADO...POSSIBLE
THUNDERSTORM DAMAGE THREAT...CONSIDERABLE
HAIL THREAT...RADAR INDICATED
MAX HAIL SIZE...2.50 IN
WIND THREAT...RADAR INDICATED
MAX WIND GUST...60 MPH
```

**Detection:**
- Severity: `EWX` (CONSIDERABLE damage threat)
- VTEC: `KBMX.SV.W.0064` — Continuation (CON) update
- Product type: Severe Weather Statement (SVS) instead of primary SVR
- Key markers: "THUNDERSTORM DAMAGE THREAT...CONSIDERABLE" with 2.5" hail and 60 mph winds

---

### Standard Severe Thunderstorm Warning (Considerable, no extreme designation)

**Source:** Real-time 2026-05-06 21:49 UTC, KBMX

```
955 
WUUS54 KBMX 070049
SVRBMX
ALC047-070115-
/O.NEW.KBMX.SV.W.0065.260507T0049Z-260507T0115Z/

BULLETIN - EAS ACTIVATION REQUESTED
Severe Thunderstorm Warning
National Weather Service Birmingham AL
749 PM CDT Wed May 6 2026

The National Weather Service in Birmingham has issued a

* Severe Thunderstorm Warning for...
  West central Dallas County in south central Alabama...

* Until 815 PM CDT.

* At 748 PM CDT, a severe thunderstorm was located near Vaiden, or
  near Uniontown, moving east at 45 mph.

  HAZARD...Tennis ball size hail and 60 mph wind gusts.

  SOURCE...Radar indicated.

  IMPACT...People and animals outdoors will be injured. Expect hail 
           damage to roofs, siding, windows, and vehicles. Expect 
           wind damage to roofs, siding, and trees.

* Locations impacted include...
  Orrville, Marion Junction, Bogue Chitto, Whites Bluff, and Hazen.

PRECAUTIONARY/PREPAREDNESS ACTIONS...

Remain alert for a possible tornado! Tornadoes can develop quickly
from severe thunderstorms. If you spot a tornado go at once into the
basement or small central room in a sturdy structure.

For your protection move to an interior room on the lowest floor of a
building.

A Tornado Watch remains in effect until 1100 PM CDT for south central
and central Alabama.

LAT...LON 3231 8742 3248 8742 3249 8711 3225 8718
TIME...MOT...LOC 0048Z 274DEG 39KT 3253 8746 

TORNADO...POSSIBLE
THUNDERSTORM DAMAGE THREAT...CONSIDERABLE
HAIL THREAT...RADAR INDICATED
MAX HAIL SIZE...2.50 IN
WIND THREAT...RADAR INDICATED
MAX WIND GUST...60 MPH
```

**Detection:**
- Severity: `standard` (no EWX designation despite "CONSIDERABLE" damage threat)
- VTEC: `KBMX.SV.W.0065` — New warning
- Key difference: No special designation markers like "DESTRUCTIVE STORM"

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
