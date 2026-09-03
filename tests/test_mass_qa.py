from __future__ import annotations

import sqlite3
import unittest

from scripts.run_mass_qa import drop_transient_error_rows


class MassQaRetryTests(unittest.TestCase):
    def test_retry_drops_only_transport_errors(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "CREATE TABLE result(run_id TEXT, qid TEXT, verdict TEXT, status TEXT, note TEXT)"
        )
        connection.executemany(
            "INSERT INTO result VALUES(?,?,?,?,?)",
            (
                ("target", "http", "error", None, "http_429:rate_limited"),
                ("target", "provider", "error", "error", "server_error:hcx_tool_selection_failed"),
                ("target", "pass", "pass", "answered", "status=answered"),
                ("other", "http", "error", None, "http_429:rate_limited"),
            ),
        )

        dropped = drop_transient_error_rows(connection, "target")

        self.assertEqual(dropped, 1)
        self.assertEqual(
            connection.execute(
                "SELECT run_id,qid,verdict,status FROM result ORDER BY run_id,qid"
            ).fetchall(),
            [
                ("other", "http", "error", None),
                ("target", "pass", "pass", "answered"),
                ("target", "provider", "error", "error"),
            ],
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
