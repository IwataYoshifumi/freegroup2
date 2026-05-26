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
