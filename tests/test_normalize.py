"""Pin the suffix-stripping behavior of normalize_district_name.

This is the only pure logic in the system worth unit-testing for a take-home
demo — everything else is either DB plumbing or LLM-mediated.
"""
from app.services.normalize import normalize_district_name


def test_basic_public_schools():
    assert normalize_district_name("Brookhaven Public Schools") == "brookhaven"


def test_abbreviated_public_schools():
    assert normalize_district_name("Brookhaven Public Sch.") == "brookhaven"


def test_county_schools():
    assert normalize_district_name("Hartwell County Schools") == "hartwell"


def test_isd():
    assert normalize_district_name("Rio Verde ISD") == "rio verde"


def test_numbered_district():
    assert normalize_district_name("Maple Ridge School District 142") == "maple ridge"


def test_sd_pound():
    # "SD #74 (Mission)" → parenthetical wins over leading code
    assert normalize_district_name("SD #74 (Mission)") == "mission"


def test_unified():
    assert normalize_district_name("Larkspur Unified School District") == "larkspur"


def test_already_clean():
    assert normalize_district_name("brookhaven") == "brookhaven"


def test_empty():
    assert normalize_district_name("") == ""
    assert normalize_district_name("   ") == ""


def test_two_districts_distinguishable():
    # Two distinct districts should normalize differently
    a = normalize_district_name("Salt Lake County School District")
    b = normalize_district_name("Salt Lake City School District")
    assert a != b
    assert "salt lake" in a
    assert "salt lake" in b
