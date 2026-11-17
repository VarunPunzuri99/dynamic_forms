import { useEffect, useMemo, useRef, useState } from "react";
import { emptyAnswer, evaluateEffects } from "./formRules";
import { BusyOverlay, ButtonLabel, PageLoader, StatusBanner } from "./LoadingUI";

function errorsById(errors) {
  return Object.fromEntries((errors || []).map((error) => [error.id, error.message]));
}

export default function EmployeeForm({ formId }) {
  const [edition, setEdition] = useState("");
  const [editionId, setEditionId] = useState(null);
  const [storagePath, setStoragePath] = useState("");
  const [unreviewedCount, setUnreviewedCount] = useState(0);
  const [ready, setReady] = useState(false);
  const [pendingDraft, setPendingDraft] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [rules, setRules] = useState([]);
  const [answers, setAnswers] = useState({});
  const [loadError, setLoadError] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [formError, setFormError] = useState("");
  const [notices, setNotices] = useState([]);
  const [pdfUrl, setPdfUrl] = useState("");
  const [blobPath, setBlobPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyLabel, setBusyLabel] = useState("");
  const [fillTab, setFillTab] = useState("answers");
  const pdfUrlRef = useRef("");

  function replacePdf(nextUrl) {
    if (pdfUrlRef.current) {
      URL.revokeObjectURL(pdfUrlRef.current);
    }
    pdfUrlRef.current = nextUrl;
    setPdfUrl(nextUrl);
  }

  useEffect(() => {
    let cancelled = false;
    setReady(false);
    fetch(`/api/forms/${formId}/live`)
      .then((response) => response.json().then((body) => ({ ok: response.ok, body })))
      .then(({ ok, body }) => {
        if (cancelled) {
          return;
        }
        if (!ok) {
          throw new Error(body.errors?.[0]?.message || "The live form could not be loaded");
        }
        const initial = {};
        for (const question of body.questions) {
          initial[question.id] = emptyAnswer(question);
        }
        setEdition(body.edition);
        setEditionId(body.edition_id);
        setStoragePath(body.storage_path || "");
        setUnreviewedCount(body.unreviewed_count || 0);
        setPendingDraft(body.pending_draft);
        setQuestions(body.questions);
        setRules(body.rules || []);
        setAnswers(initial);
        setNotices([]);
        setBlobPath("");
        replacePdf("");
        setFillTab("answers");
        setReady(true);
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadError(error.message || "The preview API is not running.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [formId]);

  useEffect(() => {
    return () => {
      if (pdfUrlRef.current) {
        URL.revokeObjectURL(pdfUrlRef.current);
      }
    };
  }, []);

  const effects = useMemo(() => evaluateEffects(questions, rules, answers), [questions, rules, answers]);

  useEffect(() => {
    setAnswers((current) => {
      let changed = false;
      const next = { ...current };
      for (const question of questions) {
        if (effects[question.id]?.disabled) {
          const cleared = emptyAnswer(question);
          if (next[question.id] !== cleared) {
            next[question.id] = cleared;
            changed = true;
          }
        }
      }
      return changed ? next : current;
    });
  }, [effects, questions]);

  function updateAnswer(id, value) {
    setAnswers((current) => ({ ...current, [id]: value }));
    replacePdf("");
    setNotices([]);
    setBlobPath("");
    setFieldErrors((current) => {
      if (!current[id]) {
        return current;
      }
      const next = { ...current };
      delete next[id];
      return next;
    });
  }

  async function readFields() {
    setBusy(true);
    setBusyLabel("Reading fields from the blank PDF…");
    setFormError("");
    try {
      const response = await fetch(`/api/forms/${formId}/live/fields`, { method: "POST" });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.errors?.[0]?.message || "The PDF fields could not be read");
      }
      window.location.hash = `#fields/${formId}`;
    } catch (failure) {
      setFormError(failure.message);
    } finally {
      setBusy(false);
      setBusyLabel("");
    }
  }

  async function fillSample() {
    setFormError("");
    setBusy(true);
    setBusyLabel("Loading sample answers…");
    try {
      const response = await fetch(`/api/forms/${formId}/live/sample-answers`);
      if (!response.ok) {
        setFormError("Sample answers could not be loaded");
        return;
      }
      const sample = await response.json();
      setAnswers((current) => ({ ...current, ...sample }));
      setFieldErrors({});
      setNotices([]);
      setBlobPath("");
      replacePdf("");
    } finally {
      setBusy(false);
      setBusyLabel("");
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setBusyLabel("Binding answers and creating PDF…");
    setFormError("");
    setFieldErrors({});
    setBlobPath("");
    try {
      const response = await fetch(`/api/forms/${formId}/live/answers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers }),
      });
      const body = await response.json();
      if (!response.ok) {
        setFieldErrors(errorsById(body.errors));
        setFormError(errorsById(body.errors)[""] || "Fix the highlighted answers and try again.");
        return;
      }
      setBusyLabel("Opening PDF preview…");
      const bytes = Uint8Array.from(atob(body.pdf_base64), (char) => char.charCodeAt(0));
      replacePdf(URL.createObjectURL(new Blob([bytes], { type: "application/pdf" })));
      setNotices(body.notices || []);
      setBlobPath(body.blob_path || "");
      setFillTab("preview");
    } catch {
      setFormError("The preview could not be created. Check that the API is still running.");
    } finally {
      setBusy(false);
      setBusyLabel("");
    }
  }

  if (loadError) {
    return (
      <main className="page">
        <h1>Employee form</h1>
        <p className="error-banner">{loadError}</p>
      </main>
    );
  }

  const formTitle = formId === "fw4" ? "Federal Form W-4" : String(formId).toUpperCase();

  if (!ready && !loadError) {
    return (
      <main className="page page-fill">
        <PageLoader
          title={`Loading ${formTitle}`}
          detail="Fetching live questions and rules from FormsEngine…"
        />
      </main>
    );
  }

  return (
    <main className="page page-fill">
      <BusyOverlay active={busy} label={busyLabel || "Working…"} />
      <header className="page-head page-head-compact">
        <div className="page-head-copy">
          <p className="eyebrow">Employee fill</p>
          <h1>
            {formTitle}
            {edition ? (
              <span className="year-pill">
                {edition} live
                {ready ? ` (${questions.length} questions)` : ""}
              </span>
            ) : null}
          </h1>
          <p className="lede">
            Fill approved questions, then create a PDF. Preview opens in the next tab.{" "}
            <a href={`#compare/${formId}`}>Compare years</a>
            {storagePath ? (
              <>
                {" · "}
                <a href={storagePath} target="_blank" rel="noreferrer" title={storagePath}>
                  Blank PDF
                </a>
              </>
            ) : null}
          </p>
        </div>
      </header>

      {(pendingDraft || rules.length > 0) && (
        <div className="callouts callouts-compact">
          {pendingDraft ? (
            <p className="note">
              A {pendingDraft.edition} draft with {pendingDraft.question_count} questions is waiting for review. Employees still see this {edition} form.
            </p>
          ) : null}
          {rules.length ? (
            <p className="note">
              {rules.length} answer {rules.length === 1 ? "rule" : "rules"} active — some questions turn off when earlier answers make them unnecessary.
            </p>
          ) : null}
        </div>
      )}

      {ready && questions.length === 0 && unreviewedCount === 0 ? (
        <div className="note">
          <p>
            Edition id {editionId} has no fields yet. Read them from the blank PDF before this form can be filled.
          </p>
          <button type="button" onClick={readFields} disabled={busy}>
            <ButtonLabel busy={busy && busyLabel.includes("Reading")} busyText="Reading fields…">
              Read fields from this PDF
            </ButtonLabel>
          </button>
        </div>
      ) : null}
      {ready && questions.length === 0 && unreviewedCount > 0 ? (
        <p className="note">
          {unreviewedCount} fields were imported and are waiting for review.{" "}
          <a href={`#fields/${formId}`}>Open Review fields</a>
        </p>
      ) : null}
      {busy && busyLabel ? <StatusBanner>{busyLabel}</StatusBanner> : null}

      <div className={`fill-tabs panel-surface${busy ? " is-busy" : ""}`}>
        <div className="fill-tablist" role="tablist" aria-label="Fill workflow">
          <button
            type="button"
            role="tab"
            className={fillTab === "answers" ? "fill-tab active" : "fill-tab"}
            aria-selected={fillTab === "answers"}
            onClick={() => setFillTab("answers")}
          >
            <span className="fill-tab-step">1</span>
            Fill form
          </button>
          <button
            type="button"
            role="tab"
            className={fillTab === "preview" ? "fill-tab active" : "fill-tab"}
            aria-selected={fillTab === "preview"}
            onClick={() => setFillTab("preview")}
          >
            <span className="fill-tab-step">2</span>
            PDF preview
            {pdfUrl ? <span className="fill-tab-dot" aria-hidden="true" /> : null}
          </button>
        </div>

        {fillTab === "answers" ? (
          <section className="fill-tabpanel" role="tabpanel">
            <form className="fill-form" onSubmit={onSubmit}>
              <div className="form-toolbar">
                <p className="form-toolbar-note">
                  {questions.length} fields · rules apply as you type · fake answers only
                </p>
                <div className="actions">
                  <button type="button" className="secondary" onClick={fillSample} disabled={busy || questions.length === 0}>
                    <ButtonLabel busy={busy && busyLabel.includes("sample")} busyText="Loading…">
                      Sample
                    </ButtonLabel>
                  </button>
                  <button type="submit" disabled={busy || questions.length === 0}>
                    <ButtonLabel busy={busy && busyLabel.includes("Binding")} busyText="Creating…">
                      Create PDF
                    </ButtonLabel>
                  </button>
                </div>
              </div>
              <div className="question-grid">
                {questions.map((question, index) => (
                  <Question
                    key={question.id}
                    index={index + 1}
                    question={question}
                    value={answers[question.id]}
                    error={fieldErrors[question.id]}
                    disabled={busy || Boolean(effects[question.id]?.disabled)}
                    required={Boolean(effects[question.id]?.required)}
                    onChange={updateAnswer}
                  />
                ))}
              </div>
              {formError ? <p className="error-banner">{formError}</p> : null}
              <div className="actions actions-bar">
                <button type="button" className="secondary" onClick={fillSample} disabled={busy || questions.length === 0}>
                  <ButtonLabel busy={busy && busyLabel.includes("sample")} busyText="Loading sample…">
                    Use sample answers
                  </ButtonLabel>
                </button>
                <button type="submit" disabled={busy || questions.length === 0}>
                  <ButtonLabel busy={busy && busyLabel.includes("Binding")} busyText="Creating PDF…">
                    Create PDF preview
                  </ButtonLabel>
                </button>
              </div>
            </form>
          </section>
        ) : (
          <section className="fill-tabpanel fill-tabpanel-preview" role="tabpanel">
            <div className="panel-head panel-head-row">
              <div>
                <h2>PDF preview</h2>
                <p className="panel-sub">
                  {pdfUrl ? `Filled ${edition} output` : "Create a preview from the Fill form tab first."}
                </p>
              </div>
              <div className="actions">
                <button type="button" className="secondary" onClick={() => setFillTab("answers")}>
                  Back to form
                </button>
                {blobPath ? (
                  <a className="button-link" href={blobPath} target="_blank" rel="noreferrer">
                    Open in Blob
                  </a>
                ) : null}
              </div>
            </div>
            {pdfUrl ? (
              <>
                {notices.map((notice) => (
                  <p className="notice" key={notice}>
                    {notice}
                  </p>
                ))}
                {blobPath ? (
                  <p className="note note-success">
                    Saved to Blob Storage.{" "}
                    <a href={blobPath} target="_blank" rel="noreferrer">
                      Open completed PDF
                    </a>
                  </p>
                ) : null}
                <iframe title={`Filled ${edition} form`} src={pdfUrl} />
              </>
            ) : (
              <div className="preview-empty">
                <p className="preview-empty-title">No preview yet</p>
                <p>Complete the form, then click Create PDF. This tab opens automatically with the bound file.</p>
                <button type="button" onClick={() => setFillTab("answers")}>
                  Go to Fill form
                </button>
              </div>
            )}
          </section>
        )}
      </div>
    </main>
  );
}

const KIND = {
  text: "Text",
  number: "Amount",
  checkbox: "Yes or no",
  choice: "Choose one",
};

function choiceOptions(question) {
  return Array.isArray(question.options) ? question.options : null;
}

export function Question({ question, value, error, onChange, index, disabled = false, required }) {
  const fieldId = `q-${question.id}`;
  const isRequired = required == null ? Boolean(question.required) : Boolean(required);
  const options = question.type === "choice" ? choiceOptions(question) : null;
  const choiceUnavailable = question.type === "choice" && (!options || options.length === 0);
  const wide = question.type === "checkbox" || question.type === "choice";
  return (
    <div
      className={[
        "field",
        wide ? "field-span-2" : "",
        error || choiceUnavailable ? "has-error" : "",
        disabled ? "is-disabled" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="q-index" aria-hidden="true">
        {index}
      </div>
      <div className="q-body">
        <div className="q-meta">
          <span className="q-kind">{KIND[question.type] || "Answer"}</span>
          {isRequired ? <span className="q-flag">Required</span> : null}
          {disabled ? <span className="q-flag">Off by rule</span> : null}
          {choiceUnavailable ? <span className="q-flag">Unavailable</span> : null}
        </div>
        {question.type === "choice" ? (
          choiceUnavailable ? (
            <div className="choice-unavailable">
              <p className="prompt">{question.label}</p>
              <p className="field-error">
                This question has no choices configured and cannot be answered until the reviewer adds them.
              </p>
            </div>
          ) : (
            <fieldset className="choices" disabled={disabled}>
              <legend>
                {question.label}
                {isRequired ? " *" : ""}
              </legend>
              <div className="choice-options">
                {options.map((option) => (
                  <label key={option.value}>
                    <input
                      type="radio"
                      name={question.id}
                      value={option.value}
                      checked={value === option.value}
                      disabled={disabled}
                      onChange={() => onChange(question.id, option.value)}
                    />{" "}
                    {option.label}
                  </label>
                ))}
              </div>
            </fieldset>
          )
        ) : question.type === "checkbox" ? (
          <label className="prompt-row" htmlFor={fieldId}>
            <span className="prompt">
              {question.label}
              {question.status === "needs_review" ? <span className="badge">Needs review</span> : null}
            </span>
            <input
              id={fieldId}
              type="checkbox"
              checked={Boolean(value)}
              disabled={disabled}
              onChange={(event) => onChange(question.id, event.target.checked)}
            />
          </label>
        ) : (
          <>
            <label className="field-label" htmlFor={fieldId}>
              {question.label}
              {isRequired ? " *" : ""}
            </label>
            <input
              id={fieldId}
              type="text"
              inputMode={question.type === "number" ? "decimal" : "text"}
              maxLength={question.max_length}
              value={value ?? ""}
              disabled={disabled}
              onChange={(event) => onChange(question.id, event.target.value)}
              aria-invalid={Boolean(error)}
            />
          </>
        )}
        {error ? <div className="field-error">{error}</div> : null}
      </div>
    </div>
  );
}
