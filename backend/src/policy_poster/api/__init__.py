"""FastAPI service over the policy-poster pipeline."""

from .app import create_app

__all__ = ["create_app"]
