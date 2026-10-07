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

const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

export default function App() {
  const [url, setUrl] = useState("");
  const [lang, setLang] = useState("en");
  const [data, setData] = useState(null);
  const [idx, setIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [playerErr, setPlayerErr] = useState("");
  const [status, setStatus] = useState("idle"); // idle | listening | recording | comparing
  const [recs, setRecs] = useState({}); // id -> { url, blob }
  const [checks, setChecks] = useState({}); // id -> { loading | result | error }

  const playerRef = useRef(null);
  const timerRef = useRef(null);
  const recorderRef = useRef(null);
  const audioRef = useRef(null);
  const listRef = useRef(null);

  const sentences = data?.sentences ?? [];
  const cur = sentences[idx];

  // ---------- загрузка субтитров ----------
  async function load(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const r = await fetch(`/api/transcript?url=${encodeURIComponent(url)}&lang=${lang}`);
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Ошибка");
      if (!j.sentences.length) throw new Error("Субтитры пустые");
      setPlayerErr("");
      setData(j);
      setIdx(0);
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

  useEffect(() => {
    listRef.current?.querySelector(".on")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [idx]);

  function stopAll() {
    clearInterval(timerRef.current);
    playerRef.current?.pauseVideo?.();
    if (audioRef.current) audioRef.current.pause();
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }

  // Играет оригинал [start, end]; возвращает промис, который резолвится в конце фрагмента
  function playOriginal(s) {
    return new Promise((resolve) => {
      const p = playerRef.current;
      if (!p?.seekTo) return resolve();
      clearInterval(timerRef.current);
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

  // ---------- 1. Listen ----------
  async function listen() {
    stopAll();
    setStatus("listening");
    await playOriginal(cur);
    setStatus("idle");
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
      a.onended = resolve;
      a.onerror = resolve;
      a.currentTime = 0;
      a.play().catch(resolve);
    });
  }

  async function compare(mode) {
    const rec = recs[cur.id];
    if (!rec) return;
    stopAll();
    setStatus("comparing");
    try {
      await unlockAudio(rec.url);
      if (mode === "orig") await playOriginal(cur);
      else if (mode === "mine") await playMine();
      else {
        await playOriginal(cur);
        await new Promise((r) => setTimeout(r, 250));
        await playMine();
      }
    } finally {
      setStatus("idle");
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
      const r = await fetch("/api/check", { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Ошибка проверки");
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
      </main>
    );
  }

  const rec = recs[cur.id];
  const chk = checks[cur.id];
  const busy = status !== "idle";

  return (
    <main className="train">
      <header>
        <button className="ghost" onClick={() => { stopAll(); setData(null); }}>Назад</button>
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

      <section className="steps">
        <button onClick={listen} disabled={status === "recording" || status === "comparing"} className={status === "listening" ? "active" : ""}>
          <b>1</b> Listen
        </button>
        <button onClick={toggleRecord} disabled={status === "listening" || status === "comparing"} className={"rec " + (status === "recording" ? "active" : "")}>
          <b>2</b> {status === "recording" ? "Stop" : rec ? "Re-record" : "Record"}
        </button>
        <button onClick={() => compare("both")} disabled={!rec || busy} className={status === "comparing" ? "active" : ""}>
          <b>3</b> Compare
        </button>
      </section>

      {rec && (
        <div className="ab">
          <button className="ghost" disabled={busy} onClick={() => compare("orig")}>Только оригинал</button>
          <button className="ghost" disabled={busy} onClick={() => compare("mine")}>Только я</button>
          <button className="ghost" disabled={busy || chk?.loading} onClick={check}>
            {chk?.loading ? "Слушаю…" : "Проверить произношение"}
          </button>
        </div>
      )}
      {chk?.result && (
        <div className="verdict">
          <b>{chk.result.score}%</b>
          <span>Whisper услышал: «{chk.result.heard || "ничего"}»</span>
        </div>
      )}
      {chk?.error && <p className="err">{chk.error}</p>}

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
