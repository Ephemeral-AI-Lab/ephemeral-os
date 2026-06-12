#!/usr/bin/env node

const { readFileSync } = require("node:fs");

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  let payload;
  try {
    payload = JSON.parse(input);
  } catch {
    deny("cannot submit: hook payload was not valid JSON");
    return;
  }

  if (payload.advisory_requirement?.required !== true) {
    allow();
    return;
  }

  if (!isJsonObject(payload.tool_input)) {
    deny("cannot submit: tool_input must be a JSON object");
    return;
  }

  const transcriptPath = payload.run?.transcript_path;
  if (typeof transcriptPath !== "string" || transcriptPath.length === 0) {
    deny("cannot submit: missing transcript path for advisory verification");
    return;
  }

  let transcript;
  try {
    transcript = readFileSync(transcriptPath, "utf8");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    deny(`cannot submit: unable to read transcript for advisory verification: ${message}`);
    return;
  }

  const target = {
    tool_name: payload.tool_name,
    payload: payload.tool_input,
  };
  const latest = latestAdvisoryVerdict(transcript, target);
  if (latest.kind === "pass") {
    allow();
    return;
  }
  deny(reasonFor(latest));
});

function latestAdvisoryVerdict(transcript, target) {
  const askAdvisorIds = new Set();
  let latest = { kind: "missing" };
  for (const rawLine of transcript.split("\n")) {
    if (rawLine.trim() === "") continue;
    let line;
    try {
      line = JSON.parse(rawLine);
    } catch {
      continue;
    }

    if (line?.kind === "assistant" && Array.isArray(line.message?.content)) {
      for (const block of line.message.content) {
        if (
          block?.type === "tool_use" &&
          block.name === "ask_advisor" &&
          canonicalJson(block.input) === canonicalJson(target)
        ) {
          askAdvisorIds.add(block.tool_use_id);
          latest = { kind: "pending" };
        }
      }
      continue;
    }

    if (
      line?.kind !== "tool_result" ||
      !askAdvisorIds.has(line.result?.tool_use_id)
    ) {
      continue;
    }

    if (line.result.is_error === true) {
      latest = { kind: "error" };
      continue;
    }

    const verdict = advisoryVerdict(line.result.content);
    if (verdict === undefined) {
      latest = { kind: "invalid" };
      continue;
    }
    if (
      verdict.tool_name !== target.tool_name ||
      canonicalJson(verdict.payload) !== canonicalJson(target.payload)
    ) {
      latest = { kind: "mismatch" };
      continue;
    }
    latest = { kind: verdict.verdict };
  }
  return latest;
}

function advisoryVerdict(content) {
  const submission = parseJsonObject(content);
  if (submission === undefined) return undefined;
  const payload = submission.payload;
  if (!isJsonObject(payload)) return undefined;
  if (payload.verdict !== "pass" && payload.verdict !== "fail") return undefined;
  if (typeof payload.tool_name !== "string" || payload.tool_name.length === 0) {
    return undefined;
  }
  if (!isJsonObject(payload.payload)) return undefined;
  if (typeof payload.reason !== "string" || payload.reason.length === 0) {
    return undefined;
  }
  return payload;
}

function parseJsonObject(content) {
  if (isJsonObject(content)) return content;
  if (typeof content !== "string") return undefined;
  try {
    const parsed = JSON.parse(content);
    return isJsonObject(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function reasonFor(latest) {
  switch (latest.kind) {
    case "missing":
      return "cannot submit: advisory pass required but no matching ask_advisor call was found";
    case "pending":
      return "cannot submit: advisory pass required but the matching ask_advisor call has no result";
    case "error":
      return "cannot submit: advisory pass required but ask_advisor failed";
    case "invalid":
      return "cannot submit: advisory pass required but advisor did not return a valid verdict";
    case "mismatch":
      return "cannot submit: advisory pass required but advisor verdict targeted a different tool or payload";
    case "fail":
      return "cannot submit: advisor verdict was fail";
    default:
      return "cannot submit: advisory pass required";
  }
}

function isJsonObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (isJsonObject(value)) {
    const entries = Object.entries(value)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value);
}

function allow() {
  process.stdout.write(JSON.stringify({ decision: "allow" }));
}

function deny(reason) {
  process.stdout.write(JSON.stringify({ decision: "deny", reason }));
}
