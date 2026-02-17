from prefect import flow
from evidence_build import build_evidence

@flow(name="evidence_pipeline")
def evidence_pipeline():
    build_evidence()

if __name__ == "__main__":
    evidence_pipeline()