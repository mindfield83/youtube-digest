# tests/unit/conftest.py
"""Shared fixtures for unit tests."""
import os
import sys
from unittest.mock import MagicMock

import pytest

# Set environment variables BEFORE any app imports
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["SUPADATA_API_KEY"] = "test-key"
