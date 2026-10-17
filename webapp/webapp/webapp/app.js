// Простая логика: парсим query param `data` (base64 JSON, если есть) или `mode`+`game_id`.
// При клике на действие шлём Telegram.WebApp.sendData({action: "...", game_id: ..., mode: ...})
(() => {
  const params = new URLSearchParams(location.search);
  const rawData = params.get("data"); // base64 encoded JSON created by bot
  const modeParam = params.get("mode");
  const gameIdParam = params.get("game_id");

  let payload = null;
  if (rawData) {
    try {
      const json = JSON.parse(decodeURIComponent(atob(rawData)));
      payload = json;
    } catch (e) {
      console.warn("Could not parse data param", e);
    }
  } else if (modeParam && gameIdParam) {
    payload = { mode: modeParam, game_id: Number(gameIdParam) };
  }

  const modeContainer = document.getElementById("mode-container");
  const controls = document.getElementById("controls");
  const titleEl = document.getElementById("title");
  const subtitleEl = document.getElementById("subtitle");

  const telegram = window.Telegram?.WebApp;
  if (telegram) {
    telegram.expand(); // максимально развернуть окно
  }

  function sendToBot(obj){
    if (telegram && telegram.sendData) {
      telegram.sendData(JSON.stringify(obj));
    } else {
      alert("Telegram WebApp API not available. Откройте в Telegram.");
    }
  }

  function renderSpin(data){
    titleEl.textContent = data.title || "Spin-the-Wheel";
    subtitleEl.textContent = "Режим: Spin-the-Wheel";

    const wheel = document.createElement("div");
    wheel.className = "card wheel";

    data.slices.forEach(s=>{
      const d = document.createElement("div");
      d.className = "slice";
      d.innerHTML = `<strong>${s.label}</strong><div class="small">вес: ${s.weight}</div>`;
      wheel.appendChild(d);
    });

    modeContainer.appendChild(wheel);

    const btn = document.createElement("button");
    btn.className = "cta";
    btn.textContent = "🎡 Попросить бот крутнуть";
    btn.onclick = ()=>{
      sendToBot({ action: "request_spin", game_id: data.game_id });
    };
    controls.appendChild(btn);
  }

  function renderVault(data){
    titleEl.textContent = data.title || "Mystery Vault";
    subtitleEl.textContent = "Режим: Mystery Vault";

    const info = document.createElement("div");
    info.className = "card";
    info.innerHTML = `<p class="small">Вы видите только подсказку. Откройте сундук и узнайте приз!</p>`;
    modeContainer.appendChild(info);

    const btn = document.createElement("button");
    btn.className = "cta";
    btn.textContent = "🔐 Открыть сундук";
    btn.onclick = ()=>{
      sendToBot({ action: "request_vault_open", game_id: data.game_id });
    };
    controls.appendChild(btn);
  }

  function init(){
    if (!payload) {
      modeContainer.innerHTML = '<div class="card">Неверный вызов мини-приложения. Нет данных игры.</div>';
      return;
    }
    // Если payload содержит slices or prizes — используем для рендера клиентской части
    if (payload.mode === "spin" || payload.slices) {
      const data = Object.assign({ mode: "spin", slices: payload.slices || [], game_id: payload.game_id || payload.id, title: payload.title }, {});
      renderSpin(data);
      return;
    }
    if (payload.mode === "vault" || payload.prizes) {
      const data = Object.assign({ mode: "vault", prizes: payload.prizes || [], game_id: payload.game_id || payload.id, title: payload.title }, {});
      renderVault(data);
      return;
    }

    modeContainer.innerHTML = '<div class="card">Режим не распознан.</div>';
  }

  init();
})();
