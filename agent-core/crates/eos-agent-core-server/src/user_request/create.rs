//! Create user-request orchestration.

use eos_types::{
    AgentRunApi, Message, RequestId, RequestStatus, SpawnAgentRequest, SpawnAgentTarget,
};

use crate::dto::{CreateUserRequestInput, CreateUserRequestOutput};
use crate::error::AgentCoreServerError;
use crate::service::AgentCoreService;
use crate::user_request::finalizer::finish_user_request_after_root_agent;

pub(crate) async fn create_user_request(
    service: &AgentCoreService,
    input: CreateUserRequestInput,
) -> Result<CreateUserRequestOutput, AgentCoreServerError> {
    let request_id = RequestId::new_v4();
    let binding = service
        .sandbox_gateway
        .provisioner()
        .prepare_for_run(
            &request_id,
            input.sandbox_id.as_ref().map(eos_types::SandboxId::as_str),
        )
        .await
        .map_err(|err| AgentCoreServerError::SandboxProvision(err.message))?;

    service
        .request_store
        .create_request(
            &request_id,
            &service.settings.workspace_root,
            Some(&binding.sandbox_id),
            &input.prompt,
        )
        .await?;

    let spawn = service
        .agent_run_service
        .spawn_agent(SpawnAgentRequest {
            agent_name: service.settings.root_agent_name.clone(),
            initial_messages: vec![Message::from_user_text(input.prompt)],
            target: SpawnAgentTarget::Root {
                request_id: request_id.clone(),
            },
            tool_use_id: None,
            sandbox_id: Some(binding.sandbox_id),
            workspace_root: service.settings.workspace_root.clone(),
            is_isolated_workspace_mode: false,
        })
        .await;

    let root_agent_run_id = match spawn {
        Ok(agent_run_id) => agent_run_id,
        Err(err) => {
            if let Err(finish_err) = service
                .request_store
                .finish_request(&request_id, RequestStatus::Failed)
                .await
            {
                tracing::warn!(
                    request_id = request_id.as_str(),
                    error = %finish_err,
                    "failed to mark request failed after root spawn failure"
                );
            }
            return Err(err.into());
        }
    };

    tokio::spawn(finish_user_request_after_root_agent(
        service.request_store.clone(),
        service.agent_run_service.clone(),
        request_id.clone(),
        root_agent_run_id,
    ));

    Ok(CreateUserRequestOutput { request_id })
}
