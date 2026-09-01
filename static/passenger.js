const LANG_SPEECH = { ko: "ko-KR", en: "en-US", ja: "ja-JP", zh: "zh-CN" };
const UI_TEXT = {
  ko: {
    language: "언어 선택", talk: "기사에게 말하기", placeholder: "메시지를 입력하세요...",
    stt: "🎤 음성 입력", send: "기사에게 전송", sttDefault: "음성 입력에는 마이크 권한이 필요합니다.",
    sttInsecure: "음성 입력은 HTTPS(또는 localhost)에서만 사용할 수 있습니다. 텍스트나 빠른 문장을 사용하세요.",
    sttUnsupported: "이 브라우저는 음성 입력을 지원하지 않습니다. 텍스트를 사용하세요.",
    listening: "🔴 듣는 중...", recognized: "음성을 텍스트로 변환했습니다. 내용을 확인한 뒤 전송하세요.",
    denied: "마이크 권한이 거부되었습니다. 사이트 설정에서 마이크를 허용하거나 텍스트를 사용하세요.",
    noMic: "사용 가능한 휴대폰 마이크를 찾지 못했습니다. 텍스트를 사용하세요.",
    sttError: "음성 입력 오류", quick: "빠른 문장", messages: "기사 메시지", guide: "관광 안내 카드",
    fare: "요금 환산", convert: "환산", converting: "환율 조회 중...", help: "사람의 도움이 필요한가요?",
    helpBody: "자동 번역으로 해결하기 어려운 관광 문의는 한국관광공사 1330을 이용하세요.", sos: "SOS · 1330 전화",
  },
  en: {
    language: "Choose language", talk: "Talk to driver", placeholder: "Type a message...",
    stt: "🎤 Speech", send: "Send → Driver", sttDefault: "Speech input requires microphone permission.",
    sttInsecure: "Speech input requires HTTPS (or localhost). Use text or a quick phrase.",
    sttUnsupported: "This browser does not support speech input. Please type your message.",
    listening: "🔴 Listening...", recognized: "Speech converted to text. Check it before sending.",
    denied: "Microphone permission was denied. Allow it in site settings or type your message.",
    noMic: "No phone microphone is available. Please type your message.",
    sttError: "Speech input error", quick: "Quick phrases", messages: "Driver messages", guide: "Smart Guide Cards",
    fare: "Fare converter", convert: "Convert", converting: "Checking exchange rate...", help: "Need human help?",
    helpBody: "Call the 1330 Korea Travel Helpline when automated translation is not enough.", sos: "SOS · Call 1330",
  },
  ja: {
    language: "言語を選択", talk: "運転手に伝える", placeholder: "メッセージを入力してください...",
    stt: "🎤 音声入力", send: "運転手に送信", sttDefault: "音声入力にはマイクの許可が必要です。",
    sttInsecure: "音声入力にはHTTPS（またはlocalhost）が必要です。テキストか定型文をご利用ください。",
    sttUnsupported: "このブラウザは音声入力に対応していません。テキストをご利用ください。",
    listening: "🔴 聞き取り中...", recognized: "音声をテキストに変換しました。確認して送信してください。",
    denied: "マイクが許可されていません。サイト設定で許可するか、テキストをご利用ください。",
    noMic: "利用可能なマイクがありません。テキストをご利用ください。",
    sttError: "音声入力エラー", quick: "定型文", messages: "運転手からのメッセージ", guide: "観光案内カード",
    fare: "料金換算", convert: "換算", converting: "為替レートを確認中...", help: "人の助けが必要ですか？",
    helpBody: "自動翻訳で解決できない観光相談は韓国観光案内1330をご利用ください。", sos: "SOS · 1330に電話",
  },
  zh: {
    language: "选择语言", talk: "告诉司机", placeholder: "请输入消息...",
    stt: "🎤 语音输入", send: "发送给司机", sttDefault: "语音输入需要麦克风权限。",
    sttInsecure: "语音输入需要HTTPS（或localhost）。请使用文字或快捷短语。",
    sttUnsupported: "此浏览器不支持语音输入。请使用文字输入。",
    listening: "🔴 正在聆听...", recognized: "语音已转换为文字，请确认后发送。",
    denied: "麦克风权限被拒绝。请在网站设置中允许，或使用文字输入。",
    noMic: "找不到可用的手机麦克风。请使用文字输入。",
    sttError: "语音输入错误", quick: "快捷短语", messages: "司机消息", guide: "旅游指南卡",
    fare: "车费换算", convert: "换算", converting: "正在查询汇率...", help: "需要人工帮助吗？",
    helpBody: "自动翻译无法解决时，请拨打韩国旅游咨询热线1330。", sos: "SOS · 拨打1330",
  },
};

let currentLang = "en";
let lastMessageId = 0;
let bootstrapData = null;

const el = (id) => document.getElementById(id);

function ui() {
  return UI_TEXT[currentLang] || UI_TEXT.en;
}

function setSttState(state, detail = "") {
  const hint = el("sttHint");
  const text = ui();
  const messages = {
    default: text.sttDefault,
    insecure: text.sttInsecure,
    unsupported: text.sttUnsupported,
    recognized: text.recognized,
    denied: text.denied,
    noMic: text.noMic,
  };
  hint.dataset.state = state;
  hint.dataset.detail = detail;
  hint.textContent = messages[state] || `${text.sttError}: ${detail}`;
}

function applyLanguageUI() {
  const text = ui();
  document.documentElement.lang = currentLang === "zh" ? "zh-CN" : currentLang;
  el("languageHeading").textContent = text.language;
  el("talkHeading").textContent = text.talk;
  el("messageInput").placeholder = text.placeholder;
  el("sttBtn").textContent = text.stt;
  el("sendBtn").textContent = text.send;
  el("quickHeading").textContent = text.quick;
  el("messagesHeading").textContent = text.messages;
  el("guideHeading").textContent = text.guide;
  el("fareHeading").textContent = text.fare;
  el("convertBtn").textContent = text.convert;
  el("helpHeading").textContent = text.help;
  el("helpBody").textContent = text.helpBody;
  el("sosCall").textContent = text.sos;
  setSttState(el("sttHint").dataset.state || "default", el("sttHint").dataset.detail || "");
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "요청 실패");
  return data;
}

function speak(text, lang) {
  if (!("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = LANG_SPEECH[lang] || "en-US";
  u.rate = 1.0;
  u.volume = 0.95;
  speechSynthesis.speak(u);
}

function setNetwork(ok) {
  const badge = el("netBadge");
  badge.textContent = ok ? "ONLINE" : "OFFLINE";
  badge.className = ok ? "badge ok" : "badge bad";
}

async function setLanguage(lang) {
  currentLang = lang;
  document.querySelectorAll("#langButtons button").forEach((b) => {
    b.classList.toggle("active", b.dataset.lang === lang);
  });

  await api("/api/session/language", {
    method: "POST",
    body: JSON.stringify({ lang }),
  });

  applyLanguageUI();
  renderQuickPhrases();
  renderGuidesList();
}

function renderQuickPhrases() {
  const root = el("quickPhrases");
  root.innerHTML = "";

  bootstrapData.passenger_quick_phrases.forEach((phrase) => {
    const b = document.createElement("button");
    b.textContent = phrase.translations[currentLang];
    b.onclick = async () => {
      try {
        await api("/api/passenger/quick", {
          method: "POST",
          body: JSON.stringify({ phrase_id: phrase.id, lang: currentLang }),
        });
        setNetwork(true);
      } catch (e) {
        alert(e.message);
      }
    };
    root.appendChild(b);
  });
}

function renderGuidesList() {
  const select = el("guideSelect");
  const oldValue = select.value;
  select.innerHTML = "";

  bootstrapData.guides.forEach((guide) => {
    const o = document.createElement("option");
    o.value = guide.id;
    o.textContent = guide.name[currentLang];
    select.appendChild(o);
  });

  if (oldValue) select.value = oldValue;
  select.onchange = renderGuide;
  renderGuide();
}

function renderGuide() {
  const id = el("guideSelect").value || bootstrapData.guides[0]?.id;
  const guide = bootstrapData.guides.find((g) => g.id === id) || bootstrapData.guides[0];
  if (!guide) return;
  el("guideCard").innerHTML = `<strong>${guide.name[currentLang]}</strong><br>${guide.body[currentLang]}`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }[c]));
}

function appendMessage(message) {
  const isConfirmation = message.kind === "driver_confirm";
  if (message.sender !== "driver" && !isConfirmation) return;

  const bubble = document.createElement("div");
  bubble.className = `bubble driver${isConfirmation ? " confirmation" : ""}`;
  if (isConfirmation) {
    bubble.innerHTML = `
      <div class="meta">✓ DRIVER CONFIRM</div>
      <div>${escapeHtml(message.translated_text)}</div>
    `;
  } else {
    bubble.innerHTML = `
      <div class="meta">${message.kind === "ptt_speech" ? "🎤 DRIVER" : "DRIVER"} · ${message.target_lang.toUpperCase()}</div>
      <div>${escapeHtml(message.translated_text)}</div>
      <div class="sub">${escapeHtml(message.source_text)}</div>
    `;
  }

  el("conversation").appendChild(bubble);
  el("conversation").scrollTop = el("conversation").scrollHeight;
  speak(message.translated_text, message.target_lang);
}

async function poll() {
  try {
    const data = await api(`/api/messages?after=${lastMessageId}`);
    setNetwork(true);

    for (const m of data.messages) {
      lastMessageId = Math.max(lastMessageId, m.id);
      appendMessage(m);
    }
  } catch (_) {
    setNetwork(false);
  } finally {
    setTimeout(poll, 900);
  }
}

function setupSTT() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!window.isSecureContext) {
    el("sttBtn").disabled = true;
    setSttState("insecure");
    return;
  }

  if (!Recognition) {
    el("sttBtn").disabled = true;
    setSttState("unsupported");
    return;
  }

  const recognition = new Recognition();
  recognition.interimResults = false;
  recognition.continuous = false;

  el("sttBtn").onclick = () => {
    recognition.lang = LANG_SPEECH[currentLang];
    el("sttBtn").textContent = ui().listening;
    try { recognition.start(); } catch (_) {}
  };

  recognition.onresult = (event) => {
    el("messageInput").value = event.results[0][0].transcript;
    setSttState("recognized");
  };

  recognition.onend = () => {
    el("sttBtn").textContent = ui().stt;
  };

  recognition.onerror = (event) => {
    el("sttBtn").textContent = ui().stt;
    setSttState(
      event.error === "not-allowed"
        ? "denied"
        : event.error === "audio-capture" ? "noMic" : "error",
      event.error
    );
  };
}

async function init() {
  bootstrapData = await api("/api/bootstrap");
  currentLang = bootstrapData.passenger_lang || "en";
  applyLanguageUI();

  document.querySelectorAll("#langButtons button").forEach((b) => {
    b.onclick = () => setLanguage(b.dataset.lang).catch((e) => alert(e.message));
    b.classList.toggle("active", b.dataset.lang === currentLang);
  });

  renderQuickPhrases();
  renderGuidesList();
  setupSTT();

  el("sendBtn").onclick = async () => {
    const text = el("messageInput").value.trim();
    if (!text) return;

    try {
      await api("/api/passenger/send", {
        method: "POST",
        body: JSON.stringify({ text, lang: currentLang }),
      });
      el("messageInput").value = "";
      setNetwork(true);
    } catch (e) {
      setNetwork(false);
      alert(e.message);
    }
  };

  el("convertBtn").onclick = async () => {
    const amount = Number(el("krwAmount").value || 0);
    const to = el("currencySelect").value;

    el("currencyResult").textContent = ui().converting;

    try {
      const result = await api(`/api/currency?amount=${encodeURIComponent(amount)}&to=${encodeURIComponent(to)}`);
      el("currencyResult").textContent =
        `${amount.toLocaleString()} KRW ≈ ${result.converted.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${result.currency}`
        + (result.date ? ` · 기준일 ${result.date}` : "");
    } catch (e) {
      el("currencyResult").textContent = e.message;
    }
  };

  poll();
}

init().catch((e) => {
  setNetwork(false);
  alert(e.message);
});
