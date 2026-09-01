const LANG_LABEL = { ko: "KO", en: "EN", ja: "JA", zh: "ZH" };

let lastMessageId = 0;
let latestPassengerMessage = null;
let currentPassengerLang = "en";
let currentRideState = "START";
let bootstrapData = null;
let gpioAvailable = false;
let audioAvailable = false;
let audioInputEnabled = false;
let audioOutput = "3.5mm";

const HARDWARE_STATUS = {
  idle: { text: "🟢 SYSTEM READY", className: "idle" },
  request: { text: "🔵 승객 요청", className: "request" },
  recording: { text: "🔵 외부 마이크 녹음 중", className: "recording" },
  processing: { text: "🔵 음성 인식 중", className: "processing" },
  confirmed: { text: "🟢 요청 확인", className: "confirmed" },
  offline: { text: "🔴 서버 연결 끊김", className: "error" },
  translation_unavailable: { text: "🔴 번역 연결 오류", className: "error" },
  error: { text: "🔴 GPIO/시스템 오류", className: "error" },
};

const el = (id) => document.getElementById(id);

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "요청 실패");
  return data;
}

function setNetwork(ok) {
  const badge = el("driverNetBadge");
  badge.textContent = ok ? "ONLINE" : "OFFLINE";
  badge.className = ok ? "badge ok" : "badge bad";
  if (!ok) setHardwareStatus("offline");
}

function setHardwareStatus(status) {
  const root = el("hardwareStatus");
  const config = HARDWARE_STATUS[status] || HARDWARE_STATUS.idle;
  const suffix = [
    gpioAvailable ? "" : "GPIO OFF",
    audioInputEnabled && !audioAvailable ? "MIC ERROR" : "",
    !audioInputEnabled ? `${audioOutput} OUT` : "",
  ].filter(Boolean).join(" · ");
  root.textContent = `${config.text}${suffix ? ` · ${suffix}` : ""}`;
  root.className = `hardware-status ${config.className}`;
}

function speakKorean(text) {
  if (!("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();

  const u = new SpeechSynthesisUtterance(text);
  u.lang = "ko-KR";
  u.rate = 1.0;
  u.volume = Number(el("ttsVolume").value || 0.9);

  speechSynthesis.speak(u);
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }[c]));
}

function renderContextPhrases(phrases) {
  const root = el("contextQuick");
  root.innerHTML = "";

  phrases.forEach((phrase) => {
    const b = document.createElement("button");
    b.textContent = phrase.translations.ko;
    b.onclick = async () => {
      try {
        await api("/api/driver/quick", {
          method: "POST",
          body: JSON.stringify({ phrase_id: phrase.id }),
        });
      } catch (e) {
        alert(e.message);
      }
    };
    root.appendChild(b);
  });
}

function appendLog(message) {
  const bubble = document.createElement("div");
  const isSystem = message.sender === "system";
  bubble.className = `bubble ${isSystem ? "system" : message.sender}`;

  const label = isSystem
    ? "SYSTEM"
    : message.sender === "passenger" ? "PASSENGER" : "DRIVER";
  const mainText = isSystem
    ? message.translated_text
    : message.sender === "passenger" ? message.translated_text : message.source_text;
  const subText = isSystem
    ? ""
    : message.sender === "passenger" ? message.source_text : message.translated_text;

  bubble.innerHTML = `
    <div class="meta">${label} · ${message.mode || ""}</div>
    <div>${escapeHtml(mainText)}</div>
    ${subText ? `<div class="sub">${escapeHtml(subText)}</div>` : ""}
  `;

  el("driverConversation").appendChild(bubble);
  el("driverConversation").scrollTop = el("driverConversation").scrollHeight;
}

function onPassengerMessage(message) {
  latestPassengerMessage = message;
  currentPassengerLang = message.source_lang || currentPassengerLang;

  el("passengerLangBadge").textContent =
    LANG_LABEL[currentPassengerLang] || currentPassengerLang.toUpperCase();

  el("latestPassenger").textContent = message.translated_text;

  const hits = message.correction_hits || [];
  el("correctionInfo").textContent = hits.length
    ? `로컬 보정: ${hits.join(" · ")}`
    : message.kind === "quick_phrase"
      ? "검수된 빠른 문장 · 오프라인 대응 가능"
      : "";

  speakKorean(message.translated_text);
}

function showConfirmFeedback() {
  el("confirmBtn").textContent = "✓ 확인 완료";
  setTimeout(() => {
    el("confirmBtn").textContent = "✓ 확인";
  }, 1200);
}

function handleSystemEvent(message) {
  if (message.kind === "driver_confirm") {
    setHardwareStatus(message.hardware_status || "confirmed");
    showConfirmFeedback();
    appendLog(message);
    return true;
  }

  if (message.kind === "hardware_replay") {
    speakKorean(message.translated_text);
    return true;
  }

  if (message.kind === "hardware_ptt") {
    const pressed = message.action === "pressed";
    const status = pressed
      ? (message.recording_started
          ? "recording"
          : (message.audio_input_enabled ? "error" : (message.restore_status || "idle")))
      : (message.processing_started ? "processing" : (message.restore_status || "idle"));
    setHardwareStatus(status);
    el("driverSttBtn").classList.toggle("ptt-active", pressed);
    el("driverSttBtn").title = pressed
      ? (message.recording_started
          ? "물리 PTT 입력으로 외부 마이크를 녹음하고 있습니다."
          : "Pi 4의 3.5mm 잭은 출력 전용입니다. 텍스트 입력을 사용하세요.")
      : "기사 음성 입력은 별도 USB 마이크/오디오 어댑터가 있어야 사용할 수 있습니다.";
    if (!message.audio_input_enabled) {
      el("driverSttHint").textContent =
        "PTT 감지됨 · 3.5mm 잭은 출력 전용이라 녹음하지 않았습니다.";
    }
    return true;
  }

  if (message.kind === "hardware_ptt_error") {
    setHardwareStatus("error");
    appendLog(message);
    return true;
  }

  return false;
}

async function poll() {
  try {
    const data = await api(`/api/messages?after=${lastMessageId}`);
    setNetwork(true);
    if (data.hardware) {
      gpioAvailable = Boolean(data.hardware.gpio_available);
      audioAvailable = Boolean(data.hardware.audio?.available);
      audioInputEnabled = Boolean(data.hardware.audio?.input_enabled);
      audioOutput = data.hardware.audio_output || "3.5mm";
      setHardwareStatus(data.hardware.status);
    }

    for (const m of data.messages) {
      lastMessageId = Math.max(lastMessageId, m.id);
      if (handleSystemEvent(m)) continue;
      appendLog(m);

      if (m.sender === "passenger") {
        onPassengerMessage(m);
        setHardwareStatus(
          m.mode === "translation_unavailable" ? "translation_unavailable" : "request"
        );
      }

      if (m.sender === "driver") {
        currentPassengerLang = m.target_lang || currentPassengerLang;
        el("passengerLangBadge").textContent =
          LANG_LABEL[currentPassengerLang] || currentPassengerLang.toUpperCase();
        if (m.kind === "ptt_speech") {
          setHardwareStatus(m.restore_status || "idle");
          el("driverSttBtn").textContent = "✓ 음성 전송 완료";
          setTimeout(() => {
            el("driverSttBtn").textContent = "🎤 PTT/STT";
          }, 1500);
        }
      }
    }
  } catch (_) {
    setNetwork(false);
  } finally {
    setTimeout(poll, 900);
  }
}

function setupDriverSTT() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (!audioInputEnabled) {
    el("driverSttBtn").disabled = true;
    el("driverSttBtn").textContent = "🎤 마이크 없음";
    el("driverSttBtn").title = "Pi 4의 3.5mm 잭은 오디오 출력 전용입니다.";
    el("driverSttHint").textContent =
      "3.5mm 이어폰으로 TTS를 들을 수 있습니다. 답변은 텍스트 또는 빠른 문장을 사용하세요.";
    return;
  }

  if (!window.isSecureContext) {
    el("driverSttBtn").disabled = true;
    el("driverSttHint").textContent =
      "브라우저 음성 입력은 HTTPS(또는 localhost)에서만 허용됩니다. 텍스트 입력을 사용하세요.";
    return;
  }

  if (!Recognition) {
    el("driverSttBtn").disabled = true;
    el("driverSttBtn").title = "이 브라우저는 Web Speech STT를 지원하지 않습니다.";
    el("driverSttHint").textContent =
      "이 브라우저는 음성 입력을 지원하지 않습니다. 텍스트 입력을 사용하세요.";
    return;
  }

  const recognition = new Recognition();
  recognition.lang = "ko-KR";
  recognition.interimResults = false;
  recognition.continuous = false;

  el("driverSttBtn").onclick = () => {
    el("driverSttBtn").textContent = "🔴 듣는 중...";
    try { recognition.start(); } catch (_) {}
  };

  recognition.onresult = (event) => {
    el("driverInput").value = event.results[0][0].transcript;
    el("driverSttHint").textContent = "음성을 텍스트로 변환했습니다. 내용을 확인한 뒤 전송하세요.";
  };

  recognition.onend = () => {
    el("driverSttBtn").textContent = "🎤 PTT/STT";
  };

  recognition.onerror = (event) => {
    el("driverSttBtn").textContent = "🎤 PTT/STT";
    el("driverSttHint").textContent = event.error === "not-allowed"
      ? "마이크 권한이 거부되었습니다. 브라우저 사이트 설정에서 마이크를 허용하거나 텍스트 입력을 사용하세요."
      : event.error === "audio-capture"
        ? "사용 가능한 마이크 입력이 없습니다. Pi 4의 3.5mm 잭은 출력 전용입니다."
        : `음성 입력 오류: ${event.error}. 텍스트 입력을 사용하세요.`;
  };
}

function setupTheme() {
  const hour = new Date().getHours();
  if (hour >= 19 || hour < 6) document.body.classList.add("dark");

  el("themeBtn").onclick = () => {
    document.body.classList.toggle("dark");
  };
}

async function init() {
  bootstrapData = await api("/api/bootstrap");
  currentPassengerLang = bootstrapData.passenger_lang || "en";
  currentRideState = bootstrapData.ride_state || "START";
  gpioAvailable = Boolean(bootstrapData.hardware?.gpio_available);
  audioAvailable = Boolean(bootstrapData.hardware?.audio?.available);
  audioInputEnabled = Boolean(bootstrapData.hardware?.audio?.input_enabled);
  audioOutput = bootstrapData.hardware?.audio_output || "3.5mm";
  setHardwareStatus(bootstrapData.hardware?.status || "idle");

  el("passengerLangBadge").textContent =
    LANG_LABEL[currentPassengerLang] || currentPassengerLang.toUpperCase();

  document.querySelectorAll("#rideStates button").forEach((b) => {
    b.classList.toggle("active", b.dataset.state === currentRideState);

    b.onclick = async () => {
      const result = await api("/api/ride/state", {
        method: "POST",
        body: JSON.stringify({ state: b.dataset.state }),
      });

      currentRideState = result.state;

      document.querySelectorAll("#rideStates button").forEach((x) => {
        x.classList.toggle("active", x.dataset.state === currentRideState);
      });

      renderContextPhrases(result.phrases);
    };
  });

  renderContextPhrases(bootstrapData.driver_context_phrases);

  el("replayBtn").onclick = async () => {
    try {
      await api("/api/driver/replay", { method: "POST", body: "{}" });
    } catch (e) {
      alert(e.message);
    }
  };

  el("confirmBtn").onclick = async () => {
    try {
      await api("/api/driver/confirm", { method: "POST", body: "{}" });
    } catch (e) {
      alert(e.message);
    }
  };

  el("driverSendBtn").onclick = async () => {
    const text = el("driverInput").value.trim();
    if (!text) return;

    try {
      await api("/api/driver/send", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      el("driverInput").value = "";
    } catch (e) {
      alert(e.message);
    }
  };

  setupDriverSTT();
  setupTheme();
  poll();
}

init().catch((e) => {
  setNetwork(false);
  alert(e.message);
});
