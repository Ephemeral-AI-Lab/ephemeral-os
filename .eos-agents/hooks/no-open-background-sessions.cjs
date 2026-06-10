#!/usr/bin/env node

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
    process.stdout.write(
      JSON.stringify({
        decision: "deny",
        reason: "cannot submit: hook payload was not valid JSON",
      }),
    );
    return;
  }

  const sessions = Array.isArray(payload.background_sessions)
    ? payload.background_sessions
    : [];
  if (sessions.length === 0) {
    process.stdout.write(JSON.stringify({ decision: "allow" }));
    return;
  }

  const names = sessions
    .map((session) => {
      const type = typeof session.type === "string" ? session.type : "session";
      const id = typeof session.id === "string" ? session.id : "unknown";
      const status = typeof session.status === "string" ? session.status : "open";
      return `${type}:${id} (${status})`;
    })
    .join(", ");
  process.stdout.write(
    JSON.stringify({
      decision: "deny",
      reason: `cannot submit while ${String(sessions.length)} background session(s) are open (running or undelivered): ${names}. Cancel them or wait for their completion notices.`,
    }),
  );
});
