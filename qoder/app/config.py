"""运行期配置：全部来自环境变量，不写入任何密钥。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    powercontext_url: str
    powercontext_token: str | None
    request_timeout: float
    data_dir: Path
    operation_log: Path

    @property
    def question_bank(self) -> Path:
        return PROJECT_ROOT / "data" / "questions.json"

    @property
    def local_db(self) -> Path:
        return self.data_dir / "recall-coach.db"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("RECALL_DATA_DIR", PROJECT_ROOT / "data" / "runtime")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = Path(os.environ.get("RECALL_LOG_DIR", PROJECT_ROOT / "var" / "log")).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        powercontext_url=os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8777").rstrip("/"),
        powercontext_token=os.environ.get("POWERCONTEXT_TOKEN") or None,
        request_timeout=float(os.environ.get("POWERCONTEXT_TIMEOUT", "20")),
        data_dir=data_dir,
        operation_log=log_dir / "powercontext-operations.jsonl",
    )
