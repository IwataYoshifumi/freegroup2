"""duplicates アプリの Form 層（仕様書 v1.4.2 §11.6 / §11.7、17 番）。

MergeForm：マージ画面用 Form（DuplicateCandidate のレビュー判定 +
surviving 側 primary Contact の修正を一体で扱う）。値違い検出 + バリデーション
までが責務で、実 DB 書込（merge_log 作成 / Contact 更新 / set_primary_contact 等）は
duplicates/services/merge_executor.py の 3 サービス関数（Mark_as_Different_Person /
Execute_Merge_Only / Execute_Merge_with_Updates）の責務。

[性質] presentation 層モジュール（DB 操作なし・副作用なし、§11.6.3 設計原則）
"""

from django import forms

from config.constants import (
    DUPLICATE_CHECK_FIELDS,
    DifferentPersonReason,
    DuplicateMergeReason,
)
from contacts.forms import ContactBaseForm
from contacts.models import Contact


class MergeForm(ContactBaseForm):
    """マージ画面用 Form（仕様書 §11.6.2 / §11.7.3、17 番）。

    [性質] presentation 層クラス（DB 操作なし・副作用なし、§11.6.3 設計原則）
    [入力] candidate: DuplicateCandidate、surviving_person: Person、
           merged_person: Person（いずれも kwarg、必須）

    DuplicateCandidate のペア（person_a / person_b）と surviving / merged の関係：
      - surviving_person：マージで残る側（ユーザーが surviving_person_choice で選択）
      - merged_person：マージで統合される側
      - View 側で candidate.person_a / person_b から決定し、kwarg で渡す

    フィールド名 `note` 表記（仕様書 §11.6.2 の `review_note`）は merge_executor.py
    との整合のため `note` で統一（D-4a 指示書 §2 / ストック #37 候補）。
    値違い検出の対象は DUPLICATE_CHECK_FIELDS 限定（仕様書 §11.5.5 と整合）。
    """

    review_decision = forms.ChoiceField(
        choices=[
            ("merged", "同一人物"),
            ("additional_role", "同一人物（別肩書追加）"),
            ("different", "別人"),
        ],
        required=True,
        label="判定",
    )
    review_result = forms.MultipleChoiceField(
        choices=DuplicateMergeReason.choices + DifferentPersonReason.choices,
        required=False,
        label="判定理由",
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea,
        label="備考",
    )
    surviving_person_choice = forms.ChoiceField(
        choices=[("person_a", "左側"), ("person_b", "右側")],
        required=False,
        label="サバイブ側選択",
    )

    def __init__(
        self,
        *args,
        candidate=None,
        surviving_person=None,
        merged_person=None,
        **kwargs,
    ):
        if (
            candidate is None
            or surviving_person is None
            or merged_person is None
        ):
            raise TypeError(
                "MergeForm requires 'candidate', 'surviving_person', "
                "'merged_person' keyword arguments."
            )
        self.candidate = candidate
        self.surviving_person = surviving_person
        self.merged_person = merged_person

        # initial：surviving 側 primary_contact の UPDATABLE_FIELDS 値で初期化。
        # ContactUpdateForm と同様に instance は使わない（_post_clean による
        # メモリ上フィールドの上書きを避ける、contacts/forms.py L106-118 参照）。
        surviving_primary = surviving_person.primary_contact
        merged_initial = {
            f: getattr(surviving_primary, f)
            for f in Contact.UPDATABLE_FIELDS
        }
        merged_initial.update(kwargs.pop("initial", {}) or {})
        kwargs["initial"] = merged_initial
        kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)

        self._compute_field_diff()
        self._add_dynamic_confirm_checkboxes()
        self._apply_widget_classes()

    def _compute_field_diff(self):
        """[性質] 副作用あり（self._value_diff_fields / _value_match_fields を設定）"""
        surviving_primary = self.surviving_person.primary_contact
        merged_primary = self.merged_person.primary_contact
        self._value_diff_fields = []
        self._value_match_fields = []
        for field_name in DUPLICATE_CHECK_FIELDS:
            sv = getattr(surviving_primary, field_name)
            mv = getattr(merged_primary, field_name)
            if sv == mv:
                self._value_match_fields.append(field_name)
            else:
                self._value_diff_fields.append(field_name)

    def _add_dynamic_confirm_checkboxes(self):
        """[性質] 副作用あり（self.fields に confirmed_<field> BooleanField を追加）

        surviving 側 primary_contact の DUPLICATE_CHECK_FIELDS のうち、ContactFieldConfidence
        が low/medium かつ未確認のものに対して動的にチェックボックスを追加する
        （仕様書 §11.6.2、ContactUpdateForm L121-129 のパターン踏襲）。
        """
        confidences = (
            self.surviving_person.primary_contact.get_field_confidences()
        )
        for field_name in DUPLICATE_CHECK_FIELDS:
            conf = confidences.get(field_name)
            if conf is None:
                continue
            if (
                conf.confidence in ("low", "medium")
                and conf.confirmed_at is None
            ):
                self.fields[f"confirmed_{field_name}"] = forms.BooleanField(
                    required=False,
                    label=f"『{field_name}』フィールドを確認しました",
                )

    def clean(self):
        """バリデーション（仕様書 §11.7.3、D-4d-1 第 4 弾 3 値化版で再構成）。

        [性質] 純関数（self.cleaned_data から検証、DB 操作なし）
        [出力] cleaned_data: dict（review_result は MultipleChoiceField により list[str]）

        review_decision の 3 値（merged / additional_role / different）に応じた検証：
          - merged: review_result が 1 個以上 + 全 value がマージ系（ADDITIONAL_ROLE 除く 6 値）
          - additional_role: review_result は空でも可。整形後 cleaned["review_result"]
            を `["additional_role"]` に固定（DC.review_result への保存値も追従、
            ActionLog の record_merge_action が `ADDITIONAL_ROLE in review_result` で
            検出できるようにする）
          - different: review_result が 1 個以上 + 全 value が別人系（3 値）

        他項目：
          - other_merged / other_different 選択時の note 必須
          - review_decision が merged または additional_role のときのみ、
            surviving_person_choice の選択を必須化（D-4d-1 第 7 弾 §2-1-B：
            未選択時 / 別人判定時はテンプレ側でサバイブ選択 UI が disabled 化）
          - review_decision が merged または additional_role のときのみ、
            surviving 側 low/mid 未確認 CB の全 ON を要求（D-4d-1 第 5 弾 §2-1：
            別人判定では確認 CB ブロックがテンプレ側で動的非表示のため、
            バリデーションも走らせない）
        """
        cleaned = super().clean()

        review_decision = cleaned.get("review_decision")
        review_result = cleaned.get("review_result") or []
        all_merged_values = set(DuplicateMergeReason.values)
        merged_ui_values = all_merged_values - {
            DuplicateMergeReason.ADDITIONAL_ROLE.value
        }
        different_values = set(DifferentPersonReason.values)
        result_set = set(review_result)

        # 1: review_decision ごとの整合性検証
        if review_decision == "merged":
            if not result_set:
                self.add_error(
                    "review_result",
                    "「同一人物」を選択した場合は判定理由を 1 つ以上選択してください",
                )
            elif not result_set.issubset(merged_ui_values):
                self.add_error(
                    "review_result",
                    "「同一人物」を選択した場合はマージ系の判定理由のみを選択してください",
                )
        elif review_decision == "additional_role":
            # additional_role は判定理由 UI を出さないため、cleaned 上で固定値に整形。
            # DC.review_result への保存値もこの値（ActionLog の in 検出に必須）。
            if result_set and not result_set.issubset(all_merged_values):
                self.add_error(
                    "review_result",
                    "「同一人物（別肩書追加）」では別人系の判定理由は選択できません",
                )
            cleaned["review_result"] = [
                DuplicateMergeReason.ADDITIONAL_ROLE.value
            ]
            review_result = cleaned["review_result"]
            result_set = set(review_result)
        elif review_decision == "different":
            if not result_set:
                self.add_error(
                    "review_result",
                    "「別人」を選択した場合は判定理由を 1 つ以上選択してください",
                )
            elif not result_set.issubset(different_values):
                self.add_error(
                    "review_result",
                    "「別人」を選択した場合は別人系の判定理由のみを選択してください",
                )

        # 2: review_decision が merged / additional_role のとき
        # surviving_person_choice を必須化（D-4d-1 第 7 弾 §2-1-B）。
        # 未選択時 / different のときはテンプレ側でサバイブ選択 UI が disabled 化、
        # かつ別人判定ではサバイブ値は使われないため必須化しない。
        if review_decision in ("merged", "additional_role"):
            if not cleaned.get("surviving_person_choice"):
                msg = (
                    "主コンタクトを選択してください"
                    if review_decision == "additional_role"
                    else "サバイブ側を選択してください"
                )
                self.add_error("surviving_person_choice", msg)

        # 3: other_* を含む選択時の note 必須
        has_other = (
            DuplicateMergeReason.OTHER_MERGED in result_set
            or DifferentPersonReason.OTHER_DIFFERENT in result_set
        )
        if has_other and not cleaned.get("note"):
            self.add_error(
                "note",
                "「その他」を選択した場合は備考の入力が必要です",
            )

        # 4: surviving 側 low/mid 未確認の動的 CB が全 ON
        # 別人判定（review_decision='different'）では確認チェックブロックが
        # テンプレ側 app-section--executes-merge ラッパーで動的非表示になっており
        # ユーザが CB を ON にできない。バリデーションも走らせない（D-4d-1 第 5 弾 §2-1）。
        if review_decision in ("merged", "additional_role"):
            confidences = (
                self.surviving_person.primary_contact.get_field_confidences()
            )
            for field_name in DUPLICATE_CHECK_FIELDS:
                conf = confidences.get(field_name)
                if conf is None:
                    continue
                if (
                    conf.confidence in ("low", "medium")
                    and conf.confirmed_at is None
                ):
                    chk_name = f"confirmed_{field_name}"
                    if not cleaned.get(chk_name):
                        self.add_error(
                            chk_name,
                            f"『{field_name}』フィールドの確認チェックを ON にしてください",
                        )

        return cleaned

    def get_merge_reason(self):
        """review_result からマージ系 value のリストを取り出す（D-4d-1 第 4 弾 §2-4-C）。

        [性質] 純関数（self.cleaned_data から導出、DB 操作なし・副作用なし）
        [入力] なし
        [出力] list[str]（review_result の中で DuplicateMergeReason.values に含まれる
               value だけを順序を維持してリスト化。別人系のみ or 未入力なら空リスト []。
               review_decision='additional_role' のとき clean() で
               cleaned_data['review_result']=['additional_role'] に整形済のため、
               本メソッドは自然に ['additional_role'] を返す）

        Execute_Merge_Only は本メソッドの戻り値の list に対して `ADDITIONAL_ROLE in ...`
        の所属判定を行う（D-4d-1 第 4 弾 §2-4-E）。
        """
        review_result = self.cleaned_data.get("review_result") or []
        merged_values = set(DuplicateMergeReason.values)
        return [v for v in review_result if v in merged_values]

    def has_confirm_checkboxes(self):
        """confirmed_<field> 動的 CB が 1 件以上あるかを返す。

        [性質] 純関数（self.fields のキー走査のみ、DB 操作なし）
        [出力] bool（テンプレ側で「確認チェック」セクションの表示判定に使用）
        """
        return any(name.startswith("confirmed_") for name in self.fields)

    def confirmed_field_names(self):
        """ユーザーが確認・編集したフィールド名リスト（仕様書 §11.6.2 / §9.4）。

        [性質] 純関数（DB 操作なし・副作用なし）
        [入力] なし（self.cleaned_data / self.surviving_person.primary_contact から参照）
        [出力] list[str]（Contact.UPDATABLE_FIELDS のサブセット）

        判定基準（OR）：
          (1) confirmed_<field> チェックボックスが ON
          (2) フォーム送信値が surviving_person.primary_contact の現在値と異なる
        """
        confirmed = []
        surviving_primary = self.surviving_person.primary_contact
        for field_name in Contact.UPDATABLE_FIELDS:
            chk_on = bool(
                self.cleaned_data.get(f"confirmed_{field_name}")
            )
            current_value = getattr(surviving_primary, field_name)
            submitted_value = self.cleaned_data.get(field_name)
            edited = submitted_value != current_value
            if chk_on or edited:
                confirmed.append(field_name)
        return confirmed

    def has_field_updates(self):
        """surviving_person.primary_contact のフィールド更新有無を判定。

        [性質] 純関数（DB 操作なし・副作用なし）
        [入力] なし（self.cleaned_data / self.surviving_person.primary_contact から参照）
        [出力] bool（17 番 View で Execute_Merge_Only /
               Execute_Merge_with_Updates の分岐に使用、§11.4.6）

        判定：cleaned_data の値が surviving_person.primary_contact の値と
        1 フィールドでも異なれば True。
        """
        surviving_primary = self.surviving_person.primary_contact
        for field_name in Contact.UPDATABLE_FIELDS:
            current_value = getattr(surviving_primary, field_name)
            submitted_value = self.cleaned_data.get(field_name)
            if submitted_value != current_value:
                return True
        return False

    def value_diff_fields(self):
        """値違いフィールドのリスト（DUPLICATE_CHECK_FIELDS 内、§11.5.5）。

        [性質] 純関数（DB 操作なし・副作用なし）
        [出力] list[str]（D-4d テンプレートで「値が違う」として強調表示する対象）
        """
        return list(self._value_diff_fields)

    def value_match_fields(self):
        """値一致フィールドのリスト（DUPLICATE_CHECK_FIELDS 内、§11.5.5）。

        [性質] 純関数（DB 操作なし・副作用なし）
        [出力] list[str]（D-4d テンプレートで「値が同じ」として目立たせる対象）
        """
        return list(self._value_match_fields)

    def hidden_name_fields(self):
        """マージレビュー画面で表示を省略する氏名サブフィールド名のリスト（D-4d-1 第 3 弾 §2 修正項目 3）。

        [性質] 純関数（self.surviving_person / self.merged_person.primary_contact から
               導出、DB 操作なし・副作用なし）
        [入力] なし
        [出力] list[str]（`["last_name", "first_name"]` または `[]`）

        full_name が両側一致 + last_name / first_name も両側一致 + last_name / first_name
        がそれぞれ非空 + full_name に last_name / first_name が部分一致で含まれる、を
        すべて満たすとき `["last_name", "first_name"]` を返す。それ以外は `[]`。

        full_name で氏名情報が完結している場合に姓・名の重複表示を抑止する用途
        （View 側で field_groups から除外）。氏名グループは full_name で残るので
        グループ見出しは消えない。
        """
        surviving_primary = self.surviving_person.primary_contact
        merged_primary = self.merged_person.primary_contact

        sv_full = surviving_primary.full_name
        sv_last = surviving_primary.last_name
        sv_first = surviving_primary.first_name
        mg_full = merged_primary.full_name
        mg_last = merged_primary.last_name
        mg_first = merged_primary.first_name

        if sv_full != mg_full:
            return []
        if sv_last != mg_last:
            return []
        if sv_first != mg_first:
            return []
        if not sv_last or not sv_first:
            return []
        if sv_last not in sv_full:
            return []
        if sv_first not in sv_full:
            return []
        return ["last_name", "first_name"]
