"""
server.py — FastAPI web server for the supervisor matching frontend.
Place this file at the ROOT of the matching_llm project (same level as scripts/).
Run with: uvicorn server:app --reload --port 8000
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

# Make scripts/ importable
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
import json
import tempfile

from model_client import RemoteChatModel
from profile_loader import (
    load_stage1_cards,
    load_student_profile,
    save_student_profile,
    load_supervisor_detail_by_name,
)
from stage1_agent import (
    opening_message as stage1_opening,
    continue_stage1,
    compress_student_profile as s1_compress,
    build_stage1_snapshots,
    build_stage1_system,
)
from stage2_agent import (
    opening_message as stage2_opening,
    continue_stage2,
)
from stage3_agent import draft_email
from text_utils import extract_keywords, read_file_to_text

# ── App setup ──────────────────────────────────────────────────────────────
app = FastAPI(title="Supervisor Matching")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static frontend files
STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Shared model instance (loaded once)
_model: Optional[RemoteChatModel] = None

def get_model() -> RemoteChatModel:
    global _model
    if _model is None:
        _model = RemoteChatModel()
    return _model

# In-memory session store (one session per server run is fine for a demo)
sessions: Dict[str, Dict[str, Any]] = {}

# ── Pydantic models ────────────────────────────────────────────────────────
class Stage1ChatRequest(BaseModel):
    session_id: str
    message: str
    recommend_now: bool = False

class Stage2StartRequest(BaseModel):
    session_id: str
    supervisor_name: str

class Stage2ChatRequest(BaseModel):
    session_id: str
    message: str

class Stage3Request(BaseModel):
    session_id: str

# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

# ── CV upload ──────────────────────────────────────────────────────────────
@app.post("/api/upload_cv")
async def upload_cv(file: UploadFile = File(...)):
    """Accept a CV file, extract text and keywords, store in student profile."""
    data = await file.read()
    suffix = Path(file.filename).suffix.lower()
    
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    
    try:
        text = read_file_to_text(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
    
    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from the uploaded file.")
    
    keywords = extract_keywords(text, top_k=14)
    profile = {
        "cv_text": text,
        "cv_keywords": keywords,
        "interest_text": "",
        "preference_text": "",
    }
    save_student_profile(profile)
    
    return {"ok": True, "keywords": keywords, "cv_excerpt": text[:300]}

# ── Stage 1: start ─────────────────────────────────────────────────────────
@app.post("/api/stage1/start")
def stage1_start():
    """Create a new session and get the opening message from the Stage 1 agent."""
    model = get_model()
    profile = load_student_profile()
    if not profile.get("cv_text"):
        raise HTTPException(status_code=400, detail="Please upload your CV first.")
    
    import uuid
    session_id = str(uuid.uuid4())
    
    opening = stage1_opening(model, profile)
    
    sessions[session_id] = {
        "student_profile": profile,
        "stage1_history": [],
        "stage1_result": {},
        "stage1_turns": 0,
        "selected_supervisor": None,
        "selected_supervisor_detail": None,
        "stage2_history": [],
    }
    
    return {
        "session_id": session_id,
        "message": opening,
    }

# ── Stage 1: chat ──────────────────────────────────────────────────────────
@app.post("/api/stage1/chat")
def stage1_chat(req: Stage1ChatRequest):
    sess = sessions.get(req.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    model = get_model()
    profile = sess["student_profile"]
    history = sess["stage1_history"]
    
    sess["stage1_turns"] += 1
    recommend_now = req.recommend_now or sess["stage1_turns"] >= 4
    
    result = continue_stage1(
        model, profile, history, req.message, recommend_now=recommend_now
    )
    
    if recommend_now:
        sess["stage1_result"] = result if isinstance(result, dict) else {}
        recs = sess["stage1_result"].get("recommendations", [])
        summary = sess["stage1_result"].get("student_summary", "")
        next_action = sess["stage1_result"].get("next_action", "")
        # Build a readable message for display
        lines = []
        if summary:
            lines.append(summary)
        lines.append("")
        lines.append("Based on our conversation, here are my recommendations:")
        for i, r in enumerate(recs, 1):
            lines.append(f"{i}. **{r.get('name','')}** — {r.get('reason','')}")
        if next_action:
            lines.append("")
            lines.append(next_action)
        message = "\n".join(lines)
        # Add to history so stage2 can read it
        history.append({"user": req.message, "assistant": message})
        return {
            "message": message,
            "stage1_done": True,
            "recommendations": recs,
            "student_summary": summary,
        }
    else:
        msg = result.get("message", "") if isinstance(result, dict) else str(result)
        history.append({"user": req.message, "assistant": msg})
        return {"message": msg, "stage1_done": False, "recommendations": []}

# ── Stage 2: start ─────────────────────────────────────────────────────────
@app.post("/api/stage2/start")
def stage2_start(req: Stage2StartRequest):
    sess = sessions.get(req.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    model = get_model()
    profile = sess["student_profile"]
    stage1_summary = sess["stage1_result"].get("student_summary", "")
    stage1_history = sess["stage1_history"]
    
    # Reset stage2 history for fresh explore
    sess["stage2_history"] = []
    sess["selected_supervisor"] = req.supervisor_name
    
    result = stage2_opening(
        model,
        req.supervisor_name,
        profile,
        stage1_summary,
        stage1_history,
    )
    
    sess["selected_supervisor_detail"] = result["detail"]
    
    return {
        "message": result["message"],
        "supervisor_name": req.supervisor_name,
    }

# ── Stage 2: chat ──────────────────────────────────────────────────────────
@app.post("/api/stage2/chat")
def stage2_chat(req: Stage2ChatRequest):
    sess = sessions.get(req.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    if not sess.get("selected_supervisor_detail"):
        raise HTTPException(status_code=400, detail="Stage 2 not started yet.")
    
    model = get_model()
    profile = sess["student_profile"]
    stage1_summary = sess["stage1_result"].get("student_summary", "")
    stage1_history = sess["stage1_history"]
    stage2_history = sess["stage2_history"]
    detail = sess["selected_supervisor_detail"]
    
    result = continue_stage2(
        model,
        detail,
        profile,
        stage1_summary,
        stage1_history,
        stage2_history,
        req.message,
    )
    
    msg = result["message"]
    stage2_history.append({"user": req.message, "assistant": msg})
    
    # Hint email generation after 3 turns
    show_email_hint = len(stage2_history) >= 3
    
    return {"message": msg, "show_email_hint": show_email_hint}

# ── Stage 3: generate email ────────────────────────────────────────────────
@app.post("/api/stage3/email")
def stage3_email(req: Stage3Request):
    sess = sessions.get(req.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    if not sess.get("selected_supervisor_detail"):
        raise HTTPException(status_code=400, detail="No supervisor selected yet.")
    
    model = get_model()
    profile = sess["student_profile"]
    stage1_summary = sess["stage1_result"].get("student_summary", "")
    stage2_history = sess["stage2_history"]
    detail = sess["selected_supervisor_detail"]
    
    email_text = draft_email(model, detail, profile, stage1_summary, stage2_history)
    
    return {
        "email": email_text,
        "supervisor_name": sess["selected_supervisor"],
    }

# ── Health check ───────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok"}
