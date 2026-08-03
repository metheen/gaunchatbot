/*!
 * GaunAI — Gömülebilir Sohbet Widget'ı
 *
 * gaziantep.edu.tr gibi ÜÇÜNCÜ TARAF bir sayfaya eklenmek üzere tasarlandı.
 * Shadow DOM ile TAM stil izolasyonu sağlar: host sayfanın CSS'i widget'ı
 * bozamaz, widget'ın stili de host sayfaya SIZMAZ (bu yüzden Tailwind CDN
 * kullanılmıyor — CDN'in global reset'i host sayfanın düzenini bozabilirdi).
 *
 * Kullanım (host sayfaya, </body> kapanmadan önce):
 *   <script src="https://gaunai.gaziantep.edu.tr/embed.js" defer
 *           data-api-url="https://gaunai.gaziantep.edu.tr/api/chat"></script>
 *
 * data-api-url verilmezse aşağıdaki DEFAULT_API_URL kullanılır.
 */
(function () {
  "use strict";

  var DEFAULT_API_URL = "https://gaunai.gaziantep.edu.tr/api/chat";
  var scriptEl = document.currentScript;
  var API_URL = (scriptEl && scriptEl.dataset && scriptEl.dataset.apiUrl) || DEFAULT_API_URL;

  if (document.getElementById("gaunai-widget-host")) return; // çift yükleme koruması

  var host = document.createElement("div");
  host.id = "gaunai-widget-host";
  document.body.appendChild(host);
  var root = host.attachShadow({ mode: "open" });

  var STYLE = "\
    :host, * { box-sizing: border-box; }\
    .wrap { position: fixed; bottom: 20px; right: 20px; z-index: 2147483000;\
            display: flex; flex-direction: column; align-items: flex-end;\
            font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }\
    .fab { position: relative; width: 56px; height: 56px; border-radius: 9999px;\
           background: #112d5c; color: #fff; border: 1px solid rgba(255,255,255,.1);\
           box-shadow: 0 10px 25px -5px rgba(17,45,92,.5); cursor: pointer;\
           display: grid; place-items: center; transition: transform .15s, background .15s; }\
    .fab:hover { background: #0d234a; transform: scale(1.05); }\
    .fab svg { width: 28px; height: 28px; fill: currentColor; }\
    .badge { position: absolute; top: -2px; right: -2px; width: 14px; height: 14px;\
             border-radius: 9999px; background: #c62828; border: 2px solid #fff; }\
    .panel { display: none; flex-direction: column; overflow: hidden; margin-bottom: 12px;\
             width: 352px; max-width: calc(100vw - 40px); height: 512px;\
             max-height: calc(100vh - 96px); border-radius: 16px; background: #fff;\
             box-shadow: 0 24px 50px -12px rgba(17,45,92,.45);\
             border: 1px solid rgba(0,0,0,.05); animation: pop .18s ease-out; }\
    .panel.open { display: flex; }\
    @keyframes pop { from { opacity:0; transform: translateY(16px) scale(.96);} to {opacity:1; transform:none;} }\
    header { display: flex; align-items: center; gap: 12px; background: #112d5c; color: #fff;\
              padding: 12px 16px; }\
    header .icon { width: 36px; height: 36px; border-radius: 9999px; background: rgba(255,255,255,.15);\
              display: grid; place-items: center; border: 1px solid rgba(255,255,255,.25); flex-shrink:0; }\
    header .icon svg { width: 20px; height: 20px; fill: currentColor; }\
    header .title { min-width: 0; flex: 1; }\
    header h1 { margin: 0; font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }\
    header .status { display: flex; align-items: center; gap: 6px; font-size: 11px; color: rgba(255,255,255,.7); margin: 2px 0 0; }\
    header .dot { width: 8px; height: 8px; border-radius: 9999px; background: #34d399; }\
    header button { background: none; border: none; color: rgba(255,255,255,.8); cursor: pointer;\
              width: 32px; height: 32px; border-radius: 9999px; display: grid; place-items: center; }\
    header button:hover { background: rgba(255,255,255,.15); color: #fff; }\
    header button svg { width: 20px; height: 20px; }\
    .msgs { flex: 1; overflow-y: auto; background: #f9fafb; padding: 16px; display: flex;\
              flex-direction: column; gap: 12px; }\
    .row { display: flex; }\
    .row.user { justify-content: flex-end; }\
    .row.bot { justify-content: flex-start; }\
    .bubble-user { max-width: 80%; border-radius: 16px 16px 4px 16px; background: #e0f2fe;\
              padding: 8px 14px; font-size: 14px; color: #1e293b; }\
    .bot-col { max-width: 85%; }\
    .bubble-bot { border-radius: 16px 16px 16px 4px; border: 1px solid #e2e8f0; background: #fff;\
              padding: 10px 14px; font-size: 14px; color: #1e293b; line-height: 1.55; }\
    .bubble-bot p { margin: 0 0 8px; } .bubble-bot p:last-child { margin-bottom: 0; }\
    .bubble-bot ol, .bubble-bot ul { margin: 4px 0 8px; padding-left: 20px; }\
    .bubble-bot li { margin: 3px 0; padding-left: 2px; }\
    .bubble-bot strong { font-weight: 600; color: #0f172a; }\
    .bubble-bot a { color: #112d5c; text-decoration: underline; word-break: break-word; }\
    .faq { margin-top: 8px; }\
    .faq-title { font-size: 12px; font-weight: 600; color: #475569; margin: 0 0 6px; }\
    .faq-list { display: flex; flex-direction: column; gap: 6px; }\
    .faq-chip { text-align: left; background: #fff; border: 1px solid #dbe2ea; color: #112d5c;\
              border-radius: 10px; padding: 8px 12px; font-size: 13px; cursor: pointer; line-height: 1.4;\
              transition: background .12s, border-color .12s; }\
    .faq-chip:hover { background: #eef4ff; border-color: #112d5c; }\
    .fb { margin-top: 6px; display: flex; gap: 6px; color: #cbd5e1; }\
    .fb button { background: none; border: none; cursor: pointer; font-size: 12px; border-radius: 9999px;\
              padding: 2px 6px; }\
    .fb button:hover { background: #f1f5f9; }\
    .typing { border-radius: 16px 16px 16px 4px; border: 1px solid #e2e8f0; background: #fff;\
              padding: 12px 16px; display: inline-flex; gap: 4px; }\
    .typing span { width: 8px; height: 8px; border-radius: 9999px; background: #94a3b8;\
              animation: blink 1.2s infinite; }\
    .typing span:nth-child(2) { animation-delay: .2s; }\
    .typing span:nth-child(3) { animation-delay: .4s; }\
    @keyframes blink { 0%,80%,100% {opacity:.2;} 40% {opacity:1;} }\
    form { display: flex; align-items: flex-end; gap: 8px; border-top: 1px solid #e2e8f0;\
              background: #fff; padding: 12px; }\
    input { min-width: 0; flex: 1; border-radius: 9999px; border: 1px solid #cbd5e1; background: #f8fafc;\
              padding: 10px 16px; font-size: 14px; color: #1e293b; outline: none; }\
    input:focus { border-color: #112d5c; box-shadow: 0 0 0 3px rgba(17,45,92,.15); background:#fff; }\
    .send { flex-shrink: 0; width: 44px; height: 44px; border-radius: 9999px; background: #112d5c;\
              color: #fff; border: none; cursor: pointer; display: grid; place-items: center; }\
    .send:hover { background: #0d234a; }\
    .send:disabled, input:disabled { opacity: .5; cursor: default; }\
    .send svg { width: 20px; height: 20px; fill: currentColor; }\
    ::-webkit-scrollbar { width: 6px; } ::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 9999px; }\
  ";

  var HTML = "\
    <div class='wrap'>\
      <section class='panel' id='panel' role='dialog' aria-label='GaunAI Asistan'>\
        <header>\
          <span class='icon'><svg viewBox='0 0 24 24'><path d='M12 3 1 8l11 5 9-4.09V15h2V8L12 3zM5 13.18v3.5L12 20l7-3.32v-3.5L12 16l-7-2.82z'/></svg></span>\
          <div class='title'><h1>GaunAI Asistan</h1><p class='status'><span class='dot'></span>Çevrimiçi</p></div>\
          <button id='close' aria-label='Kapat'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round'><path d='M6 6l12 12M18 6L6 18'/></svg></button>\
        </header>\
        <div class='msgs' id='msgs'></div>\
        <form id='form'>\
          <input id='input' type='text' autocomplete='off' placeholder='Sorunuzu yazın...' />\
          <button type='submit' class='send' id='send' aria-label='Gönder'><svg viewBox='0 0 24 24'><path d='M3.4 20.4 21 12 3.4 3.6 3 10l12 2-12 2 .4 6.4z'/></svg></button>\
        </form>\
      </section>\
      <button class='fab' id='fab' aria-label='GaunAI Asistanı Aç'>\
        <svg viewBox='0 0 24 24'><path d='M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H8l-5 4V6a2 2 0 0 1 1-2z'/></svg>\
        <span class='badge' id='badge'></span>\
      </button>\
    </div>";

  var styleTag = document.createElement("style");
  styleTag.textContent = STYLE;
  root.appendChild(styleTag);
  var wrapTag = document.createElement("div");
  wrapTag.innerHTML = HTML;
  root.appendChild(wrapTag);

  var panel = root.getElementById("panel");
  var fab = root.getElementById("fab");
  var badge = root.getElementById("badge");
  var closeBtn = root.getElementById("close");
  var form = root.getElementById("form");
  var input = root.getElementById("input");
  var sendBtn = root.getElementById("send");
  var msgs = root.getElementById("msgs");

  var greeted = false;
  var chatHistory = [];
  var HISTORY_MAX = 4;

  var scrollDown = function () { msgs.scrollTop = msgs.scrollHeight; };
  var escapeHtml = function (s) {
    return s.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  // Satır içi markdown: **kalın**, [metin](url) ve çıplak URL'ler → güvenli <a>.
  // Önce HTML escape; sonra link/kalın syntax'ını token'la yerine koyup en sona
  // gerçek <a>/<strong>'a çeviririz (iç içe link / çift işleme olmasın diye).
  var mdInline = function (raw) {
    var s = escapeHtml(raw), links = [];
    var stash = function (html) { links.push(html); return "@@GAUNLINK" + (links.length - 1) + "@@"; };
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function (_, t, u) {
      return stash("<a href='" + u + "' target='_blank' rel='noopener noreferrer'>" + t + "</a>");
    });
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(https?:\/\/[^\s<]+)/g, function (u) {
      return stash("<a href='" + u + "' target='_blank' rel='noopener noreferrer'>" + u + "</a>");
    });
    return s.replace(/@@GAUNLINK(\d+)@@/g, function (_, i) { return links[Number(i)]; });
  };

  // Gövde → paragraf/liste: boş satır = paragraf ayracı; ardışık "1."/"-" satırları
  // <ol>/<ul>; diğerleri <p> (içteki tek satır sonları <br>).
  var renderBody = function (bodyText) {
    var lines = bodyText.split(/\r?\n/), out = [], para = [], list = null, ltype = null;
    var flushP = function () { if (para.length) { out.push("<p>" + para.map(mdInline).join("<br>") + "</p>"); para = []; } };
    var flushL = function () { if (list) { out.push("<" + ltype + ">" + list.join("") + "</" + ltype + ">"); list = null; ltype = null; } };
    lines.forEach(function (line) {
      if (!line.trim()) { flushP(); flushL(); return; }
      var ol = line.match(/^\s*\d+[.\)]\s+(.*)$/), ul = line.match(/^\s*[-*•]\s+(.*)$/);
      if (ol) { flushP(); if (ltype && ltype !== "ol") flushL(); ltype = "ol"; list = list || []; list.push("<li>" + mdInline(ol[1]) + "</li>"); }
      else if (ul) { flushP(); if (ltype && ltype !== "ul") flushL(); ltype = "ul"; list = list || []; list.push("<li>" + mdInline(ul[1]) + "</li>"); }
      else { flushL(); para.push(line); }
    });
    flushP(); flushL();
    return out.join("");
  };

  function renderBotHtml(text) {
    var lines = text.split(/\r?\n/), body = [], sources = [];
    lines.forEach(function (line) {
      var m = line.match(/^\s*🔗?\s*Kaynak\s*:\s*(.+)$/i);
      if (m) { (m[1].match(/https?:\/\/[^\s\]\)]+/g) || []).forEach(function (u) { sources.push(u); }); }
      else { body.push(line); }
    });
    var html = renderBody(body.join("\n"));
    sources.forEach(function (u) {
      html += "<a href='" + escapeHtml(u) + "' target='_blank' rel='noopener noreferrer'>🔗 Kaynak: " + escapeHtml(u) + "</a><br>";
    });
    return html;
  }

  function addUser(text) {
    var row = document.createElement("div");
    row.className = "row user";
    row.innerHTML = "<div class='bubble-user'></div>";
    row.firstChild.textContent = text;
    msgs.appendChild(row);
    scrollDown();
  }

  function addBot(html, logId) {
    var row = document.createElement("div");
    row.className = "row bot";
    var col = document.createElement("div");
    col.className = "bot-col";
    var bubble = document.createElement("div");
    bubble.className = "bubble-bot";
    bubble.innerHTML = html;
    col.appendChild(bubble);
    if (logId != null) {
      var fb = document.createElement("div");
      fb.className = "fb";
      fb.innerHTML = "<button type='button' data-score='1' aria-label='Faydalı'>👍</button>" +
                      "<button type='button' data-score='-1' aria-label='Faydasız'>👎</button>";
      fb.querySelectorAll("button").forEach(function (btn) {
        btn.addEventListener("click", function () { sendFeedback(logId, Number(btn.dataset.score), fb); });
      });
      col.appendChild(fb);
    }
    row.appendChild(col);
    msgs.appendChild(row);
    scrollDown();
  }

  function sendFeedback(logId, score, fbEl) {
    if (fbEl.dataset.done) return;
    fbEl.dataset.done = "1";
    fbEl.querySelectorAll("button").forEach(function (b) { b.disabled = true; b.style.opacity = ".4"; });
    var apiRoot = API_URL.replace(/\/api\/chat\/?$/, "");
    fetch(apiRoot + "/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ log_id: logId, score: score }),
    }).catch(function () {});
  }

  function addTyping() {
    var row = document.createElement("div");
    row.className = "row bot";
    row.innerHTML = "<div class='typing'><span></span><span></span><span></span></div>";
    msgs.appendChild(row);
    scrollDown();
    return row;
  }

  function openPanel() {
    panel.classList.add("open");
    fab.style.display = "none";
    if (!greeted) {
      addBot(renderBotHtml(
        "Merhaba! Ben GaunAI Asistan. Ders kaydı, yatay geçiş, sınav itirazı, " +
        "personel iletişimi gibi konularda size yardımcı olabilirim. Nasıl yardımcı olabilirim?"));
      renderFaq();
      greeted = true;
    }
    setTimeout(function () { input.focus(); }, 50);
  }
  function closePanel() {
    panel.classList.remove("open");
    fab.style.display = "";
  }

  fab.addEventListener("click", openPanel);
  closeBtn.addEventListener("click", closePanel);

  // Sıkça Sorulan Sorular — karşılamadan sonra tıklanabilir liste olarak eklenir;
  // tıklanınca soru normal sohbet akışına gönderilir (RAG cevabı + 👍/👎).
  var FAQ = [
    "Kayıt dondurma nasıl yapılır?",
    "Yatay geçiş şartları nelerdir?",
    "Sınav sonucuma nasıl itiraz ederim?",
    "İkinci nüsha diploma nasıl alınır?",
    "Kütüphane nerede?",
    "Yemekhanede bugün ne var?"
  ];
  function renderFaq() {
    var row = document.createElement("div");
    row.className = "row bot";
    var col = document.createElement("div");
    col.className = "bot-col";
    var box = document.createElement("div");
    box.className = "faq";
    var title = document.createElement("div");
    title.className = "faq-title";
    title.textContent = "Sıkça Sorulan Sorular";
    box.appendChild(title);
    var list = document.createElement("div");
    list.className = "faq-list";
    FAQ.forEach(function (q) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "faq-chip";
      b.textContent = q;
      b.addEventListener("click", function () { submitQuestion(q); });
      list.appendChild(b);
    });
    box.appendChild(list);
    col.appendChild(box);
    row.appendChild(col);
    msgs.appendChild(row);
    scrollDown();
  }

  function submitQuestion(q) {
    q = (q || "").trim();
    if (!q || input.disabled) return;
    addUser(q);
    input.value = "";
    input.disabled = sendBtn.disabled = true;
    var typing = addTyping();

    fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, history: chatHistory }),
    })
      .then(function (res) {
        typing.remove();
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        var botText = data.answer || "Bilmiyorum.";
        addBot(renderBotHtml(botText), data.log_id);
        chatHistory.push({ role: "user", content: q });
        chatHistory.push({ role: "assistant", content: botText });
        while (chatHistory.length > HISTORY_MAX) chatHistory.shift();
      })
      .catch(function (err) {
        typing.remove();
        var row = document.createElement("div");
        row.className = "row bot";
        row.innerHTML = "<div class='bubble-bot' style='color:#a31f1f'>Bağlantı hatası: sunucuya ulaşılamadı.</div>";
        msgs.appendChild(row);
        scrollDown();
      })
      .finally(function () {
        input.disabled = sendBtn.disabled = false;
        input.focus();
      });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    submitQuestion(input.value);
  });
})();
