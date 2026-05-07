"""モデル削除時の FS 実体クリーンアップシグナル。

DB が 1 次ソース、FS 実体は DB レコードに紐付いた従属物。
レコード削除時に対応するファイルを削除し、孤児ファイルが残らないようにする。

各ハンドラは個別の try/except で囲み、1 つのファイル削除失敗が他のクリーンアップを
ブロックしないようにする。FileNotFoundError は無視する。
"""

import logging
import os
from glob import glob

from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver

from cards.models import BusinessCard, DebugMask, OriginalImage

logger = logging.getLogger(__name__)


def _safe_unlink(path: str, label: str) -> None:
    """[性質] 副作用あり（ファイル削除）。FileNotFoundError は無視。

    [入力] path: 削除対象の絶対パス、label: ログ用ラベル
    [出力] None
    """
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("%s file delete failed %s: %s", label, path, e)


def _safe_rmdir(path: str, label: str) -> None:
    """[性質] 副作用あり（空ディレクトリ削除）。FileNotFoundError は無視。

    [入力] path: 削除対象の絶対パス、label: ログ用ラベル
    [出力] None

    os.rmdir は空ディレクトリ専用。中にファイルが残っていれば OSError を返すので
    強制削除はせず、warning ログを残して処理継続する。
    """
    try:
        os.rmdir(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("%s rmdir failed %s: %s", label, path, e)


@receiver(post_delete, sender=DebugMask)
def _delete_debug_mask_file(sender, instance, **kwargs):
    """DebugMask 削除時に mask_image の FS 実体を削除。"""
    if not instance.mask_image:
        return
    try:
        _safe_unlink(instance.mask_image.path, "DebugMask.mask_image")
    except Exception as exc:
        logger.warning("DebugMask post_delete handler failed: %s", exc)


@receiver(post_delete, sender=BusinessCard)
def _delete_card_image_file(sender, instance, **kwargs):
    """BusinessCard 削除時に card_image の FS 実体を削除。"""
    if not instance.card_image:
        return
    try:
        _safe_unlink(instance.card_image.path, "BusinessCard.card_image")
    except Exception as exc:
        logger.warning("BusinessCard post_delete handler failed: %s", exc)


@receiver(post_delete, sender=OriginalImage)
def _delete_original_image_files(sender, instance, **kwargs):
    """OriginalImage 削除時の FS 実体クリーンアップ。

    削除対象：
      - image_file 本体（MEDIA_ROOT/originals/.../<id>.jpg）
      - DEBUG=True 時の dev 画像（MEDIA_ROOT/debug/**/<id>-*-dev.jpg、glob で走査）
    """
    if instance.image_file:
        try:
            _safe_unlink(instance.image_file.path, "OriginalImage.image_file")
        except Exception as exc:
            logger.warning("OriginalImage image_file cleanup failed: %s", exc)

    try:
        debug_root = os.path.join(settings.MEDIA_ROOT, "debug")
        if os.path.isdir(debug_root):
            pattern = os.path.join(debug_root, "**", f"{instance.id}-*-dev.jpg")
            for p in glob(pattern, recursive=True):
                _safe_unlink(p, "OriginalImage.dev_image")
    except Exception as exc:
        logger.warning("OriginalImage dev image cleanup failed: %s", exc)

    # DebugMask post_delete でファイル群が削除された後の空ディレクトリ
    # （MEDIA_ROOT/debug_masks/<id>/）を削除する。
    try:
        debug_masks_dir = os.path.join(
            settings.MEDIA_ROOT, "debug_masks", str(instance.id)
        )
        if os.path.isdir(debug_masks_dir):
            _safe_rmdir(debug_masks_dir, "OriginalImage.debug_masks_dir")
    except Exception as exc:
        logger.warning("OriginalImage debug_masks dir cleanup failed: %s", exc)
