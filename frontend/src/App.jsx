import { useEffect, useState } from "react";
import Compare from "./Compare";
import Dashboard from "./Dashboard";
import EmployeeForm from "./EmployeeForm";
import Fields from "./Fields";
import FormWorkspace from "./FormWorkspace";
import { Spinner } from "./LoadingUI";

function readRoute() {
  const raw = window.location.hash.replace(/^#/, "");
  const [screen, formId] = raw.split("/");

  // Old W-4-only bookmark (#review) → dynamic Review fields for every form.
  if (screen === "review") {
    const id = formId || "fw4";
    return { screen: "fields", formId: id, redirectTo: `#fields/${id}` };
  }
  if (!screen || screen === "home" || screen === "dashboard") {
    return { screen: "home", formId: null };
  }
  if (screen === "workspace" && formId) {
    return { screen: "workspace", formId };
  }
  if (screen === "compare" && formId) {
    return { screen: "compare", formId };
  }
  if (screen === "fields" && formId) {
    return { screen: "fields", formId };
  }
  if (screen === "form" && formId) {
    return { screen: "employee", formId };
  }
  // Legacy bare #form / #compare without id → home
  if (screen === "form" || screen === "compare" || screen === "fields") {
    return { screen: "home", formId: null, redirectTo: "#home" };
  }
  return { screen: "home", formId: null, redirectTo: "#home" };
}

export default function App() {
  const [route, setRoute] = useState(readRoute);
  const [forms, setForms] = useState([]);
  const [formsLoading, setFormsLoading] = useState(true);

  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!window.location.hash || window.location.hash === "#") {
      window.location.hash = "#home";
    }
  }, []);

  useEffect(() => {
    if (route.redirectTo && window.location.hash !== route.redirectTo) {
      window.location.hash = route.redirectTo;
    }
  }, [route.redirectTo]);

  useEffect(() => {
    setFormsLoading(true);
    fetch("/api/forms")
      .then((response) => response.json())
      .then((body) => {
        if (Array.isArray(body)) {
          setForms(body);
        }
      })
      .catch(() => setForms([]))
      .finally(() => setFormsLoading(false));
  }, []);

  const formId = route.formId;
  const onHome = route.screen === "home";
  const inForm = Boolean(formId);

  function chooseForm(nextFormId) {
    if (!nextFormId) {
      window.location.hash = "#home";
      return;
    }
    const screen =
      route.screen === "compare"
        ? "compare"
        : route.screen === "fields"
          ? "fields"
          : route.screen === "workspace"
            ? "workspace"
            : "form";
    window.location.hash = `${screen}/${nextFormId}`;
  }

  const screen =
    route.screen === "employee"
      ? "form"
      : route.screen === "home"
        ? "home"
        : route.screen;

  const selectedForm = forms.find((form) => form.form_id === formId);

  return (
    <div className="app">
      <header className="app-header">
        <div className={`app-header-inner ${onHome ? "is-home" : "is-form"}`}>
          <a className="brand-block brand-link" href="#home">
            <span className="brand-mark" aria-hidden="true" />
            <div className="brand-copy">
              <span className="brand">Forms Studio</span>
              <span className="brand-sub">Review · Approve · Fill</span>
            </div>
          </a>

          {onHome ? (
            <div className="header-home-tag">
              <span className="header-home-pill">Dashboard</span>
              <span className="header-home-hint">Pick a form to continue</span>
            </div>
          ) : (
            <nav className="topnav" aria-label="Primary">
              <a
                className={screen === "workspace" ? "active" : ""}
                href={`#workspace/${formId}`}
              >
                Overview
              </a>
              <a className={screen === "form" ? "active" : ""} href={`#form/${formId}`}>
                Fill form
              </a>
              <a
                className={screen === "compare" ? "active" : ""}
                href={`#compare/${formId}`}
              >
                Compare years
              </a>
              <a
                className={screen === "fields" ? "active" : ""}
                href={`#fields/${formId}`}
              >
                Review fields
              </a>
            </nav>
          )}

          {inForm ? (
            <label className="form-picker">
              <span className="form-picker-label">Active form</span>
              {formsLoading ? <Spinner size="xs" /> : null}
              <select
                className="compare-select"
                value={formId}
                disabled={formsLoading}
                onChange={(event) => chooseForm(event.target.value)}
              >
                {forms.length === 0 ? <option value={formId}>{formId}</option> : null}
                {forms.map((form) => (
                  <option key={form.form_id} value={form.form_id}>
                    {form.display_name || form.form_id}
                    {form.live
                      ? ` · ${form.live.edition} · ${form.live.question_count} questions`
                      : ""}
                  </option>
                ))}
              </select>
              {selectedForm?.live ? (
                <span
                  className="form-picker-meta"
                  title={`Live ${selectedForm.live.edition}`}
                >
                  Live {selectedForm.live.edition}
                </span>
              ) : null}
            </label>
          ) : (
            <div className="header-spacer" aria-hidden="true" />
          )}
        </div>
      </header>

      <div className="app-body">
        {route.screen === "home" ? (
          <Dashboard />
        ) : route.screen === "workspace" ? (
          <FormWorkspace key={formId} formId={formId} />
        ) : route.screen === "compare" ? (
          <Compare key={formId} formId={formId} />
        ) : route.screen === "fields" ? (
          <Fields key={formId} formId={formId} />
        ) : (
          <EmployeeForm key={formId} formId={formId} />
        )}
      </div>
    </div>
  );
}
