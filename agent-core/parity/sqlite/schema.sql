-- Canonical clean schema (Base.metadata.create_all) for the seven
-- target tables. Captured by parity/_capture/capture.py; the live DDL
-- patches in db/engine.py.initialize_db are intentionally NOT applied
-- (eos-db replaces them with versioned migrations). Do not edit by hand.

CREATE UNIQUE INDEX ix_agent_runs_task_id ON agent_runs (task_id);

CREATE INDEX ix_attempts_iteration_id ON attempts (iteration_id);

CREATE INDEX ix_attempts_workflow_id ON attempts (workflow_id);

CREATE INDEX ix_iterations_workflow_id ON iterations (workflow_id);

CREATE INDEX ix_tasks_attempt_id ON tasks (attempt_id);

CREATE INDEX ix_tasks_iteration_id ON tasks (iteration_id);

CREATE INDEX ix_tasks_request_id ON tasks (request_id);

CREATE INDEX ix_tasks_workflow_id ON tasks (workflow_id);

CREATE INDEX ix_workflows_parent_task_id ON workflows (parent_task_id);

CREATE INDEX ix_workflows_request_id ON workflows (request_id);

CREATE TABLE agent_runs (
	id VARCHAR(36) NOT NULL,
	task_id VARCHAR(96) NOT NULL,
	initial_messages JSON,
	agent_name VARCHAR(128) NOT NULL,
	message_history JSON,
	terminal_tool_result JSON,
	token_count INTEGER NOT NULL,
	error TEXT,
	created_at DATETIME NOT NULL,
	finished_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE
);

CREATE TABLE attempts (
	id VARCHAR(36) NOT NULL,
	iteration_id VARCHAR(36) NOT NULL,
	workflow_id VARCHAR(36) NOT NULL,
	attempt_sequence_no INTEGER NOT NULL,
	stage VARCHAR(16) NOT NULL,
	status VARCHAR(16) NOT NULL,
	planner_task_id VARCHAR(96),
	generator_task_ids JSON NOT NULL,
	reducer_task_ids JSON NOT NULL,
	outcomes JSON NOT NULL,
	deferred_goal TEXT,
	fail_reason VARCHAR(48),
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	closed_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_attempt_iteration_sequence UNIQUE (iteration_id, attempt_sequence_no),
	FOREIGN KEY(iteration_id) REFERENCES iterations (id) ON DELETE CASCADE
);

CREATE TABLE iterations (
	id VARCHAR(36) NOT NULL,
	workflow_id VARCHAR(36) NOT NULL,
	sequence_no INTEGER NOT NULL,
	creation_reason VARCHAR(32) NOT NULL,
	goal TEXT NOT NULL,
	attempt_budget INTEGER NOT NULL,
	status VARCHAR(16) NOT NULL,
	attempt_ids JSON NOT NULL,
	deferred_goal TEXT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	closed_at DATETIME,
	outcomes TEXT,
	PRIMARY KEY (id),
	CONSTRAINT uq_iteration_workflow_sequence UNIQUE (workflow_id, sequence_no),
	FOREIGN KEY(workflow_id) REFERENCES workflows (id) ON DELETE CASCADE
);

CREATE TABLE model_registrations (
	id INTEGER NOT NULL,
	"key" VARCHAR(128) NOT NULL,
	label VARCHAR(256) NOT NULL,
	class_path VARCHAR(512) NOT NULL,
	kwargs_json TEXT NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE ("key")
);

CREATE TABLE requests (
	id VARCHAR(36) NOT NULL,
	cwd VARCHAR(1024) NOT NULL,
	sandbox_id VARCHAR(128),
	request_prompt TEXT NOT NULL,
	root_task_id VARCHAR(96),
	status VARCHAR(32) NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	finished_at DATETIME,
	PRIMARY KEY (id)
);

CREATE TABLE tasks (
	id VARCHAR(96) NOT NULL,
	request_id VARCHAR(36) NOT NULL,
	role VARCHAR(32) NOT NULL,
	instruction TEXT NOT NULL,
	status VARCHAR(32) NOT NULL,
	workflow_id VARCHAR(36),
	iteration_id VARCHAR(36),
	attempt_id VARCHAR(96),
	agent_name VARCHAR(128),
	needs JSON NOT NULL,
	outcomes JSON NOT NULL,
	terminal_tool_result JSON,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(request_id) REFERENCES requests (id) ON DELETE CASCADE
);

CREATE TABLE workflows (
	id VARCHAR(36) NOT NULL,
	request_id VARCHAR(36) NOT NULL,
	parent_task_id VARCHAR(96) NOT NULL,
	goal TEXT NOT NULL,
	status VARCHAR(16) NOT NULL,
	iteration_ids JSON NOT NULL,
	outcomes TEXT,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	closed_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(request_id) REFERENCES requests (id) ON DELETE CASCADE
);
