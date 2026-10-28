import csv
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


BIS_BASE = "https://stats.bis.org/api/v2/data/dataflow/BIS"
EUROSTAT_URL = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
    "prc_hicp_minr?format=JSON&lang=EN&freq=M&unit=RCH_A"
    "&coicop18=TOTAL&geo=EU27_2020&lastTimePeriod=12"
)

SERIES = [
    {
        "id": "us.bis.cpi.annual",
        "kind": "inflation",
        "period": "annual",
        "region": "US",
        "provider": "BIS",
        "name": {"en": "US CPI", "tr": "ABD TÜFE"},
        "url": f"{BIS_BASE}/WS_LONG_CPI/1.0/M.US.771?format=csv&lastNObservations=12",
    },
    {
        "id": "tr.bis.cpi.annual",
        "kind": "inflation",
        "period": "annual",
        "region": "TR",
        "provider": "BIS",
        "name": {"en": "Türkiye CPI", "tr": "Türkiye TÜFE"},
        "url": f"{BIS_BASE}/WS_LONG_CPI/1.0/M.TR.771?format=csv&lastNObservations=12",
    },
    {
        "id": "eu.eurostat.hicp.annual",
        "kind": "inflation",
        "period": "annual",
        "region": "EU",
        "provider": "Eurostat",
        "name": {"en": "EU HICP", "tr": "AB HICP"},
        "url": EUROSTAT_URL,
        "format": "eurostat-json-stat",
    },
    {
        "id": "us.bis.policy",
        "kind": "interest",
        "period": "annual",
        "region": "US",
        "provider": "BIS",
        "name": {"en": "US policy rate", "tr": "ABD politika faizi"},
        "url": f"{BIS_BASE}/WS_CBPOL/1.0/M.US?format=csv&lastNObservations=12",
    },
    {
        "id": "tr.bis.policy",
        "kind": "interest",
        "period": "annual",
        "region": "TR",
        "provider": "BIS",
        "name": {"en": "CBRT policy rate", "tr": "TCMB politika faizi"},
        "url": f"{BIS_BASE}/WS_CBPOL/1.0/M.TR?format=csv&lastNObservations=12",
    },
    {
        "id": "eu.bis.policy",
        "kind": "interest",
        "period": "annual",
        "region": "EU",
        "provider": "BIS",
        "name": {"en": "Euro area policy rate", "tr": "Euro Bölgesi politika faizi"},
        "url": f"{BIS_BASE}/WS_CBPOL/1.0/M.XM?format=csv&lastNObservations=12",
    },
]


def get_text(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "inflation-tracker/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8-sig")


def normalize_month(value):
    # BIS may return YYYY-MM or a daily date such as YYYY-MM-DD.
    return str(value)[:7]


def fetch_bis_csv(url):
    text = get_text(url)
    reader = csv.DictReader(io.StringIO(text))

    points_by_month = {}
    for row in reader:
        normalized = {str(k).strip().lower(): v for k, v in row.items() if k}

        period = (
            normalized.get("time_period")
            or normalized.get("time")
            or normalized.get("period")
        )
        value = normalized.get("obs_value") or normalized.get("value")

        if not period or value in (None, "", "NaN", "nan"):
            continue

        month = normalize_month(period)
        points_by_month[month] = float(value)

    points = [
        {"month": month, "rate": rate}
        for month, rate in sorted(points_by_month.items())
    ]
    if not points:
        raise ValueError(f"BIS returned no observations: {url}")
    return points


def fetch_eurostat_json_stat(url):
    dataset = json.loads(get_text(url))
    dimension_ids = dataset["id"]
    sizes = dataset["size"]
    dimensions = dataset["dimension"]
    values = dataset["value"]

    time_dimension_id = next(
        (
            dimension_id
            for dimension_id in dimension_ids
            if dimension_id.lower() in ("time", "time_period")
        ),
        None,
    )
    if time_dimension_id is None:
        raise ValueError("Eurostat response has no time dimension")

    time_position = dimension_ids.index(time_dimension_id)
    time_categories = dimensions[time_dimension_id]["category"]["index"]
    time_by_index = {index: period for period, index in time_categories.items()}

    # JSON-stat stores observations in a flattened array. These strides map
    # each flattened index back to its time-period index.
    stride = 1
    for size in sizes[time_position + 1:]:
        stride *= size

    observations = values if isinstance(values, dict) else {
        str(index): value for index, value in enumerate(values)
    }

    points_by_month = {}
    for flat_index, value in observations.items():
        if value is None:
            continue

        time_index = (int(flat_index) // stride) % sizes[time_position]
        period = time_by_index.get(time_index)
        if period:
            points_by_month[normalize_month(period)] = float(value)

    points = [
        {"month": month, "rate": rate}
        for month, rate in sorted(points_by_month.items())
    ]
    if not points:
        raise ValueError("Eurostat returned no observations")
    return points


def main():
    result_series = []

    for series in SERIES:
        print(f"Fetching {series['id']}")

        if series.get("format") == "eurostat-json-stat":
            points = fetch_eurostat_json_stat(series["url"])
        else:
            points = fetch_bis_csv(series["url"])

        result_series.append({
            "id": series["id"],
            "kind": series["kind"],
            "period": series["period"],
            "region": series["region"],
            "provider": series["provider"],
            "name": series["name"],
            "points": points,
        })

    output = {
        "version": 1,
        "note": "Rates are percentages. Inflation is the annual rate of change.",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m"),
        "series": result_series,
    }

    output_path = Path("data.json")
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()