# tests/unit/test_celery_app.py
"""Unit tests for Celery app configuration."""
import pytest


def test_celery_app_exists():
    """Celery app should be importable."""
    from app.celery_app import celery_app
    assert celery_app is not None


def test_celery_app_name():
    """Celery app should have correct name."""
    from app.celery_app import celery_app
    assert celery_app.main == "youtube_digest"


def test_celery_config_broker():
    """Celery should use Redis broker from config."""
    from app.celery_app import celery_app
    # Default test config uses redis://localhost:6379/0
    assert "redis://" in celery_app.conf.broker_url


def test_celery_config_result_backend():
    """Celery should use Redis result backend."""
    from app.celery_app import celery_app
    assert "redis://" in celery_app.conf.result_backend


def test_celery_timezone():
    """Celery should use UTC timezone."""
    from app.celery_app import celery_app
    assert celery_app.conf.timezone == "UTC"


def test_celery_task_serializer():
    """Celery should use JSON serializer."""
    from app.celery_app import celery_app
    assert celery_app.conf.task_serializer == "json"


def test_celery_includes_tasks():
    """Celery should include tasks module."""
    from app.celery_app import celery_app
    assert "app.tasks" in celery_app.conf.include
