CONCRETE INTELLIGENCE — GEMINI AI INTAKE UPGRADE

Files:
- app.py: Streamlit UI with Gemini-powered natural-language interpretation.
- concrete_mix_design.py: calculation engine; keep in the same folder.
- requirements.txt: Streamlit, pandas, and Google GenAI SDK.

SETUP
1. Upload/commit app.py and requirements.txt to the same GitHub repo as concrete_mix_design.py.
2. In Streamlit Community Cloud, open app -> Settings -> Secrets.
3. Add this secret (use a NEW key if you previously pasted your key in chat; revoke the exposed key):
   GEMINI_API_KEY = "your_new_key_here"
4. Save and reboot/redeploy the app.
5. The app uses Gemini model gemini-2.5-flash for intake interpretation. Free-tier access and limits depend on Google's current terms and region.

BEHAVIOUR
- AI interprets natural language and separates explicit facts from inferences.
- It does not calculate mix quantities; the deterministic calculation engine does that.
- If no Gemini key is configured, a local rule-based fallback is shown clearly and is not described as generative AI.
- AI extraction can still be wrong. Confirm safety-critical inputs and code-dependent classifications.

ENGINEERING LIMITATIONS
The calculation engine itself documents unverified code data and approximate strength/water-cement curves. Treat outputs as preliminary trial-mix estimates only. Check the official IS codes, actual material test results, laboratory trial batches and qualified engineer approval before construction use.
