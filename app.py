import re
from datetime import date
import streamlit as st
import pandas as pd
import json
import os
import concrete_mix_design as engine

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

st.set_page_config(page_title="Concrete Intelligence | Mix Design", page_icon="🏗️", layout="wide")

# ---------- Visual system ----------
st.markdown("""
<style>
:root { --ink:#14283f; --muted:#64748b; --line:#e2e8f0; --blue:#1d4ed8; }
.block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1180px;}
.hero {padding: 1.7rem 1.8rem; border:1px solid #dbe5f0; border-radius:18px;
       background:linear-gradient(120deg,#f8fbff 0%,#edf4fb 100%); margin-bottom:1.2rem;}
.eyebrow {font-size:.76rem; letter-spacing:.13em; font-weight:750; color:#1d4ed8; text-transform:uppercase;}
.hero h1 {font-size:2.2rem; line-height:1.15; color:#14283f; margin:.45rem 0;}
.hero p {color:#52657b; max-width:780px; margin-bottom:0;}
div[data-testid="stMetric"] {border:1px solid #e2e8f0; border-radius:12px; padding:14px; background:white;}
section[data-testid="stSidebar"] {background:#f8fafc;}
.small-muted {color:#64748b;font-size:.88rem;}
</style>
""", unsafe_allow_html=True)

if "parsed" not in st.session_state:
    st.session_state.parsed = None
if "draft" not in st.session_state:
    st.session_state.draft = None
if "result" not in st.session_state:
    st.session_state.result = None

st.markdown("""
<div class="hero">
  <div class="eyebrow">Concrete Intelligence · IS 10262:2019 / IS 456:2000</div>
  <h1>Describe the concrete. We'll work through the design.</h1>
  <p>Start with an ordinary sentence. The assistant extracts what it can, identifies uncertainty,
  asks for essential missing details, then sends confirmed inputs to the calculation engine.</p>
</div>
""", unsafe_allow_html=True)

st.info("Engineering prototype: output is a preliminary trial mix, not construction approval or a compliance certificate. The underlying engine contains documented approximations and unverified code-data items; review these before relying on results.")

def parse_statement(text):
    """Conservative local extraction: extract only values explicitly stated."""
    low = text.lower()
    data = {}
    evidence = {}
    # Grade
    matches = re.findall(r'(?<![a-z0-9])m\s?(\d{2})(?![a-z0-9])', low)
    if not matches:
        matches = re.findall(r'\bgrade\s+(?:of\s+)?(?:m\s?)?(\d{2})\b', low)
    valid = [int(x) for x in matches if int(x) in [10,15,20,25,30,35,40,45,50,55,60,65,70,75,80]]
    if valid:
        data["grade"] = valid[0]; evidence["grade"] = f"Found M{valid[0]} in your statement"
    # slump
    sm = re.search(r'slump[^0-9]{0,15}(\d{2,3})', low)
    if not sm:
        sm = re.search(r'(\d{2,3})\s*mm\s*slump', low)
    if sm and 10 <= int(sm.group(1)) <= 300:
        data["slump"] = int(sm.group(1)); evidence["slump"] = f"Found {sm.group(1)} mm slump"
    # aggregate size (avoid matching the slump number by removing slump phrase)
    low2 = re.sub(r'slump[^.]{0,25}', ' ', low)
    am = re.search(r'\b(10|20|40)\s*mm\s*(?:nominal\s*)?(?:size\s*)?(?:aggregate|agg)?\b', low2)
    if am:
        data["max_size"] = int(am.group(1)); evidence["max_size"] = f"Found {am.group(1)} mm aggregate"
    # member / application
    if any(k in low for k in ["column", "beam", "slab", "footing", "pile", "rcc", "reinforced concrete"]):
        data["member"] = "RCC"; evidence["member"] = "Reinforced concrete application detected"
    elif "plain concrete" in low or re.search(r'\bpcc\b', low):
        data["member"] = "PCC"; evidence["member"] = "Plain concrete detected"
    # Exposure only when explicitly named; environmental descriptions need confirmation.
    for term, val in [("very severe","Very severe"), ("extreme","Extreme"), ("severe","Severe"), ("moderate","Moderate"), ("mild","Mild")]:
        if re.search(r'\b'+re.escape(term)+r'\b', low):
            data["exposure"] = val; evidence["exposure"] = f"Explicitly mentions {val} exposure"
            break
    if "tidal" in low:
        data["exposure_hint"] = "Tidal exposure may indicate Extreme exposure; confirm actual exposure classification."
    elif "sea water spray" in low or "sea-water spray" in low:
        data["exposure_hint"] = "Sea-water spray may indicate Very severe exposure; confirm against IS 456 and project conditions."
    elif "coastal" in low or "marine" in low or "salt air" in low:
        data["exposure_hint"] = "Coastal/marine environment detected. Severity depends on actual exposure (e.g. sheltered, wet/dry cycles, spray, tidal zone); please confirm."
    # cement
    for term, val, cls in [("opc 33","OPC 33",33),("opc 43","OPC 43",43),("opc 53","OPC 53",53),("ppc","PPC",43),("psc","PSC",43)]:
        if term in low:
            data["cement_type"] = val; data["cement_class"] = cls
            evidence["cement_type"] = f"Found {val}"
            break
    # aggregate zone
    zm = re.search(r'\bzone\s*(i{1,3}|iv|[1-4])\b', low)
    if zm:
        z = zm.group(1).upper()
        data["fa_zone"] = {"1":"I","2":"II","3":"III","4":"IV"}.get(z,z)
        evidence["fa_zone"] = f"Found fine aggregate Zone {data['fa_zone']}"
    # placing
    if "pump" in low:
        data["placing"] = "Pumped"; evidence["placing"] = "Pumped placement detected"
    elif "tremie" in low:
        data["placing"] = "Tremie"; evidence["placing"] = "Tremie placement detected"
    # control
    if "fair site control" in low:
        data["control"] = "Fair"; evidence["control"] = "Fair site control stated"
    elif "good site control" in low:
        data["control"] = "Good"; evidence["control"] = "Good site control stated"
    return data, evidence

def interpret_with_ai(text):
    """Use a language model to interpret the problem; never calculate mix quantities here."""
    api_key = None
    try:
        api_key = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        api_key = None
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key or genai is None or types is None:
        data, evidence = parse_statement(text)
        a = engine.analyse_job(text)
        if not data.get("grade") and a.grades:
            data["grade"] = a.grades[0]
            evidence["grade"] = f"Detected grade M{a.grades[0]} by local analysis"
        if not data.get("slump") and a.slump:
            data["slump"] = a.slump
            evidence["slump"] = f"Detected {a.slump} mm slump by local analysis"
        if not data.get("max_size") and a.max_size:
            data["max_size"] = a.max_size
            evidence["max_size"] = f"Detected {a.max_size} mm aggregate by local analysis"
        return data, evidence, {
            "mode": "fallback",
            "summary": "AI is not connected yet. This is a local rule-based interpretation, not generative AI.",
            "facts": list(evidence.values()),
            "inferences": ([data["exposure_hint"]] if data.get("exposure_hint") else []),
            "questions": ["Add a Gemini API key in Streamlit Secrets to enable natural-language AI interpretation."],
            "confidence": "limited"
        }

    client = genai.Client(api_key=api_key)
    schema = {
        "type": "OBJECT",
        "properties": {
            "grade": {"type": "INTEGER", "nullable": True},
            "slump": {"type": "INTEGER", "nullable": True},
            "max_size": {"type": "INTEGER", "nullable": True},
            "member": {"type": "STRING", "nullable": True, "enum": ["RCC", "PCC"]},
            "exposure": {"type": "STRING", "nullable": True, "enum": ["Mild", "Moderate", "Severe", "Very severe", "Extreme"]},
            "cement_type": {"type": "STRING", "nullable": True, "enum": ["OPC 33", "OPC 43", "OPC 53", "PPC", "PSC"]},
            "fa_zone": {"type": "STRING", "nullable": True, "enum": ["I", "II", "III", "IV"]},
            "placing": {"type": "STRING", "nullable": True, "enum": ["Normal", "Pumped", "Tremie"]},
            "control": {"type": "STRING", "nullable": True, "enum": ["Good", "Fair"]},
            "facts": {"type": "ARRAY", "items": {"type": "STRING"}},
            "inferences": {"type": "ARRAY", "items": {"type": "STRING"}},
            "questions": {"type": "ARRAY", "items": {"type": "STRING"}},
            "summary": {"type": "STRING"},
            "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]}
        },
        "required": ["grade", "slump", "max_size", "member", "exposure", "cement_type", "fa_zone", "placing", "control", "facts", "inferences", "questions", "summary", "confidence"]
    }
    system_prompt = (
        "You are the natural-language intake assistant for an Indian concrete mix-design prototype. "
        "Interpret the complete user statement, not just keywords. Extract only values explicitly stated as facts. "
        "Identify plausible engineering inferences separately; never turn an inference into a confirmed input. "
        "High strength does not specify an exact M-grade. Good workability does not specify a numeric slump. "
        "Coastal alone does not uniquely determine exposure class: distinguish sheltered coastal air, general coastal exposure, "
        "sea-water spray/splash, full immersion and tidal zone, and ask a focused question if needed. "
        "Never invent cement type, aggregate grading, slump, site control or material test results. "
        "For a column/beam/slab/footing, RCC may be a reasonable inference but label it as an inference. "
        "Do not calculate mix proportions. Ask only essential follow-up questions, prioritizing inputs that affect code compliance. "
        "Return only valid JSON matching the provided response schema. Keep statements concise. This is not a substitute for a qualified engineer."
    )
    prompt = (
        system_prompt + "\\n\\nInterpret this user requirement:\\n" + text
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    out = json.loads(response.text)
    keys = ["grade", "slump", "max_size", "member", "exposure", "cement_type", "fa_zone", "placing", "control"]
    data = {k: out[k] for k in keys if out.get(k) is not None}
    if data.get("cement_type"):
        data["cement_class"] = engine.CEMENT_CLASS[data["cement_type"]]
    if out.get("exposure") is None and ("coastal" in text.lower() or "marine" in text.lower()):
        data["exposure_hint"] = "Coastal exposure needs clarification; the AI has not assigned a final exposure class."
    evidence = {f"fact_{i+1}": fact for i, fact in enumerate(out["facts"])}
    return data, evidence, {"mode": "ai", **out}


with st.sidebar:
    st.markdown("### Workspace")
    st.caption("Natural-language mix design")
    st.markdown("---")
    st.markdown("**Workflow**")
    st.write("1. Describe the job")
    st.write("2. Confirm extracted details")
    st.write("3. Resolve missing inputs")
    st.write("4. Review preliminary results")
    st.markdown("---")
    st.caption("Calculation engine: concrete_mix_design.py")

st.subheader("01 · Describe your requirement")
examples = [
    "M40 reinforced concrete column, severe exposure, 100 mm slump, 20 mm crushed aggregate, OPC 53, Zone II sand.",
    "High-strength concrete for a coastal building column with good workability.",
    "M30 concrete for an indoor slab, 75 mm slump, 20 mm aggregate.",
]
with st.expander("Try an example"):
    for i, ex in enumerate(examples):
        if st.button(ex, key=f"example_{i}", use_container_width=True):
            st.session_state.statement = ex
            st.rerun()

statement = st.text_area(
    "Concrete requirement",
    key="statement",
    height=125,
    placeholder="Example: I need high-strength concrete for a reinforced column in a coastal area, with good workability and 100 mm slump…",
    help="Write naturally. Include any details you already know; you do not need to know every technical parameter."
)

c_a, c_b = st.columns([1, 4])
with c_a:
    analyse_clicked = st.button("Understand requirement", type="primary", use_container_width=True)
with c_b:
    st.markdown("<p class='small-muted'>AI extracts context and separates facts from inferences. Confirm safety-critical details before calculating.</p>", unsafe_allow_html=True)

if analyse_clicked:
    if not statement.strip():
        st.warning("Describe the concrete requirement first.")
    else:
        try:
            parsed, evidence, interpretation = interpret_with_ai(statement)
            st.session_state.parsed = parsed
            st.session_state.evidence = evidence
            st.session_state.interpretation = interpretation
            st.session_state.draft = None
            st.session_state.result = None
        except Exception as exc:
            st.error(f"AI interpretation failed: {exc}")
            st.info("Check that GEMINI_API_KEY is set in Streamlit Secrets and that the key has API access. No mix calculation has been run.")

if st.session_state.parsed is not None:
    parsed = st.session_state.parsed
    evidence = st.session_state.get("evidence", {})
    st.markdown("---")
    st.subheader("02 · What we understood")
    st.caption("Values extracted from your sentence are prefilled. Confirm or change them below; inferred or missing values are never silently treated as facts.")
    interpretation = st.session_state.get("interpretation", {})
    if interpretation.get("mode") == "fallback":
        st.warning(interpretation.get("summary", "AI is not connected."))
    elif interpretation:
        st.success("AI interpretation completed · confidence: " + interpretation.get("confidence", "not rated"))
        st.write(interpretation.get("summary", ""))
    if evidence:
        for k, v in evidence.items():
            st.write(f"✓ **{k.replace('_',' ').title()}** — {v}")
    if interpretation.get("inferences"):
        st.markdown("#### Engineering inferences — not confirmed facts")
        for item in interpretation["inferences"]:
            st.write(f"- {item}")
    if interpretation.get("questions"):
        st.markdown("#### Clarifications the assistant thinks are needed")
        for item in interpretation["questions"]:
            st.write(f"- {item}")
    if parsed.get("exposure_hint"):
        st.warning(parsed["exposure_hint"])
    if not parsed:
        st.warning("I couldn't confidently extract technical values. Use the fields below to confirm the essential design inputs.")

    with st.form("confirm_inputs"):
        col1, col2, col3 = st.columns(3)
        grades = [20,25,30,35,40,45,50,55,60,65,70,75,80]
        with col1:
            grade = st.selectbox("Concrete grade", grades, index=grades.index(parsed.get("grade", 30)) if parsed.get("grade",30) in grades else 2)
            exposure_opts = engine.EXPOSURES
            exposure = st.selectbox("Exposure condition", exposure_opts, index=exposure_opts.index(parsed.get("exposure", "Moderate")) if parsed.get("exposure","Moderate") in exposure_opts else 1)
            member = st.selectbox("Concrete type", ["RCC","PCC"], index=["RCC","PCC"].index(parsed.get("member","RCC")))
        with col2:
            slump = st.number_input("Target slump (mm)", min_value=10, max_value=300, value=int(parsed.get("slump", 100)), step=5)
            max_size = st.selectbox("Nominal aggregate size (mm)", [10,20,40], index=[10,20,40].index(parsed.get("max_size",20)) if parsed.get("max_size",20) in [10,20,40] else 1)
            cement_type = st.selectbox("Cement type", list(engine.CEMENT_CLASS.keys()), index=list(engine.CEMENT_CLASS.keys()).index(parsed.get("cement_type","OPC 53")) if parsed.get("cement_type","OPC 53") in engine.CEMENT_CLASS else 2)
        with col3:
            zone = st.selectbox("Fine aggregate zone", ["I","II","III","IV"], index=["I","II","III","IV"].index(parsed.get("fa_zone","II")) if parsed.get("fa_zone","II") in ["I","II","III","IV"] else 1)
            placing = st.selectbox("Placement method", ["Normal","Pumped","Tremie"], index=["Normal","Pumped","Tremie"].index(parsed.get("placing","Normal")))
            control = st.selectbox("Site control", ["Good","Fair"], index=["Good","Fair"].index(parsed.get("control","Good")))
        with st.expander("Material data and admixture (optional; defaults are assumptions)"):
            a1,a2,a3 = st.columns(3)
            with a1:
                sg_cement = st.number_input("Cement specific gravity", 2.5, 3.5, 3.15, 0.01)
                sg_fa = st.number_input("Fine aggregate specific gravity (SSD)", 2.0, 3.5, 2.65, 0.01)
                sg_ca = st.number_input("Coarse aggregate specific gravity (SSD)", 2.0, 3.5, 2.70, 0.01)
            with a2:
                abs_fa = st.number_input("Fine aggregate absorption (%)", 0.0, 15.0, 1.0, 0.1)
                abs_ca = st.number_input("Coarse aggregate absorption (%)", 0.0, 15.0, 0.5, 0.1)
                fa_mc = st.number_input("Fine aggregate moisture (%) — 0 means SSD assumption", 0.0, 40.0, 0.0, 0.1)
            with a3:
                ca_mc = st.number_input("Coarse aggregate moisture (%) — 0 means SSD assumption", 0.0, 40.0, 0.0, 0.1)
                admix_dose = st.number_input("Superplasticizer dosage (% binder)", 0.0, 5.0, 1.0, 0.1)
                admix_red = st.number_input("Water reduction (%) from admixture", 0.0, 40.0, 20.0, 1.0)
        submitted = st.form_submit_button("Confirm inputs & calculate", type="primary", use_container_width=True)

    if submitted:
        try:
            spec = engine.Spec()
            spec.grade = int(grade)
            spec.exposure = exposure
            spec.member = member
            spec.slump = int(slump)
            spec.max_size = int(max_size)
            spec.cement_type = cement_type
            spec.cement_class = engine.CEMENT_CLASS[cement_type]
            spec.fa_zone = zone
            spec.placing = placing
            spec.control = control
            spec.sg_cement = float(sg_cement)
            spec.sg_fa = float(sg_fa)
            spec.sg_ca = float(sg_ca)
            spec.abs_fa = float(abs_fa)
            spec.abs_ca = float(abs_ca)
            spec.mc_fa = float(fa_mc) if fa_mc > 0 else None
            spec.mc_ca = float(ca_mc) if ca_mc > 0 else None
            spec.admix_dose = float(admix_dose)
            spec.admix_red = float(admix_red)
            # Material values above are user-entered or clearly labelled assumptions.
            spec.assumed_materials = ["sg_cement", "sg_fa", "sg_ca", "abs_fa", "abs_ca"]
            spec.assumptions = [
                "AI-assisted intake is not a code-compliance engine; confirm all inferred inputs.",
                "Default material properties and admixture performance must be replaced by supplier/lab data.",
                "The underlying calculation engine documents approximate strength/w-c curves and unverified code-data items."
            ]
            if parsed.get("exposure_hint"):
                spec.decisions.append(parsed["exposure_hint"])
            result = engine.compute(spec)
            trials = [result]
            for delta in (-0.03, 0.03):
                try:
                    trials.append(engine.compute(spec, wcm_override=round(result["wcm_design"] + delta, 2), water_override=result["water"]))
                except ValueError:
                    pass
            report = engine.build_report(spec, result, trials)
            st.session_state.result = {"spec": spec, "data": result, "report": report, "trials": trials}
        except Exception as exc:
            st.error(f"Could not calculate this mix: {exc}")
            st.caption("Check the inputs. This prototype does not cover every special concrete type or project-specific durability condition.")

if st.session_state.result:
    pack = st.session_state.result
    s, r = pack["spec"], pack["data"]
    st.markdown("---")
    st.subheader("03 · Preliminary mix design")
    st.caption(f"Trial-1 proportions · {date.today().strftime('%d %b %Y')} · per 1 m³")
    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Target mean strength", f"{r['target']:.1f} MPa")
    m2.metric("Water / binder ratio", f"{r['wcm_design']:.2f}")
    m3.metric("Total binder", f"{r['binder']:.0f} kg/m³")
    m4.metric("Mixing water", f"{r['water']:.0f} kg/m³")
    materials = [
        ("Cement / OPC", r["opc"]),
        ("Fly ash", r["fly"]),
        ("GGBS", r["ggbs"]),
        ("Silica fume", r["sf"]),
        ("Water (design)", r["water"]),
        ("Water to add after moisture correction", r["water_b"]),
        ("Fine aggregate (SSD)", r["fa"]),
        ("Coarse aggregate (SSD)", r["ca"]),
        ("Fine aggregate (batch)", r["fa_b"]),
        ("Coarse aggregate (batch)", r["ca_b"]),
        ("Admixture", r["adm_mass"]),
    ]
    df = pd.DataFrame([{"Material": name, "Quantity per m³": round(value, 2), "Unit": "kg"} for name, value in materials if abs(value) > 1e-8])
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.markdown("#### Mix by mass (based on binder)")
    binder = r["binder"] or 1
    st.write(f"**Binder : Fine aggregate : Coarse aggregate = 1 : {r['fa']/binder:.2f} : {r['ca']/binder:.2f}**")
    st.write(f"**Effective water/binder ratio = {r['wcm_eff']:.2f}**")
    if r["flags"]:
        st.markdown("#### Engineering flags to review")
        for flag in r["flags"]:
            st.warning(flag)
    st.markdown("#### Model limitations")
    for item in engine.UNVERIFIED_ITEMS:
        st.write(f"- {item}")
    st.warning("Do not use this output directly for construction. Verify the applicable code clauses and tables, test actual materials, prepare trial batches, test compressive strength/workability, and obtain approval from the responsible qualified engineer.")
    st.download_button("Download calculation report (.txt)", pack["report"], file_name=f"mix_design_M{s.grade}_{date.today():%Y%m%d}.txt", mime="text/plain", type="primary")
    st.download_button("Download input summary (.json)", __import__("json").dumps({k:v for k,v in vars(s).items()}, indent=2, default=str), file_name=f"mix_design_inputs_M{s.grade}.json", mime="application/json")
    with st.expander("Read full calculation report"):
        st.code(pack["report"], language="text")
