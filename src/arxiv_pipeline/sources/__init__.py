from __future__ import annotations

import sys
from . import handlers
from .service import SourcesModule

# Support both 'arxiv_pipeline.sources.handlers' and singular 'arxiv_pipeline.sources.handler'
handler = handlers
sys.modules["arxiv_pipeline.sources.handler"] = handlers

__all__ = ["SourcesModule", "handlers", "handler"]
