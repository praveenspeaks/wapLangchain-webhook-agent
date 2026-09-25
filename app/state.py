"""Runtime state owned by one FastAPI application instance."""

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppState:
    graph: Any = None
    start_time: float = field(default_factory=time.monotonic)
    messages_processed: int = 0
    messages_failed: int = 0
