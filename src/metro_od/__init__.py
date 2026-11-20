"""Metro de Santiago Origin-Destination (2019 vs 2023) analysis package."""

from .catalog import DATASETS, DatasetSpec, get_dataset_catalog
from .clean import (
    COMUNA_CODES_2023,
    COMMUNE_NORMALIZATION,
    DAY_TYPE_CODES_2023,
    PERIODO_CODES_2023,
    TRANSPORT_MODE_CODES_2023,
    build_clean_etapas_query,
    build_clean_pipe_matrix_2019_query,
    build_clean_viajes_query,
    export_cleaned_parquet,
    inspect_xlsb_matrices,
    load_clean_xlsb_matrix_2023,
)
from .describe import compare_schemas, describe_all_datasets, describe_csv_sample

__all__ = [
    "DATASETS",
    "DatasetSpec",
    "get_dataset_catalog",
    "describe_all_datasets",
    "describe_csv_sample",
    "compare_schemas",
    "COMMUNE_NORMALIZATION",
    "COMUNA_CODES_2023",
    "PERIODO_CODES_2023",
    "TRANSPORT_MODE_CODES_2023",
    "DAY_TYPE_CODES_2023",
    "build_clean_etapas_query",
    "build_clean_viajes_query",
    "build_clean_pipe_matrix_2019_query",
    "load_clean_xlsb_matrix_2023",
    "inspect_xlsb_matrices",
    "export_cleaned_parquet",
]
