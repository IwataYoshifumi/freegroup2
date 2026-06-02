(function () {
  const drawer = document.getElementById('drawer');
  const backdrop = document.querySelector('.app-backdrop');
  const userMenu = document.getElementById('userMenu');
  const modalBackdrop = document.querySelector('.app-modal-backdrop');
  const toast = document.querySelector('.app-toast');
  const loadingOverlay = document.querySelector('.app-loading-overlay');
  let activeModal = null;
  let toastTimer = null;

  function openDrawer() {
    if (!drawer || !backdrop) return;
    drawer.classList.add('is-open');
    backdrop.classList.add('is-open');
  }

  function closeDrawer() {
    if (!drawer || !backdrop) return;
    drawer.classList.remove('is-open');
    backdrop.classList.remove('is-open');
  }

  function toggleUserMenu() {
    if (!userMenu) return;
    userMenu.classList.toggle('is-open');
  }

  function closeUserMenu() {
    if (!userMenu) return;
    userMenu.classList.remove('is-open');
  }

  function toggleCollapsible(event, trigger) {
    const targetId = trigger.getAttribute('data-target');
    const panel = targetId ? document.getElementById(targetId) : trigger.closest('.app-collapsible');
    if (!panel) return;
    const isOpen = panel.classList.toggle('is-open');
    const icon = trigger.querySelector('.app-collapsible__icon');
    trigger.setAttribute('aria-expanded', String(isOpen));
    if (icon) icon.textContent = isOpen ? '▾' : '▸';
  }

  function toggleDrawerSection(event, trigger) {
    const section = trigger.closest('.app-drawer__section');
    if (!section) return;
    const isOpen = section.classList.toggle('is-open');
    const icon = section.querySelector('.app-drawer__section-icon');
    trigger.setAttribute('aria-expanded', String(isOpen));
    if (icon) icon.textContent = isOpen ? '▾' : '▸';
  }

  function openModal(event, trigger) {
    const targetId = trigger.getAttribute('data-target');
    const modal = targetId ? document.getElementById(targetId) : null;
    if (!modal || !modalBackdrop) return;
    activeModal = modal;
    modalBackdrop.hidden = false;
    modal.hidden = false;
    const firstFocus = modal.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (firstFocus) firstFocus.focus();
  }

  function closeModal() {
    if (activeModal) activeModal.hidden = true;
    if (modalBackdrop) modalBackdrop.hidden = true;
    activeModal = null;
  }

  /* ステップ3b：data-preview-url から HTML フラグメントを取得し #previewModal の
     body に流し込んで開く。閉じる動作は既存 closeModal (Escape / 背景 / 閉じるボタン) と共有。 */
  function openPreviewModal(event, trigger) {
    const url = trigger.getAttribute('data-preview-url');
    if (!url) return;
    const modal = document.getElementById('previewModal');
    if (!modal || !modalBackdrop) return;
    const body = modal.querySelector('.app-modal__body');
    if (!body) return;

    /* モーダルを先に表示（既存 openModal の挙動を踏襲、AJAX 中はローディングを見せる） */
    body.innerHTML = '<p>読み込み中…</p>';
    activeModal = modal;
    modalBackdrop.hidden = false;
    modal.hidden = false;

    /* AJAX で HTML フラグメント取得（同一オリジン GET、CSRF トークン不要）。
       LoginRequiredMixin の認証維持のため credentials を同一オリジンに。 */
    fetch(url, { credentials: 'same-origin' })
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.text();
      })
      .then(function (html) {
        body.innerHTML = html;
      })
      .catch(function () {
        body.innerHTML = '<p>読み込みに失敗しました。</p>';
      });
  }

  function showToast() {
    if (!toast) return;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.hidden = true;
    }, 2400);
  }

  function toggleLoading() {
    if (!loadingOverlay) return;
    loadingOverlay.hidden = !loadingOverlay.hidden;
  }

  function copyToClipboard(event, trigger) {
    const targetId = trigger.getAttribute('data-target');
    const el = targetId ? document.getElementById(targetId) : null;
    if (!el) return;
    navigator.clipboard.writeText(el.textContent).then(function () {
      const original = trigger.textContent;
      trigger.textContent = 'コピーしました';
      setTimeout(function () { trigger.textContent = original; }, 1500);
    });
  }

  const actions = {
    'toggle-drawer': openDrawer,
    'close-drawer': closeDrawer,
    'toggle-user-menu': toggleUserMenu,
    'toggle-collapsible': toggleCollapsible,
    'toggle-drawer-section': toggleDrawerSection,
    'open-modal': openModal,
    'close-modal': closeModal,
    'open-preview-modal': openPreviewModal,
    'show-toast': showToast,
    'toggle-loading': toggleLoading,
    'copy-to-clipboard': copyToClipboard
  };

  document.addEventListener('click', function (event) {
    const trigger = event.target.closest('[data-action]');
    const action = trigger ? trigger.getAttribute('data-action') : null;
    if (action && actions[action]) {
      actions[action](event, trigger);
    }

    /* .app-modal 自身（panel の外、余白部分）クリックで閉じる。
       .app-modal-backdrop に data-action="close-modal" を付けても、CSS で
       .app-modal が backdrop より上の z-index なので背景クリックが backdrop に届かない。
       event.target が .app-modal 自身（panel やその子要素ではない、modal の余白）
       のときのみ closeModal を呼ぶ。 */
    if (event.target.classList && event.target.classList.contains('app-modal')) {
      closeModal();
    }

    if (!event.target.closest('.app-user-menu')) {
      closeUserMenu();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeDrawer();
      closeUserMenu();
      closeModal();
    }
  });
})();

/* PC向けツールチップ。テーブルのoverflowに邪魔されないようbody直下に表示する。 */
(function () {
  const canHover = window.matchMedia && window.matchMedia('(hover: hover)').matches;
  if (!canHover) return;

  let tooltip = null;

  function removeTooltip() {
    if (tooltip) {
      tooltip.remove();
      tooltip = null;
    }
  }

  function showTooltip(target) {
    const text = target.getAttribute('data-tooltip');
    if (!text) return;
    removeTooltip();
    tooltip = document.createElement('div');
    tooltip.className = 'app-floating-tooltip';
    tooltip.textContent = text;
    document.body.appendChild(tooltip);
    const rect = target.getBoundingClientRect();
    const tipRect = tooltip.getBoundingClientRect();
    let left = rect.left;
    let top = rect.top - tipRect.height - 8;
    if (left + tipRect.width > window.innerWidth - 12) left = window.innerWidth - tipRect.width - 12;
    if (left < 12) left = 12;
    if (top < 12) top = rect.bottom + 8;
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
  }

  document.addEventListener('mouseover', function (event) {
    const target = event.target.closest('[data-tooltip]');
    if (target) showTooltip(target);
  });

  document.addEventListener('mouseout', function (event) {
    if (event.target.closest('[data-tooltip]')) removeTooltip();
  });

  /* overflow:scroll要素内も拾いやすいよう、windowではなくdocumentのキャプチャフェーズで監視する。 */
  document.addEventListener('scroll', removeTooltip, true);
  window.addEventListener('resize', removeTooltip);
})();

(function () {
  /* 日付・時刻の整形はあくまで入力補助。実在する日付・時刻かどうかの最終バリデーションはDjango Form側で行う。 */
  function formatDateValue(value) {
    const raw = value.replace(/\D/g, '');
    if (raw.length === 8) return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
    return value;
  }

  function formatTimeValue(value) {
    const raw = value.replace(/\D/g, '');
    if (raw.length === 4) return `${raw.slice(0, 2)}:${raw.slice(2, 4)}`;
    return value;
  }

  function formatDateTimeValue(value) {
    const raw = value.replace(/\D/g, '');
    if (raw.length === 12) {
      return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)} ${raw.slice(8, 10)}:${raw.slice(10, 12)}`;
    }
    return value;
  }

  const formatters = [
    ['.js-date-text', formatDateValue],
    ['.js-time-text', formatTimeValue],
    ['.js-datetime-text', formatDateTimeValue]
  ];

  formatters.forEach(([selector, formatter]) => {
    document.querySelectorAll(selector).forEach((input) => {
      input.addEventListener('focus', () => {
        setTimeout(() => input.select(), 0);
      });
      input.addEventListener('blur', () => {
        input.value = formatter(input.value);
      });
    });
  });
})();

/* flatpickrより後にこのapp.jsを読み込むこと。CDN失敗時は通常のTextInputとして動作する。 */
(function () {
  if (!window.flatpickr) return;

  const locale = window.flatpickr.l10ns && window.flatpickr.l10ns.ja ? window.flatpickr.l10ns.ja : undefined;

  window.flatpickr('.js-flatpickr-date', {
    dateFormat: 'Y-m-d',
    allowInput: true,
    locale
  });

  window.flatpickr('.js-flatpickr-time', {
    enableTime: true,
    noCalendar: true,
    dateFormat: 'H:i',
    time_24hr: true,
    allowInput: true,
    locale
  });

  window.flatpickr('.js-flatpickr-datetime', {
    enableTime: true,
    dateFormat: 'Y-m-d H:i',
    time_24hr: true,
    allowInput: true,
    locale
  });
})();

/* ============================================================
 * D-3d-1 + D-3d-6: 共通 AJAX ヘルパー（CSRF + fetch ラッパー + toast 通知）
 *
 * window.appAjax 名前空間で公開：
 *   - window.appAjax.postJson(url, payload) → Promise<{ok, status, data}>
 *   - window.appAjax.showToastMessage(message) → void
 *
 * 既存 showToast() は引数なしで textContent を書き換えない仕様のため、
 * メッセージ付き表示の独自関数を本ブロック内で実装する（既存ブロックは触らない）。
 * ============================================================ */
(function () {
  function getCookie(name) {
    if (!document.cookie) return null;
    const cookies = document.cookie.split(';');
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.substring(0, name.length + 1) === name + '=') {
        return decodeURIComponent(cookie.substring(name.length + 1));
      }
    }
    return null;
  }

  let toastTimer = null;
  function showToastMessage(message) {
    const toast = document.querySelector('.app-toast');
    if (!toast) return;
    toast.textContent = message || '';
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toast.hidden = true;
    }, 2400);
  }

  function errorMessageForStatus(status) {
    if (status === 400) return '入力内容に問題があります';
    if (status === 403) return 'この項目は編集できません';
    if (status === 404) return 'データが見つかりません';
    if (status >= 500 && status < 600) return 'サーバーエラーが発生しました';
    if (status === 0) return '通信エラーが発生しました';
    return 'エラーが発生しました';
  }

  function postJson(url, payload) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || ''
      },
      body: JSON.stringify(payload)
    }).then(function (response) {
      const status = response.status;
      return response.json().then(
        function (data) {
          const success = response.ok && data && data.success === true;
          if (!success) {
            showToastMessage(errorMessageForStatus(status));
            return { ok: false, status: status, data: data };
          }
          return { ok: true, status: status, data: data };
        },
        function () {
          /* JSON パース失敗 */
          showToastMessage(errorMessageForStatus(0));
          return { ok: false, status: 0, data: null };
        }
      );
    }, function () {
      /* ネットワークエラー */
      showToastMessage(errorMessageForStatus(0));
      return { ok: false, status: 0, data: null };
    });
  }

  window.appAjax = {
    postJson: postJson,
    showToastMessage: showToastMessage
  };
})();

/* ============================================================
 * D-3d-2 + D-3d-3: Contact 詳細画面 個別フィールドの「確認 OK」+ 値修正 AJAX
 *
 * テンプレート：templates/contacts/_contact_field.html。
 * edit ラジオは画面内 1 行のみ展開（排他制御）— confirm 選択は他行に影響しない。
 * ============================================================ */
(function () {
  const CONFIRMED_BADGE_HTML =
    '<span class="app-status-badge app-status-badge--success">確認済み</span>';

  function findRow(target) {
    return target.closest('.js-contact-field-row');
  }

  function setHidden(el, hidden) {
    if (!el) return;
    el.hidden = !!hidden;
  }

  function resetRow(row) {
    /* ラジオの選択を解除 + 確定ボタン / 修正フォームを hidden に戻す */
    row.querySelectorAll('.js-contact-field-action').forEach(function (input) {
      input.checked = false;
    });
    setHidden(row.querySelector('.js-contact-field-confirm-btn'), true);
    setHidden(row.querySelector('.js-contact-field-edit-form'), true);
  }

  function applyConfirmedState(row, unconfirmedCount, updatedValue) {
    /* .app-contact-field-actions を丸ごと削除して、リロード時の confirmed 状態
       （テンプレートが actions を出力しない）と一致させる。
       updatedValue は string のみ反映（空文字含む、省略時は値表示を触らない）。 */
    const actionsDiv = row.querySelector('.app-contact-field-actions');
    if (actionsDiv) actionsDiv.remove();

    row.dataset.confidenceState = 'confirmed';
    const slot = row.querySelector('.js-contact-field-badge-slot');
    if (slot) slot.innerHTML = CONFIRMED_BADGE_HTML;

    if (typeof updatedValue === 'string') {
      const valueEl = row.querySelector('.js-contact-field-value');
      if (valueEl) valueEl.textContent = updatedValue;
    }

    if (typeof unconfirmedCount === 'number') {
      const counter = document.querySelector('.js-unconfirmed-count');
      if (counter) counter.textContent = String(unconfirmedCount);
    }
  }

  function onActionChange(input, row) {
    const value = input.value;
    const confirmBtn = row.querySelector('.js-contact-field-confirm-btn');
    const editForm = row.querySelector('.js-contact-field-edit-form');
    if (value === 'confirm') {
      setHidden(confirmBtn, false);
      setHidden(editForm, true);
    } else if (value === 'edit') {
      /* 他行の edit を閉じてから自行を展開（同時 1 行のみ）。 */
      document
        .querySelectorAll('.js-contact-field-row')
        .forEach(function (otherRow) {
          if (otherRow === row) return;
          const otherEditRadio = otherRow.querySelector(
            '.js-contact-field-action[value="edit"]'
          );
          if (otherEditRadio && otherEditRadio.checked) {
            resetRow(otherRow);
          }
        });
      setHidden(confirmBtn, true);
      setHidden(editForm, false);
    }
  }

  function onConfirmClick(btn, row) {
    const contactId = row.dataset.contactId;
    const fieldName = row.dataset.fieldName;
    if (!contactId || !fieldName) return;

    btn.disabled = true;
    const url = '/contacts/' + contactId + '/ajax-confirm-fields/';
    window.appAjax
      .postJson(url, { field_names: [fieldName] })
      .then(function (result) {
        if (result.ok) {
          const count = result.data && result.data.unconfirmed_count;
          applyConfirmedState(row, count);
        }
        /* 失敗時はヘルパー側で toast 表示済み、UI はそのまま操作可能 */
      })
      .finally(function () {
        btn.disabled = false;
      });
  }

  function onUpdateClick(btn, row) {
    /* バリデーションはサーバー側に委ねる（空文字も POST）。
       失敗時は共通ヘルパーが toast を出すだけで、修正フォームは開いたまま維持。 */
    const contactId = row.dataset.contactId;
    const fieldName = row.dataset.fieldName;
    if (!contactId || !fieldName) return;

    const input = row.querySelector('.js-contact-field-edit-input');
    if (!input) return;
    const newValue = input.value;

    btn.disabled = true;
    const url = '/contacts/' + contactId + '/ajax-update-field/';
    window.appAjax
      .postJson(url, { field_name: fieldName, new_value: newValue })
      .then(function (result) {
        if (result.ok) {
          const count = result.data && result.data.unconfirmed_count;
          const updated = result.data && result.data.updated_value;
          applyConfirmedState(row, count, updated);
        }
      })
      .finally(function () {
        btn.disabled = false;
      });
  }

  function onCancelClick(row) {
    resetRow(row);
  }

  document.addEventListener('change', function (event) {
    const input = event.target.closest('.js-contact-field-action');
    if (!input) return;
    const row = findRow(input);
    if (!row) return;
    onActionChange(input, row);
  });

  document.addEventListener('click', function (event) {
    const confirmBtn = event.target.closest('.js-contact-field-confirm-btn');
    if (confirmBtn) {
      const row = findRow(confirmBtn);
      if (row) onConfirmClick(confirmBtn, row);
      return;
    }

    const updateBtn = event.target.closest('.js-contact-field-update-btn');
    if (updateBtn) {
      const row = findRow(updateBtn);
      if (row) onUpdateClick(updateBtn, row);
      return;
    }

    const cancelBtn = event.target.closest('.js-contact-field-cancel-btn');
    if (cancelBtn) {
      const row = findRow(cancelBtn);
      if (row) onCancelClick(row);
    }
  });

  // ----- full_name 補助組み立て（Phase D §3.7、仕様書 §6.5 / §11.9.5）-----
  // last_name / first_name / other_name_parts / name_order の変更に追従して full_name を
  // 補助組み立てする。ユーザーが full_name を直接編集したら以後の自動組み立てを停止する
  // （手入力尊重）。ブラウザストレージは使わずメモリ内フラグのみで管理する。
  let nameComposeManual = false;

  function composeFullNameField() {
    if (nameComposeManual) return;
    const full = document.querySelector('.js-name-full');
    if (!full) return;
    const valueOf = function (selector) {
      const el = document.querySelector(selector);
      return el ? el.value.trim() : '';
    };
    const last = valueOf('.js-name-last');
    const first = valueOf('.js-name-first');
    const other = valueOf('.js-name-other');
    const orderEl = document.querySelector('.js-name-order');
    const order = orderEl ? orderEl.value : '';
    let result;
    if (order === 'last_first') {
      result = [last, first].filter(Boolean).join(' ');
    } else if (order === 'first_last') {
      result = [first, other, last].filter(Boolean).join(' ');
    } else if (order === 'single') {
      result = last || first;
    } else {
      return; // other / 未選択 は自動組み立てしない（手入力のみ、仕様書 §6.5）
    }
    full.value = result;
  }

  document.addEventListener('input', function (event) {
    const target = event.target;
    if (!target || !target.classList) return;
    if (target.classList.contains('js-name-full')) {
      nameComposeManual = true; // ユーザーが直接編集 → 以後は補助しない
      return;
    }
    if (
      target.classList.contains('js-name-last') ||
      target.classList.contains('js-name-first') ||
      target.classList.contains('js-name-other')
    ) {
      composeFullNameField();
    }
  });

  document.addEventListener('change', function (event) {
    const target = event.target;
    if (target && target.classList && target.classList.contains('js-name-order')) {
      composeFullNameField();
    }
  });
})();

/* ============================================================
 * Phase F1: ContactSns InlineFormSet の動的追加・削除（仕様書 §11.6.7）
 *
 * partial: templates/contacts/_contact_sns_formset.html。
 * 「＋追加」: <template> から空行を複製し __prefix__ を TOTAL_FORMS の現在値で
 *            置換して挿入、TOTAL_FORMS をインクリメント。
 * 「×削除」: 既存行（hidden id に値あり = DB 由来）なら DELETE チェックを ON にして
 *            行を隠す（POST で削除反映）。新規行（id 空）なら DOM ごと除去する
 *            （TOTAL_FORMS は据え置き。欠番のインデックスは Django 側で空フォーム扱い
 *            となり empty_permitted で無視されるため、再採番は不要）。
 * ============================================================ */
(function () {
  function mgmtInput(prefix, key) {
    return document.querySelector('[name="' + prefix + '-' + key + '"]');
  }

  function onAdd(btn) {
    const block = btn.closest('.js-sns-formset');
    if (!block) return;
    const prefix = block.getAttribute('data-prefix');
    const container = block.querySelector('.js-sns-formset-container');
    const template = block.querySelector('.js-sns-empty-form-template');
    const total = mgmtInput(prefix, 'TOTAL_FORMS');
    if (!container || !template || !total) return;
    let index = parseInt(total.value, 10);
    if (isNaN(index)) index = 0;
    const html = template.innerHTML.replace(/__prefix__/g, index);
    const temp = document.createElement('div');
    temp.innerHTML = html.trim();
    const row = temp.firstElementChild;
    if (!row) return;
    container.appendChild(row);
    total.value = index + 1;
  }

  function onRemove(btn) {
    const row = btn.closest('.js-sns-formset-row');
    if (!row) return;
    /* sns_id のフィールド名は "...-sns_id"（末尾 _id）なので "-id"（PK）にはマッチしない。 */
    const idInput = row.querySelector('input[name$="-id"]');
    const isExisting = idInput && idInput.value;
    if (isExisting) {
      const del = row.querySelector('input[type="checkbox"][name$="-DELETE"]');
      if (del) del.checked = true;
      row.hidden = true;
    } else {
      row.remove();
    }
  }

  document.addEventListener('click', function (event) {
    const addBtn = event.target.closest('.js-sns-add-btn');
    if (addBtn) { onAdd(addBtn); return; }
    const removeBtn = event.target.closest('.js-sns-remove-btn');
    if (removeBtn) { onRemove(removeBtn); return; }
  });
})();

/* ============================================================
 * Phase 1c-β-2b: タグ選択 UI 動的挙動（仕様書 rev6 §4.4）
 *
 * 対象：mailings/_tag_selection.html（create mode のみ。add_by_tag /
 *       remove_by_tag mode は β-3 で同関数を mode 別に拡張する想定）
 *
 * 主な機能：
 *   - カテゴリアコーディオン初期化（選択 0 件は折りたたみ）+ 選択数バッジ
 *   - 演算切替（OR/AND）でプレビュー再発火
 *   - 含むタグ・除外タグの相互排他（双方向、後の操作優先）
 *   - 横断除外モーダル（検索可能なタグ追加 + チップ削除）
 *   - preview-v2 への JSON POST（debounce 300ms 固定 §9.2-40、スピナー）
 *   - 警告ダイアログ（§4.4.2 の 4 条件）
 *   - js-current-conditions JSON から UI 状態を復元（戻り時）
 *
 * 設計指針（指示書 §3.0 / §3.1）：
 *   - data-tag-selection-root を持つページでのみ動作（他画面に影響なし）
 *   - CSRF は update-meta 流儀（form 内 [name=csrfmiddlewaretoken]）に揃える
 *   - preview-v2 のレスポンスは {ok:false,...} 以外を成功扱い（β-1 確定形式）
 *     のため appAjax.postJson は使わず、本ブロック内に専用 fetch を書く
 *   - 含むタグ・除外タグの static checkbox（β-2a 骨格）は JS off フォールバック
 *     として残し、JS はそれを操作する補助役
 * ============================================================ */
(function () {
  const root = document.querySelector('[data-tag-selection-root]');
  if (!root) return;

  const form = document.getElementById('js-tag-conditions-form');
  if (!form) return;

  const mode = root.dataset.mode || 'create';
  const previewUrl = root.dataset.previewUrl;
  const previewCountEl = document.getElementById('js-preview-count');
  const previewSamplesEl = document.getElementById('js-preview-samples');
  const submitBtn = form.querySelector('.js-tag-selection-submit');
  const chipsEl = root.querySelector('.js-global-exclude-chips');
  const emptyEl = root.querySelector('.js-global-exclude-empty');
  const fallbackDetails = root.querySelector('.js-global-exclude-fallback');
  const modalSearchInput = document.getElementById('js-global-exclude-modal-search');
  const modalListEl = document.getElementById('js-global-exclude-modal-list');

  let debounceTimer = null;
  let lastPreviewCount = null;
  let lastPreviewNewCount = null;
  let lastPreviewInListCount = null;
  let inflightController = null;

  /* ---------- ヘルパー ---------- */
  function getCsrf() {
    const input = form.querySelector('[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------- conditions の UI ⇄ dict 変換 ---------- */
  function buildConditions() {
    const categories = [];
    form.querySelectorAll('.js-category-block').forEach(function (block) {
      const catId = block.dataset.categoryId;
      const includeIds = Array.prototype.map.call(
        block.querySelectorAll('.js-include-checkbox:checked'),
        function (cb) { return cb.value; }
      );
      const excludeIds = Array.prototype.map.call(
        block.querySelectorAll('.js-exclude-checkbox:checked'),
        function (cb) { return cb.value; }
      );
      const opSel = block.querySelector('.js-operator-select');
      const operator = opSel ? opSel.value : 'OR';
      if (includeIds.length === 0 && excludeIds.length === 0) {
        /* 空カテゴリは送らない（サーバ側は空 include を「無視」、§4.1.4） */
        return;
      }
      categories.push({
        category_id: catId,
        operator: operator,
        include_tag_ids: includeIds,
        exclude_tag_ids: excludeIds
      });
    });
    const globalExclude = Array.prototype.map.call(
      form.querySelectorAll('.js-global-exclude-checkbox:checked'),
      function (cb) { return cb.value; }
    );
    return { categories: categories, global_exclude_tag_ids: globalExclude };
  }

  function applyCurrentConditions(current) {
    if (!current || typeof current !== 'object') return;
    (current.categories || []).forEach(function (cat) {
      const block = form.querySelector(
        '.js-category-block[data-category-id="' + cat.category_id + '"]'
      );
      if (!block) return;
      (cat.include_tag_ids || []).forEach(function (tagId) {
        const cb = block.querySelector(
          '.js-include-checkbox[data-tag-id="' + tagId + '"]'
        );
        if (cb) cb.checked = true;
      });
      (cat.exclude_tag_ids || []).forEach(function (tagId) {
        const cb = block.querySelector(
          '.js-exclude-checkbox[data-tag-id="' + tagId + '"]'
        );
        if (cb) cb.checked = true;
      });
      const opSel = block.querySelector('.js-operator-select');
      if (opSel && cat.operator) opSel.value = cat.operator;
    });
    (current.global_exclude_tag_ids || []).forEach(function (tagId) {
      const cb = form.querySelector(
        '.js-global-exclude-checkbox[data-tag-id="' + tagId + '"]'
      );
      if (cb) cb.checked = true;
    });
  }

  /* ---------- アコーディオン初期化 + 選択数バッジ更新 ----------
     注：`.app-status-badge` は CSS で `display: inline-flex` を持つため、
     HTML5 の `hidden` 属性（暗黙 `display: none`）は CSS に上書きされる。
     さらに `.app-status-badge[hidden] { display: none }` を CSS に追加して
     `[hidden]` 属性を効かせている都合、show パスでは textContent と
     `style.display` の更新だけでは足りず、`removeAttribute('hidden')` で
     CSS の `[hidden]` セレクタも解除する必要がある。
  */
  function refreshCategoryBadge(block) {
    const catId = block.dataset.categoryId;
    const total = parseInt(block.dataset.totalTags || '0', 10);
    const includeCount = block.querySelectorAll('.js-include-checkbox:checked').length;
    const badge = block.querySelector('.js-category-count[data-category-id="' + catId + '"]');
    if (!badge) return;
    if (includeCount > 0) {
      badge.textContent = '選択 ' + includeCount + ' / ' + total;
      badge.removeAttribute('hidden');
      badge.style.display = '';
    } else {
      badge.textContent = '';
      badge.setAttribute('hidden', '');
      badge.style.display = 'none';
    }
  }

  function initAccordion() {
    form.querySelectorAll('.js-category-block').forEach(function (block) {
      const hasSelection =
        block.querySelectorAll('.js-include-checkbox:checked').length > 0 ||
        block.querySelectorAll('.js-exclude-checkbox:checked').length > 0;
      /* 選択 0 件のカテゴリは折りたたむ（JS off では open のまま、両層） */
      if (!hasSelection) block.removeAttribute('open');
      refreshCategoryBadge(block);
    });
  }

  /* ---------- 含む/除外 相互排他（双方向、論点 2 採用） ---------- */
  function handleExclusiveCheck(changedEl) {
    const tagId = changedEl.dataset.tagId;
    const catId = changedEl.dataset.categoryId;
    if (!tagId || !catId || !changedEl.checked) return;
    const block = form.querySelector('.js-category-block[data-category-id="' + catId + '"]');
    if (!block) return;
    if (changedEl.classList.contains('js-include-checkbox')) {
      const exclude = block.querySelector(
        '.js-exclude-checkbox[data-tag-id="' + tagId + '"]'
      );
      if (exclude && exclude.checked) exclude.checked = false;
    } else if (changedEl.classList.contains('js-exclude-checkbox')) {
      const include = block.querySelector(
        '.js-include-checkbox[data-tag-id="' + tagId + '"]'
      );
      if (include && include.checked) include.checked = false;
    }
  }

  /* ---------- 横断除外チップの再描画 ---------- */
  function refreshGlobalExcludeChips() {
    if (!chipsEl) return;
    const checked = form.querySelectorAll('.js-global-exclude-checkbox:checked');
    chipsEl.innerHTML = '';
    if (checked.length === 0) {
      if (emptyEl) emptyEl.hidden = false;
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    Array.prototype.forEach.call(checked, function (cb) {
      const chip = document.createElement('span');
      chip.className = 'app-status-badge app-status-badge--warning';
      chip.style.display = 'inline-flex';
      chip.style.alignItems = 'center';
      chip.style.gap = '6px';
      chip.style.padding = '4px 8px';
      const catName = cb.dataset.tagCategoryName || '';
      const tagName = cb.dataset.tagName || '';
      chip.innerHTML =
        '<span style="font-size:0.8em; color:#64748b;">[' + escapeHtml(catName) + ']</span>' +
        '<span>' + escapeHtml(tagName) + '</span>';
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'js-global-exclude-chip-remove';
      del.dataset.tagId = cb.dataset.tagId;
      del.setAttribute('aria-label', '削除');
      del.style.background = 'transparent';
      del.style.border = 'none';
      del.style.cursor = 'pointer';
      del.style.color = '#64748b';
      del.style.padding = '0 0 0 2px';
      del.innerHTML = '×';
      chip.appendChild(del);
      chipsEl.appendChild(chip);
    });
  }

  function handleChipRemoveClick(event) {
    const btn = event.target.closest('.js-global-exclude-chip-remove');
    if (!btn) return;
    const tagId = btn.dataset.tagId;
    const cb = form.querySelector(
      '.js-global-exclude-checkbox[data-tag-id="' + tagId + '"]'
    );
    if (cb && cb.checked) {
      cb.checked = false;
      refreshGlobalExcludeChips();
      schedulePreview();
    }
  }

  /* ---------- 横断除外モーダル ---------- */
  function renderModalList(filter) {
    if (!modalListEl) return;
    const norm = (filter || '').toLowerCase().trim();
    modalListEl.innerHTML = '';
    const all = form.querySelectorAll('.js-global-exclude-checkbox');
    let visibleCount = 0;
    Array.prototype.forEach.call(all, function (cb) {
      const tagName = (cb.dataset.tagName || '').toLowerCase();
      const catName = (cb.dataset.tagCategoryName || '').toLowerCase();
      if (norm && tagName.indexOf(norm) === -1 && catName.indexOf(norm) === -1) return;
      const row = document.createElement('div');
      row.style.display = 'flex';
      row.style.alignItems = 'center';
      row.style.justifyContent = 'space-between';
      row.style.padding = '8px 12px';
      row.style.borderBottom = '1px solid #f1f5f9';
      const label = document.createElement('span');
      label.innerHTML =
        '<span style="color:#64748b; font-size:0.85em;">[' +
        escapeHtml(cb.dataset.tagCategoryName || '') + ']</span> ' +
        escapeHtml(cb.dataset.tagName || '');
      row.appendChild(label);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'app-btn app-btn--sm js-global-exclude-modal-add';
      btn.dataset.tagId = cb.dataset.tagId;
      if (cb.checked) {
        btn.classList.add('app-btn--secondary');
        btn.disabled = true;
        btn.textContent = '追加済み';
      } else {
        btn.classList.add('app-btn--primary');
        btn.textContent = '追加';
      }
      row.appendChild(btn);
      modalListEl.appendChild(row);
      visibleCount++;
    });
    if (visibleCount === 0) {
      const empty = document.createElement('div');
      empty.className = 'app-muted';
      empty.style.padding = '12px';
      empty.textContent = '該当タグがありません。';
      modalListEl.appendChild(empty);
    }
  }

  function handleModalAddClick(event) {
    const btn = event.target.closest('.js-global-exclude-modal-add');
    if (!btn) return;
    const tagId = btn.dataset.tagId;
    const cb = form.querySelector(
      '.js-global-exclude-checkbox[data-tag-id="' + tagId + '"]'
    );
    if (!cb || cb.checked) return;
    cb.checked = true;
    /* 含むタグとの競合は注記のみ（論点 3 採用、抽出式に任せる） */
    const includeConflict = form.querySelector(
      '.js-include-checkbox[data-tag-id="' + tagId + '"]:checked'
    );
    if (includeConflict && window.appAjax && window.appAjax.showToastMessage) {
      window.appAjax.showToastMessage(
        '横断除外と含むタグに同じタグが指定されています。横断除外が優先されます。'
      );
    }
    refreshGlobalExcludeChips();
    renderModalList(modalSearchInput ? modalSearchInput.value : '');
    schedulePreview();
  }

  /* ---------- プレビュー AJAX（debounce 300ms 固定、§9.2-40） ---------- */
  function setSpinnerVisible(visible) {
    if (!previewCountEl) return;
    if (visible) {
      previewCountEl.innerHTML =
        '<span class="app-loading__spinner" style="display:inline-block; vertical-align:middle;"></span>' +
        ' <span style="margin-left:8px; color:#64748b;">計算中…</span>';
    }
  }

  function renderPreview(data) {
    if (previewCountEl) {
      const total = (typeof data.total_count === 'number') ? data.total_count : 0;
      if (mode === 'add_by_tag') {
        /* 追加モード（§4.6.1）：新規追加対象 N / 抽出 X / 既登録 Y。
           list_id 指定時は new_count / already_in_list が数値で返る（β-1 §4.3.3）。 */
        const newC = (typeof data.new_count === 'number') ? data.new_count : 0;
        const already = (typeof data.already_in_list === 'number') ? data.already_in_list : 0;
        previewCountEl.innerHTML =
          '新規追加対象 <strong>' + newC + '</strong> 件 ' +
          '<span style="color:#64748b; font-size:0.9em;">' +
          '（抽出 ' + total + ' 件 / 既登録 ' + already + ' 件）</span>';
      } else if (mode === 'remove_by_tag') {
        /* 除外モード（§4.6.5.2）：除外対象 N / 抽出 X / リスト外 Y。
           中立別名 in_list_count（=除外対象）/ out_of_list_count（=リスト外）を読む
           （§3.1 / §9.2-46〜48、new_count/already_in_list と値は同じだが命名で
           用途を明示することで配線ミスを構造的に防ぐ）。
           「除外対象 N 件」は赤太字で強調（§4.6.5.4-4）。 */
        const inList = (typeof data.in_list_count === 'number') ? data.in_list_count : 0;
        const outList = (typeof data.out_of_list_count === 'number') ? data.out_of_list_count : 0;
        previewCountEl.innerHTML =
          '除外対象 <strong style="color:#991b1b;">' + inList + '</strong> 件 ' +
          '<span style="color:#64748b; font-size:0.9em;">' +
          '（抽出 ' + total + ' 件 / リスト外 ' + outList + ' 件）</span>';
      } else {
        previewCountEl.textContent = '抽出 ' + total + ' 件';
      }
    }
    if (!previewSamplesEl) return;
    previewSamplesEl.innerHTML = '';
    const samples = data.samples || [];
    if (samples.length === 0) {
      previewSamplesEl.innerHTML =
        '<div class="app-muted" style="margin-top:4px; font-size:0.9em;">サンプル：該当なし</div>';
      return;
    }
    const wrap = document.createElement('div');
    wrap.className = 'app-table-wrapper';
    wrap.style.marginTop = '4px';
    const table = document.createElement('table');
    table.className = 'app-table app-table--nowrap';
    const thead = document.createElement('thead');
    thead.innerHTML =
      '<tr><th>氏名</th><th>会社</th><th>役職</th><th>部署</th>' +
      '<th>住所</th><th style="min-width:240px;">メール</th></tr>';
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    samples.forEach(function (s) {
      const tr = document.createElement('tr');
      const unsubBadge = s.is_unsubscribed
        ? ' <span class="app-status-badge app-status-badge--warning js-unsubscribed-badge" ' +
          'style="margin-left:6px; font-size:0.85em;" ' +
          'title="メール配信停止フラグが立っています">退会済み</span>'
        : '';
      tr.innerHTML =
        '<td>' + escapeHtml(s.name || '(氏名なし)') + unsubBadge + '</td>' +
        '<td>' + escapeHtml(s.company || '-') + '</td>' +
        '<td>' + escapeHtml(s.title || '-') + '</td>' +
        '<td>' + escapeHtml(s.department || '-') + '</td>' +
        '<td>' + escapeHtml(s.address || '-') + '</td>' +
        '<td style="font-weight:bold;">' + escapeHtml(s.email || '-') + '</td>';
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    previewSamplesEl.appendChild(wrap);
  }

  function fetchPreview() {
    if (!previewUrl) return;
    /* 進行中のリクエストを破棄して直近結果のみ採用 */
    if (inflightController) inflightController.abort();
    inflightController = new AbortController();
    const controller = inflightController;
    setSpinnerVisible(true);
    const conditions = buildConditions();
    /* mode 別の list_id 渡し：create は null、add_by_tag/remove_by_tag は β-3 で
       data-list-id を読む（mode 非依存に書ける部分の汎用化、§3.8） */
    const listId = root.dataset.listId || null;
    return fetch(previewUrl, {
      method: 'POST',
      credentials: 'same-origin',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrf()
      },
      body: JSON.stringify({ list_id: listId, conditions: conditions })
    }).then(function (resp) {
      return resp.json().then(function (data) {
        if (controller !== inflightController) return; /* 競合：古い結果は捨てる */
        if (!resp.ok || (data && data.ok === false)) {
          if (previewCountEl) previewCountEl.textContent = '-';
          if (previewSamplesEl) {
            previewSamplesEl.innerHTML =
              '<div class="app-message app-message--error" style="margin-top:4px;">' +
              escapeHtml((data && data.message) || ('プレビューに失敗しました (HTTP ' + resp.status + ')')) +
              '</div>';
          }
          lastPreviewCount = null;
          lastPreviewNewCount = null;
          lastPreviewInListCount = null;
        } else {
          lastPreviewCount = (typeof data.total_count === 'number') ? data.total_count : null;
          /* mode 別に警告ダイアログの参照件数を保存：
             - add_by_tag：new_count（新規追加対象、既登録は重複除外、§4.6.1）
             - remove_by_tag：in_list_count（除外対象＝抽出 ∩ リスト内、§4.6.5.2）
                              中立別名を読む。new_count と in_list_count は別物として扱う。 */
          lastPreviewNewCount = (typeof data.new_count === 'number') ? data.new_count : null;
          lastPreviewInListCount = (typeof data.in_list_count === 'number') ? data.in_list_count : null;
          renderPreview(data);
        }
        updateSubmitDisabledState();
      });
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return; /* 競合キャンセル、無視 */
      if (previewCountEl) previewCountEl.textContent = '-';
      if (previewSamplesEl) {
        previewSamplesEl.innerHTML =
          '<div class="app-message app-message--error" style="margin-top:4px;">通信エラーが発生しました。</div>';
      }
      lastPreviewCount = null;
      lastPreviewNewCount = null;
      lastPreviewInListCount = null;
    });
  }

  function schedulePreview() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(fetchPreview, 300);
    /* デバウンス開始時にスピナーを出す（実 fetch は 300ms 後だが UI フィードバックを優先） */
    setSpinnerVisible(true);
    updateSubmitDisabledState();
  }

  /* ---------- バリデーション / 警告ダイアログ（§4.4.2） ---------- */
  function hasAnyInclude() {
    return form.querySelectorAll('.js-include-checkbox:checked').length > 0;
  }

  function hasAnyGlobalExclude() {
    return form.querySelectorAll('.js-global-exclude-checkbox:checked').length > 0;
  }

  function updateSubmitDisabledState() {
    if (!submitBtn) return;
    /* 含む 0 件 + 横断除外 0 件 → disabled（サーバ側保険と二層防御） */
    const isEmpty = !hasAnyInclude() && !hasAnyGlobalExclude();
    submitBtn.disabled = isEmpty;
  }

  function handleSubmit(event) {
    /* §4.4.2 警告ダイアログ 4 条件。mode により参照件数と文言が変わる：
       - create：lastPreviewCount（total_count）= 抽出件数
       - add_by_tag：lastPreviewNewCount（new_count）= 新規追加対象（既登録は重複除外、§4.6.1）
       - remove_by_tag：lastPreviewInListCount（in_list_count）= 除外対象（抽出 ∩ リスト内、§4.6.5.2）
       「除外を確定」押下時の二重確認ダイアログは _member_confirmation.html 側で
       別途 submit イベントに confirm() を仕掛ける（§4.6.5.4-8、本処理は 1-L 側）。
    */
    const includeOn = hasAnyInclude();
    const globalOn = hasAnyGlobalExclude();
    const isAddByTag = (mode === 'add_by_tag');
    const isRemoveByTag = (mode === 'remove_by_tag');
    let targetCount;
    if (isAddByTag) targetCount = lastPreviewNewCount;
    else if (isRemoveByTag) targetCount = lastPreviewInListCount;
    else targetCount = lastPreviewCount;
    const n = (typeof targetCount === 'number') ? targetCount : '?';
    if (!includeOn && !globalOn) {
      /* disabled で来ないはずだが保険 */
      event.preventDefault();
      return;
    }
    if (!includeOn && globalOn) {
      let msg;
      if (isAddByTag) {
        msg = '全 Person から除外条件を適用します（新規追加対象 ' + n + ' 件）。続けますか？';
      } else if (isRemoveByTag) {
        msg = '全 Person から除外条件を適用します（除外対象 ' + n + ' 件）。続けますか？';
      } else {
        msg = '全 Person から除外条件を適用します（対象 ' + n + ' 件）。続けますか？';
      }
      if (!window.confirm(msg)) { event.preventDefault(); return; }
      return;
    }
    if (includeOn && targetCount === 0) {
      let msg;
      if (isAddByTag) {
        msg = '新規追加対象が 0 人です（抽出結果はすべて既登録、または抽出 0 件）。続けますか？';
      } else if (isRemoveByTag) {
        msg = '除外対象が 0 人です（抽出結果はすべてリスト外、または抽出 0 件）。続けますか？';
      } else {
        msg = '対象 Person が 0 人です。続けますか？';
      }
      if (!window.confirm(msg)) {
        event.preventDefault();
        return;
      }
      return;
    }
    /* 通常遷移（警告なし）：そのまま POST */
  }

  /* ---------- イベント結線 ---------- */
  form.addEventListener('change', function (event) {
    const target = event.target;
    if (
      target.classList.contains('js-include-checkbox') ||
      target.classList.contains('js-exclude-checkbox')
    ) {
      handleExclusiveCheck(target);
      const block = target.closest('.js-category-block');
      if (block) refreshCategoryBadge(block);
      schedulePreview();
      return;
    }
    if (target.classList.contains('js-operator-select')) {
      schedulePreview();
      return;
    }
    if (target.classList.contains('js-global-exclude-checkbox')) {
      refreshGlobalExcludeChips();
      schedulePreview();
      return;
    }
  });

  if (chipsEl) chipsEl.addEventListener('click', handleChipRemoveClick);
  if (modalListEl) modalListEl.addEventListener('click', handleModalAddClick);
  if (modalSearchInput) {
    modalSearchInput.addEventListener('input', function () {
      renderModalList(modalSearchInput.value);
    });
    /* モーダルが開かれた時点で再描画（既存 app-modal の openModal フックが
       無いので、グローバル click で「タグを追加」ボタンを拾った瞬間にも描画する） */
    document.addEventListener('click', function (event) {
      const opener = event.target.closest(
        '[data-action="open-modal"][data-target="globalExcludeModal"]'
      );
      if (opener) {
        modalSearchInput.value = '';
        renderModalList('');
        /* フォーカスは既存 openModal が当てるので追加処理は不要 */
      }
    });
  }

  form.addEventListener('submit', handleSubmit);

  /* ---------- 初期化 ---------- */
  function initFromSessionConditions() {
    const dataEl = document.getElementById('js-current-conditions');
    if (!dataEl) return;
    try {
      const current = JSON.parse(dataEl.textContent || 'null');
      applyCurrentConditions(current);
    } catch (e) { /* 不正 JSON は無視、空状態で続行 */ }
  }

  initFromSessionConditions();
  initAccordion();
  refreshGlobalExcludeChips();
  updateSubmitDisabledState();
  /* 初期プレビューを 1 回発火（条件が空なら 0 件 + samples=[] が返るだけ） */
  fetchPreview();
})();

/* ============================================================
 * Campaign 編集フォーム：送信元方式トグルと差出人欄の連動補完
 *
 * 「メール作成者」を選んだ瞬間に sender_email / sender_name が空欄なら、
 * <form data-creator-email=... data-creator-name=...> から拾ったログインユーザー値で
 * 補完する。既に値が入っている欄は触らない（手入力尊重）。
 * 「メルマガ」側への切替時は何もしない（メルマガ用差出人はユーザー入力に任せる）。
 * ============================================================ */
(function () {
  document.addEventListener('change', function (event) {
    const radio = event.target;
    if (!radio || radio.tagName !== 'INPUT' || radio.type !== 'radio') return;
    if (radio.name !== 'sender_mode' || radio.value !== 'creator' || !radio.checked) return;
    const form = radio.closest('form');
    if (!form) return;
    const userEmail = (form.dataset.creatorEmail || '').trim();
    const userName = (form.dataset.creatorName || '').trim();
    const emailInput = form.querySelector('input[name="sender_email"]');
    const nameInput = form.querySelector('input[name="sender_name"]');
    if (emailInput && userEmail && !emailInput.value.trim()) {
      emailInput.value = userEmail;
    }
    if (nameInput && userName && !nameInput.value.trim()) {
      nameInput.value = userName;
    }
  });
})();

/* 人物一覧：列見出しクリックによる「現在ページ内のDOMソート」（クライアント側）。
   - サーバー側の多段フォームソート（?sort=）とは完全に別系統。URLは書き換えず、サーバーにも送らず、
     リロードもしない。旧 _sort_script の URL クエリ書き換え方式とは別物（混同しない）。
   - 並べ替え対象は .js-person-page-sort テーブルの tbody 行（＝現在ページに出ている行）のみ。
   - 状態遷移：昇順 → 降順 → 解除（解除でサーバー返却の元順へ戻す）の3状態。
   - 値が両方とも数値とみなせる列は数値比較、それ以外は日本語ロケールの文字列比較。 */
(function () {
  function parseNum(s) {
    if (!/^-?[\d,]+(\.\d+)?$/.test(s)) return null;
    var n = Number(s.replace(/,/g, ''));
    return isFinite(n) ? n : null;
  }
  function cellText(row, idx) {
    var cell = row.cells[idx];
    if (!cell) return '';
    // data-sort-key があれば比較に優先（例：氏名列はカタカナ読み phonetic_name で五十音順）。
    // 無い列は従来どおり表示テキストで比較＝挙動不変。
    var key = cell.getAttribute('data-sort-key');
    if (key !== null) return key.trim();
    return cell.textContent.trim();
  }
  function initTable(table) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    // サーバー返却順を保存（解除時に戻すため）。
    Array.prototype.forEach.call(tbody.rows, function (row, i) {
      row.dataset.pageSortOrig = i;
    });
    var headers = Array.prototype.slice.call(table.querySelectorAll('th[data-sort-col]'));
    var current = null; // { th, dir }

    headers.forEach(function (th) {
      th.addEventListener('click', function () {
        var colIndex = th.cellIndex;
        var dir;
        if (current && current.th === th) {
          dir = current.dir === 'asc' ? 'desc' : 'none';
        } else {
          dir = 'asc';
        }
        headers.forEach(function (h) { h.removeAttribute('data-sort-dir'); });

        var rows = Array.prototype.slice.call(tbody.rows);
        if (dir === 'none') {
          rows.sort(function (a, b) {
            return (+a.dataset.pageSortOrig) - (+b.dataset.pageSortOrig);
          });
          current = null;
        } else {
          th.setAttribute('data-sort-dir', dir);
          var mul = dir === 'asc' ? 1 : -1;
          rows.sort(function (a, b) {
            var av = cellText(a, colIndex);
            var bv = cellText(b, colIndex);
            var an = parseNum(av);
            var bn = parseNum(bv);
            var cmp;
            if (an !== null && bn !== null) {
              cmp = an - bn;
            } else {
              cmp = av.localeCompare(bv, 'ja');
            }
            return cmp * mul;
          });
          current = { th: th, dir: dir };
        }
        rows.forEach(function (r) { tbody.appendChild(r); });
      });
    });
  }
  function init() {
    document.querySelectorAll('.js-person-page-sort').forEach(initTable);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
