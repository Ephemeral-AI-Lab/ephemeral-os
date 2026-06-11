// stdin: PlannerContextInput JSON
// stdout: { initial_messages: UserMessage[] }
const { create_variable_reference_map } = require("./variable_reference_map.cjs");

function get_initial_messages(vars) {
  const user = (text) => ({ role: "user", content: [{ type: "text", text }] });
  const messages = [user(`# Workflow goal\n${vars.workflow_goal}`)];

  if (vars.current_iteration_focus === null) {
    messages.push(user("Declare this iteration's focus and work items."));
  } else {
    messages.push(user(`# Iteration focus\n${vars.current_iteration_focus}`));
    if (vars.previous_attempt_outcome !== null) {
      messages.push(
        user(`# Previous attempt\n${JSON.stringify(vars.previous_attempt_outcome)}`),
      );
    }
    messages.push(user("Submit planner outcome with work items for this focus."));
  }

  return messages;
}

let input = "";
process.stdin.on("data", (c) => (input += c));
process.stdin.on("end", () => {
  const ctx = JSON.parse(input);
  const vars = create_variable_reference_map(ctx);
  const initial_messages = get_initial_messages(vars);
  process.stdout.write(JSON.stringify({ initial_messages }));
});
