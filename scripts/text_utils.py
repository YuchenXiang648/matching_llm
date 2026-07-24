from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
import re
import tempfile
from collections import Counter
from typing import List

try:
    import docx2txt
except Exception:
    docx2txt = None

try:
    from pdfminer.high_level import extract_text as pdf_extract_text
except Exception:
    pdf_extract_text = None

EN_STOP = {
    "the","a","an","and","or","of","to","for","in","on","at","by","with","from",
    "is","are","was","were","be","been","being","this","that","these","those",
    "as","it","its","if","then","than","but","so","such","via","we","our","you",
    "they","their","he","she","his","her","i","me","my","mine","your","yours",
    "about","into","out","over","under","between","within","without","per",
    "can","could","should","would","may","might","must","will","shall","not",
    "no","yes","more","most","less","least","very","much","many","any","some",
    "each","every","both","either","neither","one","two","three","using","used",
    "use","also","based","paper","study","approach","method","methods","result",
    "results","dataset","datasets","task","tasks","including","include","includes"
}

TOKEN_PAT = re.compile(r"[A-Za-z][A-Za-z0-9\-_/\.]{2,}")


def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def read_file_to_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        data = f.read()

    if ext in {".txt", ".md", ".csv"}:
        try:
            return normalize_spaces(data.decode("utf-8"))
        except UnicodeDecodeError:
            return normalize_spaces(data.decode("latin-1", errors="ignore"))

    if ext == ".docx":
        if docx2txt is None:
            raise RuntimeError("docx2txt is not installed, so DOCX parsing is unavailable")
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            text = docx2txt.process(tmp_path) or ""
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return normalize_spaces(text)

    if ext == ".pdf":
        if pdf_extract_text is None:
            raise RuntimeError("pdfminer.six is not installed, so PDF parsing is unavailable")
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            text = pdf_extract_text(tmp_path) or ""
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return normalize_spaces(text)

    try:
        return normalize_spaces(data.decode("utf-8"))
    except UnicodeDecodeError:
        return normalize_spaces(data.decode("latin-1", errors="ignore"))


def tokenize(text: str) -> List[str]:
    return [t for t in TOKEN_PAT.findall((text or "").lower()) if t not in EN_STOP]


def extract_keywords(text: str, top_k: int = 20) -> List[str]:
    tokens = [t for t in tokenize(text) if len(t) >= 3]
    if not tokens:
        return []
    counts = Counter(tokens)
    return [w for w, _ in counts.most_common(top_k)]
