"""
WrenPath backend - FastAPI app serving both the API and the static frontend.

Endpoints:
  POST /api/analyze  - old resume file + job posting text -> resume_data,
                        match_report, reflective_questions
  POST /api/generate - final resume_data + ats_mode -> .docx file

Run locally:
  uvicorn main:app --reload --port 8000
"""
import os

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from extractor import extract_text
from coach import analyze, CoachError
from resume_builder import build_resume_bytes

app = FastAPI(title="WrenPath API")

# Only needed if the frontend is ever served from a different origin than
# the API (e.g. local dev with a separate dev server). Same-origin
# deployment (frontend served by this same app) doesn't need this, but it's
# harmless to leave permissive for now since there's no auth/cookies here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB - old resumes are small text documents


@app.post("/api/analyze")
async def api_analyze(
    resume_file: UploadFile = File(...),
    job_posting: str = Form(...),
):
    content = await resume_file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (5 MB max).")

    try:
        resume_text = extract_text(resume_file.filename, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not resume_text.strip():
        raise HTTPException(status_code=400, detail="Could not extract any text from that file.")

    if not job_posting.strip():
        raise HTTPException(status_code=400, detail="Job posting text is required.")

    try:
        result = analyze(resume_text, job_posting)
    except CoachError as e:
        raise HTTPException(status_code=502, detail=str(e))

    return result


class GenerateRequest(BaseModel):
    resume_data: dict
    ats_mode: bool = False


@app.post("/api/generate")
async def api_generate(req: GenerateRequest):
    required_fields = ["name", "contact", "summary", "skills", "experience", "education"]
    missing = [f for f in required_fields if f not in req.resume_data]
    if missing:
        raise HTTPException(status_code=400, detail=f"resume_data missing fields: {missing}")

    try:
        docx_bytes = build_resume_bytes(req.resume_data, ats_mode=req.ats_mode)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build resume: {e}")

    filename = req.resume_data.get("name", "Resume").replace(" ", "_") + "_Resume.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok", "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY"))}


# Serve the static frontend last, so /api/* routes above take priority.
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
