#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI-assisted concrete mix design  --  IS 10262:2019 (Annex A format) + IS 456:2000

Usage
-----
  python concrete_mix_design.py                 guided session (describe the job, then menus)
  python concrete_mix_design.py --spec x.json   re-run a saved spec (no questions)
  python test_concrete_mix_design.py            run the tests

Workflow: describe the job (or skip) -> the tool interprets it (whole-word keywords,
negation, uncertainty) -> exposure class from IS 456 Table 3 -> asks ONLY what is
missing, via numbered menus -> design -> Annex A style report.

IMPORTANT
---------
  * NO IS PDF HAS BEEN CHECKED. Table values are keyed in from memory of
    IS 10262:2019 / IS 456:2000. See UNVERIFIED_ITEMS (printed in every report).
  * The w/c vs strength curves (Fig. 1 of IS 10262) are DIGITISED APPROXIMATIONS.
    Replace WC_CURVES with lab-calibrated data when you have it.
  * The result is a TRIAL-1 mix. Trial mixes + cube tests are mandatory.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date

# ============================================================================
# 1.  CODE DATA TABLES
# ============================================================================

# IS 10262:2019 Table 2 -> (assumed std. deviation s, factor X) by grade
def std_dev(fck):
    """Return (assumed_sd, X) per IS 10262:2019 Tables 1 and 2.
    X is the margin factor; assumed SD is for good control.
    """
    if fck <= 15:
        return 3.5, 5.0
    if fck <= 25:
        return 4.0, 5.5
    if fck <= 60:          # M30–M60 inclusive (Table 1 iii / Table 2 iii)
        return 5.0, 6.5
    return 6.0, 8.0        # M65 and above


EXPOSURES = ["Mild", "Moderate", "Severe", "Very severe", "Extreme"]

# IS 456:2000 Table 5 (20 mm aggregate):
# exposure -> (RCC min cement, RCC max w/c, RCC min grade,
#              PCC min cement, PCC max w/c, PCC min grade)
IS456_T5 = {
    "Mild":        (300, 0.55, 20, 220, 0.60, 0),
    "Moderate":    (300, 0.50, 25, 240, 0.60, 15),
    "Severe":      (320, 0.45, 30, 250, 0.50, 20),
    "Very severe": (340, 0.45, 35, 260, 0.45, 20),
    "Extreme":     (360, 0.40, 40, 280, 0.40, 25),
}
# IS 456 Table 6: min-cement adjustment for aggregate size (kg/m3)
SIZE_ADJ = {10: +40, 20: 0, 40: -30}

# IS 10262:2019 Table 4: water per m3 for 50 mm slump, angular aggregate (kg)
WATER_T4 = {10: 208, 20: 186, 40: 165}
# Reduction in water for less angular aggregate (Table 4 notes), kg
# IS 10262:2019 Cl. 5.3 notes: approx. reductions vs angular for same workability
SHAPE_REDUCTION = {"crushed": 0, "subangular": 10, "gravel+crushed": 15, "rounded": 20}

# IS 10262:2019 Table 3: entrapped air (%)
AIR_T3 = {10: 1.5, 20: 1.0, 40: 0.8}

# IS 10262:2019 Table 5: coarse-aggregate volume / total aggregate volume at w/c = 0.5
# IS 10262:2019 Table 5 (exact values from the standard)
CA_VOL_T5 = {
    10: {"IV": 0.54, "III": 0.52, "II": 0.50, "I": 0.48},
    20: {"IV": 0.66, "III": 0.64, "II": 0.62, "I": 0.60},
    40: {"IV": 0.73, "III": 0.72, "II": 0.71, "I": 0.69},
}

CEMENT_CLASS = {"OPC 33": 33, "OPC 43": 43, "OPC 53": 53, "PPC": 43, "PSC": 43}

# Approximate digitisation of IS 10262 Fig. 1 : (w/c, 28-day concrete strength MPa)
# by 28-day cement strength class.  REPLACE with calibrated data if available.
WC_CURVES = {
    33: [(0.30, 48), (0.35, 44), (0.40, 40), (0.45, 36), (0.50, 32), (0.55, 29),
         (0.60, 26), (0.65, 23.5), (0.70, 21)],
    43: [(0.30, 60), (0.35, 54), (0.40, 48), (0.45, 43), (0.50, 38.5), (0.55, 34.5),
         (0.60, 31), (0.65, 28), (0.70, 25.5)],
    53: [(0.30, 68), (0.35, 61), (0.40, 55), (0.45, 49.5), (0.50, 45), (0.55, 41),
         (0.60, 37), (0.65, 34), (0.70, 31)],
}


def wc_from_strength(target, cls):
    """Invert the (log-linear) strength-vs-w/c curve; extrapolates beyond the ends."""
    pts = [(w, math.log(f)) for w, f in WC_CURVES[cls]]
    y = math.log(target)
    if y >= pts[0][1]:
        (w1, y1), (w2, y2) = pts[0], pts[1]
    elif y <= pts[-1][1]:
        (w1, y1), (w2, y2) = pts[-2], pts[-1]
    else:
        for i in range(len(pts) - 1):
            if pts[i][1] >= y >= pts[i + 1][1]:
                (w1, y1), (w2, y2) = pts[i], pts[i + 1]
                break
    return w1 + (y - y1) * (w2 - w1) / (y2 - y1)


# ============================================================================
# 2.  INPUT SPEC
# ============================================================================

@dataclass
class Spec:
    # --- A-1 stipulations ---
    grade: int = 0
    cement_type: str = "OPC 53"
    cement_class: int = 53
    max_size: int = 20
    exposure: str = "Moderate"
    member: str = "RCC"                 # RCC / PCC
    slump: int = 100
    placing: str = "Normal"             # Normal / Pumped / Tremie
    control: str = "Good"               # Good / Fair
    agg_shape: str = "crushed"
    min_cement_user: float | None = None
    max_wc_user: float | None = None
    max_cement: float = 450.0
    admix_name: str = "PCE-based superplasticizer"
    admix_dose: float = 0.0             # % by mass of cementitious
    admix_red: float = 0.0              # % water reduction achieved
    fa_pct: float = 0.0                 # fly ash, % of cementitious
    ggbs_pct: float = 0.0
    sf_pct: float = 0.0
    scm_boost: bool = True              # +10 % cementitious when fly ash/GGBS used
    shape_red_user: float | None = None # user water reduction for 'other' aggregate shape, kg/m3
    # --- A-2 material data ---
    sg_cement: float = 3.15
    sg_fly: float = 2.20
    sg_ggbs: float = 2.90
    sg_sf: float = 2.20
    sg_admix: float = 1.08
    sg_fa: float = 2.65                 # fine aggregate (SSD)
    sg_ca: float = 2.70                 # coarse aggregate (SSD)
    abs_fa: float = 1.0
    abs_ca: float = 0.5
    mc_fa: float | None = None          # total moisture %, None = SSD
    mc_ca: float | None = None
    fa_zone: str = "II"
    # --- overrides ---
    sd_user: float | None = None
    wcm_user: float | None = None
    deduct_air: bool = True
    # --- bookkeeping ---
    assumptions: list = field(default_factory=list)
    assumed_materials: list = field(default_factory=list)
    qc_method: str = "Assumed from Table 2"
    qc_reason: str = ""
    exposure_basis: list = field(default_factory=list)
    chem_notes: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    admix_decision: str = ""
    admix_types: list = field(default_factory=list)


def normalise(s: Spec):
    def pick(val, options, default):
        for o in options:
            if str(val).strip().lower() == o.lower():
                return o
        return default
    s.exposure = pick(s.exposure, EXPOSURES, "Moderate")
    s.member = pick(s.member, ["RCC", "PCC"], "RCC")
    s.placing = pick(s.placing, ["Normal", "Pumped", "Tremie"], "Normal")
    s.control = pick(s.control, ["Good", "Fair"], "Good")
    s.agg_shape = pick(s.agg_shape, list(SHAPE_REDUCTION), "crushed")
    s.fa_zone = pick(s.fa_zone, ["I", "II", "III", "IV"], "II")
    if s.max_size not in WATER_T4:
        s.max_size = min(WATER_T4, key=lambda k: abs(k - s.max_size))
    if s.cement_class not in WC_CURVES:
        s.cement_class = min(WC_CURVES, key=lambda k: abs(k - s.cement_class))


def apply_values(s: Spec, values: dict):
    for k, v in values.items():
        if k not in Spec.__dataclass_fields__:
            continue
        cur = getattr(s, k)
        if isinstance(cur, list):
            if isinstance(v, list):
                setattr(s, k, list(v))
            continue
        try:
            if isinstance(cur, bool):
                v = bool(v)
            elif isinstance(cur, int):
                v = int(round(float(v)))
            elif isinstance(cur, float):
                v = float(v)
            elif cur is None:
                v = None if v is None else float(v)
            else:
                v = str(v)
        except Exception:
            continue
        setattr(s, k, v)


# ============================================================================
# 3.  JOB-DESCRIPTION ANALYSIS AND DECISION SUPPORT
#     (replaces the earlier one-keyword rule engine; no third-party libraries)
# ============================================================================

VALID_GRADES = (10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80)

# ---- keyword dictionaries: modular, one entry per concept ------------------
# key -> list of phrases. Matching is on whole words/phrases (plural 's' allowed),
# after lower-casing and turning '-' and '/' into spaces.
KEYWORDS = {
    # --- exposure conditions ---
    "tidal": ["tidal zone", "tidal", "splash zone", "wave zone", "intertidal", "inter tidal"],
    "sea_spray": ["sea spray", "sea water spray", "seawater spray", "salt spray", "spray zone",
                  "sea water splash", "seawater splash", "salt laden spray"],
    "immersed_sea": ["immersed in sea water", "immersed in seawater", "submerged in sea water",
                     "submerged in seawater", "completely immersed in sea", "fully immersed in sea"],
    "seawater": ["seawater", "sea water", "salt water", "saltwater", "marine water", "ocean water"],
    "marine": ["marine", "offshore", "jetty", "harbour", "harbor", "port", "wharf", "quay",
               "seaside", "sea side", "sea wall", "seawall", "breakwater", "ocean", "dolphin",
               "near the sea", "by the sea", "on the sea"],
    "coastal": ["coastal", "coast", "seashore", "shoreline", "sea shore", "beach", "coastline",
                "near the coast", "seaboard"],
    "chloride": ["chloride", "chlorides", "saline", "salinity", "salt laden", "salty", "brackish",
                 "de icing salt", "deicing salt", "salt air", "saturated salt air"],
    "sulphate": ["sulphate", "sulfate", "sulphate bearing", "sulfate bearing", "gypsum", "gypsiferous"],
    "aggressive_gw": ["aggressive groundwater", "aggressive ground water", "aggressive soil",
                      "aggressive sub soil", "aggressive subsoil", "acidic soil", "acidic groundwater",
                      "acid groundwater", "contaminated soil", "contaminated groundwater",
                      "aggressive water"],
    "chemical_direct": ["chemical", "industrial chemical", "chemical plant", "acid", "alkali",
                        "chemical storage", "chemical tank", "fertilizer plant", "fertiliser plant",
                        "caustic", "aggressive chemical"],
    "sewage": ["sewage", "wastewater", "waste water", "sewer", "effluent", "sewage treatment",
               "wwtp", "stp", "etp", "sludge", "digester", "septic"],
    "heavy_rain": ["heavy rain", "heavy rainfall", "severe rain", "intense rain", "high rainfall",
                   "driving rain", "heavy monsoon", "severe rainfall", "torrential"],
    "rain": ["rain", "rainfall", "monsoon", "exposed to weather", "exposed to the weather",
             "open to sky", "open to the sky", "weather exposed", "weathering", "outdoor", "outdoors",
             "exposed to rain", "external exposure", "exposed face", "exposed faces"],
    "sheltered": ["sheltered", "protected from weather", "protected against weather", "interior",
                  "indoor", "indoors", "inside building", "inside the building", "internal",
                  "enclosed", "protected from the weather", "within the building"],
    "condensation": ["condensation", "humid", "humidity", "damp", "moist environment"],
    "wet_dry": ["alternate wetting and drying", "alternating wetting and drying", "wetting and drying",
                "alternate wetting", "alternating wet and dry", "wet and dry", "wet dry cycle",
                "wet dry cycles", "wetting drying", "cyclic wetting"],
    "submerged_fresh": ["continuously submerged", "continuously under water", "permanently submerged",
                        "permanently under water", "immersed in water", "water retaining",
                        "water retaining structure", "water tank", "reservoir", "swimming pool",
                        "sump", "water storage", "storage tank", "overhead tank", "clear water"],
    "underground": ["underground", "buried", "below ground", "below ground level", "basement",
                    "foundation", "footing", "pile", "piles", "soil contact", "in contact with soil",
                    "in contact with ground", "groundwater", "ground water", "sub soil", "subsoil",
                    "below grade", "below the ground", "retained earth", "tunnel", "culvert"],
    "freezing": ["freezing", "freeze thaw", "frost", "snow", "sub zero", "icing", "freeze",
                 "cold region", "cold regions", "cold climate", "freezing conditions"],
    "polluted_air": ["polluted atmosphere", "industrial atmosphere", "corrosive fumes", "acid fumes",
                     "industrial environment", "corrosive atmosphere", "fumes", "industrial fumes",
                     "polluted air", "industrial pollution"],
    # --- structure / application ---
    "building": ["building", "multistorey", "multistory", "multi storey", "multi story", "high rise",
                 "highrise", "skyscraper", "apartment", "residential", "office", "commercial building",
                 "tower"],
    "high_rise": ["high rise", "highrise", "skyscraper", "tall building", "multistorey", "multistory",
                  "multi storey", "multi story", "tower"],
    "member": ["beam", "slab", "column", "footing", "retaining wall", "wall", "shear wall", "girder",
               "lintel", "staircase", "foundation", "pile cap", "raft"],
    "bridge": ["bridge", "flyover", "fly over", "viaduct", "pier", "abutment", "overpass", "girder"],
    "dam_tank": ["dam", "water tank", "reservoir", "water retaining", "water retaining structure",
                 "swimming pool", "canal", "spillway"],
    "pavement": ["pavement", "industrial floor", "floor slab", "road", "runway", "hardstand"],
    "precast": ["precast", "pre cast", "prefabricated", "pre fabricated"],
    "prestressed": ["prestressed", "pre stressed", "post tensioned", "post tension", "pretensioned",
                    "pre tensioned", "prestress", "psc girder"],
    "rcc": ["rcc", "reinforced concrete", "reinforced", "reinforcement", "rebar"],
    "pcc": ["pcc", "plain concrete", "plain cement concrete", "blinding", "lean concrete",
            "levelling course", "leveling course"],
    # --- performance / construction ---
    "hsc": ["high strength", "high grade", "hsc", "high performance", "very high strength",
            "ultra high strength", "high strength concrete"],
    "normal_strength": ["normal strength", "ordinary concrete", "low strength", "nominal mix",
                        "standard concrete"],
    "early_strength": ["early strength", "rapid strength gain", "rapid strength", "high early strength",
                       "fast construction", "early formwork removal", "early stripping",
                       "rapid construction", "early age strength", "fast track", "quick setting",
                       "early de shuttering", "early deshuttering"],
    "low_perm": ["low permeability", "watertight", "water tight", "impermeable", "impervious",
                 "water tightness", "high durability", "long service life", "durable",
                 "durability", "leak proof", "leakproof"],
    "pumpable": ["pumpable", "pumping", "pumped", "pump", "long distance pumping",
                 "high rise pumping", "concrete pump"],
    "congested": ["congested", "heavily reinforced", "dense reinforcement", "narrow section",
                  "narrow sections", "thin section", "thin sections", "difficult placement",
                  "tight reinforcement", "closely spaced reinforcement", "congestion"],
    "mass": ["mass concrete", "mass pour", "mass pours", "thick section", "thick sections",
             "large pour", "large pours", "large foundation", "raft foundation", "low heat",
             "heat of hydration", "controlled heat", "heat generation", "thermal cracking"],
    "shrinkage": ["shrinkage control", "low shrinkage", "shrinkage cracking", "crack control"],
    "scc": ["self compacting", "self consolidating", "scc", "self levelling", "self leveling"],
    "lightweight": ["lightweight concrete", "light weight concrete", "lightweight aggregate"],
    "heavy": ["heavy concrete", "heavyweight", "heavy weight concrete", "radiation shielding"],
    "underwater": ["underwater concrete", "placed underwater", "underwater placement", "under water placement",
                   "underwater", "under water", "tremie", "tremie placement", "diaphragm wall"],
    "high_workability": ["high workability", "flowable", "slump retention", "long transport",
                         "long transportation", "transit time", "long haul", "long distance",
                         "long transit", "transportation delay", "transport delay", "traffic delay",
                         "workability retention"],
    "hot_weather": ["hot weather", "hot climate", "high temperature", "hot environment",
                    "summer concreting", "hot region", "high ambient temperature", "desert"],
    "cold_weather": ["cold weather", "cold weather concreting", "winter concreting", "low temperature"],
    # --- materials / sustainability ---
    "opc43": ["opc 43", "43 grade", "opc43", "43 grade opc"],
    "opc53": ["opc 53", "53 grade", "opc53", "53 grade opc"],
    "ppc": ["ppc", "portland pozzolana", "portland pozzolana cement", "pozzolana cement"],
    "psc": ["psc", "portland slag", "portland slag cement", "slag cement"],
    "blended_cement": ["blended cement"],
    "fly_ash": ["fly ash", "flyash", "pfa", "pulverised fuel ash", "pulverized fuel ash"],
    "ggbs": ["ggbs", "ggbfs", "slag", "ground granulated", "granulated blast furnace slag",
             "ground granulated blast furnace slag"],
    "silica_fume": ["silica fume", "microsilica", "micro silica", "condensed silica fume"],
    "metakaolin": ["metakaolin", "meta kaolin"],
    "scm_generic": ["supplementary cementitious", "scm", "mineral admixture", "mineral additive",
                    "pozzolan", "pozzolanic material", "cementitious materials",
                    "supplementary cementing"],
    "recycled_agg": ["recycled aggregate", "recycled concrete aggregate", "rca", "recycled aggregates"],
    "m_sand": ["manufactured sand", "m sand", "msand", "crushed sand", "artificial sand",
               "stone dust", "quarry dust"],
    "sustainable": ["low carbon", "sustainable", "reduced cement", "green concrete", "economical",
                    "economic", "cost effective", "low cost", "reduce cost", "reduced carbon",
                    "eco friendly", "less cement"],
    "admixture": ["admixture", "superplasticizer", "superplasticiser", "plasticizer", "plasticiser",
                  "water reducer", "water reducing", "hrwr", "pce", "retarder", "retarding",
                  "accelerator", "air entraining", "viscosity modifier"],
    # --- production / quality control ---
    "rmc": ["ready mix", "ready mixed", "rmc", "batching plant", "ready mixed concrete"],
    "site_batch": ["site batching", "site mixed", "volume batching", "hand mixed", "manual batching",
                   "batching by volume", "site mixing"],
    "poor_qc": ["poor quality control", "poor control", "variable quality", "inconsistent",
                "limited supervision", "unskilled", "poor supervision", "weak quality control",
                "variable quality control"],
    "good_qc": ["good quality control", "controlled batching", "laboratory controlled",
                "well controlled", "strict quality control", "good control", "tight quality control"],
    "moisture_var": ["moisture variation", "variable moisture", "inconsistent materials",
                     "variable materials", "aggregate moisture variation"],
    "high_var": ["high variability", "variable strength", "scatter in strength", "strength variation"],
    "limited_curing": ["limited curing", "poor curing", "curing difficult", "water shortage",
                       "limited water for curing", "limited curing facilities"],
}

# After-match filters to avoid false hits (text following the match, within the clause)
_POSTFILTER = {
    "chemical_direct": re.compile(r"^\s*(admixture|additive|plasticizer|plasticiser|reducing|reducer)"),
    "ggbs": re.compile(r"^\s*cement"),
    "building": re.compile(r"^\s*(material|code|regulation)"),
    "rain": re.compile(r"^\s*(water\s*harvest)"),
}

_CONFIRMABLE = ("tidal", "sea_spray", "immersed_sea", "seawater", "marine", "coastal", "chloride",
                "sulphate", "aggressive_gw", "chemical_direct", "sewage", "heavy_rain", "wet_dry",
                "freezing", "polluted_air")

_NEG_RE = re.compile(r"\b(not|no|never|without|non|nor|neither|unlikely|avoid|excluding|except|"
                     r"free of|free from|away from|nowhere)\b")
_UNC_RE = re.compile(r"\b(may|might|possibly|probably|maybe|could|perhaps|potentially|not sure|"
                     r"unsure|uncertain|expected to|likely|if)\b")
_SPLIT_RE = re.compile(r"\.(?=\s|$)|[;:!?\n,]|\bbut\b|\bhowever\b|\balthough\b|\bwhile\b|\bwhereas\b")

_RX = {}
for _k, _ph in KEYWORDS.items():
    _alts = "|".join(r"\s+".join(map(re.escape, p.split()))
                     for p in sorted(set(_ph), key=len, reverse=True))
    _RX[_k] = re.compile(rf"(?<![a-z0-9])(?:{_alts})(?:s|es)?(?![a-z0-9])")


def _norm(text: str) -> str:
    t = text.lower().replace("\u2019", "'")
    t = re.sub(r"(\w+)n't\b", r"\1 not", t)
    t = re.sub(r"[-_/\u2013]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class Hit:
    key: str
    term: str
    clause: str
    negated: bool
    uncertain: bool


def detect(text: str) -> dict:
    """Whole-word keyword matching with clause-level negation and uncertainty."""
    hits: dict = {}
    for clause in (c.strip() for c in _SPLIT_RE.split(_norm(text))):
        if not clause:
            continue
        unc = bool(_UNC_RE.search(clause))
        for key, rx in _RX.items():
            for m in rx.finditer(clause):
                pf = _POSTFILTER.get(key)
                if pf and pf.search(clause[m.end():]):
                    continue
                before = " ".join(clause[:m.start()].split()[-6:])
                hits.setdefault(key, []).append(
                    Hit(key, m.group(0), clause, bool(_NEG_RE.search(before)), unc))
    return hits


_ROLE_RESTRICT = re.compile(r"\b(avoid|prohibit\w*|not permitted|not allowed|not to be used|"
                            r"do not use|dont use|no use of|ban\w*)\b")
_ROLE_AVAIL = re.compile(r"\b(available|availability|can be used|could be used|may be used|"
                         r"accessible|supplied|supply|nearby|in stock|on site|readily|obtainable)\b")
_ROLE_PREF = re.compile(r"\b(prefer\w*|would like|like to|wish|if possible|open to|consider\w*|"
                        r"interested|ideally|desire\w*|want to|hope)\b")
_ROLE_REQ = re.compile(r"\b(use|using|used|must|shall|require\w*|mandatory|specified|incorporate\w*|"
                       r"include\w*|contain\w*|need)\b")


def _scm_role(hit_list) -> str:
    roles = set()
    for h in hit_list:
        c = h.clause
        if h.negated or _ROLE_RESTRICT.search(c):
            roles.add("restricted")
        elif _ROLE_AVAIL.search(c):
            roles.add("available")
        elif _ROLE_PREF.search(c):
            roles.add("preferred")
        elif _ROLE_REQ.search(c):
            roles.add("required")
        else:
            roles.add("mentioned")
    for r_ in ("restricted", "required", "preferred", "available", "mentioned"):
        if r_ in roles:
            return r_
    return "mentioned"


@dataclass
class Analysis:
    text: str = ""
    hits: dict = field(default_factory=dict)
    grades: list = field(default_factory=list)
    slump: int | None = None
    max_size: int | None = None
    max_size_note: str = ""
    scm_pct: dict = field(default_factory=dict)
    scm_roles: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def aff(self, key):
        return [h for h in self.hits.get(key, []) if not h.negated]

    def has(self, key):
        return bool(self.aff(key))

    def uncertain(self, key):
        a = self.aff(key)
        return bool(a) and all(h.uncertain for h in a)

    def negated_only(self, key):
        return key in self.hits and not self.has(key)

    # ---- derived facts ----
    @property
    def member(self):
        if self.has("prestressed"):
            return "Prestressed"
        if self.has("pcc") and not self.has("rcc"):
            return "PCC"
        if self.has("rcc") or self.has("building") or self.has("bridge") or self.has("member"):
            return "RCC"
        return None

    @property
    def cement(self):
        found = [k for k in ("opc43", "opc53", "ppc", "psc") if self.has(k)]
        m = {"opc43": ("OPC 43", 43), "opc53": ("OPC 53", 53), "ppc": ("PPC", 43), "psc": ("PSC", 43)}
        return [m[k] for k in found]

    @property
    def hsc(self):
        return self.has("hsc") or any(g >= 60 for g in self.grades)

    def summary(self):
        L = []
        if self.grades:
            L.append("Grade stated: " + ", ".join(f"M{g}" for g in self.grades))
        elif self.has("hsc"):
            L.append("Strength: 'high strength' (grade not stated)")
        elif self.has("normal_strength"):
            L.append("Strength: normal/ordinary (grade not stated)")
        struct = [lbl for k, lbl in (("building", "building"), ("high_rise", "high-rise"),
                                     ("bridge", "bridge/pier/abutment"), ("dam_tank", "water-retaining/tank/dam"),
                                     ("pavement", "floor/pavement"), ("precast", "precast"),
                                     ("member", "structural member")) if self.has(k)]
        if self.member:
            struct.append(self.member)
        if struct:
            L.append("Structure/application: " + ", ".join(dict.fromkeys(struct)))
        cond = [k for k in _CONFIRMABLE + ("rain", "sheltered", "condensation", "submerged_fresh",
                                           "underground", "underwater") if self.has(k)]
        if cond:
            L.append("Environment cues: " + ", ".join(
                c + ("?" if self.uncertain(c) else "") for c in cond))
        ign = [k for k in _CONFIRMABLE if self.negated_only(k)]
        if ign:
            L.append("Negated (ignored): " + ", ".join(ign))
        wk = [lbl for k, lbl in (("pumpable", "pumped"), ("congested", "congested reinforcement"),
                                 ("underwater", "underwater/tremie"), ("high_workability", "long haul/slump retention"),
                                 ("scc", "self-compacting"), ("mass", "mass concrete/heat control"),
                                 ("hot_weather", "hot weather"), ("cold_weather", "cold weather"),
                                 ("early_strength", "early strength"), ("low_perm", "low permeability/durability"),
                                 ("shrinkage", "shrinkage control")) if self.has(k)]
        if self.slump:
            wk.append(f"slump {self.slump} mm")
        if wk:
            L.append("Placement/performance: " + ", ".join(wk))
        if self.max_size:
            L.append(f"Aggregate size stated: {self.max_size} mm")
        if self.cement:
            L.append("Cement stated: " + ", ".join(c[0] for c in self.cement))
        if self.scm_roles:
            L.append("SCMs: " + ", ".join(
                f"{k.replace('_', ' ')} ({v}" + (f", {self.scm_pct[k]:g} %" if k in self.scm_pct else "") + ")"
                for k, v in self.scm_roles.items()))
        qc = [lbl for k, lbl in (("rmc", "RMC"), ("site_batch", "site batching"),
                                 ("poor_qc", "poor QC stated"), ("good_qc", "good QC stated (not accepted without checks)"),
                                 ("moisture_var", "variable aggregate moisture"),
                                 ("high_var", "high strength variability"),
                                 ("limited_curing", "limited curing")) if self.has(k)]
        if qc:
            L.append("Production cues: " + ", ".join(qc))
        L += [f"Note: {n}" for n in self.notes]
        return L or ["(nothing recognised in the description)"]


def analyse_job(text: str) -> Analysis:
    a = Analysis(text=text or "")
    if not text or not text.strip():
        return a
    a.hits = detect(text)
    low = text.lower().replace("\u2019", "'")
    # grades
    gs = [int(g) for g in re.findall(r"(?<![a-z0-9])m\s?(\d{2})(?![0-9a-z])", low)]
    gs += [int(g) for g in re.findall(r"\bgrade\s+(?:of\s+)?(?:m\s?)?(\d{2})\b(?!\s*(?:cement|opc))", low)]
    a.grades = sorted({g for g in gs if g in VALID_GRADES})
    # slump (before hyphen normalisation, so that ranges survive)
    sl = []
    for m in re.finditer(r"slump[^0-9]{0,15}(\d{2,3})(?:\s*(?:-|\u2013|to)\s*(\d{2,3}))?", low):
        sl += [int(x) for x in m.groups() if x]
    for m in re.finditer(r"(\d{2,3})(?:\s*(?:-|\u2013|to)\s*(\d{2,3}))?\s*mm\s*slump", low):
        sl += [int(x) for x in m.groups() if x]
    sl = [x for x in sl if 10 <= x <= 300]
    a.slump = max(sl) if sl else None
    # aggregate size (slump expressions removed first)
    low2 = re.sub(r"\d{2,3}(?:\s*(?:-|\u2013|to)\s*\d{2,3})?\s*mm\s*slump", " ", low)
    low2 = re.sub(r"slump[^0-9]{0,15}\d{2,3}(?:\s*(?:-|\u2013|to)\s*\d{2,3})?(?:\s*mm)?", " ", low2)
    sz = re.findall(r"(?<![\d.])(10|12\.5|20|25|40)\s*mm\b", low2)
    if sz:
        v = float(sz[0])
        if v in WATER_T4:
            a.max_size = int(v)
        else:
            a.max_size_note = f"{sz[0]} mm aggregate is not tabulated in IS 10262 Table 4/5; choose 10, 20 or 40 mm."
            a.notes.append(a.max_size_note)
    # SCM roles and percentages
    for key in ("fly_ash", "ggbs", "silica_fume", "scm_generic", "metakaolin"):
        if key in a.hits:
            a.scm_roles[key] = _scm_role(a.hits[key])
    pats = [r"(fly\s?ash|ggbs|slag|silica\s?fume)\D{0,20}?(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)",
            r"(\d{1,2}(?:\.\d+)?)\s*(?:%|percent)\s*(?:of\s*)?(?:cement\s*)?(?:replacement\s*)?(?:by\s*)?"
            r"(fly\s?ash|ggbs|slag|silica\s?fume)"]
    def _scm_key(name):
        name = name.replace(" ", "")
        return ("fly_ash" if name.startswith("fly") else
                "silica_fume" if name.startswith("silica") else "ggbs")
    for i, p in enumerate(pats):
        for m in re.finditer(p, low):
            name, pct = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
            a.scm_pct[_scm_key(name)] = float(pct)
    if "metakaolin" in a.scm_roles:
        a.notes.append("Metakaolin is recognised but is not modelled by this tool; "
                       "proportion it manually or treat as a verification item.")
    if a.has("recycled_agg") or a.has("lightweight") or a.has("heavy"):
        a.notes.append("Recycled, lightweight or heavyweight aggregate is outside the "
                       "normal-weight IS 10262 procedure used here; results are not valid for it.")
    if a.has("scc"):
        a.notes.append("Self-compacting concrete needs a dedicated mix procedure (not IS 10262 Annex A).")
    return a


# ---------------------------------------------------------------------------
# 3b. EXPOSURE CLASSIFICATION  (IS 456:2000 Table 3; transparent rule mapping)
# ---------------------------------------------------------------------------
SEVERITY = {c: i for i, c in enumerate(EXPOSURES)}

# Table 3 wording (paraphrased) per class, used for evidence/trace output
TABLE3_TEXT = {
    "Mild": "surfaces protected against weather/aggressive conditions (except coastal areas)",
    "Moderate": "sheltered from severe rain/freezing; condensation and rain; continuously under water; "
                "non-aggressive soil/ground water; sheltered from saturated salt air in coastal area",
    "Severe": "severe rain; alternate wetting and drying; occasional freezing whilst wet; severe "
              "condensation; completely immersed in sea water; coastal environment",
    "Very severe": "sea-water spray; corrosive fumes; severe freezing whilst wet; in contact with or "
                   "buried under aggressive sub-soil/ground water",
    "Extreme": "members in tidal zone; direct contact with liquid/solid aggressive chemicals",
}


@dataclass
class Question:
    id: str
    text: str
    # (label, resulting class or None, note)
    options: list
    not_sure: tuple = ("Not sure", "Very severe", "unknown: conservative class adopted")


@dataclass
class Cand:
    cls: str
    why: str
    conf: str = "high"


@dataclass
class ExposureResult:
    cls: str | None = None
    cands: list = field(default_factory=list)
    pending: Question | None = None
    assumptions: list = field(default_factory=list)
    chem_notes: list = field(default_factory=list)
    ref: str = "IS 456:2000 Table 3 (and Cl. 8.2.3)"


Q_SEA = Question(
    "q_sea", "How is the concrete exposed to the sea / coastal environment?",
    [("Tidal zone (wetted and dried by tides)", "Extreme", "tidal zone"),
     ("Sea-water spray / splash", "Very severe", "sea-water spray"),
     ("Completely immersed in sea water", "Severe", "completely immersed in sea water"),
     ("Coastal air only, no direct sea contact", "Severe", "coastal environment"),
     ("Coastal location but sheltered from saturated salt air", "Moderate",
      "sheltered from saturated salt air in coastal area")],
    ("Not sure", "Very severe", "sea exposure unknown: spray assumed (conservative)"))
Q_CHLORIDE = Question(
    "q_chloride", "Where do the chlorides/salts come from?",
    [("Sea-water spray or marine atmosphere", "Very severe", "chlorides from sea-water spray"),
     ("Coastal air only", "Severe", "coastal environment"),
     ("Chloride-bearing ground water / soil in contact", "Very severe", "aggressive ground water"),
     ("Not in contact with chloride sources", None, "chlorides not in contact")],
    ("Not sure", "Very severe", "chloride source unknown: conservative class adopted"))
Q_SOIL = Question(
    "q_soil", "Is the soil / ground water in contact with the concrete aggressive (sulphates, chlorides, acids)?",
    [("No, non-aggressive (confirmed by soil/water report)", "Moderate", "non-aggressive soil/ground water"),
     ("Yes, aggressive", "Very severe", "aggressive sub-soil/ground water")],
    ("Not sure (no soil report yet)", "Very severe", "soil aggressiveness unknown: conservative class adopted"))
Q_CHEM = Question(
    "q_chem", "How is the concrete exposed to chemicals / sewage / wastewater?",
    [("Direct contact with aggressive liquid or solid chemicals", "Extreme", "direct contact with aggressive chemicals"),
     ("Corrosive fumes/atmosphere only (no direct contact)", "Very severe", "corrosive fumes"),
     ("Only non-aggressive (treated/clean) water", "Moderate", "continuously under non-aggressive water"),
     ("Chemicals are not in contact with this concrete", None, "no chemical exposure")],
    ("Not sure", "Extreme", "chemical contact unknown: conservative class adopted"))
Q_FREEZE = Question(
    "q_freeze", "How severe is freezing while the concrete is wet?",
    [("Occasional freezing", "Severe", "occasional freezing whilst wet"),
     ("Severe / frequent freeze-thaw", "Very severe", "severe freezing whilst wet")],
    ("Not sure", "Very severe", "freezing severity unknown: conservative class adopted"))
Q_GENERAL = Question(
    "q_general", "Which best describes where the concrete will be?",
    [("Inside a building, protected from weather", "Mild", "protected against weather"),
     ("Sheltered outdoors / condensation and rain / under water / non-aggressive soil", "Moderate", "moderate conditions"),
     ("Severe rain, alternate wetting and drying, coastal environment, freezing", "Severe", "severe conditions"),
     ("Sea-water spray, corrosive fumes, aggressive soil or ground water", "Very severe", "very severe conditions"),
     ("Tidal zone or direct contact with aggressive chemicals", "Extreme", "extreme conditions")],
    ("Not sure", "Severe", "environment unknown: Severe adopted provisionally"))


def _qa(q: Question, ans):
    """ans is 0 (not sure) or 1..n -> (class, note)."""
    if ans == 0:
        return q.not_sure[1], q.not_sure[2]
    lbl, cls, note = q.options[ans - 1]
    return cls, note


def classify_exposure(a: Analysis, answers: dict | None = None) -> ExposureResult:
    """Rule-based mapping from extracted conditions to IS 456 Table 3 classes.
    Returns the governing (most severe) class, or a pending follow-up Question.
    answers: {question id: 0..n}; 'confirm_<key>': 1 yes / 2 no / 0 not sure."""
    ans = answers or {}
    res = ExposureResult()

    def active(key):
        if not a.has(key):
            return False
        if a.uncertain(key) and key in _CONFIRMABLE:
            cq = ans.get("confirm_" + key)
            if cq is None:
                return None            # needs confirmation
            if cq == 2:
                return False
            if cq == 0:
                res.assumptions.append(f"'{key}' stated as uncertain and not confirmed: treated as present (conservative).")
        return True

    def need(qid, q):
        if qid not in ans:
            res.pending = q
            return True
        return False

    def add_from_q(q):
        cls, note = _qa(q, ans[q.id])
        if cls:
            res.cands.append(Cand(cls, note, "medium" if ans[q.id] == 0 else "high"))
            if ans[q.id] == 0:
                res.assumptions.append(f"{q.id}: {note}.")

    # 0. confirmations for uncertain statements
    for key in _CONFIRMABLE:
        if active(key) is None:
            res.pending = Question(
                "confirm_" + key, f"The description suggests '{key.replace('_', ' ')}' may apply. Does it?",
                [("Yes, it applies", None, "confirmed"), ("No, it does not apply", None, "denied")],
                ("Not sure (treat as applying)", None, "unconfirmed"))
            return res

    sea_def = False
    if active("tidal"):
        res.cands.append(Cand("Extreme", "tidal zone stated")); sea_def = True
    if active("sea_spray"):
        res.cands.append(Cand("Very severe", "sea-water spray stated")); sea_def = True
    if active("immersed_sea"):
        res.cands.append(Cand("Severe", "completely immersed in sea water stated")); sea_def = True

    sea_words = any(active(k) for k in ("seawater", "marine", "coastal"))
    if not sea_def and sea_words:
        if need("q_sea", Q_SEA):
            return res
        add_from_q(Q_SEA)
        sea_def = True
    if active("chloride") and not sea_def:
        if need("q_chloride", Q_CHLORIDE):
            return res
        add_from_q(Q_CHLORIDE)

    agg = active("sulphate") or active("aggressive_gw")
    if agg:
        res.cands.append(Cand("Very severe", "aggressive sulphate/ground-water condition stated"))
        res.chem_notes.append("Sulphate/aggressive ground water: sulphate-attack provisions of IS 456 "
                              "(Cl. 8.2.5 / Table 4) also apply (cement type, minimum cement, max w/c). "
                              "These are NOT applied by this tool: check them separately.")
    underground = a.has("underground") or a.has("underwater")
    if a.has("underground") and not agg:
        if a.negated_only("aggressive_gw") or a.negated_only("sulphate"):
            res.cands.append(Cand("Moderate", "buried/soil contact, aggressive soil/water explicitly denied", "medium"))
        else:
            if need("q_soil", Q_SOIL):
                return res
            add_from_q(Q_SOIL)

    if active("chemical_direct") or active("sewage"):
        if need("q_chem", Q_CHEM):
            return res
        add_from_q(Q_CHEM)
        res.chem_notes.append("Chemical/sewage exposure: Table 3 classification does not by itself cover "
                              "chemical attack; specify cement type, protective measures and "
                              "w/c per project specification/IS 456 Cl. 8.2.5 (not applied here).")
    if active("polluted_air"):
        res.cands.append(Cand("Very severe", "corrosive fumes/polluted atmosphere stated"))
    if active("heavy_rain"):
        res.cands.append(Cand("Severe", "severe/heavy rain stated"))
    elif a.has("rain"):
        res.cands.append(Cand("Moderate", "exposed to rain/weather (not stated as severe)", "medium"))
    if active("wet_dry"):
        res.cands.append(Cand("Severe", "alternate wetting and drying stated"))
    if active("freezing"):
        if need("q_freeze", Q_FREEZE):
            return res
        add_from_q(Q_FREEZE)
    if a.has("submerged_fresh") or a.has("underwater"):
        res.cands.append(Cand("Moderate", "continuously under water (assumes non-aggressive water)", "medium"))
    if a.has("condensation"):
        res.cands.append(Cand("Moderate", "condensation/humid conditions", "medium"))
    if a.has("sheltered") and not res.cands and not sea_words:
        res.cands.append(Cand("Mild", "protected from weather (interior)"))

    if not res.cands:
        if need("q_general", Q_GENERAL):
            return res
        add_from_q(Q_GENERAL)

    res.cands.sort(key=lambda c: -SEVERITY[c.cls])
    res.cls = res.cands[0].cls
    return res


# ---------------------------------------------------------------------------
# 3c. QUALITY CONTROL / STANDARD DEVIATION
# ---------------------------------------------------------------------------
QC_CHECKS = [
    ("cement", "Cement is stored properly (dry, separated by batch/type)"),
    ("weigh", "All materials are weigh-batched (not by volume)"),
    ("water", "Water is added in a controlled, measured way"),
    ("agg", "Aggregate grading and moisture content are checked regularly"),
    ("test", "Workability and strength are checked periodically"),
]


def sd_from_results(results):
    n = len(results)
    if n < 2:
        raise ValueError("At least 2 results are needed to compute a standard deviation.")
    mean = sum(results) / n
    return math.sqrt(sum((x - mean) ** 2 for x in results) / (n - 1))


@dataclass
class SDResult:
    sd: float | None          # None -> use Table 2 (+1 if control 'Fair')
    control: str
    method: str
    reason: str
    flags: list = field(default_factory=list)


def determine_sd(grade, *, n_results=0, sd_hist=None, relevant=True, checks=None) -> SDResult:
    """Choose the basis for s (IS 10262 Cl. on standard deviation / Table 2 notes)."""
    if n_results >= 30 and sd_hist and relevant:
        return SDResult(
            sd_hist, "Good", f"Established from {n_results} documented results",
            f"{n_results} (>= 30) relevant strength results give s = {sd_hist:.2f} N/mm2; used instead of Table 2.",
            ["Check the standard for any lower bound on a computed s and for the age/currency of the data."])
    why = []
    if n_results and n_results < 30:
        why.append(f"only {n_results} results (< 30) are not an adequate basis")
    if n_results and not relevant:
        why.append("results are not from the same grade/materials/production")
    checks = checks or {}
    ok = all(checks.get(k) is True for k, _ in QC_CHECKS)
    if ok:
        return SDResult(None, "Good", "Assumed from Table 2 (good control)",
                        "; ".join(why + ["all five site-control conditions confirmed"]).capitalize() + ".")
    failed = [lbl for k, lbl in QC_CHECKS if checks.get(k) is not True]
    return SDResult(None, "Fair", "Assumed from Table 2, increased by 1 N/mm2 (control not fully demonstrated)",
                    "; ".join(why + [f"{len(failed)} of 5 good-control conditions not confirmed"]).capitalize() + ".",
                    ["Unanswered/'not sure' conditions are treated as not met: " + "; ".join(failed)])


# ---------------------------------------------------------------------------
# 3d. SCM DECISION SUPPORT
# ---------------------------------------------------------------------------
SCM_LIMITS = {"fly_ash": 35.0, "ggbs": 70.0, "silica_fume": 10.0}    # typical practice; verify
SCM_MENU = {"fly_ash": [15, 20, 25, 30, 35], "ggbs": [25, 35, 40, 50, 60, 70],
            "silica_fume": [5, 7.5, 10]}
SCM_LABEL = {"fly_ash": "Fly ash", "ggbs": "GGBS", "silica_fume": "Silica fume"}


def scm_allowed(cement_type: str):
    """Avoid double-counting: PPC already contains fly ash, PSC already contains slag."""
    out = ["fly_ash", "ggbs", "silica_fume"]
    if cement_type == "PPC":
        out.remove("fly_ash")
    if cement_type == "PSC":
        out.remove("ggbs")
    return out


def scm_recommendations(s: Spec, a: Analysis, exposure_cls: str) -> list:
    """Engineering recommendations (not code requirements). Returns [(material, typical %, reason)]."""
    rec = []
    allowed = scm_allowed(s.cement_type)
    if (a.hsc or s.grade >= 60) and "silica_fume" in allowed:
        rec.append(("silica_fume", 8.0, "high-strength grade: densifies the paste and allows a very low w/b"))
    durable = exposure_cls in ("Very severe", "Extreme") or a.has("sulphate") or a.has("chloride") \
        or a.has("seawater") or a.has("marine") or a.has("low_perm")
    if durable:
        if "ggbs" in allowed:
            rec.append(("ggbs", 35.0, "chloride/sulphate resistance and lower permeability"))
        if "fly_ash" in allowed:
            rec.append(("fly_ash", 25.0, "lower permeability and heat; later-age strength gain"))
    if a.has("mass"):
        for m_, p_ in (("fly_ash", 25.0), ("ggbs", 40.0)):
            if m_ in allowed and m_ not in [r[0] for r in rec]:
                rec.append((m_, p_, "lower heat of hydration for thick sections"))
    if a.has("sustainable"):
        for m_, p_ in (("fly_ash", 25.0), ("ggbs", 35.0)):
            if m_ in allowed and m_ not in [r[0] for r in rec]:
                rec.append((m_, p_, "reduced clinker content / cost"))
    return rec


# ---------------------------------------------------------------------------
# 3e. ADMIXTURE DECISION SUPPORT
# ---------------------------------------------------------------------------
@dataclass
class AdmixDecision:
    status: str                   # Likely required / Potentially beneficial / Not currently indicated
    types: list
    reasons: list
    verify: list


def assess_admixture(s: Spec, a: Analysis, base: dict | None) -> AdmixDecision:
    """base = compute() result for the design WITHOUT admixture credit (None if it could not be solved)."""
    req, ben, types, verify = [], [], [], []
    if base is None:
        req.append("the mix cannot be proportioned (aggregate volume <= 0) at the required w/cm and "
                   "workability without a water-reducing admixture")
    else:
        if base["wcm_eff"] <= 0.35:
            req.append(f"w/cm {base['wcm_eff']:.2f} with {s.slump} mm slump needs about "
                       f"{base['water']:.0f} kg/m3 of water without admixture, i.e. "
                       f"{base['binder']:.0f} kg/m3 of cementitious")
        if base["opc"] > s.max_cement:
            req.append(f"cement would be {base['opc']:.0f} kg/m3 (> {s.max_cement:.0f} kg/m3 limit) without water reduction")
        elif base["binder"] > 550:
            req.append(f"total cementitious would be {base['binder']:.0f} kg/m3 (very high) without water reduction")
    if s.grade >= 60:
        req.append("high-strength grade (M60 or above) is not practical without a high-range water reducer")
    for k, txt in (("pumpable", "pumping"), ("congested", "congested reinforcement"),
                   ("underwater", "tremie/underwater placement"), ("scc", "self-compacting requirement"),
                   ("high_workability", "long haul / slump retention"), ("mass", "mass pour"),
                   ("hot_weather", "hot weather"), ("early_strength", "early-strength requirement"),
                   ("low_perm", "low-permeability requirement")):
        if a.has(k):
            ben.append(txt)
    if s.slump >= 100:
        ben.append(f"slump {s.slump} mm")
    if s.placing == "Pumped" and "pumping" not in ben:
        ben.append("pumped placing")
    if s.placing == "Tremie" and "tremie/underwater placement" not in ben:
        ben.append("tremie placing")

    wr = bool(req) or bool(ben)
    if wr:
        types.append("Water-reducing / PCE-based superplasticizer (IS 9103 type)")
    if a.has("hot_weather") or a.has("high_workability") or a.has("mass"):
        types.append("Retarding admixture (slump retention / set control)")
        verify.append("retarder: confirm set times and strength at the actual temperature and haul time")
    if a.has("early_strength") or a.has("cold_weather"):
        types.append("Accelerating admixture (must be chloride-free for reinforced/prestressed concrete)")
        verify.append("accelerator: confirm chloride content and long-term strength")
    if a.has("freezing"):
        types.append("Air-entraining admixture (freeze-thaw), only if freezing is confirmed")
    status = ("Likely required by the proposed design" if req else
              "Potentially beneficial" if ben else "Not currently indicated")
    verify = [
        "compatibility of the admixture with the specific cement and SCMs (trial mixes)",
        "water reduction, dose and slump retention from manufacturer data or trials (none is assumed here)",
        "chloride and alkali limits of the admixture for the exposure class"] + verify
    if len(types) > 1:
        status += "; needs verification (several admixture types, check combination)"
    return AdmixDecision(status, types, req + ben, verify)

# ============================================================================
# 4.  CALCULATION  (IS 10262:2019 steps)
# ============================================================================


def validate_spec(s: Spec):
    """Reject impossible inputs with a message the user can act on."""
    e = []
    if not (10 <= s.grade <= 80):
        e.append(f"grade M{s.grade} is outside M10-M80")
    if not (10 <= s.slump <= 300):
        e.append(f"slump {s.slump} mm is outside 10-300 mm")
    for nm, v, lo, hi in (("admixture water reduction %", s.admix_red, 0, 40),
                          ("admixture dose %", s.admix_dose, 0, 5),
                          ("fly ash %", s.fa_pct, 0, 100), ("GGBS %", s.ggbs_pct, 0, 100),
                          ("silica fume %", s.sf_pct, 0, 100),
                          ("specific gravity of cement", s.sg_cement, 2.5, 3.5),
                          ("specific gravity of CA", s.sg_ca, 2.0, 3.5),
                          ("specific gravity of FA", s.sg_fa, 2.0, 3.5),
                          ("absorption CA %", s.abs_ca, 0, 15), ("absorption FA %", s.abs_fa, 0, 15)):
        if not (lo <= v <= hi):
            e.append(f"{nm} = {v} is outside {lo}-{hi}")
    for nm, v in (("moisture content CA %", s.mc_ca), ("moisture content FA %", s.mc_fa)):
        if v is not None and not (0 <= v <= 40):
            e.append(f"{nm} = {v} is outside 0-40")
    if s.wcm_user is not None and not (0.2 <= s.wcm_user <= 0.9):
        e.append(f"user w/cm {s.wcm_user} is outside 0.20-0.90")
    if s.sd_user is not None and not (0.5 <= s.sd_user <= 15):
        e.append(f"standard deviation {s.sd_user} is outside 0.5-15 N/mm2")
    if s.cement_type == "PPC" and s.fa_pct > 0:
        e.append("PPC already contains fly ash: adding fly ash would double-count it")
    if s.cement_type == "PSC" and s.ggbs_pct > 0:
        e.append("PSC already contains slag: adding GGBS would double-count it")
    if s.fa_pct + s.ggbs_pct + s.sf_pct >= 100:
        e.append("SCM percentages add up to 100 % or more")
    if e:
        raise ValueError("; ".join(e))

def compute(s: Spec, wcm_override=None, water_override=None):
    validate_spec(s)
    r = {"flags": []}
    F = r["flags"]

    # ---- A-3 target strength ----
    sd, X = std_dev(s.grade)
    if s.control == "Fair" and not s.sd_user:
        sd += 1.0
        F.append("Fair site control: s increased by 1 N/mm2 (IS 10262 Table 2 note).")
    if s.sd_user:
        sd = s.sd_user
    t1, t2 = s.grade + 1.65 * sd, s.grade + X
    target = max(t1, t2)
    r.update(sd=sd, X=X, t1=t1, t2=t2, target=target)

    # ---- durability limits (IS 456 Table 5 / 6) ----
    row = IS456_T5[s.exposure]
    min_c, max_wc, min_gr = row[0:3] if s.member == "RCC" else row[3:6]
    min_c_adj = min_c + SIZE_ADJ[s.max_size]
    if s.min_cement_user:
        min_c_adj = max(min_c_adj, s.min_cement_user)
    if s.max_wc_user:
        max_wc = min(max_wc, s.max_wc_user)
    r.update(min_c=min_c, min_c_adj=min_c_adj, max_wc=max_wc, min_gr=min_gr)
    if s.grade < min_gr:
        F.append(f"Grade M{s.grade} is below IS 456 Table 5 minimum M{min_gr} for "
                 f"{s.exposure} exposure ({s.member}). Use at least M{min_gr}.")

    # ---- A-5 water-cement ratio ----
    wc_curve = wc_from_strength(target, s.cement_class)
    r["wc_curve"] = wc_curve
    if wcm_override is not None:
        wcm = wcm_override
    elif s.wcm_user:
        wcm = s.wcm_user
    else:
        wcm_raw = min(wc_curve, max_wc)
        r["hsc_floor"] = None
        if wc_curve < 0.30:
            # beyond the curve: use a practical w/b floor for HSC (w/b ~0.30 at M60 down
            # to ~0.26 at M80 with silica fume + high-range water reducer)
            floor = max(0.24, 0.30 - 0.002 * max(0, s.grade - 60))
            r["hsc_floor"] = floor
            wcm_raw = max(wcm_raw, floor)
            F.append(f"Strength curve (Fig. 1) is exhausted below w/c 0.30 (needs {wc_curve:.2f}, "
                     f"extrapolated). Practical HSC w/b of {floor:.2f} adopted instead; the "
                     "strength then relies on silica fume + HRWR + good aggregate: confirm by trials.")
        wcm = math.floor(wcm_raw * 100 + 1e-9) / 100
    if wcm < 0.25:
        F.append(f"w/cm {wcm:.2f} is below practical range; check feasibility.")
    if wcm > max_wc + 1e-9:
        F.append(f"Adopted w/cm {wcm:.2f} exceeds IS 456 Table 5 limit {max_wc:.2f}.")
    r["wcm_design"] = wcm
    r["limit_governs"] = wc_curve > max_wc

    # ---- A-4 air, A-6 water ----
    r["air"] = AIR_T3[s.max_size]
    base = WATER_T4[s.max_size]
    shape_red = s.shape_red_user if s.shape_red_user is not None else SHAPE_REDUCTION[s.agg_shape]
    w_shape = base - shape_red
    slump_inc = 0.03 * (s.slump - 50) / 25  # +3% per 25 mm above 50 mm; -3% below
    w_slump = w_shape * (1 + slump_inc)
    water = w_slump * (1 - s.admix_red / 100)
    if water_override is not None:
        water = water_override
    r.update(w_base=base, shape_red=shape_red, w_shape=w_shape, slump_inc=slump_inc,
             w_slump=w_slump, water=water)
    if s.admix_red > 30:
        F.append("Water reduction above 30 % is at the very top of what PCE admixtures give; "
                 "confirm with the supplier's trial data.")
    if s.slump > 100 and s.admix_red == 0:
        F.append("Slump > 100 mm with no water-reducing admixture inflates water/cement demand.")

    # ---- A-7 cementitious content ----
    binder0 = water / wcm
    binder = binder0
    boost = (s.fa_pct + s.ggbs_pct) > 0 and s.scm_boost and s.grade < 60
    if (s.fa_pct + s.ggbs_pct) > 0 and s.scm_boost and s.grade >= 60:
        F.append("+10 % cementitious allowance for fly ash/GGBS not applied for HSC "
                 "(w/b is fixed by the HSC floor).")
    if boost:
        binder = binder0 * 1.10
    r.update(binder0=binder0, boost=boost)
    if binder < min_c_adj:
        F.append(f"Computed cementitious {binder:.0f} < durability minimum {min_c_adj:.0f}; "
                 "minimum adopted (IS 456 Table 5/6).")
        binder = min_c_adj
    r["binder"] = binder
    r["wcm_eff"] = water / binder

    fly, ggbs, sf = (binder * p / 100 for p in (s.fa_pct, s.ggbs_pct, s.sf_pct))
    opc = binder - fly - ggbs - sf
    r.update(opc=opc, fly=fly, ggbs=ggbs, sf=sf)
    if opc > s.max_cement:
        F.append(f"Cement (OPC portion) {opc:.0f} > {s.max_cement:.0f} kg/m3 (IS 456 Cl. 8.2.4.2); "
                 "reduce binder via more admixture/SCM or justify for HSC.")
    if binder > 550:
        F.append(f"Total cementitious {binder:.0f} kg/m3 is high (good-practice ceiling ~550): "
                 "check heat of hydration, shrinkage and cost.")
    if s.fa_pct > 35:
        F.append("Fly ash > 35 % exceeds typical IS 1489 / IS 456 Cl. 5.2 practice.")
    if s.ggbs_pct > 70:
        F.append("GGBS > 70 % exceeds typical IS 455 practice.")
    if s.sf_pct > 10:
        F.append("Silica fume > 10 % is above the usual 5-10 % range.")

    # ---- A-8 aggregate proportions ----
    base_frac = CA_VOL_T5[s.max_size][s.fa_zone]
    frac = base_frac + 0.01 * (0.5 - r["wcm_eff"]) / 0.05
    r["frac_base"], r["frac_wc_corr"] = base_frac, frac - base_frac
    if s.placing == "Pumped":
        frac *= 0.90
        F.append("Pumping: CA volume fraction reduced by 10 % (IS 10262 guidance for pumpable concrete; verify clause).")
    frac = min(max(frac, 0.40), 0.80)
    r["frac"] = frac

    # ---- A-9 absolute-volume calculation ----
    adm_mass = binder * s.admix_dose / 100
    v_c = opc / (s.sg_cement * 1000)
    v_fly = fly / (s.sg_fly * 1000)
    v_ggbs = ggbs / (s.sg_ggbs * 1000)
    v_sf = sf / (s.sg_sf * 1000)
    v_w = water / 1000
    v_ad = adm_mass / (s.sg_admix * 1000)
    v_air = r["air"] / 100 if s.deduct_air else 0.0
    v_agg = 1.0 - (v_c + v_fly + v_ggbs + v_sf + v_w + v_ad + v_air)
    if v_agg <= 0.05:
        raise ValueError(f"the paste volume leaves only {v_agg:.3f} m3 for aggregate: "
                         "reduce the cementitious content (lower w/cm demand, add a water reducer) or relax the workability")
    ca = v_agg * frac * s.sg_ca * 1000
    fa = v_agg * (1 - frac) * s.sg_fa * 1000
    r.update(adm_mass=adm_mass, v_c=v_c, v_fly=v_fly, v_ggbs=v_ggbs, v_sf=v_sf, v_w=v_w,
             v_ad=v_ad, v_air=v_air, v_agg=v_agg, ca=ca, fa=fa)

    # ---- field moisture correction ----
    free_fa = (s.mc_fa - s.abs_fa) if s.mc_fa is not None else 0.0
    free_ca = (s.mc_ca - s.abs_ca) if s.mc_ca is not None else 0.0
    fa_b = fa * (1 + free_fa / 100)
    ca_b = ca * (1 + free_ca / 100)
    water_b = water - fa * free_fa / 100 - ca * free_ca / 100
    if water_b <= 0:
        raise ValueError("moisture correction leaves no mixing water: check the aggregate moisture inputs")
    if s.admix_dose == 0 and s.admix_red > 0:
        F.append("Water reduction is credited but the admixture dose is not fixed: its mass/volume is not "
                 "in the quantities; confirm in trials.")
    r.update(free_fa=free_fa, free_ca=free_ca, fa_b=fa_b, ca_b=ca_b, water_b=water_b)
    r["density"] = opc + fly + ggbs + sf + water + adm_mass + ca + fa
    return r



UNVERIFIED_ITEMS = [
    "IS 10262:2019 Fig. 1 w/c vs strength curves (approximate digitisation; replace with calibrated data)",
    "IS 10262:2019 +10 % cementitious allowance with fly ash/GGBS (Cl. 5.4.1); indicative 20 % superplasticizer water reduction (Cl. 5.3)",
    "Practical w/b floor for high-strength concrete: this tool's own engineering assumption, not an IS clause",
    "SCM limits (fly ash 35 %, GGBS 70 %, silica fume 10 %) and the recommended percentages: typical practice, not code-verified limits",
    "Sulphate-attack (IS 456 Cl. 8.2.5 / Table 4) and chloride-limit provisions are NOT implemented",
    "Air-content volume deduction is now enabled by default; confirm the applicable code procedure and project specification",
]

# ============================================================================
# 5.  REPORT  (IS 10262:2019 Annex A layout)
# ============================================================================

def build_report(s: Spec, r: dict, trials: list) -> str:
    L = []

    def H(t):
        L.extend(["", t, "-" * len(t)])

    def kv(k, v):
        L.append(f"  {k:<48}{v}")

    bar = "=" * 74
    L += [bar, "CONCRETE MIX PROPORTIONING  --  IS 10262:2019 (Annex A format)",
          "with IS 456:2000 durability provisions", f"Date: {date.today():%d %b %Y}", bar]

    mats = set(s.assumed_materials)
    tag = lambda *names: "  (assumed)" if any(n in mats for n in names) else ""

    H("A-1  STIPULATIONS FOR PROPORTIONING")
    kv("a) Grade designation", f"M{s.grade}")
    kv("b) Type of cement", f"{s.cement_type} (28-d strength class {s.cement_class} MPa)")
    kv("c) Maximum nominal size of aggregate", f"{s.max_size} mm")
    kv("d) Minimum cement content (IS 456 Tbl 5/6)", f"{r['min_c_adj']:.0f} kg/m3")
    kv("e) Maximum water-cement ratio (IS 456 Tbl 5)", f"{r['max_wc']:.2f}")
    kv("f) Workability", f"{s.slump} mm slump (at placement)")
    kv("g) Exposure condition (IS 456 Tbl 3)", f"{s.exposure} ({s.member})")
    for ev in s.exposure_basis:
        L.append(f"       evidence: {ev}")
    kv("h) Degree of supervision", s.control)
    kv("i) Type of aggregate", {"crushed": "Crushed angular", "subangular": "Sub-angular",
                                 "gravel+crushed": "Gravel with crushed", "rounded": "Rounded gravel"}[s.agg_shape])
    kv("j) Maximum cement content (IS 456 8.2.4.2)", f"{s.max_cement:.0f} kg/m3")
    kv("k) Chemical admixture", (f"{s.admix_name}, {s.admix_dose:g} % of cementitious, "
                                 f"{s.admix_red:g} % water reduction") if s.admix_dose else "None")
    scm = [f"{n} {p:g} %" for n, p in (("fly ash", s.fa_pct), ("GGBS", s.ggbs_pct),
                                       ("silica fume", s.sf_pct)) if p]
    kv("l) Mineral admixture(s)", ", ".join(scm) if scm else "None")
    kv("m) Method of placing", s.placing)

    H("A-2  TEST DATA FOR MATERIALS")
    kv("a) Cement used", s.cement_type)
    kv("b) Specific gravity of cement", f"{s.sg_cement:.2f}{tag('sg_cement')}")
    if s.fa_pct:
        kv("   Specific gravity of fly ash", f"{s.sg_fly:.2f}{tag('sg_fly')}")
    if s.ggbs_pct:
        kv("   Specific gravity of GGBS", f"{s.sg_ggbs:.2f}{tag('sg_ggbs')}")
    if s.sf_pct:
        kv("   Specific gravity of silica fume", f"{s.sg_sf:.2f}{tag('sg_sf')}")
    kv("c) Chemical admixture (rel. density)", f"{s.sg_admix:.2f}{tag('sg_admix')}")
    kv("d) Specific gravity: 1) coarse aggregate", f"{s.sg_ca:.2f}{tag('sg_ca')}")
    kv("                     2) fine aggregate", f"{s.sg_fa:.2f}{tag('sg_fa')}")
    kv("e) Water absorption: 1) coarse aggregate", f"{s.abs_ca:.2f} %{tag('abs_ca')}")
    kv("                     2) fine aggregate", f"{s.abs_fa:.2f} %{tag('abs_fa')}")
    kv("f) Free (surface) moisture: 1) coarse agg.",
       f"{r['free_ca']:+.2f} %" + ("" if s.mc_ca is not None else "  (SSD assumed)"))
    kv("                           2) fine agg.",
       f"{r['free_fa']:+.2f} %" + ("" if s.mc_fa is not None else "  (SSD assumed)"))
    kv("g) Sieve analysis: 1) coarse aggregate", f"Conforming to IS 383 Table 7 ({s.max_size} mm graded)")
    kv("                   2) fine aggregate", f"Conforming to IS 383 Zone {s.fa_zone}")

    H("A-3  TARGET STRENGTH FOR MIX PROPORTIONING")
    kv("Basis of s", s.qc_method)
    if s.qc_reason:
        L.append(f"    reason: {s.qc_reason}")
    kv("Standard deviation s used", f"{r['sd']:.2f} N/mm2 (X = {r['X']:.1f})")
    kv("f'ck = fck + 1.65 s", f"{s.grade} + 1.65 x {r['sd']:.1f} = {r['t1']:.2f} N/mm2")
    kv("f'ck = fck + X", f"{s.grade} + {r['X']:.1f} = {r['t2']:.2f} N/mm2")
    kv("Target mean strength f'ck (higher)", f"{r['target']:.2f} N/mm2")

    H("A-4  APPROXIMATE AIR CONTENT (Tbl 3)")
    kv("Entrapped air for " + f"{s.max_size} mm aggregate", f"{r['air']:.1f} % "
       + ("(deducted in volume balance)" if s.deduct_air else "(NOT deducted; nonstandard setting—verify before use)"))

    H("A-5  SELECTION OF WATER-CEMENT RATIO")
    kv("w/c from strength curve (Fig. 1, class " + f"{s.cement_class})", f"{r['wc_curve']:.3f}"
       + ("  (extrapolated)" if r["wc_curve"] < 0.30 else ""))
    kv("Max w/c for durability (IS 456 Tbl 5)", f"{r['max_wc']:.2f}")
    if r.get("hsc_floor"):
        kv("Practical HSC w/b floor (curve exhausted)", f"{r['hsc_floor']:.2f}")
    kv("Adopted w/cm  (lower of the two"
       + (", user-set" if s.wcm_user else "") + ")", f"{r['wcm_design']:.2f}"
       + ("   <- durability governs" if r["limit_governs"] and not s.wcm_user else
          "   <- HSC floor governs" if r.get("hsc_floor") and not s.wcm_user else
          "   <- strength governs" if not s.wcm_user else ""))

    H("A-6  SELECTION OF WATER CONTENT")
    kv(f"Water for 50 mm slump, {s.max_size} mm angular (Tbl 4)", f"{r['w_base']:.0f} kg/m3")
    if r["shape_red"]:
        kv("Less: aggregate shape correction", f"-{r['shape_red']:.0f} kg/m3 -> {r['w_shape']:.1f}")
    kv(f"Slump correction +3 % per 25 mm ({s.slump} mm)", f"+{r['slump_inc']*100:.1f} % -> {r['w_slump']:.1f} kg/m3")
    if s.admix_red:
        kv(f"Admixture water reduction ({s.admix_red:g} %)", f"{r['water']:.1f} kg/m3")
    kv("Estimated water content (SSD aggregate)", f"{r['water']:.1f} kg/m3")

    H("A-7  CALCULATION OF CEMENT / CEMENTITIOUS CONTENT")
    kv("Cementitious = water / (w/cm)", f"{r['water']:.1f} / {r['wcm_design']:.2f} = {r['binder0']:.1f} kg/m3")
    if r["boost"]:
        kv("Fly ash/GGBS used: total cementitious +10 %", f"{r['binder0']*1.1:.1f} kg/m3")
    kv("Adopted total cementitious (>= min "
       f"{r['min_c_adj']:.0f})", f"{r['binder']:.1f} kg/m3")
    kv("Effective w/cm", f"{r['wcm_eff']:.3f}")
    kv("  Cement (OPC/PPC/PSC)", f"{r['opc']:.1f} kg/m3")
    if s.fa_pct:
        kv("  Fly ash", f"{r['fly']:.1f} kg/m3")
    if s.ggbs_pct:
        kv("  GGBS", f"{r['ggbs']:.1f} kg/m3")
    if s.sf_pct:
        kv("  Silica fume", f"{r['sf']:.1f} kg/m3")

    H("A-8  PROPORTION OF VOLUME OF COARSE AND FINE AGGREGATE")
    kv(f"CA volume fraction, {s.max_size} mm, Zone {s.fa_zone} (Tbl 5)", f"{r['frac_base']:.3f}  (at w/c 0.50)")
    kv("w/cm correction (+0.01 per -0.05)", f"{r['frac_wc_corr']:+.3f}")
    if s.placing == "Pumped":
        kv("Pumping: reduce CA by 10 %", "x 0.90")
    kv("Adopted CA fraction / FA fraction", f"{r['frac']:.3f} / {1-r['frac']:.3f}")

    H("A-9  MIX CALCULATIONS  (per m3, absolute volume method)")
    kv("a) Volume of concrete", "1.000 m3")
    kv("b) Volume of cement", f"{r['v_c']:.4f} m3")
    if s.fa_pct:
        kv("   Volume of fly ash", f"{r['v_fly']:.4f} m3")
    if s.ggbs_pct:
        kv("   Volume of GGBS", f"{r['v_ggbs']:.4f} m3")
    if s.sf_pct:
        kv("   Volume of silica fume", f"{r['v_sf']:.4f} m3")
    kv("c) Volume of water", f"{r['v_w']:.4f} m3")
    kv("d) Volume of chemical admixture", f"{r['v_ad']:.4f} m3")
    if s.deduct_air:
        kv("   Volume of entrapped air", f"{r['v_air']:.4f} m3")
    kv("e) Volume of all-in aggregate", f"{r['v_agg']:.4f} m3")
    kv("f) Mass of coarse aggregate (SSD)", f"{r['ca']:.1f} kg/m3")
    kv("g) Mass of fine aggregate (SSD)", f"{r['fa']:.1f} kg/m3")

    H("A-10  MIX PROPORTIONS FOR TRIAL NUMBER 1  (kg/m3)")
    names = ["Cement", "Fly ash", "GGBS", "Silica fume", "Water", "Fine aggregate",
             "Coarse aggregate", "Chemical admixture"]
    def col(x, b=False):
        return [x["opc"], x["fly"], x["ggbs"], x["sf"], x["water_b" if b else "water"],
                x["fa_b" if b else "fa"], x["ca_b" if b else "ca"], x["adm_mass"]]
    show = [i for i, n in enumerate(names)
            if i in (0, 4, 5, 6) or (i == 1 and s.fa_pct) or (i == 2 and s.ggbs_pct)
            or (i == 3 and s.sf_pct) or (i == 7 and s.admix_dose)]
    has_mc = s.mc_fa is not None or s.mc_ca is not None
    head = f"  {'Material':<22}{'SSD basis':>12}" + (f"{'Field-corrected':>18}" if has_mc else "")
    L.append(head)
    a, b = col(r), col(r, True)
    for i in show:
        L.append(f"  {names[i]:<22}{a[i]:>12.1f}" + (f"{b[i]:>18.1f}" if has_mc else ""))
    L.append(f"  {'Density (fresh, calc.)':<22}{r['density']:>12.0f}")
    L.append("")
    tot = r["binder"]
    kv("Mix ratio by mass  cementitious : FA : CA",
       f"1 : {r['fa']/tot:.2f} : {r['ca']/tot:.2f}")
    kv("Water / cementitious (effective)", f"{r['wcm_eff']:.3f}")

    H("A-11  COMPANION TRIALS (water constant, w/cm varied; per m3, SSD)")
    L.append(f"  {'':<22}" + "".join(f"{'Trial ' + str(i+1):>12}" for i in range(len(trials))))
    rowspec = [("w/cm (design)", lambda x: f"{x['wcm_design']:.2f}"),
               ("Cement", lambda x: f"{x['opc']:.0f}"),
               ("Fly ash", lambda x: f"{x['fly']:.0f}"),
               ("GGBS", lambda x: f"{x['ggbs']:.0f}"),
               ("Silica fume", lambda x: f"{x['sf']:.0f}"),
               ("Water", lambda x: f"{x['water']:.0f}"),
               ("Fine aggregate", lambda x: f"{x['fa']:.0f}"),
               ("Coarse aggregate", lambda x: f"{x['ca']:.0f}"),
               ("Admixture", lambda x: f"{x['adm_mass']:.2f}")]
    for nm, f in rowspec:
        if nm == "Fly ash" and not s.fa_pct or nm == "GGBS" and not s.ggbs_pct \
                or nm == "Silica fume" and not s.sf_pct or nm == "Admixture" and not s.admix_dose:
            continue
        L.append(f"  {nm:<22}" + "".join(f"{f(x):>12}" for x in trials))

    if s.decisions:
        H("DESIGN DECISION LOG")
        for d_ in s.decisions:
            L.append(f"  - {d_}")
    if s.admix_decision:
        H("ADMIXTURE DECISION SUPPORT")
        L.append(f"  Assessment: {s.admix_decision}")
        for t_ in s.admix_types:
            L.append(f"  Candidate: {t_}")
    if s.chem_notes:
        H("CHEMICAL ATTACK / SPECIAL DURABILITY (separate from exposure class)")
        for n_ in s.chem_notes:
            L.append(f"  - {n_}")
    H("CODE-VERIFICATION STATUS")
    L.append("  The IS 10262:2019 and IS 456:2000 PDFs were not available to this tool; the items")
    L.append("  below are keyed from memory and MUST be checked before the mix is used:")
    for u_ in UNVERIFIED_ITEMS:
        L.append(f"  * {u_}")

    H("CHECKS, FLAGS AND ASSUMPTIONS")
    items = list(r["flags"])
    for i, t in enumerate(trials[1:], 2):
        if t["wcm_design"] > t["max_wc"] + 1e-9:
            items.append(f"Trial {i}: w/cm {t['wcm_design']:.2f} exceeds the durability limit "
                         f"{t['max_wc']:.2f}; use it for the strength curve only, not for the job.")
    items += s.assumptions
    if mats:
        items.append("Material properties marked '(assumed)' are typical values: replace with lab data.")
    items.append("w/c-strength curve is a digitised approximation of IS 10262 Fig. 1.")
    items.append("This is trial mix 1: cast >= 3 trials, test 7- and 28-day cubes, adjust w/cm, "
                 "water and admixture dose to hit the target strength AND slump retention.")
    if s.grade >= 60:
        items.append("High-strength concrete: check slump retention for the pumping/transport time, "
                     "autogenous shrinkage, heat of hydration, and curing plan.")
    for i, t in enumerate(items, 1):
        L.append(f"  {i}. {t}")
    L.append(bar)
    return "\n".join(L)


# ============================================================================
# 6.  INTERACTIVE FRONT-END  (numbered menus; free text only for the job description)
# ============================================================================

class InputClosed(Exception):
    """Raised when input ends (EOF) before the workflow is finished."""


class Console:
    """All prompts go through here so that menus behave identically and are testable."""

    def __init__(self, input_fn=input, print_fn=print):
        self._in = input_fn
        self.say = print_fn

    def raw(self, prompt):
        try:
            return self._in(prompt).strip()
        except EOFError:
            raise InputClosed("Input ended before the workflow was finished.") from None

    def text(self, prompt):
        return self.raw(prompt + ": ")

    def menu(self, title, options, zero=None, other=False, default=None):
        """options: list of (value, label).  Returns (kind, value):
        ('option', value) | ('other', None) | ('zero', None).
        Menu numbers are mapped to values here; callers never see the numbers."""
        n = len(options)
        top = n + 1 if other else n
        while True:
            lines = [f"\n{title}"]
            for i, (_, lbl) in enumerate(options, 1):
                lines.append(f"  {i}. {lbl}" + ("  [default]" if default == i else ""))
            if other:
                lines.append(f"  {n + 1}. Other / specify")
            if zero:
                lines.append(f"  0. {zero}")
            self.say("\n".join(lines))
            raw = self.raw("Select: ")
            if raw == "" and default is not None:
                return "option", options[default - 1][0]
            if raw.isdigit():
                k = int(raw)
                if 1 <= k <= n:
                    return "option", options[k - 1][0]
                if other and k == n + 1:
                    return "other", None
                if zero and k == 0:
                    return "zero", None
            lo = 0 if zero else 1
            self.say(f"  Invalid choice '{raw}'. Enter a number from {lo} to {top}.")

    def number(self, prompt, unit="", lo=None, hi=None, optional=False):
        while True:
            tail = f" ({unit})" if unit else ""
            raw = self.raw(f"{prompt}{tail}{' [Enter to skip]' if optional else ''}: ")
            if raw == "" and optional:
                return None
            try:
                v = float(raw)
                if not math.isfinite(v):
                    raise ValueError
            except ValueError:
                self.say(f"  '{raw}' is not a number. Enter a value{tail}.")
                continue
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                self.say(f"  Value must be between {lo} and {hi}{tail}.")
                continue
            return v


class Session:
    """Guided workflow: describe job -> interpret -> classify -> ask only what is missing -> design."""

    def __init__(self, con: Console):
        self.con = con
        self.s = Spec()
        self.a = Analysis()
        self.exposure = None

    # ---- helpers ----
    def note(self, topic, text):
        self.s.decisions.append(f"{topic}: {text}")

    # ---- step 1-2: job description ----
    def describe(self):
        self.con.say("\nCONCRETE MIX DESIGN  |  IS 10262:2019 + IS 456:2000")
        txt = self.con.text("Describe the concrete job in your own words (Enter to skip)")
        self.a = analyse_job(txt)
        if txt:
            self.con.say("\nHow I read your description:")
            for line in self.a.summary():
                self.con.say("  - " + line)
        self.s.assumptions += self.a.notes

    # ---- step 3: exposure ----
    def resolve_exposure(self):
        answers = {}
        while True:
            res = classify_exposure(self.a, answers)
            if res.pending is None:
                break
            q = res.pending
            labels = [(i, lbl) for i, (lbl, _, _) in enumerate(q.options, 1)]
            kind, val = self.con.menu(q.text, labels, zero=q.not_sure[0])
            answers[q.id] = 0 if kind == "zero" else val
        self.con.say(f"\nExposure classification (IS 456 Table 3): {res.cls}")
        for c in res.cands:
            self.con.say(f"  - {c.cls}: {c.why} ({c.conf} confidence)")
        for t in res.assumptions:
            self.con.say("  ! " + t)
        kind, val = self.con.menu("Accept this exposure class?",
                                  [("yes", "Yes, accept"), ("change", "No, choose another class")])
        cls = res.cls
        if val == "change":
            _, cls = self.con.menu("Select exposure class (IS 456 Table 3):", [(c, c) for c in EXPOSURES])
            self.note("Exposure", f"user overrode {res.cls} -> {cls}")
        self.s.exposure = cls
        self.exposure = res
        self.s.exposure_basis = [f"{c.cls}: {c.why} ({c.conf})" for c in res.cands]
        self.s.chem_notes = list(res.chem_notes)
        self.s.assumptions += res.assumptions
        self.note("Exposure", f"{cls}. Basis: " + "; ".join(self.s.exposure_basis) + " [IS 456 Table 3]")

    # ---- member, grade ----
    def resolve_member_grade(self):
        s, a, con = self.s, self.a, self.con
        m = a.member
        if m is None:
            _, m = con.menu("What type of concrete member?", [
                ("RCC", "Reinforced concrete (RCC)"), ("PCC", "Plain concrete (PCC)"),
                ("Prestressed", "Prestressed concrete")], zero="Not sure (treat as reinforced)")
            if m is None:
                m = "RCC"
                self.note("Member", "not stated: reinforced concrete assumed (stricter limits than plain)")
        s.member = "PCC" if m == "PCC" else "RCC"
        if m == "Prestressed":
            s.assumptions.append("Prestressed member: IS 456 Table 5 limits for RCC applied; the "
                                 "prestressing code (IS 1343) governs additional requirements.")
        row = IS456_T5[s.exposure]
        min_gr = row[2] if s.member == "RCC" else row[5]

        if len(a.grades) == 1:
            g = a.grades[0]
        else:
            if len(a.grades) > 1:
                opts = [(x, f"M{x}") for x in a.grades]
                _, g = con.menu("More than one grade was mentioned. Which one governs this mix?", opts)
            else:
                if a.hsc:
                    opts = [(x, f"M{x}") for x in (60, 65, 70, 75, 80)]
                    title = "Select the high-strength grade (IS 456 Table 2: M60 to M80):"
                else:
                    opts = [(x, f"M{x}") for x in (20, 25, 30, 35, 40, 45, 50)]
                    title = "Select the characteristic strength grade:"
                kind, g = con.menu(title, opts, zero=f"Not sure (use the IS 456 minimum for this exposure)",
                                   other=True)
                if kind == "other":
                    g = int(con.number("Grade, e.g. 42 for M42", "MPa", 10, 80))
                elif kind == "zero":
                    g = max(min_gr, 20)
                    self.note("Grade", f"not given: IS 456 Table 5 minimum M{g} for {s.exposure} "
                                       f"exposure adopted. This is a durability minimum, not a structural design grade.")
        if g not in VALID_GRADES:
            s.assumptions.append(f"M{g} is not a standard grade of IS 456 Table 2.")
        if g < min_gr:
            _, ch = con.menu(f"M{g} is below the IS 456 Table 5 minimum M{min_gr} for {s.exposure} "
                             f"exposure ({s.member}). What now?",
                             [("raise", f"Raise to M{min_gr}"), ("keep", f"Keep M{g} (non-compliant, flagged)")])
            if ch == "raise":
                self.note("Grade", f"raised from M{g} to M{min_gr} (IS 456 Table 5 minimum)")
                g = min_gr
        s.grade = int(g)

    # ---- cement ----
    def resolve_cement(self):
        s, a, con = self.s, self.a, self.con
        found = a.cement
        if len(found) == 1:
            s.cement_type, s.cement_class = found[0]
        else:
            opts = [(k, k) for k in ("OPC 43", "OPC 53", "PPC", "PSC")]
            if len(found) > 1:
                opts = [(c[0], c[0]) for c in found]
            kind, v = con.menu("Select cement type:", opts, zero="Not sure (choose for me)")
            if kind == "zero":
                v = "OPC 53" if s.grade >= 40 else "OPC 43"
                self.note("Cement", f"not specified: {v} chosen for M{s.grade}")
            s.cement_type, s.cement_class = v, CEMENT_CLASS[v]
        kind, v = con.menu("28-day strength class of the cement actually used (from the test certificate):",
                           [(43, "43 MPa"), (53, "53 MPa"), (33, "33 MPa")],
                           zero=f"Not sure (use nominal class, {s.cement_class} MPa)")
        if kind == "option":
            s.cement_class = v

    # ---- aggregate / workability ----
    def resolve_aggregate(self):
        s, a, con = self.s, self.a, self.con
        if a.max_size:
            s.max_size = a.max_size
        else:
            kind, v = con.menu("Maximum nominal size of coarse aggregate:",
                               [(10, "10 mm"), (20, "20 mm"), (40, "40 mm")],
                               zero="Not sure (infer from the member)")
            if kind == "zero":
                v = 40 if a.has("mass") else 20
                self.note("Aggregate size", f"not given: {v} mm inferred "
                                            f"({'mass concrete' if v == 40 else 'typical for RC sections'})")
            s.max_size = v
        shapes = [("crushed", "Angular (crushed)"), ("subangular", "Sub-angular"),
                  ("gravel+crushed", "Sub-rounded (gravel with crushed)"), ("rounded", "Rounded (gravel)")]
        kind, v = con.menu("Select coarse aggregate shape:", shapes, other=True, zero="Not sure")
        if kind == "other":
            s.shape_red_user = con.number("Water reduction for this aggregate vs angular", "kg/m3", 0, 40)
            s.agg_shape = "crushed"
            self.note("Aggregate shape", f"user-specified water reduction {s.shape_red_user:g} kg/m3")
        elif kind == "zero":
            s.agg_shape = "crushed"
            self.note("Aggregate shape", "not known: angular assumed (no water reduction, conservative)")
        else:
            s.agg_shape = v
            if v == "gravel+crushed":
                s.assumptions.append("'Sub-rounded' is mapped to 'gravel with crushed stone' of IS 10262 Table 4 notes: verify.")
        _, z = con.menu("Fine aggregate grading zone (IS 383):",
                        [("I", "Zone I"), ("II", "Zone II"), ("III", "Zone III"), ("IV", "Zone IV")],
                        zero="Not sure (assume Zone II)")
        s.fa_zone = z or "II"
        if z is None:
            s.assumed_materials.append("fa_zone")

    def resolve_workability(self):
        s, a, con = self.s, self.a, self.con
        s.placing = "Pumped" if a.has("pumpable") else "Tremie" if a.has("underwater") else "Normal"
        inferred = None
        if a.slump:
            s.slump = a.slump
            self.note("Workability", f"slump {a.slump} mm taken from the description")
            return
        if s.placing == "Pumped":
            inferred = (125, "pumped concrete (IS 456 Cl. 7.1 range 100-150 mm)")
        elif s.placing == "Tremie":
            inferred = (150, "tremie / underwater placing (IS 456 Cl. 7.1 about 100-150+ mm)")
        elif a.has("congested") or a.has("high_workability") or a.has("scc"):
            inferred = (100, "congested reinforcement / high workability (IS 456 Cl. 7.1: 75-100 mm and above)")
        elif a.has("mass"):
            inferred = (50, "mass concrete (IS 456 Cl. 7.1: 25-50 mm)")
        if inferred:
            kind, v = con.menu(f"Use slump {inferred[0]} mm for {inferred[1]}?",
                               [("yes", "Yes"), ("no", "No, choose another")])
            if v == "yes":
                s.slump = inferred[0]
                self.note("Workability", f"slump {inferred[0]} mm: {inferred[1]}")
                return
        opts = [(50, "Low: 25-50 mm (mass concrete, slip-form, blinding)"),
                (75, "Medium: 50-100 mm (beams, slabs, columns, hand-compacted)"),
                (100, "Medium-high: 75-100 mm (heavily reinforced sections)"),
                (125, "High: 100-150 mm (pumped, trench fill, tremie)"),
                (150, "Very high: 150 mm (piling, in-situ)")]
        kind, v = con.menu("Workability required at placement (slump):", opts, other=True,
                           zero="Not sure (use 75 mm)")
        if kind == "other":
            v = int(con.number("Slump", "mm", 25, 250))
        elif kind == "zero":
            v = 75
            self.note("Workability", "not known: 75 mm assumed for normal RC placing")
        s.slump = int(v)

    # ---- quality control ----
    def resolve_qc(self):
        s, a, con = self.s, self.a, self.con
        n, sd_h, rel = 0, None, True
        kind, v = con.menu("Do you have documented strength-test results for this grade "
                           "(same materials and production)?",
                           [("30+", "Yes, 30 or more results"), ("few", "Yes, fewer than 30 results"),
                            ("none", "No documented results")], zero="Not sure (treat as none)")
        if v == "30+":
            kind2, v2 = con.menu("How will you provide the standard deviation?",
                                 [("sd", "I know the computed standard deviation"),
                                  ("list", "I will enter the individual results")])
            if v2 == "sd":
                sd_h = con.number("Computed standard deviation", "N/mm2", 0.5, 15)
                n = int(con.number("Number of results", "", 30, 10000))
            else:
                vals = []
                self.con.say("Enter compressive strengths in N/mm2 one per line; blank line to finish.")
                while True:
                    r_ = con.raw("  result: ")
                    if r_ == "":
                        break
                    try:
                        vals.append(float(r_))
                    except ValueError:
                        self.con.say("  Not a number.")
                n = len(vals)
                if n >= 2:
                    sd_h = sd_from_results(vals)
                    self.con.say(f"  n = {n}, s = {sd_h:.2f} N/mm2")
            _, same = con.menu("Are those results from the same grade, materials and production setup?",
                               [("yes", "Yes"), ("no", "No / not sure")])
            rel = same == "yes"
        elif v == "few":
            n = int(con.number("Number of results available", "", 1, 29))
        checks = {}
        res = determine_sd(s.grade, n_results=n, sd_hist=sd_h, relevant=rel)
        if res.sd is None:
            self.con.say("\nSite control (confirm each condition that is really practised):")
            if a.has("poor_qc") or a.has("site_batch"):
                self.con.say("  (the description mentions weak/site-batched production: expect 'Fair')")
            for key, lbl in QC_CHECKS:
                kind, v = con.menu(lbl + "?", [("y", "Yes, always"), ("n", "No / sometimes")],
                                   zero="Not sure (counts as No)")
                checks[key] = (v == "y")
            res = determine_sd(s.grade, n_results=n, sd_hist=sd_h, relevant=rel, checks=checks)
        s.sd_user = res.sd
        s.control = res.control
        s.qc_method, s.qc_reason = res.method, res.reason
        s.assumptions += res.flags
        self.note("Quality control", f"{res.method}. {res.reason}")

    # ---- SCMs ----
    def resolve_scm(self):
        s, a, con = self.s, self.a, self.con
        rec = {m: (p, why) for m, p, why in scm_recommendations(s, a, s.exposure)}
        allowed = scm_allowed(s.cement_type)
        pcts = {"fly_ash": 0.0, "ggbs": 0.0, "silica_fume": 0.0}

        def pick_pct(m, preset=None):
            if preset is not None and 0 < preset <= SCM_LIMITS[m]:
                return preset
            if preset is not None:
                self.con.say(f"  {preset:g} % {SCM_LABEL[m]} is outside the usual limit "
                             f"(max {SCM_LIMITS[m]:g} %); please choose within it.")
            opts = [(p, f"{p:g} %") for p in SCM_MENU[m]]
            kind, p = con.menu(f"{SCM_LABEL[m]} replacement (% of total cementitious):", opts, other=True)
            if kind == "other":
                p = con.number(f"{SCM_LABEL[m]} replacement", "% of cementitious", 1, SCM_LIMITS[m])
            return p

        if "metakaolin" in a.scm_roles:
            self.note("SCM", "metakaolin mentioned but not modelled; ignored in the calculation")
        generic = a.scm_roles.get("scm_generic")
        if generic in ("required", "preferred", "available") and not any(
                a.scm_roles.get(m) for m in pcts):
            kind, m = con.menu("Which SCM will you use (you mentioned supplementary cementitious materials)?",
                               [(k, SCM_LABEL[k]) for k in allowed], zero="Not sure / none")
            if m:
                a.scm_roles[m] = "required" if generic == "required" else "available"
        for m in ("fly_ash", "ggbs", "silica_fume"):
            role = a.scm_roles.get(m)
            if role == "restricted":
                self.note("SCM", f"{SCM_LABEL[m]} excluded (user restriction)")
                continue
            if m not in allowed and role:
                self.note("SCM", f"{SCM_LABEL[m]} not added: {s.cement_type} already contains it "
                                 "(avoids double counting)")
                continue
            if role == "required":
                pcts[m] = pick_pct(m, a.scm_pct.get(m))
                self.note("SCM", f"{SCM_LABEL[m]} {pcts[m]:g} % (user requirement)")
            elif role in ("available", "preferred", "mentioned"):
                if m in rec:
                    p, why = rec[m]
                    kind, v = con.menu(f"{SCM_LABEL[m]} is {'available' if role == 'available' else 'of interest'}. "
                                       f"It could help here: {why}. Use it?",
                                       [("rec", f"Yes, at a typical {p:g} %"), ("pick", "Yes, choose the percentage"),
                                        ("no", "No")], zero="Not sure (do not use)")
                    if v == "rec":
                        pcts[m] = p
                    elif v == "pick":
                        pcts[m] = pick_pct(m, a.scm_pct.get(m))
                    self.note("SCM", f"{SCM_LABEL[m]} {pcts[m]:g} % ({role}; recommendation accepted)"
                              if pcts[m] else f"{SCM_LABEL[m]} not used ({role}, declined)")
                else:
                    self.note("SCM", f"{SCM_LABEL[m]} {role} but not indicated by the design: not used")
        # unrequested recommendations: ask once about availability, never assume
        todo = [m for m in rec if not a.scm_roles.get(m) and m in allowed]
        if todo:
            lines = "; ".join(f"{SCM_LABEL[m]} ({rec[m][1]})" for m in todo)
            kind, v = con.menu(f"SCMs that would suit this job: {lines}. Are any available and wanted?",
                               [(m, SCM_LABEL[m]) for m in todo] + [("all", "All of the above"),
                                                                    ("none", "None")],
                               zero="Not sure (do not use)")
            chosen = todo if v == "all" else [v] if v in todo else []
            for m in chosen:
                kind2, v2 = con.menu(f"{SCM_LABEL[m]} replacement (% of total cementitious):",
                                     [("rec", f"Typical {rec[m][0]:g} %"), ("pick", "Choose the percentage")])
                pcts[m] = rec[m][0] if v2 == "rec" else pick_pct(m)
                self.note("SCM", f"{SCM_LABEL[m]} {pcts[m]:g} % (recommended, user accepted availability)")
        s.fa_pct, s.ggbs_pct, s.sf_pct = pcts["fly_ash"], pcts["ggbs"], pcts["silica_fume"]
        if s.fa_pct + s.ggbs_pct + s.sf_pct == 0:
            self.note("SCM", "none used")

    # ---- admixture ----
    def resolve_admixture(self):
        s, a, con = self.s, self.a, self.con
        base = None
        try:
            base = compute(dataclasses.replace(s, admix_dose=0.0, admix_red=0.0, wcm_user=s.wcm_user))
        except ValueError:
            base = None
        d = assess_admixture(s, a, base)
        s.admix_decision = d.status
        s.admix_types = d.types
        self.con.say(f"\nAdmixture assessment: {d.status}")
        for r_ in d.reasons:
            self.con.say("  - because " + r_)
        if d.types:
            self.con.say("  Suggested: " + "; ".join(d.types))
        for v_ in d.verify:
            self.con.say("  ? verify: " + v_)
        if not d.types:
            self.note("Admixture", "not indicated by the design; none used")
            return
        kind, v = con.menu("Chemical admixture for this mix:",
                           [("use", f"Use: {d.types[0]}"), ("none", "No chemical admixture")],
                           zero="Not sure (follow the assessment)")
        if kind == "zero":
            v = "use" if d.status.startswith("Likely") else "none"
            self.note("Admixture", f"not sure: assessment followed ({'use' if v == 'use' else 'none'})")
        if v == "none":
            if d.status.startswith("Likely"):
                s.assumptions.append("Admixture declined although the assessment says it is likely required: "
                                     "check that the resulting mix is workable.")
            self.note("Admixture", "none used")
            return
        s.admix_name = d.types[0].split(" (")[0]
        is_wr = d.types[0].startswith("Water-reducing")
        if is_wr:
            kind, v = con.menu("Water reduction to credit in the mix design:",
                               [("enter", "Enter the supplier/trial-verified value"),
                                ("ind", "Use the indicative 20 % (IS 10262 guidance for superplasticizers; provisional)"),
                                ("none", "Take no credit (conservative)")])
            if v == "enter":
                s.admix_red = con.number("Verified water reduction", "%", 0, 40)
            elif v == "ind":
                s.admix_red = 20.0
                s.assumptions.append("Water reduction 20 % is an indicative value, not product data: confirm by trials.")
            else:
                s.admix_red = 0.0
        kind, v = con.menu("Admixture dose:", [("enter", "Enter the supplier-recommended dose"),
                                               ("trial", "Decide in trial mixes (not in quantities)")])
        if v == "enter":
            s.admix_dose = con.number("Dose", "% by mass of cementitious", 0.05, 5)
        else:
            s.assumptions.append("Admixture dose not fixed: its mass/volume is not in the quantities; set it in trials.")
        self.note("Admixture", f"{s.admix_name}; water credit {s.admix_red:g} %; dose "
                               f"{s.admix_dose:g} % of cementitious")

    # ---- material data ----
    def resolve_materials(self):
        s, con = self.s, self.con
        kind, v = con.menu("Material test data (specific gravity, absorption, moisture):",
                           [("lab", "I have lab values"), ("typ", "Use typical values (flagged as assumed)")])
        keys = ["sg_cement", "sg_ca", "sg_fa", "abs_ca", "abs_fa", "sg_admix"]
        if s.fa_pct:
            keys.append("sg_fly")
        if s.ggbs_pct:
            keys.append("sg_ggbs")
        if s.sf_pct:
            keys.append("sg_sf")
        if v == "typ":
            s.assumed_materials += keys
            return
        spec = {"sg_cement": ("Specific gravity of cement", "", 2.8, 3.3),
                "sg_ca": ("Specific gravity of coarse aggregate (SSD)", "", 2.3, 3.2),
                "sg_fa": ("Specific gravity of fine aggregate (SSD)", "", 2.3, 3.2),
                "abs_ca": ("Water absorption of coarse aggregate", "%", 0, 10),
                "abs_fa": ("Water absorption of fine aggregate", "%", 0, 10)}
        for k, (lbl, u, lo, hi) in spec.items():
            val = con.number(lbl, u, lo, hi, optional=True)
            if val is None:
                s.assumed_materials.append(k)
            else:
                setattr(s, k, val)
        s.assumed_materials += [k for k in keys if k not in spec]
        for attr, nm in (("mc_ca", "coarse"), ("mc_fa", "fine")):
            kind, v = con.menu(f"Moisture condition of the {nm} aggregate:",
                               [("ssd", "Saturated surface-dry (no correction)"),
                                ("enter", "Enter total moisture content")])
            if v == "enter":
                setattr(s, attr, con.number(f"Total moisture content of {nm} aggregate", "%", 0, 30))

    def run(self):
        self.describe()
        self.resolve_exposure()
        self.resolve_member_grade()
        self.resolve_cement()
        self.resolve_aggregate()
        self.resolve_workability()
        self.resolve_qc()
        self.resolve_scm()
        self.resolve_admixture()
        self.resolve_materials()
        normalise(self.s)
        return self.s


def run(s: Spec, con: Console | None = None):
    """Validate, compute (incl. companion trials), print and save the report."""
    say = con.say if con else print
    normalise(s)
    try:
        r1 = compute(s)
        trials = [r1]
        for d in (-0.03, +0.03):
            trials.append(compute(s, wcm_override=round(r1["wcm_design"] + d, 2),
                                  water_override=r1["water"]))
    except ValueError as e:
        say(f"\nCannot complete the design: {e}")
        say("Fix the item above (for example allow an admixture, lower the slump, or relax the w/cm) and run again.")
        return None
    rep = build_report(s, r1, trials)
    say("\n" + rep)
    stem = f"mix_design_M{s.grade}_{date.today():%Y%m%d}"
    with open(stem + ".txt", "w", encoding="utf-8") as f:
        f.write(rep)
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump(asdict(s), f, indent=2)
    say(f"\nSaved: {stem}.txt and {stem}.json (re-run with --spec {stem}.json)")
    return rep


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--spec" in argv:
        sp = Spec()
        with open(argv[argv.index("--spec") + 1]) as f:
            apply_values(sp, json.load(f))
        run(sp)
        return 0
    try:
        sess = Session(Console())
        spec = sess.run()
    except InputClosed as e:
        print(f"\n{e} Nothing was calculated.")
        return 1
    return 0 if run(spec) else 1


if __name__ == "__main__":
    sys.exit(main())
