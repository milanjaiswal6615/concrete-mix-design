
import streamlit as st
import concrete_mix_design as C

st.set_page_config(
    page_title="Concrete Mix Design",
    page_icon="🏗️",
    layout="wide"
)

st.title("🏗️ Concrete Mix Design Assistant")
st.caption("IS 10262:2019 · IS 456:2000")
st.warning(
    "Educational and preliminary design tool only. "
    "Verify applicable IS provisions, actual material test data, "
    "and trial mixes before construction use."
)

with st.form("mix_design_form"):
    st.subheader("1. Design requirements")

    col1, col2 = st.columns(2)

    with col1:
        grade = st.selectbox(
            "Concrete grade",
            [15, 20, 25, 30, 35, 40, 45, 50, 55, 60],
            index=1,
            format_func=lambda x: f"M{x}"
        )
        member = st.selectbox("Member type", ["RCC", "PCC"])
        exposure = st.selectbox(
            "Exposure condition",
            C.EXPOSURES,
            index=1
        )
        slump = st.slider("Required slump (mm)", 10, 150, 75)

    with col2:
        cement_type = st.selectbox(
            "Cement type",
            ["OPC 33", "OPC 43", "OPC 53", "PPC", "PSC"],
            index=2
        )
        max_size = st.selectbox(
            "Maximum aggregate size (mm)", [10, 20, 40], index=1
        )
        fa_zone = st.selectbox(
            "Fine aggregate zone", ["I", "II", "III", "IV"], index=1
        )
        agg_shape = st.selectbox(
            "Aggregate shape",
            ["crushed", "subangular", "gravel+crushed", "rounded"]
        )

    st.subheader("2. Material properties")

    col3, col4 = st.columns(2)

    with col3:
        sg_cement = st.number_input(
            "Cement specific gravity", 2.5, 3.5, 3.15, 0.01
        )
        sg_fa = st.number_input(
            "Fine aggregate specific gravity", 2.0, 3.5, 2.65, 0.01
        )
        abs_fa = st.number_input(
            "Fine aggregate absorption (%)", 0.0, 15.0, 1.0, 0.1
        )

    with col4:
        sg_ca = st.number_input(
            "Coarse aggregate specific gravity", 2.0, 3.5, 2.70, 0.01
        )
        abs_ca = st.number_input(
            "Coarse aggregate absorption (%)", 0.0, 15.0, 0.5, 0.1
        )
        control = st.selectbox("Site control", ["Good", "Fair"])

    calculate = st.form_submit_button(
        "Calculate preliminary mix", type="primary", use_container_width=True
    )

if calculate:
    try:
        spec = C.Spec(
            grade=grade,
            cement_type=cement_type,
            cement_class=C.CEMENT_CLASS[cement_type],
            max_size=max_size,
            exposure=exposure,
            member=member,
            slump=slump,
            fa_zone=fa_zone,
            agg_shape=agg_shape,
            control=control,
            sg_cement=sg_cement,
            sg_fa=sg_fa,
            sg_ca=sg_ca,
            abs_fa=abs_fa,
            abs_ca=abs_ca,
        )

        with st.spinner("Calculating preliminary mix proportions..."):
            result = C.compute(spec)

        st.success("Calculation completed. Review all assumptions and warnings.")

        st.subheader("3. Mix design report")

        report = C.build_report(spec, result, [])
        st.text(report)

        st.download_button(
            "Download report (.txt)",
            data=report,
            file_name="concrete_mix_design_report.txt",
            mime="text/plain",
            use_container_width=True
        )

        if result.get("flags"):
            st.subheader("Important checks")
            for flag in result["flags"]:
                st.warning(flag)

    except Exception as error:
        st.error(f"Could not complete the calculation: {error}")
        st.info(
            "Check the inputs and the calculation engine. "
            "Do not use an unsuccessful calculation for construction."
        )

st.divider()
st.caption(
    "Preliminary estimates only. Actual aggregate grading, moisture, "
    "material test results, code compliance and trial-mix validation "
    "must be checked by a qualified engineer."
)
