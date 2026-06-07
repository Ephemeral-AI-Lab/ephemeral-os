//! The single "drive one agent" loop driver.

use std::time::Instant;

use eos_agent_message_records::{AgentRunRecordStart, NodeFinishStatus};
use futures::StreamExt;

use crate::query::run_query;
use crate::telemetry::{publish_agent_run_completed, publish_os_resource_sampled};

use super::persistence::{create_agent_run_if_requested, finish_agent_run_if_requested};
use super::setup::{prepare_agent_run_context, AgentRunSetupInput};
use super::types::{AgentRunInput, AgentRunResult, EngineRunHandles, EventCallback};

/// Drive one agent to completion.
pub async fn run_agent(
    handles: &EngineRunHandles,
    input: AgentRunInput,
    on_event: Option<&EventCallback>,
) -> AgentRunResult {
    let run_started = Instant::now();
    let AgentRunInput {
        agent,
        mut initial_messages,
        task_id,
        agent_run_id,
        tool_metadata,
        attempt_submission,
        workflow_control,
        background_supervisor,
        command_session_supervisor,
        notifier,
        persist_agent_run,
        record_kind,
    } = input;

    let persistence_requested = create_agent_run_if_requested(
        handles,
        persist_agent_run,
        task_id.as_ref(),
        &agent_run_id,
        agent.name.as_str(),
    )
    .await;
    let prepared = prepare_agent_run_context(
        handles,
        AgentRunSetupInput {
            agent,
            task_id,
            agent_run_id: agent_run_id.clone(),
            tool_metadata,
            attempt_submission,
            workflow_control,
            background_supervisor,
            command_session_supervisor,
            notifier,
        },
    );

    let mut prepared = match prepared {
        Ok(prepared) => prepared,
        Err(err) => {
            let summary = err.to_string();
            finish_agent_run_if_requested(
                handles,
                persistence_requested,
                &agent_run_id,
                Some(&summary),
            )
            .await;
            return AgentRunResult {
                terminal_result: None,
                error: Some(summary),
            };
        }
    };
    if let Some(message_records) = &handles.message_records {
        match prepared.ctx.tool_metadata.request_id.as_ref() {
            Some(request_id) => match message_records
                .start_agent_run(AgentRunRecordStart {
                    request_id,
                    task_id: prepared.ctx.task_id.as_ref(),
                    agent_run_id: &agent_run_id,
                    agent_name: &prepared.ctx.agent_name,
                    kind: &record_kind,
                    system_prompt: &prepared.ctx.system_prompt,
                    initial_messages: &initial_messages,
                })
                .await
            {
                Ok(handle) => {
                    prepared.ctx.message_record = Some(handle);
                }
                Err(err) => {
                    let summary = err.to_string();
                    finish_agent_run_if_requested(
                        handles,
                        persistence_requested,
                        &agent_run_id,
                        Some(&summary),
                    )
                    .await;
                    return AgentRunResult {
                        terminal_result: None,
                        error: Some(summary),
                    };
                }
            },
            None => {
                tracing::warn!(
                    agent_run_id = agent_run_id.as_str(),
                    "message-record writer skipped run without request_id"
                );
            }
        }
    }

    let mut error: Option<String> = None;
    {
        let mut stream = run_query(&mut prepared.ctx, &mut initial_messages);
        while let Some(item) = stream.next().await {
            match item {
                Ok((event, _usage)) => {
                    if let Some(callback) = on_event {
                        callback(&event);
                    }
                }
                Err(err) => {
                    error = Some(err.to_string());
                    break;
                }
            }
        }
    }
    prepared
        .background_finalizer
        .finalize(&prepared.ctx, error.as_deref())
        .await;

    let terminal_result = prepared.ctx.terminal_result.clone();
    publish_agent_run_completed(
        handles,
        &prepared.ctx,
        run_started.elapsed().as_secs_f64() * 1000.0,
        error.as_deref(),
    );
    publish_os_resource_sampled(handles, &prepared.ctx);
    if let Some(message_record) = &prepared.ctx.message_record {
        if let Err(err) = message_record
            .finish(if error.is_some() {
                NodeFinishStatus::Failed
            } else {
                NodeFinishStatus::Completed
            })
            .await
        {
            tracing::warn!(error = %err, "agent-run message-record finish failed");
        }
    }
    finish_agent_run_if_requested(
        handles,
        persistence_requested,
        &agent_run_id,
        error.as_deref(),
    )
    .await;

    AgentRunResult {
        terminal_result,
        error,
    }
}
