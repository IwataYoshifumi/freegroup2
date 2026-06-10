# BackNavigator

Django 戻るボタン ライブラリ　使い方ガイド

## 1. 概要

BackNavigator は Django の業務アプリにおける「戻るボタン」を実現するライブラリです。

ブラウザの戻るボタンではなく、検索条件・ページネーション・ソート状態を保持したまま、設計者が意図した画面へ正確に戻ることができます。

遷移履歴は URL のクエリパラメータ（base64 エンコード JSON）として保持するため、セッションや DB を使わず、ブックマークや共有 URL でも状態が維持されます。

## 2. インストール・設定

### ディレクトリ構成

```
back_navigator/
    __init__.py
    apps.py
    back_navigator.py      ← BackNavigator クラス本体
    templatetags/
        __init__.py
        back_tags.py       ← カスタムテンプレートタグ
```

### settings.py への追加

```python
INSTALLED_APPS = [
    ...
    'back_navigator',
]
```

## 3. クラス仕様

### クラス変数

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| PARAM_NAME | back_stack | URL に埋め込む戻る履歴のキー名 |
| _BASE_EXCLUDE_KEYS | csrfmiddlewaretoken, next, _ | クエリから除外するキーの集合 |
| MAX_STACK_DEPTH | 5 | スタックの最大深度（超えたら index=1 を捨てる） |

### プロパティ

| プロパティ名 | 戻り値 | 説明 |
|---|---|---|
| back_exist | bool | 「戻る」を出す条件。履歴が1階層以上あるか |
| back_all_exist | bool | 「最初に戻る」を出す条件。履歴が**2階層以上**あるか（直前の画面と最初の画面が別になるとき） |
| back_url | str | 直前の画面へ戻る URL |
| back_all_url | str | 最初の画面へ戻る URL |
| back_title | str | 直前の画面タイトル（未設定時は「戻る」） |
| back_all_title | str | 最初の画面タイトル（未設定時は「最初に戻る」） |

### メソッド一覧

| メソッド名 | 用途 | 呼び出し場所 |
|---|---|---|
| push_current(title, keys) | 現在画面の状態をスタックに積む | View |
| append_url(url) | URL に back_stack を付与して返す（タグ append_back_url 経由で使用） | テンプレート（タグ経由） |
| hidden_fields() | POST フォーム用 hidden フィールド生成（タグ hidden_back_field 経由で使用） | テンプレート（タグ経由） |

※ `back_url` / `back_all_url` は本ライブラリではプロパティです（上のプロパティ表を参照）。

## 4. View での使い方

### 基本パターン

```python
from back_navigator.back_navigator import BackNavigator

def blog_list(request):
    back = BackNavigator(request)
    back.push_current(
        title='ブログリスト',
        keys=['title', 'author', 'page']  # 保存したいクエリキー
    )
    context = {
        'object_list': Blog.objects.all(),
        'back': back,
    }
    return render(request, 'blog/list.html', context)
```

### push_current の引数

| 引数 | 型 | 説明 |
|---|---|---|
| title | str | 画面名（戻るボタンのラベル表示用） |
| keys | list | 保存したいクエリパラメータのキーリスト |

※ keys に PARAM_NAME（back_stack）や csrfmiddlewaretoken を指定しても自動的に除外されます。

## 5. テンプレートでの使い方

### タグの読み込み

```django
{% load back_tags %}
```

### 詳細リンクへの back_stack 埋め込み

```django
{% url 'blog:detail' pk=blog.pk as detail_url %}
<a href="{% append_back_url detail_url back %}">詳細</a>
```

### 戻るボタン・最初に戻るボタン（推奨）

戻る系のリンクは、リンク（`<a>`）ごと返すタグ `back_link` / `back_all_link` を使います。**テンプレートに `{% if %}` を書く必要はありません。** 戻り先が無いときは各タグが自動で空文字を返し、ボタンは出ません。

```django
{% back_all_link back %}
{% back_link back %}
```

- `back_link` … 履歴が1階層以上（`back_exist`）あれば「戻る」リンクを出す。無ければ空。
- `back_all_link` … 履歴が2階層以上（`back_all_exist`）あれば「最初に戻る」リンクを出す。無ければ空。
- ラベルは `back_title` / `back_all_title`（既定「戻る」「最初に戻る」）。
- 並び順は左から「最初に戻る」「戻る」。確定アクション（保存・作成など）を置く場合は、その右に並べます。

### POST フォームへの埋め込み

```django
<form method="post">
    {% csrf_token %}
    {% hidden_back_field back %}
    ...
</form>
```

### URL だけが欲しいとき（特殊用途）

リンクの `<a>` ではなく URL 文字列だけが必要な場合（View での `redirect` など特殊なケース）は、URL を返すタグ `back_url` / `back_all_url` を使えます。ただし表示の出し分け（戻り先が無ければ隠す）は自前で `{% if back.back_exist %}` 等を書く必要があるため、画面に戻るボタンを置く通常の用途では `back_link` / `back_all_link` を使ってください。

## 6. データ構造

### back_stack の構造

back_stack はリスト形式で管理されます。FILO で動作し、push_current のたびに末尾に追加されます。

```json
[
  {"title": "ブログリスト", "url": "/blog/?title=test&page=2"},
  {"title": "ブログ詳細",   "url": "/blog/123/"},
  {"title": "コメント",     "url": "/comment/234/?page=1"}
]
```

index=0 が最初（「最初に戻る」の戻り先）、index=-1 が直前（「戻る」の戻り先）です。

### URL への埋め込み形式

back_stack は JSON → base64url エンコードされ、1本のクエリパラメータとして URL に付与されます。

```
/blog/123/?back_stack=W3sidGl0bGUiOiAi44OW44Ot44Kw...
```

### 最大深度とスタック管理

| 条件 | 動作 |
|---|---|
| 深度 ≦ 5 | 通常通り末尾に追加 |
| 深度 > 5 | index=0（最初）を残して index=1 を削除 |

index=0 を残すことで、`back_all_url` による「最初の画面へ戻る」が常に機能します。

## 7. カスタムテンプレートタグ一覧

| タグ | 引数 | 説明 |
|---|---|---|
| {% back_link back %} | back: BackNavigator | 「戻る」リンク（`<a>`）を返す。履歴1階層以上のときのみ。無ければ空 |
| {% back_all_link back %} | back: BackNavigator | 「最初に戻る」リンク（`<a>`）を返す。履歴2階層以上のときのみ。無ければ空 |
| {% append_back_url url back %} | url: 遷移先 URL／back: BackNavigator | URL に back_stack を付与して返す |
| {% hidden_back_field back %} | back: BackNavigator | POST フォーム用の hidden フィールドを生成 |
| {% back_url back %} | back: BackNavigator | 直前の画面へ戻る URL を返す（特殊用途。表示の出し分けは自前） |
| {% back_all_url back %} | back: BackNavigator | 最初の画面へ戻る URL を返す（特殊用途。表示の出し分けは自前） |

## 8. NG パターン

| NG パターン | 理由 |
|---|---|
| push_current の keys を空にする | クエリが保存されず意味がない |
| keys に request.GET をそのまま渡す | 不要なパラメータ（csrf token 等）が混入する |
| keys に back_stack を指定する | スタックが無限に膨らむ（自動除外されるが意図的に指定しない） |
| 戻るボタンに `back_url` / `back_all_url` を直接使い、`{% if %}` を自前で書く | 通常用途では `back_link` / `back_all_link` を使う。出し分けがタグ内に隠れ、テンプレートが薄くなる |

## 9. 画面遷移イメージ

```
① 一覧ページ（/blog/?title=test&page=2）
   └ push_current('ブログリスト', ['title', 'page'])
   └ 詳細リンク: {% append_back_url detail_url back %}
       ↓ クリック
② 詳細ページ（/blog/123/?back_stack=W3s...）
   └ push_current('ブログ詳細', [...])
   └ コメントリンク: {% append_back_url comment_url back %}
       ↓ クリック
③ コメントページ（/comment/234/?back_stack=W3s...）
   └ {% back_link back %}     → ブログ詳細に戻る
   └ {% back_all_link back %} → ブログリストに戻る（検索条件復元）
```
