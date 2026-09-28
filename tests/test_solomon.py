"""M1 original Solomon input coverage and malformed row diagnostics."""

from pathlib import Path

import pytest

from vrptw import read_solomon


DATA = Path(__file__).resolve().parents[1] / "data"
INSTANCES = sorted(DATA.glob("*.txt"))


def test_dataset_has_all_56_original_instances():
    assert len(INSTANCES) == 56


@pytest.mark.parametrize("path", INSTANCES, ids=lambda path: path.stem)
def test_all_original_instances_parse(path):
    instance = read_solomon(path)
    assert instance.name == path.stem
    assert instance.customer_count == 100
    assert instance.vehicle_count > 0
    assert instance.capacity > 0
    assert instance.distance[0][0] == 0
    assert all(instance.distance[i][j] == instance.distance[j][i]
               for i in range(101) for j in range(101))


def test_c101_fields_and_rounding():
    instance = read_solomon(DATA / "C101.txt")
    assert (instance.vehicle_count, instance.capacity, instance.total_demand) == (25, 200, 1810)
    assert (instance.customers[1].demand, instance.customers[1].ready,
            instance.customers[1].due, instance.customers[1].service) == (10, 912_000, 967_000, 90_000)
    assert instance.distance[0][1] == 18_682
    assert instance.distance[0][3] == 16_125


def _write_instance(tmp_path, row):
    path = tmp_path / "BAD.txt"
    path.write_text("BAD\nVEHICLE\nNUMBER CAPACITY\n2 5\nCUSTOMER\n"
                    "CUST NO. XCOORD. YCOORD. DEMAND READY TIME DUE DATE SERVICE TIME\n"
                    "0 0 0 0 0 100 0\n" + row + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("row, message", [
    ("1 1 0 1 0 100", "seven integer"),
    ("1 1 0 -1 0 100 0", "negative"),
    ("1 1 0 1 101 100 0", "ready exceeds due"),
    ("0 1 0 1 0 100 0", "contiguous"),
    ("1 1 0 1 bad 100 0", "invalid literal"),
])
def test_rejects_bad_customer_rows(tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        read_solomon(_write_instance(tmp_path, row))


def test_rejects_converted_vrplib():
    with pytest.raises(ValueError, match="original Solomon"):
        read_solomon(DATA / "data_vrp" / "C101.vrp")


@pytest.mark.parametrize("text, message", [
    ("BAD\nCUSTOMER\n0 0 0 0 0 10 0\n1 1 0 1 0 10 0\n", "missing VEHICLE"),
    ("BAD\nVEHICLE\nNUMBER CAPACITY\n2\nCUSTOMER\n"
     "0 0 0 0 0 10 0\n1 1 0 1 0 10 0\n", "vehicle count and capacity"),
])
def test_rejects_missing_sections_and_fleet_fields(tmp_path, text, message):
    path = tmp_path / "BAD.txt"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_solomon(path)
