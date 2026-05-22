let receipts = [];
let selectedId = "";
let initialReceiptId = receiptIdFromPath();

const profileEl = document.querySelector("#profile");
const listEl = document.querySelector("#receipt-list");
const detailEl = document.querySelector("#receipt-detail");

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (response.status === 401) {
    window.location.href = "/login";
    return null;
  }
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function money(receipt) {
  const amount = receipt.amount || "unknown";
  const currency = receipt.currency || "AMD";
  return `${amount} ${currency}`;
}

function renderList() {
  if (!receipts.length) {
    listEl.innerHTML = '<p class="empty">No receipts yet</p>';
    return;
  }
  listEl.innerHTML = receipts
    .map(
      (receipt) => `
        <button class="receipt-row ${receipt.id === selectedId ? "active" : ""}" data-id="${receipt.id}" type="button">
          <strong>${escapeHtml(receipt.merchant || "Unknown merchant")}</strong>
          <span>${escapeHtml(receipt.date || "No date")} · ${escapeHtml(money(receipt))}</span>
          <span>${escapeHtml(receipt.document_type_label || receipt.document_type || "Receipt")}</span>
        </button>
      `
    )
    .join("");
  listEl.querySelectorAll(".receipt-row").forEach((button) => {
    button.addEventListener("click", () => selectReceipt(button.dataset.id));
  });
}

async function selectReceipt(id) {
  selectedId = id;
  if (window.location.pathname !== `/receipts/${encodeURIComponent(id)}`) {
    window.history.pushState({}, "", `/receipts/${encodeURIComponent(id)}`);
  }
  renderList();
  detailEl.innerHTML = '<p class="empty">Loading...</p>';
  const data = await api(`/api/receipts/${encodeURIComponent(id)}`);
  if (!data) return;
  const receipt = data.receipt;
  const items = data.items || [];
  detailEl.innerHTML = `
    <article class="detail-card">
      <section class="summary">
        <h2>${escapeHtml(receipt.merchant || "Unknown merchant")}</h2>
        <p class="meta">${escapeHtml(receipt.date || "No date")} · ${escapeHtml(money(receipt))}</p>
        <p class="meta">${escapeHtml(receipt.receipt_id)}</p>
      </section>
      ${
        receipt.has_image
          ? `<img class="receipt-image" src="/api/receipts/${encodeURIComponent(receipt.id)}/image" alt="Receipt image">`
          : ""
      }
      <section class="items">
        <h3>Items</h3>
        ${
          items.length
            ? items.map(renderItem).join("")
            : '<p class="empty">No items parsed</p>'
        }
      </section>
    </article>
  `;
}

function renderItem(item) {
  const name = item.name_ru || item.name_en || item.name_original || "Item";
  const qty = [item.quantity, item.unit].filter(Boolean).join(" ");
  const total = item.line_total || item.unit_price || "";
  return `
    <div class="item">
      <div>
        <strong>${escapeHtml(name)}</strong>
        <p class="meta">${escapeHtml(qty)}</p>
      </div>
      <span>${escapeHtml(total)}</span>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

document.querySelector("#logout").addEventListener("click", async () => {
  await fetch("/logout", { method: "POST", credentials: "same-origin" });
  window.location.href = "/login";
});

window.addEventListener("popstate", () => {
  const id = receiptIdFromPath() || receipts[0]?.id || "";
  if (id) {
    selectReceipt(id).catch(() => {
      detailEl.innerHTML = '<p class="empty">Receipt unavailable</p>';
    });
  }
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

(async function init() {
  const me = await api("/api/me");
  if (!me) return;
  profileEl.textContent = `${me.telegram_user_id} · ${me.role}`;
  const data = await api("/api/receipts");
  if (!data) return;
  receipts = data.receipts || [];
  selectedId = initialReceiptId || receipts[0]?.id || "";
  renderList();
  if (selectedId) {
    try {
      await selectReceipt(selectedId);
    } catch {
      detailEl.innerHTML = '<p class="empty">Receipt unavailable</p>';
    }
  }
})();

function receiptIdFromPath() {
  const match = window.location.pathname.match(/^\/receipts\/(.+)$/);
  return match ? decodeURIComponent(match[1]) : "";
}
