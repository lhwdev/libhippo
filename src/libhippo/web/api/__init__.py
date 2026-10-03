"""Web API modules for LibHippo harness."""

from libhippo.web.api.autocomplete import setup_autocomplete_routes
from libhippo.web.api.knowledge import setup_knowledge_routes
from libhippo.web.api.session import setup_session_routes
from libhippo.web.api.settings import setup_settings_routes
from libhippo.web.api.tasks import setup_tasks_routes

__all__ = [
    "setup_autocomplete_routes",
    "setup_knowledge_routes",
    "setup_session_routes",
    "setup_settings_routes",
    "setup_tasks_routes",
]
