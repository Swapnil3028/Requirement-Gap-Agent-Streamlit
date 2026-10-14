# Requirement Gap Agent

A local, rule-based Python CLI and Streamlit dashboard for reviewing
requirements and user stories. Findings, questions, recommendations, and draft
acceptance criteria are grounded in the uploaded text; the application does not
invent missing business rules. Its review approach follows evidence and
traceability principles associated with ISTQB, but it is not a formal
ISTQB-compliance assessment. It does not send requirements to an external
service or use an AI model.

The files in `prompts` document the intended review criteria; the current
implementation applies deterministic Python rules rather than sending those
prompts to a language model. Python 3.10 or later is required. The local RAG
index uses LangChain, ChromaDB, and Chroma's default local embedding function;
documents and vectors are persisted locally.

## QA prompt library

Reusable, copy-and-paste prompt templates are in `prompts`:

- `istqb_requirement_review_prompt.txt` — functional gaps, missing conditions,
  edge cases, and testability issues.
- `po_tester_acceptance_criteria_prompt.txt` — acceptance-criteria review and
  missing Gherkin scenarios.
- `requirements_comparison_prompt.txt` — duplicate rules, conflicts, thresholds,
  and dependencies.

Replace `{requirements}` or `{requirement}` with the text to review. These are
standalone prompt templates and are not automatically sent to an AI model by
the CLI.

## Setup

From the project directory, create and activate a virtual environment, then
install the dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

By default, the CLI reads `sample_docs\BRD.docx` and
`sample_docs\UserStories.xlsx`, and writes `reports\RequirementGapReport.md`:

```powershell
python app.py
```

During each run, the CLI updates the persistent `.rag\chroma` index and may
retrieve related sections. Only sections from the current input batch can be
used in or shown in that run's report; content left in the index from earlier
runs is excluded. Set `--rag-directory` to choose another index location or
`--top-k` to change the retrieval count. The local embedding model may be
downloaded on first use; requirement text is not sent to a hosted embedding API.

## Streamlit interface

Launch the browser-based review interface from the project directory:

```powershell
streamlit run streamlit_app.py
```

### Deploy to Streamlit Community Cloud

Push the application source to a GitHub repository, then create a Community
Cloud app using `streamlit_app.py` as the main file and `requirements.txt` for
dependencies. Do not publish business documents, generated reports, local
history databases, vector-index data, or Streamlit secrets.

The app disables persistent shared review history by default so one visitor's
document names and scores are not shown to other visitors. To enable persistent
history for a trusted local instance, set
`REQUIREMENT_GAP_ENABLE_SHARED_HISTORY=1` in its environment. Do not enable it
on a public multi-user deployment. Retrieved-document indexing is temporary
for each analysis and is removed when that analysis completes.

Upload one or more PDF or DOCX files and select **Analyze**. The
interface previews extracted text, analyzes the batch together for cross-document
conflicts, creates a temporary vector index to retrieve related sections,
displays the Markdown report, and provides it as a download. The temporary
index is removed after analysis. Retrieved evidence is restricted to the
current upload batch. The dashboard counts
ambiguities, missing acceptance criteria, missing business rules, validation
and success-criteria gaps, NFR gaps, and conflicts. Its quality score starts
at 100 and deducts 10 per ambiguity, 10 per missing acceptance-criteria set,
5 per missing business rule, 5 per NFR area not identified in the document
batch, and 15 per conflict (clamped to 0–100). Readiness is High Risk below 50,
Needs Clarification from 50 through 84, and Ready for Development from 85
through 100. The separate legacy risk indicator remains severity-weighted:
Critical for any Critical finding or a severity score below 40, High below 70,
Medium below 90, and Low otherwise. Empty and unsupported files are
reported in the interface instead of being reviewed as if they contained
requirements. Analysis runs in a background worker so the progress display
remains interactive. It shows the current processing action, elapsed time, and
an approximate time remaining once enough progress is available to estimate;
the estimate updates as stages complete and may vary with document size and
local indexing speed. **Cancel analysis** stops at the next safe stage boundary.
An active file or database operation is allowed to finish before cancellation,
and cancellation is disabled while the review is being finalized.
Once cancellation is requested, the Analyze button is re-enabled so a new
document can be selected and submitted; a replacement analysis waits for the
cancelled worker to release shared index resources.
When `REQUIREMENT_GAP_ENABLE_SHARED_HISTORY=1` is set, each completed run is
appended to `.history\reviews.sqlite3` with
the document names, local timestamp, score, ambiguity count, missing-AC count,
conflict count, risk level, and score-model version. Leadership metrics show
the Requirement Quality Index, Sprint Readiness band, ambiguity and missing-AC
trends, and a risk heat map. Historical scores from the former
severity-weighted model remain visible but are excluded from the new quality
score trend. Persistent history is disabled by default to avoid showing one
visitor's document names and scores to other visitors.
After a successful analysis, the uploaded files are cleared from the uploader;
the completed results remain visible until another analysis is started.

The RAG API can also be used directly:

```python
from pathlib import Path

from app import load_requirements
from rag_store import RequirementRAG

requirements = load_requirements(Path("sample_docs") / "BRD.docx")
rag = RequirementRAG(Path(".rag") / "chroma")
try:
    rag.index_requirements(requirements)
    related = rag.retrieve_for_requirements(requirements, k=3)
finally:
    rag.close()
```

Provide different input paths or an output path as needed:

```powershell
python app.py --brd path\to\BRD.docx --stories path\to\UserStories.xlsx --output reports\review.md
```

At least one input is required. The BRD may be a `.pdf`, `.docx`, `.txt`, or `.md` file;
user stories may be an `.xlsx`, `.txt`, or `.md` file. Text documents are analyzed
by paragraph rather than by every extracted line; wrapped PDF lines are joined,
and DOCX paragraph boundaries are preserved. Obvious headings, standalone words,
and scenario titles are excluded, while adjacent Given/When/Then steps are grouped
as one scenario. Excel files are read from every worksheet; a first row containing
recognized column headings is treated as a header, and columns such as
`Acceptance Criteria` are checked for missing values. Word table rows are reviewed
as separate requirement statements. The reusable `document_parser.extract_text(path)`
function extracts normalized text from PDF, DOCX, and TXT files; it cleans
whitespace, Unicode spacing, and word-break hyphenation while retaining paragraph
boundaries where the source format provides them.

The analysis pipeline runs in this order: parser, full-batch business-domain
detection, ambiguity review, acceptance
criteria review, business-rule review, NFR review, clarification question
generation, deduplication, the "Was This Already Answered?" review, senior QA
review and confidence scoring, then a Human Tone Rewrite stage produces
stakeholder-facing rationale, business impact, and actionable recommendations
before the report and dashboard are displayed. The rewrite is deterministic
presentation logic: it does not change the technical finding, severity,
confidence, or quality score. The agent checks each generated question
against every extracted requirement in the uploaded batch. When it finds
answering evidence, it removes that question from the outstanding list and
records the question, answer text, source location, and reason ("Answer already
available") in the report and dashboard. The deduplication pass merges the
strongest version of repeated issues before the answer check. The senior QA gate checks
source traceability and finding completeness, and rechecks document evidence
before any report/dashboard result is published. It cross-references all
extracted sections and uploaded documents, including
business rules, appendices, workflows, and acceptance criteria. Related
acceptance outcomes, measurable thresholds, observable results, or explicit
calculation/workflow details elsewhere in the upload can resolve a finding;
unrelated evidence is not used. Duplicate findings are merged with the
strongest severity retained, while their supporting source evidence is kept.
Potential contradictions remain visible for an owner to decide rather than
automatically choosing an authoritative statement.

Domain detection uses deterministic keyword evidence across the entire uploaded
batch and reports a likely domain with heuristic confidence, or marks the domain
undetermined when evidence is weak or tied. A domain-specific QA checklist is
shown in the report and dashboard as optional stakeholder review prompts. These
prompts are not findings, do not assert missing business rules, and do not
affect the quality score.

The generated report begins with an Executive Summary that states the overall
quality score and readiness status, then highlights up to five distinct
clarification areas. Areas are ordered by finding severity and confidence, with
the detailed evidence and recommendations available in the report that follows.
When no actionable areas are identified, the summary says so and still
recommends stakeholder validation.

The report checks Correct, Complete, Consistent, Unambiguous, Testable,
Traceable, Feasible, Necessary, Prioritized, Verifiable, and Modifiable.
Correctness and feasibility are explicitly marked as requiring human review
because deterministic text checks cannot validate business truth or delivery
constraints. NFR coverage checks Performance, Security, Authorization,
Authentication, Audit, Logging, Availability, Scalability, Accessibility,
Compliance, Localization, and Recovery. For each area, it distinguishes an
absent mention from a mention without an area-specific verification target or
control. It also flags requirements without acceptance criteria, business
rules, validation outcomes, or success criteria. These are heuristic review
prompts, not proof that a system requirement is absent or incorrect.

For an acceptance-criteria-free statement in a recognizable user-story form,
the report drafts positive, negative, validation, exception, and boundary
Gherkin-style scenario outlines using only the stated actor and action.
Unspecified conditions, outcomes, and handling remain explicit Product Owner
placeholders; they are not asserted as business behavior. Clarification
questions quote the source requirement and ask for only the missing decision
or test oracle.

The files currently in `sample_docs` are empty placeholders. Replace them with
valid documents before running the default command.

## Review behavior and limitations

The checks are explainable heuristics, not a substitute for stakeholder review.
Each finding quotes its source statement; conflict findings quote both
statements. Ambiguity and testability checks look for a built-in list of vague
terms and observable or measurable outcomes. To avoid duplicate explanations,
terms already reported as ambiguous are not also emitted as testability gaps.
Acceptance criteria are detected from explicit labels or Given/When/Then
wording. Conflict checks identify duplicate normalized rules, opposite
polarity for matching rules, and differing numeric thresholds when their
subject can be inferred from the wording. These deterministic checks may miss
semantic conflicts expressed with unrelated phrasing. Calculation/workflow
gaps and NFR coverage use keyword heuristics; they may miss equivalent
statements expressed with different terminology or flag a statement whose
context is not a formal rule. Review findings and severity with the business
owner. The score is a prioritization aid, not a formal ISTQB certification or
substitute for risk-based professional judgment.

Run the automated checks with:

```powershell
python -m unittest discover -s tests -v
```
