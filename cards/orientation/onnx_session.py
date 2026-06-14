"""PP-LCNet ONNX セッション（モジュールレベル 1 回ロード・スレッド安全）。

アップロード毎に 6.5MB を再ロードしない（v0.3 §10メモ2）。Django は同時リクエストを
別スレッドで捌くため、SessionOptions でスレッド数を絞り CPU プロバイダを明示して
共有セッションへの同時アクセスの競合・性能劣化を避ける（v0.3 §7・§10）。
"""

import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort

MODEL_PATH = Path(__file__).resolve().parent / "models" / "pp-lcnet_x1_0_doc_ori.onnx"

_session = None
_lock = threading.Lock()


def get_session() -> ort.InferenceSession:
    """[性質] 副作用あり（初回のみ ONNX ロード）/ 共有 InferenceSession を返す（遅延・1回）。"""
    global _session
    if _session is None:
        with _lock:
            if _session is None:
                opts = ort.SessionOptions()
                # 共有セッションへの同時アクセス時の競合・スレッド過多を避ける。
                opts.intra_op_num_threads = 1
                opts.inter_op_num_threads = 1
                _session = ort.InferenceSession(
                    str(MODEL_PATH),
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
    return _session


def run_softmax(tensor: np.ndarray) -> np.ndarray:
    """[性質] 副作用あり（推論）/ NCHW テンソルを推論し (4,) の出力ベクトルを返す。

    モデル出力は既に softmax 済み確率（4値の和≈1）。二重 softmax は禁止（v0.3 §6.2）。
    """
    sess = get_session()
    iname = sess.get_inputs()[0].name
    return sess.run(None, {iname: tensor})[0][0]
