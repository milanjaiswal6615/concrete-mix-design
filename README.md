Concrete Mix Design Tool
A Python-based prototype for concrete mix proportioning, intended to
support workflows informed by IS 10262:2019 and IS 456:2000.
Important engineering notice: This is a prototype, not a certified
engineering design tool. Do not use its output for construction
without independent review by a qualified concrete/materials engineer
and verification against the applicable standards, project
specifications, and laboratory test results.

Current features
- Command-line workflow for entering concrete job and material
  details.
- Exposure-condition and concrete-member prompts.
- Mix proportioning report with estimated quantities per cubic metre.
- Trial-mix comparisons and decision/assumption notes.
- JSON output intended to support rerunning a saved specification.
- Automated unit tests for selected calculation and input-handling
  behavior.
Requirements
- Python 3.10 or later is recommended.
- No third-party package is required if the project uses only the
  Python standard library. If your copy includes a requirements.txt,
  install the listed packages as well.
Run locally
1. Put the Python application and test file in the same folder.
2. Open a terminal in that folder.
3. Run the tests:
   python -m unittest test_concrete_mix_design -v
4. Start the command-line program:
   python concrete_mix_design.py
5. Follow the prompts and review the generated report and JSON file.
On Windows, if python is not recognized, try py instead:
py -m unittest test_concrete_mix_design -v
py concrete_mix_design.py
Outputs
The program may generate a text mix-design report and a JSON
specification file in the working directory. The exact filenames are
shown by the program when it finishes.

Review every assumption in the report before relying on its results.
Values based on typical material properties should be replaced with
laboratory measurements when available.
Known limitations and verification required
The current prototype has limitations that must be addressed before
treating it as code-compliant or suitable for construction:
- The water-cementitious-ratio/strength relationship uses an
  approximate curve and must be checked against calibrated data and
  the applicable standard.
- Typical specific gravity, absorption, and moisture values may be
  assumed when laboratory values are not supplied.
- Sulphate-attack and chloride-limit checks are not fully implemented.
- Air-content handling and absolute-volume calculations must be
  verified against the applicable IS 10262 provisions and the intended
  calculation method.
- Supplementary cementitious material and admixture recommendations
  must be validated against the standards, manufacturer data, material
  compatibility, and trial mixes.
- Trial mixes, fresh-concrete workability checks, and 7-day/28-day
  strength testing are needed to establish performance. A generated
  report alone does not establish that the concrete will meet the
  specified strength or durability.
Do not describe the application as fully IS-compliant until each
relevant clause, table, and calculation has been verified and
documented.
Planned development
The intended next phase is a web interface connected to the existing
Python calculation engine, followed by a Retrieval-Augmented Generation
(RAG) component that retrieves relevant information from approved
standards and curated mix-design records.

The RAG component should provide source-grounded explanations and
citations. It must not invent material quantities or replace
deterministic calculations performed by the calculation engine.
Suggested repository structure
concrete-mix-design/
├── concrete_mix_design.py
├── test_concrete_mix_design.py
├── README.md
├── requirements.txt        # only if dependencies are needed
└── data/                   # curated records and approved reference material, when added
Adjust the filenames above to match the files actually included in your
repository.
Safety and responsibility
This project is for learning, prototyping, and engineering support. The
responsible engineer must verify inputs, calculations, applicable code
provisions, laboratory data, and trial-mix results before approving any
mix for use.
