import { useEffect, useState } from "react";
import FieldPreview, { ReviewFieldRow } from "./FieldPreview";
import { BusyOverlay, ButtonLabel, PageLoader, StatusBanner } from "./LoadingUI";
import RulesEditor, { rulesAreValid } from "./RulesEditor";

function errorMessage(body, fallback) {
  return body?.errors?.[0]?.message || fallback;
}

function mapQuestionRows(questions) {
  return (questions || []).map((question) => ({
    ...question,
    include:
      question.review_status === "approved"
        ? true
        : question.review_status === "removed"
          ? false
          : Boolean(question.suggested_include),
  }));
}

/** Load live if present; else try drafts until one has imported questions. */
async function loadEditionForReview(formId, pack) {
  if (pack?.live?.edition_id != null) {
    const detail = await fetch(`/api/forms/${formId}/editions/${pack.live.edition_id}`);
    const detailBody = await detail.json();
    if (!detail.ok) {
      throw new Error(errorMessage(detailBody, "The fields could not be loaded"));
    }
    return { source: "live", detailBody, rows: mapQuestionRows(detailBody.questions) };
  }

  const drafts = [...(pack?.editions || [])]
    .filter((row) => String(row.status || "").toLowerCase() === "draft")
    .sort((a, b) => {
      const aq = Number(a.question_count || a.approved_count || 0);
      const bq = Number(b.question_count || b.approved_count || 0);
      if (bq !== aq) return bq - aq;
      return String(b.edition || "").localeCompare(String(a.edition || ""));
    });

  if (drafts.length === 0) {
    throw new Error(
      `No edition to review for ${formId}. Import fields from the PDF on Overview first.`
    );
  }

  let lastEmpty = null;
  for (const draft of drafts) {
    const detail = await fetch(`/api/forms/${formId}/editions/${draft.id}`);
    const detailBody = await detail.json();
    if (!detail.ok) {
      throw new Error(errorMessage(detailBody, "The fields could not be loaded"));
    }
    const rows = mapQuestionRows(detailBody.questions);
    if (rows.length > 0) {
      return { source: "draft", detailBody, rows };
    }
    lastEmpty = draft;
  }

  throw new Error(
    `Edition ${lastEmpty?.edition || "?"} (id ${lastEmpty?.id}) has no questions yet. Go to Overview and click Import fields from PDF.`
  );
}

export default function Fields({ formId }) {
  const [edition, setEdition] = useState(null);
  const [rows, setRows] = useState([]);
  const [rules, setRules] = useState([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmGenerated, setConfirmGenerated] = useState(false);
  const [reviewSource, setReviewSource] = useState("live");

  useEffect(() => {
    let cancelled = false;
    setLoadError("");
    setEdition(null);
    setRows([]);
    setRules([]);
    setSelectedKey("");
    setReviewSource("live");

    (async () => {
      const packRes = await fetch(`/api/forms/${formId}/editions`);
      const packBody = await packRes.json();
      if (!packRes.ok) {
        throw new Error(errorMessage(packBody, "Editions could not be loaded"));
      }
      if (cancelled) return;

      const loaded = await loadEditionForReview(formId, packBody);
      if (cancelled) return;

      setReviewSource(loaded.source);
      setEdition(loaded.detailBody.edition);
      setRows(loaded.rows);
      setRules(loaded.detailBody.rules || []);
      setSelectedKey(loaded.rows[0]?.question_key || "");
    })().catch((failure) => {
      if (!cancelled) {
        setLoadError(failure.message);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [formId]);

  function updateRow(questionKey, patch) {
    setRows((current) => current.map((row) => (row.question_key === questionKey ? { ...row, ...patch } : row)));
  }

  const included = rows.filter((row) => row.include);
  const generatedIncluded = included.some((row) => row.change_type !== "reviewed");
  const rulesOk = rulesAreValid(rules, included);
  const selected = rows.find((row) => row.question_key === selectedKey) || null;

  async function approve() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`/api/forms/${formId}/editions/${edition.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirm_generated_wording: confirmGenerated,
          questions: rows.map((row) => ({
            question_key: row.question_key,
            label: row.label,
            include: row.include,
          })),
          rules,
        }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(errorMessage(body, "The fields could not be approved"));
      }
      window.location.hash = `#form/${formId}`;
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <main className="page">
        <h1>Review {formId} fields</h1>
        <p className="error-banner">{loadError}</p>
        <p className="lede">
          <a href={`#workspace/${formId}`}>← Back to {formId} overview</a>
        </p>
      </main>
    );
  }

  if (!edition && !loadError) {
    return (
      <main className="page page-review">
        <PageLoader
          title={`Loading ${formId} fields`}
          detail="Fetching edition questions for review…"
        />
      </main>
    );
  }

  return (
    <main className="page page-review">
      <BusyOverlay active={busy} label="Saving approved fields and rules…" />
      <header className="page-head">
        <div className="page-head-copy">
          <a className="back-link" href={`#workspace/${formId}`}>
            ← {formId} overview
          </a>
          <p className="eyebrow">Reviewer</p>
          <h1>Review {formId} fields</h1>
          <p className="lede">
            {reviewSource === "draft"
              ? "Fields imported on the draft edition (not live yet). Check wording, then Approve — that publishes this edition as live for RN."
              : "Fields from the live blank PDF. Select a row to confirm the highlighted box. Checked rows become employee questions; rules can hide later questions from earlier answers."}
          </p>
        </div>
        {edition ? (
          <div className="stats">
            <div className="stat">
              <span>Edition</span>
              <strong>
                {edition.edition} · id {edition.id}
                {reviewSource === "draft" ? " · draft" : " · live"}
              </strong>
            </div>
            <div className="stat">
              <span>Included</span>
              <strong>
                {included.length} / {rows.length}
              </strong>
            </div>
          </div>
        ) : null}
      </header>
      {edition ? (
        <p className="note note-muted" title={edition.storage_path || ""}>
          Blank PDF linked for edition {edition.edition}.
        </p>
      ) : null}
      {busy ? <StatusBanner>Saving approved fields and rules to FormsEngine…</StatusBanner> : null}
      <div className={`review-workspace${busy ? " is-busy" : ""}`}>
        <div className="review-table">
          {rows.map((row) => (
            <ReviewFieldRow
              key={row.question_key}
              row={row}
              selected={row.question_key === selectedKey}
              onSelect={(item) => setSelectedKey(item.question_key)}
              onIncludeChange={(questionKey, include) => updateRow(questionKey, { include })}
              onLabelChange={(questionKey, label) => updateRow(questionKey, { label })}
            />
          ))}
        </div>
        {edition ? <FieldPreview formId={formId} editionId={edition.id} row={selected} /> : null}
      </div>
      <RulesEditor questions={included} rules={rules} onChange={setRules} disabled={busy || !edition} />
      {generatedIncluded ? (
        <label className="note confirm-wording">
          <input
            type="checkbox"
            checked={confirmGenerated}
            onChange={(event) => setConfirmGenerated(event.target.checked)}
          />{" "}
          I checked the selected wording. It was read from the PDF.
        </label>
      ) : null}
      {!rulesOk ? <p className="error-banner">Fix or remove rules that point at unchecked questions.</p> : null}
      {error ? <p className="error-banner">{error}</p> : null}
      {message ? <p className="note note-success">{message}</p> : null}
      <div className="actions">
        <button
          type="button"
          disabled={busy || !edition || included.length === 0 || (generatedIncluded && !confirmGenerated) || !rulesOk}
          onClick={approve}
        >
          <ButtonLabel busy={busy} busyText="Saving…">
            {reviewSource === "draft" ? "Approve & publish" : "Approve selected fields"}
          </ButtonLabel>
        </button>
      </div>
    </main>
  );
}
