import os
import pandas as pd
import streamlit as st
import datetime

st.set_page_config(page_title="AMR Diagnostic Console — Demo", layout="wide")

@st.cache_data
def load_kb():
    return pd.read_csv("demo_faults.csv")

kb = load_kb()
kb["code"] = kb["code"].str.upper()

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

st.title("AMR Diagnostic Console — Demo")
st.caption("Demo KB (invented data) for safe online testing")

if "stage" not in st.session_state:
    st.session_state.stage = "START"
    st.session_state.current = None
    st.session_state.attempts = 0

def show_record(rec):
    st.markdown("### Fault Overview")
    st.write(f"**Code:** {rec['code']}")
    st.write(f"**Subsystem:** {rec['subsystem']}")
    st.write(f"**Severity:** {rec['severity']}")
    st.write(f"**Title:** {rec['title']}")
    st.markdown("---")
    st.write("**Description:**")
    st.write(rec["description"])
    st.markdown("**Likely Causes:**")
    st.write(rec["likely_causes"])
    st.markdown("**Recovery Steps:**")
    steps = rec["recovery_steps"].split("  ")
    for s in steps:
        st.write("-", s)
    st.markdown("**Verification:**")
    st.write(rec["verification"])

if st.session_state.stage == "START":
    st.write("Do you have the AMR error code?")
    col1, col2 = st.columns(2)
    if col1.button("Yes"):
        st.session_state.stage = "ASK_CODE"
        st.rerun()
    if col2.button("No"):
        st.session_state.stage = "GUIDE"
        st.rerun()

elif st.session_state.stage == "ASK_CODE":
    code = st.text_input("Enter error code (e.g., N205)")
    if code:
        code = code.strip().upper()
        rec = kb[kb["code"] == code]
        if rec.empty:
            st.error("Code not found in demo KB.")
        else:
            st.session_state.current = rec.iloc[0]
            show_record(st.session_state.current)
            st.session_state.stage = "CONFIRM"
            st.rerun()

elif st.session_state.stage == "GUIDE":
    st.markdown("""
### How to find the error code
1. Open Alarms / Faults
2. Filter Active alarms
3. Open latest entry
4. Copy the Error Code
5. Return here and enter it
""")
    if st.button("I found it"):
        st.session_state.stage = "ASK_CODE"
        st.rerun()

elif st.session_state.stage == "CONFIRM":
    st.write("Did this resolve the issue?")
    col1, col2 = st.columns(2)
    if col1.button("Resolved"):
        st.success("Marked as resolved.")
        st.session_state.stage = "START"
        st.rerun()
    if col2.button("Not resolved"):
        if st.session_state.attempts == 0:
            st.session_state.attempts += 1
            st.warning("Try full power cycle and connector reseat, then test again.")
        else:
            st.error("Escalate to RCOE.")
            st.write(f"Timestamp: {now()}")
            st.session_state.stage = "START"
            st.session_state.attempts = 0
