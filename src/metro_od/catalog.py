"""Data catalog and schema specifications for the 2019, 2023, and GTFS datasets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


@dataclass(frozen=True)
class DatasetSpec:
    """Metadata specification for a raw dataset in the repository."""

    key: str
    year: str
    category: str  # "etapas", "viajes", "od_matrix", "gtfs"
    relative_path: str
    delimiter: str = ";"
    format: str = "csv"  # "csv", "xlsb", "gtfs_csv"
    schema_doc_path: Optional[str] = None
    null_values: List[str] = field(default_factory=lambda: ["", "-", "NULL", "null"])
    known_quirks: List[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return DATA_DIR / self.relative_path

    @property
    def schema_path(self) -> Optional[Path]:
        return (DATA_DIR / self.schema_doc_path) if self.schema_doc_path else None

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 2)


DATASETS: Dict[str, DatasetSpec] = {
    # --- 2019 Raw Validation Stages & Trips ---
    "etapas_2019": DatasetSpec(
        key="etapas_2019",
        year="2019",
        category="etapas",
        relative_path="2019/etapas_19/etapas20190408_transparencia.csv",
        delimiter=";",
        schema_doc_path="2019/etapas_19.txt",
        known_quirks=[
            "Uses '-' for missing values in numeric and string columns.",
            "Commune names are truncated (e.g., 'LA FLORI').",
            "Coordinates (x_subida, y_subida, x_bajada, y_bajada) are in UTM Zone 19S (meters).",
        ],
    ),
    "viajes_2019": DatasetSpec(
        key="viajes_2019",
        year="2019",
        category="viajes",
        relative_path="2019/viajes_19/viajes20190408_transparencia.csv",
        delimiter=";",
        schema_doc_path="2019/viajes-19.txt",
        known_quirks=[
            "107 columns; uses ';' delimiter and '-' as null placeholder.",
            "Categorical fields (tipodia, comunasubida, periodosubida, tipotransporte_1era) are text strings.",
            "Commune names truncated to 8 characters in several rows (e.g., 'LA FLORI').",
        ],
    ),
    # --- 2019 Aggregated OD Matrices (April 1-5, 2019) ---
    "od_comunas_2019": DatasetSpec(
        key="od_comunas_2019",
        year="2019",
        category="od_matrix",
        relative_path="2019/MatrizOD_Subidas_Bajadas_2019.04/tablasWeb2019Abril_01_05.MatrizODComunasMediaHora.csv",
        delimiter=";",
        known_quirks=["Half-hourly average workday trips between communes."],
    ),
    "od_zonas_2019": DatasetSpec(
        key="od_zonas_2019",
        year="2019",
        category="od_matrix",
        relative_path="2019/MatrizOD_Subidas_Bajadas_2019.04/tablasWeb2019Abril_01_05.MatrizODZonasMediaHora.csv",
        delimiter=";",
        known_quirks=["Half-hourly average workday trips between Zona 777 traffic zones."],
    ),
    "subidas_parada_2019": DatasetSpec(
        key="subidas_parada_2019",
        year="2019",
        category="od_matrix",
        relative_path="2019/MatrizOD_Subidas_Bajadas_2019.04/tablasWeb2019Abril_01_05.SubidasPorParadaMediaHora.csv",
        delimiter=";",
        known_quirks=[
            "Mixed delimiter in rows: header is 'ParadaSubida;MediaHora;SubidasPromedio' but col 1 and 2 are joined by '|' (e.g. '-|00:00:00;0.2').",
        ],
    ),
    "bajadas_zona_2019": DatasetSpec(
        key="bajadas_zona_2019",
        year="2019",
        category="od_matrix",
        relative_path="2019/MatrizOD_Subidas_Bajadas_2019.04/tablasWeb2019Abril_01_05.BajadasPorZonasMediaHora.csv",
        delimiter=";",
        known_quirks=[
            "Mixed delimiter in rows: header is 'Zona777bajada;MediaHora;BajadasPromedio' but col 1 and 2 are joined by '|' (e.g. '0|00:00:00;0.2938').",
        ],
    ),
    # --- 2023 Raw Validation Stages & Trips (April 17-23, 2023) ---
    "etapas_2023": DatasetSpec(
        key="etapas_2023",
        year="2023",
        category="etapas",
        relative_path="2023/etapas_042023_17al23.csv",
        delimiter=";",
        schema_doc_path="2023/Estructura Tabla Etapas 0423.txt",
        known_quirks=[
            "9.41 GB file with 32 columns (adds periodosubida, periodobajada, tiempoiniexpedicion).",
            "Mixed Coordinate Reference System (CRS): BUS rows use UTM meters (e.g. 342041.0, 6296903.0), while METRO rows store WGS84 (lat, lon) in (x_subida, y_subida) (e.g. -33.5102, -70.7562).",
            "Zone IDs are formatted as quoted float strings (e.g. '\"77.0\"', '\"818.0\"').",
            "Malformed sentinel dates in tiempoiniexpedicion (e.g. '2010-01-00 -').",
        ],
    ),
    "viajes_2023": DatasetSpec(
        key="viajes_2023",
        year="2023",
        category="viajes",
        relative_path="2023/viajes_042023_17al23.csv",
        delimiter=",",
        schema_doc_path="2023/Estructura Tabla Viajes 0423.txt",
        known_quirks=[
            "7.49 GB file using ',' delimiter instead of ';'.",
            "Trailing comma on every row creates an extra 'Unnamed: 100' column.",
            "Column names changed vs 2019 (e.g. comunasubida -> comuna_inicio_viaje, tviaje_min -> tviaje2, tviaje is deprecated/nullified with '-').",
            "Categorical fields (tipodia, comuna_inicio_viaje, periodo_inicio_viaje, tipo_transporte_1) are integer IDs instead of text labels.",
            "Includes encrypted id_tarjeta and id_viaje correlative not present in 2019 public table.",
        ],
    ),
    # --- 2023 Aggregated Subidas & Bajadas (.xlsb) ---
    "matriz_subidas_2023_xlsb": DatasetSpec(
        key="matriz_subidas_2023_xlsb",
        year="2023",
        category="od_matrix",
        relative_path="2023/Tablas de Subidas y Bajadas/2023.04 Matriz_sub_SS_MH.xlsb",
        delimiter="",
        format="xlsb",
        known_quirks=["Excel Binary Workbook (.xlsb); requires fastexcel (calamine) or pyxlsb to read."],
    ),
    "matriz_bajadas_2023_xlsb": DatasetSpec(
        key="matriz_bajadas_2023_xlsb",
        year="2023",
        category="od_matrix",
        relative_path="2023/Tablas de Subidas y Bajadas/2023.04 Matriz_baj_SS_MH.xlsb",
        delimiter="",
        format="xlsb",
        known_quirks=["Excel Binary Workbook (.xlsb); requires fastexcel (calamine) or pyxlsb to read."],
    ),
}


def get_dataset_catalog() -> List[Dict[str, object]]:
    """Return a list of dictionaries summarizing all registered datasets and GTFS files."""
    rows: List[Dict[str, object]] = []
    for spec in DATASETS.values():
        rows.append(
            {
                "key": spec.key,
                "year": spec.year,
                "category": spec.category,
                "format": spec.format,
                "delimiter": spec.delimiter or "N/A",
                "size_mb": spec.size_mb,
                "relative_path": spec.relative_path,
                "exists": spec.path.exists(),
                "quirks_count": len(spec.known_quirks),
            }
        )
    gtfs_dir = DATA_DIR / "GTFS"
    if gtfs_dir.exists():
        for txt_path in sorted(gtfs_dir.glob("*.txt")):
            rows.append(
                {
                    "key": f"gtfs_{txt_path.stem}",
                    "year": "GTFS",
                    "category": "gtfs",
                    "format": "gtfs_csv",
                    "delimiter": ",",
                    "size_mb": round(txt_path.stat().st_size / (1024 * 1024), 2),
                    "relative_path": f"GTFS/{txt_path.name}",
                    "exists": True,
                    "quirks_count": 0,
                }
            )
    return rows
