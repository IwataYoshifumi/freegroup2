# Anthropic APIキーの取得

FreeGroup2 の名刺 OCR は Claude API（Anthropic API）を利用します。OCR を動かすには、ユーザーご自身の Anthropic アカウントで API キーを発行し、FreeGroup2 に設定する必要があります。キーの発行も、OCR にかかる課金も、すべてご自身の Anthropic アカウントに帰属します。

このページでは、アカウント作成から API キーを FreeGroup2 に設定するまでの手順を説明します。

## はじめに：API Console と claude.ai は別物です

最初に必ず押さえておいてほしい点があります。

- API キーを発行するのは **API Console（[platform.claude.com](https://platform.claude.com/)）** です。
- ふだんチャットで使う **claude.ai** とは別のサービスで、**アカウントも課金も別管理**です。
- Claude Pro / Team を契約していても、その料金に **API のクレジットは含まれません**。API を使うには API Console 側で別途クレジットを用意する必要があります。

「claude.ai にはログインできるのに API キーの画面が見つからない」という場合は、ほとんどがこの混同が原因です。

## 1. Anthropic Console でアカウントを作成する

[platform.claude.com](https://platform.claude.com/) にアクセスし、アカウントを作成します。メールアドレスや Google アカウントで登録できます。

すでに claude.ai 用のアカウントを持っている場合でも、API Console 側で改めてログイン・初期設定が必要です。

## 2. 支払い方法の登録・クレジットの購入

API キーは、支払い方法を登録してクレジットを用意しないと実際には動きません。**この手順を飛ばすと、キーを発行しても OCR がエラーになります。**

API Console の請求（Billing）設定から、クレジットカードなどの支払い方法を登録し、クレジットを購入します。

## 3. API キーを発行する

API Console で次の順に操作します。

1. **Settings → API keys** を開く
2. **Create Key** を押す
3. キーの **名前**・**Workspace**・**権限** を選んで作成する

発行されたキーは **`sk-ant-` で始まる文字列**です。

## 4. キーを安全に保管する

- API キーは **作成時に一度しか表示されません**。後から同じキーを再表示することはできません。
- コピーし損ねた場合は、新しいキーを再発行してください（古いキーは削除して構いません）。
- API キーは **公開リポジトリにコミットしない**でください。`.env` ファイルは Git 管理から外す運用が前提です。

## 5. FreeGroup2 に設定する

取得したキーを、FreeGroup2 プロジェクトの `.env` ファイルに記入します。

```text
ANTHROPIC_API_KEY=sk-ant-...（取得したキー）
```

- 変数名は `ANTHROPIC_API_KEY` です（この名前のとおりに記入してください）。
- `settings.py` 側では OCR の設定のみを扱い、キーそのものは保持しません。キーの値は `.env` だけに置きます。

`.env` の他の項目については [インストール](../install.md) の「環境変数（.env）の設定」を参照してください。

## 任意：利用上限（spend limit）を設定する

Anthropic Console では、Workspace 単位で **月額の利用上限（spend limit）** を設定できます。

API の課金はご自身のアカウントに乗るため、想定外の使いすぎを防ぐ意味で、上限を設定しておくことを推奨します。

## つまずいたとき

| 症状 | 確認すること |
| --- | --- |
| キーを紛失した | 再表示はできません。新しいキーを再発行してください（手順 3）。 |
| キーが動かない・OCR がエラーになる | 支払い方法の登録とクレジット購入が済んでいるか確認してください（手順 2）。 |
| claude.ai にはログインできるが API キーの画面が無い | [platform.claude.com](https://platform.claude.com/)（API Console）側にログインしているか確認してください。claude.ai とは別サービスです。 |

設定が済んだら、[名刺の取り込み](../usage/import.md) に進んで OCR を試してください。
