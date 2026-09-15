"""
Vercel entrypoint.

Vercel's Python runtime looks for a module under api/ that exposes an ASGI
application named `app`, and vercel.json rewrites every path onto this one file,
so FastAPI still sees the original request path and routes it as usual.

There is no uvicorn here and no lifespan worth running: the platform freezes the
process between requests, so the background poll loop that fills the pipeline
locally would never advance. DEMO_STATELESS (set in vercel.json, and implied by
Vercel's own VERCEL=1) makes each request rebuild the mock race instead. See
app/demo.py for why that is equivalent and what it costs.
"""
import os
import sys

# The runtime imports this file directly, so the project root - the parent of
# api/, holding the app package - is not necessarily on the path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402

__all__ = ["app"]
