//! Query loop.

use std::collections::BTreeSet;
use std::pin::Pin;

use async_stream::try_stream;
use eos_llm_client::{ContentBlock, Message, MessageRole, UsageSnapshot};
use eos_tools::SystemNotification;
use eos_types::{JsonObject, ToolUseId};
use futures::{Stream, StreamExt};

use crate::notifications::{budget_figures, enqueue_notification_rules};
use crate::query::{build_query_run_request, QueryContext, QueryExitReason};
use crate::telemetry::{stamp_identity, StreamEvent};
use crate::tool_call::{dispatch_assistant_tools, ToolUseRequest};
use crate::EngineError;

/// Query-loop output stream.
pub type QueryStream<'a> = Pin<
    Box<dyn Stream<Item = Result<(StreamEvent, Option<UsageSnapshot>), EngineError>> + Send + 'a>,
>;

/// Whether the terminal non-submission ceiling has been reached.
#[must_use]
pub fn terminal_submission_failed(ctx: &QueryContext) -> bool {
    let ceiling = ctx.tool_call_limit.saturating_mul(3).saturating_add(1) / 2;
    ctx.tool_calls_used
        .saturating_add(ctx.text_only_no_terminal_turns)
        >= ceiling
}

fn terminal_not_submitted_message(ctx: &QueryContext) -> String {
    let (_used, limit, ceiling, _turns_remaining) = budget_figures(ctx);
    format!(
        "Agent stopped: terminal tool not submitted. tool_calls_used={}, text_only_no_terminal_turns={}, tool_call_limit={}, hard_ceiling={}",
        ctx.tool_calls_used, ctx.text_only_no_terminal_turns, limit, ceiling
    )
}

fn synthetic_tool_use_id() -> Result<ToolUseId, EngineError> {
    "terminal_not_submitted".parse().map_err(EngineError::from)
}

fn tool_uses_from_message(message: &Message) -> Vec<ToolUseRequest> {
    message
        .content
        .iter()
        .filter_map(|block| match block {
            ContentBlock::ToolUse {
                tool_use_id,
                name,
                input,
            } => Some(ToolUseRequest {
                tool_use_id: tool_use_id.clone(),
                name: name.clone(),
                input: input.clone(),
            }),
            _ => None,
        })
        .collect()
}

fn append_notifications(messages: &mut Vec<Message>, notifications: &[SystemNotification]) {
    if notifications.is_empty() {
        return;
    }
    messages.push(Message {
        role: MessageRole::User,
        content: notifications
            .iter()
            .map(|notification| ContentBlock::SystemNotification {
                text: notification.message.clone(),
            })
            .collect(),
    });
}

async fn collect_notifications(
    ctx: &mut QueryContext,
    messages: &[Message],
) -> Vec<SystemNotification> {
    let notifier = ctx.notifier.clone();
    enqueue_notification_rules(messages, ctx, &notifier).await;
    notifier.drain().await
}

fn notification_event(ctx: &QueryContext, notification: &SystemNotification) -> StreamEvent {
    StreamEvent::SystemNotification {
        agent_name: ctx.agent_name.clone(),
        agent_run_id: Some(ctx.agent_run_id.clone()),
        text: notification.message.clone(),
    }
}

fn tool_result_message(tool_results: Vec<ContentBlock>) -> Message {
    Message {
        role: MessageRole::User,
        content: tool_results,
    }
}

fn terminal_not_submitted_event(ctx: &mut QueryContext) -> Result<StreamEvent, EngineError> {
    ctx.set_exit_reason(QueryExitReason::TerminalNotSubmitted);
    Ok(StreamEvent::ToolExecutionCompleted {
        agent_name: ctx.agent_name.clone(),
        agent_run_id: Some(ctx.agent_run_id.clone()),
        tool_name: String::new(),
        output: terminal_not_submitted_message(ctx),
        is_error: true,
        tool_use_id: synthetic_tool_use_id()?,
        metadata: JsonObject::new(),
        is_terminal: false,
    })
}

/// Run the query loop.
#[must_use]
pub fn run_query<'a>(ctx: &'a mut QueryContext, messages: &'a mut Vec<Message>) -> QueryStream<'a> {
    Box::pin(try_stream! {
        loop {
            if terminal_submission_failed(ctx) {
                let notifications = collect_notifications(ctx, messages).await;
                for notification in &notifications {
                    yield (notification_event(ctx, notification), None);
                }
                append_notifications(messages, &notifications);
                yield (terminal_not_submitted_event(ctx)?, None);
                break;
            }

            let notifications = collect_notifications(ctx, messages).await;
            for notification in &notifications {
                yield (notification_event(ctx, notification), None);
            }
            append_notifications(messages, &notifications);

            let run_request = build_query_run_request(ctx, messages).await;
            if let Some(recorder) = &ctx.prompt_report {
                recorder
                    .record_llm_request(
                        run_request.prompt_report_seq,
                        &ctx.system_prompt,
                        &run_request.request.messages,
                        &run_request.request.tools,
                    )
                    .await?;
            }
            let source = ctx.event_source.clone().ok_or(EngineError::MissingEventSource)?;
            let mut stream = source.stream(&run_request.request).await?;
            let mut final_message: Option<Message> = None;
            let mut final_usage: Option<UsageSnapshot> = None;
            let mut streamed_tool_use_ids = BTreeSet::new();

            while let Some(item) = stream.next().await {
                let event = stamp_identity(item?, &ctx.agent_name, &ctx.agent_run_id);
                match &event {
                    StreamEvent::ToolUseDelta { tool_use_id, .. } => {
                        if streamed_tool_use_ids.insert(tool_use_id.clone()) {
                            ctx.record_tool_call();
                        }
                    }
                    StreamEvent::AssistantMessageComplete { payload, .. } => {
                        final_usage = Some(payload.usage);
                        final_message = Some(payload.message.clone());
                    }
                    _ => {}
                }
                let usage = match &event {
                    StreamEvent::AssistantMessageComplete { payload, .. } => Some(payload.usage),
                    _ => None,
                };
                yield (event, usage);
            }

            let message = match final_message {
                Some(message) => message,
                None => Err(EngineError::Internal(
                    "provider stream ended without assistant completion".to_owned(),
                ))?,
            };
            let usage = final_usage.unwrap_or_default();
            if let Some(recorder) = &ctx.prompt_report {
                recorder
                    .record_assistant(run_request.prompt_report_seq, &message, usage)
                    .await?;
            }
            let tool_uses = tool_uses_from_message(&message);
            for call in &tool_uses {
                if !streamed_tool_use_ids.contains(&call.tool_use_id) {
                    ctx.record_tool_call();
                }
            }

            messages.push(message.clone());
            if tool_uses.is_empty() {
                ctx.record_text_only_turn();
                if terminal_submission_failed(ctx) {
                    let notifications = collect_notifications(ctx, messages).await;
                    for notification in &notifications {
                        yield (notification_event(ctx, notification), None);
                    }
                    append_notifications(messages, &notifications);
                    yield (terminal_not_submitted_event(ctx)?, None);
                    break;
                }
                continue;
            }

            let outcome = dispatch_assistant_tools(ctx, &tool_uses, messages).await?;
            for event in outcome.events {
                let stamped = stamp_identity(event, &ctx.agent_name, &ctx.agent_run_id);
                yield (stamped, None);
            }
            if let Some(recorder) = &ctx.prompt_report {
                recorder
                    .record_tool_results(run_request.prompt_report_seq, &outcome.tool_results)
                    .await?;
            }
            messages.push(tool_result_message(outcome.tool_results));

            if outcome
                .terminal_result
                .as_ref()
                .is_some_and(|result| result.is_terminal)
            {
                ctx.set_exit_reason(QueryExitReason::ToolStop);
                let notifications = collect_notifications(ctx, messages).await;
                for notification in &notifications {
                    yield (notification_event(ctx, notification), None);
                }
                append_notifications(messages, &notifications);
                break;
            }
        }
    })
}
