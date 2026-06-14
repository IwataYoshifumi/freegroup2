"""PP-LCNet_x1_0_doc_ori 前処理（検証スクリプト parity_check.py からの移植・書き直し禁止）。

parity 10/10 が成立したのはモデル同梱 inference.yml 正本どおりの前処理だったため。
具体値（resize_short=256 / CenterCrop 224 / /255 / mean・std / NCHW / INTER_LINEAR /
int(x+0.5) 丸め）はハードコード固定（.env 外出ししない）。

[性質] 純関数（DB操作なし・副作用なし）
"""

import cv2
import numpy as np
from PIL import Image

# inference.yml 正本（PreProcess.NormalizeImage）。RGB・ImageNet 平均/分散・scale=1/255。
_MEAN = np.array([0.485, 0.456, 0.406], dtype="float32")
_STD = np.array([0.229, 0.224, 0.225], dtype="float32")
_RESIZE_SHORT = 256
_CROP = 224


def _core(rgb: np.ndarray) -> np.ndarray:
    """[性質] 純関数 / RGB(HWC,uint8) → NCHW float32 テンソル（移植コア・値固定）。"""
    h, w = rgb.shape[:2]
    scale = _RESIZE_SHORT / min(h, w)
    nw, nh = int(w * scale + 0.5), int(h * scale + 0.5)        # PaddleClas 丸め
    img = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top, left = (nh - _CROP) // 2, (nw - _CROP) // 2           # center crop 224
    img = img[top:top + _CROP, left:left + _CROP]
    img = img.astype("float32") / 255.0
    img = (img - _MEAN) / _STD
    return img.transpose(2, 0, 1)[None].astype("float32")       # NCHW


def preprocess_pil(pil_image: Image.Image) -> np.ndarray:
    """[性質] 純関数 / PIL Image（パイプライン入力）→ NCHW float32。RGB 化して移植コアへ。"""
    rgb = np.asarray(pil_image.convert("RGB"))
    return _core(rgb)


def preprocess_path(path: str) -> np.ndarray:
    """[性質] 純関数 / ファイルパス → NCHW float32。日本語パス対応（np.fromfile + cv2.imdecode）。

    cv2.imread は Windows で日本語パスを開けないため使わない（v0.3 §5.2）。
    """
    bgr = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return _core(rgb)
