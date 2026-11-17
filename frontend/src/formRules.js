const EMPTY = {
  checkbox: false,
  text: "",
  number: "",
  choice: "",
};

export function emptyAnswer(question) {
  return EMPTY[question.type] ?? "";
}

export function hasAnswer(question, value) {
  if (question.type === "checkbox") {
    return value === true;
  }
  if (question.type === "choice") {
    return typeof value === "string" && value !== "";
  }
  if (value == null) {
    return false;
  }
  return String(value).trim() !== "";
}

/** Match backend.rules.condition_met checkbox equals literals: true / "true" / "1" / "yes". */
export function checkboxEqualsWantsChecked(expected) {
  return expected === true || ["true", "1", "yes"].includes(String(expected).toLowerCase());
}

export function conditionMet(question, value, condition, expected) {
  if (condition === "answered") {
    return hasAnswer(question, value);
  }
  if (condition === "blank") {
    return !hasAnswer(question, value);
  }
  if (condition === "checked") {
    return question.type === "checkbox" && value === true;
  }
  if (condition === "equals") {
    if (question.type === "checkbox") {
      const want = checkboxEqualsWantsChecked(expected);
      return want ? value === true : value === false;
    }
    if (value == null) {
      return false;
    }
    return String(value).trim() === String(expected ?? "").trim();
  }
  return false;
}

export function evaluateEffects(questions, rules, answers) {
  const byId = Object.fromEntries(questions.map((question) => [question.id, question]));
  const working = { ...answers };
  let disabled = new Set();
  let optional = new Set();
  let required = new Set();

  for (let round = 0; round < questions.length + 2; round += 1) {
    const nextDisabled = new Set();
    const nextOptional = new Set();
    const nextRequired = new Set();
    for (const rule of rules || []) {
      const source = byId[rule.when_question];
      const target = byId[rule.then_question];
      if (!source || !target) {
        continue;
      }
      const sourceValue = disabled.has(rule.when_question) ? emptyAnswer(source) : working[rule.when_question];
      if (!conditionMet(source, sourceValue, rule.when_condition, rule.when_value)) {
        continue;
      }
      if (rule.then_effect === "disable") {
        nextDisabled.add(rule.then_question);
      } else if (rule.then_effect === "optional") {
        nextOptional.add(rule.then_question);
      } else if (rule.then_effect === "required") {
        nextRequired.add(rule.then_question);
      }
    }
    for (const id of nextDisabled) {
      nextOptional.delete(id);
      nextRequired.delete(id);
      working[id] = emptyAnswer(byId[id]);
    }
    const same =
      setsEqual(disabled, nextDisabled) && setsEqual(optional, nextOptional) && setsEqual(required, nextRequired);
    disabled = nextDisabled;
    optional = nextOptional;
    required = nextRequired;
    if (same) {
      break;
    }
  }

  const effects = {};
  for (const question of questions) {
    effects[question.id] = {
      disabled: disabled.has(question.id),
      required: disabled.has(question.id)
        ? false
        : required.has(question.id)
          ? true
          : optional.has(question.id)
            ? false
            : Boolean(question.required),
    };
  }
  return effects;
}

function setsEqual(left, right) {
  if (left.size !== right.size) {
    return false;
  }
  for (const value of left) {
    if (!right.has(value)) {
      return false;
    }
  }
  return true;
}

/** True when disable edges alone contain a directed cycle (matches backend.rules). */
export function rulesAreValid(rules, questions) {
  const ids = new Set(questions.map((question) => question.question_key || question.id));
  for (const rule of rules || []) {
    if (!ids.has(rule.when_question) || !ids.has(rule.then_question)) {
      return false;
    }
    if (rule.when_question === rule.then_question) {
      return false;
    }
    if (rule.when_condition === "equals" && !String(rule.when_value || "").trim()) {
      return false;
    }
  }
  if (disableDependencyCycleExists(rules)) {
    return false;
  }
  return true;
}

export function disableDependencyCycleExists(rules) {
  const graph = new Map();
  for (const rule of rules || []) {
    if (rule.then_effect !== "disable") {
      continue;
    }
    const source = rule.when_question;
    const target = rule.then_question;
    if (!graph.has(source)) {
      graph.set(source, new Set());
    }
    if (!graph.has(target)) {
      graph.set(target, new Set());
    }
    graph.get(source).add(target);
  }

  const visiting = new Set();
  const visited = new Set();

  function dfs(node) {
    visiting.add(node);
    for (const nxt of graph.get(node) || []) {
      if (visiting.has(nxt)) {
        return true;
      }
      if (!visited.has(nxt) && dfs(nxt)) {
        return true;
      }
    }
    visiting.delete(node);
    visited.add(node);
    return false;
  }

  for (const node of graph.keys()) {
    if (!visited.has(node) && dfs(node)) {
      return true;
    }
  }
  return false;
}

export function newRuleId() {
  return `rule-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function blankRule(questions) {
  const first = questions[0]?.id || "";
  const second = questions[1]?.id || questions[0]?.id || "";
  return {
    id: newRuleId(),
    when_question: first,
    when_condition: "answered",
    when_value: "",
    then_question: second,
    then_effect: "disable",
  };
}
