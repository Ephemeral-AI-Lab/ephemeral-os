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
        "Plan work items for this predefined leg goal. Omit leg_goal and " +
          "next_leg_goal in submit_planner_outcome.",
      ),
    );
  } else {
    messages.push(
      user(
        "Dynamic mode: omit leg_goal to keep the current leg goal, submit " +
          "leg_goal to refocus, or submit successor-only next_leg_goal for the " +
          "next leg. Clearing a standing next_leg_goal requires submitting a " +
          "replacement leg_goal.",
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
