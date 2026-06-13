const { create_variable_reference_map } = require("./variable_reference_map.cjs");

function user(text) {
  return { role: "user", content: [{ type: "text", text }] };
}

function get_initial_messages(vars) {
  const messages = [
    user(`# Current leg goal\n${vars.current_leg_goal ?? ""}`),
    user(`# Work item path\n${vars.work_item_context_path ?? ""}`),
    user(`# Work item title\n${vars.work_item_title ?? ""}`),
    user(`# Work item spec\n${vars.assigned_work_spec ?? ""}`),
  ];
  if (vars.dependency_outcomes.length > 0) {
    messages.splice(
      1,
      0,
      user(`# Direct dependency outcomes\n${JSON.stringify(vars.dependency_outcomes)}`),
    );
  }
  messages.push(
    user(
      "Stay inside the current leg_goal and this work item. Do not plan new legs, " +
        "change leg_goal, or decide next_leg_goal. Submit worker outcome for this work item.",
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
