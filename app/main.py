"""Streamlit entry point for the Concierge with Memory app."""

import sys
from pathlib import Path

# `streamlit run app/main.py` only puts app/ on the path; add the project root
# so `from app...` imports work locally and on Railway.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from pydantic import BaseModel

from app.llm import LLMError, call_llm

SYSTEM_PROMPT = (
    "You are a concierge for members of an executive community. "
    "Be concise, warm and practical."
)


class ConciergeReply(BaseModel):
    reply: str


st.set_page_config(page_title="Concierge", page_icon="🛎️")
st.title("🛎️ Concierge")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("How can I help you today?"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking..."):
                result = call_llm(SYSTEM_PROMPT, st.session_state.messages, ConciergeReply)
            st.markdown(result.reply)
            st.session_state.messages.append({"role": "assistant", "content": result.reply})
        except LLMError as exc:
            st.error(f"Could not get a reply: {exc}")
