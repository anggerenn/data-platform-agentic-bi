import path_setup
from prefect import flow
from evidence_build import refresh_evidence_sources

@flow(name="evidence_pipeline")
def evidence_pipeline():
    refresh_evidence_sources()

if __name__ == "__main__":
    evidence_pipeline()