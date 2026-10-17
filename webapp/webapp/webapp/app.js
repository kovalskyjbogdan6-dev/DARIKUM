(() => {
  const tg = window.Telegram?.WebApp;
  if (tg) tg.expand();

  const grid = document.getElementById("grid");
  const controls = document.getElementById("controls");

  async function fetchCases(){
    try{
      const r = await fetch("/api/cases");
      if(!r.ok) throw new Error("fetch failed");
      return await r.json();
    }catch(e){
      console.error(e);
      return [];
    }
  }

  function sendToBot(obj){
    if (tg && tg.sendData){
      tg.sendData(JSON.stringify(obj));
    } else {
      alert("Откройте это мини‑приложение в Telegram.");
    }
  }

  function renderCases(cases){
    grid.innerHTML = "";
    if(!cases.length){
      grid.innerHTML = '<div class="card"><div class="small">Нет кейсов</div></div>';
      return;
    }
    cases.forEach(c => {
      const el = document.createElement("div");
      el.className = "card";
      el.innerHTML = `
        <img src="${c.image || '/webapp/assets/case_placeholder.png'}" alt="${c.title}" />
        <div class="title">${c.title}</div>
        <div class="price">⭐ ${c.price}</div>
      `;
      const btn = document.createElement("button");
      btn.className = "open";
      btn.textContent = "Открыть";
      btn.onclick = ()=> {
        sendToBot({action:"open_case", case_id: c.id});
      };
      el.appendChild(btn);
      grid.appendChild(el);
    });
  }

  async function init(){
    grid.innerHTML = '<div class="card"><div class="small">Загрузка...</div></div>';
    const cases = await fetchCases();
    renderCases(cases);

    const refresh = document.createElement("button");
    refresh.className = "open";
    refresh.style.marginTop = "12px";
    refresh.textContent = "Обновить";
    refresh.onclick = async ()=> {
      refresh.disabled = true;
      const cs = await fetchCases();
      renderCases(cs);
      setTimeout(()=>refresh.disabled=false,500);
    };
    controls.appendChild(refresh);
  }
  init();
})();
