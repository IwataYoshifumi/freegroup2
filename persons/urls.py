"""persons アプリの URL ルーティング（仕様書 v1.4.2 §11.4 / §11.5）。

定義する URL：
  - person_list（7 番）: 人物一覧画面（GET）
  - person_detail（8 番）: 人物詳細画面（GET、status 4 分岐）
"""

from django.urls import path

from . import views


app_name = "persons"


urlpatterns = [
    path("", views.PersonListView.as_view(), name="person_list"),
    path("<uuid:pk>/", views.PersonDetailView.as_view(), name="person_detail"),
]
