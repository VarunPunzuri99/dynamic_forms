import { blankRule, rulesAreValid } from "./formRules";

export { rulesAreValid };

const CONDITIONS = [
  { value: "answered", label: "has an answer" },
  { value: "blank", label: "is left blank" },
  { value: "checked", label: "is checked" },
  { value: "equals", label: "equals" },
];

const EFFECTS = [
  { value: "disable", label: "turn it off", hint: "The employee cannot fill it, and it is skipped on submit." },
  { value: "optional", label: "make it optional", hint: "The employee may leave it blank." },
  { value: "required", label: "make it required", hint: "The employee must answer it." },
];

function shortLabel(question, index) {
  const text = (question.label || question.question_key || question.id || "").trim();
  const short = text.length > 72 ? `${text.slice(0, 69)}…` : text;
  return `Q${index + 1}. ${short || "Untitled question"}`;
}

function conditionLabel(value) {
  return CONDITIONS.find((item) => item.value === value)?.label || value;
}

function effectLabel(value) {
  return EFFECTS.find((item) => item.value === value)?.label || value;
}

function sentenceFor(rule, choices) {
  const when = choices.find((item) => item.id === rule.when_question);
  const then = choices.find((item) => item.id === rule.then_question);
  const whenText = when ? shortLabel(when, when.index) : "a missing question";
  const thenText = then ? shortLabel(then, then.index) : "a missing question";
  const valueBit =
    rule.when_condition === "equals" && rule.when_value
      ? ` “${rule.when_value}”`
      : "";
  return `If ${whenText} ${conditionLabel(rule.when_condition)}${valueBit}, then ${effectLabel(rule.then_effect)} ${thenText}.`;
}

export default function RulesEditor({ questions, rules, onChange, disabled }) {
  const choices = questions.map((question, index) => ({
    id: question.question_key || question.id,
    type: question.input_type || question.type,
    label: question.label,
    options: question.options || [],
    index,
  }));

  function updateRule(ruleId, patch) {
    onChange(rules.map((rule) => (rule.id === ruleId ? { ...rule, ...patch } : rule)));
  }

  function removeRule(ruleId) {
    onChange(rules.filter((rule) => rule.id !== ruleId));
  }

  function addRule() {
    if (choices.length < 1) {
      return;
    }
    onChange([...rules, blankRule(choices.map((item) => ({ id: item.id })))]);
  }

  return (
    <section className="rules-panel">
      <header className="rules-head">
        <div>
          <p className="rules-eyebrow">Reviewer step</p>
          <h2>Optional answer rules</h2>
          <p className="lede">
            Tell the form what to do when one answer changes another. Example: if question 2 has an answer, turn question 3 off so the employee can go straight to question 4. Skip this if every checked question should always stay available.
          </p>
        </div>
        <div className="rules-count" aria-live="polite">
          <strong>{rules.length}</strong>
          <span>{rules.length === 1 ? "rule" : "rules"}</span>
        </div>
      </header>

      {choices.length < 2 ? (
        <p className="rules-empty">Check at least two questions above before you can add a rule.</p>
      ) : null}

      {rules.length === 0 && choices.length >= 2 ? (
        <div className="rules-empty-card">
          <p className="rules-empty-title">No rules yet</p>
          <p>Every checked question stays available while the employee fills the form.</p>
        </div>
      ) : null}

      <div className="rules-list">
        {rules.map((rule, ruleIndex) => {
          const when = choices.find((item) => item.id === rule.when_question);
          const then = choices.find((item) => item.id === rule.then_question);
          const missingWhen = !when;
          const missingThen = !then;
          const effect = EFFECTS.find((item) => item.value === rule.then_effect);
          return (
            <article key={rule.id} className={missingWhen || missingThen ? "rule-card has-error" : "rule-card"}>
              <div className="rule-card-top">
                <span className="rule-number">Rule {ruleIndex + 1}</span>
                <button
                  type="button"
                  className="rule-remove"
                  disabled={disabled}
                  onClick={() => removeRule(rule.id)}
                >
                  Remove
                </button>
              </div>

              <p className="rule-sentence">{sentenceFor(rule, choices)}</p>

              <div className="rule-flow">
                <div className="rule-step">
                  <p className="rule-step-label">If this happens</p>
                  <label className="rule-field">
                    <span>Question</span>
                    <select
                      className="rule-select"
                      value={rule.when_question}
                      disabled={disabled}
                      onChange={(event) =>
                        updateRule(rule.id, { when_question: event.target.value, when_value: "" })
                      }
                    >
                      {choices.map((item) => (
                        <option key={item.id} value={item.id}>
                          {shortLabel(item, item.index)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="rule-field">
                    <span>Condition</span>
                    <select
                      className="rule-select"
                      value={rule.when_condition}
                      disabled={disabled}
                      onChange={(event) => updateRule(rule.id, { when_condition: event.target.value })}
                    >
                      {CONDITIONS.filter((item) => item.value !== "checked" || when?.type === "checkbox").map(
                        (item) => (
                          <option key={item.value} value={item.value}>
                            {item.label}
                          </option>
                        )
                      )}
                    </select>
                  </label>
                  {rule.when_condition === "equals" ? (
                    when?.type === "choice" && when.options.length ? (
                      <label className="rule-field">
                        <span>Value</span>
                        <select
                          className="rule-select"
                          value={rule.when_value || ""}
                          disabled={disabled}
                          onChange={(event) => updateRule(rule.id, { when_value: event.target.value })}
                        >
                          <option value="">Choose a value</option>
                          {when.options.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : (
                      <label className="rule-field">
                        <span>Value</span>
                        <input
                          className="rule-input"
                          type="text"
                          value={rule.when_value || ""}
                          disabled={disabled}
                          onChange={(event) => updateRule(rule.id, { when_value: event.target.value })}
                        />
                      </label>
                    )
                  ) : null}
                </div>

                <div className="rule-arrow" aria-hidden="true">
                  <span>then</span>
                </div>

                <div className="rule-step">
                  <p className="rule-step-label">Do this</p>
                  <label className="rule-field">
                    <span>Question</span>
                    <select
                      className="rule-select"
                      value={rule.then_question}
                      disabled={disabled}
                      onChange={(event) => updateRule(rule.id, { then_question: event.target.value })}
                    >
                      {choices.map((item) => (
                        <option key={item.id} value={item.id}>
                          {shortLabel(item, item.index)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="rule-field">
                    <span>Result</span>
                    <select
                      className="rule-select"
                      value={rule.then_effect}
                      disabled={disabled}
                      onChange={(event) => updateRule(rule.id, { then_effect: event.target.value })}
                    >
                      {EFFECTS.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {effect ? <p className="rule-hint">{effect.hint}</p> : null}
                </div>
              </div>

              {missingWhen || missingThen ? (
                <p className="field-error">This rule points at a question that is not checked above. Check that question or remove the rule.</p>
              ) : null}
            </article>
          );
        })}
      </div>

      <div className="rules-actions">
        <button type="button" className="rule-add" disabled={disabled || choices.length < 2} onClick={addRule}>
          + Add a rule
        </button>
        <p className="rule-footnote">Rules are saved with this edition when you save or approve the checked questions.</p>
      </div>
    </section>
  );
}

