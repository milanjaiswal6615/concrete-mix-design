CONCRETE INTELLIGENCE — AI INTAKE UPGRADE

Files:
- app.py: Streamlit UI with OpenAI-powered natural-language interpretation.
- concrete_mix_design.py: existing deterministic calculation engine; keep in the same folder.
- requirements.txt: includes the OpenAI SDK.

SETUP
1. Upload/commit app.py and requirements.txt to the same GitHub repo as concrete_mix_design.py.
2. In Streamlit Community Cloud, open app -> Settings -> Secrets.
3. Add this secret (use your own API key; never commit it to GitHub):
   OPENAI_API_KEY = "your_api_key_here"
4. Save and reboot/redeploy the app.
5. The app uses the OpenAI API model gpt-4.1-mini for intake interpretation. API usage may incur charges.

BEHAVIOUR
- AI interprets natural language and separates explicit facts from inferences.
- It does not calculate concrete quantities; the existing deterministic engine does that.
- If no API key is configured, a local rule-based fallback is shown clearly and is not described as AI.
- AI extraction can still be wrong. Confirm safety-critical inputs and all code-dependent classifications.

ENGINEERING LIMITATIONS
The calculation engine itself documents unverified code data and approximate strength/water-cement curves. Treat outputs as preliminary trial-mix estimates only. Check the official IS codes, actual material test results, laboratory trial batches and qualified engineer approval before construction use.
