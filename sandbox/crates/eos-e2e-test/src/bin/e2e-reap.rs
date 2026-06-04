use anyhow::Result;

fn main() -> Result<()> {
    let count = eos_e2e_test::container::reap_e2e_containers()?;
    println!("removed {count} eos-e2e container(s)");
    Ok(())
}
