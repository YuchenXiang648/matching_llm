from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
import json
from typing import Any, Dict, List, Optional

import requests


class RemoteChatModel:
    def __init__(self, base_url: Optional[str] = None, model_name: Optional[str] = None, timeout: int = 180):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://128.16.12.12:11434").rstrip("/")
        self.model_name = model_name or os.getenv("OLLAMA_MODEL") or "gemma4:26b"
        self.timeout = int(os.getenv("OLLAMA_TIMEOUT", str(timeout)))

        print(f"[model] base_url={self.base_url}")
        print(f"[model] model_name={self.model_name}")

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/api/chat"

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        system: Optional[str] = None,
        temperature: float = 0.2,
        top_p: float = 0.9,
        top_k: int = 40,
        num_ctx: int = 8192,
        stop: Optional[List[str]] = None,
    ) -> str:
        chat_messages: List[Dict[str, str]] = list(messages)

        if system:
            chat_messages = [
                {"role": "system", "content": system},
                *chat_messages,
            ]

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "stream": False,
            "messages": chat_messages,
            "think": False,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "top_k": top_k,
                "num_ctx": num_ctx,
            },
        }
        if stop:
            payload["options"]["stop"] = stop

        resp = requests.post(self.chat_url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("message", {}) or {}).get("content", "").strip()


def extract_json(text: str) -> Any:
    s = (text or "").strip()
    try:
        return json.loads(s)
    except Exception:
        pass

    start_obj = s.find("{")
    end_obj = s.rfind("}")
    if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
        chunk = s[start_obj : end_obj + 1]
        try:
            return json.loads(chunk)
        except Exception:
            pass

    start_arr = s.find("[")
    end_arr = s.rfind("]")
    if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
        chunk = s[start_arr : end_arr + 1]
        try:
            return json.loads(chunk)
        except Exception:
            pass

    raise ValueError("Failed to parse JSON from model output")
