const API_BASE = "/api";
const CURRENCY = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0
});

const DATE_TIME = new Intl.DateTimeFormat("ru-RU", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});

function parseNum(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function isSameDay(d1, d2) {
  return d1.getFullYear() === d2.getFullYear() && d1.getMonth() === d2.getMonth() && d1.getDate() === d2.getDate();
}

function formatMoney(value) {
  return CURRENCY.format(value || 0);
}

function findProduct(state, id) {
  return state.products.find((item) => item.id === id);
}

function getShipmentAmount(shipment) {
  const items = Array.isArray(shipment.items) ? shipment.items : [];
  return items.reduce((sum, item) => sum + parseNum(item.amount), 0);
}

function getShipmentItemsCount(shipment) {
  const items = Array.isArray(shipment.items) ? shipment.items : [];
  return items.length;
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let state = {
  products: [],
  receipts: [],
  shipments: []
};

let currentMe = null;
let chartPeriod = "day";
let toastTimer = null;
let pickerTarget = "receipt";
let productSearchQuery = "";
let pickerSearchQuery = "";
let shipmentDraft = { items: [] };

const screenButtons = Array.from(document.querySelectorAll(".main-nav__item"));
const screens = Array.from(document.querySelectorAll(".screen"));
const periodButtons = Array.from(document.querySelectorAll(".period-btn"));
const openPickerButtons = Array.from(document.querySelectorAll("[data-role='open-product-picker']"));
const productForm = document.getElementById("product-form");
const productFormWrap = document.getElementById("product-form-wrap");
const openProductFormBtn = document.getElementById("open-product-form");
const productsSearchInput = document.getElementById("products-search");
const quickProductForm = document.getElementById("quick-product-form");
const receiptForm = document.getElementById("receipt-form");
const shipmentForm = document.getElementById("shipment-form");
const addShipmentLineBtn = document.getElementById("add-shipment-line");
const shipmentLinesTable = document.getElementById("shipment-lines-table");
const shipmentDraftTotal = document.getElementById("shipment-draft-total");
const productsTable = document.getElementById("products-table");
const pickerProductsTable = document.getElementById("picker-products-table");
const pickerSearchInput = document.getElementById("picker-search");
const receiptsTable = document.getElementById("receipts-table");
const shipmentsTable = document.getElementById("shipments-table");
const receiptProductInput = document.getElementById("receipt-product-id");
const shipmentProductInput = document.getElementById("shipment-product-id");
const receiptProductLabel = document.getElementById("receipt-product-label");
const shipmentProductLabel = document.getElementById("shipment-product-label");
const pickerModal = document.getElementById("product-picker-modal");
const closePickerBtn = document.getElementById("close-picker");
const shipmentViewModal = document.getElementById("shipment-view-modal");
const closeShipmentViewBtn = document.getElementById("close-shipment-view");
const shipmentViewTable = document.getElementById("shipment-view-table");
const shipmentViewTotal = document.getElementById("shipment-view-total");
const productEditModal = document.getElementById("product-edit-modal");
const closeProductEditBtn = document.getElementById("close-product-edit");
const productEditForm = document.getElementById("product-edit-form");
const kpiDay = document.getElementById("kpi-day");
const kpiMonth = document.getElementById("kpi-month");
const kpiYear = document.getElementById("kpi-year");
const kpiProducts = document.getElementById("kpi-products");
const chartTitle = document.getElementById("chart-title");
const chartTotal = document.getElementById("chart-total");
const chartCanvas = document.getElementById("revenue-chart");
const toast = document.getElementById("toast");

const authScreen = document.getElementById("auth-screen");
const authMessage = document.getElementById("auth-message");
const loginForm = document.getElementById("login-form");
const currentUserLabel = document.getElementById("current-user-label");
const logoutBtn = document.getElementById("logout-btn");

function canEdit() {
  return !!currentMe && (currentMe.role === "owner" || currentMe.role === "manager");
}

function showToast(text, type, durationMs) {
  toast.textContent = text;
  toast.classList.remove("toast--success");
  if (type === "success") {
    toast.classList.add("toast--success");
  }
  toast.classList.add("is-show");

  if (toastTimer) {
    clearTimeout(toastTimer);
  }

  toastTimer = setTimeout(() => {
    toast.classList.remove("is-show");
    toast.classList.remove("toast--success");
  }, typeof durationMs === "number" ? durationMs : 1800);
}

function setAuthMessage(message) {
  authMessage.textContent = message || "Войдите как администратор.";
}

function setAuthVisible(visible) {
  authScreen.classList.toggle("is-hidden", !visible);
}

function updateUserLabel() {
  if (!currentMe) {
    currentUserLabel.textContent = "Гость";
    return;
  }
  const codePart = currentMe.orgJoinCode ? ` • Код: ${currentMe.orgJoinCode}` : "";
  currentUserLabel.textContent = `${currentMe.name} (${currentMe.role}) • ${currentMe.orgName}${codePart}`;
}

async function apiRequest(path, options) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...options
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }

  if (!response.ok) {
    const message = payload && payload.error ? payload.error : "Ошибка запроса";
    throw new ApiError(message, response.status);
  }

  return payload;
}

async function syncState() {
  const data = await apiRequest("/state");
  state = {
    products: Array.isArray(data.products) ? data.products : [],
    receipts: Array.isArray(data.receipts) ? data.receipts : [],
    shipments: Array.isArray(data.shipments) ? data.shipments : []
  };
  currentMe = data.me || currentMe;
  updateUserLabel();
}

function ensureSelectedProduct() {
  const firstProductId = state.products.length ? state.products[0].id : "";

  if (!findProduct(state, receiptProductInput.value)) {
    receiptProductInput.value = firstProductId;
  }

  if (!findProduct(state, shipmentProductInput.value)) {
    shipmentProductInput.value = firstProductId;
  }
}

function renderPickerLabels() {
  const receiptProduct = findProduct(state, receiptProductInput.value);
  const shipmentProduct = findProduct(state, shipmentProductInput.value);

  receiptProductLabel.textContent = receiptProduct
    ? `Товар: ${receiptProduct.name} | Цена: ${formatMoney(receiptProduct.price)}`
    : "Выбрать товар";

  shipmentProductLabel.textContent = shipmentProduct
    ? `Товар: ${shipmentProduct.name} | Цена: ${formatMoney(shipmentProduct.price)}`
    : "Выбрать товар";
}

function renderProducts() {
  const query = productSearchQuery.toLowerCase().trim();
  const list = state.products.filter((p) => {
    if (!query) {
      return true;
    }
    return String(p.name).toLowerCase().indexOf(query) !== -1 || String(p.sku).toLowerCase().indexOf(query) !== -1;
  });

  if (!list.length) {
    productsTable.innerHTML = `<tr><td colspan="6">Товаров пока нет.</td></tr>`;
    return;
  }

  productsTable.innerHTML = list
    .map((p) => {
      const actions = canEdit()
        ? `<button type="button" class="price-btn" data-action="edit-product" data-id="${p.id}">Редактировать</button>
           <button type="button" class="delete-btn" data-action="delete-product" data-id="${p.id}">Удалить</button>`
        : `<span>Только просмотр</span>`;

      return `
      <tr>
        <td>${p.name}</td>
        <td>${p.sku}</td>
        <td>${p.unit}</td>
        <td>${formatMoney(p.price)}</td>
        <td class="${p.stock <= 0 ? "bad-stock" : ""}">${p.stock}</td>
        <td>${actions}</td>
      </tr>
    `;
    })
    .join("");
}

function renderPickerProducts() {
  const query = pickerSearchQuery.toLowerCase().trim();
  const list = state.products.filter((p) => {
    if (!query) {
      return true;
    }
    return (
      String(p.name).toLowerCase().indexOf(query) !== -1 ||
      String(p.sku).toLowerCase().indexOf(query) !== -1
    );
  });

  if (!list.length) {
    pickerProductsTable.innerHTML = `<tr><td colspan="5">По вашему запросу ничего не найдено.</td></tr>`;
    return;
  }

  pickerProductsTable.innerHTML = list
    .map(
      (p) => `
      <tr>
        <td>${p.name}</td>
        <td class="${p.stock <= 0 ? "bad-stock" : ""}">${p.stock}</td>
        <td>${p.purchasePrice > 0 ? formatMoney(p.purchasePrice) : "-"}</td>
        <td>${formatMoney(p.price)}</td>
        <td><button type="button" class="choose-btn" data-action="choose-product" data-id="${p.id}">Выбрать</button></td>
      </tr>
    `
    )
    .join("");
}

function renderReceipts() {
  if (!state.receipts.length) {
    receiptsTable.innerHTML = `<tr><td colspan="5">Приемок пока нет.</td></tr>`;
    return;
  }

  receiptsTable.innerHTML = state.receipts
    .map((row) => {
      const product = findProduct(state, row.productId);
      const action = canEdit()
        ? `<button type="button" class="delete-btn" data-action="delete-receipt" data-id="${row.id}">Удалить</button>`
        : "<span>Только просмотр</span>";
      return `
        <tr>
          <td>${DATE_TIME.format(new Date(row.createdAt))}</td>
          <td>${product ? product.name : "Удаленный товар"}</td>
          <td>${row.quantity}</td>
          <td>${row.cost > 0 ? formatMoney(row.cost) : "-"}</td>
          <td>${action}</td>
        </tr>
      `;
    })
    .join("");
}

function renderShipments() {
  if (!state.shipments.length) {
    shipmentsTable.innerHTML = `<tr><td colspan="4">Отгрузок пока нет.</td></tr>`;
    return;
  }

  shipmentsTable.innerHTML = state.shipments
    .map((row) => {
      const amount = getShipmentAmount(row);
      const deleteBtn = canEdit()
        ? `<button type="button" class="delete-btn" data-action="delete-shipment" data-id="${row.id}">Удалить</button>`
        : "";

      return `
        <tr>
          <td>${DATE_TIME.format(new Date(row.createdAt))}</td>
          <td>${getShipmentItemsCount(row)}</td>
          <td>${formatMoney(amount)}</td>
          <td>
            <button type="button" class="view-btn" data-action="view-shipment" data-id="${row.id}">Состав</button>
            <button type="button" class="print-btn" data-action="print-shipment" data-id="${row.id}">Печать</button>
            ${deleteBtn}
          </td>
        </tr>
      `;
    })
    .join("");
}

function printShipmentDocument(shipmentId) {
  const printWindow = window.open(`/print/shipment/${encodeURIComponent(shipmentId)}`, "_blank");
  if (!printWindow || printWindow.closed) {
    showToast("Браузер заблокировал окно печати");
    return;
  }
}

function openShipmentView(shipmentId) {
  const shipment = state.shipments.find((row) => row.id === shipmentId);
  if (!shipment) {
    return;
  }

  const items = Array.isArray(shipment.items) ? shipment.items : [];
  if (!items.length) {
    shipmentViewTable.innerHTML = `<tr><td colspan="4">Позиции не найдены.</td></tr>`;
    shipmentViewTotal.textContent = "Итого: 0 ₽";
  } else {
    shipmentViewTable.innerHTML = items
      .map((item) => {
        const product = findProduct(state, item.productId);
        return `
          <tr>
            <td>${product ? product.name : "Удаленный товар"}</td>
            <td>${item.quantity}</td>
            <td>${formatMoney(item.price)}</td>
            <td>${formatMoney(item.amount)}</td>
          </tr>
        `;
      })
      .join("");
    shipmentViewTotal.textContent = `Итого: ${formatMoney(getShipmentAmount(shipment))}`;
  }

  shipmentViewModal.classList.add("is-open");
  shipmentViewModal.setAttribute("aria-hidden", "false");
}

function closeShipmentView() {
  shipmentViewModal.classList.remove("is-open");
  shipmentViewModal.setAttribute("aria-hidden", "true");
}

function openProductEditModal(product) {
  if (!product) {
    return;
  }
  productEditForm.elements.id.value = product.id;
  productEditForm.elements.name.value = product.name;
  productEditForm.elements.price.value = Number(product.price).toFixed(2);
  productEditModal.classList.add("is-open");
  productEditModal.setAttribute("aria-hidden", "false");
}

function closeProductEditModal() {
  productEditModal.classList.remove("is-open");
  productEditModal.setAttribute("aria-hidden", "true");
}

function getDraftReservedQuantity(productId) {
  return shipmentDraft.items.reduce((sum, item) => (item.productId === productId ? sum + item.quantity : sum), 0);
}

function addShipmentDraftItem(productId, quantity) {
  const product = findProduct(state, productId);
  const qty = Math.floor(parseNum(quantity));
  if (!product || qty <= 0) {
    return { ok: false, message: "Проверьте товар и количество" };
  }

  const available = product.stock - getDraftReservedQuantity(productId);
  if (available < qty) {
    return { ok: false, message: "Недостаточно остатка для добавления позиции" };
  }

  const existing = shipmentDraft.items.find((item) => item.productId === productId);
  if (existing) {
    existing.quantity += qty;
    existing.amount = existing.quantity * existing.price;
  } else {
    shipmentDraft.items.push({
      productId,
      quantity: qty,
      price: product.price,
      amount: qty * product.price
    });
  }

  return { ok: true };
}

function renderShipmentDraft() {
  if (!shipmentDraft.items.length) {
    shipmentLinesTable.innerHTML = `<tr><td colspan="5">Позиции не добавлены.</td></tr>`;
    shipmentDraftTotal.textContent = "Итого по отгрузке: 0 ₽";
    return;
  }

  shipmentLinesTable.innerHTML = shipmentDraft.items
    .map((item) => {
      const product = findProduct(state, item.productId);
      const action = canEdit()
        ? `<button type="button" class="delete-btn" data-action="delete-shipment-line" data-id="${item.productId}">Удалить</button>`
        : "<span>Только просмотр</span>";
      const qtyCell = canEdit()
        ? `<input class="shipment-qty-input" type="number" min="1" step="1" data-action="edit-shipment-line-qty" data-id="${item.productId}" value="${item.quantity}" />`
        : `${item.quantity}`;
      const priceCell = canEdit()
        ? `<input class="shipment-price-input" type="number" min="0" step="0.01" data-action="edit-shipment-line-price" data-id="${item.productId}" value="${Number(item.price).toFixed(2)}" />`
        : `${formatMoney(item.price)}`;
      return `
        <tr>
          <td>${product ? product.name : "Удаленный товар"}</td>
          <td>${qtyCell}</td>
          <td>${priceCell}</td>
          <td>${formatMoney(item.amount)}</td>
          <td>${action}</td>
        </tr>
      `;
    })
    .join("");

  const total = shipmentDraft.items.reduce((sum, item) => sum + item.amount, 0);
  shipmentDraftTotal.textContent = `Итого по отгрузке: ${formatMoney(total)}`;
}

function getRevenueKpi() {
  const now = new Date();
  const month = now.getMonth();
  const year = now.getFullYear();
  let dayTotal = 0;
  let monthTotal = 0;
  let yearTotal = 0;

  state.shipments.forEach((shipment) => {
    const date = new Date(shipment.createdAt);
    const amount = getShipmentAmount(shipment);
    if (date.getFullYear() === year) {
      yearTotal += amount;
      if (date.getMonth() === month) {
        monthTotal += amount;
        if (isSameDay(date, now)) {
          dayTotal += amount;
        }
      }
    }
  });

  return { dayTotal, monthTotal, yearTotal };
}

function getChartData(period) {
  const now = new Date();

  if (period === "day") {
    const labels = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}:00`);
    const values = Array(24).fill(0);
    state.shipments.forEach((row) => {
      const date = new Date(row.createdAt);
      if (isSameDay(date, now)) {
        values[date.getHours()] += getShipmentAmount(row);
      }
    });
    return { labels, values };
  }

  if (period === "month") {
    const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    const labels = Array.from({ length: daysInMonth }, (_, i) => String(i + 1));
    const values = Array(daysInMonth).fill(0);

    state.shipments.forEach((row) => {
      const date = new Date(row.createdAt);
      if (date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth()) {
        values[date.getDate() - 1] += getShipmentAmount(row);
      }
    });

    return { labels, values };
  }

  const labels = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
  const values = Array(12).fill(0);

  state.shipments.forEach((row) => {
    const date = new Date(row.createdAt);
    if (date.getFullYear() === now.getFullYear()) {
      values[date.getMonth()] += getShipmentAmount(row);
    }
  });

  return { labels, values };
}

function drawChart(period) {
  const ctx = chartCanvas.getContext("2d");
  const chart = getChartData(period);
  const labels = chart.labels;
  const values = chart.values;
  const total = values.reduce((acc, v) => acc + v, 0);
  const max = Math.max(...values, 1);

  const w = chartCanvas.width;
  const h = chartCanvas.height;
  const padL = 54;
  const padR = 20;
  const padT = 24;
  const padB = 44;

  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "#d7e4ef";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i += 1) {
    const y = padT + ((h - padT - padB) / 5) * i;
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(w - padR, y);
    ctx.stroke();
  }

  ctx.strokeStyle = "#9eb3c8";
  ctx.beginPath();
  ctx.moveTo(padL, padT);
  ctx.lineTo(padL, h - padB);
  ctx.lineTo(w - padR, h - padB);
  ctx.stroke();

  const chartW = w - padL - padR;
  const chartH = h - padT - padB;
  const stepX = labels.length > 1 ? chartW / (labels.length - 1) : chartW;

  ctx.beginPath();
  values.forEach((value, idx) => {
    const x = padL + stepX * idx;
    const y = h - padB - (value / max) * chartH;
    if (idx === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.strokeStyle = "#13a3b7";
  ctx.lineWidth = 3;
  ctx.stroke();

  ctx.fillStyle = "#13a3b7";
  values.forEach((value, idx) => {
    const x = padL + stepX * idx;
    const y = h - padB - (value / max) * chartH;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  });

  ctx.fillStyle = "#587088";
  ctx.font = "13px Segoe UI";
  const stepLabel = Math.ceil(labels.length / 10);
  labels.forEach((label, idx) => {
    if (idx % stepLabel !== 0 && idx !== labels.length - 1) {
      return;
    }
    const x = padL + stepX * idx;
    ctx.fillText(label, x - 8, h - 16);
  });

  chartTitle.textContent = `График выручки: ${period === "day" ? "день" : period === "month" ? "месяц" : "год"}`;
  chartTotal.textContent = `Итого: ${formatMoney(total)}`;
}

function renderKpi() {
  const totals = getRevenueKpi();
  kpiDay.textContent = formatMoney(totals.dayTotal);
  kpiMonth.textContent = formatMoney(totals.monthTotal);
  kpiYear.textContent = formatMoney(totals.yearTotal);
  kpiProducts.textContent = String(state.products.length);
}

function applyRoleUi() {
  const editable = canEdit();
  openProductFormBtn.style.display = editable ? "inline-block" : "none";
  productFormWrap.classList.toggle("is-hidden", !editable);
  receiptForm.querySelector("button[type='submit']").style.display = editable ? "inline-block" : "none";
  addShipmentLineBtn.style.display = editable ? "inline-block" : "none";
  document.getElementById("submit-shipment").style.display = editable ? "inline-block" : "none";
}

function openPicker(target) {
  pickerTarget = target;
  pickerSearchQuery = "";
  if (pickerSearchInput) {
    pickerSearchInput.value = "";
  }
  renderPickerProducts();
  pickerModal.classList.add("is-open");
  pickerModal.setAttribute("aria-hidden", "false");
}

function closePicker() {
  pickerModal.classList.remove("is-open");
  pickerModal.setAttribute("aria-hidden", "true");
}

function refresh() {
  ensureSelectedProduct();
  renderProducts();
  renderPickerProducts();
  renderPickerLabels();
  renderReceipts();
  renderShipments();
  renderShipmentDraft();
  renderKpi();
  drawChart(chartPeriod);
  applyRoleUi();
}

async function refreshFromServer() {
  await syncState();
  shipmentDraft.items = shipmentDraft.items.filter((draft) => !!findProduct(state, draft.productId));
  refresh();
}

async function secureAction(fn) {
  try {
    await fn();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      currentMe = null;
      updateUserLabel();
      setAuthVisible(true);
      setAuthMessage("Сессия истекла. Войдите снова.");
      return;
    }
    showToast(error.message || "Ошибка");
  }
}

screenButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.getAttribute("data-screen");
    screenButtons.forEach((el) => el.classList.remove("is-active"));
    btn.classList.add("is-active");
    screens.forEach((screen) => {
      screen.classList.toggle("is-active", screen.id === `screen-${target}`);
    });
  });
});

periodButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    chartPeriod = btn.getAttribute("data-chart-period");
    periodButtons.forEach((el) => el.classList.remove("is-active"));
    btn.classList.add("is-active");
    drawChart(chartPeriod);
  });
});

if (openProductFormBtn && productFormWrap) {
  openProductFormBtn.addEventListener("click", () => {
    productFormWrap.classList.toggle("is-hidden");
  });
}

if (productsSearchInput) {
  productsSearchInput.addEventListener("input", () => {
    productSearchQuery = productsSearchInput.value || "";
    renderProducts();
  });
}

if (pickerSearchInput) {
  pickerSearchInput.addEventListener("input", () => {
    pickerSearchQuery = pickerSearchInput.value || "";
    renderPickerProducts();
  });
}

openPickerButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const target = button.getAttribute("data-target") || "receipt";
    openPicker(target);
  });
});

closePickerBtn.addEventListener("click", closePicker);
pickerModal.addEventListener("click", (event) => {
  if (event.target === pickerModal) {
    closePicker();
  }
});

closeShipmentViewBtn.addEventListener("click", closeShipmentView);
shipmentViewModal.addEventListener("click", (event) => {
  if (event.target === shipmentViewModal) {
    closeShipmentView();
  }
});

closeProductEditBtn.addEventListener("click", closeProductEditModal);
productEditModal.addEventListener("click", (event) => {
  if (event.target === productEditModal) {
    closeProductEditModal();
  }
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(loginForm);

  await secureAction(async () => {
    await apiRequest("/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: String(data.get("username") || "").trim(),
        password: String(data.get("password") || "")
      })
    });
    loginForm.reset();
    await refreshFromServer();
    setAuthVisible(false);
    showToast("Вход выполнен", "success", 1200);
  });
});

logoutBtn.addEventListener("click", async () => {
  await secureAction(async () => {
    await apiRequest("/auth/logout", { method: "POST", body: JSON.stringify({}) });
    currentMe = null;
    updateUserLabel();
    setAuthVisible(true);
    setAuthMessage("Вы вышли из аккаунта.");
  });
});

pickerProductsTable.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action='choose-product']");
  if (!button) {
    return;
  }

  const productId = button.getAttribute("data-id");
  if (!findProduct(state, productId)) {
    return;
  }

  if (pickerTarget === "shipment") {
    const result = addShipmentDraftItem(productId, 1);
    if (!result.ok) {
      showToast(result.message);
      return;
    }
    shipmentProductInput.value = "";
    shipmentForm.querySelector("input[name='quantity']").value = "";
    refresh();
    closePicker();
    showToast("Позиция добавлена");
    return;
  } else {
    receiptProductInput.value = productId;
  }

  renderPickerLabels();
  closePicker();
  showToast("Товар выбран");
});

quickProductForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canEdit()) {
    return;
  }

  const data = new FormData(quickProductForm);
  const name = String(data.get("name") || "").trim();
  const stock = Math.max(0, Math.floor(parseNum(data.get("stock"))));
  const purchasePrice = Math.max(0, parseNum(data.get("purchasePrice")));
  const salePrice = Math.max(0, parseNum(data.get("salePrice")));

  if (!name) {
    showToast("Укажите название товара");
    return;
  }

  await secureAction(async () => {
    await apiRequest("/products", {
      method: "POST",
      body: JSON.stringify({ name, stock, purchasePrice, price: salePrice, unit: "шт" })
    });
    quickProductForm.reset();
    await refreshFromServer();
    showToast("Новый товар сохранен");
  });
});

productForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canEdit()) {
    return;
  }

  const data = new FormData(productForm);
  const payload = {
    name: String(data.get("name") || "").trim(),
    sku: String(data.get("sku") || "").trim(),
    unit: String(data.get("unit") || "шт").trim(),
    price: parseNum(data.get("price")),
    stock: Math.max(0, Math.floor(parseNum(data.get("stock")))),
    purchasePrice: 0
  };

  if (!payload.name || !payload.sku) {
    showToast("Заполните название и код товара");
    return;
  }

  await secureAction(async () => {
    await apiRequest("/products", { method: "POST", body: JSON.stringify(payload) });
    productForm.reset();
    productForm.querySelector("input[name='unit']").value = "шт";
    await refreshFromServer();
    showToast("Товар добавлен");
  });
});

productsTable.addEventListener("click", async (event) => {
  const targetButton = event.target.closest("button[data-action]");
  if (!targetButton || !canEdit()) {
    return;
  }

  const action = targetButton.getAttribute("data-action");
  const productId = targetButton.getAttribute("data-id");

  if (action === "delete-product") {
    const product = findProduct(state, productId);
    if (!product) {
      return;
    }

    if (!window.confirm(`Удалить товар «${product.name}» и все связанные документы?`)) {
      return;
    }

    await secureAction(async () => {
      await apiRequest(`/products/${productId}`, { method: "DELETE" });
      await refreshFromServer();
      showToast("Товар удален");
    });
    return;
  }

  if (action === "edit-product") {
    const product = findProduct(state, productId);
    if (!product) {
      return;
    }
    openProductEditModal(product);
  }
});

productEditForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canEdit()) {
    return;
  }

  const id = String(productEditForm.elements.id.value || "");
  const name = String(productEditForm.elements.name.value || "").trim();
  const price = parseNum(productEditForm.elements.price.value);

  if (!id || !name) {
    showToast("Название не может быть пустым");
    return;
  }
  if (price < 0) {
    showToast("Цена не может быть отрицательной");
    return;
  }

  await secureAction(async () => {
    await apiRequest(`/products/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name, price })
    });
    closeProductEditModal();
    await refreshFromServer();
    showToast("Товар обновлен");
  });
});

receiptsTable.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action='delete-receipt']");
  if (!button || !canEdit()) {
    return;
  }

  if (!window.confirm("Удалить эту приемку?")) {
    return;
  }

  const id = button.getAttribute("data-id");
  await secureAction(async () => {
    await apiRequest(`/receipts/${id}`, { method: "DELETE" });
    await refreshFromServer();
    showToast("Приемка удалена");
  });
});

shipmentsTable.addEventListener("click", async (event) => {
  const actionButton = event.target.closest("button[data-action]");
  if (!actionButton) {
    return;
  }

  const action = actionButton.getAttribute("data-action");
  const id = actionButton.getAttribute("data-id");

  if (action === "view-shipment") {
    openShipmentView(id);
    return;
  }

  if (action === "print-shipment") {
    printShipmentDocument(id);
    return;
  }

  if (action === "delete-shipment" && canEdit()) {
    if (!window.confirm("Удалить эту отгрузку?")) {
      return;
    }

    await secureAction(async () => {
      await apiRequest(`/shipments/${id}`, { method: "DELETE" });
      await refreshFromServer();
      showToast("Отгрузка удалена");
    });
  }
});

receiptForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canEdit()) {
    return;
  }

  const data = new FormData(receiptForm);
  const payload = {
    productId: String(data.get("productId") || ""),
    quantity: Math.floor(parseNum(data.get("quantity"))),
    cost: Math.max(0, parseNum(data.get("cost")))
  };

  if (!payload.productId || payload.quantity <= 0) {
    showToast("Проверьте товар и количество");
    return;
  }

  await secureAction(async () => {
    await apiRequest("/receipts", { method: "POST", body: JSON.stringify(payload) });
    receiptForm.reset();
    await refreshFromServer();
    showToast("Приемка проведена");
  });
});

addShipmentLineBtn.addEventListener("click", () => {
  if (!canEdit()) {
    return;
  }

  const data = new FormData(shipmentForm);
  const productId = String(data.get("productId") || "");
  const quantity = Math.floor(parseNum(data.get("quantity")));
  const result = addShipmentDraftItem(productId, quantity);
  if (!result.ok) {
    showToast(result.message);
    return;
  }

  shipmentForm.querySelector("input[name='quantity']").value = "";
  refresh();
  showToast("Позиция добавлена");
});

shipmentLinesTable.addEventListener("click", (event) => {
  if (!canEdit()) {
    return;
  }

  const button = event.target.closest("button[data-action='delete-shipment-line']");
  if (!button) {
    return;
  }

  const productId = button.getAttribute("data-id");
  shipmentDraft.items = shipmentDraft.items.filter((item) => item.productId !== productId);
  refresh();
  showToast("Позиция удалена");
});

shipmentLinesTable.addEventListener("change", (event) => {
  if (!canEdit()) {
    return;
  }

  const qtyInput = event.target.closest("input[data-action='edit-shipment-line-qty']");
  if (qtyInput) {
    const productId = qtyInput.getAttribute("data-id");
    const quantity = Math.floor(parseNum(qtyInput.value));
    const item = shipmentDraft.items.find((line) => line.productId === productId);
    const product = findProduct(state, productId);
    if (!item || !product) {
      return;
    }
    if (quantity <= 0) {
      showToast("Количество должно быть больше нуля");
      refresh();
      return;
    }
    if (quantity > product.stock) {
      showToast("Недостаточно остатка на складе");
      refresh();
      return;
    }

    item.quantity = quantity;
    item.amount = item.quantity * item.price;
    refresh();
    showToast("Количество позиции изменено");
    return;
  }

  const input = event.target.closest("input[data-action='edit-shipment-line-price']");
  if (!input) {
    return;
  }

  const productId = input.getAttribute("data-id");
  const price = Math.max(0, parseNum(input.value));
  const item = shipmentDraft.items.find((line) => line.productId === productId);
  if (!item) {
    return;
  }

  item.price = price;
  item.amount = item.quantity * price;
  refresh();
  showToast("Цена позиции изменена");
});

shipmentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!canEdit()) {
    return;
  }

  if (!shipmentDraft.items.length) {
    showToast("Добавьте хотя бы одну позицию");
    return;
  }

  const payload = {
    items: shipmentDraft.items.map((item) => ({
      productId: item.productId,
      quantity: item.quantity,
      price: item.price
    }))
  };

  await secureAction(async () => {
    await apiRequest("/shipments", { method: "POST", body: JSON.stringify(payload) });
    shipmentDraft = { items: [] };
    shipmentForm.querySelector("input[name='quantity']").value = "";
    await refreshFromServer();
    showToast("Отгрузка проведена", "success", 1000);
  });
});

async function init() {
  updateUserLabel();
  setAuthVisible(true);
  setAuthMessage("Войдите как администратор.");

  await secureAction(async () => {
    await refreshFromServer();
    setAuthVisible(false);
  });
}

init();
