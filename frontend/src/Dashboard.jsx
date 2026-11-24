import { useEffect, useMemo, useState } from "react";
import { BusyOverlay, PageLoader, StatusBanner } from "./LoadingUI";

function titleCaseForm(formId, displayName) {
  if (displayName && displayName !== formId) return displayName;
  if (formId === "fw4") return "Federal Form W-4";
  if (!formId) return "Form";
  return formId
    .split(/[-_]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function statusTone(edition) {
  if (edition?.is_live) return "live";
  const status = String(edition?.status || "").toLowerCase();
  if (status === "approved") return "approved";
  if (status === "draft") return "draft";
  return "muted";
}

function statusLabel(edition) {
  if (edition?.is_live) return "Live";
  const status = String(edition?.status || "unknown");
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export default function Dashboard() {
  const [forms, setForms] = useState([]);
  const [packs, setPacks] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyLabel, setBusyLabel] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    fetch("/api/forms")
      .then((response) => response.json().then((body) => ({ ok: response.ok, body })))
      .then(async ({ ok, body }) => {
        if (cancelled) return;
        if (!ok) {
          throw new Error(body?.errors?.[0]?.message || "Forms could not be loaded");
        }
        const list = Array.isArray(body) ? body : [];
        setForms(list);
        setBusyLabel("Loading editions…");
        const entries = await Promise.all(
          list.map(async (form) => {
            try {
              const response = await fetch(`/api/forms/${form.form_id}/editions`);
              const pack = await response.json();
              if (!response.ok) {
                return [
                  form.form_id,
                  { editions: [], live: form.live || null, error: pack?.errors?.[0]?.message },
                ];
              }
              return [form.form_id, pack];
            } catch (failure) {
              return [form.form_id, { editions: [], live: form.live || null, error: failure.message }];
            }
          })
        );
        if (!cancelled) {
          setPacks(Object.fromEntries(entries));
        }
      })
      .catch((failure) => {
        if (!cancelled) setError(failure.message || "Forms could not be loaded");
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
          setBusyLabel("");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const stats = useMemo(() => {
    const liveCount = forms.filter((form) => form.live).length;
    const editionCount = Object.values(packs).reduce(
      (sum, pack) => sum + (pack?.editions?.length || 0),
      0
    );
    return {
      forms: forms.length,
      live: liveCount,
      editions: editionCount,
    };
  }, [forms, packs]);

  const rows = useMemo(
    () =>
      forms.map((form) => {
        const pack = packs[form.form_id] || {};
        const editions = Array.isArray(pack.editions) ? pack.editions : [];
        const live = pack.live || form.live || null;
        const sorted = [...editions].sort((a, b) =>
          String(b.edition || "").localeCompare(String(a.edition || ""))
        );
        const liveEdition = sorted.find((edition) => edition.is_live) || live;
        // Prefer approved question count (not total imported / all questions)
        const approvedCount =
          liveEdition?.approved_count ??
          live?.approved_count ??
          live?.question_count ??
          null;
        return {
          form,
          pack,
          live,
          sorted,
          approvedCount,
          name: titleCaseForm(form.form_id, form.display_name),
        };
      }),
    [forms, packs]
  );

  if (loading && !forms.length) {
    return <PageLoader title="Loading forms" detail="Fetching live editions and year PDFs…" />;
  }

  return (
    <main className="page page-dashboard">
      {busyLabel ? <BusyOverlay label={busyLabel} /> : null}

      <div className="dashboard-hero">
        <div className="dashboard-hero-copy">
          <p className="eyebrow">Forms catalog</p>
          <h1>All forms &amp; years</h1>
          <p className="lede">
            Open a form to fill the live edition, compare another year, or review fields.
            Publish from Compare when a draft should become live.
          </p>
        </div>
        <div className="dashboard-hero-stats" aria-label="Catalog summary">
          <div className="dash-stat">
            <span className="dash-stat-value">{stats.forms}</span>
            <span className="dash-stat-label">Forms</span>
          </div>
          <div className="dash-stat">
            <span className="dash-stat-value">{stats.live}</span>
            <span className="dash-stat-label">Live</span>
          </div>
          <div className="dash-stat">
            <span className="dash-stat-value">{stats.editions}</span>
            <span className="dash-stat-label">Editions</span>
          </div>
        </div>
      </div>

      {error ? <StatusBanner tone="danger">{error}</StatusBanner> : null}

      {!forms.length && !error ? (
        <div className="dashboard-empty panel-surface">
          <h2>No forms yet</h2>
          <p className="lede">
            When FormsEngine lists forms (for example fw4 or colorado), they will appear here.
          </p>
        </div>
      ) : (
        <div className="catalog-table-wrap panel-surface">
          <div className="catalog-table-toolbar">
            <div>
              <h2>Forms</h2>
              <p className="panel-sub">Live editions and year history</p>
            </div>
          </div>
          <div className="catalog-table-scroll">
            <table className="catalog-table">
              <thead>
                <tr>
                  <th scope="col">Form</th>
                  <th scope="col">Live</th>
                  <th scope="col">Approved</th>
                  <th scope="col">Years</th>
                  <th scope="col">Editions</th>
                  <th scope="col" className="col-actions">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ form, pack, live, sorted, approvedCount, name }) => (
                  <tr key={form.form_id}>
                    <td>
                      <div className="catalog-form-cell">
                        <span className="form-card-mark" aria-hidden="true">
                          {(form.form_id || "?").slice(0, 2).toUpperCase()}
                        </span>
                        <div>
                          <a className="catalog-form-name" href={`#workspace/${form.form_id}`}>
                            {name}
                          </a>
                          <div className="catalog-form-id">{form.form_id}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      {live ? (
                        <span className="year-pill">{live.edition}</span>
                      ) : (
                        <span className="badge form-card-nolive">—</span>
                      )}
                    </td>
                    <td
                      className="catalog-num"
                      title="Approved questions on the live edition"
                    >
                      {approvedCount ?? "—"}
                    </td>
                    <td className="catalog-num">{sorted.length || "—"}</td>
                    <td>
                      {pack.error ? (
                        <span className="form-card-years-error">{pack.error}</span>
                      ) : sorted.length === 0 ? (
                        <span className="catalog-muted">—</span>
                      ) : (
                        <ul className="year-chip-list catalog-editions">
                          {sorted.map((edition) => (
                            <li key={edition.id}>
                              <span
                                className={`year-chip tone-${statusTone(edition)}`}
                                title={`${statusLabel(edition)} · ${edition.approved_count ?? edition.question_count ?? 0} questions`}
                              >
                                <span className="year-chip-year">{edition.edition || "—"}</span>
                                <span className="year-chip-status">{statusLabel(edition)}</span>
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td className="col-actions">
                      <div className="catalog-actions">
                        <a className="button-link primary" href={`#workspace/${form.form_id}`}>
                          Open
                        </a>
                        <a className="button-link secondary" href={`#compare/${form.form_id}`}>
                          Compare
                        </a>
                        <a className="button-link ghost" href={`#form/${form.form_id}`}>
                          Fill
                        </a>
                        <a className="button-link ghost" href={`#fields/${form.form_id}`}>
                          Review
                        </a>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </main>
  );
}
