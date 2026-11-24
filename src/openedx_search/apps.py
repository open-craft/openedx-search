"""
openedx_search Django application initialization.
"""

from django.apps import AppConfig


class OpenEdxSearchConfig(AppConfig):
    """
    Django app configuration for openedx_search.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "openedx_search"
    verbose_name = "Open edX Search"

    def ready(self) -> None:
        """Register the engine adapters the library ships."""
        # Imported here so the app module stays free of engine code until the
        # settings are loaded, and so a consumer cannot import an adapter by
        # accident through this module.
        from .meilisearch import register  # pylint: disable=import-outside-toplevel

        register()
