const { create_variable_reference_map } = require("./variable_reference_map.cjs");

function user(text) {
  return { role: "user", content: [{ type: "text", text }] };
}

function get_initial_messages(vars) {
  const messages = [
    user(`# Pursuit goal\n${vars.pursuit_goal}`),
    user(`# Current leg goal\n${vars.current_leg_goal ?? ""}`),
    user(`# Current leg context\n${vars.current_leg_context_path ?? ""}`),
  ];

  if (vars.pursuit_leg_goal_mode === "predefined") {
    messages.push(
      user(
        "The caller predefined this leg_goal. Omit leg_goal and next_leg_goal. " +
          "Do not refocus this leg or declare future legs. If the predefined " +
          "leg_goal is too broad or wrong, plan only work that completes the " +
          "current predefined leg_goal.",
      ),
    );
  } else {
    if (vars.current_leg_next_leg_goal !== null) {
      messages.push(user(`# Standing next_leg_goal\n${vars.current_leg_next_leg_goal}`));
    }
    messages.push(
      user(
        "Dynamic mode: A new dynamic leg exists only because the previous leg " +
          "closed successfully and declared next_leg_goal. Success means the " +
          "full effective leg_goal is achieved. Omit leg_goal to keep the " +
          "current leg goal. Include leg_goal only to refocus this leg. " +
          "Refocus supersedes prior live attempts and resets the standing " +
          "next_leg_goal. Include next_leg_goal only for work that should become " +
          "a future leg after this leg succeeds. If you cannot achieve the full " +
          "leg_goal in this leg, submit a narrowed leg_goal and put the remainder " +
          "in next_leg_goal. Omitting both preserves any standing next_leg_goal.",
      ),
    );
  }

  if (vars.previous_attempt_outcome !== null) {
    messages.push(user(`# Previous attempt\n${JSON.stringify(vars.previous_attempt_outcome)}`));
  }
  messages.push(
    user(
      "Submit work_items with id, agent_name, title, spec, and depends_on. " +
        "depends_on may reference current payload ids or visible prior work items " +
        "in this leg-goal version.",
    ),
  );
  return messages;
}

let input = "";
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", () => {
  const vars = create_variable_reference_map(JSON.parse(input));
  process.stdout.write(JSON.stringify({ initial_messages: get_initial_messages(vars) }));
});
