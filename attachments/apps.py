from django.apps import AppConfig


class AttachmentsConfig(AppConfig):
    name = "attachments"
    verbose_name = "添付ファイル"

    def ready(self):
        from attachments import signals  # noqa: F401
