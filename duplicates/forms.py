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

    review_result = forms.MultipleChoiceField(
        choices=DuplicateMergeReason.choices + DifferentPersonReason.choices,
        widget=forms.CheckboxSelectMultiple,
        label="判定理由",
    )
    merge_reason = forms.ChoiceField(
        choices=DuplicateMergeReason.choices,
        required=False,
        label="マージ理由",
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea,
        label="備考",
    )
    surviving_person_choice = forms.ChoiceField(
        choices=[("person_a", "左側"), ("person_b", "右側")],
        required=True,
        initial="person_a",
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
        """6 項目バリデーション（仕様書 §11.7.3）。

        [性質] 純関数（self.cleaned_data から検証、DB 操作なし）
        [出力] cleaned_data: dict

        バリデーション項目：
          1. review_result：1 つ以上 + merged / different 系の混在禁止
          2. other_merged / other_different 選択時の note 必須
          3. merged 系のときの merge_reason 必須（different 系のみなら不要）
          4. surviving 側の DUPLICATE_CHECK_FIELDS low/mid 未確認の動的 CB 全 ON
          5. 値違いフィールドの確認済み（D-4a では最低限、詳細 UI 連動は D-4d）
          6. surviving_person_choice の必須（required=True で担保）
        """
        cleaned = super().clean()

        review_result = cleaned.get("review_result") or []
        merged_values = set(DuplicateMergeReason.values)
        different_values = set(DifferentPersonReason.values)
        merged_in_result = [v for v in review_result if v in merged_values]
        different_in_result = [
            v for v in review_result if v in different_values
        ]

        # 1: review_result 整合性
        if not review_result:
            self.add_error(
                "review_result", "判定理由を1つ以上選択してください"
            )
        elif merged_in_result and different_in_result:
            self.add_error(
                "review_result",
                "マージ系（merged）と別人系（different_person）の理由を"
                "同時に選択できません",
            )

        # 2: other_* 選択時の note 必須
        has_other = (
            DuplicateMergeReason.OTHER_MERGED in review_result
            or DifferentPersonReason.OTHER_DIFFERENT in review_result
        )
        if has_other and not cleaned.get("note"):
            self.add_error(
                "note",
                "「その他」を選択した場合は備考の入力が必要です",
            )

        # 3: merged 系のときの merge_reason 必須
        if merged_in_result and not different_in_result:
            if not cleaned.get("merge_reason"):
                self.add_error(
                    "merge_reason", "マージ理由を選択してください"
                )

        # 4: surviving 側 low/mid 未確認の動的 CB が全 ON
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

        # 5: 値違いフィールドの確認済み（D-4a では no-op、D-4d で実装）
        # 6: surviving_person_choice は required=True で担保

        return cleaned

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
