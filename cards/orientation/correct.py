"""判定結果（OrientationResult）→ 正位化画像＋向き補正状態の解決（純関数寄り）。

回転は transpose のみ（rotate 禁止・v0.3 §4.1・§10メモ6）。expand 不要・無損失・次元自動補正で
既存 views.py 手動回転（ROTATE_*）と規約も揃う。スコアは ONNX 生値（softmax済み）をそのまま閾値
比較に使う（二重 softmax 禁止・v0.3 §6.2）。

向き補正状態（BusinessCard.OrientationCorrection の値文字列）：
  rotated  … score>=閾値 かつ label≠0 → transpose で回転補正
  straight … score>=閾値 かつ label=0  → 最初から正立（無回転）
  kept     … score<閾値（label 不問）   → 据え置き
  failed   … 判定器が例外で落ちて据え置き
  （none/passthrough は呼び出し側で correction=None・score=None とする）
"""

from PIL import Image

from cards.orientation.backends import STATUS_FAILED, STATUS_PASSTHROUGH

# 契約値ラベル（current CW 回転量）→ PIL transpose（反時計回り）。label=0 は無回転。
_ROTATE = {
    90: Image.Transpose.ROTATE_90,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_270,
}

CORRECTION_ROTATED = "rotated"
CORRECTION_KEPT = "kept"
CORRECTION_STRAIGHT = "straight"
CORRECTION_FAILED = "failed"


def resolve_orientation(image, result, threshold):
    """[性質] 純関数（DB操作なし・副作用なし）/ (final_image, correction, score) を返す。

    [入力] image: PIL.Image（切り抜き済み）、result: OrientationResult、threshold: float
    [出力] (final_image, correction_or_None, score_or_None)
      none/passthrough → (image, None, None)
      failed           → (image, "failed", 0.0)
      score>=閾値 label0 → (image, "straight", score)
      score>=閾値 label≠0 → (transpose(image), "rotated", score)
      score<閾値          → (image, "kept", score)
    """
    if result.status == STATUS_PASSTHROUGH:
        return image, None, None
    if result.status == STATUS_FAILED:
        return image, CORRECTION_FAILED, result.score  # 0.0
    if result.score >= threshold:
        if result.label == 0:
            return image, CORRECTION_STRAIGHT, result.score
        return image.transpose(_ROTATE[result.label]), CORRECTION_ROTATED, result.score
    return image, CORRECTION_KEPT, result.score
