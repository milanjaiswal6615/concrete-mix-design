#!/usr/bin/env python3
"""Tests for concrete_mix_design.py  (run:  python test_concrete_mix_design.py)

These verify the SOFTWARE behaviour and the arithmetic of the procedure. They do NOT
verify the IS table values against the standards (the PDFs were not available).
"""
import dataclasses
import os
import tempfile
import unittest

import concrete_mix_design as C


def scripted(inputs):
    """Console fed from a list; raises InputClosed when the list runs out."""
    it = iter(inputs)
    out = []

    def fake_input(prompt=""):
        out.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    con = C.Console(input_fn=fake_input, print_fn=lambda *a: out.append(" ".join(map(str, a))))
    con.log = out
    return con


def base_spec(**kw):
    s = C.Spec(grade=30, cement_type="OPC 43", cement_class=43, max_size=20, exposure="Moderate",
               slump=100, admix_dose=0.8, admix_red=20.0)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class TestMenu(unittest.TestCase):
    OPTS = [("a", "Angular"), ("b", "Sub-angular"), ("c", "Rounded")]

    def test_number_maps_to_value(self):
        con = scripted(["2"])
        self.assertEqual(con.menu("Shape:", self.OPTS), ("option", "b"))

    def test_invalid_then_valid(self):
        con = scripted(["9", "x", "", "1"])
        self.assertEqual(con.menu("Shape:", self.OPTS), ("option", "a"))
        self.assertEqual(sum("Invalid choice" in l for l in con.log), 3)

    def test_zero_and_other(self):
        con = scripted(["0"])
        self.assertEqual(con.menu("S:", self.OPTS, zero="Not sure"), ("zero", None))
        con = scripted(["4"])
        self.assertEqual(con.menu("S:", self.OPTS, other=True), ("other", None))

    def test_zero_rejected_when_not_offered(self):
        con = scripted(["0", "3"])
        self.assertEqual(con.menu("S:", self.OPTS), ("option", "c"))

    def test_default_on_enter(self):
        con = scripted([""])
        self.assertEqual(con.menu("S:", self.OPTS, default=2), ("option", "b"))

    def test_eof_raises(self):
        with self.assertRaises(C.InputClosed):
            scripted([]).menu("S:", self.OPTS)

    def test_number_validation(self):
        con = scripted(["abc", "nan", "500", "12.5"])
        self.assertEqual(con.number("Value", "mm", 0, 100), 12.5)
        self.assertEqual(scripted([""]).number("V", optional=True), None)

    def test_menu_number_is_not_an_engineering_value(self):
        # selecting "2" in a grade menu must give the 2nd entry, not "2 MPa"
        con = scripted(["2"])
        _, g = con.menu("Grade:", [(60, "M60"), (65, "M65"), (70, "M70")])
        self.assertEqual(g, 65)


class TestExtraction(unittest.TestCase):
    def test_negation_does_not_trigger(self):
        a = C.analyse_job("The concrete is not exposed to seawater or chlorides.")
        self.assertFalse(a.has("seawater"))
        self.assertTrue(a.negated_only("seawater"))
        self.assertFalse(a.has("chloride"))

    def test_clause_split_keeps_second_condition(self):
        a = C.analyse_job("Not exposed to seawater, but exposed to chlorides.")
        self.assertFalse(a.has("seawater"))
        self.assertTrue(a.has("chloride"))

    def test_chemical_admixture_is_not_chemical_exposure(self):
        a = C.analyse_job("Use chemical admixtures for a building.")
        self.assertFalse(a.has("chemical_direct"))

    def test_whole_words_only(self):
        a = C.analyse_job("Supporting reports on the pier")          # 'port' inside other words
        self.assertFalse(a.has("marine"))

    def test_uncertainty(self):
        a = C.analyse_job("The structure may experience freezing.")
        self.assertTrue(a.has("freezing") and a.uncertain("freezing"))

    def test_scm_roles(self):
        self.assertEqual(C.analyse_job("Use fly ash in the mix.").scm_roles["fly_ash"], "required")
        self.assertEqual(C.analyse_job("Fly ash is available nearby.").scm_roles["fly_ash"], "available")
        self.assertEqual(C.analyse_job("We would prefer GGBS if possible.").scm_roles["ggbs"], "preferred")
        self.assertEqual(C.analyse_job("Do not use fly ash.").scm_roles["fly_ash"], "restricted")
        self.assertNotIn("fly_ash", C.analyse_job("A normal building.").scm_roles)

    def test_slag_cement_is_psc_not_ggbs_requirement(self):
        a = C.analyse_job("Use slag cement.")
        self.assertTrue(a.has("psc"))
        self.assertFalse(a.has("ggbs"))

    def test_numbers(self):
        a = C.analyse_job("M40 and M50 concrete, slump 100-150 mm, 20 mm aggregate, 30% fly ash")
        self.assertEqual(a.grades, [40, 50])
        self.assertEqual(a.slump, 150)
        self.assertEqual(a.max_size, 20)
        self.assertEqual(a.scm_pct["fly_ash"], 30.0)

    def test_slump_not_mistaken_for_aggregate_size(self):
        a = C.analyse_job("100 mm slump concrete")
        self.assertIsNone(a.max_size)
        self.assertEqual(a.slump, 100)

    def test_empty(self):
        a = C.analyse_job("")
        self.assertEqual(a.hits, {})
        self.assertTrue(a.summary())

    def test_prompt_examples_run(self):
        for t in ["We need high-strength concrete for a multistorey building.",
                  "Concrete will be used for a marine structure exposed to seawater.",
                  "We are constructing an underground water-retaining structure.",
                  "The structure is exposed to chlorides and a humid environment.",
                  "We require concrete for a wastewater treatment plant.",
                  "The concrete will be placed underwater.",
                  "We need economical concrete using supplementary cementitious materials."]:
            self.assertTrue(C.analyse_job(t).summary())


class TestExposure(unittest.TestCase):
    def test_interior_building_mild(self):
        r = C.classify_exposure(C.analyse_job("Interior residential building slabs, protected from weather"))
        self.assertEqual(r.cls, "Mild")

    def test_no_information_asks_general(self):
        r = C.classify_exposure(C.analyse_job(""))
        self.assertEqual(r.pending.id, "q_general")
        r = C.classify_exposure(C.analyse_job(""), {"q_general": 4})
        self.assertEqual(r.cls, "Very severe")

    def test_marine_is_not_one_class(self):
        a = C.analyse_job("Marine structure exposed to seawater")
        self.assertEqual(C.classify_exposure(a).pending.id, "q_sea")
        for ans, cls in ((1, "Extreme"), (2, "Very severe"), (3, "Severe"), (4, "Severe"), (5, "Moderate")):
            self.assertEqual(C.classify_exposure(a, {"q_sea": ans}).cls, cls)

    def test_coastal_building_not_assumed_seawater(self):
        a = C.analyse_job("A building near the coast")
        r = C.classify_exposure(a, {"q_sea": 4})
        self.assertEqual(r.cls, "Severe")

    def test_explicit_conditions(self):
        self.assertEqual(C.classify_exposure(C.analyse_job("pier in the tidal zone")).cls, "Extreme")
        self.assertEqual(C.classify_exposure(C.analyse_job("deck exposed to sea spray")).cls, "Very severe")
        self.assertEqual(C.classify_exposure(C.analyse_job("exposed to heavy rainfall")).cls, "Severe")
        self.assertEqual(C.classify_exposure(C.analyse_job("alternate wetting and drying")).cls, "Severe")
        self.assertEqual(C.classify_exposure(C.analyse_job("sulphate bearing soil foundation")).cls, "Very severe")

    def test_governing_class_is_most_severe(self):
        a = C.analyse_job("Tidal zone pier, also heavy rain and condensation")
        self.assertEqual(C.classify_exposure(a).cls, "Extreme")

    def test_underground_asks_about_soil(self):
        a = C.analyse_job("Foundation below ground level")
        self.assertEqual(C.classify_exposure(a).pending.id, "q_soil")
        self.assertEqual(C.classify_exposure(a, {"q_soil": 1}).cls, "Moderate")
        self.assertEqual(C.classify_exposure(a, {"q_soil": 0}).cls, "Very severe")     # not sure -> conservative

    def test_negated_aggressive_soil(self):
        a = C.analyse_job("Foundation below ground level in non-aggressive groundwater")
        self.assertEqual(C.classify_exposure(a).cls, "Moderate")

    def test_uncertain_condition_is_confirmed(self):
        a = C.analyse_job("The structure may experience freezing")
        self.assertEqual(C.classify_exposure(a).pending.id, "confirm_freezing")
        r = C.classify_exposure(a, {"confirm_freezing": 2})
        self.assertEqual(r.pending.id, "q_general")                 # denied -> nothing left -> general question

    def test_chemical_exposure_kept_separate(self):
        a = C.analyse_job("Wastewater treatment plant")
        r = C.classify_exposure(a, {"q_chem": 1})
        self.assertEqual(r.cls, "Extreme")
        self.assertTrue(r.chem_notes)

    def test_sulphate_notes_not_in_class(self):
        r = C.classify_exposure(C.analyse_job("sulphate bearing soil"))
        self.assertTrue(any("8.2.5" in n for n in r.chem_notes))


class TestQC(unittest.TestCase):
    def test_historical(self):
        r = C.determine_sd(40, n_results=35, sd_hist=3.2)
        self.assertEqual(r.sd, 3.2)

    def test_too_few_results_use_table(self):
        r = C.determine_sd(40, n_results=12, sd_hist=3.0, checks={k: True for k, _ in C.QC_CHECKS})
        self.assertIsNone(r.sd)
        self.assertEqual(r.control, "Good")

    def test_good_only_if_all_checks(self):
        ch = {k: True for k, _ in C.QC_CHECKS}
        self.assertEqual(C.determine_sd(30, checks=ch).control, "Good")
        ch["weigh"] = False
        self.assertEqual(C.determine_sd(30, checks=ch).control, "Fair")
        self.assertEqual(C.determine_sd(30, checks={}).control, "Fair")       # unanswered = not met

    def test_irrelevant_history_ignored(self):
        self.assertIsNone(C.determine_sd(40, n_results=40, sd_hist=3.0, relevant=False).sd)

    def test_sd_from_results(self):
        self.assertAlmostEqual(C.sd_from_results([30, 34, 32, 36]), 2.5820, places=3)
        with self.assertRaises(ValueError):
            C.sd_from_results([30])

    def test_fair_adds_one(self):
        good = C.compute(base_spec(control="Good"))
        fair = C.compute(base_spec(control="Fair"))
        self.assertAlmostEqual(fair["sd"] - good["sd"], 1.0)
        hist = C.compute(base_spec(control="Fair", sd_user=3.0))
        self.assertEqual(hist["sd"], 3.0)              # documented s is not increased


class TestSCM(unittest.TestCase):
    def test_no_double_counting(self):
        self.assertNotIn("fly_ash", C.scm_allowed("PPC"))
        self.assertNotIn("ggbs", C.scm_allowed("PSC"))
        with self.assertRaises(ValueError):
            C.compute(base_spec(cement_type="PPC", fa_pct=20))

    def test_percentage_sum_validated(self):
        with self.assertRaises(ValueError):
            C.compute(base_spec(fa_pct=60, ggbs_pct=40))

    def test_recommendations_are_options_not_assumed(self):
        s = base_spec(grade=70)
        recs = C.scm_recommendations(s, C.analyse_job("high strength marine pier"), "Very severe")
        self.assertIn("silica_fume", [r[0] for r in recs])
        self.assertEqual((s.fa_pct, s.ggbs_pct, s.sf_pct), (0, 0, 0))

    def test_scm_mass_balance(self):
        r = C.compute(base_spec(fa_pct=20, sf_pct=5))
        self.assertAlmostEqual(r["opc"] + r["fly"] + r["ggbs"] + r["sf"], r["binder"], places=6)
        self.assertAlmostEqual(r["fly"] / r["binder"], 0.20, places=6)


class TestAdmixture(unittest.TestCase):
    def test_not_assumed_for_plain_job(self):
        s = base_spec(slump=75, admix_dose=0, admix_red=0)
        d = C.assess_admixture(s, C.analyse_job("normal building"), C.compute(s))
        self.assertTrue(d.status.startswith("Not currently indicated"))
        self.assertEqual(d.types, [])

    def test_pumping_is_beneficial(self):
        s = base_spec(slump=125, placing="Pumped", admix_dose=0, admix_red=0)
        d = C.assess_admixture(s, C.analyse_job("pumped concrete"), C.compute(s))
        self.assertTrue(d.status.startswith("Potentially beneficial"))

    def test_high_strength_likely_required(self):
        s = base_spec(grade=70, exposure="Very severe", slump=125, admix_dose=0, admix_red=0)
        try:
            base = C.compute(s)
        except ValueError:
            base = None
        d = C.assess_admixture(s, C.analyse_job("high strength"), base)
        self.assertTrue(d.status.startswith("Likely required"))
        self.assertTrue(any("manufacturer" in v or "trials" in v for v in d.verify))

    def test_retarder_only_when_justified(self):
        s = base_spec(slump=125)
        self.assertFalse(any("Retarding" in t for t in
                             C.assess_admixture(s, C.analyse_job("pumped slab"), None).types))
        self.assertTrue(any("Retarding" in t for t in
                            C.assess_admixture(s, C.analyse_job("hot weather concreting, long transport"), None).types))


class TestCompute(unittest.TestCase):
    def test_slump_adjustment_above_and_below_50mm(self):
        low = C.compute(base_spec(slump=25, admix_red=0))
        base = C.compute(base_spec(slump=50, admix_red=0))
        high = C.compute(base_spec(slump=75, admix_red=0))
        self.assertAlmostEqual(low["w_slump"], base["w_shape"] * 0.97, places=6)
        self.assertAlmostEqual(base["w_slump"], base["w_shape"], places=6)
        self.assertAlmostEqual(high["w_slump"], high["w_shape"] * 1.03, places=6)

    def test_m30_regression(self):
        r = C.compute(base_spec())
        self.assertAlmostEqual(r["target"], 38.25, places=2)
        self.assertEqual(r["wcm_design"], 0.50)                       # limited by IS 456 Table 5
        self.assertAlmostEqual(r["water"], 157.7, delta=0.1)
        self.assertAlmostEqual(r["opc"], 315.5, delta=0.2)
        self.assertAlmostEqual(r["ca"], 1221.7, delta=1.0)

    def test_volumes_close_to_one(self):
        s = base_spec(fa_pct=20, sf_pct=5, ggbs_pct=0)
        r = C.compute(s)
        v = (r["opc"] / (s.sg_cement * 1000) + r["fly"] / (s.sg_fly * 1000) + r["sf"] / (s.sg_sf * 1000)
             + r["water"] / 1000 + r["adm_mass"] / (s.sg_admix * 1000)
             + r["ca"] / (s.sg_ca * 1000) + r["fa"] / (s.sg_fa * 1000) + r["v_air"])
        self.assertAlmostEqual(v, 1.0, places=6)

    def test_strength_vs_durability_governing(self):
        r = C.compute(base_spec(grade=50, cement_class=53, exposure="Mild"))
        self.assertFalse(r["limit_governs"])                          # strength governs
        r = C.compute(base_spec(grade=25, exposure="Extreme"))
        self.assertTrue(r["limit_governs"])                           # durability governs
        self.assertLessEqual(r["wcm_design"], r["max_wc"])

    def test_min_cement_enforced(self):
        r = C.compute(base_spec(grade=20, exposure="Extreme", slump=50, admix_dose=0, admix_red=0))
        self.assertGreaterEqual(r["binder"], r["min_c_adj"])

    def test_no_optional_materials(self):
        s = base_spec(admix_dose=0, admix_red=0)
        r = C.compute(s)
        self.assertEqual((r["fly"], r["ggbs"], r["sf"], r["adm_mass"]), (0, 0, 0, 0))
        self.assertTrue(C.build_report(s, r, [r, r, r]))

    def test_air_volume_deducted_by_default(self):
        s = base_spec(admix_dose=0, admix_red=0)
        r = C.compute(s)
        self.assertTrue(s.deduct_air)
        self.assertAlmostEqual(r["v_air"], r["air"] / 100, places=8)

    def test_moisture_correction_conserves_total_water(self):
        s = base_spec(mc_fa=3.0, mc_ca=1.0)
        r = C.compute(s)
        wet = r["water_b"] + r["fa_b"] + r["ca_b"]
        ssd = r["water"] + r["fa"] + r["ca"]
        self.assertAlmostEqual(wet, ssd, places=6)

    def test_hsc_floor_applied(self):
        r = C.compute(base_spec(grade=70, exposure="Very severe", slump=125, admix_red=25, admix_dose=1.0,
                                cement_class=53, sf_pct=8))
        self.assertIsNotNone(r["hsc_floor"])
        self.assertGreaterEqual(r["wcm_design"], 0.28 - 1e-9)

    def test_infeasible_mix_gives_clear_error(self):
        with self.assertRaises(ValueError) as cm:
            C.compute(base_spec(min_cement_user=2500, admix_dose=0, admix_red=0))
        self.assertIn("aggregate", str(cm.exception))

    def test_invalid_inputs(self):
        for bad in (dict(grade=5), dict(slump=0), dict(wcm_user=0), dict(sg_ca=0), dict(admix_red=90)):
            with self.assertRaises(ValueError):
                C.compute(base_spec(**bad))

    def test_report_has_annex_sections(self):
        s = base_spec()
        r = C.compute(s)
        rep = C.build_report(s, r, [r, r, r])
        for sec in ("A-1", "A-2", "A-3", "A-5", "A-6", "A-7", "A-8", "A-9", "A-10", "CODE-VERIFICATION STATUS"):
            self.assertIn(sec, rep)


class TestSession(unittest.TestCase):
    """End-to-end scripted runs through the real prompts."""

    def run_session(self, inputs):
        con = scripted(inputs)
        spec = C.Session(con).run()
        return spec, con

    def test_marine_pier_hsc(self):
        spec, con = self.run_session([
            "High strength M70 concrete for a pumped pier exposed to sea water spray, 20 mm aggregate. "
            "Silica fume is available.",
            "1",                      # accept exposure
            "2", "2",                 # OPC 53 ; certificate class 53
            "1", "2",                 # angular ; zone II
            "1",                      # accept slump 125
            "3", "1", "1", "1", "1", "1",   # no QC data ; five good-control answers
            "1",                      # silica fume at typical %
            "4",                      # no other SCM
            "1", "1", "25", "1", "1.0",     # use admixture ; enter water reduction 25 ; dose 1.0
            "2",                      # typical material data
        ])
        self.assertEqual(spec.grade, 70)
        self.assertEqual(spec.exposure, "Very severe")
        self.assertEqual(spec.sf_pct, 8.0)
        self.assertEqual((spec.fa_pct, spec.ggbs_pct), (0.0, 0.0))
        self.assertEqual(spec.admix_red, 25.0)
        self.assertEqual(spec.control, "Good")
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                rep = C.run(spec, con)
            finally:
                os.chdir(cwd)
        self.assertIsNotNone(rep)
        self.assertIn("Very severe", rep)

    def test_skip_description_uses_menus(self):
        spec, con = self.run_session([
            "",                       # skip description
            "2",                      # general question: Moderate
            "1",                      # accept
            "1",                      # RCC
            "3",                      # grade M30
            "1", "1",                 # OPC 43 ; class 43
            "2",                      # 20 mm
            "2", "2",                 # sub-angular ; zone II
            "2",                      # slump 75 menu entry
            "3", "1", "1", "1", "1", "1",
            "2",                      # materials typical  (no SCM/admixture questions for a plain job)
        ])
        self.assertEqual((spec.grade, spec.exposure, spec.agg_shape, spec.max_size), (30, "Moderate", "subangular", 20))

    def test_invalid_selection_does_not_crash(self):
        con = scripted(["", "9", "abc", "2", "1", "1", "3", "1", "1", "2", "2", "2", "2", "3", "1", "1", "1", "1", "1", "2"])
        spec = C.Session(con).run()
        self.assertTrue(any("Invalid choice" in l for l in con.log))
        self.assertEqual(spec.exposure, "Moderate")

    def test_not_sure_paths_are_conservative_and_logged(self):
        spec, con = self.run_session([
            "Foundation below ground level",
            "0",                      # soil aggressiveness: not sure -> Very severe
            "1",                      # accept
            "0",                      # member not sure -> RCC
            "0",                      # grade not sure -> minimum
            "0", "0",                 # cement not sure ; class not sure
            "0",                      # aggregate size not sure
            "0", "0",                 # shape not sure ; zone not sure
            "0",                      # slump not sure
            "0",                      # QC data not sure
            "0", "0", "0", "0", "0",  # all QC conditions not sure
            "2",                      # typical materials
        ])
        self.assertEqual(spec.exposure, "Very severe")
        self.assertEqual(spec.member, "RCC")
        self.assertEqual(spec.control, "Fair")
        self.assertEqual(spec.agg_shape, "crushed")
        self.assertTrue(any("Grade" in d for d in spec.decisions))

    def test_input_closed_midway(self):
        with self.assertRaises(C.InputClosed):
            C.Session(scripted(["M40 building"])).run()


if __name__ == "__main__":
    unittest.main(verbosity=1)