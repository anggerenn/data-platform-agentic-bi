import path_setup
from prefect import flow
from dlt_ingestion import run_dlt
from dbt_transformation import run_dbt
from vanna_retrain import validate_schema, retrain_vanna_schema


@flow(name="analytics_pipeline")
def analytics_pipeline():
    run_dlt()
    run_dbt()
    validate_schema()
    retrain_vanna_schema()


if __name__ == "__main__":
    analytics_pipeline()