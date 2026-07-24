from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_client import RemoteChatModel


if __name__ == "__main__":
    model = RemoteChatModel()
    text = model.chat([
        {"role": "user", "content": "Reply with one short sentence confirming the model connection is working."}
    ])
    print(text)
