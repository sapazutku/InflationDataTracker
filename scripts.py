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

# BIS CPI serisi: M.<ülke kodları>.771
# M = aylık gözlem; 771 = yıllık yüzde değişim (yıllık enflasyon oranı).
BIS_CPI_REGIONS = [
    "US",
    "TR",
    "GB",
    "ES",
    "JP",
    "CN",
    "DE",
    "FR",
    "RU",
    "ZA",
    "AR",
    "BR",
    "IN",
    "AU",
    "MA",
]

# BIS politika faizi serisi: M.<ülke kodları>
# XM = Euro Bölgesi. İspanya, Almanya ve Fransa için ortak ECB serisi kullanılır.
BIS_POLICY_REGIONS = [
    "US",
    "TR",
    "GB",
    "JP",
    "CN",
    "RU",
    "XM",
    "ZA",
    "BR",
    "IN",
    "AU",
    "MA",
]
# Arjantin'in BIS politika faizi Haziran 2025'te durdu; eski bir rakam güncelmiş
# gibi görünmesin diye alınmıyor.

REGION_META = {
    "US": {
        "region": "US",
        "en": "United States",
        "tr": "ABD",
    },
    "TR": {
        "region": "TR",
        "en": "Türkiye",
        "tr": "Türkiye",
    },
    "GB": {
        "region": "GB",
        "en": "United Kingdom",
        "tr": "Birleşik Krallık",
    },
    "ES": {
        "region": "ES",
        "en": "Spain",
        "tr": "İspanya",
    },
    "JP": {
        "region": "JP",
        "en": "Japan",
        "tr": "Japonya",
    },
    "CN": {
        "region": "CN",
        "en": "China",
        "tr": "Çin",
    },
    "DE": {
        "region": "DE",
        "en": "Germany",
        "tr": "Almanya",
    },
    "FR": {
        "region": "FR",
        "en": "France",
        "tr": "Fransa",
    },
    "RU": {
        "region": "RU",
        "en": "Russia",
        "tr": "Rusya",
    },
    "ZA": {
        "region": "ZA",
        "en": "South Africa",
        "tr": "Güney Afrika",
    },
    "AR": {
        "region": "AR",
        "en": "Argentina",
        "tr": "Arjantin",
    },
    "BR": {
        "region": "BR",
        "en": "Brazil",
        "tr": "Brezilya",
    },
    "IN": {
        "region": "IN",
        "en": "India",
        "tr": "Hindistan",
    },
    "AU": {
        "region": "AU",
        "en": "Australia",
        "tr": "Avustralya",
    },
    "MA": {
        "region": "MA",
        "en": "Morocco",
        "tr": "Fas",
    },
    "XM": {
        "region": "EU",
        "en": "Euro area",
        "tr": "Euro Bölgesi",
    },
}


def make_bis_url(dataflow, regions, unit=None):
    """Bir BIS veri akışı için birden fazla ülkeyi tek istekte sorgular."""
    key = "M." + "+".join(regions)

    if unit:
        key += f".{unit}"

    return (
        f"{BIS_BASE}/{dataflow}/1.0/{key}"
        "?format=csv&lastNObservations=12"
    )


BIS_CPI_URL = make_bis_url(
    dataflow="WS_LONG_CPI",
    regions=BIS_CPI_REGIONS,
    unit="771",
)

BIS_POLICY_URL = make_bis_url(
    dataflow="WS_CBPOL",
    regions=BIS_POLICY_REGIONS,
)


def get_text(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "inflation-tracker/1.0"},
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8-sig")


def normalize_month(value):
    """YYYY-MM-DD veya YYYY-MM değerini YYYY-MM'e indirger."""
    return str(value).strip()[:7]


def get_region_from_row(row):
    """
    BIS CSV satırından ülke kodunu bulur.
    Çoklu seri yanıtlarında genellikle REF_AREA sütunu bulunur.
    """
    region = (
        row.get("ref_area")
        or row.get("reference_area")
        or row.get("geo")
        or row.get("country")
    )

    if region:
        return str(region).strip().upper()

    # CSV'de REF_AREA sütunu yoksa seri anahtarından çıkarmayı dener.
    series_key = row.get("series_key") or row.get("key")
    if series_key:
        parts = str(series_key).split(".")
        if len(parts) >= 2:
            return parts[1].strip().upper()

    return None


def fetch_bis_csv_by_region(url):
    """
    BIS'in çok serili CSV yanıtını ülke koduna göre ayırır.

    Sonuç:
    {
        "US": [{"month": "2026-01", "rate": 2.4}, ...],
        "TR": [...],
    }
    """
    text = get_text(url)
    reader = csv.DictReader(io.StringIO(text))

    points_by_region = {}

    for row in reader:
        normalized = {
            str(key).strip().lower(): value
            for key, value in row.items()
            if key
        }

        region = get_region_from_row(normalized)
        period = (
            normalized.get("time_period")
            or normalized.get("time")
            or normalized.get("period")
        )
        value = (
            normalized.get("obs_value")
            or normalized.get("value")
        )

        if not region or not period or value in (None, "", "NaN", "nan"):
            continue

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue

        month = normalize_month(period)
        points_by_region.setdefault(region, {})[month] = numeric_value

    result = {
        region: [
            {"month": month, "rate": rate}
            for month, rate in sorted(month_values.items())
        ]
        for region, month_values in points_by_region.items()
    }

    if not result:
        raise ValueError(f"BIS returned no observations: {url}")

    return result


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
    time_by_index = {
        index: period
        for period, index in time_categories.items()
    }

    # JSON-stat gözlemleri düz bir dizide tutulur. Bu stride, düz dizindeki
    # her gözlem için zaman boyutundaki karşılık gelen indeksi bulur.
    stride = 1
    for size in sizes[time_position + 1:]:
        stride *= size

    observations = (
        values
        if isinstance(values, dict)
        else {
            str(index): value
            for index, value in enumerate(values)
        }
    )

    points_by_month = {}

    for flat_index, value in observations.items():
        if value is None:
            continue

        time_index = (
            int(flat_index) // stride
        ) % sizes[time_position]

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


def make_series_record(region_code, kind, points):
    meta = REGION_META[region_code]
    is_inflation = kind == "inflation"

    if is_inflation:
        series_id = f"{region_code.lower()}.bis.cpi.annual"
        name_en = f"{meta['en']} CPI"
        name_tr = f"{meta['tr']} TÜFE"
    else:
        series_id = f"{region_code.lower()}.bis.policy"
        name_en = f"{meta['en']} policy rate"
        name_tr = f"{meta['tr']} politika faizi"

    return {
        "id": series_id,
        "kind": kind,
        "period": "monthly",
        "region": meta["region"],
        "provider": "BIS",
        "name": {
            "en": name_en,
            "tr": name_tr,
        },
        "points": points,
    }


def main():
    result_series = []

    print("Fetching BIS inflation group")
    cpi_by_region = fetch_bis_csv_by_region(BIS_CPI_URL)

    for region_code in BIS_CPI_REGIONS:
        points = cpi_by_region.get(region_code, [])

        if not points:
            print(f"Warning: BIS CPI has no observations for {region_code}")
            continue

        result_series.append(
            make_series_record(
                region_code=region_code,
                kind="inflation",
                points=points,
            )
        )

    print("Fetching Eurostat EU HICP")
    eu_hicp_points = fetch_eurostat_json_stat(EUROSTAT_URL)

    result_series.append({
        "id": "eu.eurostat.hicp.annual",
        "kind": "inflation",
        "period": "monthly",
        "region": "EU",
        "provider": "Eurostat",
        "name": {
            "en": "EU HICP",
            "tr": "AB HICP",
        },
        "points": eu_hicp_points,
    })

    print("Fetching BIS policy-rate group")
    policy_by_region = fetch_bis_csv_by_region(BIS_POLICY_URL)

    for region_code in BIS_POLICY_REGIONS:
        points = policy_by_region.get(region_code, [])

        if not points:
            print(f"Warning: BIS policy rate has no observations for {region_code}")
            continue

        result_series.append(
            make_series_record(
                region_code=region_code,
                kind="interest",
                points=points,
            )
        )

    output = {
        "version": 1,
        "note": (
            "Rates are percentages. Inflation points are monthly "
            "year-over-year percentage changes."
        ),
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