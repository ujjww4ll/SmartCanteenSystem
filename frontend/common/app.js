// NOTE: This file expects config.js to be loaded first.
// All pages that use app.js MUST include:
//   <script src="../common/config.js"></script>
//   <script src="../common/app.js"></script>

// ── Auth helpers ──────────────────────────────────────────────────────────────
function authHeaders() {
    return {
        "Content-Type":  "application/json",
        "Authorization": "Bearer " + localStorage.getItem("token")
    };
}

function protectPage(requiredRole) {
    const token = localStorage.getItem("token");
    const user  = JSON.parse(localStorage.getItem("user") || "null");

    if (!token || token === "undefined") {
        window.location.href = "../student/login.html";
        return;
    }
    if (!user || !user.role) {
        localStorage.clear();
        window.location.href = "../student/login.html";
        return;
    }
    if (requiredRole && user.role !== requiredRole) {
        alert("Unauthorized access");
        localStorage.clear();
        window.location.href = "../student/login.html";
        return;
    }
}

function logout() {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    localStorage.removeItem("order");
    window.location = "../student/login.html";
}

// ── Cart ──────────────────────────────────────────────────────────────────────
let cart = [];

function addToCart(name, price, time) {
    cart.push({ name, price, time });
    renderCart();
    showToast(`✅ ${name} added to cart!`);
}

function removeItem(i) {
    const removed = cart.splice(i, 1)[0];
    renderCart();
    showToast(`🗑️ ${removed.name} removed`);
}

function renderCart() {
    let total = 0;
    let html  = "";

    cart.forEach((item, idx) => {
        total += item.price;
        html  += `
        <div class="cart-item" style="display:flex;align-items:center;justify-content:space-between;
             padding:12px 16px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.08);
             border-radius:12px;margin-bottom:10px;">
          <div>
            <span style="font-weight:600;">${item.name}</span>
            <span style="color:rgba(240,240,255,0.5);font-size:12px;margin-left:8px;">⏱ ${item.time} min</span>
          </div>
          <div style="display:flex;align-items:center;gap:12px;">
            <span style="font-weight:700;color:#ffd60a;">₹${item.price}</span>
            <button onclick="removeItem(${idx})" style="
              background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);
              color:#fca5a5;border-radius:8px;padding:4px 10px;cursor:pointer;font-size:12px;
              transition:all 0.2s;">✕</button>
          </div>
        </div>`;
    });

    const cartEl = document.getElementById("cart");
    const totalEl = document.getElementById("cart-total");
    const countEl = document.getElementById("cart-count");

    if (cartEl)  cartEl.innerHTML  = html || `<p style="color:rgba(255,255,255,0.3);text-align:center;padding:20px;">Your cart is empty</p>`;
    if (totalEl) totalEl.textContent = `₹${total}`;
    if (countEl) countEl.textContent = cart.length;
}

// ── Checkout ──────────────────────────────────────────────────────────────────
function checkout() {
    if (cart.length === 0) {
        showToast("⚠️ Cart is empty!", "error");
        return;
    }

    const params   = new URLSearchParams(window.location.search);
    const canteenId = params.get("canteen");
    const btn       = document.getElementById("checkoutBtn");

    if (btn) { btn.disabled = true; btn.textContent = "Placing order..."; }

    fetch(API + "/order/create", {
        method:  "POST",
        headers: authHeaders(),
        body:    JSON.stringify({ canteen_id: canteenId, items: cart })
    })
    .then(r => r.json())
    .then(d => {
        if (d.error) {
            showToast("❌ " + d.error, "error");
            if (btn) { btn.disabled = false; btn.textContent = "Place Order"; }
            return;
        }
        localStorage.setItem("order", d.order_id);
        window.location = "order_status.html";
    })
    .catch(() => {
        showToast("❌ Could not connect to server.", "error");
        if (btn) { btn.disabled = false; btn.textContent = "Place Order"; }
    });
}

// ── Student order status polling ──────────────────────────────────────────────
function pollStudent() {
    const id = localStorage.getItem("order");
    if (!id) return;

    const poll = () => {
        fetch(API + "/order/status/" + id, { headers: authHeaders() })
        .then(r => r.json())
        .then(o => {
            if (!o.status) return;

            const statusEl  = document.getElementById("status");
            const priceEl   = document.getElementById("price");
            const expectedEl= document.getElementById("expected");
            const itemsEl   = document.getElementById("items");

            if (statusEl)   statusEl.textContent  = o.status;
            if (priceEl)    priceEl.textContent    = "₹" + o.price;
            if (expectedEl) expectedEl.textContent = o.expected_time + " mins";

            if (itemsEl && o.items) {
                itemsEl.innerHTML = o.items.map(i =>
                    `<div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.06);">• ${i.name} — ₹${i.price}</div>`
                ).join("");
            }

            // Update progress bar
            const steps  = ["WAITING","ACCEPTED","PREPARING","READY","COMPLETED"];
            const stepIdx = steps.indexOf(o.status);
            document.querySelectorAll(".progress-step").forEach((el, i) => {
                el.classList.toggle("active",    i <= stepIdx);
                el.classList.toggle("current",   i === stepIdx);
            });

            if (o.status === "COMPLETED") {
                showToast("🎉 Your order is ready! Go pick it up.");
            }
        })
        .catch(() => {});
    };

    poll();
    setInterval(poll, 3000);
}

// ── Canteen dashboard polling ─────────────────────────────────────────────────
function loadOrders() {
    const poll = () => {
        fetch(API + "/canteen/orders", { headers: authHeaders() })
        .then(r => r.json())
        .then(data => {
            if (!Array.isArray(data)) return;

            const ordersEl = document.getElementById("orders");
            if (!ordersEl) return;

            if (data.length === 0) {
                ordersEl.innerHTML = `<div style="text-align:center;color:rgba(255,255,255,0.3);padding:60px 20px;">
                  <div style="font-size:48px;margin-bottom:12px;">🍽️</div>
                  <p>No orders yet. Waiting for hungry students...</p></div>`;
                return;
            }

            ordersEl.innerHTML = data.map(o => `
              <div class="card" style="${o.queue_position === 1 ? 'border-color:#ff6b35;box-shadow:0 0 20px rgba(255,107,53,0.3);' : ''}">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
                  <span style="font-weight:700;font-size:16px;">Order #${o.order_id}</span>
                  <span class="status ${o.status}">${o.status}</span>
                </div>
                <p style="font-size:13px;color:rgba(240,240,255,0.5);">
                  Queue: <b style="color:#ffd60a;">#${o.queue_position}</b> &nbsp;|&nbsp;
                  Priority: ${o.priority} &nbsp;|&nbsp;
                  Total: <b style="color:#10b981;">₹${o.price}</b>
                </p>
                <div style="margin:12px 0;">
                  ${o.items.map(i => `<span style="display:inline-block;padding:3px 10px;background:rgba(255,255,255,0.06);border-radius:20px;font-size:12px;margin:3px;">${i.name}</span>`).join("")}
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;">
                  <button onclick="update(${o.order_id},'accept')"   style="background:rgba(59,130,246,0.2);border:1px solid rgba(59,130,246,0.4);color:#93c5fd;border-radius:8px;padding:7px 14px;cursor:pointer;font-size:12px;font-weight:600;">Accept</button>
                  <button onclick="update(${o.order_id},'preparing')"style="background:rgba(168,85,247,0.2);border:1px solid rgba(168,85,247,0.4);color:#c4b5fd;border-radius:8px;padding:7px 14px;cursor:pointer;font-size:12px;font-weight:600;">Preparing</button>
                  <button onclick="update(${o.order_id},'ready')"    style="background:rgba(16,185,129,0.2);border:1px solid rgba(16,185,129,0.4);color:#86efac;border-radius:8px;padding:7px 14px;cursor:pointer;font-size:12px;font-weight:600;">Ready</button>
                  <button onclick="update(${o.order_id},'complete')" style="background:rgba(34,197,94,0.2);border:1px solid rgba(34,197,94,0.4);color:#4ade80;border-radius:8px;padding:7px 14px;cursor:pointer;font-size:12px;font-weight:600;">Completed</button>
                </div>
              </div>`).join("");
        })
        .catch(() => {});
    };

    poll();
    setInterval(poll, 3000);
}

// ── Update order status ───────────────────────────────────────────────────────
function update(id, type) {
    const endpoints = {
        accept:    "/order/accept",
        preparing: "/order/preparing",
        ready:     "/order/ready",
        complete:  "/order/complete"
    };
    fetch(API + endpoints[type], {
        method:  "POST",
        headers: authHeaders(),
        body:    JSON.stringify({ order_id: id })
    }).then(() => loadOrders()).catch(() => {});
}

// ── Toast notifications ───────────────────────────────────────────────────────
function showToast(msg, type = "success") {
    const existing = document.getElementById("bu-toast");
    if (existing) existing.remove();

    const toast = document.createElement("div");
    toast.id = "bu-toast";
    const bg = type === "error" ? "rgba(239,68,68,0.9)" : "rgba(16,185,129,0.9)";
    toast.style.cssText = `
      position:fixed;bottom:28px;right:28px;z-index:9999;
      padding:14px 22px;background:${bg};color:white;
      border-radius:14px;font-family:Poppins,sans-serif;font-size:14px;font-weight:600;
      box-shadow:0 8px 32px rgba(0,0,0,0.4);
      animation:toastIn 0.4s cubic-bezier(0.34,1.56,0.64,1);
      backdrop-filter:blur(10px);max-width:320px;`;
    toast.textContent = msg;

    const style = document.createElement("style");
    style.textContent = `@keyframes toastIn{from{opacity:0;transform:translateY(20px) scale(0.9)}to{opacity:1;transform:translateY(0) scale(1)}}`;
    document.head.appendChild(style);
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}

// ── Session keepalive check ───────────────────────────────────────────────────
setInterval(() => {
    const token = localStorage.getItem("token");
    if (!token && window.location.pathname.includes("menu.html")) {
        window.location = "../student/login.html";
    }
}, 15000);