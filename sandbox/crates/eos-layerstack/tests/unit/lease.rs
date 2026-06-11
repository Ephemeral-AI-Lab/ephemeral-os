use super::*;

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn acquire_rejects_empty_owner_without_panicking() -> TestResult {
    let manifest = Manifest::new(0, Vec::new(), 1)?;
    let Err(err) = LeaseRegistry::new().acquire(manifest, "") else {
        return Err(std::io::Error::other("empty owner id was accepted").into());
    };
    assert!(matches!(err, LayerStackError::InvalidLeaseOwner(_)));
    Ok(())
}
