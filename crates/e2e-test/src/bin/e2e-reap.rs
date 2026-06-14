use anyhow::Result;

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let Some(first) = args.next() else {
        let Some(run_id) = std::env::var("EOS_E2E_RUN_ID")
            .ok()
            .filter(|run_id| !run_id.trim().is_empty())
        else {
            anyhow::bail!("pass --run-id <id>, set EOS_E2E_RUN_ID, or pass --all");
        };
        let count = e2e_test::container::reap_e2e_containers_for_run(&run_id)?;
        println!("removed {count} eos-e2e container(s) for run {run_id}");
        return Ok(());
    };
    match first.as_str() {
        "--all" => {
            let count = e2e_test::container::reap_e2e_containers()?;
            println!("removed {count} eos-e2e container(s)");
        }
        "--run-id" => {
            let run_id = args
                .next()
                .filter(|run_id| !run_id.trim().is_empty())
                .ok_or_else(|| anyhow::anyhow!("--run-id requires a value"))?;
            let count = e2e_test::container::reap_e2e_containers_for_run(&run_id)?;
            println!("removed {count} eos-e2e container(s) for run {run_id}");
        }
        other => anyhow::bail!("unknown argument {other}; use --run-id <id> or --all"),
    }
    Ok(())
}
