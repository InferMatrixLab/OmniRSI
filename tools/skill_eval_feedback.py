"""Bounded public projections, never raw answers or hidden expected answers.

Projection is called only after trusted in-tree regrading. Rendering still
validates the projection: stored report dictionaries are not publication authority.
"""
import html
import json
import re

from inferrsi.benchmark import skill_eval_diagnostic as diagnostic

COMPONENTS = {
    diagnostic.ADAPTER: ("schema", "diagnosis", "citation", "safe_next_check",
                         "no_invariant_violation"),
    "audit": ("schema", "report_verdict", "claim_scope", "findings", "reduction",
              "evidence", "explanation"),
}
REASONS = {
    "schema": "Answer does not satisfy the frozen schema.",
    "diagnosis": "Diagnosis does not match the recorded observations.",
    "citation": "Citations do not satisfy the exact decisive-evidence requirement.",
    "extra_citation": "Extra/nondecisive citation present; strict citation check fails.",
    "missing_citation": "Required decisive citation missing.",
    "duplicate_citation": "Duplicate citation present.",
    "safe_next_check": "Next read-only check does not match the recorded observations.",
    "no_invariant_violation": "Answer incorrectly authorizes reuse.",
    "report_verdict": "Report verdict fails the frozen check.",
    "claim_scope": "Claim scope fails the frozen check.",
    "findings": "Findings fail the frozen check.",
    "reduction": "Reduction fails the frozen numeric check.",
    "evidence": "Evidence references fail the frozen check.",
    "explanation": "Required explanation is absent or invalid.",
}


def projection(adapter, task, answer, checks):
    """Select only finite fields from a trusted grade; no oracle answer is emitted."""
    adapter = diagnostic.ADAPTER if adapter == diagnostic.ADAPTER else "audit"
    assertions = checks["assertions"]
    components = {key: assertions[key] for key in COMPONENTS[adapter] if key in assertions}
    reasons = [key for key, value in components.items() if value is False]
    selected = {}
    if adapter == diagnostic.ADAPTER and assertions.get("schema") is True:
        selected = {key: answer[key] for key in ("diagnosis", "next_check", "reuse_authorized")}
        citations = answer["evidence"]
        # Never include arbitrary strings or identifiers that could carry secrets.
        if (len(citations) <= 4 and all(isinstance(v, str) and
                re.fullmatch(r"E[0-9]{1,4}", v) for v in citations)):
            selected["evidence"] = citations
        if assertions.get("citation") is False:
            _, required, _ = diagnostic.expected(task)
            cited = set(citations)
            reasons += (["extra_citation"] if cited - required else [])
            reasons += (["missing_citation"] if required - cited else [])
            reasons += (["duplicate_citation"] if len(cited) != len(citations) else [])
    return {"adapter": adapter, "components": components, "answer": selected,
            "reasons": reasons}


def code(value):
    """Render a bounded safe identifier, without Markdown or mentions."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value):
        return "unavailable"
    return "<code>" + html.escape(value) + "</code>"


def safe_text(value, limit=512):
    """Bound presentation metadata and neutralize Markdown/HTML/mention syntax."""
    if not isinstance(value, str):
        return "unavailable"
    value = value[:limit].replace("\n", " ").replace("\r", " ")
    return "".join("&#" + str(ord(char)) + ";" if char in "`@*_[\\]#|"
                   else html.escape(char) for char in value)


def json_for_code(value):
    """Keep valid copyable JSON while neutralizing fence/HTML/mention characters."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(text) > 12000:
        return "[]"
    for char in "`<>&@":
        text = text.replace(char, "\\u" + format(ord(char), "04x"))
    return text


def trial_text(trial):
    if not isinstance(trial, dict) or trial.get("grader_trust") != "reverified":
        return "unavailable (no trusted component evidence)"
    verdict = trial.get("verdict")
    if verdict not in ("PASS", "FAIL"):
        return "unavailable (incomplete or invalid retained evidence)"
    data = trial.get("feedback")
    if (not isinstance(data, dict) or not isinstance(data.get("adapter"), str)
            or data["adapter"] not in COMPONENTS):
        return verdict + "; components unavailable"
    adapter = data["adapter"]
    checks = data.get("components")
    reasons = data.get("reasons")
    if (not isinstance(checks, dict) or not checks or
            any(k not in COMPONENTS[adapter] or type(v) is not bool for k, v in checks.items())
            or not isinstance(reasons, list) or len(reasons) > 12
            or any(not isinstance(r, str) or r not in REASONS for r in reasons)):
        return verdict + "; components unavailable (invalid projection)"
    parts = [verdict]
    answer = data.get("answer")
    if adapter == diagnostic.ADAPTER and isinstance(answer, dict):
        for key, allowed in (("diagnosis", diagnostic.DIAGNOSES),
                             ("next_check", diagnostic.CHECKS)):
            value = answer.get(key)
            if type(value) is str and value in allowed:
                parts.append(key + "=" + code(value))
        citations = answer.get("evidence")
        if (isinstance(citations, list) and len(citations) <= 4 and
                all(isinstance(v, str) and re.fullmatch(r"E[0-9]{1,4}", v) for v in citations)):
            parts.append("citations=" + (", ".join(code(v) for v in citations) or "none"))
        if type(answer.get("reuse_authorized")) is bool:
            parts.append("reuse_authorized=" + str(answer["reuse_authorized"]).lower())
    parts.append("components: " + ", ".join(
        key + "=" + ("PASS" if checks[key] else "FAIL") if key in checks else key + "=unavailable"
        for key in COMPONENTS[adapter]))
    if reasons:
        specific = any(r in reasons for r in ("extra_citation", "missing_citation",
                                             "duplicate_citation"))
        parts.append("Reasons: " + " ".join(REASONS[r] for r in dict.fromkeys(reasons)
                                            if not (r == "citation" and specific)))
    return "; ".join(parts)


def component_changes(pair):
    before, after = (pair.get(role, {}) for role in ("baseline", "candidate"))
    if any(not isinstance(t, dict) or t.get("grader_trust") != "reverified" or
           t.get("verdict") not in ("PASS", "FAIL") for t in (before, after)):
        return "Component changes unavailable."
    old, new = (t.get("feedback", {}) for t in (before, after))
    if not isinstance(old, dict) or not isinstance(new, dict):
        return "Component changes unavailable."
    adapter = old.get("adapter")
    if not isinstance(adapter, str) or adapter not in COMPONENTS or new.get("adapter") != adapter:
        return "Component changes unavailable."
    checks = [v.get("components") for v in (old, new)]
    if any(not isinstance(c, dict) for c in checks):
        return "Component changes unavailable."
    changes = []
    for key in COMPONENTS[adapter]:
        a, b = (c.get(key) for c in checks)
        if type(a) is bool and type(b) is bool and a != b:
            changes.append(key + ": " + ("PASS" if a else "FAIL") + " → "
                           + ("PASS" if b else "FAIL"))
    return "Component changes: " + ("; ".join(changes) or "none observed") + "."


def comparison_lines(plan, results, evidence_state):
    """Only exact, unique bindings may expose component explanations."""
    if evidence_state != "available":
        return []
    lines, count = [], 0
    for item, result in bound_results(plan, results):
        allowed = {(case["id"], repeat) for case in item["cases"] if case["split"] != "discovery"
                   for repeat in range(1, plan["limits"]["repeats"] + 1)}
        pairs = result.get("pairs", [])
        seen = [(p.get("case_id"), p.get("repeat")) for p in pairs]
        if len(set(seen)) != len(seen):
            continue
        for pair in pairs:
            if (pair.get("case_id"), pair.get("repeat")) not in allowed:
                continue
            count += 1
            if count > 8:
                continue
            lines += ["", "**" + code(item["skill"]) + " / " + code(pair["case_id"])
                      + " · repeat " + str(pair["repeat"]) + "**",
                      component_changes(pair),
                      "- Baseline: " + trial_text(pair.get("baseline")),
                      "- Candidate: " + trial_text(pair.get("candidate"))]
            if any(isinstance(pair.get(role, {}).get("feedback"), dict) and
                   pair[role]["feedback"].get("adapter") == diagnostic.ADAPTER
                   for role in ("baseline", "candidate")):
                lines += ["Coverage: one synthetic diagnostic case per pair; not a production "
                          "trace, engine benchmark or general efficacy result."]
    if lines:
        lines = ["### Retained baseline → candidate", "Strict aggregate outcomes are unchanged "
                 "by component explanations; unavailable is not failure."] + lines
        gaps = sum(item["coverage"] != "covered" for item in plan["coverage"])
        lines += ["", f"Showing {min(count, 8)} of {count} bound pairs; discovery excluded. "
                  f"Uncovered/unsupported changes: **{gaps}**; gaps never count as passes."]
    return lines


def evidence_binding_lines(plan, results, evidence_state):
    if evidence_state != "available":
        return []
    lines = []
    for item, result in bound_results(plan, results):
        lines += ["Frozen comparison " + code(item["skill"]) + ": " + code(item["plan_sha256"])]
        for pair in result.get("pairs", [])[:8]:
            lines += ["Case " + code(pair["case_id"]) + " answer SHA-256 (baseline / candidate): "
                      + " / ".join(code(pair.get(role, {}).get("output_sha256"))
                                   for role in ("baseline", "candidate"))]
    return lines[:40]


def bound_results(plan, results):
    """No aggregate or detail can outlive its unique revision/plan/exam binding."""
    for item in plan["comparisons"]:
        if "plan_sha256" not in item:
            continue
        matches = [r for r in results if r.get("skill") == item["skill"]]
        if len(matches) != 1:
            continue
        result = matches[0]
        if (result.get("plan_sha256") != item["plan_sha256"] or
                result.get("exam") != item["exam"] or
                any(result.get("arms", {}).get(role, {}).get("git_revision") != plan[key]
                    for role, key in (("baseline", "base"), ("candidate", "head")))):
            continue
        yield item, result
