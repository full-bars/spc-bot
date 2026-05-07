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

### Standard Flash Flood Warning (Complete Product)

**Source:** Real-time 2026-05-07 06:02 UTC, KFFC (Peachtree City, GA)

```
616 
WGUS52 KFFC 070602
FFWFFC
GAC145-215-071200-
/O.NEW.KFFC.FF.W.0005.260507T0602Z-260507T1200Z/
/00000.0.ER.000000T0000Z.000000T0000Z.000000T0000Z.OO/

BULLETIN - EAS ACTIVATION REQUESTED
Flash Flood Warning
National Weather Service Peachtree City GA
202 AM EDT Thu May 7 2026

The National Weather Service in Peachtree City has issued a

* Flash Flood Warning for...
  Southern Harris County in west central Georgia...
  Northern Muscogee County in west central Georgia...

* Until 800 AM EDT.

* At 202 AM EDT, Doppler radar indicated thunderstorms producing
  heavy rain across the warned area. Between 2 and 3 inches of rain
  have fallen. Additional rainfall amounts of 1 to 2 inches are
  possible in the warned area. Flash flooding is ongoing or expected
  to begin shortly.

  HAZARD...Flash flooding caused by thunderstorms.

  SOURCE...Radar indicated.

  IMPACT...Flash flooding of small creeks and streams, urban areas,
           highways, streets and underpasses as well as other poor
           drainage and low-lying areas.

* Some locations that will experience flash flooding include...
  Waverly Hall, Bibb City, Upatoi, Fortson, Cataula, Flat Rock,
  Mulberry Grove, Midland, Ellerslie, Columbus Metropolitan Airport,
  Kenwood, Laurel Hills, Edgewood, Green Island Hills, Highland Park,
  Mountain Hill, Rose Hill, Goat Rock Lake and Lake Harding.

PRECAUTIONARY/PREPAREDNESS ACTIONS...

Turn around, don't drown when encountering flooded roads. Most flood
deaths occur in vehicles.

Be especially cautious at night when it is harder to recognize the
dangers of flooding.

Flooding is occurring or is imminent. It is important to know where
you are relative to streams, rivers, or creeks which can become
killers in heavy rains. Campers and hikers should avoid streams or
creeks.

&&

LAT...LON 3270 8512 3272 8510 3273 8496 3272 8472
      3265 8470 3258 8470 3253 8471 3249 8485
      3247 8500 3249 8499 3251 8500 3254 8502
      3258 8507 3262 8509 3264 8508 3264 8510
      3266 8509 3267 8509 3268 8511

FLASH FLOOD...RADAR INDICATED

$$
```

**Detection:**
- Severity: `standard` (no EMERGENCY designation)
- VTEC: `KFFC.FF.W.0005` — New warning
- Rainfall: 2-3 inches fallen, 1-2 inches additional expected
- Key markers: "FLASH FLOOD WARNING" without emergency language

---

### Flash Flood Emergency (Complete Product - Issuance)

**Source:** Real-time 2025-11-20 19:29 UTC, KSJT (San Angelo, TX)

```
936 
WGUS54 KSJT 201929
FFWSJT
TXC095-307-327-202230-
/O.NEW.KSJT.FF.W.0116.251120T1929Z-251120T2230Z/
/00000.0.ER.000000T0000Z.000000T0000Z.000000T0000Z.OO/

BULLETIN - EAS ACTIVATION REQUESTED
Flash Flood Warning
National Weather Service San Angelo TX
129 PM CST Thu Nov 20 2025

...FLASH FLOOD EMERGENCY FOR MENARD...

The National Weather Service in San Angelo has issued a

* Flash Flood Warning for...
  Southeastern Concho County in west central Texas...
  Western McCulloch County in west central Texas...
  Central Menard County in west central Texas...

* Until 430 PM CST.

* At 126 PM CST, Doppler radar and automated rain gauges indicated 
  thunderstorms producing heavy rain across the warned area. Between 
  6 and 9 inches of rain have fallen. Additional rainfall amounts of 
  3 to 5 inches are possible in the warned area. Flash flooding is 
  already occurring. Heavy rainfall will continue to occur over the 
  next few hours which will worsen existing flooding.

  This is a FLASH FLOOD EMERGENCY for Menard. This is a PARTICULARLY 
  DANGEROUS SITUATION. SEEK HIGHER GROUND NOW!

  HAZARD...Life threatening flash flooding. Thunderstorms
           producing flash flooding.

  SOURCE...Radar and automated gauges.

  IMPACT...This is a PARTICULARLY DANGEROUS SITUATION. SEEK
           HIGHER GROUND NOW! Life threatening flash flooding of
           low water crossings, small creeks and streams, urban
           areas, highways, streets and underpasses.

* Some locations that will experience flash flooding include...
  Menard and The Intersection Of Us-83 And Highway 29.

This includes the following Low Water Crossings...
Decker St. crossing San Saba River, County Road 128 crossing Reubes 
Creek, County Road 126 crossing Saddle Creek, County Road 124 
crossing Saddle Creek, Ranch Road 2291 crossing Las Moras Creek and 
Callan Lane crossing. Five-Mile Crossing at Farm to Market 2092

PRECAUTIONARY/PREPAREDNESS ACTIONS...

Move to higher ground now! This is an extremely dangerous and 
life-threatening situation. Do not attempt to travel unless you are 
fleeing an area subject to flooding or under an evacuation order.

Turn around, don't drown when encountering flooded roads. Most flood 
deaths occur in vehicles.

Be aware of your surroundings and do not drive on flooded roads.

In hilly terrain there are hundreds of low water crossings which are 
potentially dangerous in heavy rain. Do not attempt to cross flooded 
roads. Find an alternate route.

Remain alert for flooding even in locations not receiving rain. 
Arroyos, streams, and rivers can become raging killer currents in a 
matter of minutes, even from distant rainfall.

&&

LAT...LON 3082 9997 3109 9974 3120 9964 3111 9949
      3101 9960 3077 9986

FLASH FLOOD...RADAR AND GAUGE INDICATED
FLASH FLOOD DAMAGE THREAT...CATASTROPHIC

$$
```

**Detection:**
- Severity: `emergency` (contains "FLASH FLOOD EMERGENCY FOR MENARD" + "PARTICULARLY DANGEROUS SITUATION")
- VTEC: `KSJT.FF.W.0116` — New warning
- Rainfall: 6-9 inches fallen, 3-5 inches additional possible
- Damage threat: CATASTROPHIC
- Key markers: "FLASH FLOOD EMERGENCY", "PARTICULARLY DANGEROUS SITUATION", "Life threatening", "SEEK HIGHER GROUND NOW!"

---

### Flash Flood Emergency (Continuation)

**Source:** Real-time 2025-11-20 20:54 UTC, KSJT (same event, CON update)

```
154 
WGUS74 KSJT 202054
FFSSJT

Flash Flood Statement
National Weather Service San Angelo TX
254 PM CST Thu Nov 20 2025

TXC095-307-327-202230-
/O.CON.KSJT.FF.W.0116.000000T0000Z-251120T2230Z/
/00000.0.ER.000000T0000Z.000000T0000Z.000000T0000Z.OO/
Concho TX-McCulloch TX-Menard TX-
254 PM CST Thu Nov 20 2025

...FLASH FLOOD EMERGENCY FOR MENARD...

...FLASH FLOOD WARNING REMAINS IN EFFECT UNTIL 430 PM CST THIS 
AFTERNOON FOR SOUTHEASTERN CONCHO, WEST CENTRAL MCCULLOCH AND 
CENTRAL MENARD COUNTIES...

At 251 PM CST, Doppler radar and automated rain gauges indicated 
thunderstorms producing heavy rain across the warned area. Between 6 
and 9 inches of rain have fallen. Additional rainfall amounts of 1 
to 2 inches are possible in the warned area. Flash flooding is 
already occurring. In addition, local emergency management reported 
that water was entering homes in the Harris Hollow section of town.

This is a FLASH FLOOD EMERGENCY for Menard. This is a PARTICULARLY 
DANGEROUS SITUATION. SEEK HIGHER GROUND NOW!

HAZARD...Life threatening flash flooding. Thunderstorms producing
         flash flooding.

SOURCE...Radar and automated gauges.

IMPACT...This is a PARTICULARLY DANGEROUS SITUATION. SEEK HIGHER
         GROUND NOW! Life threatening flash flooding of low water
         crossings, small creeks and streams, urban areas,
         highways, streets and underpasses.

Some locations that will experience flash flooding include...
  Menard and The Intersection Of Us-83 And Highway 29.

This includes the following Low Water Crossings...
Decker St. crossing San Saba River, County Road 128 crossing Reubes 
Creek, County Road 126 crossing Saddle Creek, County Road 124 
crossing Saddle Creek, Ranch Road 2291 crossing Las Moras Creek and 
Callan Lane crossing. Five-Mile Crossing at Farm to Market 2092

PRECAUTIONARY/PREPAREDNESS ACTIONS...

Move to higher ground now! This is an extremely dangerous and 
life-threatening situation. Do not attempt to travel unless you are 
fleeing an area subject to flooding or under an evacuation order.

Turn around, don't drown when encountering flooded roads. Most flood 
deaths occur in vehicles.

Be aware of your surroundings and do not drive on flooded roads.

In hilly terrain there are hundreds of low water crossings which are 
potentially dangerous in heavy rain. Do not attempt to cross flooded 
roads. Find an alternate route.

Remain alert for flooding even in locations not receiving rain. 
Arroyos, streams, and rivers can become raging killer currents in a 
matter of minutes, even from distant rainfall.

&&

LAT...LON 3082 9997 3109 9974 3120 9964 3111 9949
      3101 9960 3077 9986

FLASH FLOOD...RADAR AND GAUGE INDICATED
FLASH FLOOD DAMAGE THREAT...CATASTROPHIC

$$
```

**Detection:**
- Severity: `emergency` (maintains emergency designation on CON update)
- VTEC: `KSJT.FF.W.0116` — Continuation (CON) update
- Product type: Flash Flood Statement (FFS) instead of primary FFW
- Escalation: Now mentions homes being flooded (worsening situation)

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
