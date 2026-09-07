# -*- coding: utf-8 -*-
"""
汎用メールドメイン マスターリスト
=================================

用途：
  Contact.org_domain_name（会社ドメイン）や Company.domain の重複検出・
  スコアリングにおいて、「個人・世帯単位で契約する、会社を特定する根拠に
  ならないドメイン」を判定するためのマスターリスト。
  is_generic_email_domain() から参照される（contacts/services/normalization.py）。

  例：tanaka@gmail.com のドメイン gmail.com は「田中さんの会社」を
  特定する根拠にならない。一方 tanaka@network-tokai.co.jp のような
  独自ドメインは会社の識別子として使える。

判定方式が2種類ある理由（★重要）：
  - GENERIC_EMAIL_DOMAINS（完全一致）
    フリーメール・キャリアメール・大手ISP（サブドメインなし発行）・
    ケーブルテレビ（JCOM統合後）は、ドメイン自体が全契約者で共通の
    固定文字列（例：@jcom.zaq.ne.jp）。完全一致で判定できる。

  - GENERIC_EMAIL_DOMAIN_SUFFIXES（末尾一致）
    以下の2パターンはドメインが契約者ごとに変わるため、完全一致リストには
    そもそも入れられない。末尾（サフィックス）が一致するかどうかで判定する。
      (a) レンタルサーバーの初期ドメイン
          例：tanaka123.sakura.ne.jp → サフィックス .sakura.ne.jp
      (b) サブドメイン必須で発行される老舗ISP（★2026-09-06レビューで判明）
          例：estate.ocn.ne.jp、ruby.biglobe.ne.jp
          OCN・BIGLOBE・So-net・plala・@nifty(.ne.jp) は、歴史的に
          「ローカルなサブドメイン + プロバイダドメイン」という形式でしか
          メールアドレスを発行しない（@ocn.ne.jp 単体のアドレスは存在しない）。
          これらを誤って完全一致リストに入れると、「サブドメイン部分」が
          独自ドメインとして扱われ、同じISPを使っているだけの無関係な
          個人同士が「ドメイン一致」でスコア加点されてしまう
          （最悪の場合、会社名がたまたま似ていると exact_match で
          自動リンクされる誤結合事故に直結する）。

網羅性についての注意：
  日本国内だけでもケーブルテレビ局・ISP・レンタルサーバーは数百〜千単位で
  存在し、完全網羅は現実的ではない。本リストは「実務で遭遇する頻度が
  高いもの」を優先して収録した候補であり、確定版ではない。
  レビューキュー（CompanyDuplicateCandidate）の運用で漏れが見つかり次第、
  このファイルに追記していく運用とする（v0.13のレビューで確立した方針：
  マスターリストの不完全性は exact_match 自動リンク時の ActionLog 記録で
  保険をかける。詳細は仕様書 §6.5.4 参照）。

最終更新：2026-09-06
  - v1: 初版作成
  - v2〜v3: OCN/BIGLOBE/So-net/plala/@nifty(.ne.jp) を完全一致から
    末尾一致へ移動（サブドメイン必須のため）。ybb.ne.jp/odn.ne.jp/mineo.jp
    等の主要ドメインを追加。xrea.com/wpx.jp を末尾一致に追加。
    is_generic_email_domain() に @ 以降を切り出す前処理を追加。
  - v4: 「サブドメイン必須ISP」の追加調査を実施し、dion.ne.jp/
    auone-net.jp/iij4u.or.jp/dti.ne.jp/bekkoame(.or.jp)/vodafone.ne.jp
    を完全一致から末尾一致へ移動。hi-ho.ne.jp/pdx.ne.jpは両対応（加入
    時期・契約種別によりサブドメインの有無が分かれるため）。冗長だった
    jcom.zaq.ne.jp（.zaq.ne.jpサフィックスと重複）を削除。willcom.com/
    kcn.jpを追加。ロリポップ！初期ドメイン4種・Jimdo/Weeblyを末尾一致に追加。
  - v5: round-3レビューで残っていた老舗ISP4件（sannet/t-com/tokai/gol）を
    再調査。gol.comはサブドメインなし確定で維持。sannet/t-com/tokaiは
    確証を得られず現状維持（保留）。新たに iij.ad.jp（IIJという法人自体の
    組織ドメインであり個人向け汎用メールではない）と bb.excite.co.jp
    （エキサイト社の通知送信専用ドメイン）を誤登録と判断し削除。
    mac.jp（round-3で実在確認が取れなかったもの）も削除。dream.jpは
    「××@dream.jp」形式（サブドメインなし）と確認し維持。
    round-2で指摘のあったアジア系（nate.com/foxmail.com/sohu.com）・
    欧州系（freenet.de）フリーメールを追加。
  - v6: bbexcite.jp自体も実はサブドメイン必須（xxx.サブドメイン.bbexcite.jp）
    と判明し、完全一致から末尾一致（.bbexcite.jp）へ移動（v5のchangelogで
    「bb.excite.co.jp削除の代替はbbexcite.jp」と書いたが、そのbbexcite.jp
    自体に同じ欠陥が残っていたための追加修正）。eonet.ne.jp/bbiq.jp/
    pikara.ne.jp(.or.jp)がサブドメイン必須（ランダム割当）と確認され
    末尾一致へ移動。enjoy.ne.jp/megaegg.ne.jp/commufa.jpも同業種（光回線）
    の同型リスクありと判断し確証度中のまま予防的に末尾一致へ移動。
    odn.ne.jp/wakwak.comは逆にサブドメインなしと確認され完全一致で維持。
    mopera.net（mopera Uブランド）をmopera.ne.jp（旧moperaブランド）とは
    別に追加（誤記ではなく両ブランドとも実在するため両方収録）。
    ymail.ne.jpを追加（実務の会員登録許可リストで実在確認）。
  - v7: ジット君の一貫性指摘を反映。enjoy.ne.jp/megaegg.ne.jp/commufa.jpは
    「確証度中」のまま完全一致から削除して末尾一致のみにしていたが、これは
    hi-ho.ne.jp/pdx.ne.jpで採用した「確証が取れないなら両方に残す」方針と
    矛盾していた（二重登録に実害はないため、方針をこちらに統一し完全一致へ
    復元）。mopera.net（公式サポートページで「bbb@mopera.net」の形式を確認）
    とymail.ne.jp（2022年3月開始のYahoo!メール新ドメイン、公式発表で確認）
    について、サブドメインなしの確認コメントを追記。
"""

# =============================================================================
# 完全一致で判定するドメイン
# =============================================================================
GENERIC_EMAIL_DOMAINS = frozenset({
    # --- 海外系フリーメール（大手） -----------------------------------
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "outlook.jp",
    "hotmail.com",
    "hotmail.co.jp",
    "live.com",
    "live.jp",
    "msn.com",
    "yahoo.com",
    "yahoo.co.jp",
    "yahoo.ne.jp",       # ★追加：Y!mobile契約者向け
    "ymail.com",
    "rocketmail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "aim.com",

    # --- 海外系フリーメール（プライバシー志向・その他、念のため） -----
    "protonmail.com",
    "proton.me",
    "pm.me",
    "gmx.com",
    "gmx.net",
    "gmx.de",
    "mail.com",
    "yandex.com",
    "yandex.ru",
    "mail.ru",
    "zoho.com",           # ★追加：zohomail.comに加えzoho.com自体も
    "zohomail.com",
    "tutanota.com",
    "tuta.io",
    "fastmail.com",
    "hushmail.com",

    # --- 欧州の国別フリーメール（取引先に外国籍企業が混ざる場合の保険） -
    "web.de",           # ドイツ
    "freenet.de",       # ★追加：ドイツ
    "t-online.de",      # ドイツ
    "libero.it",        # イタリア
    "orange.fr",        # フランス
    "laposte.net",      # フランス
    "free.fr",          # フランス
    "ziggo.nl",         # オランダ

    # --- 中華圏・韓国のフリーメール（アジア圏取引先向けの保険） --------
    "qq.com",
    "foxmail.com",         # ★追加：Tencent系（QQと同運営）
    "163.com",
    "126.com",
    "sina.com",
    "sohu.com",            # ★追加
    "naver.com",
    "hanmail.net",
    "daum.net",
    "nate.com",            # ★追加：韓国SK Communications

    # --- 日本の携帯キャリアメール ----------------------------------------
    "docomo.ne.jp",
    "ezweb.ne.jp",
    "au.com",
    "softbank.ne.jp",
    "i.softbank.jp",
    "disney.ne.jp",      # au（旧ディズニー・モバイル）
    "ymobile.ne.jp",
    "y-mobile.ne.jp",     # ★追加：ハイフンあり表記も存在
    "rakuten.jp",         # 楽天モバイル
    "mineo.jp",           # ★追加：オプテージ
    "uqmobile.jp",        # ★追加：UQモバイル

    # --- 日本の大手プロバイダ・回線一体型ISP -----------------------------
    # ★注意：ocn.ne.jp / biglobe.ne.jp / so-net.ne.jp / plala.or.jp /
    # nifty.ne.jp / dion.ne.jp / auone-net.jp / iij4u.or.jp / dti.ne.jp /
    # bekkoame.or.jp はサブドメイン必須で発行されるため、ここには置かず
    # 下記 GENERIC_EMAIL_DOMAIN_SUFFIXES 側に定義する
    # （★2026-09-06レビューで dion.ne.jp/auone-net.jp/iij4u.or.jp/dti.ne.jp
    # /bekkoame も同種のサブドメイン必須ISPと判明し追加移動）。
    "nifty.com",           # ★確認済み：@nifty はサブドメインなしで発行
    "nifty.jp",            # ★追加
    "asahi-net.or.jp",     # ★確認済み：[ID]@asahi-net.or.jp、サブドメインなし
    "asahi-net.jp",
    "au-one.jp",
    "dream.jp",            # dtiの新ブランド（サブドメインなし確認済み）
    "eaccess.ne.jp",
    "e-mobile.ne.jp",
    "emobile.ad.jp",
    "gol.ne.jp",
    "gol.com",             # フュージョン（表記ゆれ、サブドメインなし確認済み）
    "hi-ho.ne.jp",         # ★確認済み：加入時期によりサブドメインの有無が異なる
                           #   （サブドメインなしの時期もあるため完全一致にも残す。
                           #    サブドメインありの分は .hi-ho.ne.jp 側でカバー）
    "infoweb.ne.jp",       # @nifty旧ドメイン
    "itscom.net",          # イッツコム
    "itscom.jp",
    "enjoy.ne.jp",         # ★v7で復元：エディオンネット。確証度中のまま.enjoy.ne.jp
                           #   （末尾一致）にも登録済み。hi-ho.ne.jp/pdx.ne.jpと同じ
                           #   「確証が取れないなら両方に残す」方針に統一するため、
                           #   完全一致からの削除を取り消した（検出漏れ防止）
    "megaegg.ne.jp",       # ★v7で復元：メガ・エッグ（同上の理由）
    "commufa.jp",          # ★v7で復元：コミュファ光（同上の理由）
    "sannet.ne.jp",        # ★未確認：サブドメイン有無を確定できず保留（round-3以降）
    "t-com.ne.jp",         # ★未確認：サブドメイン有無を確定できず保留
    "tokai.or.jp",         # ★未確認：サブドメイン有無を確定できず保留
    "tnc.ne.jp",
    "ucom.ne.jp",
    "vectant.ne.jp",       # 丸紅アクセスソリューションズ
    "wakwak.com",          # ★確認済み：基本メールアドレスはサブドメインなし
    "excite.co.jp",
    "infoseek.jp",
    "goo.jp",
    "mail.goo.ne.jp",
    "rakuten.ne.jp",
    "rakumail.jp",
    "freebit.net",
    "ybb.ne.jp",           # ★確認済み：Yahoo! BB、サブドメインなし
    "ymail.ne.jp",         # ★v7で再確認：2022年3月開始のYahoo!メール新ドメイン
                           #   （@yahoo.co.jpと同一アカウントで使える追加アドレス、
                           #   サブドメインなし。ymail.com＝Yahoo!米国版とは別物）
    "odn.ne.jp",           # ★確認済み：旧ODN、基本メールはサブドメインなし
    "mopera.ne.jp",        # NTTドコモ「mopera」（旧ブランド、サブドメインなし）
    "mopera.net",          # NTTドコモ「mopera U」（新ブランド）。★v7で確認済み：
                           #   公式サポートページで「bbb@mopera.net」という
                           #   サブドメインなしの形式を確認（旧moperaとは別ブランド
                           #   として両方現行で実在するため両方収録）
    "gyao.ne.jp",          # ★追加
    "cyberhome.ne.jp",     # ★追加
    "em-net.ne.jp",        # ★追加
    "spice.or.jp",         # ★追加
    "email.ne.jp",         # ★追加
    "rim.or.jp",           # ★追加
    "tiki.ne.jp",          # ★追加
    "valley.ne.jp",        # ★追加
    "kcn.jp",              # ★追加：近鉄ケーブルネットワーク（サブドメインなしと確認済み）
    "willcom.com",         # ★追加：旧ウィルコム（サブドメインなしと確認済み）
    "pdx.ne.jp",           # ★追加：旧DDIポケット（サブドメインなし形も存在、
                           #   サブドメインあり形は .pdx.ne.jp 側でカバー）

    # --- 日本のケーブルテレビ局（★網羅は困難、主要どころのみ） -----------
    # JCOM（最大手、旧ZAQ系地域局はほぼ全て jcom.zaq.ne.jp / jcom.home.ne.jp に統一済み。
    # サブドメインなしと確認済み）
    # ★2026-09-06レビューで削除：jcom.zaq.ne.jp は下記 .zaq.ne.jp サフィックスに
    # 包含されるため冗長（"jcom.zaq.ne.jp".endswith(".zaq.ne.jp") は既に True）。
    # jcom.home.ne.jp は .zaq.ne.jp に包含されないため完全一致で維持する。
    "jcom.home.ne.jp",
    # 主要な独立系ケーブルテレビ局（地域最大手クラスのみ収録）
    "cts.ne.jp",           # ケーブルテレビ品川
    "c-able.ne.jp",        # 山口ケーブルビジョン
    "actv.ne.jp",          # 青森ケーブルテレビ
    "cc9.ne.jp",           # ケーブルテレビ株式会社（cc9）
    "cc9.jp",
    "icntv.ne.jp",         # いちはらケーブルテレビ
    "ztv.co.jp",           # ZTV
    "mx.scn.tv",           # ZTV系
    "catv296.ne.jp",       # イッツコム系（旧）
})


# =============================================================================
# 末尾一致で判定するドメイン
# =============================================================================
GENERIC_EMAIL_DOMAIN_SUFFIXES = (
    # --- サブドメイン必須で発行される老舗ISP（★2026-09-06レビューで追加）--
    # 例：estate.ocn.ne.jp、ruby.biglobe.ne.jp のように、サブドメイン部分は
    # プロバイダが割り当てるランダムな文字列であり、契約者間で共有される
    # 可能性もある（会社の識別子には絶対にならない）。
    ".ocn.ne.jp",          # NTTコミュニケーションズ
    ".biglobe.ne.jp",      # ビッグローブ
    ".so-net.ne.jp",       # ソネット
    ".plala.or.jp",        # NTTぷらら
    ".nifty.ne.jp",        # @nifty旧形式（nifty.com とは別に存在）
    ".zaq.ne.jp",          # 旧ZAQ（JCOM統合前、地域プレフィックス付きの残存分）
    ".zaq.jp",
    ".dion.ne.jp",         # ★追加：au one net旧DION（例：h2.dion.ne.jp）
    ".auone-net.jp",       # ★追加：au one net新形式（例：ab.auone-net.jp）
    ".iij4u.or.jp",        # ★追加：IIJ4U（例：bp.iij4u.or.jp）
    ".dti.ne.jp",          # ★追加：DTI（例：mars.dti.ne.jp）
    ".bekkoame.or.jp",     # ★追加：ベッコアメ（正しくは.or.jp、例：leo.bekkoame.or.jp）
    ".hi-ho.ne.jp",        # ★追加：Hi-ho（加入時期によりサブドメインあり）
    ".vodafone.ne.jp",     # ★追加：旧ボーダフォン（例：d.vodafone.ne.jp、単独形は存在しない）
    ".pdx.ne.jp",          # 旧DDIポケット（サブドメインあり形、例：di.pdx.ne.jp）
    ".bbexcite.jp",        # ★2026-09-06追加：BB.excite（例：xxx.サブドメイン.bbexcite.jp、
                           #   サブドメイン必須と確認。完全一致リストからは削除した）
    ".eonet.ne.jp",        # ★追加：eo光（サブドメインはランダム割当と確認済み）
    ".bbiq.jp",            # ★追加：BBIQ（サブドメイン必須と確認済み）
    ".pikara.ne.jp",       # ★追加：ピカラ光（サブドメイン必須と確認済み）
    ".pikara.or.jp",       # 表記ゆれ
    ".enjoy.ne.jp",        # ★追加：エディオンネット（eonet/bbiq/pikaraと同業種のため
                           #   同型リスクありと判断、確証度中）
    ".megaegg.ne.jp",      # ★追加：メガ・エッグ（同上）
    ".commufa.jp",         # ★追加：コミュファ光（同上）

    # --- 国内レンタルサーバー -----------------------------------------
    ".sakura.ne.jp",       # さくらインターネット
    ".xsrv.jp",            # エックスサーバー
    ".xserver.jp",
    ".lolipop.jp",         # ロリポップ！
    ".heteml.jp",          # ヘテムル（GMOペパボ）
    ".conoha.jp",          # ConoHa WING
    ".mixh.jp",            # mixhost
    ".coreserver.jp",      # コアサーバー
    ".colorfulbox.jp",     # ColorfulBox
    ".star-domain.jp",     # スターサーバー
    ".kagoya.ne.jp",       # カゴヤ・ジャパン
    ".value-domain.com",   # バリュードメイン系レンタルサーバー
    ".xrea.com",           # ★追加：XREA（アカウント名.サーバー番号.xrea.com形式）
    ".wpx.jp",             # ★追加：wpXレンタルサーバー（サーバーID.wpx.jp形式）
    ".web.fc2.com",        # ★追加：FC2ホームページ（実務での遭遇頻度は低いが念のため）
    # ロリポップ！の初期ドメインは105種類あるが、代表的なもののみ収録
    # （実際の名刺データで遭遇頻度が高いもの。全種は追わない）
    ".main.jp",            # ★追加：ロリポップ！初期ドメイン
    ".moo.jp",             # ★追加：同上
    ".sub.jp",             # ★追加：同上
    ".bitter.jp",          # ★追加：同上

    # --- 海外系（EC店舗の仮メール等が紛れるケースの保険） --------------
    ".myshopify.com",      # Shopify系
    ".wixsite.com",        # Wix
    ".000webhostapp.com",  # 000webhost
    ".jimdofree.com",      # ★追加：Jimdo（無料プラン）
    ".jimdosite.com",      # ★追加：Jimdo
    ".weebly.com",         # ★追加：Weebly
)


# =============================================================================
# 判定関数
# =============================================================================

def is_generic_email_domain(domain: str) -> bool:
    """会社名の識別子として使えない、汎用的なメールドメインかどうかを判定する。

    Company重複検出（仕様書 §6.5.1）のドメイン一致スコアリングにおいて、
    汎用ドメイン同士が一致しても加点しないためのガード関数として使う。

    :param domain: 判定対象のドメイン（例："gmail.com"、"estate.ocn.ne.jp"）。
                   誤って "user@gmail.com" のようなメールアドレス全体が
                   渡された場合も、@ 以降を切り出して判定する（★堅牢化）。
    :return: 汎用ドメインなら True
    """
    if not domain:
        return False

    normalized = domain.strip().lower()

    # ★2026-09-06レビューで追加：メールアドレス全体が誤って渡された場合の
    # 防波堤。"@" が含まれていれば最後の "@" より後ろをドメインとみなす。
    if "@" in normalized:
        normalized = normalized.rsplit("@", 1)[-1]

    if normalized in GENERIC_EMAIL_DOMAINS:
        return True

    if normalized.endswith(GENERIC_EMAIL_DOMAIN_SUFFIXES):
        return True

    return False


# ★2026-09-06レビューで確認した挙動の注記：
# is_generic_email_domain("ocn.ne.jp") はサブドメインなしのため False を返す。
# これは実害がない想定である。OCN・BIGLOBE等は実際には
# @xxx.ocn.ne.jp のようにサブドメイン付きでしかメールアドレスを
# 発行しないため、Contact.org_domain_name にサブドメインなしの
# "ocn.ne.jp" が入ること自体が本来起こらない
# （derive_org_domain_name() は email の "@" 以降をそのまま抽出するだけで、
#  実際のメールアドレスに含まれるサブドメインは保持されるため）。
