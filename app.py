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
