import { useState } from "react";

const fmtT = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
const n0 = (v) => (v == null ? "—" : Math.round(v));

const STATUS_LABEL = {
  good: "произнесено хорошо",
  fair: "можно лучше",
  poor: "слабое произношение",
  mispronunciation: "ошибка произношения",
  omission: "слово пропущено",
  unknown: "нет оценки для этого слова",
};

// Предложение с разметкой ошибок Vocametrix. Цвет дублируется подчёркиванием/зачёркиванием.
export function AnnotatedSentence({ result }) {
  const [pick, setPick] = useState(null);
  const ins = {};
  result.insertions.forEach((x) => (ins[x.afterWord] = [...(ins[x.afterWord] || []), x.word]));
  const w = result.words[pick];
  return (
    <>
      <p className="sentence vsent">
        {(ins[-1] || []).map((x, k) => <span key={"i-1" + k} className="vw-ins" title="Лишнее слово">+{x} </span>)}
        {result.words.map((t, i) => {
          const after = ins[i] || [];
          const clickable = t.status !== "skip" && t.status !== "unknown";
          return (
            <span key={i}>
              <span
                className={`vw vw-${t.status}${pick === i ? " picked" : ""}`}
                title={STATUS_LABEL[t.status] || ""}
                role={clickable ? "button" : undefined}
                tabIndex={clickable ? 0 : undefined}
                onClick={() => clickable && setPick(pick === i ? null : i)}
                onKeyDown={(e) => clickable && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setPick(pick === i ? null : i))}
              >
                {t.t}
              </span>{" "}
              {after.map((x, k) => <span key={k} className="vw-ins" title="Лишнее слово, которого нет в тексте">+{x} </span>)}
            </span>
          );
        })}
      </p>
      <div className="legend">
        <span className="vw vw-mispronunciation">ошибка</span>
        <span className="vw vw-poor">слабо</span>
        <span className="vw vw-fair">средне</span>
        <span className="vw vw-omission">пропущено</span>
        <span className="vw-ins">+лишнее</span>
      </div>
      {w && (
        <div className="wdetail">
          <b>{w.t.replace(/^[^\w']+|[^\w']+$/g, "")}</b>{" "}
          <span>{STATUS_LABEL[w.status]}{w.score != null && w.status !== "omission" ? ` · ${n0(w.score)}/100` : ""}</span>
          {w.phonemes?.length > 0 && (
            <div className="phones">
              {w.phonemes.map((p, k) => (
                <span key={k} className={"ph " + (p.s == null ? "" : p.s >= 80 ? "good" : p.s >= 60 ? "fair" : "poor")}>
                  {p.p}<small>{n0(p.s)}</small>
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}

function Tile({ label, value, hint }) {
  return (
    <div className="tile" title={hint}>
      <b>{n0(value)}</b>
      <span>{label}</span>
    </div>
  );
}

function Bar({ label, value }) {
  return (
    <div className="bar">
      <span>{label}</span>
      <div className="track"><div style={{ width: `${value ?? 0}%` }} /></div>
      <b>{n0(value)}</b>
    </div>
  );
}

function ErrorBox({ error, onRetry, busy }) {
  const wait = error.retryAfter ? ` Примерно через ${error.retryAfter} с.` : "";
  return (
    <div className="vmerr">
      <p className="err">{error.message}{error.code === "rate_limited" ? wait : ""}</p>
      {error.retryable && (
        <button className="btn" disabled={busy} onClick={onRetry}>Повторить</button>
      )}
    </div>
  );
}

function PronunciationBlock({ st, onRun, busy }) {
  if (!st) return null;
  if (st.loading) return <p className="hint">Оцениваю произношение…</p>;
  if (st.error) return <><h4>Произношение</h4><ErrorBox error={st.error} onRetry={onRun} busy={busy} /></>;
  const r = st.result;
  const s = r.scores;
  return (
    <div className="vmblock">
      <h4>Произношение <small>Pronunciation Assessment</small></h4>
      <div className="tiles">
        <Tile label="Accuracy" value={s.accuracy} hint="Точность произнесения звуков" />
        <Tile label="Fluency" value={s.fluency} hint="Плавность и паузы" />
        <Tile label="Prosody" value={s.prosody} hint="Ударение, интонация, ритм по оценке Azure" />
        <Tile label="Complete" value={s.completeness} hint="Доля слов текста, которые были произнесены" />
      </div>
      <p className="hint">
        Общий балл Vocametrix (PronScore): <b>{n0(s.pron)}</b> — взвешенная сумма четырёх оценок выше, считается сервисом.
      </p>

      {r.omitted.length > 0 && (
        <p className="note">
          Пропущено слов: <b>{r.omitted.length}</b> ({r.omitted.join(", ")}). Пропуски снижают Completeness и общий балл, даже если
          остальные слова звучат хорошо.
          {r.spokenWordsAccuracy != null && <> Средняя оценка произнесённых слов: <b>{r.spokenWordsAccuracy}</b> (расчёт сайта по оценкам слов).</>}
          {" "}Если вы ничего не пропускали, возможно, границы фрагмента неточны (особенно в автосубтитрах): подправьте их и повторите анализ.
        </p>
      )}
      {r.mispronounced.length > 0 && <p className="note">Ошибки произношения: <b>{r.mispronounced.join(", ")}</b></p>}
      {r.insertions.length > 0 && <p className="note">Лишние слова в записи: <b>{r.insertions.map((x) => x.word).join(", ")}</b></p>}
      {r.unmatchedWords > 0 && <p className="note">Для слов без оценки ({r.unmatchedWords}) разметки нет — их не удалось сопоставить с ответом сервиса.</p>}
      {!r.hasPhonemes && <p className="hint">Оценки отдельных звуков доступны только для en-US.</p>}
      {r.hasPhonemes && <p className="hint">Нажмите на слово в тексте, чтобы увидеть оценки звуков.</p>}
      <p className="hint">Оценки автоматические и приблизительные: на них влияют шум, микрофон и акцент. Это ориентир, а не окончательный вердикт.</p>
    </div>
  );
}

function ProsodyBlock({ st, onRun, busy }) {
  if (!st) return null;
  if (st.loading) return <p className="hint">Сравниваю интонацию с оригиналом…</p>;
  if (st.error) return <><h4>Сходство с оригиналом</h4><ErrorBox error={st.error} onRetry={onRun} busy={busy} /></>;
  const r = st.result;
  const f = r.fragment;
  const d = r.details;
  const durGap = f.originalSeconds > 0 && Math.abs(f.recordingSeconds - f.originalSeconds) / f.originalSeconds > 0.4;
  return (
    <div className="vmblock">
      <h4>Сходство с оригиналом <small>Prosody Similarity</small></h4>
      <Bar label="Интонация" value={r.similarity.pitch} />
      <Bar label="Ритм" value={r.similarity.rhythm} />
      <Bar label="Интенсивность" value={r.similarity.intensity} />
      <Bar label="Темп" value={r.similarity.speechRate} />
      <p className="hint">
        Фрагмент оригинала {fmtT(f.start)}–{fmtT(f.end)} ({f.originalSeconds} с), ваша запись {f.recordingSeconds} с.
        {d.modelRate != null && d.userRate != null && <> Темп речи: оригинал {d.modelRate}, вы {d.userRate}.</>}
      </p>
      {durGap && (
        <p className="note">
          Длительность записи заметно отличается от оригинала. Запись может содержать тишину в начале или в конце, а границы фрагмента —
          быть неточными: от этого страдают ритм и темп.
        </p>
      )}
      <p className="hint">
        Это сходство интонации, ритма, громкости и темпа с оригиналом, а <b>не правильность произношения</b>. На него влияют высота голоса,
        шум, музыка в оригинале и границы фрагмента. Высокое сходство не гарантирует чистого произношения, низкое не значит ошибку.
      </p>
      {(r.vocametrixOverall != null || r.needsWork) && (
        <details className="more">
          <summary>Подробности от Vocametrix</summary>
          {r.vocametrixOverall != null && <p>Композитный балл просодии: {n0(r.vocametrixOverall)}{r.level ? ` (${r.level})` : ""}</p>}
          {r.needsWork && <p>Needs work: {r.needsWork}</p>}
          {r.bestMatch && <p>Best match: {r.bestMatch}</p>}
        </details>
      )}
    </div>
  );
}

// Результаты по одному фрагменту: два независимых блока, баллы не объединяются.
export default function VocametrixPanel({ status, state, hasRecording, busy, onRun }) {
  const loading = !!(state?.pron?.loading || state?.pros?.loading);
  const ran = !!(state?.pron || state?.pros);
  return (
    <section className={"panel" + (hasRecording ? "" : " off")}>
      <h3>Анализ Vocametrix</h3>
      {status && !status.enabled ? (
        <p className="hint">Анализ не настроен на сервере: не задан VOCAMETRIX_API_KEY.</p>
      ) : (
        <>
          <button className="btn wide" disabled={!hasRecording || busy || loading} onClick={() => onRun("both")}>
            {loading ? "Анализирую…" : ran ? "Повторить анализ" : "Проанализировать запись"}
          </button>
          {!ran && (
            <p className="hint">
              Запись до {status?.maxSeconds ?? 30} с, до {status?.maxUploadMB ?? 10} МБ. Анализ расходует запросы к API
              {status ? ` (осталось ≈${status.requestsLeft}, один анализ ≈ ${status.requestsPerAnalysis.pronunciation + status.requestsPerAnalysis.prosody})` : ""}.
            </p>
          )}
          <PronunciationBlock st={state?.pron} onRun={() => onRun("pron")} busy={busy || loading} />
          <ProsodyBlock st={state?.pros} onRun={() => onRun("pros")} busy={busy || loading} />
        </>
      )}
    </section>
  );
}
