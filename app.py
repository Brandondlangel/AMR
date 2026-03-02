import os, re, json, datetime
import pandas as pd
import streamlit as st

# Optional: OpenAI for Route C smart matching
OPENAI_AVAILABLE = True
try:
    from openai import OpenAI
except Exception:
    OPENAI_AVAILABLE = False

APP_TITLE = "AMR Diagnostic Console — Demo"
DEFAULT_MODEL = os.getenv("AMR_OPENAI_MODEL", "gpt-4.1-nano")  # economical default
KB_PATH = os.getenv("AMR_DEMO_KB_PATH", "demo_faults.csv")

st.set_page_config(page_title=APP_TITLE, layout="wide")

st.markdown("""
<style>
.block-container { max-width: 1050px; padding-top: 1.0rem; padding-bottom: 2rem; }
.small { color:#6b7280; font-size: 0.9rem; }
hr { border: none; height: 1px; background: #e5e7eb; margin: 12px 0; }
</style>
""", unsafe_allow_html=True)

@st.cache_data
def load_kb(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["code"] = df["code"].astype(str).str.strip()
    for c in ["subsystem","severity","title","description","likely_causes","recovery_steps","verification","notes"]:
        df[c] = df[c].fillna("").astype(str)
    return df

kb = load_kb(KB_PATH)
kb_by_code = {str(r["code"]).strip(): r for _, r in kb.iterrows()}

def now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()

def normalize_code(txt: str) -> str:
    return re.sub(r"\s+", "", (txt or "").strip())

def steps_to_bullets(steps: str) -> str:
    s = (steps or "").strip()
    if not s:
        return "- (No recovery steps available in KB)"
    parts = re.split(r"\s*\d+\)\s*", s)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) <= 1:
        return "\n".join([f"- {p}" for p in re.split(r"[;\n]+", s) if p.strip()]) or f"- {s}"
    return "\n".join([f"- {p}" for p in parts])

def record_to_markdown(r: dict) -> str:
    return f"""
**Fault Overview**
- **Code:** `{r['code']}`
- **Subsystem:** {r['subsystem']}
- **Severity:** {r['severity']}
- **Title:** {r['title']}

**Technical Description**
- {r['description']}

**Likely Causes**
- {r['likely_causes']}

**Recovery Procedure (Checklist)**
{steps_to_bullets(r['recovery_steps'])}

**Verification**
- {r['verification']}

**Notes**
- {r['notes'] if r['notes'] else '—'}
""".strip()

def local_match(symptoms: str, subsystem_hint: str = ""):
    text = (symptoms or "").lower()
    hint = (subsystem_hint or "").lower()

    def score_row(row):
        blob = " ".join([row["title"], row["description"], row["likely_causes"], row["recovery_steps"], row["subsystem"]]).lower()
        tokens = [t for t in re.findall(r"[a-z0-9]+", text) if len(t) >= 3]
        score = 0.0
        for t in set(tokens):
            if t in blob:
                score += 1.0
        if hint and hint in row["subsystem"].lower():
            score += 2.0
        denom = max(8.0, len(set(tokens)) + 4.0)
        return score / denom

    scored = []
    for _, r in kb.iterrows():
        scored.append((str(r["code"]), score_row(r)))
    scored.sort(key=lambda x: x[1], reverse=True)
    top3 = [{"code": c, "confidence": round(float(s), 3)} for c, s in scored[:3] if s > 0]
    if not top3:
        return {"best_code": "NOT_FOUND", "confidence": 0.0, "top_3": [], "reason": "No strong keyword overlap with the KB.", "next_questions": [
            "What exact message do you see on the HMI (copy/paste if possible)?",
            "Which subsystem is affected (Drive / Navigation / Battery / Lift / Safety / Comms)?",
            "What was the AMR doing right before the issue happened?"
        ]}
    best = top3[0]
    return {"best_code": best["code"], "confidence": best["confidence"], "top_3": top3, "reason": "Matched by symptom keyword overlap (offline fallback).", "next_questions": []}

def openai_smart_match(symptoms: str, subsystem_hint: str):
    if not OPENAI_AVAILABLE:
        return None
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    candidates = []
    for _, r in kb.iterrows():
        candidates.append({
            "code": str(r["code"]),
            "subsystem": r["subsystem"],
            "severity": r["severity"],
            "title": r["title"],
            "description": r["description"],
            "likely_causes": r["likely_causes"],
        })

    system = (
        "You are an AMR fault classifier. You must choose the best matching numeric code from the provided KB list only. "
        "Output JSON with keys: best_code, confidence (0.0-1.0), top_3 (list of {code,confidence}), reason, next_questions (list). "
        "If you cannot confidently match, output best_code=NOT_FOUND with confidence=0.0 and include 2-4 next_questions. "
        "Do not invent codes. Do not provide recovery steps."
    )

    user_payload = {"subsystem_hint": subsystem_hint, "symptoms": symptoms, "kb": candidates}
    client = OpenAI(api_key=api_key)

    try:
        r = client.responses.create(
            model=DEFAULT_MODEL,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload)}
            ],
            text={"format": {"type": "json_object"}},
        )
        return json.loads(r.output_text)
    except Exception:
        return None

def init_state():
    st.session_state.stage = "START"  # START | ASK_CODE | GUIDE_GET_CODE | ASK_SYMPTOMS | SHOW_MATCH | CONFIRM1 | CONFIRM2 | ESCALATE | POST
    st.session_state.route = None
    st.session_state.attempts = 0
    st.session_state.current_code = None
    st.session_state.current_record = None
    st.session_state.symptoms = ""
    st.session_state.events = []
    st.session_state.messages = []  # persistent chat transcript

if "stage" not in st.session_state:
    init_state()

def push(role: str, content: str):
    st.session_state.messages.append({"role": role, "content": content})

def render_chat():
    for m in st.session_state.messages:
        st.chat_message(m["role"]).markdown(m["content"])

def log_event(status: str, notes: str = ""):
    rec = st.session_state.current_record
    st.session_state.events.append({
        "timestamp": now(),
        "route": st.session_state.route or "",
        "input_code": st.session_state.current_code or "",
        "selected_code": (str(rec["code"]) if rec is not None else ""),
        "subsystem": (rec["subsystem"] if rec is not None else ""),
        "severity": (rec["severity"] if rec is not None else ""),
        "status": status,
        "attempts": st.session_state.attempts,
        "notes": notes or "",
        "symptoms": st.session_state.symptoms or "",
    })

def start_over():
    init_state()
    st.rerun()

st.title("AMR Diagnostic Console — Demo")
st.markdown('<div class="small">Chat workflow with an invented numeric-only knowledge base for safe testing (no company data).</div>', unsafe_allow_html=True)
st.markdown("<hr>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### Controls")
    st.button("Start Over", on_click=start_over)
    st.markdown("---")
    st.markdown("### Examples (numeric codes)")
    st.code("37\n112\n207\n401\n812")
    st.markdown("---")
    smart = "OpenAI" if (OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY","").strip()) else "Offline fallback"
    st.markdown(f"- Model: `{DEFAULT_MODEL}`")
    st.markdown(f"- Smart Match: `{smart}`")

# Always render transcript first so it never "disappears" on reruns
render_chat()

# Stage-driven UI
if st.session_state.stage == "START":
    if not st.session_state.messages:
        push("assistant", "Do you have the AMR **numeric** error code displayed on the console/HMI?")
        st.rerun()

    c1, c2 = st.columns(2)
    if c1.button("Yes, I have the code"):
        st.session_state.route = "A"
        st.session_state.stage = "ASK_CODE"
        push("assistant", "Enter the **numeric** error code (examples: `37`, `112`, `401`).")
        st.rerun()
    if c2.button("No, I don't have it"):
        st.session_state.route = "B"
        st.session_state.stage = "GUIDE_GET_CODE"
        push("assistant",
             "**Let’s locate the numeric error code on the console/HMI.**\n\n"
             "1) Open **Alarms / Faults** (or **Active Alarms**)\n"
             "2) Filter to **Active / Current**\n"
             "3) Open the most recent alarm entry\n"
             "4) Locate the **numeric Error Code** (example: `112`) and copy it\n"
             "5) Return here and paste the code\n\n"
             "If you still can’t find it, we can triage by symptoms.")
        st.rerun()

elif st.session_state.stage == "ASK_CODE":
    code_in = st.chat_input("Enter numeric error code")
    if code_in:
        push("user", code_in)
        code = normalize_code(code_in)

        if not code.isdigit():
            push("assistant", "That doesn’t look like a numeric code. Please enter numbers only (example: `112`).")
            st.rerun()

        rec = kb_by_code.get(code)
        if rec is None:
            push("assistant", "I couldn’t find an exact match for that code in the demo KB. Try `37`, `112`, `207`, `401`, or `812`.")
            st.rerun()

        st.session_state.current_code = code
        st.session_state.current_record = rec
        push("assistant", record_to_markdown(rec))
        push("assistant", "Did this resolve the issue after completing the recovery checklist?")
        st.session_state.stage = "CONFIRM1"
        st.rerun()

    c1, c2 = st.columns(2)
    if c1.button("Back"):
        st.session_state.stage = "START"
        push("assistant", "Do you have the AMR **numeric** error code displayed on the console/HMI?")
        st.rerun()
    if c2.button("Start Over"):
        start_over()

elif st.session_state.stage == "GUIDE_GET_CODE":
    c1, c2, c3 = st.columns(3)
    if c1.button("I found the code"):
        st.session_state.route = "B→A"
        st.session_state.stage = "ASK_CODE"
        push("assistant", "Great — enter the numeric code now (example: `112`).")
        st.rerun()
    if c2.button("Still can’t get the code"):
        st.session_state.route = "C"
        st.session_state.stage = "ASK_SYMPTOMS"
        push("assistant",
             "No problem — we can diagnose by symptoms.\n\n"
             "Please answer in one message (bullet points are fine):\n"
             "- What was the AMR trying to do? (navigate / charge / pick / lift / docking / etc.)\n"
             "- What do you see? (exact message text, LEDs, alarms, behavior)\n"
             "- Which subsystem seems affected? (Drive / Navigation / Battery / Lift / Safety / Comms / Not sure)\n"
             "- Anything else that might help (location, recent changes, environment)")
        st.rerun()
    if c3.button("Start Over"):
        start_over()

elif st.session_state.stage == "ASK_SYMPTOMS":
    msg = st.chat_input("Describe what is happening")
    if msg:
        push("user", msg)
        st.session_state.symptoms = msg
        subsystem_hint = ""
        m = re.search(r"(Drive|Navigation|Battery|Lift|Safety|Comms|Not sure)", msg, re.IGNORECASE)
        if m:
            subsystem_hint = m.group(1)

        match = openai_smart_match(msg, subsystem_hint)
        if not match:
            match = local_match(msg, subsystem_hint)

        st.session_state.match = match
        st.session_state.stage = "SHOW_MATCH"
        st.rerun()

    c1, c2 = st.columns(2)
    if c1.button("Back"):
        st.session_state.stage = "START"
        push("assistant", "Do you have the AMR **numeric** error code displayed on the console/HMI?")
        st.rerun()
    if c2.button("Start Over"):
        start_over()

elif st.session_state.stage == "SHOW_MATCH":
    match = st.session_state.get("match", {"best_code": "NOT_FOUND"})
    best_code = normalize_code(match.get("best_code", "NOT_FOUND"))
    conf = float(match.get("confidence", 0.0) or 0.0)

    if best_code == "NOT_FOUND":
        push("assistant", "I couldn't confidently match your symptoms to a single numeric code in the demo KB.")
        qs = match.get("next_questions", []) or []
        if qs:
            push("assistant", "To narrow it down, please answer:\n" + "\n".join([f"- {q}" for q in qs]))
        st.session_state.stage = "ASK_SYMPTOMS"
        st.rerun()

    top3 = match.get("top_3", []) or []
    push("assistant",
         f"**Best match:** `{best_code}` (confidence: `{conf:.2f}`)\n\n"
         + ("**Top candidates:**\n" + "\n".join([f"- `{t['code']}` (confidence `{float(t.get('confidence',0)):.2f}`)" for t in top3]) if top3 else "")
         + ("\n\n**Reason:** " + (match.get("reason", "") or "").strip())
    )

    rec = kb_by_code.get(best_code)
    if rec is None:
        push("assistant", "Internal demo KB error: matched code not found. Please try again.")
        st.session_state.stage = "ASK_SYMPTOMS"
        st.rerun()

    st.session_state.current_code = best_code
    st.session_state.current_record = rec
    push("assistant", record_to_markdown(rec))
    push("assistant", "Did this resolve the issue after completing the recovery checklist?")
    st.session_state.stage = "CONFIRM1"
    st.rerun()

elif st.session_state.stage == "CONFIRM1":
    c1, c2, c3 = st.columns(3)
    if c1.button("Yes — Resolved"):
        log_event("RESOLVED")
        push("assistant", "✅ Recorded as **RESOLVED**.")
        st.session_state.stage = "POST"
        st.rerun()
    if c2.button("No — Not resolved"):
        st.session_state.attempts = 1
        push("assistant",
             "**Understood. Let’s try one alternative recovery action before escalation:**\n\n"
             "- Re-seat any related connectors (power/comm/sensor) if accessible\n"
             "- Perform a full power cycle (OFF 60 seconds, then ON)\n"
             "- Retry the action at reduced speed / in a clear area\n\n"
             "After that, did it resolve the issue?")
        st.session_state.stage = "CONFIRM2"
        st.rerun()
    if c3.button("Start Over"):
        start_over()

elif st.session_state.stage == "CONFIRM2":
    c1, c2, c3 = st.columns(3)
    if c1.button("Yes — Resolved after alternative"):
        log_event("RESOLVED_AFTER_ALT")
        push("assistant", "✅ Recorded as **RESOLVED after alternative recovery**.")
        st.session_state.stage = "POST"
        st.rerun()
    if c2.button("No — Still not resolved"):
        st.session_state.attempts = 2
        rec = st.session_state.current_record
        code = str(rec["code"]) if rec is not None else st.session_state.current_code
        push("assistant",
             "⚠️ **Recommendation: Escalate to RCOE.**\n\n"
             "Below is a ready-to-send escalation summary (copy/paste):\n\n"
             f"**Escalation Summary**\n"
             f"- Timestamp: {now()}\n"
             f"- Code (candidate): {code}\n"
             f"- Subsystem: {rec['subsystem'] if rec is not None else ''}\n"
             f"- Title: {rec['title'] if rec is not None else ''}\n"
             f"- What user reported: {st.session_state.symptoms or '(code-based)'}\n"
             f"- Actions attempted: Primary checklist + alternative power-cycle/re-seat\n"
             f"- Current status: Not resolved\n")
        st.session_state.stage = "ESCALATE"
        st.rerun()
    if c3.button("Start Over"):
        start_over()

elif st.session_state.stage == "ESCALATE":
    notes = st.text_input("Optional notes for the log (location, dock ID, screenshots reference)")
    c1, c2, c3 = st.columns(3)
    if c1.button("Mark Escalated"):
        log_event("ESCALATED_TO_RCOE", notes=notes)
        push("assistant", "✅ Recorded as **ESCALATED to RCOE**.")
        st.session_state.stage = "POST"
        st.rerun()
    if c2.button("Back"):
        st.session_state.stage = "CONFIRM2"
        st.rerun()
    if c3.button("Start Over"):
        start_over()

if st.session_state.get("stage") == "POST":
    st.markdown("<hr>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    if c1.button("Start New Triage"):
        start_over()
    if c2.button("View Session Log"):
        df = pd.DataFrame(st.session_state.events) if st.session_state.events else pd.DataFrame()
        st.dataframe(df, use_container_width=True)
        if not df.empty:
            st.download_button(
                "Download Session Log (CSV)",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name="amr_demo_session_log.csv",
                mime="text/csv",
            )
