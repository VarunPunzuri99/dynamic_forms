import { useEffect, useState } from "react";
import FieldPreview, { ReviewFieldRow } from "./FieldPreview";
import { BusyOverlay, ButtonLabel, PageLoader, PanelLoader, StatusBanner } from "./LoadingUI";
import RulesEditor, { rulesAreValid } from "./RulesEditor";

function errorMessage(body, fallback) {
  return body?.errors?.[0]?.message || fallback;
}

function withInclude(questions) {
  return questions.map((question) => ({
    ...question,
    include:
      question.review_status === "approved"
        ? true
        : question.review_status === "removed"
          ? false
          : Boolean(question.suggested_include),
  }));
}

function selectionPayload(rows, confirmGenerated, rules) {
  return {
    confirm_generated_wording: confirmGenerated,
    questions: rows.map((row) => ({
      question_key: row.question_key,
      label: row.label,
      include: row.include,
    })),
    rules,
  };
}

export default function Compare({ formId }) {
  const [pack, setPack] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [rows, setRows] = useState([]);
  const [rules, setRules] = useState([]);
  const [savedEdition, setSavedEdition] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [loadingEdition, setLoadingEdition] = useState(false);
  const [confirmGenerated, setConfirmGenerated] = useState(false);
  const [selectedKey, setSelectedKey] = useState("");

  useEffect(() => {
    let cancelled = false;
    setPack(null);
    setSelectedId("");
    setRows([]);
    setRules([]);
    setSavedEdition(null);
    setMessage("");
    setError("");
    setLoadError("");
    fetch(`/api/forms/${formId}/editions`)
      .then((response) => response.json().then((body) => ({ ok: response.ok, body })))
      .then(({ ok, body }) => {
        if (cancelled) {
          return;
        }
        if (!ok) {
          throw new Error(errorMessage(body, "The editions could not be loaded"));
        }
        setPack(body);
        const choices = body.editions.filter((edition) => edition.id !== body.live?.id);
        if (choices.length === 1) {
          setSelectedId(String(choices[0].id));
        }
      })
      .catch((failure) => {
        if (!cancelled) {
          setLoadError(failure.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [formId]);

  useEffect(() => {
    if (!selectedId) {
      setRows([]);
      setRules([]);
      setSavedEdition(null);
      setLoadingEdition(false);
      return undefined;
    }
    let cancelled = false;
    setLoadingEdition(true);
    setError("");
    fetch(`/api/forms/${formId}/editions/${selectedId}`)
      .then((response) => response.json().then((body) => ({ ok: response.ok, body })))
      .then(({ ok, body }) => {
        if (cancelled) {
          return;
        }
        if (!ok) {
          throw new Error(errorMessage(body, "The saved questions could not be loaded"));
        }
        if (!body.questions.length) {
          setSavedEdition(null);
          setRows([]);
          setRules([]);
          return;
        }
        const nextRows = withInclude(body.questions);
        setSavedEdition(body.edition);
        setRows(nextRows);
        setRules(body.rules || []);
        setSelectedKey(nextRows[0]?.question_key || "");
        setConfirmGenerated(false);
      })
      .catch((failure) => {
        if (!cancelled) {
          setError(failure.message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingEdition(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [formId, selectedId]);

  function updateRow(questionKey, patch) {
    setRows((current) => current.map((row) => (row.question_key === questionKey ? { ...row, ...patch } : row)));
  }

  async function onCompare() {
    setBusyAction("compare");
    setError("");
    setMessage("");
    try {
      const response = await fetch(`/api/forms/${formId}/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edition_id: Number(selectedId) }),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(errorMessage(body, "The comparison could not run"));
      }
      const detail = await fetch(`/api/forms/${formId}/editions/${body.edition_id}`);
      const detailBody = await detail.json();
      if (!detail.ok) {
        throw new Error(errorMessage(detailBody, "The saved questions could not be loaded"));
      }
      const nextRows = withInclude(detailBody.questions);
      setSavedEdition(detailBody.edition);
      setRows(nextRows);
      setRules(detailBody.rules || []);
      setSelectedKey(nextRows[0]?.question_key || "");
      setConfirmGenerated(false);
      const carried = body.counts.carried_labels || 0;
      setMessage(
        `Saved ${body.question_count} questions on edition id ${body.edition_id} (${body.edition}). Added ${body.counts.added}, unchanged ${body.counts.unchanged}, removed ${body.counts.removed}. Used the live form’s wording on ${carried} matching fields. The live ${formId} form was left as it is.`
      );
      const refreshed = await fetch(`/api/forms/${formId}/editions`);
      if (refreshed.ok) {
        setPack(await refreshed.json());
      }
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusyAction("");
    }
  }

  if (loadError) {
    return (
      <main className="page">
        <h1>Compare {formId}</h1>
        <p className="error-banner">{loadError}</p>
      </main>
    );
  }

  if (!pack && !loadError) {
    return (
      <main className="page page-compare">
        <PageLoader
          title={`Loading ${formId} editions`}
          detail="Fetching live and draft editions for compare…"
        />
      </main>
    );
  }

  const choices = (pack?.editions || []).filter((edition) => edition.id !== pack?.live?.id);
  const busyLabel =
    busyAction === "compare"
      ? "Comparing PDFs and saving draft questions…"
      : busyAction === "save"
        ? "Saving checked questions and rules…"
        : busyAction === "publish"
          ? "Publishing this edition as live…"
          : "";
  const included = rows.filter((row) => row.include);
  const generatedIncluded = included.some((row) => row.change_type !== "reviewed");
  const rulesOk = rulesAreValid(rules, included);
  const selected = rows.find((row) => row.question_key === selectedKey) || null;
  const canSave =
    !busyAction &&
    savedEdition?.status === "draft" &&
    included.length > 0 &&
    !(generatedIncluded && !confirmGenerated) &&
    rulesOk;

  async function saveSelection() {
    setBusyAction("save");
    setError("");
    setMessage("");
    try {
      const response = await fetch(`/api/forms/${formId}/editions/${savedEdition.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selectionPayload(rows, confirmGenerated, rules)),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(errorMessage(body, "The selected questions could not be saved"));
      }
      setRows((current) =>
        current.map((row) => ({
          ...row,
          review_status: row.include ? "approved" : "removed",
        }))
      );
      setMessage(
        `Saved ${body.published_questions} checked questions and ${rules.length} rules on edition id ${savedEdition.id}. This edition is still a draft. Approve it when these sentences should replace the live form.`
      );
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusyAction("");
    }
  }

  async function publishSelection() {
    const publishingId = savedEdition?.id;
    setBusyAction("publish");
    setError("");
    setMessage("");
    try {
      const response = await fetch(`/api/forms/${formId}/editions/${publishingId}/publish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selectionPayload(rows, confirmGenerated, rules)),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(errorMessage(body, "The edition could not be approved"));
      }
      window.location.hash = `#form/${formId}`;
    } catch (failure) {
      setError(failure.message);
      try {
        const refreshed = await fetch(`/api/forms/${formId}/editions`);
        if (refreshed.ok) {
          const packBody = await refreshed.json();
          setPack(packBody);
          const current = packBody.editions?.find((edition) => edition.id === publishingId);
          if (current) {
            setSavedEdition((previous) =>
              previous
                ? { ...previous, status: current.status, is_live: current.is_live }
                : previous
            );
          }
        }
      } catch {
        // Keep the publish error; a failed refresh must not hide it.
      }
    } finally {
      setBusyAction("");
    }
  }

  return (
    <main className="page page-compare">
      <BusyOverlay active={Boolean(busyAction)} label={busyLabel || "Working…"} />
      <header className="page-head">
        <div className="page-head-copy">
          <p className="eyebrow">Reviewer</p>
          <h1>Compare {formId}</h1>
          <p className="lede">
            Live PDF stays current. Choose another edition to compare, save reviewed questions on that draft, then publish when it should become live.
          </p>
        </div>
        {pack?.live ? (
          <div className="stats">
            <div className="stat">
              <span>Live edition</span>
              <strong>
                {pack.live.edition} · id {pack.live.id}
              </strong>
            </div>
            <div className="stat">
              <span>Active questions</span>
              <strong>{pack.live.approved_count}</strong>
            </div>
          </div>
        ) : null}
      </header>
      {pack?.live ? null : <p className="note">This form has no live edition yet.</p>}
      {busyLabel ? <StatusBanner>{busyLabel}</StatusBanner> : null}
      <label className="field field-inline" htmlFor="edition-pdf">
        <span className="q-body">
          <span className="q-kind">Compare source</span>
          <span className="prompt">PDF to compare</span>
          <select
            id="edition-pdf"
            className="compare-select"
            value={selectedId}
            disabled={Boolean(busyAction)}
            onChange={(event) => setSelectedId(event.target.value)}
          >
            <option value="">Choose an edition PDF</option>
            {choices.map((edition) => (
              <option key={edition.id} value={edition.id}>
                {edition.edition} — {edition.storage_path} — edition id {edition.id} — {edition.status} —{" "}
                {edition.question_count} questions, {edition.approved_count} checked
              </option>
            ))}
          </select>
        </span>
      </label>
      {choices.length === 0 && pack ? (
        <p className="note">Add another edition row for {formId} before a comparison can run. Its storage path must be the new PDF.</p>
      ) : null}
      {loadingEdition ? (
        <PanelLoader title="Loading edition" detail="Fetching saved questions for the selected PDF…" />
      ) : null}
      {error ? <p className="error-banner">{error}</p> : null}
      {message ? <p className="note note-success">{message}</p> : null}
      <div className="actions">
        <button type="button" disabled={Boolean(busyAction) || loadingEdition || !selectedId || !pack?.live} onClick={onCompare}>
          <ButtonLabel busy={busyAction === "compare"} busyText="Comparing…">
            Compare and save questions
          </ButtonLabel>
        </button>
      </div>
      {savedEdition ? (
        <>
          <h2>
            Questions on edition id {savedEdition.id} ({savedEdition.edition})
          </h2>
          <p className="note">
            Status {savedEdition.status}. PDF {savedEdition.storage_path}. Compared with {savedEdition.compared_to_path}. Click a row to highlight that box on the PDF. Unchanged fields reuse the live form’s wording when the PDF field name matches. {included.length} of {rows.length} are checked.
          </p>
          <div className="review-workspace">
            <div className="review-table">
              {rows.map((row) => (
                <ReviewFieldRow
                  key={row.question_key}
                  row={row}
                  selected={row.question_key === selectedKey}
                  includeLabel={`Show ${row.question_key} on the form`}
                  onSelect={(item) => setSelectedKey(item.question_key)}
                  onIncludeChange={(questionKey, include) => updateRow(questionKey, { include })}
                  onLabelChange={(questionKey, label) => updateRow(questionKey, { label })}
                />
              ))}
            </div>
            <FieldPreview formId={formId} editionId={savedEdition.id} row={selected} />
          </div>
          <RulesEditor
            questions={included}
            rules={rules}
            onChange={setRules}
            disabled={savedEdition.status !== "draft" || Boolean(busyAction)}
          />
          {generatedIncluded ? (
            <label className="note confirm-wording">
              <input
                type="checkbox"
                checked={confirmGenerated}
                onChange={(event) => setConfirmGenerated(event.target.checked)}
              />{" "}
              I checked the selected wording. It was read from the PDF or reused from the live form.
            </label>
          ) : null}
          {!rulesOk ? <p className="error-banner">Fix or remove rules that point at unchecked questions.</p> : null}
          <div className="actions">
            <button type="button" className="secondary" disabled={!canSave} onClick={saveSelection}>
              <ButtonLabel busy={busyAction === "save"} busyText="Saving…">
                {`Save ${included.length} checked questions`}
              </ButtonLabel>
            </button>
            <button type="button" disabled={!canSave} onClick={publishSelection}>
              <ButtonLabel busy={busyAction === "publish"} busyText="Publishing…">
                Approve and make this edition live
              </ButtonLabel>
            </button>
          </div>
        </>
      ) : null}
    </main>
  );
}
