from django.apps import AppConfig


class CardsConfig(AppConfig):
    name = "cards"

    def ready(self):
        from cards import signals  # noqa: F401
