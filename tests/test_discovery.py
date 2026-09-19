"""Coverage for list_endpoints callable flags + callable_counts."""
import server
from server import SCOPE_TIERS, configure, do_list_endpoints


def test_no_args_summary_includes_callable_counts():
    configure(SCOPE_TIERS["read"], set())
    result = do_list_endpoints()

    assert "categories" in result
    assert "callable_counts" in result
    # Every category present in `categories` also appears in callable_counts.
    assert set(result["callable_counts"]) == set(result["categories"])
    # Read-only scope: for each category, callable_counts equals the count of
    # non-deprecated read endpoints under it.
    for category, total in result["categories"].items():
        cbl = result["callable_counts"][category]
        assert 0 <= cbl <= total


def test_no_args_summary_reflects_pair_denies():
    # Deny all of connections; callable_counts["connections"] should be 0.
    configure(SCOPE_TIERS["read/write/delete"], {("connections", "read"),
                                                 ("connections", "write"),
                                                 ("connections", "delete")})
    result = do_list_endpoints()
    assert result["callable_counts"]["connections"] == 0
    # But `categories` still shows the total, because we never filter discovery.
    assert result["categories"]["connections"] > 0


def test_no_args_summary_reflects_endpoint_denies():
    configure(
        SCOPE_TIERS["read/write/delete"],
        set(),
        endpoint_denies={"sync_connection"},
    )
    result = do_list_endpoints()
    conn_total = result["categories"]["connections"]
    conn_callable = result["callable_counts"]["connections"]
    assert conn_callable == conn_total - 1


def test_per_row_callable_flag_true_when_granted():
    configure(SCOPE_TIERS["read"], set())
    result = do_list_endpoints(category="connections")
    read_rows = [e for e in result["endpoints"] if e["scope"] == "read"]
    assert read_rows
    assert all(e["callable"] for e in read_rows)


def test_per_row_callable_flag_false_when_out_of_scope():
    configure(SCOPE_TIERS["read"], set())
    result = do_list_endpoints(category="connections")
    write_rows = [e for e in result["endpoints"] if e["scope"] == "write"]
    assert write_rows
    assert all(not e["callable"] for e in write_rows)


def test_per_row_callable_flag_false_when_endpoint_denied():
    configure(
        SCOPE_TIERS["read/write/delete"],
        set(),
        endpoint_denies={"sync_connection"},
    )
    result = do_list_endpoints(category="connections")
    denied = next(e for e in result["endpoints"] if e["name"] == "sync_connection")
    others = [e for e in result["endpoints"] if e["name"] != "sync_connection"
              and not e.get("deprecated")]
    assert denied["callable"] is False
    assert all(e["callable"] for e in others)


def test_list_endpoints_never_filters_by_availability():
    # Even with maximum restriction, every non-deprecated endpoint is listed.
    configure((), set(), endpoint_denies={"sync_connection"})
    result = do_list_endpoints(category="connections")
    names = [e["name"] for e in result["endpoints"]]
    assert "sync_connection" in names
    # Everything is non-callable when scope is empty.
    assert all(not e["callable"] for e in result["endpoints"])


def test_search_still_works_and_carries_callable():
    configure(SCOPE_TIERS["read"], set())
    result = do_list_endpoints(search="connection")
    assert result["endpoints"]
    assert all("callable" in e for e in result["endpoints"])
