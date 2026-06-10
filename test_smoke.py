import os
import tempfile
import unittest

import app as portal


class DriverPortalSmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        portal.app.config.update(
            TESTING=True,
            DATABASE_PATH=os.path.join(self.tmp.name, "test.sqlite3"),
            SECRET_KEY="test-secret",
        )
        portal.init_db()
        self.client = portal.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, username, password):
        return self.client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=True,
        )

    def test_admin_seed_and_login(self):
        response = self.login("admin", "admin123")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin", response.data)
        self.assertIn(b"Driver flows", response.data)
        self.assertIn(b"Dispatcher desk profiles", response.data)
        split = portal.query_one(
            "SELECT form_schema FROM request_types WHERE label = 'Split has been cleared'"
        )
        self.assertIsNotNone(split)
        self.assertIn('"receipt_mode": "none"', split["form_schema"])
        cip = portal.query_one("SELECT id FROM request_types WHERE label = 'CIPs'")
        editor = self.client.get(f"/admin/request-types/{cip['id']}/edit")
        self.assertIn(b"Edit driver flow", editor.data)
        self.assertIn(b"Add CIP Start Of Day", editor.data)

        self.client.post(
            "/admin/groups/create",
            data={"name": "Temp Queue", "sort_order": "999"},
        )
        group = portal.query_one("SELECT id FROM dispatcher_groups WHERE name = 'Temp Queue'")
        self.client.post(f"/admin/groups/{group['id']}/delete")
        self.assertIsNone(portal.query_one("SELECT id FROM dispatcher_groups WHERE name = 'Temp Queue'"))

        self.client.post(
            "/admin/depots/create",
            data={"name": "Temp Depot", "dispatcher_group_id": "1", "sort_order": "999"},
        )
        depot = portal.query_one("SELECT id FROM depots WHERE name = 'Temp Depot'")
        self.client.post(f"/admin/depots/{depot['id']}/delete")
        self.assertIsNone(portal.query_one("SELECT id FROM depots WHERE name = 'Temp Depot'"))

    def test_driver_request_lifecycle(self):
        self.login("admin", "admin123")
        self.client.post(
            "/admin/users/create",
            data={
                "username": "driver1",
                "display_name": "Driver One",
                "password": "pass123",
                "role": "driver",
            },
        )
        self.client.post(
            "/admin/users/create",
            data={
                "username": "dispatch1",
                "display_name": "Dispatch One",
                "password": "pass123",
                "role": "dispatch",
            },
        )
        self.client.get("/logout")

        response = self.login("driver1", "pass123")
        self.assertIn(b"Shift details", response.data)
        self.assertRegex(response.data.decode(), r"<option[^>]*>\s*Kauri\s*</option>")
        self.assertNotIn(b"Kauri \xc2\xb7", response.data)

        depot = portal.query_one("SELECT id FROM depots WHERE name = 'Kauri'")
        driver_home = self.client.post(
            "/driver/profile",
            data={
                "driver_name": "Driver One",
                "truck_number": "T42",
                "depot_id": depot["id"],
            },
            follow_redirects=True,
        )
        self.assertIn(b"choice-tile", driver_home.data)
        self.assertIn(b"request-back-button", driver_home.data)
        self.assertIn(b'id="request-buttons"', driver_home.data)
        req_type = portal.query_one("SELECT id FROM request_types WHERE label = 'CIPs'")
        self.client.post(
            "/driver/request",
            data={
                "request_type_id": req_type["id"],
                "detail_cip_action": "Add CIP Start Of Day",
                "note": "Testing",
            },
            follow_redirects=True,
        )
        request_row = portal.query_one("SELECT id, status, details_json FROM driver_requests")
        self.assertEqual(request_row["status"], "new")
        self.assertIn("Add CIP Start Of Day", request_row["details_json"])
        milk_type = portal.query_one("SELECT id FROM request_types WHERE label = 'Milk Left Behind'")
        self.client.post(
            "/driver/request",
            data={
                "request_type_id": milk_type["id"],
                "detail_supply_number": "SUP-12345",
                "detail_milk_volume": "0-500L",
                "detail_milk_stirred": "Yes",
                "note": "Gate code is 1234",
            },
            follow_redirects=True,
        )
        milk_request = portal.query_one(
            """
            SELECT id, supply_number, details_json, note
            FROM driver_requests
            WHERE request_type_label = 'Milk Left Behind'
            """
        )
        self.assertEqual(milk_request["supply_number"], "SUP-12345")
        self.assertEqual(milk_request["note"], "Gate code is 1234")
        self.client.get("/logout")

        self.login("dispatch1", "pass123")
        dispatch_page = self.client.get("/dispatch")
        dispatch_html = dispatch_page.data.decode()
        self.assertIn("<th>Supply No</th>", dispatch_html)
        self.assertIn("<strong>SUP-12345</strong>", dispatch_html)
        self.assertIn("<strong>Driver:</strong> Gate code is 1234", dispatch_html)
        self.assertIn("Volume: 0-500L", dispatch_html)
        self.assertNotIn("Supply number: SUP-12345", dispatch_html)
        self.assertNotIn("Milk left behind: 0-500L", dispatch_html)
        self.assertNotIn("Message: Milk left behind", dispatch_html)
        group = portal.query_one("SELECT id, name FROM dispatcher_groups WHERE name = 'Te Rapa'")
        self.client.post(
            f"/dispatch/request/{request_row['id']}/note",
            data={"note": "Internal dispatch note"},
            follow_redirects=True,
        )
        internal_note = portal.query_one(
            """
            SELECT body, visible_to_driver
            FROM request_comments
            WHERE request_id = ? AND body = 'Internal dispatch note'
            """,
            (request_row["id"],),
        )
        self.assertEqual(internal_note["visible_to_driver"], 0)
        self.client.post(
            f"/dispatch/request/{request_row['id']}/reassign",
            data={"dispatcher_group_id": group["id"]},
            follow_redirects=True,
        )
        moved = portal.query_one(
            "SELECT dispatcher_group_name FROM driver_requests WHERE id = ?",
            (request_row["id"],),
        )
        self.assertEqual(moved["dispatcher_group_name"], "Te Rapa")
        self.client.post(
            f"/dispatch/request/{request_row['id']}/acknowledge",
            data={"comment": "Received"},
            follow_redirects=True,
        )
        acknowledged = portal.query_one("SELECT status FROM driver_requests WHERE id = ?", (request_row["id"],))
        self.assertEqual(acknowledged["status"], "acknowledged")
        self.client.post(
            f"/dispatch/request/{request_row['id']}/delete",
            follow_redirects=True,
        )
        still_there = portal.query_one("SELECT id FROM driver_requests WHERE id = ?", (request_row["id"],))
        self.assertIsNotNone(still_there)
        self.client.post(
            f"/dispatch/request/{request_row['id']}/complete",
            data={"comment": "Done"},
            follow_redirects=True,
        )
        completed = portal.query_one("SELECT status FROM driver_requests WHERE id = ?", (request_row["id"],))
        self.assertEqual(completed["status"], "done")
        self.client.get("/logout")

        self.login("driver1", "pass123")
        self.client.post(
            "/driver/profile",
            data={
                "driver_name": "Driver One",
                "truck_number": "T42",
                "depot_id": depot["id"],
            },
            follow_redirects=True,
        )
        history_page = self.client.get("/driver")
        history_html = history_page.data.decode()
        self.assertIn("Old requests", history_html)
        self.assertIn("driver-request-history", history_html)
        self.assertIn("Add CIP Start Of Day", history_html)
        history_section = history_html.split('id="driver-request-history"', 1)[1]
        self.assertNotIn(">Done</span>", history_section)
        self.assertNotIn(">Sent</span>", history_section)
        self.client.post(
            f"/driver/request/{request_row['id']}/dismiss",
            follow_redirects=True,
        )
        hidden = portal.query_one(
            "SELECT driver_hidden_at FROM driver_requests WHERE id = ?",
            (request_row["id"],),
        )
        self.assertIsNotNone(hidden["driver_hidden_at"])
        driver_api = self.client.get("/api/driver/requests").get_json()
        visible_ids = [
            item["id"]
            for item in driver_api["active_requests"] + driver_api["history_requests"]
        ]
        self.assertNotIn(request_row["id"], visible_ids)
        self.client.get("/logout")

        self.login("dispatch1", "pass123")
        self.client.post(
            "/dispatch/requests/delete-done",
            data={"request_id": str(request_row["id"])},
            follow_redirects=True,
        )
        deleted = portal.query_one("SELECT id FROM driver_requests WHERE id = ?", (request_row["id"],))
        self.assertIsNone(deleted)


if __name__ == "__main__":
    unittest.main()
