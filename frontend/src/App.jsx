import { useEffect, useRef, useState } from "react";

// ---------- YouTube IFrame API ----------
let ytReady;
function loadYT() {
  if (ytReady) return ytReady;
  ytReady = new Promise((resolve) => {
    if (window.YT?.Player) return resolve();
    window.onYouTubeIframeAPIReady = resolve;
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    document.head.appendChild(s);
  });
  return ytReady;
}

// iOS Safari пишет audio/mp4, Chrome/Android — webm
function pickMime() {
  for (const m of ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"]) {
    if (window.MediaRecorder?.isTypeSupported?.(m)) return m;
  }
  return "";
}

const HKEY = "shadowing:history";
function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HKEY)) || []; } catch { return []; }
}
function persistHistory(h) {
  try { localStorage.setItem(HKEY, JSON.stringify(h)); } catch { /* приватный режим */ }
}

const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

export default function App() {
  const [url, setUrl] = useState("");
  const [lang, setLang] = useState("en");
  const [data, setData] = useState(null);
  const [idx, setIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [playerErr, setPlayerErr] = useState("");
  const [history, setHistory] = useState(loadHistory); // [{ videoId, url, lang, title, idx, ts }]
  const [status, setStatus] = useState("idle"); // idle | listening | recording | comparing
  const [recs, setRecs] = useState({}); // id -> { url, blob }
  const [cmode, setCmode] = useState(null); // какой вариант Compare сейчас играет
  const [checks, setChecks] = useState({}); // id -> { loading | result | error }

  const playerRef = useRef(null);
  const timerRef = useRef(null);
  const runRef = useRef(0); // «номер запуска»: отмена увеличивает его
  const cancelRef = useRef(null);
  const recorderRef = useRef(null);
  const audioRef = useRef(null);
  const listRef = useRef(null);

  const sentences = data?.sentences ?? [];
  const cur = sentences[idx];

  // ---------- загрузка субтитров ----------
  function updateHistory(fn) {
    setHistory((h) => {
      const n = fn(h);
      persistHistory(n);
      return n;
    });
  }

  function removeItem(videoId) {
    updateHistory((h) => h.filter((x) => x.videoId !== videoId));
  }

  function load(e) {
    e.preventDefault();
    return open(url, lang);
  }

  async function open(u, l) {
    setError("");
    setLoading(true);
    try {
      const r = await fetch(`${import.meta.env.BASE_URL}api/transcript?url=${encodeURIComponent(u)}&lang=${l}`);
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Ошибка");
      if (!j.sentences.length) throw new Error("Субтитры пустые");
      setPlayerErr("");
      setData(j);
      const prev = history.find((h) => h.videoId === j.videoId);
      const start = Math.min(prev?.idx ?? 0, j.sentences.length - 1);
      updateHistory((h) => [
        { videoId: j.videoId, url: u, lang: l, title: j.title || prev?.title || "", idx: start, ts: Date.now() },
        ...h.filter((x) => x.videoId !== j.videoId),
      ].slice(0, 30));
      setIdx(start);
      setRecs({});
      setChecks({});
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // ---------- плеер ----------
  useEffect(() => {
    if (!data) return;
    let dead = false;
    loadYT().then(() => {
      if (dead) return;
      playerRef.current = new window.YT.Player("yt", {
        videoId: data.videoId,
        playerVars: { playsinline: 1, controls: 1, rel: 0, origin: window.location.origin },
        events: { onError: (e) => setPlayerErr(String(e.data)) },
      });
    });
    return () => {
      dead = true;
      clearInterval(timerRef.current);
      playerRef.current?.destroy?.();
      playerRef.current = null;
    };
  }, [data?.videoId]);

  // запоминаем, на какой фразе остановились
  useEffect(() => {
    if (!data) return;
    updateHistory((h) => h.map((x) => (x.videoId === data.videoId ? { ...x, idx } : x)));
  }, [idx, data?.videoId]);

  useEffect(() => {
    listRef.current?.querySelector(".on")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [idx]);

  function stopAll() {
    runRef.current++;
    clearInterval(timerRef.current);
    playerRef.current?.pauseVideo?.();
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.pause();
    }
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    cancelRef.current?.(); // освобождаем ожидающие промисы
    cancelRef.current = null;
  }

  // Играет оригинал [start, end]; резолвится в конце фрагмента или при отмене
  function playOriginal(s) {
    return new Promise((resolve) => {
      const p = playerRef.current;
      if (!p?.seekTo) return resolve();
      clearInterval(timerRef.current);
      cancelRef.current = resolve;
      p.seekTo(s.start, true);
      p.playVideo();
      timerRef.current = setInterval(() => {
        if (p.getCurrentTime() >= s.end) {
          clearInterval(timerRef.current);
          p.pauseVideo();
          resolve();
        }
      }, 80);
    });
  }

  // ---------- 1. Listen (повторное нажатие = стоп) ----------
  async function listen() {
    if (status === "listening") {
      stopAll();
      setStatus("idle");
      return;
    }
    stopAll();
    const token = runRef.current;
    setStatus("listening");
    await playOriginal(cur);
    if (token === runRef.current) setStatus("idle");
  }

  // ---------- 2. Record ----------
  async function toggleRecord() {
    if (status === "recording") {
      recorderRef.current.stop();
      return;
    }
    stopAll();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = pickMime();
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks = [];
      const id = cur.id;
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const type = rec.mimeType || mime || "audio/mp4";
        const blob = new Blob(chunks, { type });
        setRecs((r) => {
          if (r[id]) URL.revokeObjectURL(r[id].url);
          return { ...r, [id]: { url: URL.createObjectURL(blob), blob } };
        });
        setChecks((c) => { const n = { ...c }; delete n[id]; return n; });
        setStatus("idle");
      };
      recorderRef.current = rec;
      rec.start();
      setStatus("recording");
    } catch {
      setError("Нет доступа к микрофону. Проверьте разрешение в Safari и что сайт открыт по HTTPS.");
    }
  }

  // ---------- 3. Compare ----------
  // На iOS audio.play() вне тапа блокируется, поэтому «разблокируем» элемент в момент тапа
  function unlockAudio(url) {
    const a = audioRef.current;
    a.src = url;
    a.muted = true;
    return a.play().then(() => {
      a.pause();
      a.currentTime = 0;
      a.muted = false;
    });
  }

  function playMine() {
    return new Promise((resolve) => {
      const a = audioRef.current;
      cancelRef.current = resolve;
      a.onended = resolve;
      a.onerror = resolve;
      a.currentTime = 0;
      a.play().catch(resolve);
    });
  }

  // повторное нажатие на активную кнопку = стоп
  async function compare(mode) {
    if (status === "comparing") {
      stopAll();
      setStatus("idle");
      return;
    }
    const rec = recs[cur.id];
    if (!rec) return;
    stopAll();
    const token = runRef.current;
    const alive = () => token === runRef.current;
    setCmode(mode);
    setStatus("comparing");
    try {
      await unlockAudio(rec.url);
      if (!alive()) return;
      if (mode === "orig") await playOriginal(cur);
      else if (mode === "mine") await playMine();
      else {
        await playOriginal(cur);
        if (!alive()) return;
        await new Promise((r) => setTimeout(r, 250));
        if (!alive()) return;
        await playMine();
      }
    } finally {
      if (alive()) setStatus("idle");
    }
  }

  // ---------- Whisper ----------
  async function check() {
    const id = cur.id;
    const r0 = recs[id];
    setChecks((c) => ({ ...c, [id]: { loading: true } }));
    try {
      const fd = new FormData();
      fd.append("audio", r0.blob, "rec." + (r0.blob.type.includes("mp4") ? "mp4" : "webm"));
      fd.append("text", cur.text);
      fd.append("lang", lang.split("-")[0]);
      const r = await fetch(`${import.meta.env.BASE_URL}api/check`, { method: "POST", body: fd });
      const raw = await r.text();
      let j = {};
      try { j = JSON.parse(raw); } catch { /* пустой или не-JSON ответ */ }
      if (!r.ok || !raw) {
        throw new Error(j.detail || `Сервер ответил ${r.status}${raw ? "" : " без текста"}. Смотрите логи: docker compose logs shadowing`);
      }
      setChecks((c) => ({ ...c, [id]: { result: j } }));
    } catch (e) {
      setChecks((c) => ({ ...c, [id]: { error: e.message } }));
    }
  }

  function go(i) {
    if (i < 0 || i >= sentences.length) return;
    stopAll();
    setStatus("idle");
    setIdx(i);
  }

  function nudge(key, d) {
    setData((prev) => ({
      ...prev,
      sentences: prev.sentences.map((s) =>
        s.id === cur.id ? { ...s, [key]: Math.max(0, +(s[key] + d).toFixed(2)) } : s
      ),
    }));
  }

  // ---------- UI ----------
  if (!data) {
    return (
      <main className="home">
        <h1>Shadowing</h1>
        <p className="lead">Вставьте ссылку на видео с субтитрами. Слушайте фразу, повторяйте вслух, сравнивайте.</p>
        <form onSubmit={load}>
          <input
            type="url"
            inputMode="url"
            placeholder="https://youtube.com/watch?v=…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
          <label className="lang">
            Язык субтитров
            <input value={lang} onChange={(e) => setLang(e.target.value.trim())} maxLength={5} />
          </label>
          <button className="primary" disabled={loading}>
            {loading ? "Загружаю…" : "Загрузить"}
          </button>
        </form>
        {error && <p className="err">{error}</p>}
        {history.length > 0 && (
          <section className="hist">
            <h2>Недавние</h2>
            <ul>
              {history.map((h) => (
                <li key={h.videoId}>
                  <button className="item" onClick={() => open(h.url, h.lang)} disabled={loading}>
                    <img src={`https://i.ytimg.com/vi/${h.videoId}/mqdefault.jpg`} alt="" loading="lazy" />
                    <span>
                      <b>{h.title || h.videoId}</b>
                      <small>{h.lang} · фраза {h.idx + 1}</small>
                    </span>
                  </button>
                  <button className="ghost" aria-label="Удалить" onClick={() => removeItem(h.videoId)}>×</button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
    );
  }

  const rec = recs[cur.id];
  const chk = checks[cur.id];
  const busy = status !== "idle";

  return (
    <main className="train">
      <header>
        <button className="ghost" onClick={() => { stopAll(); setStatus("idle"); setData(null); }}>Назад</button>
        <span>{idx + 1} / {sentences.length}</span>
        {data.generated && <span className="tag" title="Автосубтитры: границы могут быть неточными">авто</span>}
      </header>

      <div className="video"><div id="yt" /></div>
      {playerErr && (
        <p className="err">
          Видео не воспроизводится (код {playerErr}).{" "}
          {{ "100": "Видео удалено или приватное.", "101": "Автор запретил встраивание.", "150": "Автор запретил встраивание.", "153": "YouTube не получил адрес сайта." }[playerErr] || ""}{" "}
          Попробуйте другое видео (например, TED).
        </p>
      )}

      <section className="card">
        <p className="sentence">
          {chk?.result
            ? chk.result.words.map((w, i) => <span key={i} className={"w-" + w.s}>{w.t} </span>)
            : cur.text}
        </p>
        <div className="nudge">
          <span>{fmt(cur.start)}–{fmt(cur.end)}</span>
          <button className="ghost" onClick={() => nudge("start", -0.3)}>начало −</button>
          <button className="ghost" onClick={() => nudge("start", 0.3)}>+</button>
          <button className="ghost" onClick={() => nudge("end", -0.3)}>конец −</button>
          <button className="ghost" onClick={() => nudge("end", 0.3)}>+</button>
        </div>
      </section>

      <section className="steps two">
        <button onClick={listen} disabled={status === "recording" || status === "comparing"} className={status === "listening" ? "active" : ""}>
          <b>1</b> {status === "listening" ? "■ Stop" : "▶ Listen"}
        </button>
        <button onClick={toggleRecord} disabled={status === "listening" || status === "comparing"} className={"rec " + (status === "recording" ? "active" : "")}>
          <b>2</b> {status === "recording" ? "■ Stop" : rec ? "● Re-record" : "● Record"}
        </button>
      </section>

      <section className={"panel" + (rec ? "" : " off")}>
        <h3><b>3</b> Compare</h3>
        <div className="seg">
          {[["both", "Оба подряд"], ["orig", "Оригинал"], ["mine", "Я"]].map(([m, label]) => {
            const on = status === "comparing" && cmode === m;
            return (
              <button
                key={m}
                className={"btn " + (m === "both" ? "main " : "") + (on ? "active" : "")}
                disabled={!rec || (busy && !on)}
                onClick={() => compare(m)}
              >
                {on ? "■ Стоп" : "▶ " + label}
              </button>
            );
          })}
        </div>
        {!rec && <p className="hint">Сначала запишите свой голос</p>}
      </section>

      <section className={"panel" + (rec ? "" : " off")}>
        <h3>Проверка произношения</h3>
        <button className="btn wide" disabled={!rec || busy || chk?.loading} onClick={check}>
          {chk?.loading ? "Слушаю…" : "Проверить через Whisper"}
        </button>
        {chk?.result && (
          <div className="verdict">
            <b>{chk.result.score}%</b>
            <span>Whisper услышал: «{chk.result.heard || "ничего"}»</span>
          </div>
        )}
        {chk?.error && <p className="err">{chk.error}</p>}
      </section>

      {error && <p className="err">{error}</p>}

      <nav className="pager">
        <button onClick={() => go(idx - 1)} disabled={idx === 0}>‹</button>
        <button onClick={() => go(idx + 1)} disabled={idx === sentences.length - 1}>›</button>
      </nav>

      <ol className="list" ref={listRef}>
        {sentences.map((s, i) => (
          <li key={s.id} className={(i === idx ? "on " : "") + (recs[s.id] ? "done" : "")} onClick={() => go(i)}>
            {s.text}
          </li>
        ))}
      </ol>

      <audio ref={audioRef} playsInline />
    </main>
  );
}
