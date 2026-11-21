# ログインセキュリティ強化 nginx 接続元 IP 設定手順（v1.0）

- 対象機能: ログイン失敗ロック・IP 遮断・ログイン試行記録（仕様書 v1.3.1 §5.7）
- 対象バージョン: FreeGroup2 v1.7+（`nginx:1.27-alpine` + Django `CLIENT_IP_SOURCE=x_real_ip`）

---

## 1. 構成ごとの設定要否

FreeGroup2 本番環境では、Django は常に前段 `nginx` が設定する `X-Real-IP` のみを信頼します（`nginx/nginx.conf` で `proxy_set_header X-Real-IP $remote_addr;` を付与）。接続元の構成に応じて `nginx/extra.d/` の設定有無を切り替えます。

| 導入構成 | `nginx/extra.d/cloudflare-realip.conf` | `cloudflared` 固定 IP 割り当て | 備考 |
|---|---|---|---|
| LAN 直アクセスのみ（Cloudflare なし） | **不要** | 不要 | `nginx` から見た `$remote_addr` がそのまま利用者 IP になります |
| Cloudflare Tunnel のみ | **必要** | **必要** | `cloudflared` からの通信のみ `CF-Connecting-IP` を採用します |
| LAN 直 ＋ Cloudflare 併用（現在の本番） | **必要** | **必要** | Cloudflare 経由は `CF-Connecting-IP` で補正し、LAN 直はそのまま `$remote_addr` を使います |

> **禁止事項**: `set_real_ip_from` に Docker ネットワーク全体（サブネットやゲートウェイ IP）を指定してはなりません。LAN 直アクセスが Docker ゲートウェイ経由に見える構成で、LAN 内端末が `CF-Connecting-IP` ヘッダを偽装できるようになるためです。

---

## 2. Cloudflare 導入先での設定手順

### 手順 1: `cloudflared` コンテナへ固定 IP を割り当てる

標準の `docker-compose.yml` 自体は変更せず、同梱サンプル `docker-compose.cloudflare-realip.sample.yml` を参考に導入先専用のオーバーライド設定（例: `docker-compose.override.yml`、または `-f` 併用）を用意します。

- `networks.default` に環境と衝突しないサブネット（サンプル値: `172.28.0.0/24`）を設定
- `services.cloudflared.networks.default.ipv4_address` に固定 IP（サンプル値: `172.28.0.10`）を設定
- 既存の `default` ネットワークにサブネット定義が無かった場合は、コンテナとネットワークを再作成して反映します:
  ```sh
  docker compose down
  docker compose up -d
  ```
- 反映後、割り当てられた固定 IP を確認します:
  ```sh
  docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' freegroup2-cloudflared-1
  ```

### 手順 2: realip 設定ファイルを配置する

1. サンプルをコピーして `.conf` ファイルを作成します（`.sample` のままでは `nginx` に読み込まれません）。
   ```sh
   cp nginx/extra.d/cloudflare-realip.conf.sample nginx/extra.d/cloudflare-realip.conf
   ```
2. `nginx/extra.d/cloudflare-realip.conf` を開き、`<cloudflared の固定 IP>` を手順 1 の IP（例: `172.28.0.10`）に書き換えます。
   ```nginx
   set_real_ip_from 172.28.0.10;
   real_ip_header CF-Connecting-IP;
   ```
3. `nginx` の構文チェックを行い、再読み込みします（`docker-compose.yml` の `nginx` サービスが `./nginx/extra.d:/etc/nginx/extra.d:ro` としてマウントしています）。
   ```sh
   docker compose exec nginx nginx -t
   docker compose exec nginx nginx -s reload
   ```

---

## 3. Cloudflare Tunnel の転送先設定に関する注意

Cloudflare Zero Trust（管理画面）側の Public Hostname 設定は、以下を必ず守ってください。

- **転送先 URL**: `https://freegroup2-nginx-1:443`
  - **443 番（https）を指定**: `nginx` の 80 番は HTTPS リダイレクト専用のため、80 番へ転送してはなりません。
  - **nginx のコンテナ名で指定**: LAN IP アドレス経由で転送すると、ホストやゲートウェイを経由して `nginx` から見た接続元が `cloudflared` の固定 IP にならず、補正が働きません。
  - **TLS 検証省略（`noTLSVerify`）**: `nginx` の証明書は自己署名証明書のため、Tunnel 設定で証明書検証を省略します。
- **デモ環境（`demo2.freegroup.work` 等）を本対応させる際の注意**:
  - 従来 LAN アドレス経由の転送になっている環境へ本機能を入れる場合は、本番と同様に「`nginx` コンテナ名・443 番・`noTLSVerify`」へ揃えてから上記 realip 設定を有効化してください。

---

## 4. 適用後の確認方法

1. `.env` の `CLIENT_IP_SOURCE` が `x_real_ip`（または未設定＝既定値 `x_real_ip`）かつ `DEBUG=False` で起動チェックの警告が出ないこと。
2. 管理者権限を持つユーザーで「ログイン試行記録一覧」（`/accounts/login-attempts/`）を開き、以下の両方で接続元 IP が正しく記録されることを確認する。
   - **Cloudflare 経由のアクセス**: 接続元端末のグローバル IP が記録される（`cloudflared` のコンテナ IP や Cloudflare エッジ IP ではないこと）。
   - **LAN 直アクセス**: 端末の LAN IP（または実接続元 IP）がそのまま記録される。
   - **検証（偽装ヘッダ耐性）**: LAN 直アクセス時に任意の `CF-Connecting-IP` ヘッダを付与しても無視され、`cloudflared` 以外からの `CF-Connecting-IP` が採用されないこと。
